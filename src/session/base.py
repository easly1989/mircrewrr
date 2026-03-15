"""Sessione HTTP base con persistenza cookie e login generico."""

import json
import time
import logging
from pathlib import Path

import requests

logger = logging.getLogger("session")


class BaseSession:
    """Sessione HTTP con persistenza cookie e login generico.

    Sottoclassi devono implementare:
    - _do_login() -> bool
    - _check_logged_in(html: str) -> bool
    """

    def __init__(self, base_url: str, username: str, password: str,
                 cookies_file: Path, cookie_ttl: int = 43200):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.cookies_file = cookies_file
        self.cookie_ttl = cookie_ttl

        self.http = requests.Session()
        self.http.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        self.user_agent = None
        self.session_valid = False
        self.last_login = 0

        self._load_cookies()

    def get(self, url, **kwargs):
        """GET request."""
        kwargs.setdefault("timeout", 30)
        return self.http.get(url, **kwargs)

    def post(self, url, data=None, **kwargs):
        """POST request."""
        kwargs.setdefault("timeout", 30)
        return self.http.post(url, data=data, **kwargs)

    def ensure_logged_in(self) -> "BaseSession":
        """Verifica la sessione, fa login se necessario."""
        if self.session_valid and (time.time() - self.last_login) < 3600:
            return self

        try:
            r = self.get(self.base_url)
            html = r.text if hasattr(r, "text") else str(r)
            if self._check_logged_in(html):
                logger.info("Session still valid")
                self.session_valid = True
                self.last_login = time.time()
                return self
        except Exception as e:
            logger.error(f"Session check failed: {e}")

        self._do_login()
        return self

    def _do_login(self) -> bool:
        """Esegue il login. Da sovrascrivere nelle sottoclassi."""
        raise NotImplementedError

    def _check_logged_in(self, html: str) -> bool:
        """Verifica se siamo loggati. Da sovrascrivere nelle sottoclassi."""
        raise NotImplementedError

    # --- Cookie persistence ---

    def _save_cookies(self):
        try:
            cookies = {}
            for c in self.http.cookies:
                cookies[c.name] = {"value": c.value, "domain": c.domain, "path": c.path}
            save_data = {
                "cookies": cookies,
                "user_agent": self.user_agent,
                "time": time.time(),
            }
            with open(self.cookies_file, "w") as f:
                json.dump(save_data, f)
        except Exception as e:
            logger.warning(f"Save cookies error: {e}")

    def _load_cookies(self):
        try:
            if self.cookies_file.exists():
                with open(self.cookies_file) as f:
                    data = json.load(f)
                if time.time() - data.get("time", 0) < self.cookie_ttl:
                    for name, c in data.get("cookies", {}).items():
                        self.http.cookies.set(name, c["value"], domain=c.get("domain", ""))
                    ua = data.get("user_agent")
                    if ua:
                        self.user_agent = ua
                        self.http.headers["User-Agent"] = ua
                    logger.info(f"Loaded {len(data.get('cookies', {}))} saved cookies")
                    return True
        except Exception:
            pass
        return False
