"""ADMIN_TRUSTED_NETWORKS: /admin without login for trusted clients; Torznab keeps its key."""

import pytest

from admin.auth import client_ip, init_auth
from admin.routes import admin_bp, init_admin
from config import Config, parse_networks
from torznab.server import TorznabServer

from test_admin_auth import KEY, FakeSite

LAN = "192.168.1.0/24,100.114.0.0/16,fd00::/8"
PROXY = "172.22.0.1"


def make_client(tmp_path, networks=LAN, proxies=PROXY):
    server = TorznabServer(api_key=KEY)
    server.register_site("fake", FakeSite())
    init_auth(server.app, server, tmp_path, parse_networks(networks, "N"), parse_networks(proxies, "P"))
    init_admin(server, None, {}, {})
    server.app.register_blueprint(admin_bp)
    return server.app.test_client()


def get(client, path="/admin/api/status", peer=PROXY, xff=None, **kw):
    headers = {"X-Forwarded-For": xff} if xff else {}
    return client.get(path, headers=headers, environ_base={"REMOTE_ADDR": peer}, **kw)


def test_trusted_client_through_the_proxy_needs_no_login(tmp_path):
    c = make_client(tmp_path)
    assert get(c, xff="192.168.1.2").status_code == 200
    assert get(c, "/admin", xff="100.114.3.4").status_code == 200


def test_rightmost_forwarded_entry_is_the_client(tmp_path):
    c = make_client(tmp_path)
    # A client may send its own X-Forwarded-For: only the proxy's last entry counts.
    assert get(c, xff="192.168.1.2, 8.8.8.8").status_code == 401
    assert get(c, xff="8.8.8.8, 192.168.1.2").status_code == 200


def test_forwarded_for_from_an_untrusted_peer_is_ignored(tmp_path):
    c = make_client(tmp_path)
    assert get(c, peer="172.22.0.5", xff="192.168.1.2").status_code == 401
    assert get(c, peer="192.168.1.7").status_code == 200  # a direct LAN peer is itself the client


def test_ipv6_and_mapped_addresses(tmp_path):
    c = make_client(tmp_path)
    assert get(c, xff="fd00::5").status_code == 200
    assert get(c, xff="2001:db8::1").status_code == 401
    assert get(c, peer="::ffff:172.22.0.1", xff="::ffff:192.168.1.2").status_code == 200
    assert get(c, xff="not-an-ip").status_code == 401


def test_unset_means_login_as_before(tmp_path):
    c = make_client(tmp_path, networks="", proxies="")
    assert get(c, peer="192.168.1.2").status_code == 401


def test_cross_site_writes_are_refused(tmp_path):
    c = make_client(tmp_path)
    h = {"X-Forwarded-For": "192.168.1.2", "Origin": "https://evil.example"}
    r = c.put("/admin/api/config", json={}, headers=h, environ_base={"REMOTE_ADDR": PROXY})
    assert r.status_code == 401


def test_torznab_still_needs_the_key(tmp_path):
    c = make_client(tmp_path)
    r = c.get("/fake/api?t=search&q=x&apikey=wrong", environ_base={"REMOTE_ADDR": "192.168.1.2"})
    assert r.status_code == 401


@pytest.mark.parametrize("spec", ["192.168.1.0/33", "lan", "192.168.1.0/24,,nope"])
def test_malformed_variable_stops_the_start(monkeypatch, spec):
    monkeypatch.setenv("ADMIN_TRUSTED_NETWORKS", spec)
    with pytest.raises(ValueError, match="ADMIN_TRUSTED_NETWORKS"):
        Config.from_env()


def test_empty_variable_is_off(monkeypatch):
    monkeypatch.setenv("ADMIN_TRUSTED_NETWORKS", " ")
    assert Config.from_env().admin_trusted_networks == []


def test_client_ip_helper():
    proxies = parse_networks(PROXY, "P")
    assert str(client_ip("10.0.0.1", "192.168.1.2", proxies)) == "10.0.0.1"
    assert str(client_ip(PROXY, "", proxies)) == PROXY
    assert client_ip("garbage", "", proxies) is None
