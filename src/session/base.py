"""Sessione HTTP base con persistenza cookie e login generico."""

import copy
import json
import os
import threading
import time
import logging
from contextlib import contextmanager
from pathlib import Path

import requests

logger = logging.getLogger("session")

# How long a thread waits for a login (or Cloudflare refresh) started by another thread
# before it carries on with the current session. Byparr alone can take ~90 s, so a hung
# one does not hold requests past Prowlarr's timeout (~100 s).
LOGIN_LOCK_TIMEOUT = 60

# Logins the site answered and refused, one after the other, before login attempts stop
# until a person acts. Every refused login counts against the forum's attempt limit
# (then a CAPTCHA a script cannot solve), so retrying a rejected password helps nobody.
# LOGIN_MAX_REFUSALS=0 in the environment turns the block off (the reasons are still logged).
LOGIN_MAX_REFUSALS = int(os.getenv("LOGIN_MAX_REFUSALS") or 2)  # an empty value (compose) means the default


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

        self._http = requests.Session()
        self._http.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        })
        self.user_agent = None
        self.session_valid = False
        self.last_login = 0
        # Counts finished login attempts, so invalidate() can tell a session that died
        # from one another thread has just renewed.
        self.login_gen = 0
        # Set by _do_login when the site answered and refused the login (not for a network
        # or Cloudflare failure, where no attempt reached the site).
        self.login_refusal = None
        self.last_login_secs = None  # how long the last login took, to size a retry
        self.login_refusals = 0
        # Reason of the last refusal once LOGIN_MAX_REFUSALS in a row piled up: no login is
        # tried until retry_login(), saved credentials (a new session) or a restart.
        self.login_blocked = None
        # Logins and Cloudflare refreshes run one at a time, on a private copy of the
        # HTTP session that replaces the shared one only when they succeed.
        self._login_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._private = threading.local()

        self._load_cookies()

    @property
    def http(self) -> requests.Session:
        """The shared HTTP session, or this thread's private copy while it logs in."""
        private = getattr(self._private, "http", None)
        return private if private is not None else self._http

    def set_deadline(self, deadline):
        """Give this thread a time.monotonic() deadline that lock waits and Byparr calls
        respect, so one search never outlives its caller's timeout. None clears it."""
        self._private.deadline = deadline

    def _time_left(self):
        deadline = getattr(self._private, "deadline", None)
        return None if deadline is None else deadline - time.monotonic()

    @contextmanager
    def _locked(self):
        """Hold the login lock; yields False when another thread kept it for too long
        (or longer than this thread's deadline allows)."""
        left = self._time_left()
        wait = LOGIN_LOCK_TIMEOUT if left is None else max(0, min(LOGIN_LOCK_TIMEOUT, left))
        acquired = self._login_lock.acquire(timeout=wait)
        try:
            yield acquired
        finally:
            if acquired:
                self._login_lock.release()

    def _on_private_http(self, fn):
        """Run fn() with this thread's `self.http` bound to a private copy of the shared
        session (cookies and headers, the Cloudflare cookies depend on the User-Agent).
        The copy replaces the shared session only if fn() returns something truthy, so
        other threads never see half-written cookies and a failed login leaves the live
        session untouched. Requests already in flight finish on the session they started
        with. Call it with the login lock held; a nested call reuses the copy.
        """
        if getattr(self._private, "http", None) is not None:
            return fn()
        candidate = requests.Session()
        candidate.headers.update(self._http.headers)
        with self._http.cookies._cookies_lock:  # requests in flight may set cookies meanwhile
            snapshot = list(self._http.cookies)
        for c in snapshot:
            candidate.cookies.set_cookie(copy.copy(c))
        baseline = {(c.name, c.domain, c.path): c.value for c in snapshot}
        self._private.http = candidate
        try:
            result = fn()
        finally:
            self._private.http = None
        if result:
            # Cookies that requests still in flight added or changed on the shared session
            # while this ran are kept, unless the new login set a cookie of that name: the
            # newer login wins, and a cookie it expired is not brought back.
            with self._state_lock:
                with self._http.cookies._cookies_lock:
                    current = list(self._http.cookies)
                # Names the login itself set or changed; copies cloned unchanged do not count.
                names = {c.name for c in candidate.cookies
                         if baseline.get((c.name, c.domain, c.path)) != c.value}
                for c in current:
                    if baseline.get((c.name, c.domain, c.path)) != c.value and c.name not in names:
                        candidate.cookies.set_cookie(copy.copy(c))
                self._http = candidate
        return result

    def get(self, url, **kwargs):
        """GET request."""
        return self.http.get(url, **self._bounded(kwargs))

    def post(self, url, data=None, **kwargs):
        """POST request."""
        return self.http.post(url, data=data, **self._bounded(kwargs))

    def _bounded(self, kwargs: dict) -> dict:
        """Default request timeout (30 s), cut to what is left of this thread's deadline."""
        kwargs.setdefault("timeout", 30)
        left = self._time_left()
        if left is not None:
            kwargs["timeout"] = min(kwargs["timeout"], max(1, left))
        return kwargs

    def _is_fresh(self) -> bool:
        return self.session_valid and (time.time() - self.last_login) < 3600

    def ensure_logged_in(self) -> "BaseSession":
        """Verifica la sessione, fa login se necessario (un thread alla volta)."""
        if self._is_fresh() or self.login_blocked:
            return self

        gen = self.login_gen
        with self._locked() as acquired:
            if not acquired:
                logger.warning(f"Another login is still running after {LOGIN_LOCK_TIMEOUT}s, using the current session")
                return self
            # Threads that waited here reuse the attempt the first one just made. After a
            # failure they do not try again at once: every failed login counts against
            # phpBB's attempt limit, and the next request will try again.
            if self._is_fresh() or self.login_gen != gen or self.login_blocked:
                return self

            self.login_refusal = None
            started = time.monotonic()
            ok = self._on_private_http(self._check_or_login)
            left = self._time_left()
            if not ok and left is not None and left <= 0:
                # The search deadline cut the login short: that says nothing about the
                # credentials, so it is not recorded as an attempt other threads must respect.
                logger.warning("Login stopped by the search deadline, not counted as a failed attempt")
                return self
            with self._state_lock:
                self.last_login_secs = time.monotonic() - started
                self.session_valid = bool(ok)
                if ok:
                    self.last_login = time.time()
                    self.login_refusals = 0
                elif self.login_refusal:
                    self.login_refusals += 1
                    if LOGIN_MAX_REFUSALS and self.login_refusals >= LOGIN_MAX_REFUSALS:
                        self.login_blocked = self.login_refusal
                self.login_gen += 1
            if self.login_blocked:
                logger.error(f"Login refused {self.login_refusals} times in a row ({self.login_blocked}): "
                             "no new attempt until 'Retry login' in the admin panel, saved credentials or a restart")
            elif not ok:
                logger.error("Login failed - searches will likely return 0 results")
        return self

    def retry_login(self) -> bool:
        """Lift a login block and try once (the admin panel's button)."""
        with self._state_lock:
            self.login_blocked = None
            self.login_refusals = 0
            self.session_valid = False
        self.ensure_logged_in()
        return self.session_valid

    def invalidate(self, seen_gen: int) -> bool:
        """Mark the session dead after a request saw a logged-out page, unless a login
        finished since that request started (`seen_gen` was read before it)."""
        with self._state_lock:
            if self.login_gen != seen_gen:
                return False
            self.session_valid = False
            return True

    def _check_or_login(self) -> bool:
        try:
            r = self.get(self.base_url)
            html = r.text if hasattr(r, "text") else str(r)
            if self._check_logged_in(html):
                logger.info("Session still valid")
                return True
        except Exception as e:
            logger.error(f"Session check failed: {e}")
        return self._do_login()

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
