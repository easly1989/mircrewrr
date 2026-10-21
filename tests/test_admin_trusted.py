"""ADMIN_TRUSTED_NETWORKS: /admin without login for trusted clients; Torznab keeps its key."""

import pytest

from admin.auth import SECRET_HEADER, client_ip, init_auth
from admin.routes import admin_bp, init_admin
from config import Config, parse_addresses, parse_admin_trust, parse_networks
from torznab.server import TorznabServer

from test_admin_auth import KEY, FakeSite

LAN = "192.168.1.0/24,100.114.0.0/16,fd00::/8"
PROXY = "172.22.0.1"
SECRET = "s3cret-from-traefik-0123"


def make_client(tmp_path, networks=LAN, proxies=PROXY, secret=SECRET):
    server = TorznabServer(api_key=KEY)
    server.register_site("fake", FakeSite())
    init_auth(server.app, server, tmp_path, parse_networks(networks, "N"), parse_addresses(proxies, "P"), secret)
    init_admin(server, None, {}, {})
    server.app.register_blueprint(admin_bp)
    return server.app.test_client()


def get(client, path="/admin/api/status", peer=PROXY, xff=None, secret=SECRET, **kw):
    headers = {}
    if xff:
        headers["X-Forwarded-For"] = xff
    if secret:
        headers[SECRET_HEADER] = secret
    return client.get(path, headers=headers, environ_base={"REMOTE_ADDR": peer}, **kw)


def test_trusted_client_through_the_proxy_needs_no_login(tmp_path):
    c = make_client(tmp_path)
    assert get(c, xff="192.168.1.2").status_code == 200
    r = get(c, "/admin", xff="100.114.3.4")
    assert r.status_code == 200 and r.headers["X-Frame-Options"] == "DENY"


def test_forged_forwarded_for_from_the_gateway_without_secret(tmp_path):
    # Behind Docker's port proxy every direct caller is the gateway: only the secret counts.
    c = make_client(tmp_path)
    assert get(c, xff="192.168.1.2", secret=None).status_code == 401
    assert get(c, xff="192.168.1.2", secret="wrong-secret-0123456").status_code == 401
    r = c.put("/admin/api/config", json={}, headers={"X-Forwarded-For": "192.168.1.2"}, environ_base={"REMOTE_ADDR": PROXY})
    assert r.status_code == 401


def test_rightmost_forwarded_entry_is_the_client(tmp_path):
    c = make_client(tmp_path)
    assert get(c, xff="192.168.1.2, 8.8.8.8").status_code == 401
    assert get(c, xff="8.8.8.8, 192.168.1.2").status_code == 200


def test_forwarded_for_from_an_untrusted_peer_is_ignored(tmp_path):
    c = make_client(tmp_path)
    assert get(c, peer="172.22.0.5", xff="192.168.1.2").status_code == 401
    assert get(c, peer="192.168.1.7", secret=None).status_code == 200  # a direct LAN peer is itself the client


def test_ipv6_and_mapped_addresses(tmp_path):
    c = make_client(tmp_path)
    assert get(c, xff="fd00::5").status_code == 200
    assert get(c, xff="2001:db8::1").status_code == 401
    assert get(c, peer="::ffff:172.22.0.1", xff="::ffff:192.168.1.2").status_code == 200
    assert get(c, xff="not-an-ip").status_code == 401


def test_unset_means_login_as_before(tmp_path):
    c = make_client(tmp_path, networks="", proxies="", secret="")
    assert get(c, peer="192.168.1.2", secret=None).status_code == 401
    assert get(c, xff="192.168.1.2").status_code == 401


def test_cross_site_writes_are_refused(tmp_path):
    c = make_client(tmp_path)
    base = {"X-Forwarded-For": "192.168.1.2", SECRET_HEADER: SECRET}
    for extra in ({"Origin": "https://evil.example"}, {"Origin": "https://sibling.localhost"}, {}):  # {}: no Origin
        r = c.put("/admin/api/config", json={}, headers={**base, **extra}, environ_base={"REMOTE_ADDR": PROXY})
        assert r.status_code == 403, extra
    r = c.put("/admin/api/config", json={}, headers={**base, "Origin": "http://localhost"}, environ_base={"REMOTE_ADDR": PROXY})
    assert r.status_code not in (401, 403)  # past the guard (the route itself rejects the empty body)


def test_torznab_still_needs_the_key(tmp_path):
    c = make_client(tmp_path)
    r = c.get("/fake/api?t=search&q=x&apikey=wrong", environ_base={"REMOTE_ADDR": "192.168.1.2"})
    assert r.status_code == 401


@pytest.mark.parametrize("var,spec", [
    ("ADMIN_TRUSTED_NETWORKS", "192.168.1.0/33"),
    ("ADMIN_TRUSTED_NETWORKS", "lan"),
    ("ADMIN_TRUSTED_NETWORKS", "192.168.1.0/24,,nope"),
    ("ADMIN_TRUSTED_PROXIES", "172.22.0.0/16"),  # a network is not a proxy
])
def test_malformed_variable_stops_the_start(monkeypatch, var, spec):
    monkeypatch.setenv(var, spec)
    monkeypatch.setenv("ADMIN_TRUSTED_PROXY_SECRET", SECRET)
    with pytest.raises(ValueError, match=var):
        Config.from_env()


def test_proxies_need_a_secret():
    with pytest.raises(ValueError, match="ADMIN_TRUSTED_PROXY_SECRET"):
        parse_admin_trust(LAN, PROXY, "short")


def test_empty_variable_is_off(monkeypatch):
    monkeypatch.setenv("ADMIN_TRUSTED_NETWORKS", " ")
    monkeypatch.setenv("ADMIN_TRUSTED_PROXIES", "")
    assert Config.from_env().admin_trusted_networks == []


def test_client_ip_helper():
    proxies = parse_addresses(PROXY, "P")
    assert str(client_ip("10.0.0.1", "192.168.1.2", proxies, SECRET, SECRET)) == "10.0.0.1"
    assert str(client_ip(PROXY, "192.168.1.2", proxies, "", SECRET)) == PROXY
    assert str(client_ip(PROXY, "192.168.1.2", proxies, SECRET, SECRET)) == "192.168.1.2"
    assert client_ip("garbage", "", proxies) is None
