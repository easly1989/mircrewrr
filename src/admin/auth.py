"""API-key protection for every /admin route (login page + session cookie).

One guard on the whole Flask app, so routes added under /admin later are
protected automatically. Torznab endpoints and /health are not touched.
"""

import hashlib
import hmac
import ipaddress
import logging
import secrets
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from flask import jsonify, redirect, render_template, request, session, url_for

logger = logging.getLogger("admin.auth")


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
    """Protect /admin and /admin/api/* with the server's API key.

    Clients in trusted_networks (ADMIN_TRUSTED_NETWORKS) skip the login; none by default.
    """
    app.secret_key = _load_secret(data_dir / "admin_session_secret")
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="mircrewrr_admin",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )

    def api_key() -> str:
        return str(server.api_key or "")  # str(): config.json may hold a non-string

    def token() -> str:
        # Bound to the current key: rotating the API key invalidates open sessions.
        return hmac.new(app.secret_key.encode(), api_key().encode(), hashlib.sha256).hexdigest()

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
        if key_ok(request.headers.get("X-Api-Key", "")):
            return None
        if is_authenticated() or is_trusted():
            if request.method in ("GET", "HEAD") or same_origin():
                return None
            return jsonify({"error": "Cross-origin write refused"}), 403
        if path.startswith("/admin/api/"):
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for("admin_login"))

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        error = None
        if request.method == "POST":
            if key_ok(request.form.get("api_key", "")):
                session.clear()
                session["auth"] = token()
                session.permanent = True
                return redirect("/admin")
            logger.warning("Admin login failed from %s", request.remote_addr)
            error = "Invalid API key"
        return render_template("login.html", error=error), (401 if error else 200)

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))
