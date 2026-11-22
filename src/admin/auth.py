"""Protection for every /admin route: login page + session cookie.

Log in with the account set from the panel (username + password) or with the API key.
One guard on the whole Flask app, so routes added under /admin later are
protected automatically. Torznab endpoints and /health are not touched.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from flask import jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger("admin.auth")

MAX_FAILURES = 5  # failed logins per client IP ...
FAILURE_WINDOW = 60  # ... in this many seconds, then 429
MIN_PASSWORD, MAX_PASSWORD, MAX_USERNAME = 10, 256, 64
HASH_METHOD = "scrypt"
MAX_CONCURRENT_HASHES = 3


def _load_secret(path: Path) -> str:
    """Return the session-signing secret, creating it in the data dir on first run."""
    if path.exists() and path.read_text().strip():
        return path.read_text().strip()
    secret = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret)
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best effort (e.g. Windows)
    return secret


def _load_account(path: Path):
    """The saved {"username", "hash"}, or None when no account is set (or the file is unusable)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.warning("Admin account file %s is unusable (%s): only the API key logs in", path, e)
        return None
    if isinstance(data, dict) and isinstance(data.get("username"), str) and isinstance(data.get("hash"), str):
        return {"username": data["username"], "hash": data["hash"]}
    logger.warning("Admin account file %s has the wrong shape: only the API key logs in", path)
    return None


def _save_account(path: Path, account: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never readable by others, not even briefly
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(account))
    os.replace(tmp, path)


def validate_account(username, password):
    """An error message, or None when both are acceptable."""
    if not isinstance(username, str) or not isinstance(password, str):
        return "Username and password are required"
    if not username.strip() or len(username.strip()) > MAX_USERNAME or not username.strip().isprintable():
        return f"Username must be 1-{MAX_USERNAME} printable characters"
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        return f"Password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters"
    return None


SECRET_HEADER = "X-Mircrewrr-Proxy-Secret"


def client_ip(remote_addr: str, forwarded_for: str, trusted_proxies: list, sent_secret: str = "", secret: str = ""):
    """The client's IP: the socket peer, or the rightmost X-Forwarded-For entry (the one
    the proxy added) when the peer is a trusted proxy that also sent the shared secret.
    None if unparseable."""
    def parse(s):
        a = ipaddress.ip_address(s.strip())
        return a.ipv4_mapped or a if a.version == 6 else a  # ::ffff:1.2.3.4 is 1.2.3.4

    try:
        ip = parse(remote_addr)
        if (forwarded_for and secret and ip in trusted_proxies
                and hmac.compare_digest(sent_secret.strip().encode(), secret.encode())):
            ip = parse(forwarded_for.split(",")[-1])
        return ip
    except ValueError:
        return None


def init_auth(app, server, data_dir: Path, trusted_networks=(), trusted_proxies=(), proxy_secret=""):
    """Protect /admin and /admin/api/* with the account (username + password) or the server's API key.

    Clients in trusted_networks (ADMIN_TRUSTED_NETWORKS) skip the login; none by default.
    """
    app.secret_key = _load_secret(data_dir / "admin_session_secret")
    account_path = data_dir / "admin_account.json"
    state = {"account": _load_account(account_path)}
    failures: dict = {}  # client IP -> times of recent failed logins
    failures_lock = threading.Lock()
    account_lock = threading.Lock()
    hash_slots = threading.BoundedSemaphore(MAX_CONCURRENT_HASHES)
    dummy_hash = generate_password_hash(secrets.token_hex(8), method=HASH_METHOD)  # a wrong username costs a hash check too
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="mircrewrr_admin",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )

    def api_key() -> str:
        return str(server.api_key or "")  # str(): config.json may hold a non-string

    def token() -> str:
        # Bound to the current key and password: rotating either invalidates open sessions.
        bound = api_key() + "\0" + (state["account"] or {}).get("hash", "")
        return hmac.new(app.secret_key.encode(), bound.encode(), hashlib.sha256).hexdigest()

    def password_ok(username: str, password: str) -> bool:
        if len(password) > MAX_PASSWORD or len(username) > 4 * MAX_USERNAME:
            return False  # no account can match; do not hash what an anonymous client sends
        acct = state["account"]
        with hash_slots:  # scrypt takes ~32 MiB: never more than a few at once, whatever the number of clients
            pw_ok = check_password_hash(acct["hash"] if acct else dummy_hash, password)
        user_ok = acct is not None and hmac.compare_digest(username.strip().encode(), acct["username"].encode())
        return pw_ok and user_ok

    def client() -> str:
        """The rate-limit bucket: the client IP, an IPv6 client as its /64 (one host holds a whole /64)."""
        ip = client_ip(request.remote_addr or "", request.headers.get("X-Forwarded-For", ""), trusted_proxies,
                       request.headers.get(SECRET_HEADER, ""), proxy_secret)
        if ip is None:
            return request.remote_addr or ""
        return str(ipaddress.ip_network(f"{ip}/64", strict=False)) if ip.version == 6 else str(ip)

    def recent_failures(ip: str) -> list:
        # Caller holds failures_lock.
        now = time.monotonic()
        if len(failures) > 1000:
            for k in [k for k, v in failures.items() if not v or now - v[-1] >= FAILURE_WINDOW]:
                del failures[k]
        kept = [t for t in failures.get(ip, []) if now - t < FAILURE_WINDOW]
        failures[ip] = kept
        return kept

    def reserve(ip: str):
        """Count a failed login before the credentials are checked, so parallel requests cannot
        outrun the limit. Returns the timestamp to give back with release() on success, or None
        when the IP is already at the limit."""
        with failures_lock:
            recent = recent_failures(ip)
            if len(recent) >= MAX_FAILURES:
                return None
            stamp = time.monotonic()
            recent.append(stamp)
            return stamp

    def release(ip: str, stamp: float) -> None:
        with failures_lock:
            if stamp in failures.get(ip, []):
                failures[ip].remove(stamp)

    def key_ok(candidate: str) -> bool:
        # An empty configured key never authenticates (fail closed).
        return bool(api_key()) and hmac.compare_digest(candidate.encode(), api_key().encode())

    def is_authenticated() -> bool:
        if not api_key():
            return False
        if key_ok(request.headers.get("X-Api-Key", "")):
            return True
        return hmac.compare_digest(str(session.get("auth", "")), token())

    def is_trusted() -> bool:
        if not trusted_networks:
            return False
        ip = client_ip(request.remote_addr or "", request.headers.get("X-Forwarded-For", ""), trusted_proxies,
                       request.headers.get(SECRET_HEADER, ""), proxy_secret)
        return ip is not None and any(ip in n for n in trusted_networks)

    def login_page(error=None, status=200, key_form=False):
        return render_template("login.html", error=error, has_account=state["account"] is not None,
                               key_form=key_form), status

    def same_origin() -> bool:
        # Writes through the session cookie or a trusted network must come from this
        # panel: SameSite=Lax does not stop a sibling *.ruggiero.cc site, and a trusted
        # network has no cookie at all. Browsers send Origin on every write.
        origin = request.headers.get("Origin")
        return bool(origin) and urlparse(origin).netloc == request.host

    # Logged here, not while parsing the env, so they reach the admin log panel.
    if trusted_networks:
        logger.warning("Admin panel open without login to %s", ", ".join(map(str, trusted_networks)))
    for n in trusted_networks:
        if n.prefixlen < (8 if n.version == 4 else 32):
            logger.warning("ADMIN_TRUSTED_NETWORKS: %s is a very wide network", n)
    if proxy_secret and not trusted_proxies:
        logger.warning("ADMIN_TRUSTED_PROXY_SECRET is set but ADMIN_TRUSTED_PROXIES is empty: X-Forwarded-For is ignored")

    @app.after_request
    def admin_no_frames(resp):
        if request.path == "/admin" or request.path.startswith("/admin/"):
            resp.headers["X-Frame-Options"] = "DENY"
        return resp

    @app.before_request
    def admin_guard():
        path = request.path
        if not (path == "/admin" or path.startswith("/admin/")):
            return None
        # Public: the login form and the static assets it uses.
        if path == "/admin/login" or path.startswith("/admin/static/"):
            return None
        # A script sending X-Api-Key needs no Origin: other sites cannot set that header.
        # Guesses in the header count against the same per-IP limit as the login form.
        # A client the panel admits anyway (open session, trusted network) is not guessing: its
        # stale header must not use up attempts that the owner's login needs.
        header_key = request.headers.get("X-Api-Key", "")
        if header_key and not (hmac.compare_digest(str(session.get("auth", "")), token()) or is_trusted()):
            ip = client()
            stamp = reserve(ip)
            if stamp is None:
                return jsonify({"error": "Too many failed attempts"}), 429
            if key_ok(header_key):
                release(ip, stamp)
                return None
            logger.warning("Admin API key rejected from %s", ip)
        if is_authenticated() or is_trusted():
            if request.method in ("GET", "HEAD") or same_origin():
                return None
            return jsonify({"error": "Cross-origin write refused"}), 403
        if path.startswith("/admin/api/"):
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for("admin_login"))

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method != "POST":
            return login_page()
        by_key = "api_key" in request.form  # the fallback form sends only the key
        # A page on another site can post this form from the owner's browser and burn their
        # attempts. Browsers always send Origin on a POST; scripts without one are fine.
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return login_page("Cross-origin login refused", 403, by_key)
        ip = client()
        stamp = reserve(ip)
        if stamp is None:
            return login_page("Too many failed attempts. Try again in a minute.", 429, by_key)
        if by_key:
            ok = key_ok(request.form.get("api_key", ""))
        else:
            ok = password_ok(request.form.get("username", ""), request.form.get("password", ""))
        if ok:
            release(ip, stamp)  # a good login does not use up an attempt
            session.clear()
            session["auth"] = token()
            session.permanent = True
            return redirect("/admin")
        logger.warning("Admin login failed from %s", ip)
        return login_page("Invalid API key" if by_key else "Invalid username or password", 401, by_key)

    @app.route("/admin/api/account", methods=["GET", "PUT"])
    def admin_account():
        # Behind admin_guard: reached only with a session, the API key or a trusted network.
        if request.method == "GET":
            return jsonify({"username": (state["account"] or {}).get("username")})
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        username, password = body.get("username"), body.get("password")
        error = validate_account(username, password)
        if error:
            return jsonify({"error": error}), 400
        has_session = hmac.compare_digest(str(session.get("auth", "")), token())
        account = {"username": username.strip(), "hash": generate_password_hash(password, method=HASH_METHOD)}
        with account_lock:  # the file and the in-memory copy always hold the same account
            _save_account(account_path, account)
            state["account"] = account
        if has_session:
            session["auth"] = token()  # the new password ends the other sessions, not this one
        logger.info("Admin account saved for %s", account["username"])
        return jsonify({"username": account["username"]})

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))
