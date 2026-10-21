"""The real server (waitress) must hand X-Forwarded-For to admin.auth untouched.

Flask's test client skips waitress, which by default drops proxy headers.
"""

import threading
import urllib.error
import urllib.request

from waitress import create_server

from admin.auth import SECRET_HEADER, init_auth
from admin.routes import admin_bp, init_admin
from config import parse_addresses, parse_networks
from main import WAITRESS_OPTIONS
from torznab.server import TorznabServer

from test_admin_auth import KEY, FakeSite

SECRET = "s3cret-from-traefik-0123"


def status(url, headers):
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def test_forwarded_for_reaches_the_app(tmp_path):
    server = TorznabServer(api_key=KEY)
    server.register_site("fake", FakeSite())
    # The test client connects from 127.0.0.1, which plays the reverse proxy here.
    init_auth(server.app, server, tmp_path, parse_networks("192.168.1.0/24", "N"),
              parse_addresses("127.0.0.1", "P"), SECRET)
    init_admin(server, None, {}, {})
    server.app.register_blueprint(admin_bp)

    httpd = create_server(server.app, host="127.0.0.1", port=0, **WAITRESS_OPTIONS)
    threading.Thread(target=httpd.run, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.effective_port}/admin/api/status"
        assert status(url, {"X-Forwarded-For": "192.168.1.2", SECRET_HEADER: SECRET}) == 200
        assert status(url, {"X-Forwarded-For": "192.168.1.2"}) == 401
        assert status(url, {"X-Forwarded-For": "8.8.8.8", SECRET_HEADER: SECRET}) == 401
    finally:
        httpd.close()
