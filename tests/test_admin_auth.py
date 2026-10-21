"""Admin auth guard: /admin* needs the API key; Torznab and /health stay open."""

import pytest

from admin.auth import init_auth
from admin.routes import admin_bp, init_admin
from torznab.server import BaseSite, TorznabServer

KEY = "test-key"


class FakeSite(BaseSite):
    def search(self, query, categories, target_season, target_episode):
        return []

    def download(self, topic_id, infohash, season, episode):
        return None

    def get_capabilities_xml(self):
        return "<caps/>"

    def health_info(self):
        return {"status": "ok"}


@pytest.fixture
def server(tmp_path):
    server = TorznabServer(api_key=KEY)
    server.register_site("fake", FakeSite())
    init_auth(server.app, server, tmp_path)
    init_admin(server, None, {}, {})
    server.app.register_blueprint(admin_bp)
    return server


@pytest.fixture
def client(server):
    c = server.app.test_client()
    c.environ_base["HTTP_ORIGIN"] = "http://localhost"  # the panel's own origin, as a browser sends it
    return c


def login(client, key=KEY):
    return client.post("/admin/login", data={"api_key": key})


def test_admin_page_redirects_to_login_without_session(client):
    r = client.get("/admin")
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/login")
    assert client.get("/admin/login").status_code == 200


@pytest.mark.parametrize("path", ["/admin/api/status", "/admin/api/config", "/admin/api/logs", "/admin/api/nope"])
def test_admin_api_requires_session(client, path):
    assert client.get(path).status_code == 401


def test_admin_write_requires_session(client):
    assert client.put("/admin/api/config", json={"api_key": "x"}).status_code == 401
    assert client.delete("/admin/api/sites/fake").status_code == 401


def test_right_key_logs_in(client):
    r = login(client)
    assert r.status_code == 302
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert client.get("/admin/api/status").status_code == 200
    assert client.get("/admin").status_code == 200


def test_wrong_key_fails(client):
    r = login(client, "wrong")
    assert r.status_code == 401
    assert client.get("/admin/api/status").status_code == 401


def test_empty_key_never_authenticates(tmp_path):
    server = TorznabServer(api_key="")
    init_auth(server.app, server, tmp_path)
    init_admin(server, None, {}, {})
    server.app.register_blueprint(admin_bp)
    c = server.app.test_client()
    assert login(c, "").status_code == 401
    assert c.get("/admin/api/status").status_code == 401


def test_api_key_header_authenticates(client):
    assert client.get("/admin/api/status", headers={"X-Api-Key": KEY}).status_code == 200
    assert client.get("/admin/api/status", headers={"X-Api-Key": "bad"}).status_code == 401


def test_session_writes_need_the_panel_origin(server):
    c = server.app.test_client()  # no Origin by default
    login(c)
    assert c.put("/admin/api/config", json={}).status_code == 403
    assert c.put("/admin/api/config", json={}, headers={"Origin": "https://other.localhost"}).status_code == 403
    assert c.get("/admin/api/status").status_code == 200  # reads are unaffected
    # A script with X-Api-Key needs no Origin.
    r = server.app.test_client().put("/admin/api/config", json={}, headers={"X-Api-Key": KEY})
    assert r.status_code not in (401, 403)  # past the guard (the route itself rejects the empty body)


def test_logout_ends_session(client):
    login(client)
    assert client.post("/admin/logout").status_code == 302
    assert client.get("/admin/api/status").status_code == 401


def test_rotating_key_invalidates_session(client, server):
    login(client)
    server.api_key = "rotated"
    assert client.get("/admin/api/status").status_code == 401


def test_secret_persists_across_restarts(tmp_path):
    secrets = []
    for _ in range(2):
        server = TorznabServer(api_key=KEY)
        init_auth(server.app, server, tmp_path)
        secrets.append(server.app.secret_key)
    assert secrets[0] == secrets[1]


def test_static_assets_stay_public(client):
    assert client.get("/admin/static/style.css").status_code == 200


def test_health_and_torznab_unaffected(client):
    assert client.get("/health").status_code == 200
    assert client.get("/fake/api?t=caps&apikey=" + KEY).status_code == 200
    assert client.get("/fake/api?t=caps").status_code == 401  # Torznab's own key check, unchanged
