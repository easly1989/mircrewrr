#!/usr/bin/env python3
"""
MIRCrew Proxy Server per Prowlarr - v5.8
- NO Thanks durante ricerca (solo al download)
- Filtro stagione da titolo thread
- Espansione magnets per contenuti già ringraziati (TV e film)
- v5.1: Risultati sintetici per episodio (thread TV non ringraziati)
- v5.1: Download con season/ep params per episodio specifico
- v5.1: Attributi Torznab season/episode per Sonarr
- v5.2: Multi-season threads riabilitati (thanked=expand, non-thanked=thread-level)
- v5.3: Riconoscimento season pack (solo season attr, no episode) per Sonarr
- v5.3.1: Fix espansione magnets anche per film già ringraziati
- v5.3.2: Fix ricerca - rimuovi +keyword che richiedeva match esatto
- v5.4: FlareSolverr per bypass Cloudflare managed challenge
- v5.4.1: Switch to nodriver FlareSolverr fork for better Cloudflare bypass
- v5.4.2: Enhanced login debug logging to diagnose failures
- v5.4.3: Fix CSRF - FlareSolverr visits login page directly for valid session
- v5.4.4: More detailed login debug logging
- v5.5: Fix CSRF by using FlareSolverr HTML directly (don't refetch login page)
- v5.5.1: Enhanced diagnostics - cookies and login state checks
- v5.6: Use plain requests.Session for login POST (cloudscraper may interfere)
- v5.7: Fix session IP mismatch - use FlareSolverr for both GET and POST
- v5.7.1: Remove sessions (nodriver doesn't support them), pass cookies manually
- v5.8: FlareSolverr GET + requests POST, extract submit button value from form
"""

import os
import re
import json
import time
import logging
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urljoin, quote_plus, unquote, urlencode

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
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://flaresolverr:8191")

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


def get_cf_cookies_via_flaresolverr(url: str = None) -> Optional[Dict[str, str]]:
    """
    Usa FlareSolverr per bypassare il Cloudflare managed challenge.
    Ritorna un dict con cookies, userAgent e response HTML, o None in caso di errore.
    """
    target_url = url or BASE_URL
    try:
        logger.info(f"Requesting Cloudflare bypass via FlareSolverr: {target_url}")
        resp = requests.post(
            f"{FLARESOLVERR_URL}/v1",
            json={"cmd": "request.get", "url": target_url, "maxTimeout": 60000},
            timeout=90,
        )
        data = resp.json()
        if data.get("status") != "ok":
            logger.error(f"FlareSolverr error: {data.get('message', 'unknown')}")
            return None

        solution = data.get("solution", {})
        cookies = {c["name"]: c["value"] for c in solution.get("cookies", [])}
        user_agent = solution.get("userAgent", "")
        response_html = solution.get("response", "")
        logger.info(f"FlareSolverr OK: {len(cookies)} cookies, UA={user_agent[:60]}..., HTML={len(response_html)} chars")
        return {"cookies": cookies, "userAgent": user_agent, "html": response_html}

    except Exception as e:
        logger.error(f"FlareSolverr request failed: {e}")
        return None


class MircrewSession:
    def __init__(self):
        self.scraper = None
        self.session_valid = False
        self.last_login = 0
        self.cf_user_agent = None
        self._init_scraper()
        self._load_cookies()

    def _init_scraper(self, user_agent: str = None):
        browser_cfg = {'browser': 'chrome', 'platform': 'windows', 'desktop': True}
        self.scraper = cloudscraper.create_scraper(browser=browser_cfg)
        if user_agent:
            self.scraper.headers.update({"User-Agent": user_agent})

    def _apply_cf_cookies(self, cf: Dict[str, str]):
        """Applica i cookies di FlareSolverr allo scraper."""
        domain = BASE_URL.split("//")[-1].split("/")[0]
        for name, value in cf["cookies"].items():
            self.scraper.cookies.set(name, value, domain=domain)
        if cf.get("userAgent"):
            self.cf_user_agent = cf["userAgent"]
            self.scraper.headers.update({"User-Agent": cf["userAgent"]})

    def _save_cookies(self):
        try:
            cookies = {c.name: {'value': c.value, 'domain': c.domain} for c in self.scraper.cookies}
            with open(COOKIES_FILE, 'w') as f:
                json.dump({
                    'cookies': cookies,
                    'time': time.time(),
                    'userAgent': self.cf_user_agent,
                }, f)
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
                    if data.get('userAgent'):
                        self.cf_user_agent = data['userAgent']
                        self.scraper.headers.update({"User-Agent": data['userAgent']})
                    return True
        except:
            pass
        return False

    def _check_logged_in(self, html: str) -> bool:
        return "mode=logout" in html

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

        login_url = f"{BASE_URL}/ucp.php?mode=login"

        try:
            # Step 1: GET login page via FlareSolverr (bypass Cloudflare)
            resp = requests.post(f"{FLARESOLVERR_URL}/v1",
                json={"cmd": "request.get", "url": login_url, "maxTimeout": 60000},
                timeout=90)
            data = resp.json()
            if data.get("status") != "ok":
                logger.error(f"FlareSolverr GET failed: {data.get('message')}")
                return False

            solution = data["solution"]
            html = solution.get("response", "")
            user_agent = solution.get("userAgent", "")
            cookies_list = solution.get("cookies", [])
            cookies_dict = {c["name"]: c["value"] for c in cookies_list}
            logger.info(f"GET OK: {len(cookies_list)} cookies, HTML={len(html)} chars")

            # Step 2: Estrai form tokens dall'HTML
            soup = BeautifulSoup(html, "lxml")
            form = soup.find("form", {"id": "login"})
            if not form:
                logger.error("Login form not found!")
                return False

            # Estrai campi hidden
            fields = {inp["name"]: inp.get("value", "")
                      for inp in form.find_all("input", {"type": "hidden"})
                      if inp.get("name")}

            # Estrai valore del pulsante submit (potrebbe essere "Accedi" in italiano)
            submit_btn = form.find("input", {"type": "submit", "name": "login"})
            login_value = submit_btn.get("value", "Login") if submit_btn else "Login"

            sid = fields.get("sid", "")
            logger.info(f"Form fields: {list(fields.keys())}, sid: {sid[:20]}..., submit: '{login_value}'")

            # Step 3: POST via requests.Session con cookies di FlareSolverr
            login_session = requests.Session()
            login_session.headers.update({
                "User-Agent": user_agent,
                "Referer": login_url,
                "Origin": BASE_URL,
                "Content-Type": "application/x-www-form-urlencoded",
            })

            # Applica cookies di FlareSolverr
            for c in cookies_list:
                login_session.cookies.set(c["name"], c["value"])

            post_data = {
                "username": USERNAME, "password": PASSWORD,
                "autologin": "on", "viewonline": "on",
                "redirect": fields.get("redirect", "index.php"),
                "creation_time": fields.get("creation_time", ""),
                "form_token": fields.get("form_token", ""),
                "sid": sid, "login": login_value  # Usa valore dal form
            }

            time.sleep(0.5)
            logger.info(f"Posting login for user: {USERNAME}")
            r = login_session.post(f"{BASE_URL}/ucp.php?mode=login&sid={sid}",
                                   data=post_data, timeout=30)

            logger.info(f"POST response: status={r.status_code}, len={len(r.text)}, url={r.url}")

            if self._check_logged_in(r.text):
                logger.info("=== LOGIN SUCCESS ===")
                self._init_scraper(user_agent=user_agent)
                # Copia cookies dalla sessione di login
                for cookie in login_session.cookies:
                    self.scraper.cookies.set(cookie.name, cookie.value, domain=cookie.domain)
                self.session_valid = True
                self.last_login = time.time()
                self._save_cookies()
                return True

            soup_post = BeautifulSoup(r.text, "lxml")
            error_div = soup_post.find("div", class_="error")
            if error_div:
                logger.error(f"Login error: {error_div.get_text(strip=True)[:200]}")
            else:
                title = soup_post.find("title")
                logger.error(f"Login failed - page title: {title.get_text(strip=True) if title else 'N/A'}")
                if soup_post.find("form", {"id": "login"}):
                    logger.error("Still on login page - credentials likely wrong")
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
TV_FORUM_IDS = {51, 52, 29, 30, 31, 33, 35, 37}


# === TITLE PARSING HELPERS ===

def extract_season_from_title(title: str) -> Optional[int]:
    """Estrae numero stagione dal titolo thread."""
    # Pattern per stagione singola
    patterns = [
        r'[Ss]tagione?\s*(\d+)',  # Stagione 15
        r'[Ss]eason\s*(\d+)',      # Season 15
        r'\b[Ss](\d{1,2})\b',      # S15 (standalone)
    ]

    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            return int(match.group(1))

    return None


def is_multi_season_title(title: str) -> bool:
    """
    Verifica se il titolo indica multiple stagioni.
    NON skippa più, ma indica che non possiamo generare risultati sintetici.
    """
    patterns = [
        r'[Ss]tagion[ei]\s*\d+\s*[-–]\s*\d+',  # Stagioni 1-8, Stagione 1-8
        r'[Ss]\d+\s*[-–]\s*[Ss]?\d+',           # S1-S8, S1-8
        r'[Ss]eason\s*\d+\s*[-–]\s*\d+',        # Season 1-8
    ]

    for pattern in patterns:
        if re.search(pattern, title, re.I):
            return True

    return False


def extract_season_from_query(query: str) -> Optional[int]:
    """Estrae stagione dalla query di ricerca."""
    patterns = [
        r'[Ss](\d{1,2})[Ee]',           # S15E...
        r'[Ss]tagione?\s*(\d+)',         # Stagione 15
        r'[Ss]eason\s*(\d+)',            # Season 15
        r'\b(\d{1,2})[xX]\d',            # 15x01
    ]

    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            return int(match.group(1))

    return None


def extract_episode_from_query(query: str) -> Optional[int]:
    """Estrae episodio dalla query di ricerca."""
    patterns = [
        r'[Ss]\d{1,2}[Ee](\d{1,3})',     # S15E17
        r'[Ee]pisod[eio]+\s*(\d+)',       # Episodio 17
        r'\d{1,2}[xX](\d{1,3})',          # 15x17
    ]

    for pattern in patterns:
        match = re.search(pattern, query, re.I)
        if match:
            return int(match.group(1))

    return None


def title_matches_season(title: str, target_season: int) -> bool:
    """Verifica se il titolo corrisponde alla stagione cercata."""
    title_season = extract_season_from_title(title)

    if title_season is None:
        # Nessuna stagione nel titolo - potrebbe essere valido
        return True

    return title_season == target_season


def extract_episode_count_from_title(title: str) -> Optional[int]:
    """
    Estrae il numero di episodi disponibili dal titolo.
    Patterns:
    - [18/24] → 18 episodi
    - [IN CORSO 05/15] → 5 episodi
    - [IN CORSO][05/15] → 5 episodi
    - [COMPLETA] → None (sconosciuto, ma completa)
    - (07/10) → 7 episodi
    """
    patterns = [
        r'\[IN CORSO[^\]]*?(\d+)/\d+\]',  # [IN CORSO 05/15] o [IN CORSO][05/15]
        r'\[(\d+)/\d+\]',                   # [18/24]
        r'\((\d+)/\d+\)',                   # (07/10)
        r'\[IN CORSO\]\s*\[(\d+)/\d+\]',   # [IN CORSO][05/15]
    ]

    for pattern in patterns:
        match = re.search(pattern, title, re.I)
        if match:
            return int(match.group(1))

    # Se è COMPLETA ma non sappiamo quanti episodi, ritorna None
    if re.search(r'\[COMPLET[AEO]\]', title, re.I):
        return None  # Completa ma numero sconosciuto

    return None


def generate_show_name_from_title(title: str) -> str:
    """Estrae il nome della serie dal titolo del thread."""
    # Rimuovi tutto dopo il primo " - Stagione" o simili
    name = re.split(r'\s*[-–]\s*[Ss]tagion', title)[0]
    # Rimuovi anno tra parentesi
    name = re.sub(r'\s*\(\d{4}\)\s*', ' ', name)
    # Pulisci
    name = name.strip(' -–')
    return name


# === URL/PARSING HELPERS ===

def clean_url(url: str) -> str:
    url = unquote(url)
    url = re.sub(r'&hilit=[^&]*', '', url)
    url = re.sub(r'&sid=[^&]*', '', url)
    if not url.startswith('http'):
        url = urljoin(BASE_URL, url)
    return url


def get_topic_id(url: str) -> Optional[str]:
    match = re.search(r'[?&]t=(\d+)', url)
    return match.group(1) if match else None


def get_post_id(url: str) -> Optional[str]:
    match = re.search(r'[?&]p=(\d+)', url)
    return match.group(1) if match else None


def get_infohash(magnet: str) -> Optional[str]:
    match = re.search(r'btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', magnet)
    return match.group(1).upper() if match else None


def extract_size_from_text(text: str) -> int:
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
    is_4k = bool(re.search(r'\b(2160p|4K|UHD)\b', title, re.I))
    if forum_id in [25, 26, 34, 36]:
        return 15*1024**3 if is_4k else 10*1024**3
    elif forum_id in TV_FORUM_IDS:
        return 5*1024**3 if is_4k else 2*1024**3
    return 512*1024**2


def extract_episode_info(text: str) -> Optional[Dict[str, Any]]:
    """Estrae info episodio dal nome del magnet."""
    patterns = [
        r'[Ss](\d{1,2})[\.\s]?[Ee](\d{1,3})(?:-[Ee]?(\d{1,3}))?',
        r'(\d{1,2})[xX](\d{1,3})(?:-(\d{1,3}))?',
        r'[Ss]tagion[ei]\s*(\d{1,2}).*?[Ee]pisodio\s*(\d{1,3})',
        r'[Ss]eason\s*(\d{1,2}).*?[Ee]pisode\s*(\d{1,3})',
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            return {
                'season': int(groups[0]),
                'episode': int(groups[1]),
                'episode_end': int(groups[2]) if len(groups) > 2 and groups[2] else None,
            }

    return None


def extract_pack_info(text: str) -> Optional[Dict[str, Any]]:
    """
    Rileva se il nome indica un pack di stagione/i (non singolo episodio).
    Ritorna:
    - {"season": X, "is_pack": True} per pack stagione singola
    - {"season_start": X, "season_end": Y, "is_pack": True} per multi-season pack
    - None se non è un pack
    """
    # Prima verifica che NON sia un singolo episodio
    if extract_episode_info(text):
        return None

    # Pattern per multi-season pack (S01-S05, Stagioni 1-8, etc.)
    multi_patterns = [
        r'[Ss](\d{1,2})\s*[-–]\s*[Ss]?(\d{1,2})',           # S01-S05, S1-5
        r'[Ss]tagion[ei]\s*(\d{1,2})\s*[-–]\s*(\d{1,2})',   # Stagioni 1-8
        r'[Ss]eason[s]?\s*(\d{1,2})\s*[-–]\s*(\d{1,2})',    # Seasons 1-8
    ]

    for pattern in multi_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return {
                'season_start': int(match.group(1)),
                'season_end': int(match.group(2)),
                'is_pack': True,
            }

    # Pattern per season pack singola (S01 Complete, Stagione 1 Completa, etc.)
    single_pack_patterns = [
        r'[Ss](\d{1,2})\s*[.-]?\s*[Cc]omplet[ae]',          # S01 Complete/Completa
        r'[Ss]tagion[ei]\s*(\d{1,2})\s*[Cc]omplet[ae]',     # Stagione 1 Completa
        r'[Ss]eason\s*(\d{1,2})\s*[Cc]omplete',              # Season 1 Complete
        r'[Cc]omplet[ae]\s*[Ss]tagion[ei]\s*(\d{1,2})',     # Completa Stagione 1
        r'[Cc]omplete\s*[Ss]eason\s*(\d{1,2})',              # Complete Season 1
    ]

    for pattern in single_pack_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return {
                'season': int(match.group(1)),
                'is_pack': True,
            }

    # Pattern per stagione sola senza episodio (es. "Show.S01.1080p" senza E##)
    # Questo è ambiguo, potrebbe essere pack o nome incompleto
    # Lo marchiamo come pack solo se ha indicatori come 1080p/720p/Complete
    season_only = re.search(r'[Ss](\d{1,2})(?:\.|$|\s)(?!E\d)', text)
    if season_only:
        # Verifica che abbia indicatori di qualità (suggerisce pack completo)
        if re.search(r'\b(1080p|720p|2160p|4K|UHD|WEB-?DL|BluRay|HDTV)\b', text, re.I):
            return {
                'season': int(season_only.group(1)),
                'is_pack': True,
                'uncertain': True,  # Flag per indicare che non siamo sicuri
            }

    return None


def extract_name_from_magnet(magnet: str) -> str:
    match = re.search(r'dn=([^&]+)', magnet)
    return unquote(match.group(1)).replace('+', ' ') if match else ""


# === THREAD CONTENT FUNCTIONS ===

def fetch_thread_content(topic_url: str) -> Tuple[Optional[BeautifulSoup], Optional[str]]:
    """
    Carica contenuto thread SENZA cliccare Thanks.
    Usato durante la ricerca per thread già ringraziati.
    """
    scraper = session.get_scraper()
    topic_url = clean_url(topic_url)

    try:
        r = scraper.get(topic_url, timeout=30)
        if r.status_code != 200:
            return None, None
        return BeautifulSoup(r.text, "lxml"), r.text
    except Exception as e:
        logger.error(f"fetch_thread_content error: {e}")
        return None, None


def fetch_thread_and_click_thanks(topic_url: str) -> Tuple[Optional[BeautifulSoup], Optional[str], bool]:
    """
    Carica thread E clicca Thanks se necessario.
    Usato SOLO durante il download.
    """
    global thanks_cache

    scraper = session.get_scraper()
    topic_url = clean_url(topic_url)
    topic_id = get_topic_id(topic_url)

    logger.info(f"=== FETCH+THANKS: {topic_url} ===")

    try:
        r = scraper.get(topic_url, timeout=30)
        if r.status_code != 200:
            return None, None, False

        soup = BeautifulSoup(r.text, "lxml")

        # Già ringraziato?
        if topic_id and topic_id in thanks_cache:
            logger.info("Already thanked (cache)")
            return soup, r.text, False

        # Trova primo post e pulsante Thanks
        first_post = soup.select_one("div.post")
        if not first_post:
            return soup, r.text, False

        quote_link = first_post.select_one("a[href*='mode=quote']")
        if not quote_link:
            return soup, r.text, False

        first_post_id = get_post_id(quote_link.get("href", ""))
        if not first_post_id:
            return soup, r.text, False

        # Cerca pulsante Thanks per il primo post
        thanks_link = None
        for a in soup.find_all("a", href=lambda x: x and "thanks=" in str(x)):
            href = a.get("href", "")
            if f"p={first_post_id}" in href or f"thanks={first_post_id}" in href:
                thanks_link = href
                break

        if thanks_link:
            logger.info(f"Clicking Thanks: {thanks_link}")
            thanks_url = urljoin(BASE_URL, thanks_link)

            try:
                scraper.get(thanks_url, timeout=30)
                time.sleep(1)

                # Ricarica pagina
                r = scraper.get(topic_url, timeout=30)
                soup = BeautifulSoup(r.text, "lxml")

                if topic_id:
                    thanks_cache.add(topic_id)
                    save_thanks_cache()

                return soup, r.text, True

            except Exception as e:
                logger.error(f"Thanks click failed: {e}")
        else:
            logger.info("No thanks button (already thanked)")
            if topic_id:
                thanks_cache.add(topic_id)
                save_thanks_cache()

        return soup, r.text, False

    except Exception as e:
        logger.exception(f"fetch_thread_and_click_thanks error: {e}")
        return None, None, False


def extract_magnets_from_soup(soup: BeautifulSoup, html: str) -> List[Dict[str, Any]]:
    """Estrae magnets dal contenuto HTML."""
    results = []

    first_post = soup.select_one("div.post div.content")
    if not first_post:
        return []

    post_text = first_post.get_text()
    default_size = extract_size_from_text(post_text)

    magnet_links = first_post.find_all("a", href=lambda x: x and str(x).startswith("magnet:"))

    if not magnet_links:
        # Cerca nel raw HTML
        magnets_raw = re.findall(r'magnet:\?xt=urn:btih:[a-fA-F0-9]{40}[^\s"\'<>]*', html)
        magnets_raw += re.findall(r'magnet:\?xt=urn:btih:[a-zA-Z2-7]{32}[^\s"\'<>]*', html)
        for m in magnets_raw:
            magnet = re.sub(r'\s+', '', m)
            infohash = get_infohash(magnet)
            if not infohash:
                continue
            name = extract_name_from_magnet(magnet)
            episode_info = extract_episode_info(name)
            pack_info = extract_pack_info(name) if not episode_info else None
            results.append({
                "magnet": magnet,
                "infohash": infohash,
                "name": name,
                "size": default_size,
                "episode_info": episode_info,
                "pack_info": pack_info,
            })
    else:
        for link in magnet_links:
            magnet = re.sub(r'\s+', '', link.get("href", ""))
            infohash = get_infohash(magnet)
            if not infohash:
                continue

            name = extract_name_from_magnet(magnet) or link.get_text(strip=True)
            episode_info = extract_episode_info(name)
            pack_info = extract_pack_info(name) if not episode_info else None
            results.append({
                "magnet": magnet,
                "infohash": infohash,
                "name": name,
                "size": default_size,
                "episode_info": episode_info,
                "pack_info": pack_info,
            })

    # Dedup
    seen = set()
    unique = []
    for r in results:
        if r["infohash"] not in seen:
            seen.add(r["infohash"])
            unique.append(r)

    return unique


# === SEARCH ===

def search_mircrew(query: str, categories: List[int] = None,
                   target_season: int = None, target_episode: int = None) -> List[Dict]:
    """
    Ricerca su MIRCrew.
    - Filtra per stagione se specificata
    - Thread multi-stagione: thanked=expand magnets, non-thanked=thread-level result
    - Thread singola stagione: thanked=expand, non-thanked=synthetic episodes
    - NON clicca Thanks durante la ricerca
    """
    scraper = session.get_scraper()

    # Non usare +keyword (richiede match esatto per ogni parola)
    # phpBB cerca tutte le parole di default con terms=all
    keywords = query if query else str(datetime.now().year)

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
        logger.info(f"Search '{query}': status={r.status_code}, season={target_season}, ep={target_episode}")

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

                # Check multi-stagione (non skippiamo, ma gestiamo diversamente)
                is_multi_season = is_multi_season_title(thread_title)

                # Filtra per stagione se specificata
                if target_season is not None:
                    if not title_matches_season(thread_title, target_season):
                        logger.debug(f"SKIP season mismatch: {thread_title[:40]}...")
                        continue

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

                # Per contenuti già ringraziati (TV o film): espandi magnets
                if is_thanked:
                    logger.info(f"Expanding thanked {'TV' if is_tv else 'movie'}: {thread_title[:40]}...")
                    soup_thread, html = fetch_thread_content(url)

                    if soup_thread and html:
                        magnets = extract_magnets_from_soup(soup_thread, html)

                        # Filtra per episodio se specificato (solo per TV)
                        if is_tv and target_episode is not None:
                            magnets = [m for m in magnets
                                       if m.get("episode_info") and
                                          m["episode_info"]["episode"] == target_episode]

                        for mag in magnets:
                            title = mag["name"] if mag["name"] else thread_title

                            results.append({
                                "title": title,
                                "link": url,
                                "topic_id": topic_id,
                                "infohash": mag["infohash"],
                                "guid": f"{topic_id}-{mag['infohash'][:8]}",
                                "pubDate": pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                                "size": mag["size"],
                                "category": CATEGORY_MAP.get(forum_id, 5000 if is_tv else 2000),
                                "forum_id": forum_id,
                                "seeders": 10,  # Boost per già ringraziati
                                "peers": 1,
                                "episode_info": mag["episode_info"],
                                "pack_info": mag.get("pack_info"),
                            })

                        if magnets:
                            logger.info(f"  -> {len(magnets)} magnets")
                            continue

                # Per TV non ringraziati: genera risultati sintetici per episodio
                # (ma solo per thread a stagione singola - multi-season va a thread-level)
                if is_tv and not is_thanked and not is_multi_season:
                    title_season = extract_season_from_title(thread_title) or 1
                    episode_count = extract_episode_count_from_title(thread_title)
                    show_name = generate_show_name_from_title(thread_title)

                    # Se abbiamo un conteggio episodi, genera risultati sintetici
                    if episode_count and episode_count > 0:
                        logger.info(f"Generating {episode_count} synthetic episodes for: {thread_title[:40]}...")

                        for ep_num in range(1, episode_count + 1):
                            # Filtra per episodio se specificato
                            if target_episode is not None and ep_num != target_episode:
                                continue

                            synthetic_title = f"{show_name} S{title_season:02d}E{ep_num:02d}"

                            results.append({
                                "title": synthetic_title,
                                "link": url,
                                "topic_id": topic_id,
                                "infohash": None,  # Sconosciuto fino al download
                                "guid": f"{topic_id}-S{title_season}E{ep_num}",
                                "pubDate": pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                                "size": get_default_size(forum_id, thread_title),
                                "category": CATEGORY_MAP.get(forum_id, 5000),
                                "forum_id": forum_id,
                                "seeders": 1,
                                "peers": 1,
                                "episode_info": {"season": title_season, "episode": ep_num},
                            })
                        continue

                # Thread-level result (film, o TV senza info episodi)
                results.append({
                    "title": thread_title,
                    "link": url,
                    "topic_id": topic_id,
                    "infohash": None,
                    "guid": topic_id,
                    "pubDate": pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                    "size": get_default_size(forum_id, thread_title),
                    "category": CATEGORY_MAP.get(forum_id, 2000 if not is_tv else 5000),
                    "forum_id": forum_id,
                    "seeders": 1,
                    "peers": 1,
                    "episode_info": None,
                })

            except Exception as e:
                logger.warning(f"Parse error: {e}")

        logger.info(f"Search returned {len(results)} results")
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
    return jsonify({"status": "ok", "service": "MIRCrew Proxy", "version": "5.8.0"})


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "version": "5.8.0",
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

    # Parse season/episode from Prowlarr params
    target_season = None
    target_episode = None

    season_param = request.args.get("season")
    ep_param = request.args.get("ep")

    if season_param:
        try:
            target_season = int(season_param)
        except:
            pass

    if ep_param:
        try:
            target_episode = int(ep_param)
        except:
            pass

    # Fallback: parse from query
    if target_season is None:
        target_season = extract_season_from_query(query)
    if target_episode is None:
        target_episode = extract_episode_from_query(query)

    forum_ids = None
    if cat_str:
        torznab_cats = [int(c) for c in cat_str.split(",") if c.isdigit()]
        if torznab_cats:
            forum_ids = [fid for fid, tcat in CATEGORY_MAP.items() if tcat in torznab_cats]

    results = search_mircrew(query, forum_ids, target_season, target_episode)

    items = ""
    for r in results:
        ep_info = r.get("episode_info")
        pack_info = r.get("pack_info")

        # Costruisci download URL con tutti i parametri necessari
        if r.get("infohash"):
            dl_url = f"http://{request.host}/download?topic_id={r['topic_id']}&infohash={r['infohash']}"
        elif ep_info:
            # Risultato sintetico: include season/ep per download
            dl_url = f"http://{request.host}/download?topic_id={r['topic_id']}&season={ep_info['season']}&ep={ep_info['episode']}"
        else:
            dl_url = f"http://{request.host}/download?topic_id={r['topic_id']}"

        # Attributi Torznab per season/episode/pack
        season_attr = ""
        episode_attr = ""

        if ep_info:
            # Episodio singolo: season + episode
            season_attr = f'<torznab:attr name="season" value="{ep_info["season"]}"/>'
            episode_attr = f'<torznab:attr name="episode" value="{ep_info["episode"]}"/>'
        elif pack_info:
            # Season pack: solo season, NO episode (Sonarr capisce che è un pack)
            if pack_info.get("season"):
                season_attr = f'<torznab:attr name="season" value="{pack_info["season"]}"/>'
            elif pack_info.get("season_start"):
                # Multi-season pack: mettiamo la prima stagione
                # Sonarr/Prowlarr dovrebbe capire dal titolo che è multi-season
                season_attr = f'<torznab:attr name="season" value="{pack_info["season_start"]}"/>'

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
{season_attr}
{episode_attr}
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
    Download magnet - SOLO QUI si clicca Thanks se necessario.
    Supporta:
    - infohash: ritorna magnet con hash esatto
    - season + ep: cerca magnet corrispondente a SxxExx
    - nessuno: ritorna primo magnet (per film)
    """
    topic_id = request.args.get("topic_id")
    infohash = request.args.get("infohash", "").upper()

    # Parametri season/episode per risultati sintetici
    season_param = request.args.get("season")
    ep_param = request.args.get("ep")

    target_season = None
    target_episode = None

    if season_param:
        try:
            target_season = int(season_param)
        except:
            pass
    if ep_param:
        try:
            target_episode = int(ep_param)
        except:
            pass

    if not topic_id:
        return "Missing topic_id", 400

    url = f"{BASE_URL}/viewtopic.php?t={topic_id}"
    logger.info(f"=== DOWNLOAD: topic={topic_id}, infohash={infohash or 'N/A'}, S{target_season}E{target_episode} ===")

    # Fetch con Thanks (solo qui!)
    soup, html, thanks_clicked = fetch_thread_and_click_thanks(url)

    if not soup or not html:
        return "Failed to load thread", 500

    magnets = extract_magnets_from_soup(soup, html)

    if not magnets:
        return "No magnets found", 404

    # 1. Se infohash specificato, cerca quello esatto
    if infohash:
        for m in magnets:
            if m["infohash"] == infohash:
                logger.info(f"Found by infohash: {m['name'][:50]}...")
                return Response(status=302, headers={"Location": m["magnet"]})

        logger.error(f"Infohash {infohash} not found!")
        return f"Infohash {infohash} not found", 404

    # 2. Se season/episode specificati, cerca magnet corrispondente
    if target_season is not None and target_episode is not None:
        for m in magnets:
            ep_info = m.get("episode_info")
            if ep_info and ep_info["season"] == target_season and ep_info["episode"] == target_episode:
                logger.info(f"Found S{target_season:02d}E{target_episode:02d}: {m['name'][:50]}...")
                return Response(status=302, headers={"Location": m["magnet"]})

        # Non trovato - log tutti i magnets disponibili per debug
        available = [f"S{m['episode_info']['season']:02d}E{m['episode_info']['episode']:02d}"
                    for m in magnets if m.get("episode_info")]
        logger.error(f"S{target_season:02d}E{target_episode:02d} not found! Available: {available}")
        return f"Episode S{target_season:02d}E{target_episode:02d} not found. Available: {available}", 404

    # 3. Nessun filtro: ritorna primo magnet (per film o fallback)
    logger.info(f"Returning first: {magnets[0]['name'][:50]}...")
    return Response(status=302, headers={"Location": magnets[0]["magnet"]})


@app.route("/thread/<topic_id>")
def thread_info(topic_id):
    """Debug endpoint."""
    url = f"{BASE_URL}/viewtopic.php?t={topic_id}"

    # Usa fetch senza thanks per debug
    is_thanked = topic_id in thanks_cache
    soup, html = fetch_thread_content(url)

    if not soup or not html:
        return jsonify({"error": "Failed to load thread"})

    magnets = extract_magnets_from_soup(soup, html)

    return jsonify({
        "topic_id": topic_id,
        "url": url,
        "thanked": is_thanked,
        "magnets_visible": len(magnets),
        "magnets": [{
            "infohash": m["infohash"][:12] + "...",
            "name": m["name"][:60],
            "episode": m["episode_info"]
        } for m in magnets]
    })


if __name__ == "__main__":
    if not USERNAME or not PASSWORD:
        logger.error("MIRCREW_USERNAME and MIRCREW_PASSWORD required!")
        exit(1)

    logger.info(f"=== MIRCrew Proxy v5.8 starting on {HOST}:{PORT} ===")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
