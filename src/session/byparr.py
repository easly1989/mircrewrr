"""Sessione HTTP con bypass Cloudflare via Byparr/FlareSolverr."""

import logging
import time
from pathlib import Path

import requests

from .base import BaseSession

BYPASS_ERROR_TTL = 300  # seconds a failed call to the bypass proxy stays on the Dashboard

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
        # Why the last call to the bypass proxy failed (it is down, or did not answer), None while
        # it works. The Dashboard shows it: a down solver breaks every search, whatever the forum does.
        self.bypass_error = None
        self.bypass_error_at = 0.0
        # Counts Cloudflare solves: a thread that waited for the lock can tell the cookies
        # were renewed meanwhile and need no second Byparr call.
        self.cf_gen = 0
        super().__init__(base_url, username, password, cookies_file, **kwargs)

    def _byparr_request(self, url, method="GET", post_data=None):
        """Send request through Byparr/FlareSolverr to solve CF challenges."""
        timeout_sec = max(1, self.flaresolverr_timeout // 1000)
        grace = 30  # the HTTP call may outlast Byparr's own timeout a little
        left = self._time_left()
        cut_short = False
        if left is not None:  # never run past the search's deadline
            cut_short = int(left) < timeout_sec
            timeout_sec = max(1, min(timeout_sec, int(left)))
            grace = 3
        payload = {
            "cmd": f"request.{method.lower()}",
            "url": url,
            "max_timeout": timeout_sec,
            "maxTimeout": min(self.flaresolverr_timeout, timeout_sec * 1000),
        }
        if post_data:
            payload["postData"] = post_data

        try:
            logger.info(f"Byparr {method}: {url[:80]}...")
            r = requests.post(
                f"{self.flaresolverr_url}/v1",
                json=payload,
                timeout=timeout_sec + grace,
            )
            self.bypass_error = None  # any HTTP answer: it is up, whatever it says about the page

            try:
                data = r.json()
            except (ValueError, requests.exceptions.JSONDecodeError):
                logger.error(f"Byparr non-JSON response (HTTP {r.status_code}): {r.text[:500]!r}")
                return None

            if data.get("status") != "ok":
                logger.error(f"Byparr error (HTTP {r.status_code}): {data.get('message', 'unknown')}")
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
            self._private.cf_solved = True  # counted in cf_gen only if the copy is published
            self._save_cookies()

            logger.info(f"Byparr done: status={solution.get('status')}, cookies={len(byparr_cookies)}")
            return solution

        except requests.ConnectionError:
            logger.error(f"Cannot connect to Byparr at {self.flaresolverr_url} - is it running?")
            self._set_bypass_error("cannot connect, is it running?")
            return None
        except requests.Timeout:
            logger.error(f"Byparr at {self.flaresolverr_url} did not answer in time")
            if not cut_short:  # a timeout shortened by the search deadline says nothing about Byparr
                self._set_bypass_error("no answer in time")
            return None
        except Exception as e:
            logger.error(f"Byparr request failed for {url[:60]}: {e}")
            return None

    def _set_bypass_error(self, reason: str) -> None:
        self.bypass_error = reason
        self.bypass_error_at = time.monotonic()

    def bypass_problem(self):
        """The recent bypass proxy failure for the Dashboard, or None. It is only refreshed when a
        request reaches Byparr, which cached cookies make rare, so an old failure is dropped."""
        if self.bypass_error and time.monotonic() - self.bypass_error_at < BYPASS_ERROR_TTL:
            return self.bypass_error
        return None

    def _solve_cf(self):
        """Solve Cloudflare challenge via Byparr and store cookies."""
        logger.info("Solving Cloudflare challenge via Byparr...")
        solution = self._byparr_request(self.base_url)
        if solution and solution.get("status") == 200:
            logger.info("CF challenge solved, cookies acquired")
            return True
        logger.error("Failed to solve CF challenge")
        self.cf_valid = False
        return False

    def _on_private_http(self, fn):
        if getattr(self._private, "http", None) is not None:  # nested: the outer call decides
            return fn()
        self._private.cf_solved = False
        result = super()._on_private_http(fn)
        if result and self._private.cf_solved:
            self.cf_gen += 1  # the new cookies are published (the lock is still held)
        self._private.cf_solved = False
        return result

    def _is_cf_blocked(self, response) -> bool:
        """Check if response is a Cloudflare block (including HTTP 200 JS challenges)."""
        if response.status_code == 403:
            return True
        if response.status_code == 503 and "cloudflare" in response.text.lower():
            return True
        # CF JS challenges can return HTTP 200
        if response.status_code == 200:
            text_lower = response.text[:2000].lower()
            if "cf-browser-verification" in text_lower or "checking your browser" in text_lower:
                return True
        return False

    def get(self, url, **kwargs):
        """GET with automatic CF bypass retry."""
        try:
            gen = self.cf_gen
            r = self.http.get(url, **self._bounded(kwargs))
            if self._is_cf_blocked(r):
                logger.warning(f"CF blocked GET {url[:60]}, solving via Byparr...")
                # Byparr only gets a URL: the query string must be part of it.
                full_url = requests.Request("GET", url, params=kwargs.get("params")).prepare().url
                solution = self._refresh_cf(lambda: self._byparr_request(full_url, "GET"), gen)
                if solution:
                    # Ask again with the fresh cookies: Byparr's own page was fetched without
                    # our login, so it shows a logged-out forum. Its body is the last resort.
                    r = self.http.get(url, **self._bounded(kwargs))
                    if self._is_cf_blocked(r) and solution is not True:
                        return _ByparrResponse(solution)
                    return r
                r = self.http.get(url, **self._bounded(kwargs))
            return r
        except Exception as e:
            logger.error(f"GET {url[:60]} failed: {e}")
            raise

    def post(self, url, data=None, **kwargs):
        """POST with automatic CF bypass retry."""
        try:
            gen = self.cf_gen
            r = self.http.post(url, data=data, **self._bounded(kwargs))
            if self._is_cf_blocked(r):
                logger.warning(f"CF blocked POST {url[:60]}, refreshing CF cookies...")
                if self._refresh_cf(self._solve_cf, gen):
                    r = self.http.post(url, data=data, **self._bounded(kwargs))
            return r
        except Exception as e:
            logger.error(f"POST {url[:60]} failed: {e}")
            raise

    def _refresh_cf(self, fn, gen=None):
        """Run a Byparr call on a private session copy, one at a time with logins.
        Returns None when the lock stays busy too long, and True without calling Byparr
        when another thread solved Cloudflare since `gen` (a cf_gen read before the request)."""
        with self._locked() as acquired:
            if not acquired:
                return None
            if gen is not None and self.cf_gen != gen:
                return True
            return self._on_private_http(fn)

    def _check_or_login(self) -> bool:
        if not self.cf_valid and not self._solve_cf():
            logger.error("CF bypass failed - session will likely not work")
        ok = super()._check_or_login()
        if not ok:
            # CF cookies might be stale: solve again next time
            self.cf_valid = False
        return ok

    def invalidate(self, seen_gen: int) -> bool:
        invalidated = super().invalidate(seen_gen)
        if invalidated:
            self.cf_valid = False
        return invalidated

    def _load_cookies(self):
        result = super()._load_cookies()
        if result:
            self.cf_valid = True
        return result
