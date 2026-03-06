#!/usr/bin/env python3
"""
MIRCrew Proxy Server per Prowlarr - v8.0
- NO Thanks durante ricerca (solo al download)
- Filtro stagione da titolo thread
- Espansione magnets per contenuti già ringraziati (TV e film)
- Risultati sintetici per episodio (thread TV non ringraziati)
- Download con season/ep params per episodio specifico
- Attributi Torznab season/episode per Sonarr
- Multi-season threads (thanked=expand, non-thanked=thread-level)
- Riconoscimento season pack per Sonarr
- v8.0: Fix Docker Cloudflare bypass - GPU rendering via Mesa/SwiftShader,
         profilo Chrome persistente, interazione attiva Turnstile,
         Xvfb 32-bit, Chrome flags anti-detection migliorati
"""

import os
import re
import json
import time
import logging
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urljoin, quote_plus, unquote, urlencode

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
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


def _get_chromium_version() -> Optional[int]:
    """Detect installed Chromium major version for undetected_chromedriver compatibility."""
    chrome_path = os.getenv("CHROME_PATH", "/usr/bin/chromium")
    try:
        result = subprocess.run(
            [chrome_path, "--version"],
            capture_output=True, text=True, timeout=10
        )
        match = re.search(r'(\d+)\.', result.stdout)
        if match:
            version = int(match.group(1))
            logger.info(f"Detected Chromium version: {version}")
            return version
    except Exception as e:
        logger.warning(f"Could not detect Chromium version: {e}")
    return None


def _try_click_turnstile(driver) -> bool:
    """
    Cerca e clicca il checkbox Turnstile dentro l'iframe Cloudflare.
    Ritorna True se ha trovato e cliccato qualcosa.
    """
    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "challenges.cloudflare.com" in src or "turnstile" in src:
                logger.info(f"Found Turnstile iframe: {src[:80]}...")
                driver.switch_to.frame(iframe)
                try:
                    # Prova a trovare il checkbox/widget cliccabile
                    clickable = None
                    for selector in [
                        "input[type='checkbox']",
                        ".ctp-checkbox-label",
                        "#challenge-stage",
                        "label",
                    ]:
                        try:
                            clickable = WebDriverWait(driver, 3).until(
                                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                            )
                            if clickable:
                                break
                        except (TimeoutException, NoSuchElementException):
                            continue

                    if clickable:
                        clickable.click()
                        logger.info("Clicked Turnstile widget!")
                        return True
                    else:
                        logger.debug("Turnstile iframe found but no clickable element")
                except Exception as e:
                    logger.debug(f"Turnstile iframe interaction error: {e}")
                finally:
                    driver.switch_to.default_content()
    except Exception as e:
        logger.debug(f"Turnstile search error: {e}")
    return False


def _wait_for_cloudflare(driver, timeout: int = 120) -> bool:
    """
    Attende che il challenge Cloudflare si risolva.
    Prima aspetta la risoluzione automatica (10s), poi prova a cliccare il Turnstile.
    """
    logger.info("Waiting for Cloudflare challenge...")
    start = time.time()
    last_log = 0
    turnstile_clicked = False

    while time.time() - start < timeout:
        try:
            title = driver.title.lower()
            elapsed = int(time.time() - start)
            # Log progress every 15 seconds
            if elapsed - last_log >= 15:
                logger.info(f"Cloudflare wait: {elapsed}s elapsed, page title='{driver.title[:80]}'")
                try:
                    body_len = len(driver.find_element(By.TAG_NAME, "body").text)
                    logger.info(f"Cloudflare wait: body length={body_len}")
                except Exception:
                    logger.info("Cloudflare wait: could not read body")
                last_log = elapsed

            if "just a moment" not in title and "attention" not in title:
                # Verifica che la pagina abbia contenuto reale
                try:
                    body = driver.find_element(By.TAG_NAME, "body").text
                    if len(body) > 100:
                        logger.info(f"Cloudflare challenge passed after {elapsed}s!")
                        return True
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Cloudflare wait error: {e}")

        # Dopo 8 secondi, prova a cliccare il Turnstile (una volta sola)
        if not turnstile_clicked and elapsed > 8:
            logger.info("Auto-solve not working, trying to click Turnstile...")
            turnstile_clicked = _try_click_turnstile(driver)
            if turnstile_clicked:
                # Dopo il click, aspetta un po' di più per la risoluzione
                time.sleep(5)
                continue

        # Se il primo click non ha funzionato, riprova dopo 30s
        if turnstile_clicked and elapsed > 40:
            logger.info("Retrying Turnstile click...")
            turnstile_clicked = False  # reset per permettere un nuovo tentativo

        time.sleep(2)

    # On timeout, save page source for debugging
    try:
        logger.warning(f"Cloudflare wait timed out after {timeout}s, title='{driver.title[:80]}'")
        (DATA_DIR / "debug_cloudflare_timeout.html").write_text(driver.page_source[:50000])
        logger.info("Saved debug page to debug_cloudflare_timeout.html")
    except Exception:
        logger.warning(f"Cloudflare wait timed out after {timeout}s")
    return False


def _browser_login() -> Optional[Dict[str, Any]]:
    """
    Synchronous browser-based login using undetected_chromedriver.
    Handles Cloudflare bypass AND phpBB login in a single browser session.
    Returns dict with cookies and userAgent on success, None on failure.
    """
    driver = None
    try:
        logger.info("=== BROWSER LOGIN START ===")

        # Detect chromium version
        version_main = _get_chromium_version()

        # Configure Chrome options - mimare browser reale il più possibile
        options = uc.ChromeOptions()
        options.add_argument("--no-first-run")
        options.add_argument("--no-service-autorun")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=it-IT")
        options.add_argument("--no-sandbox")

        # GPU/WebGL - Chrome's bundled SwiftShader (no ANGLE/Vulkan dependency)
        # Produces realistic Canvas/WebGL fingerprints in Docker
        options.add_argument("--use-gl=swiftshader")
        options.add_argument("--enable-webgl")
        options.add_argument("--in-process-gpu")

        # Profilo persistente - Cloudflare riconosce "returning visitor"
        chrome_profile = str(DATA_DIR / "chrome-profile")
        os.makedirs(chrome_profile, exist_ok=True)
        options.add_argument(f"--user-data-dir={chrome_profile}")

        # NB: --disable-dev-shm-usage RIMOSSO - flag container-specifico rilevabile
        # shm_size=512m in docker-compose rende questo flag non necessario

        chrome_path = os.getenv("CHROME_PATH", "/usr/bin/chromium")

        logger.info(f"Starting undetected_chromedriver (version_main={version_main})...")
        driver_kwargs = {
            "options": options,
            "browser_executable_path": chrome_path,
            "headless": False,  # Must be False for Cloudflare bypass (Xvfb provides virtual display)
        }
        if version_main:
            driver_kwargs["version_main"] = version_main

        driver = uc.Chrome(**driver_kwargs)

        # Navigate to login page
        login_url = f"{BASE_URL}/ucp.php?mode=login"
        logger.info(f"Navigating to {login_url}...")
        driver.get(login_url)

        # Wait for Cloudflare challenge to resolve
        _wait_for_cloudflare(driver, timeout=120)

        # Wait for login form to appear
        try:
            WebDriverWait(driver, 45).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            logger.info("Login form found")
        except TimeoutException:
            logger.error("Login form not found after waiting")
            try:
                (DATA_DIR / "debug_browser_page.html").write_text(driver.page_source[:50000])
            except Exception:
                pass
            return None

        # Check if already logged in
        if "mode=logout" in driver.page_source:
            logger.info("Already logged in!")
        else:
            # Fill login form
            logger.info("Filling login form...")
            username_input = driver.find_element(By.NAME, "username")
            username_input.clear()
            username_input.send_keys(USERNAME)

            password_input = driver.find_element(By.NAME, "password")
            password_input.clear()
            password_input.send_keys(PASSWORD)

            # Check autologin if available
            try:
                autologin = driver.find_element(By.NAME, "autologin")
                if not autologin.is_selected():
                    autologin.click()
            except NoSuchElementException:
                pass

            time.sleep(0.5)

            # Click login button
            logger.info("Clicking login button...")
            try:
                driver.find_element(By.NAME, "login").click()
            except NoSuchElementException:
                driver.find_element(By.NAME, "password").submit()

            # Wait for login to complete
            time.sleep(4)

            # Check login result
            page_source = driver.page_source.lower()
            logged_in = any(ind in page_source for ind in [
                "logout", "esci", "ucp.php?mode=logout"
            ])

            if not logged_in:
                # Check for error message
                try:
                    soup = BeautifulSoup(driver.page_source, "lxml")
                    error_div = soup.find("div", class_="error")
                    if error_div:
                        logger.error(f"Login error: {error_div.get_text(strip=True)[:200]}")
                    else:
                        title_el = soup.find("title")
                        logger.error(f"Login failed - page: {title_el.get_text(strip=True) if title_el else 'unknown'}")
                except Exception:
                    logger.error("Login failed - could not parse error")

                try:
                    (DATA_DIR / "debug_browser_login_failed.html").write_text(driver.page_source[:50000])
                except Exception:
                    pass
                return None

        logger.info("=== BROWSER LOGIN SUCCESS ===")

        # Extract cookies as simple {name: value} dict
        cookies = {}
        for cookie in driver.get_cookies():
            cookies[cookie["name"]] = cookie["value"]

        # Extract user agent
        user_agent = driver.execute_script("return navigator.userAgent;")

        logger.info(f"Extracted {len(cookies)} cookies, UA={user_agent[:50]}...")
        return {"cookies": cookies, "userAgent": user_agent}

    except Exception as e:
        logger.exception(f"Browser login error: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
                logger.info("Browser closed. Memory freed.")
            except Exception:
                pass


class MircrewSession:
    def __init__(self):
        self.scraper = None  # Named 'scraper' for compatibility with rest of codebase
        self.session_valid = False
        self.last_login = 0
        self.cf_user_agent = None
        self._init_scraper()
        self._load_cookies()

    def _init_scraper(self, user_agent: str = None):
        """Initialize a plain requests.Session with browser-like headers."""
        self.scraper = requests.Session()
        self.scraper.headers.update({
            "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": BASE_URL,
        })

    def _save_cookies(self):
        """Save cookies in simplified {name: value} format."""
        try:
            cookies = {c.name: c.value for c in self.scraper.cookies}
            with open(COOKIES_FILE, 'w') as f:
                json.dump({
                    'cookies': cookies,
                    'time': time.time(),
                    'userAgent': self.cf_user_agent,
                }, f)
        except Exception as e:
            logger.warning(f"Save cookies error: {e}")

    def _load_cookies(self):
        """Load cookies, handling both old {name: {value, domain}} and new {name: value} formats."""
        try:
            if COOKIES_FILE.exists():
                with open(COOKIES_FILE) as f:
                    data = json.load(f)
                if time.time() - data.get('time', 0) < 43200:  # 12 hours
                    domain = BASE_URL.split("//")[-1].split("/")[0]
                    for name, value in data.get('cookies', {}).items():
                        if isinstance(value, dict):
                            # Old format: {name: {value: ..., domain: ...}}
                            self.scraper.cookies.set(name, value.get('value', ''), domain=value.get('domain', domain))
                        else:
                            # New format: {name: value}
                            self.scraper.cookies.set(name, value, domain=domain)
                    if data.get('userAgent'):
                        self.cf_user_agent = data['userAgent']
                        self.scraper.headers.update({"User-Agent": data['userAgent']})
                    return True
        except Exception:
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
        except Exception:
            pass

        if self._do_login():
            return self.scraper
        return self.scraper

    def _do_login(self) -> bool:
        """
        Login v8.0 - Browser-based login con Chrome flags anti-detection,
        GPU rendering via SwiftShader, profilo persistente, e click Turnstile attivo.
        Retries up to 2 times with delay on failure.
        """
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    delay = 20
                    logger.info(f"Login retry {attempt}/{max_attempts} after {delay}s delay...")
                    time.sleep(delay)

                result = _browser_login()

                if not result:
                    logger.error(f"Browser login failed (attempt {attempt}/{max_attempts})")
                    continue

                cookies = result["cookies"]
                user_agent = result["userAgent"]

                # Re-initialize session with browser user-agent
                self._init_scraper(user_agent=user_agent)

                # Apply cookies
                domain = BASE_URL.split("//")[-1].split("/")[0]
                for name, value in cookies.items():
                    self.scraper.cookies.set(name, value, domain=domain)

                self.cf_user_agent = user_agent
                self.session_valid = True
                self.last_login = time.time()
                self._save_cookies()

                logger.info(f"Applied {len(cookies)} cookies to requests.Session")
                return True

            except Exception as e:
                logger.exception(f"Login exception (attempt {attempt}/{max_attempts}): {e}")

        logger.error(f"All {max_attempts} login attempts failed - Cloudflare Turnstile non superato")
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
    return jsonify({"status": "ok", "service": "MIRCrew Proxy", "version": "6.0.3"})


@app.route("/health")
def health():
    cookies_age = 0
    cookies_valid = False
    try:
        if COOKIES_FILE.exists():
            with open(COOKIES_FILE) as f:
                data = json.load(f)
            cookies_age = round((time.time() - data.get('time', 0)) / 3600, 1)
            cookies_valid = cookies_age < 12
    except Exception:
        pass

    return jsonify({
        "status": "ok",
        "version": "8.0",
        "logged_in": session.session_valid,
        "cookies_valid": cookies_valid,
        "cookies_age_hours": cookies_age,
        "thanks_cached": len(thanks_cache)
    })


@app.route("/api")
def api():
    received_key = request.args.get("apikey", "")
    if API_KEY and received_key != API_KEY:
        logger.warning(f"API key mismatch: received='{received_key}', expected='{API_KEY}'")
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

    # Startup debug: log all configuration
    logger.info(f"=== MIRCrew Proxy v8.0 starting on {HOST}:{PORT} ===")
    logger.info(f"Config: BASE_URL={BASE_URL}")
    logger.info(f"Config: USERNAME={USERNAME[:3]}*** (set)")
    logger.info(f"Config: PASSWORD={'***' if PASSWORD else 'NOT SET'}")
    logger.info(f"Config: API_KEY={API_KEY}")
    logger.info(f"Config: PROXY_HOST={HOST}, PROXY_PORT={PORT}")
    logger.info(f"Config: DATA_DIR={DATA_DIR}")
    logger.info(f"Config: LOG_LEVEL={os.getenv('LOG_LEVEL', 'INFO')}")
    logger.info(f"Config: COOKIES_FILE={COOKIES_FILE} (exists={COOKIES_FILE.exists()})")

    # Check Xvfb
    try:
        xvfb_check = subprocess.run(["pgrep", "-a", "Xvfb"], capture_output=True, text=True, timeout=5)
        if xvfb_check.stdout.strip():
            logger.info(f"Xvfb: running ({xvfb_check.stdout.strip()})")
        else:
            logger.warning("Xvfb: NOT running - browser login may fail!")
    except Exception:
        logger.warning("Xvfb: could not check status")

    # Check Chromium
    chrome_ver = _get_chromium_version()
    if chrome_ver:
        logger.info(f"Chromium: version {chrome_ver} detected")
    else:
        logger.warning("Chromium: NOT found or version detection failed")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
