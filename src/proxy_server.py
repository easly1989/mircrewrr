#!/usr/bin/env python3
"""
MIRCrew Proxy Server per Prowlarr - v4.0
- CRITICAL FIX: Per serie TV, ritorna UN RISULTATO PER EPISODIO (non per thread)
- Download per infohash specifico, mai il primo magnet a caso
- GUID = topic_id + infohash per unicità episodio
"""

import os
import re
import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urljoin, quote_plus, unquote

import cloudscraper
from bs4 import BeautifulSoup
from flask import Flask, request, Response, jsonify

# Configurazione
BASE_URL = os.getenv("MIRCREW_URL", "https://mircrew-releases.org")
USERNAME = os.getenv("MIRCREW_USERNAME", "")
PASSWORD = os.getenv("MIRCREW_PASSWORD", "")
API_KEY = os.getenv("MIRCREW_API_KEY", "mircrew-api-key")
HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PORT = int(os.getenv("PROXY_PORT", "9696"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
COOKIES_FILE = DATA_DIR / "cookies.json"
THANKS_CACHE_FILE = DATA_DIR / "thanks_cache.json"

# Logging
logging.basicConfig(
    level=logging.DEBUG if os.getenv("LOG_LEVEL") == "DEBUG" else logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mircrew")

app = Flask(__name__)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Cache dei topic già ringraziati
thanks_cache = set()

def load_thanks_cache():
    global thanks_cache
    try:
        if THANKS_CACHE_FILE.exists():
            with open(THANKS_CACHE_FILE) as f:
                thanks_cache = set(json.load(f))
            logger.info(f"Thanks cache loaded: {len(thanks_cache)} topics")
    except Exception as e:
        logger.warning(f"Failed to load thanks cache: {e}")

def save_thanks_cache():
    try:
        with open(THANKS_CACHE_FILE, 'w') as f:
            json.dump(list(thanks_cache), f)
    except Exception as e:
        logger.warning(f"Failed to save thanks cache: {e}")

load_thanks_cache()


class MircrewSession:
    def __init__(self):
        self.scraper = None
        self.session_valid = False
        self.last_login = 0
        self._init_scraper()
        self._load_cookies()

    def _init_scraper(self):
        self.scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
        )

    def _save_cookies(self):
        try:
            cookies = {c.name: {'value': c.value, 'domain': c.domain} for c in self.scraper.cookies}
            with open(COOKIES_FILE, 'w') as f:
                json.dump({'cookies': cookies, 'time': time.time()}, f)
        except Exception as e:
            logger.warning(f"Save cookies error: {e}")

    def _load_cookies(self):
        try:
            if COOKIES_FILE.exists():
                with open(COOKIES_FILE) as f:
                    data = json.load(f)
                if time.time() - data.get('time', 0) < 43200:
                    for name, c in data.get('cookies', {}).items():
                        self.scraper.cookies.set(name, c['value'], domain=c.get('domain', ''))
                    return True
        except:
            pass
        return False

    def _check_logged_in(self, html: str) -> bool:
        return "ucp.php?mode=logout" in html

    def get_scraper(self):
        if self.session_valid and (time.time() - self.last_login) < 3600:
            return self.scraper

        try:
            r = self.scraper.get(BASE_URL, timeout=30)
            if self._check_logged_in(r.text):
                logger.info("Session still valid")
                self.session_valid = True
                self.last_login = time.time()
                return self.scraper
        except:
            pass

        if self._do_login():
            return self.scraper
        return self.scraper

    def _do_login(self) -> bool:
        logger.info("=== LOGIN START ===")
        self._init_scraper()

        try:
            r = self.scraper.get(BASE_URL, timeout=30)
            time.sleep(1)

            r = self.scraper.get(f"{BASE_URL}/ucp.php?mode=login", timeout=30)
            soup = BeautifulSoup(r.text, "lxml")
            form = soup.find("form", {"id": "login"})
            if not form:
                logger.error("Login form not found!")
                return False

            fields = {}
            for inp in form.find_all("input", {"type": "hidden"}):
                if inp.get("name"):
                    fields[inp["name"]] = inp.get("value", "")

            sid = fields.get("sid", "")
            data = {
                "username": USERNAME, "password": PASSWORD,
                "autologin": "on", "viewonline": "on",
                "redirect": fields.get("redirect", "index.php"),
                "creation_time": fields.get("creation_time", ""),
                "form_token": fields.get("form_token", ""),
                "sid": sid, "login": "Login"
            }

            time.sleep(0.5)
            r = self.scraper.post(f"{BASE_URL}/ucp.php?mode=login&sid={sid}", data=data,
                headers={"Referer": f"{BASE_URL}/ucp.php?mode=login"}, timeout=30)

            if self._check_logged_in(r.text):
                logger.info("=== LOGIN SUCCESS ===")
                self.session_valid = True
                self.last_login = time.time()
                self._save_cookies()
                return True

            logger.error("Login failed")
            return False
        except Exception as e:
            logger.exception(f"Login exception: {e}")
            return False


session = MircrewSession()

# Category mapping
CATEGORY_MAP = {
    25: 2000, 26: 2000,  # Movies
    51: 5000, 52: 5000, 29: 5000, 30: 5000, 31: 5000,  # TV
    33: 5070, 35: 5070, 37: 5070,  # TV/Anime
    34: 2000, 36: 2000,  # Anime/Cartoon Movies
    39: 7000, 40: 7020, 41: 3030, 42: 7030, 43: 7010,  # Books
    45: 3000, 46: 3000, 47: 3000,  # Audio
}
FORUM_IDS = list(CATEGORY_MAP.keys())

# Forum IDs per serie TV (richiedono espansione per episodio)
TV_FORUM_IDS = {51, 52, 29, 30, 31, 33, 35, 37}


def clean_url(url: str) -> str:
    """Pulisce URL rimuovendo parametri inutili."""
    url = unquote(url)
    url = re.sub(r'&hilit=[^&]*', '', url)
    url = re.sub(r'&sid=[^&]*', '', url)
    if not url.startswith('http'):
        url = urljoin(BASE_URL, url)
    return url


def get_topic_id(url: str) -> Optional[str]:
    """Estrae topic_id dall'URL."""
    match = re.search(r'[?&]t=(\d+)', url)
    return match.group(1) if match else None


def get_post_id(url: str) -> Optional[str]:
    """Estrae post_id dall'URL."""
    match = re.search(r'[?&]p=(\d+)', url)
    return match.group(1) if match else None


def get_infohash(magnet: str) -> Optional[str]:
    """Estrae infohash (btih) dal magnet link."""
    match = re.search(r'btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', magnet)
    if match:
        return match.group(1).upper()
    return None


def extract_size_from_text(text: str) -> int:
    """Estrae dimensione dal testo del post."""
    patterns = [
        r'File\s*size\s*:\s*([\d.,]+\s*[KMGTP]i?[Bb])',
        r'Dimensione\s*:\s*([\d.,]+\s*[KMGTP]i?[Bb])',
        r'Size\s*:\s*([\d.,]+\s*[KMGTP]i?[Bb])',
        r'Filesize\s*:\s*([\d.,]+\s*[KMGTP]i?[Bb])',
        r'Peso\s*:\s*([\d.,]+\s*[KMGTP]i?[Bb])',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return parse_size(match.group(1))

    match = re.search(r'\b([\d.,]+)\s*([KMGTP]i?[Bb])\b', text, re.I)
    if match:
        return parse_size(match.group(0))

    return 0


def parse_size(size_str: str) -> int:
    """Converte stringa dimensione in bytes."""
    if not size_str:
        return 0

    size_str = size_str.upper().replace(',', '.').strip()

    parts = size_str.split('.')
    if len(parts) > 2:
        size_str = ''.join(parts[:-1]) + '.' + parts[-1]

    match = re.search(r'([\d.]+)\s*([KMGTP])?I?B?', size_str)
    if not match:
        return 0

    try:
        num = float(match.group(1))
    except ValueError:
        return 0

    unit = match.group(2) or 'M'
    mult = {'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4, 'P': 1024**5}
    return int(num * mult.get(unit, 1024**2))


def get_default_size(forum_id: int, title: str) -> int:
    """Dimensione default basata su categoria e qualità."""
    is_4k = bool(re.search(r'\b(2160p|4K|UHD)\b', title, re.I))

    if forum_id in [25, 26, 34, 36]:
        return 15*1024**3 if is_4k else 10*1024**3
    elif forum_id in TV_FORUM_IDS:
        return 5*1024**3 if is_4k else 2*1024**3
    return 512*1024**2


def extract_episode_info(text: str) -> Optional[Dict[str, Any]]:
    """Estrae info episodio dal nome del magnet o testo."""
    patterns = [
        # S01E01, S01.E01, S01 E01
        r'[Ss](\d{1,2})[\.\s]?[Ee](\d{1,3})(?:-[Ee]?(\d{1,3}))?',
        # 1x01, 1x01-05
        r'(\d{1,2})[xX](\d{1,3})(?:-(\d{1,3}))?',
        # Stagione 1 Episodio 1
        r'[Ss]tagion[ei]\s*(\d{1,2}).*?[Ee]pisodio\s*(\d{1,3})',
        # Season 1 Episode 1
        r'[Ss]eason\s*(\d{1,2}).*?[Ee]pisode\s*(\d{1,3})',
        # E01 standalone (assumes season 1 or from context)
        r'\b[Ee](\d{1,3})\b',
    ]

    for i, pattern in enumerate(patterns):
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            if i == 4:  # E01 standalone pattern
                return {
                    'season': 1,  # Default season
                    'episode': int(groups[0]),
                    'episode_end': None,
                    'match': match.group(0)
                }
            return {
                'season': int(groups[0]),
                'episode': int(groups[1]),
                'episode_end': int(groups[2]) if len(groups) > 2 and groups[2] else None,
                'match': match.group(0)
            }

    return None


def extract_name_from_magnet(magnet: str) -> str:
    """Estrae nome file dal parametro dn= del magnet."""
    match = re.search(r'dn=([^&]+)', magnet)
    if match:
        return unquote(match.group(1)).replace('+', ' ')
    return ""


def get_thread_content(topic_url: str) -> Tuple[Optional[BeautifulSoup], Optional[str], bool]:
    """
    Carica contenuto thread, cliccando Grazie se necessario.
    Ritorna: (soup, html, thanks_clicked)
    """
    global thanks_cache

    scraper = session.get_scraper()
    topic_url = clean_url(topic_url)
    topic_id = get_topic_id(topic_url)

    logger.info(f"=== GET THREAD: {topic_url} (topic_id={topic_id}) ===")

    try:
        r = scraper.get(topic_url, timeout=30)
        if r.status_code != 200:
            logger.error(f"Failed to load thread: {r.status_code}")
            return None, None, False

        soup = BeautifulSoup(r.text, "lxml")

        # Controlla se già ringraziato
        if topic_id and topic_id in thanks_cache:
            logger.info("Topic already thanked (from cache)")
            return soup, r.text, False

        # Trova primo post
        first_post = soup.select_one("div.post")
        if not first_post:
            logger.warning("First post not found")
            return soup, r.text, False

        # Trova post_id del primo post
        quote_link = first_post.select_one("a[href*='mode=quote']")
        first_post_id = None
        if quote_link:
            first_post_id = get_post_id(quote_link.get("href", ""))
            logger.info(f"First post ID: {first_post_id}")

        # Cerca pulsante Grazie
        thanks_link = None
        if first_post_id:
            for a in soup.find_all("a", href=lambda x: x and "thanks=" in str(x)):
                href = a.get("href", "")
                if f"p={first_post_id}" in href or f"thanks={first_post_id}" in href:
                    thanks_link = href
                    break

        if thanks_link:
            logger.info(f"Thanks button found: {thanks_link}")
            thanks_url = urljoin(BASE_URL, thanks_link)

            try:
                scraper.get(thanks_url, timeout=30)
                logger.info("Thanks clicked!")
                time.sleep(1)

                r = scraper.get(topic_url, timeout=30)
                soup = BeautifulSoup(r.text, "lxml")

                if topic_id:
                    thanks_cache.add(topic_id)
                    save_thanks_cache()

                return soup, r.text, True

            except Exception as e:
                logger.error(f"Failed to click thanks: {e}")
        else:
            logger.info("No thanks button (already thanked or own post)")
            if topic_id:
                thanks_cache.add(topic_id)
                save_thanks_cache()

        return soup, r.text, False

    except Exception as e:
        logger.exception(f"get_thread_content exception: {e}")
        return None, None, False


def get_magnets_from_thread(topic_url: str) -> List[Dict[str, Any]]:
    """
    Estrae tutti i magnet dal thread.
    Ogni magnet ha: magnet, infohash, name, size, episode_info
    """
    soup, html, _ = get_thread_content(topic_url)

    if not soup or not html:
        return []

    results = []

    first_post = soup.select_one("div.post div.content")
    if not first_post:
        logger.warning("First post content not found")
        return []

    post_text = first_post.get_text()
    default_size = extract_size_from_text(post_text)
    logger.info(f"Thread default size: {default_size} bytes ({default_size/1024**3:.2f} GB)")

    # Trova tutti i magnet
    magnet_links = first_post.find_all("a", href=lambda x: x and str(x).startswith("magnet:"))

    if not magnet_links:
        magnets_raw = re.findall(r'magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^\s"\'<>]*', html)
        magnets_raw += re.findall(r'magnet:\?xt=urn:btih:[a-zA-Z2-7]{32}[^\s"\'<>]*', html)
        for m in magnets_raw:
            magnet = re.sub(r'\s+', '', m)
            infohash = get_infohash(magnet)
            if not infohash:
                continue
            name = extract_name_from_magnet(magnet)
            ep_info = extract_episode_info(name)
            results.append({
                "magnet": magnet,
                "infohash": infohash,
                "name": name,
                "size": default_size,
                "episode_info": ep_info,
            })
    else:
        for link in magnet_links:
            magnet = re.sub(r'\s+', '', link.get("href", ""))
            infohash = get_infohash(magnet)
            if not infohash:
                continue

            name = extract_name_from_magnet(magnet)
            if not name:
                name = link.get_text(strip=True)

            ep_info = extract_episode_info(name)

            # Cerca dimensione specifica per questo magnet nel testo vicino
            parent = link.parent
            if parent:
                parent_text = parent.get_text()
                specific_size = extract_size_from_text(parent_text)
                if specific_size > 0:
                    size = specific_size
                else:
                    size = default_size
            else:
                size = default_size

            results.append({
                "magnet": magnet,
                "infohash": infohash,
                "name": name,
                "size": size,
                "episode_info": ep_info,
            })

    # Rimuovi duplicati per infohash
    seen = set()
    unique = []
    for r in results:
        if r["infohash"] not in seen:
            seen.add(r["infohash"])
            unique.append(r)

    logger.info(f"Found {len(unique)} unique magnets in thread")
    return unique


def search_mircrew(query: str, categories: List[int] = None) -> List[Dict]:
    """
    Ricerca su MIRCrew.
    Per serie TV: espande ogni thread in risultati per singolo episodio.
    """
    scraper = session.get_scraper()

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
        "ch": "300",
        "t": "0",
        "submit": "Cerca",
    }

    for fid in (categories or FORUM_IDS):
        params[f"fid[{fid}]"] = str(fid)

    try:
        r = scraper.get(f"{BASE_URL}/search.php", params=params, timeout=30)
        logger.info(f"Search '{query}': status={r.status_code}")

        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, "lxml")
        results = []
        seen_threads = set()

        for row in soup.select("li.row"):
            try:
                link = row.select_one("a.topictitle")
                if not link:
                    continue

                thread_title = link.get_text(strip=True)
                href = link.get("href", "")
                url = clean_url(urljoin(BASE_URL, href))
                topic_id = get_topic_id(url)

                if not topic_id or topic_id in seen_threads:
                    continue
                seen_threads.add(topic_id)

                # Categoria
                cat_link = row.select_one("a[href*='viewforum.php']")
                forum_id = 25
                if cat_link:
                    m = re.search(r'f=(\d+)', cat_link.get("href", ""))
                    if m:
                        forum_id = int(m.group(1))

                # Data
                time_el = row.select_one("time[datetime]")
                pub_date = datetime.now()
                if time_el:
                    try:
                        pub_date = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
                    except:
                        pass

                is_tv = forum_id in TV_FORUM_IDS
                is_thanked = topic_id in thanks_cache

                # Per serie TV: espandi in singoli episodi
                if is_tv:
                    logger.info(f"TV thread: {thread_title} - fetching episodes...")
                    magnets = get_magnets_from_thread(url)

                    if magnets:
                        for mag in magnets:
                            ep_info = mag.get("episode_info")

                            # Titolo: usa nome magnet se ha info episodio
                            if ep_info:
                                title = mag["name"] if mag["name"] else thread_title
                            else:
                                title = mag["name"] if mag["name"] else thread_title

                            results.append({
                                "title": title,
                                "link": url,
                                "topic_id": topic_id,
                                "infohash": mag["infohash"],
                                "guid": f"{topic_id}-{mag['infohash'][:8]}",
                                "pubDate": pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                                "size": mag["size"],
                                "category": CATEGORY_MAP.get(forum_id, 5000),
                                "forum_id": forum_id,
                                "seeders": 11 if is_thanked else 1,
                                "peers": 1,
                                "episode_info": ep_info,
                            })

                        logger.info(f"Expanded to {len(magnets)} episode results")
                    else:
                        # Nessun magnet trovato, ritorna thread-level
                        results.append({
                            "title": thread_title,
                            "link": url,
                            "topic_id": topic_id,
                            "infohash": None,
                            "guid": topic_id,
                            "pubDate": pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                            "size": get_default_size(forum_id, thread_title),
                            "category": CATEGORY_MAP.get(forum_id, 5000),
                            "forum_id": forum_id,
                            "seeders": 1,
                            "peers": 1,
                            "episode_info": None,
                        })
                else:
                    # Film/altro: risultato thread-level
                    results.append({
                        "title": thread_title,
                        "link": url,
                        "topic_id": topic_id,
                        "infohash": None,
                        "guid": topic_id,
                        "pubDate": pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                        "size": get_default_size(forum_id, thread_title),
                        "category": CATEGORY_MAP.get(forum_id, 2000),
                        "forum_id": forum_id,
                        "seeders": 11 if is_thanked else 1,
                        "peers": 1,
                        "episode_info": None,
                    })

            except Exception as e:
                logger.warning(f"Parse error: {e}")

        logger.info(f"Search returned {len(results)} total results")
        return results

    except Exception as e:
        logger.exception(f"Search exception: {e}")
        return []


def escape_xml(s):
    if not s:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# === ROUTES ===

@app.route("/")
def index():
    return jsonify({"status": "ok", "service": "MIRCrew Proxy", "version": "4.0.0"})


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "version": "4.0.0",
        "logged_in": session.session_valid,
        "thanks_cached": len(thanks_cache)
    })


@app.route("/api")
def api():
    if API_KEY and request.args.get("apikey") != API_KEY:
        return Response('<?xml version="1.0"?><error code="100" description="Invalid API Key"/>',
                       mimetype="application/xml", status=401)

    t = request.args.get("t", "caps")

    if t == "caps":
        return get_caps()
    elif t in ["search", "tvsearch", "movie", "music", "book"]:
        return do_search()

    return Response(f'<?xml version="1.0"?><error code="203" description="Unknown: {t}"/>',
                   mimetype="application/xml", status=400)


def get_caps():
    return Response('''<?xml version="1.0" encoding="UTF-8"?>
<caps>
<server title="MIRCrew Proxy"/>
<limits default="100" max="300"/>
<searching>
<search available="yes" supportedParams="q"/>
<tv-search available="yes" supportedParams="q,season,ep"/>
<movie-search available="yes" supportedParams="q"/>
</searching>
<categories>
<category id="2000" name="Movies"/>
<category id="5000" name="TV"/>
<category id="5070" name="TV/Anime"/>
<category id="3000" name="Audio"/>
<category id="7000" name="Books"/>
</categories>
</caps>''', mimetype="application/xml")


def do_search():
    query = request.args.get("q", "")
    cat_str = request.args.get("cat", "")

    forum_ids = None
    if cat_str:
        torznab_cats = [int(c) for c in cat_str.split(",") if c.isdigit()]
        if torznab_cats:
            forum_ids = [fid for fid, tcat in CATEGORY_MAP.items() if tcat in torznab_cats]

    results = search_mircrew(query, forum_ids)

    items = ""
    for r in results:
        # Download URL include infohash se disponibile
        if r.get("infohash"):
            dl_url = f"http://{request.host}/download?topic_id={r['topic_id']}&infohash={r['infohash']}"
        else:
            dl_url = f"http://{request.host}/download?url={quote_plus(r['link'])}"

        items += f'''<item>
<title>{escape_xml(r['title'])}</title>
<guid>{escape_xml(r['guid'])}</guid>
<link>{escape_xml(r['link'])}</link>
<comments>{escape_xml(r['link'])}</comments>
<pubDate>{r['pubDate']}</pubDate>
<size>{r['size']}</size>
<enclosure url="{escape_xml(dl_url)}" length="{r['size']}" type="application/x-bittorrent"/>
<torznab:attr name="category" value="{r['category']}"/>
<torznab:attr name="size" value="{r['size']}"/>
<torznab:attr name="seeders" value="{r['seeders']}"/>
<torznab:attr name="peers" value="{r['peers']}"/>
<torznab:attr name="downloadvolumefactor" value="0"/>
<torznab:attr name="uploadvolumefactor" value="1"/>
</item>
'''

    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:torznab="http://torznab.com/schemas/2015/feed">
<channel>
<title>MIRCrew</title>
<link>{BASE_URL}</link>
{items}
</channel>
</rss>'''

    return Response(xml, mimetype="application/rss+xml")


@app.route("/download")
def download():
    """
    Download magnet link.
    - Se infohash specificato: ritorna SOLO quel magnet specifico
    - Altrimenti: ritorna primo magnet (per film)
    """
    topic_id = request.args.get("topic_id")
    infohash = request.args.get("infohash", "").upper()
    url = request.args.get("url", "")

    if topic_id:
        url = f"{BASE_URL}/viewtopic.php?t={topic_id}"
    elif url:
        url = unquote(url)
    else:
        return "Missing topic_id or url", 400

    logger.info(f"=== DOWNLOAD: topic={topic_id}, infohash={infohash or 'ANY'} ===")

    magnets = get_magnets_from_thread(url)

    if not magnets:
        logger.error("No magnets found!")
        return "Magnet not found", 404

    # Se infohash specificato, cerca quello esatto
    if infohash:
        for m in magnets:
            if m["infohash"] == infohash:
                logger.info(f"Found exact match: {m['name']}")
                return Response(status=302, headers={"Location": m["magnet"]})

        # Se non trovato esatto, errore (NON ritornare un magnet sbagliato!)
        logger.error(f"Infohash {infohash} not found in thread! Available: {[m['infohash'][:8] for m in magnets]}")
        return f"Infohash {infohash} not found in this thread", 404

    # Nessun infohash specificato: ritorna primo (per film)
    magnet = magnets[0]["magnet"]
    logger.info(f"Returning first magnet: {magnets[0]['name']}")
    return Response(status=302, headers={"Location": magnet})


@app.route("/thread/<topic_id>")
def thread_info(topic_id):
    """Endpoint debug per vedere tutti i magnet di un thread."""
    url = f"{BASE_URL}/viewtopic.php?t={topic_id}"
    magnets = get_magnets_from_thread(url)

    return jsonify({
        "topic_id": topic_id,
        "url": url,
        "magnets": [{
            "infohash": m["infohash"],
            "name": m["name"],
            "size": m["size"],
            "size_gb": round(m["size"] / 1024**3, 2),
            "episode_info": m["episode_info"]
        } for m in magnets],
        "count": len(magnets)
    })


if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        logger.error("MIRCREW_USERNAME and MIRCREW_PASSWORD required!")
        exit(1)

    logger.info(f"=== MIRCrew Proxy v4.0 starting on {HOST}:{PORT} ===")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
