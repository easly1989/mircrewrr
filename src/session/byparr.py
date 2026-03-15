"""Sessione HTTP con bypass Cloudflare via Byparr/FlareSolverr."""

import time
import logging
from pathlib import Path

import requests

from .base import BaseSession

logger = logging.getLogger("session.byparr")


class _ByparrResponse:
    """Minimal response wrapper for Byparr solution data."""

    def __init__(self, solution):
        self.status_code = solution.get("status", 200)
        self.text = solution.get("response", "")
        self.url = solution.get("url", "")


class ByparrSession(BaseSession):
    """Estende BaseSession con bypass Cloudflare via Byparr/FlareSolverr."""

    def __init__(self, base_url: str, username: str, password: str,
                 cookies_file: Path, flaresolverr_url: str,
                 flaresolverr_timeout: int = 60000, **kwargs):
        self.flaresolverr_url = flaresolverr_url
        self.flaresolverr_timeout = flaresolverr_timeout
        self.cf_valid = False
        super().__init__(base_url, username, password, cookies_file, **kwargs)

    def _byparr_request(self, url, method="GET", post_data=None):
        """Send request through Byparr/FlareSolverr to solve CF challenges."""
        payload = {
            "cmd": f"request.{method.lower()}",
            "url": url,
            "maxTimeout": self.flaresolverr_timeout,
        }
        if post_data:
            payload["postData"] = post_data

        try:
            logger.info(f"Byparr {method}: {url[:80]}...")
            r = requests.post(
                f"{self.flaresolverr_url}/v1",
                json=payload,
                timeout=self.flaresolverr_timeout // 1000 + 30,
            )
            data = r.json()

            if data.get("status") != "ok":
                logger.error(f"Byparr error: {data.get('message', 'unknown')}")
                return None

            solution = data.get("solution", {})

            # Extract and apply cookies from Byparr's browser session
            byparr_cookies = solution.get("cookies", [])
            for c in byparr_cookies:
                self.http.cookies.set(
                    c["name"], c["value"],
                    domain=c.get("domain", ""),
                    path=c.get("path", "/"),
                )

            # Use same user-agent as Byparr's browser
            ua = solution.get("userAgent")
            if ua:
                self.user_agent = ua
                self.http.headers["User-Agent"] = ua

            self.cf_valid = True
            self._save_cookies()

            logger.info(f"Byparr done: status={solution.get('status')}, cookies={len(byparr_cookies)}")
            return solution

        except requests.ConnectionError:
            logger.error(f"Cannot connect to Byparr at {self.flaresolverr_url} - is it running?")
            return None
        except Exception as e:
            logger.error(f"Byparr request failed: {e}")
            return None

    def _solve_cf(self):
        """Solve Cloudflare challenge via Byparr and store cookies."""
        logger.info("Solving Cloudflare challenge via Byparr...")
        solution = self._byparr_request(self.base_url)
        if solution and solution.get("status") == 200:
            logger.info("CF challenge solved, cookies acquired")
            return True
        logger.error("Failed to solve CF challenge")
        return False

    def _is_cf_blocked(self, response) -> bool:
        """Check if response is a Cloudflare block."""
        if response.status_code == 403:
            return True
        if response.status_code == 503 and "cloudflare" in response.text.lower():
            return True
        return False

    def get(self, url, **kwargs):
        """GET with automatic CF bypass retry."""
        kwargs.setdefault("timeout", 30)
        try:
            r = self.http.get(url, **kwargs)
            if self._is_cf_blocked(r):
                logger.warning(f"CF blocked GET {url[:60]}, solving via Byparr...")
                solution = self._byparr_request(url, "GET")
                if solution:
                    return _ByparrResponse(solution)
                r = self.http.get(url, **kwargs)
            return r
        except Exception as e:
            logger.error(f"GET {url[:60]} failed: {e}")
            raise

    def post(self, url, data=None, **kwargs):
        """POST with automatic CF bypass retry."""
        kwargs.setdefault("timeout", 30)
        try:
            r = self.http.post(url, data=data, **kwargs)
            if self._is_cf_blocked(r):
                logger.warning(f"CF blocked POST {url[:60]}, refreshing CF cookies...")
                if self._solve_cf():
                    r = self.http.post(url, data=data, **kwargs)
            return r
        except Exception as e:
            logger.error(f"POST {url[:60]} failed: {e}")
            raise

    def ensure_logged_in(self) -> "ByparrSession":
        """Verifica la sessione, con solve CF se necessario."""
        if self.session_valid and (time.time() - self.last_login) < 3600:
            return self

        if not self.cf_valid:
            self._solve_cf()

        return super().ensure_logged_in()

    def _load_cookies(self):
        result = super()._load_cookies()
        if result:
            self.cf_valid = True
        return result
