"""Admin auth guard: /admin* needs the account or the API key; Torznab and /health stay open."""

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


# --- username + password account ---

USER, PASSWORD = "carlo", "correct horse battery"


def password_login(client, username=USER, password=PASSWORD):
    return client.post("/admin/login", data={"username": username, "password": password})


def set_account(client, username=USER, password=PASSWORD):
    return client.put("/admin/api/account", json={"username": username, "password": password})


def test_without_account_the_login_page_asks_for_the_key_only(client):
    page = client.get("/admin/login").get_data(as_text=True)
    assert 'name="api_key"' in page and 'name="username"' not in page
    login(client)
    assert client.get("/admin/api/account").get_json() == {"username": None}


def test_account_endpoint_needs_auth(client):
    assert client.get("/admin/api/account").status_code == 401
    assert set_account(client).status_code == 401


def test_set_account_then_log_in_with_it(client, server, tmp_path):
    login(client)
    assert set_account(client).get_json() == {"username": USER}
    assert PASSWORD not in (tmp_path / "admin_account.json").read_text()  # only a hash is stored

    fresh = server.app.test_client()
    page = fresh.get("/admin/login").get_data(as_text=True)
    assert 'name="username"' in page and 'name="api_key"' in page  # the key stays as a fallback
    assert password_login(fresh).status_code == 302
    assert fresh.get("/admin/api/status").status_code == 200


def test_wrong_password_or_username_fails_the_same_way(client):
    login(client)
    set_account(client)
    for user, pw in [(USER, "wrong password!"), ("someone", PASSWORD), ("", ""), (USER, "")]:
        r = password_login(client, user, pw)
        assert r.status_code == 401 and b"Invalid username or password" in r.data


def test_api_key_still_logs_in_with_an_account(client, server):
    login(client)
    assert set_account(client).status_code == 200
    other = server.app.test_client()
    assert login(other).status_code == 302
    assert other.get("/admin/api/status").status_code == 200
    assert other.get("/admin/api/status", headers={"X-Api-Key": KEY}).status_code == 200


@pytest.mark.parametrize("username,password", [
    ("carlo", "short"), ("", PASSWORD), ("   ", PASSWORD), ("x" * 65, PASSWORD),
    ("carlo", "x" * 257), (None, PASSWORD), ("carlo", 12345678901), ("bad\x00name", PASSWORD),
])
def test_invalid_account_is_refused(client, tmp_path, username, password):
    login(client)
    r = set_account(client, username, password)
    assert r.status_code == 400 and r.get_json()["error"]
    assert not (tmp_path / "admin_account.json").exists()


def test_new_password_ends_other_sessions_but_not_this_one(server):
    mine, other = server.app.test_client(), server.app.test_client()
    for c in (mine, other):
        c.environ_base["HTTP_ORIGIN"] = "http://localhost"
        login(c)
    set_account(mine)
    assert mine.get("/admin/api/status").status_code == 200
    assert other.get("/admin/api/status").status_code == 401


def test_account_survives_restart(tmp_path):
    def build():
        server = TorznabServer(api_key=KEY)
        init_auth(server.app, server, tmp_path)
        init_admin(server, None, {}, {})
        server.app.register_blueprint(admin_bp)
        return server.app.test_client()

    first = build()
    first.environ_base["HTTP_ORIGIN"] = "http://localhost"
    login(first)
    set_account(first)
    assert password_login(build()).status_code == 302


def test_failed_logins_are_limited_per_ip(client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("admin.auth.time.monotonic", lambda: now[0])
    for _ in range(5):
        assert login(client, "wrong").status_code == 401
    assert login(client).status_code == 429  # even the right key is refused while limited
    assert b"Too many" in login(client).data
    now[0] += 61
    assert login(client).status_code == 302  # the window has passed


def test_limit_counts_password_and_key_failures_together(client):
    login(client)
    set_account(client)
    for i in range(5):
        (password_login(client, USER, "wrong password!") if i % 2 else login(client, "wrong"))
    assert password_login(client).status_code == 429


def test_successful_login_does_not_use_up_an_attempt(client):
    for _ in range(4):
        login(client, "wrong")
    assert login(client).status_code == 302
    assert login(client, "wrong").status_code == 401  # the 5th failure
    assert login(client, "wrong").status_code == 429


def test_header_key_guesses_count_against_the_limit(client):
    for _ in range(5):
        assert client.get("/admin/api/status", headers={"X-Api-Key": "guess"}).status_code == 401
    assert client.get("/admin/api/status", headers={"X-Api-Key": KEY}).status_code == 429
    assert login(client).status_code == 429  # the same bucket as the login form


def test_good_header_key_calls_are_not_limited(client):
    for _ in range(20):
        assert client.get("/admin/api/status", headers={"X-Api-Key": KEY}).status_code == 200


def test_login_posted_from_another_site_is_refused_and_not_counted(client):
    for _ in range(10):
        r = client.post("/admin/login", data={"api_key": "x"}, headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
    assert login(client).status_code == 302  # the owner is not locked out


def test_ipv6_clients_share_one_bucket_per_64(server):
    def from_addr(addr):
        c = server.app.test_client()
        c.environ_base["REMOTE_ADDR"] = addr
        return c
    for i in range(5):
        assert login(from_addr(f"2001:db8:1:2::{i + 1}"), "wrong").status_code == 401  # a new address each time
    assert login(from_addr("2001:db8:1:2::99")).status_code == 429
    assert login(from_addr("2001:db8:1:3::1")).status_code == 302  # another /64


def test_oversized_password_is_refused_without_a_hash_check(client, monkeypatch):
    login(client)
    set_account(client)

    def boom(*a, **k):
        raise AssertionError("an oversized password must not be hashed")

    monkeypatch.setattr("admin.auth.check_password_hash", boom)
    assert password_login(client, USER, "x" * 5000).status_code == 401


def test_stale_header_key_from_an_open_session_costs_no_attempt(client):
    login(client)  # an open session
    for _ in range(10):
        assert client.get("/admin/api/status", headers={"X-Api-Key": "old-key"}).status_code == 200
    other = client.application.test_client()
    assert login(other).status_code == 302  # the bucket is still empty


def test_unusable_account_file_leaves_only_the_key(tmp_path):
    (tmp_path / "admin_account.json").write_text("{not json")
    server = TorznabServer(api_key=KEY)
    init_auth(server.app, server, tmp_path)
    init_admin(server, None, {}, {})
    server.app.register_blueprint(admin_bp)
    c = server.app.test_client()
    assert password_login(c).status_code == 401
    assert login(c).status_code == 302


# --- Torznab key guesses share the limiter ---

def caps(client, key):
    return client.get("/fake/api?t=caps&apikey=" + key)


def test_wrong_torznab_keys_are_limited_and_then_even_the_right_one_waits(client):
    for _ in range(5):
        assert caps(client, "guess").status_code == 401
    r = caps(client, KEY)
    assert r.status_code == 429 and b'code="429"' in r.data


def test_torznab_failures_do_not_lock_the_panel_login(client):
    for _ in range(6):
        caps(client, "old-key")
    assert caps(client, "old-key").status_code == 429  # the Torznab bucket is full ...
    assert login(client).status_code == 302  # its own bucket


def test_right_torznab_key_and_missing_key_are_not_counted(client):
    for _ in range(20):
        assert caps(client, KEY).status_code == 200
        assert client.get("/fake/api?t=caps").status_code == 401  # no key sent: nothing guessed
    assert caps(client, "guess").status_code == 401


def test_default_or_short_api_key_logs_a_warning(tmp_path, caplog):
    for key in ("mircrew-api-key", "short"):
        caplog.clear()
        init_auth(TorznabServer(api_key=key).app, TorznabServer(api_key=key), tmp_path)
        assert "public default or shorter" in caplog.text
    caplog.clear()
    server = TorznabServer(api_key="k" * 24)
    init_auth(server.app, server, tmp_path)
    assert "public default or shorter" not in caplog.text


def test_searches_in_flight_with_the_right_key_do_not_fill_the_bucket(tmp_path):
    """Five slow searches with the right key (a search runs up to 90 s) must not make the sixth call 429."""
    import threading

    started, release_all = threading.Semaphore(0), threading.Event()

    class SlowSite(FakeSite):
        def search(self, query, categories, target_season, target_episode):
            started.release()
            release_all.wait(10)
            return []

    server = TorznabServer(api_key=KEY)
    server.register_site("fake", SlowSite())
    init_auth(server.app, server, tmp_path)
    init_admin(server, None, {}, {})
    server.app.register_blueprint(admin_bp)

    workers = [threading.Thread(target=lambda: server.app.test_client().get(f"/fake/api?t=search&q=x&apikey={KEY}"))
               for _ in range(5)]
    for w in workers:
        w.start()
    try:
        for _ in range(5):
            assert started.acquire(timeout=10)  # all five are inside the handler
        assert server.app.test_client().get(f"/fake/api?t=caps&apikey={KEY}").status_code == 200
    finally:
        release_all.set()
        for w in workers:
            w.join(10)


def test_429_says_when_to_retry(client):
    for _ in range(5):
        caps(client, "guess")
    r = caps(client, KEY)
    assert r.status_code == 429 and r.headers["Retry-After"] == "60"
