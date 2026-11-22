"""Implementazione MIRCrew: sessione con login phpBB e sito Torznab."""

import ctypes
import gc
import json
import re
import threading
import time
import logging
from contextlib import contextmanager
from datetime import datetime
from itertools import zip_longest
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import Config
from session import ByparrSession
from torznab.server import BaseSite, SiteUnavailable
from torznab.models import TorznabResult

from .constants import CATEGORY_MAP as DEFAULT_CATEGORY_MAP
from .constants import ALLOWED_FORUM_IDS as DEFAULT_ALLOWED_FORUM_IDS
from .constants import FORUM_IDS as DEFAULT_FORUM_IDS
from .constants import TV_FORUM_IDS as DEFAULT_TV_FORUM_IDS
from .constants import CAPABILITIES_XML as DEFAULT_CAPABILITIES_XML
from . import parser, request_topics

logger = logging.getLogger("mircrew")

# Default CSS selectors
DEFAULT_SELECTORS = {
    "search_result_row": "li.row",
    "topic_title_link": "a.topictitle",
    "forum_link": "a[href*='viewforum.php']",
    "pub_date": "time[datetime]",
    "first_post": "div.post",
    "post_content": "div.post div.content",
    "quote_link": "a[href*='mode=quote']",
    "magnet_link": "a[href^='magnet:']",
}

# Default search params – minimal set matching the manual forum search
# (only sf + sr; extra params like sc/sk/sd/st/ch/t/submit caused phpBB
#  to return 0 results even though the manual search found matches)
DEFAULT_SEARCH_PARAMS = {
    "sf": "titleonly",
    "sr": "topics",
}


try:
    _malloc_trim = ctypes.CDLL("libc.so.6").malloc_trim
except (OSError, AttributeError):  # not glibc (Windows dev box, musl)
    _malloc_trim = None


def _release_memory():
    """Give the memory of finished searches back to the OS (#71).

    Freed pages otherwise stay in the process: RSS kept its peak for good.
    """
    gc.collect()
    if _malloc_trim:
        _malloc_trim(0)


def login_failure_reason(html: str) -> str:
    """What phpBB answered to a login that did not log in: page title and error text
    (wrong password, too many attempts, invalid form token...). Never the form fields."""
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else "no title"
    error = soup.select_one(".error, .errorbox")
    error_text = " ".join(error.get_text(" ", strip=True).split()) if error else "no error element"
    return f"page title '{title[:100]}', error '{error_text[:300]}'"


class PageError(SiteUnavailable):
    """One page answered an HTTP error: unlike a dead session, it says nothing about the other pages."""


class PageNotFound(PageError):
    """The forum answered 404 or 410: a gone topic, not an outage (a download answers 404)."""


class BudgetSpent(SiteUnavailable):
    """The search budget ran out: a read that already has partial results can return them."""


class MircrewSession(ByparrSession):
    """Sessione MIRCrew con login phpBB specifico."""

    def _check_logged_in(self, html: str) -> bool:
        return "mode=logout" in html

    def _do_login(self) -> bool:
        logger.info("=== LOGIN START ===")
        try:
            if not self.cf_valid:
                if not self._solve_cf():
                    logger.error("Cannot login: CF bypass failed")
                    return False

            r = self.get(f"{self.base_url}/ucp.php?mode=login")
            html = r.text if hasattr(r, "text") else str(r)
            soup = BeautifulSoup(html, "lxml")
            form = soup.find("form", {"id": "login"})
            if not form:
                logger.error(f"Login form not found: {login_failure_reason(html)}")
                logger.debug(f"Page content preview: {html[:500]}")
                return False

            fields = {}
            for inp in form.find_all("input", {"type": "hidden"}):
                if inp.get("name"):
                    fields[inp["name"]] = inp.get("value", "")

            sid = fields.get("sid", "")
            login_data = {
                "username": self.username, "password": self.password,
                "autologin": "on", "viewonline": "on",
                "redirect": fields.get("redirect", "index.php"),
                "creation_time": fields.get("creation_time", ""),
                "form_token": fields.get("form_token", ""),
                "sid": sid, "login": "Login",
            }

            time.sleep(0.5)
            r = self.post(
                f"{self.base_url}/ucp.php?mode=login&sid={sid}",
                data=login_data,
                headers={"Referer": f"{self.base_url}/ucp.php?mode=login"},
            )
            html = r.text if hasattr(r, "text") else str(r)

            if self._check_logged_in(html):
                logger.info("=== LOGIN SUCCESS ===")
                self._save_cookies()
                return True

            reason = login_failure_reason(html)
            logger.error(f"Login failed: {reason}")
            if r.status_code == 200 and not self._is_cf_blocked(r):
                self.login_refusal = reason  # the forum answered and said no
            return False
        except Exception as e:
            logger.exception(f"Login exception: {e}")
            return False


class MircrewSite(BaseSite):
    """Implementazione MIRCrew del sito Torznab."""

    def __init__(self, session: MircrewSession, config: Config):
        self.session = session
        self.config = config
        self._search_slots = threading.BoundedSemaphore(self.MAX_CONCURRENT_SEARCHES)
        self._running = 0  # searches running or queued
        self._running_lock = threading.Lock()
        self._budget = threading.local()  # this thread's search deadline (time.monotonic())
        self.thanks_cache: set = set()
        self.thanks_cache_file = config.data_dir / "thanks_cache.json"
        self._load_thanks_cache()

        # Load customizable config with fallbacks to defaults
        custom = config.custom or {}
        self.category_map = self._load_category_map(custom)
        self.forum_ids = list(self.category_map.keys())
        self.tv_forum_ids = set(custom.get("tv_forum_ids", DEFAULT_TV_FORUM_IDS))
        try:
            self.allowed_forum_ids = [int(f) for f in custom.get("allowed_forum_ids", DEFAULT_ALLOWED_FORUM_IDS)]
        except (TypeError, ValueError):
            logger.warning("Invalid allowed_forum_ids in config, using the default list")
            self.allowed_forum_ids = list(DEFAULT_ALLOWED_FORUM_IDS)
        if not self.allowed_forum_ids:
            logger.warning("allowed_forum_ids is empty: every search will return no results")
        self.selectors = {**DEFAULT_SELECTORS, **custom.get("selectors", {})}
        self.search_params = {**DEFAULT_SEARCH_PARAMS, **custom.get("search_params", {})}
        self.capabilities_xml = custom.get("capabilities_xml", DEFAULT_CAPABILITIES_XML)

    def _load_category_map(self, custom: dict) -> Dict[int, int]:
        """Load category map from custom config or defaults."""
        raw = custom.get("category_map")
        if not raw:
            return DEFAULT_CATEGORY_MAP.copy()
        # JSON keys are strings, convert to int
        return {int(k): int(v) for k, v in raw.items()}

    def get_capabilities_xml(self) -> str:
        return self.capabilities_xml

    def health_info(self) -> dict:
        session = self.session
        last_login_ago = int(time.time() - session.last_login) if session.last_login else None
        return {
            "status": "ok",
            "logged_in": session.session_valid,
            "login_blocked": session.login_blocked,
            "last_login_secs_ago": last_login_ago,
            "cf_valid": session.cf_valid,
            "cf_bypass_url": session.flaresolverr_url,
            "thanks_cached": len(self.thanks_cache),
        }

    def parse_season_from_query(self, query: str) -> Optional[int]:
        return parser.extract_season_from_query(query)

    def parse_episode_from_query(self, query: str) -> Optional[int]:
        return parser.extract_episode_from_query(query)

    # === SEARCH ===

    MAX_FALLBACK_ATTEMPTS = 5
    # Each running search holds parsed pages in memory; 16 at once (one per waitress
    # thread) pushed the process to its 256 MiB limit (#71). Extra searches queue here.
    MAX_CONCURRENT_SEARCHES = 3
    # Below Prowlarr's ~100 s request timeout: past it nobody reads the answer anyway.
    SEARCH_QUEUE_TIMEOUT = 60
    # Prowlarr gives up on a Torznab call after 100 s (.NET HttpClient default, not
    # configurable; measured in its log). Queue wait, logins and every page of one search
    # share this budget, counted from the moment the search arrives.
    SEARCH_BUDGET = 90
    PAGE_TIMEOUT = 30

    def search(self, query: str, categories: Optional[List[int]],
               target_season: Optional[int], target_episode: Optional[int]) -> List[TorznabResult]:
        deadline = time.monotonic() + self.SEARCH_BUDGET
        with self._running_lock:
            self._running += 1
        try:
            if not self._search_slots.acquire(timeout=self.SEARCH_QUEUE_TIMEOUT):
                logger.warning(f"Search '{query}' dropped: waited {self.SEARCH_QUEUE_TIMEOUT}s for a free slot")
                # Not an empty feed: Sonarr would take "no release exists" and not look again.
                raise SiteUnavailable(f"search queue full: no free slot after {self.SEARCH_QUEUE_TIMEOUT}s")
            self._budget.deadline = deadline
            self.session.set_deadline(deadline)  # lock waits and Byparr calls respect it too
            try:
                return self._search(query, categories, target_season, target_episode)
            finally:
                self._budget.deadline = None
                self.session.set_deadline(None)
                self._search_slots.release()
        finally:
            with self._running_lock:
                self._running -= 1
                idle = self._running == 0
            if idle:
                _release_memory()

    def _search(self, query: str, categories: Optional[List[int]],
                target_season: Optional[int], target_episode: Optional[int]) -> List[TorznabResult]:
        """Ricerca con normalizzazione, retry terms=any, fallback progressivo e ranking."""
        scraper = self._logged_in_scraper()

        normalized = parser.normalize_search_query(query)
        keywords = normalized if normalized else str(datetime.now().year)
        # Empty query (RSS sync / Prowlarr test) searches the current year: nothing to filter on.
        rq = normalized or None

        logger.info(f"Search query: '{query}' -> normalized: '{keywords}'")

        # Mappa categorie Torznab → forum IDs
        forum_ids = None
        if categories:
            mapped = [fid for fid, tcat in self.category_map.items() if tcat in categories]
            forum_ids = [fid for fid in mapped if fid in self.allowed_forum_ids]
            if mapped and not forum_ids:
                # e.g. Books/Audio: only dropped forums carry this category
                logger.info(f"Search '{query}': categories {categories} map only to forums that are not allowed")
                return []

        # Stage 1: terms=all (tutte le parole devono matchare)
        results = self._do_search(scraper, keywords, forum_ids, target_season, target_episode,
                                  terms="all", relevance_query=rq)
        stage = "1 (terms=all)"

        # Stage 1b: variante con apostrofi italiani ripristinati (es. Lamore → L'amore).
        # Tentata SOLO se la ricerca normale non trova nulla, così non corrompe
        # i titoli stranieri (Leftovers resta Leftovers se già trovato).
        if not results:
            apo = parser.restore_italian_apostrophes(keywords)
            if apo != keywords:
                logger.info(f"Retry with Italian apostrophe variant: '{apo}'")
                results = self._do_search(scraper, apo, forum_ids, target_season, target_episode,
                                          terms="all", relevance_query=rq)
                stage = "1b (apostrophe variant)"

        # Stage 2: terms=any (almeno una parola deve matchare)
        if not results and len(keywords.split()) > 1:
            logger.info(f"Retry search with terms=any for: '{keywords}'")
            results = self._do_search(scraper, keywords, forum_ids, target_season, target_episode,
                                      terms="any", relevance_query=rq)
            stage = "2 (terms=any)"

        # Stage 3: fallback progressivo con sottoinsiemi di keywords (limitato)
        if not results and len(keywords.split()) > 1:
            words = keywords.split()
            attempts = 0
            for length in range(len(words) - 1, 0, -1):
                for start in range(len(words) - length + 1):
                    if attempts >= self.MAX_FALLBACK_ATTEMPTS:
                        break
                    subset = ' '.join(words[start:start + length])
                    logger.info(f"Progressive fallback ({attempts + 1}/{self.MAX_FALLBACK_ATTEMPTS}): trying '{subset}'")
                    time.sleep(1)
                    results = self._do_search(scraper, subset, forum_ids,
                                               target_season, target_episode, terms="all",
                                               relevance_query=rq)
                    attempts += 1
                    if results:
                        stage = f"3 (keyword subset '{subset}')"
                        break
                if results or attempts >= self.MAX_FALLBACK_ATTEMPTS:
                    break

        # Stage 4: retry senza filtro categoria (tutti i forum ammessi, non solo quelli della categoria)
        if not results and forum_ids:
            logger.info(f"Retry without category filter for: '{keywords}'")
            results = self._do_search(scraper, keywords, None, target_season, target_episode,
                                      terms="all", relevance_query=rq)
            stage = "4 (no category filter)"

        logger.info(f"Search '{query}': {len(results)} results from stage {stage}" if results
                    else f"Search '{query}': no relevant results after all stages")

        # Ordina per rilevanza rispetto alla query originale
        if results:
            results.sort(
                key=lambda r: parser.compute_relevance_score(r.title, keywords, query),
                reverse=True,
            )

        return results

    def _logged_in_scraper(self):
        scraper = self.session.ensure_logged_in()
        if self.session.login_blocked and not self.session.session_valid:
            raise SiteUnavailable(f"login blocked after repeated refusals ({self.session.login_blocked}); "
                                  "save the credentials again or use Retry login in the admin panel")
        return scraper

    def _get_page(self, scraper, url, params=None):
        """GET a forum page (search or thread). A page showing that a valid session died
        (CF challenge, phpBB login page) invalidates it, logs in again and retries once; a
        second dead page, or a session that was already invalid, raises SiteUnavailable."""
        for attempt in (1, 2):
            left = self._time_left()
            if left is not None and left <= 0:
                raise BudgetSpent(f"search used up its {self.SEARCH_BUDGET}s budget")
            seen_gen = self.session.login_gen  # read before the request: see invalidate()
            was_valid = self.session.session_valid
            started = time.monotonic()
            r = scraper.get(url, params=params, timeout=self.PAGE_TIMEOUT if left is None else min(self.PAGE_TIMEOUT, left))
            if r.status_code in (404, 410):
                raise PageNotFound(f"page returned HTTP {r.status_code}")
            if r.status_code != 200:
                raise PageError(f"page returned HTTP {r.status_code}")

            # The phpBB header links sit ~7-12 KB into real pages, so the whole text is checked
            # (as _check_logged_in does); only the Cloudflare marker lives at the very top.
            head = r.text[:2000].lower()
            is_not_logged_in = "mode=login" in r.text and "mode=logout" not in r.text
            is_cf_challenge = "cf-browser-verification" in head or "checking your browser" in head
            if not (is_not_logged_in or is_cf_challenge):
                return r

            reason = "CF challenge" if is_cf_challenge else "not logged in"
            invalidated = self.session.invalidate(seen_gen)
            if attempt == 2 or not was_valid or (not invalidated and not self.session.session_valid):
                # Logged out before this request, or a login that began after it failed: logging
                # in again would only add a failed attempt to phpBB's count.
                logger.warning(f"Page is {reason} and no new login will help now")
                raise SiteUnavailable(f"page is {reason}")
            # Retry only if the time left covers this request and a login (the last one took
            # as long as measured, but a quick "session still valid" check must not shrink it).
            needed = (time.monotonic() - started) + max(self.session.last_login_secs or 0, self.PAGE_TIMEOUT)
            left = self._time_left()
            if left is not None and left < needed:
                logger.warning(f"Page is {reason}: {left:.0f}s left, a new login and retry need ~{needed:.0f}s")
                raise SiteUnavailable(f"page is {reason} and too little of the budget is left to log in again")
            logger.warning(f"Page is {reason} - logging in again and retrying once")
            scraper = self.session.ensure_logged_in()

    def _time_left(self):
        """Seconds left of this thread's search budget, None outside a search."""
        deadline = getattr(self._budget, "deadline", None)
        return None if deadline is None else deadline - time.monotonic()

    def _do_search(self, scraper, keywords: str, forum_ids: Optional[List[int]],
                   target_season: Optional[int], target_episode: Optional[int],
                   terms: str = "all", relevance_query: Optional[str] = None) -> List[TorznabResult]:
        """Esegue la ricerca su MIRCrew e parsa i risultati.

        Se relevance_query è valorizzata, i thread il cui titolo non contiene abbastanza
        parole di quella query (parser.MIN_QUERY_COVERAGE) vengono scartati: gli stadi
        allargati (terms=any, sottoinsiemi) altrimenti restituirebbero titoli non correlati.
        """
        base_url = self.config.base_url
        params = {**self.search_params, "keywords": keywords, "terms": terms}

        # Always restrict fid[] to the allowed forums (the requested ones when the client
        # asked for categories). Use fid[] (not fid[N]) to match phpBB's expected array format.
        params["fid[]"] = [str(fid) for fid in (forum_ids or self.allowed_forum_ids)]

        soup = None
        try:
            r = self._get_page(scraper, f"{base_url}/search.php", params)
            logger.info(f"Search '{keywords}' (terms={terms}): status={r.status_code}, "
                        f"season={target_season}, ep={target_episode}")

            soup = BeautifulSoup(r.text, "lxml")

            row_selector = self.selectors["search_result_row"]
            matched_rows = soup.select(row_selector)

            # Log diagnostics at INFO when no rows found (helps debug selector/session issues)
            if not matched_rows:
                body = soup.find("body")
                body_preview = body.get_text(separator=" ", strip=True)[:300] if body else "(no body)"
                logger.info(f"No rows matched selector '{row_selector}' | "
                            f"response={len(r.text)} chars | preview: {body_preview}")

            results = []
            seen_threads = set()
            filtered_lang_count = 0
            irrelevant_count = 0

            for row in matched_rows:
                try:
                    link = row.select_one(self.selectors["topic_title_link"])
                    if not link:
                        continue

                    thread_title = link.get_text(strip=True)
                    href = link.get("href", "")
                    url = parser.clean_url(urljoin(base_url, href), base_url)
                    topic_id = parser.get_topic_id(url)

                    if not topic_id or topic_id in seen_threads:
                        continue
                    seen_threads.add(topic_id)

                    if relevance_query and parser.query_coverage(thread_title, relevance_query) < parser.MIN_QUERY_COVERAGE:
                        logger.debug(f"SKIP irrelevant: {thread_title[:40]}...")
                        irrelevant_count += 1
                        continue

                    is_multi_season = parser.is_multi_season_title(thread_title)

                    if target_season is not None:
                        if not parser.title_matches_season(thread_title, target_season):
                            logger.debug(f"SKIP season mismatch: {thread_title[:40]}...")
                            continue

                    cat_link = row.select_one(self.selectors["forum_link"])
                    m = re.search(r'f=(\d+)', cat_link.get("href", "")) if cat_link else None
                    forum_id = int(m.group(1)) if m else None
                    if forum_id not in self.allowed_forum_ids:
                        logger.debug(f"SKIP forum {forum_id} not allowed: {thread_title[:40]}...")
                        continue

                    time_el = row.select_one(self.selectors["pub_date"])
                    pub_date = datetime.now()
                    if time_el:
                        try:
                            pub_date = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
                        except Exception:
                            pass

                    is_tv = forum_id in self.tv_forum_ids
                    is_thanked = topic_id in self.thanks_cache

                    # Per contenuti già ringraziati: espandi magnets
                    if is_thanked:
                        logger.info(f"Expanding thanked {'TV' if is_tv else 'movie'}: {thread_title[:40]}...")
                        soup_thread, html = self._fetch_thread_content(url)

                        if soup_thread and html:
                            try:
                                magnets = parser.extract_magnets_from_soup(soup_thread, html,
                                                                           self.selectors.get("post_content"))
                            finally:
                                soup_thread.decompose()

                            if is_tv and target_episode is not None:
                                magnets = [m for m in magnets
                                           if m.get("episode_info") and
                                              m["episode_info"]["episode"] == target_episode]

                            for mag in magnets:
                                title = mag["name"] if mag["name"] else thread_title
                                # Filtro lingua: controlla sia il nome magnet che il titolo thread
                                if not parser.has_italian_audio(title) and not parser.has_italian_audio(thread_title):
                                    logger.debug(f"SKIP non-Italian: {title[:40]}...")
                                    filtered_lang_count += 1
                                    continue
                                languages = parser.extract_languages_from_title(title) or parser.extract_languages_from_title(thread_title)
                                dl_params = {"topic_id": topic_id, "infohash": mag["infohash"]}

                                # Pack multi-stagione: se copre la stagione cercata,
                                # esponi QUELLA stagione così Sonarr lo associa correttamente.
                                mag_pack = mag.get("pack_info")
                                if mag_pack and target_season is not None:
                                    ss = mag_pack.get("season_start")
                                    se = mag_pack.get("season_end")
                                    if ss and se and ss <= target_season <= se:
                                        mag_pack = {**mag_pack, "season": target_season}

                                results.append(TorznabResult(
                                    title=title,
                                    link=url,
                                    guid=f"{topic_id}-{mag['infohash'][:8]}",
                                    pub_date=pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                                    size=mag["size"],
                                    category=self.category_map.get(forum_id, 5000 if is_tv else 2000),
                                    seeders=10,
                                    peers=1,
                                    infohash=mag["infohash"],
                                    episode_info=mag["episode_info"],
                                    pack_info=mag_pack,
                                    languages=languages,
                                    download_params=dl_params,
                                ))

                            if magnets:
                                logger.info(f"  -> {len(magnets)} magnets")
                                continue

                    # Per TV non ringraziati: genera risultati sintetici
                    if is_tv and not is_thanked and not is_multi_season:
                        # Filtro lingua sul titolo thread
                        if not parser.has_italian_audio(thread_title):
                            logger.debug(f"SKIP non-Italian TV: {thread_title[:40]}...")
                            filtered_lang_count += 1
                            continue

                        title_season = parser.extract_season_from_title(thread_title) or 1
                        episode_count = parser.extract_episode_count_from_title(thread_title)
                        show_name = parser.generate_show_name_from_title(thread_title)

                        if episode_count and episode_count > 0:
                            media_tags = parser.extract_media_tags_from_title(thread_title)
                            thread_languages = parser.extract_languages_from_title(thread_title)
                            logger.info(f"Generating {episode_count} synthetic episodes for: {thread_title[:40]}...")

                            for ep_num in range(1, episode_count + 1):
                                if target_episode is not None and ep_num != target_episode:
                                    continue

                                synthetic_title = f"{show_name} S{title_season:02d}E{ep_num:02d}"
                                if media_tags:
                                    synthetic_title += f" {media_tags}"

                                ep_info = {"season": title_season, "episode": ep_num}
                                dl_params = {"topic_id": topic_id, "season": str(title_season), "ep": str(ep_num)}

                                results.append(TorznabResult(
                                    title=synthetic_title,
                                    link=url,
                                    guid=f"{topic_id}-S{title_season}E{ep_num}",
                                    pub_date=pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                                    size=parser.get_default_size(forum_id, thread_title, self.tv_forum_ids),
                                    category=self.category_map.get(forum_id, 5000),
                                    episode_info=ep_info,
                                    languages=thread_languages,
                                    download_params=dl_params,
                                ))
                            continue

                    # Thread-level result (film, o TV senza info episodi, o pack multi-stagione)
                    # Filtro lingua
                    if not parser.has_italian_audio(thread_title):
                        logger.debug(f"SKIP non-Italian: {thread_title[:40]}...")
                        filtered_lang_count += 1
                        continue

                    # Pack multi-stagione TV: se il thread copre la stagione cercata,
                    # esponi l'attributo season così Sonarr lo riconosce per quella stagione.
                    pack_info = None
                    if is_tv and target_season is not None:
                        season_range = parser.extract_season_range_from_title(thread_title)
                        if season_range and season_range[0] <= target_season <= season_range[1]:
                            pack_info = {"season": target_season, "is_pack": True}

                    dl_params = {"topic_id": topic_id}
                    results.append(TorznabResult(
                        title=thread_title,
                        link=url,
                        guid=topic_id,
                        pub_date=pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                        size=parser.get_default_size(forum_id, thread_title, self.tv_forum_ids),
                        category=self.category_map.get(forum_id, 2000 if not is_tv else 5000),
                        pack_info=pack_info,
                        languages=parser.extract_languages_from_title(thread_title),
                        download_params=dl_params,
                    ))

                except BudgetSpent:
                    if not results:
                        raise  # nothing to answer with: an empty feed would read as "no release exists"
                    # No time left to expand more threads: answer with what is there.
                    logger.warning("Search budget spent while expanding threads, returning the results so far")
                    break
                except SiteUnavailable:
                    raise  # a dead session or an outage is not a row that failed to parse
                except Exception as e:
                    logger.warning(f"Parse error: {e}")

            logger.info(f"Search returned {len(results)} results"
                        + (f" ({filtered_lang_count} filtered for non-Italian language)" if filtered_lang_count else "")
                        + (f" ({irrelevant_count} of {len(matched_rows)} rows dropped as irrelevant to '{relevance_query}')"
                           if irrelevant_count else ""))
            return results

        except SiteUnavailable:
            raise
        except requests.RequestException as e:
            raise SiteUnavailable(f"search request failed: {e}") from e
        except Exception as e:
            logger.exception(f"Search exception: {e}")
            return []
        finally:
            # bs4 trees are reference cycles: without this they wait for a full GC
            # and pile up across concurrent searches (#71).
            if soup is not None:
                soup.decompose()

    # === REQUESTS (sections 53, 54 and 57) ===

    REQUEST_SECTIONS = (53, 54)  # Richieste Film, Richieste Serie TV
    SATISFIED_SECTION = 57       # Richieste Video Soddisfatte
    MAX_REQUEST_TITLES = 5
    MAX_REQUEST_TOPICS = 10
    MAX_REQUEST_TITLE_CHARS = 100
    REQUEST_PAGE_DELAY = 1.0     # seconds between forum pages: this read comes on top of normal traffic

    @contextmanager
    def _request_scope(self):
        """One search slot and the 90 s budget of a search, for a read that is not a search."""
        deadline = time.monotonic() + self.SEARCH_BUDGET  # counted from arrival, like a search
        if not self._search_slots.acquire(timeout=self.SEARCH_QUEUE_TIMEOUT):
            raise SiteUnavailable(f"search queue full: no free slot after {self.SEARCH_QUEUE_TIMEOUT}s")
        self._budget.deadline = deadline
        self.session.set_deadline(deadline)
        try:
            yield
        finally:
            self._budget.deadline = None
            self.session.set_deadline(None)
            self._search_slots.release()

    def find_requests(self, section: int, titles: List[str], imdb: Optional[str] = None) -> dict:
        """Read-only: request topics in `section` and in 57 whose title matches one of `titles`.

        Each topic comes with its first post's author, date and IMDb id, its reply count and its
        last reply. `match` is True when the first post carries the requested IMDb id. Nothing is
        posted, thanked or clicked, and nothing in the answer carries a link or a session id.
        `truncated` is True when the topic cap or the time budget cut the answer short.
        """
        if section not in self.REQUEST_SECTIONS:
            raise ValueError(f"section must be one of {list(self.REQUEST_SECTIONS)}")
        # Search operators (+ - | * " ( ) ~) would change what the forum looks for.
        cleaned = [" ".join(re.sub(r'[+\-|*"()~]+', " ", t)[:self.MAX_REQUEST_TITLE_CHARS].split()) for t in titles]
        wanted = list(dict.fromkeys(t for t in cleaned if t))
        if not wanted:
            raise ValueError("at least one title is required")
        if len(wanted) > self.MAX_REQUEST_TITLES:
            raise ValueError(f"at most {self.MAX_REQUEST_TITLES} titles")
        if imdb and not re.fullmatch(r"tt\d{6,10}", imdb):
            raise ValueError("imdb must look like tt1234567")

        base = self.config.base_url
        sections = {section, self.SATISFIED_SECTION}
        fetched = []
        truncated = False

        def page(scraper, path, params):
            if fetched:
                time.sleep(self.REQUEST_PAGE_DELAY)
            fetched.append(1)
            return self._get_page(scraper, f"{base}/{path}", params).text

        with self._request_scope():
            scraper = self._logged_in_scraper()
            per_title: List[List[dict]] = []
            seen: set = set()
            try:
                for title in wanted:
                    params = {**self.search_params, "keywords": title, "terms": "all",
                              "fid[]": [str(s) for s in sorted(sections)]}
                    try:
                        rows = request_topics.parse_search_rows(page(scraper, "search.php", params), self.selectors)
                    except ValueError as e:  # no results section: an error or flood page, not "no requests"
                        raise SiteUnavailable(f"search page is not a results page: {e}") from e
                    fresh = [r for r in rows if r["section"] in sections and r["topic_id"] not in seen]
                    seen.update(r["topic_id"] for r in fresh)
                    per_title.append(fresh)
            except BudgetSpent:
                if not per_title:
                    raise
                truncated = True

            # One topic per title in turn, so a title with many hits does not crowd out the others.
            queue = [r for group in zip_longest(*per_title) for r in group if r]
            truncated = truncated or len(queue) > self.MAX_REQUEST_TOPICS
            topics = []
            for row in queue[:self.MAX_REQUEST_TOPICS]:
                try:
                    posts = request_topics.parse_topic_posts(
                        page(scraper, "viewtopic.php", {"t": row["topic_id"]}), self.selectors)
                except PageNotFound:
                    logger.info(f"Request topic {row['topic_id']} is gone, skipped")
                    continue
                except BudgetSpent:
                    truncated = True
                    break
                first = posts[0] if posts else {}
                last_reply = None
                if row["replies"] and row["last_post_id"]:
                    wanted_id = row["last_post_id"]
                    last = next((p for p in posts if p["post_id"] == wanted_id), None)
                    if last is None:  # the last post is on a later page of the topic
                        try:
                            last = next((p for p in request_topics.parse_topic_posts(
                                page(scraper, "viewtopic.php", {"p": wanted_id}), self.selectors)
                                if p["post_id"] == wanted_id), None)
                        except PageNotFound:  # the reply was deleted since the search: the topic still counts
                            last = None
                        except BudgetSpent:
                            truncated = True
                            last = None
                    if last and last is not first:
                        last_reply = {k: last[k] for k in ("author", "date", "text")}
                topics.append({
                    "topic_id": row["topic_id"],
                    "title": row["title"],
                    "section": row["section"],
                    "author": first.get("author") or row["author"],
                    "date": first.get("date") or row["date"],
                    "replies": row["replies"],
                    "last_reply": last_reply,
                    "imdb": first.get("imdb"),
                    "match": bool(imdb) and first.get("imdb") == imdb,
                    "url": f"{base}/viewtopic.php?t={row['topic_id']}",
                })
        topics.sort(key=lambda t: not t["match"])  # stable: search order otherwise
        return {"section": section, "imdb": imdb, "truncated": truncated, "topics": topics}

    MAX_REPORT_TOPICS = 25

    def report_requests(self, topic_ids: List[str]) -> dict:
        """Read-only report on request topics (for a daily follow-up): where each one is now
        (section 53, 54, 57 or another), its title, reply count and last reply. A topic that no
        longer exists is `gone`. One forum page per topic (a second one for the last reply of a
        topic that spans pages), one second apart; `truncated` when the time budget cut it short.
        """
        ids = list(dict.fromkeys(str(t).strip() for t in topic_ids if str(t).strip()))
        if not ids:
            raise ValueError("at least one topic_id is required")
        if not all(re.fullmatch(r"[0-9]{1,10}", i) for i in ids):
            raise ValueError("topic_id must be a number")
        if len(ids) > self.MAX_REPORT_TOPICS:
            raise ValueError(f"at most {self.MAX_REPORT_TOPICS} topics")

        base = self.config.base_url
        fetched = []
        topics = []
        truncated = False

        def page(scraper, params):
            if fetched:
                time.sleep(self.REQUEST_PAGE_DELAY)
            fetched.append(1)
            return self._get_page(scraper, f"{base}/viewtopic.php", params).text

        with self._request_scope():
            scraper = self._logged_in_scraper()
            for topic_id in ids:
                try:
                    overview = request_topics.parse_topic_overview(page(scraper, {"t": topic_id}), self.selectors)
                except PageNotFound:
                    topics.append({"topic_id": topic_id, "gone": True})
                    continue
                except BudgetSpent:
                    truncated = True
                    break
                except (PageError, ValueError) as e:  # a topic this account cannot read, a 5xx, a message page
                    topics.append({"topic_id": topic_id, "gone": False, "error": str(e)})
                    continue
                posts = overview["posts"]
                total = overview["total_posts"]
                replies = max(total - 1, 0) if total is not None else None  # unknown without the count
                last = posts[-1] if replies and posts else None
                if replies and overview["last_start"]:  # the last post is on a later page
                    try:
                        later = request_topics.parse_topic_posts(
                            page(scraper, {"t": topic_id, "start": overview["last_start"]}), self.selectors)
                        last = later[-1] if later else None
                    except PageError:  # the topic is still there: report it without its last reply
                        last = None
                    except BudgetSpent:
                        truncated = True
                        last = None
                topics.append({
                    "topic_id": topic_id,
                    "gone": False,
                    "title": overview["title"],
                    "section": overview["section"],
                    "replies": replies,
                    "last_reply": {k: last[k] for k in ("author", "date", "text")} if last else None,
                    "url": f"{base}/viewtopic.php?t={topic_id}",
                })
                if truncated:
                    break
        return {"truncated": truncated, "topics": topics}

    # === DOWNLOAD ===

    def download(self, topic_id: str, infohash: Optional[str],
                 season: Optional[int], episode: Optional[int]) -> Optional[str]:
        """Click thanks + estrai magnet."""
        url = f"{self.config.base_url}/viewtopic.php?t={topic_id}"
        logger.info(f"=== DOWNLOAD: topic={topic_id}, infohash={infohash or 'N/A'}, S{season}E{episode} ===")

        for attempt in (1, 2):
            gen = self.session.login_gen
            soup, html, thanks_clicked = self._fetch_thread_and_click_thanks(url)
            magnets = (parser.extract_magnets_from_soup(soup, html, self.selectors.get("post_content"))
                       if soup and html else [])
            # A login in the middle of the download means the thanks may have gone to the
            # dead session and the magnets stayed hidden: do the whole sequence once more.
            if magnets or attempt == 2 or self.session.login_gen == gen:
                break
            logger.warning("Session renewed during the download, fetching the thread again")
        if not magnets:
            return None

        # 1. Cerca per infohash
        if infohash:
            for m in magnets:
                if m["infohash"] == infohash:
                    logger.info(f"Found by infohash: {m['name'][:50]}...")
                    return m["magnet"]
            logger.error(f"Infohash {infohash} not found!")
            return None

        # 2. Cerca per season/episode
        if season is not None and episode is not None:
            for m in magnets:
                ep_info = m.get("episode_info")
                if ep_info and ep_info["season"] == season and ep_info["episode"] == episode:
                    logger.info(f"Found S{season:02d}E{episode:02d}: {m['name'][:50]}...")
                    return m["magnet"]
            available = [f"S{m['episode_info']['season']:02d}E{m['episode_info']['episode']:02d}"
                        for m in magnets if m.get("episode_info")]
            logger.error(f"S{season:02d}E{episode:02d} not found! Available: {available}")
            return None

        # 3. Primo magnet (film o fallback)
        logger.info(f"Returning first: {magnets[0]['name'][:50]}...")
        return magnets[0]["magnet"]

    # === DEBUG ===

    def debug_thread(self, topic_id: str) -> dict:
        """Debug endpoint per ispezionare un thread."""
        url = f"{self.config.base_url}/viewtopic.php?t={topic_id}"
        is_thanked = topic_id in self.thanks_cache
        try:
            soup, html = self._fetch_thread_content(url)
        except SiteUnavailable as e:
            return {"error": f"Failed to load thread: {e}"}

        if not soup or not html:
            return {"error": "Failed to load thread"}

        magnets = parser.extract_magnets_from_soup(soup, html,
                                                    self.selectors.get("post_content"))
        return {
            "topic_id": topic_id,
            "url": url,
            "thanked": is_thanked,
            "magnets_visible": len(magnets),
            "magnets": [{
                "infohash": m["infohash"][:12] + "...",
                "name": m["name"][:60],
                "episode": m["episode_info"],
            } for m in magnets],
        }

    # === THREAD CONTENT ===

    def _fetch_thread_content(self, topic_url: str):
        """Carica contenuto thread SENZA cliccare Thanks."""
        scraper = self.session.ensure_logged_in()
        topic_url = parser.clean_url(topic_url, self.config.base_url)
        try:
            r = self._get_page(scraper, topic_url)
            return BeautifulSoup(r.text, "lxml"), r.text
        except PageError as e:  # a deleted or broken thread: only this thread is left out
            logger.warning(f"Thread {topic_url} not read: {e}")
            return None, None
        except SiteUnavailable:
            raise  # the search answers 503; a partial feed would read as "no release exists"
        except requests.RequestException as e:
            raise SiteUnavailable(f"thread request failed: {e}") from e
        except Exception as e:
            logger.error(f"fetch_thread_content error: {e}")
            return None, None

    def _fetch_thread_and_click_thanks(self, topic_url: str):
        """Carica thread E clicca Thanks se necessario."""
        scraper = self.session.ensure_logged_in()
        topic_url = parser.clean_url(topic_url, self.config.base_url)
        topic_id = parser.get_topic_id(topic_url)
        base_url = self.config.base_url

        logger.info(f"=== FETCH+THANKS: {topic_url} ===")

        try:
            r = self._get_page(scraper, topic_url)
            soup = BeautifulSoup(r.text, "lxml")

            if topic_id and topic_id in self.thanks_cache:
                if self._has_magnets(soup, r.text):
                    logger.info("Already thanked (cache)")
                    return soup, r.text, False
                # Cached while a dead session ate the click (older builds did that): forget it
                # and click again.
                logger.warning(f"Topic {topic_id} is cached as thanked but shows no magnet: thanking again")
                self.thanks_cache.discard(topic_id)
                self._save_thanks_cache()

            first_post = soup.select_one(self.selectors["first_post"])
            if not first_post:
                return soup, r.text, False

            quote_link = first_post.select_one(self.selectors["quote_link"])
            if not quote_link:
                return soup, r.text, False

            first_post_id = parser.get_post_id(quote_link.get("href", ""))
            if not first_post_id:
                return soup, r.text, False

            thanks_link = None
            for a in soup.find_all("a", href=lambda x: x and "thanks=" in str(x)):
                href = a.get("href", "")
                if f"p={first_post_id}" in href or f"thanks={first_post_id}" in href:
                    thanks_link = href
                    break

            if thanks_link:
                logger.info(f"Clicking Thanks: {thanks_link}")
                thanks_url = urljoin(base_url, thanks_link)
                try:
                    scraper.get(thanks_url, timeout=30)
                    time.sleep(1)
                    r = self._get_page(scraper, topic_url)
                    soup = BeautifulSoup(r.text, "lxml")
                    # The click may have hit a dead session (the page getter then logged in
                    # again and returned an unthanked page): cache only once magnets show.
                    thanked = self._has_magnets(soup, r.text)
                    if topic_id and thanked:
                        self.thanks_cache.add(topic_id)
                        self._save_thanks_cache()
                    return soup, r.text, thanked
                except SiteUnavailable:
                    raise
                except Exception as e:
                    logger.error(f"Thanks click failed: {e}")
            else:
                logger.info("No thanks button (already thanked)")
                if topic_id and self._has_magnets(soup, r.text):
                    self.thanks_cache.add(topic_id)
                    self._save_thanks_cache()

            return soup, r.text, False

        except PageNotFound as e:
            logger.warning(f"Thread {topic_url} is gone: {e}")
            return None, None, False
        except SiteUnavailable:
            raise  # download() lets it reach the route, which answers 503
        except Exception as e:
            logger.exception(f"fetch_thread_and_click_thanks error: {e}")
            return None, None, False

    def _has_magnets(self, soup, html) -> bool:
        return bool(parser.extract_magnets_from_soup(soup, html, self.selectors.get("post_content")))

    # === THANKS CACHE ===

    def _load_thanks_cache(self):
        try:
            if self.thanks_cache_file.exists():
                with open(self.thanks_cache_file) as f:
                    self.thanks_cache = set(json.load(f))
                logger.info(f"Thanks cache loaded: {len(self.thanks_cache)} topics")
        except Exception as e:
            logger.warning(f"Failed to load thanks cache: {e}")

    def _save_thanks_cache(self):
        try:
            with open(self.thanks_cache_file, "w") as f:
                json.dump(list(self.thanks_cache), f)
        except Exception as e:
            logger.warning(f"Failed to save thanks cache: {e}")


def create_site(config: Config) -> MircrewSite:
    """Factory function per creare MircrewSite con la sua sessione."""
    session = MircrewSession(
        base_url=config.base_url,
        username=config.username,
        password=config.password,
        cookies_file=config.data_dir / "cookies.json",
        flaresolverr_url=config.flaresolverr_url,
        flaresolverr_timeout=config.flaresolverr_timeout,
    )
    return MircrewSite(session=session, config=config)
