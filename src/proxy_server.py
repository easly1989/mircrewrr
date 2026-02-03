#!/usr/bin/env python3
"""
MIRCrew Proxy Server per Prowlarr

Questo microservizio fa da proxy tra Prowlarr e mircrew-releases.org,
gestendo l'autenticazione via CloudScraper per bypassare CloudFlare.

Espone un'API REST compatibile con Prowlarr Custom Indexer (Torznab/Newznab-like).
"""

import os
import re
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin, quote_plus
from xml.etree.ElementTree import Element, SubElement, tostring

import cloudscraper
from bs4 import BeautifulSoup
from flask import Flask, request, Response, jsonify

# Configurazione
BASE_URL = os.getenv("MIRCREW_URL", "https://mircrew-releases.org")
USERNAME = os.getenv("MIRCREW_USERNAME", "")
PASSWORD = os.getenv("MIRCREW_PASSWORD", "")
API_KEY = os.getenv("MIRCREW_API_KEY", "mircrew-api-key")  # Per sicurezza
HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PORT = int(os.getenv("PROXY_PORT", "9696"))

# Setup logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mircrew-proxy")

# Flask app
app = Flask(__name__)

# Sessione globale (con caching)
class MircrewSession:
    def __init__(self):
        self.scraper: Optional[cloudscraper.CloudScraper] = None
        self.last_login: float = 0
        self.session_valid: bool = False
        self.login_timeout: int = 3600  # Re-login dopo 1 ora

    def get_scraper(self) -> cloudscraper.CloudScraper:
        """Ottiene o crea una sessione autenticata."""
        now = time.time()

        # Se la sessione è scaduta o non valida, ri-autentica
        if not self.session_valid or (now - self.last_login) > self.login_timeout:
            self._login()

        return self.scraper

    def _login(self) -> bool:
        """Esegue il login."""
        logger.info("Esecuzione login...")

        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True,
            }
        )

        try:
            # 1. Homepage per cookies iniziali
            response = self.scraper.get(BASE_URL, timeout=30)
            if response.status_code != 200:
                logger.error(f"Homepage failed: {response.status_code}")
                return False

            time.sleep(0.5)

            # 2. Pagina login
            login_url = f"{BASE_URL}/ucp.php?mode=login"
            response = self.scraper.get(login_url, timeout=30)
            if response.status_code != 200:
                logger.error(f"Login page failed: {response.status_code}")
                return False

            # Parse form
            soup = BeautifulSoup(response.text, "lxml")
            login_form = soup.find("form", {"id": "login"})
            if not login_form:
                logger.error("Login form not found")
                return False

            # Estrai campi hidden
            form_fields = {}
            for inp in login_form.find_all("input", {"type": "hidden"}):
                name = inp.get("name", "")
                value = inp.get("value", "")
                if name:
                    form_fields[name] = value

            sid = form_fields.get("sid", "")

            # Form data
            form_data = {
                "username": USERNAME,
                "password": PASSWORD,
                "redirect": form_fields.get("redirect", "index.php"),
                "creation_time": form_fields.get("creation_time", ""),
                "form_token": form_fields.get("form_token", ""),
                "sid": sid,
                "login": "Login"
            }

            # 3. POST login
            post_url = f"{BASE_URL}/ucp.php?mode=login&sid={sid}"
            headers = {
                "Referer": login_url,
                "Origin": BASE_URL,
            }

            time.sleep(0.3)
            response = self.scraper.post(post_url, data=form_data, headers=headers, timeout=30)

            # Verifica login
            if "mode=logout" in response.text or "logout" in response.text.lower():
                logger.info("Login riuscito!")
                self.session_valid = True
                self.last_login = time.time()
                return True
            else:
                logger.error("Login fallito - logout link non trovato")
                self.session_valid = False
                return False

        except Exception as e:
            logger.error(f"Login exception: {e}")
            self.session_valid = False
            return False

    def invalidate(self):
        """Invalida la sessione corrente."""
        self.session_valid = False


# Istanza globale della sessione
session = MircrewSession()

# Mapping categorie phpBB -> Torznab
CATEGORY_MAP = {
    25: 2000,   # Movies
    26: 2000,   # Movies - Film
    51: 5000,   # TV - In corso
    52: 5000,   # TV - Complete
    29: 5000,   # Documentari
    30: 5000,   # TV Show
    31: 5000,   # Teatro
    33: 5070,   # Anime
    34: 2000,   # Anime Movies
    35: 5070,   # Anime Serie
    36: 2000,   # Cartoon Movies
    37: 5070,   # Cartoon Serie
    39: 7000,   # Books
    40: 7020,   # E-Books
    41: 3030,   # Audiobooks
    42: 7030,   # Comics
    43: 7010,   # Magazines
    45: 3000,   # Music
    46: 3000,   # Music Audio
    47: 3000,   # Music Video
}

# Categorie disponibili per la ricerca
FORUM_IDS = [25, 26, 51, 52, 29, 30, 31, 33, 34, 35, 36, 37, 39, 40, 41, 42, 43, 45, 46, 47]


def parse_size(size_str: str) -> int:
    """Converte stringa dimensione in bytes."""
    if not size_str:
        return 0

    size_str = size_str.upper().strip()
    multipliers = {
        'KB': 1024,
        'MB': 1024**2,
        'GB': 1024**3,
        'TB': 1024**4,
        'KIB': 1024,
        'MIB': 1024**2,
        'GIB': 1024**3,
        'TIB': 1024**4,
    }

    for suffix, mult in multipliers.items():
        if suffix in size_str:
            try:
                num = float(re.sub(r'[^\d.,]', '', size_str.replace(',', '.')))
                return int(num * mult)
            except:
                pass

    return 0


def estimate_size(title: str, forum_id: int) -> int:
    """Stima la dimensione in base al titolo e categoria."""
    is_4k = bool(re.search(r'\b(2160p|4K|UHD)\b', title, re.I))

    # Film
    if forum_id in [25, 26, 34, 36]:
        return 15 * 1024**3 if is_4k else 8 * 1024**3

    # Serie TV
    if forum_id in [51, 52, 29, 30, 31, 33, 35, 37]:
        return 4 * 1024**3 if is_4k else 2 * 1024**3

    # Default
    return 1 * 1024**3


def search_mircrew(query: str, categories: List[int] = None) -> List[Dict[str, Any]]:
    """Esegue ricerca su MIRCrew."""
    scraper = session.get_scraper()

    search_url = f"{BASE_URL}/search.php"

    # Prepara keywords
    keywords = query if query else str(datetime.now().year)
    keywords = " ".join(f"+{w}" for w in keywords.split())

    params = {
        "keywords": keywords,
        "terms": "all",
        "sc": "0",
        "sf": "titleonly",
        "sr": "topics",
        "sk": "t",
        "sd": "d",
        "st": "0",
        "ch": "100",
        "t": "0",
        "submit": "Cerca",
    }

    # Aggiungi categorie
    forum_ids = categories if categories else FORUM_IDS
    for fid in forum_ids:
        params[f"fid[{fid}]"] = str(fid)

    try:
        response = scraper.get(search_url, params=params, timeout=30)

        if response.status_code != 200:
            logger.error(f"Search failed: {response.status_code}")
            return []

        soup = BeautifulSoup(response.text, "lxml")
        results = []

        for row in soup.find_all("li", {"class": "row"}):
            try:
                title_link = row.find("a", {"class": "topictitle"})
                if not title_link:
                    continue

                title = title_link.get_text(strip=True)
                details_url = urljoin(BASE_URL, title_link.get("href", ""))

                # Categoria
                cat_link = row.find("a", href=lambda x: x and "viewforum.php" in x)
                forum_id = 25  # default
                if cat_link:
                    href = cat_link.get("href", "")
                    match = re.search(r'f=(\d+)', href)
                    if match:
                        forum_id = int(match.group(1))

                # Data
                time_elem = row.find("time")
                pub_date = datetime.now()
                if time_elem and time_elem.get("datetime"):
                    try:
                        pub_date = datetime.fromisoformat(time_elem.get("datetime").replace("Z", "+00:00"))
                    except:
                        pass

                # Dimensione dal titolo o stima
                size = 0
                size_match = re.search(r'[\[\({]([\d.,]+\s*[KMGT]i?B)[\]\)}]', title, re.I)
                if size_match:
                    size = parse_size(size_match.group(1))
                if not size:
                    size = estimate_size(title, forum_id)

                results.append({
                    "title": title,
                    "details": details_url,
                    "guid": details_url,
                    "pubdate": pub_date.isoformat(),
                    "size": size,
                    "category": CATEGORY_MAP.get(forum_id, 8000),
                    "forum_id": forum_id,
                })

            except Exception as e:
                logger.warning(f"Error parsing row: {e}")
                continue

        logger.info(f"Search '{query}' returned {len(results)} results")
        return results

    except Exception as e:
        logger.error(f"Search exception: {e}")
        session.invalidate()
        return []


def get_magnet(topic_url: str) -> Optional[str]:
    """Ottiene il magnet link da una pagina topic."""
    scraper = session.get_scraper()

    try:
        response = scraper.get(topic_url, timeout=30)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, "lxml")

        # Cerca link magnet
        magnet_link = soup.find("a", href=lambda x: x and x.startswith("magnet:?"))
        if magnet_link:
            return magnet_link.get("href")

        # Cerca nel testo
        magnet_match = re.search(r'magnet:\?xt=urn:[^\s"\'<>]+', response.text)
        if magnet_match:
            return magnet_match.group(0)

        return None

    except Exception as e:
        logger.error(f"Get magnet exception: {e}")
        return None


# ============== API Endpoints ==============

@app.route("/")
def index():
    """Health check."""
    return jsonify({
        "status": "ok",
        "service": "MIRCrew Proxy",
        "version": "1.0.0",
        "session_valid": session.session_valid
    })


@app.route("/api")
def api_caps():
    """Capabilities - formato Torznab."""
    apikey = request.args.get("apikey", "")

    # Verifica API key se configurata
    if API_KEY and apikey != API_KEY:
        return Response(
            '<?xml version="1.0" encoding="UTF-8"?><error code="100" description="Invalid API Key"/>',
            mimetype="application/xml",
            status=401
        )

    t = request.args.get("t", "caps")

    if t == "caps":
        return get_caps()
    elif t == "search":
        return do_search()
    elif t == "tvsearch":
        return do_search()
    elif t == "movie":
        return do_search()
    else:
        return Response(
            f'<?xml version="1.0" encoding="UTF-8"?><error code="203" description="Function not available: {t}"/>',
            mimetype="application/xml",
            status=400
        )


def get_caps():
    """Ritorna le capabilities in formato Torznab XML."""
    xml = '''<?xml version="1.0" encoding="UTF-8"?>
<caps>
    <server title="MIRCrew Proxy" />
    <limits default="100" max="300" />
    <searching>
        <search available="yes" supportedParams="q" />
        <tv-search available="yes" supportedParams="q,season,ep" />
        <movie-search available="yes" supportedParams="q" />
        <music-search available="yes" supportedParams="q" />
        <book-search available="yes" supportedParams="q" />
    </searching>
    <categories>
        <category id="2000" name="Movies" />
        <category id="5000" name="TV" />
        <category id="5070" name="TV/Anime" />
        <category id="3000" name="Audio" />
        <category id="7000" name="Books" />
    </categories>
</caps>'''
    return Response(xml, mimetype="application/xml")


def do_search():
    """Esegue ricerca e ritorna risultati in formato Torznab RSS."""
    query = request.args.get("q", "")

    # Parsing categorie richieste
    cat_param = request.args.get("cat", "")
    torznab_cats = [int(c) for c in cat_param.split(",") if c.isdigit()] if cat_param else None

    # Converti categorie Torznab -> forum IDs
    forum_ids = None
    if torznab_cats:
        forum_ids = []
        for fid, tcat in CATEGORY_MAP.items():
            if tcat in torznab_cats:
                forum_ids.append(fid)

    results = search_mircrew(query, forum_ids)

    # Costruisci RSS
    rss = Element("rss", {"version": "2.0", "xmlns:torznab": "http://torznab.com/schemas/2015/feed"})
    channel = SubElement(rss, "channel")

    SubElement(channel, "title").text = "MIRCrew Proxy"
    SubElement(channel, "description").text = "MIRCrew search results"

    for item in results:
        item_elem = SubElement(channel, "item")
        SubElement(item_elem, "title").text = item["title"]
        SubElement(item_elem, "guid").text = item["guid"]
        SubElement(item_elem, "link").text = item["details"]
        SubElement(item_elem, "pubDate").text = item["pubdate"]
        SubElement(item_elem, "size").text = str(item["size"])
        SubElement(item_elem, "category").text = str(item["category"])

        # Torznab attributes
        SubElement(item_elem, "{http://torznab.com/schemas/2015/feed}attr", {
            "name": "category",
            "value": str(item["category"])
        })
        SubElement(item_elem, "{http://torznab.com/schemas/2015/feed}attr", {
            "name": "size",
            "value": str(item["size"])
        })

        # Link per download (tramite nostro endpoint)
        download_url = f"http://{HOST}:{PORT}/download?url={quote_plus(item['details'])}"
        SubElement(item_elem, "enclosure", {
            "url": download_url,
            "type": "application/x-bittorrent"
        })

    xml_str = tostring(rss, encoding="unicode")
    return Response(f'<?xml version="1.0" encoding="UTF-8"?>{xml_str}', mimetype="application/xml")


@app.route("/download")
def download():
    """Endpoint per ottenere il magnet link."""
    topic_url = request.args.get("url", "")

    if not topic_url:
        return "Missing URL parameter", 400

    magnet = get_magnet(topic_url)

    if magnet:
        # Redirect al magnet link
        return Response(
            status=302,
            headers={"Location": magnet}
        )
    else:
        return "Magnet link not found", 404


@app.route("/health")
def health():
    """Health check dettagliato."""
    return jsonify({
        "status": "healthy",
        "session_valid": session.session_valid,
        "last_login": session.last_login,
        "uptime": time.time() - app.config.get("start_time", time.time())
    })


if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        logger.error("MIRCREW_USERNAME e MIRCREW_PASSWORD devono essere configurati!")
        exit(1)

    app.config["start_time"] = time.time()
    logger.info(f"Avvio MIRCrew Proxy su {HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
