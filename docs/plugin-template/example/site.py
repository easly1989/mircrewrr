"""Example plugin: a Torznab site that finds nothing. Copy this folder and fill in the TODOs."""

import logging
from typing import List, Optional

from config import Config
from session import ByparrSession
from torznab.models import TorznabResult
from torznab.server import BaseSite

logger = logging.getLogger("example")

DEFAULT_CAPABILITIES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<caps>
<server title="Example Proxy"/>
<searching><search available="yes" supportedParams="q"/></searching>
<categories><category id="2000" name="Movies"/><category id="5000" name="TV"/></categories>
</caps>"""


class ExampleSession(ByparrSession):
    """HTTP session of the site: Cloudflare through Byparr, then the site's own login."""

    def _check_logged_in(self, html: str) -> bool:
        # TODO: return True when this page shows a logged-in user
        return False

    def _do_login(self) -> bool:
        # TODO: log in with self.username / self.password and return True on success
        logger.warning("Login not implemented for this plugin")
        return False


class ExampleSite(BaseSite):
    def __init__(self, session: ExampleSession, config: Config):
        self.session = session
        self.config = config
        # Values saved from the panel's Settings arrive in config.custom, keyed as in manifest "custom_config".
        self.capabilities_xml = (config.custom or {}).get("capabilities_xml", DEFAULT_CAPABILITIES_XML)

    def search(self, query: str, categories: Optional[List[int]],
               target_season: Optional[int], target_episode: Optional[int]) -> List[TorznabResult]:
        # TODO: search the site. Raise torznab.server.SiteUnavailable when the site cannot be
        # reached, so Prowlarr records a failure; return [] when it is reachable but has no hits.
        logger.info(f"Search: q={query!r} cat={categories} S{target_season}E{target_episode}")
        return []

    def download(self, topic_id: str, infohash: Optional[str],
                 season: Optional[int], episode: Optional[int]) -> Optional[str]:
        # TODO: return the magnet URI, or None when there is none
        logger.info(f"Download: topic={topic_id} hash={infohash}")
        return None

    def get_capabilities_xml(self) -> str:
        return self.capabilities_xml

    def health_info(self) -> dict:
        # Shown on the Dashboard and Indexers pages. Add "logged_in" (bool) once the login works,
        # and "login_blocked" (text) when the site refuses it, as the MIRCrew plugin does.
        return {
            "status": "ok",
            "cf_valid": self.session.cf_valid,
            "cf_bypass_url": self.session.flaresolverr_url,
        }


def create_site(config: Config):
    """Called once at startup, and again when the Settings are saved."""
    session = ExampleSession(
        base_url=config.base_url,
        username=config.username,
        password=config.password,
        cookies_file=config.data_dir / "example_cookies.json",
        flaresolverr_url=config.cf_bypass_url,
        flaresolverr_timeout=config.cf_bypass_timeout,
    )
    return ExampleSite(session, config)
