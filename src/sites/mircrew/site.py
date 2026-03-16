"""Implementazione MIRCrew: sessione con login phpBB e sito Torznab."""

import json
import re
import time
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from config import Config
from session import ByparrSession
from torznab.server import BaseSite
from torznab.models import TorznabResult

from .constants import CATEGORY_MAP, FORUM_IDS, TV_FORUM_IDS, CAPABILITIES_XML
from . import parser

logger = logging.getLogger("mircrew")


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
                logger.error("Login form not found!")
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
                self.session_valid = True
                self.last_login = time.time()
                self._save_cookies()
                return True

            logger.error("Login failed - check credentials")
            return False
        except Exception as e:
            logger.exception(f"Login exception: {e}")
            return False


class MircrewSite(BaseSite):
    """Implementazione MIRCrew del sito Torznab."""

    def __init__(self, session: MircrewSession, config: Config):
        self.session = session
        self.config = config
        self.thanks_cache: set = set()
        self.thanks_cache_file = config.data_dir / "thanks_cache.json"
        self._load_thanks_cache()

    def get_capabilities_xml(self) -> str:
        return CAPABILITIES_XML

    def health_info(self) -> dict:
        return {
            "status": "ok",
            "logged_in": self.session.session_valid,
            "cf_valid": self.session.cf_valid,
            "flaresolverr_url": self.session.flaresolverr_url,
            "thanks_cached": len(self.thanks_cache),
        }

    def parse_season_from_query(self, query: str) -> Optional[int]:
        return parser.extract_season_from_query(query)

    def parse_episode_from_query(self, query: str) -> Optional[int]:
        return parser.extract_episode_from_query(query)

    # === SEARCH ===

    def search(self, query: str, categories: Optional[List[int]],
               target_season: Optional[int], target_episode: Optional[int]) -> List[TorznabResult]:
        """Ricerca con normalizzazione e retry terms=any."""
        scraper = self.session.ensure_logged_in()

        normalized = parser.normalize_search_query(query)
        keywords = normalized if normalized else str(datetime.now().year)

        logger.info(f"Search query: '{query}' -> normalized: '{keywords}'")

        # Mappa categorie Torznab → forum IDs
        forum_ids = None
        if categories:
            forum_ids = [fid for fid, tcat in CATEGORY_MAP.items() if tcat in categories]

        results = self._do_search(scraper, keywords, forum_ids, target_season, target_episode, terms="all")

        if not results and len(keywords.split()) > 1:
            logger.info(f"Retry search with terms=any for: '{keywords}'")
            results = self._do_search(scraper, keywords, forum_ids, target_season, target_episode, terms="any")

        # Stage 3: fallback progressivo con sottoinsiemi di keywords
        if not results and len(keywords.split()) > 1:
            words = keywords.split()
            for length in range(len(words) - 1, 0, -1):
                for start in range(len(words) - length + 1):
                    subset = ' '.join(words[start:start + length])
                    logger.info(f"Progressive fallback: trying '{subset}'")
                    results = self._do_search(scraper, subset, forum_ids,
                                               target_season, target_episode, terms="all")
                    if results:
                        break
                if results:
                    break

        return results

    def _do_search(self, scraper, keywords: str, forum_ids: Optional[List[int]],
                   target_season: Optional[int], target_episode: Optional[int],
                   terms: str = "all") -> List[TorznabResult]:
        """Esegue la ricerca su MIRCrew e parsa i risultati."""
        base_url = self.config.base_url
        params = {
            "keywords": keywords,
            "terms": terms,
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

        for fid in (forum_ids or FORUM_IDS):
            params[f"fid[{fid}]"] = str(fid)

        try:
            r = scraper.get(f"{base_url}/search.php", params=params, timeout=30)
            logger.info(f"Search '{keywords}' (terms={terms}): status={r.status_code}, "
                        f"season={target_season}, ep={target_episode}")

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
                    url = parser.clean_url(urljoin(base_url, href), base_url)
                    topic_id = parser.get_topic_id(url)

                    if not topic_id or topic_id in seen_threads:
                        continue
                    seen_threads.add(topic_id)

                    is_multi_season = parser.is_multi_season_title(thread_title)

                    if target_season is not None:
                        if not parser.title_matches_season(thread_title, target_season):
                            logger.debug(f"SKIP season mismatch: {thread_title[:40]}...")
                            continue

                    cat_link = row.select_one("a[href*='viewforum.php']")
                    forum_id = 25
                    if cat_link:
                        m = re.search(r'f=(\d+)', cat_link.get("href", ""))
                        if m:
                            forum_id = int(m.group(1))

                    time_el = row.select_one("time[datetime]")
                    pub_date = datetime.now()
                    if time_el:
                        try:
                            pub_date = datetime.fromisoformat(time_el["datetime"].replace("Z", "+00:00"))
                        except Exception:
                            pass

                    is_tv = forum_id in TV_FORUM_IDS
                    is_thanked = topic_id in self.thanks_cache

                    # Per contenuti già ringraziati: espandi magnets
                    if is_thanked:
                        logger.info(f"Expanding thanked {'TV' if is_tv else 'movie'}: {thread_title[:40]}...")
                        soup_thread, html = self._fetch_thread_content(url)

                        if soup_thread and html:
                            magnets = parser.extract_magnets_from_soup(soup_thread, html)

                            if is_tv and target_episode is not None:
                                magnets = [m for m in magnets
                                           if m.get("episode_info") and
                                              m["episode_info"]["episode"] == target_episode]

                            for mag in magnets:
                                title = mag["name"] if mag["name"] else thread_title
                                dl_params = {"topic_id": topic_id, "infohash": mag["infohash"]}

                                results.append(TorznabResult(
                                    title=title,
                                    link=url,
                                    guid=f"{topic_id}-{mag['infohash'][:8]}",
                                    pub_date=pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                                    size=mag["size"],
                                    category=CATEGORY_MAP.get(forum_id, 5000 if is_tv else 2000),
                                    seeders=10,
                                    peers=1,
                                    infohash=mag["infohash"],
                                    episode_info=mag["episode_info"],
                                    pack_info=mag.get("pack_info"),
                                    download_params=dl_params,
                                ))

                            if magnets:
                                logger.info(f"  -> {len(magnets)} magnets")
                                continue

                    # Per TV non ringraziati: genera risultati sintetici
                    if is_tv and not is_thanked and not is_multi_season:
                        title_season = parser.extract_season_from_title(thread_title) or 1
                        episode_count = parser.extract_episode_count_from_title(thread_title)
                        show_name = parser.generate_show_name_from_title(thread_title)

                        if episode_count and episode_count > 0:
                            media_tags = parser.extract_media_tags_from_title(thread_title)
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
                                    size=parser.get_default_size(forum_id, thread_title),
                                    category=CATEGORY_MAP.get(forum_id, 5000),
                                    episode_info=ep_info,
                                    download_params=dl_params,
                                ))
                            continue

                    # Thread-level result (film, o TV senza info episodi)
                    dl_params = {"topic_id": topic_id}
                    results.append(TorznabResult(
                        title=thread_title,
                        link=url,
                        guid=topic_id,
                        pub_date=pub_date.strftime("%a, %d %b %Y %H:%M:%S +0000"),
                        size=parser.get_default_size(forum_id, thread_title),
                        category=CATEGORY_MAP.get(forum_id, 2000 if not is_tv else 5000),
                        download_params=dl_params,
                    ))

                except Exception as e:
                    logger.warning(f"Parse error: {e}")

            logger.info(f"Search returned {len(results)} results")
            return results

        except Exception as e:
            logger.exception(f"Search exception: {e}")
            return []

    # === DOWNLOAD ===

    def download(self, topic_id: str, infohash: Optional[str],
                 season: Optional[int], episode: Optional[int]) -> Optional[str]:
        """Click thanks + estrai magnet."""
        url = f"{self.config.base_url}/viewtopic.php?t={topic_id}"
        logger.info(f"=== DOWNLOAD: topic={topic_id}, infohash={infohash or 'N/A'}, S{season}E{episode} ===")

        soup, html, thanks_clicked = self._fetch_thread_and_click_thanks(url)

        if not soup or not html:
            return None

        magnets = parser.extract_magnets_from_soup(soup, html)
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
        soup, html = self._fetch_thread_content(url)

        if not soup or not html:
            return {"error": "Failed to load thread"}

        magnets = parser.extract_magnets_from_soup(soup, html)
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
            r = scraper.get(topic_url, timeout=30)
            if r.status_code != 200:
                return None, None
            return BeautifulSoup(r.text, "lxml"), r.text
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
            r = scraper.get(topic_url, timeout=30)
            if r.status_code != 200:
                return None, None, False

            soup = BeautifulSoup(r.text, "lxml")

            if topic_id and topic_id in self.thanks_cache:
                logger.info("Already thanked (cache)")
                return soup, r.text, False

            first_post = soup.select_one("div.post")
            if not first_post:
                return soup, r.text, False

            quote_link = first_post.select_one("a[href*='mode=quote']")
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
                    r = scraper.get(topic_url, timeout=30)
                    soup = BeautifulSoup(r.text, "lxml")
                    if topic_id:
                        self.thanks_cache.add(topic_id)
                        self._save_thanks_cache()
                    return soup, r.text, True
                except Exception as e:
                    logger.error(f"Thanks click failed: {e}")
            else:
                logger.info("No thanks button (already thanked)")
                if topic_id:
                    self.thanks_cache.add(topic_id)
                    self._save_thanks_cache()

            return soup, r.text, False

        except Exception as e:
            logger.exception(f"fetch_thread_and_click_thanks error: {e}")
            return None, None, False

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
