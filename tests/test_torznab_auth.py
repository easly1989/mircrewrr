"""Torznab routes: download and debug need the API key; download links carry it (#65)."""

import pytest

from admin.auth import client_ip
from config import Config, parse_addresses, parse_admin_trust
from torznab.models import TorznabResult
from torznab.server import TorznabServer

from test_admin_auth import KEY, FakeSite


class OneResult(FakeSite):
    def search(self, query, categories, target_season, target_episode):
        return [TorznabResult(title="T", guid="g", link="l", pub_date="d", size=1, category=5000,
                              download_params={"topic_id": "42"})]

    def download(self, topic_id, infohash, season, episode):
        return "magnet:?xt=urn:btih:abc"


@pytest.fixture
def client():
    server = TorznabServer(api_key=KEY)
    server.register_site("fake", OneResult())
    return server.app.test_client()


@pytest.mark.parametrize("path", ["/fake/download?topic_id=42", "/fake/thread/42", "/fake/debug-search?q=x"])
def test_routes_need_the_key(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path + "&apikey=wrong" if "?" in path else path + "?apikey=wrong").status_code == 401


def test_download_with_key_redirects(client):
    r = client.get(f"/fake/download?topic_id=42&apikey={KEY}")
    assert r.status_code == 302 and r.headers["Location"].startswith("magnet:")


def test_search_links_carry_the_key(client):
    xml = client.get(f"/fake/api?t=search&q=x&apikey={KEY}").get_data(as_text=True)
    assert f"/fake/download?topic_id=42&amp;apikey={KEY}" in xml


def test_trusted_network_containing_the_proxy_is_refused():
    with pytest.raises(ValueError, match="contains the trusted proxy"):
        parse_admin_trust("172.16.0.0/12", "172.22.0.1", "s3cret-from-traefik-0123")


def test_secrets_stay_out_of_repr():
    r = repr(Config(password="pw-xyz", api_key="key-xyz", admin_trusted_proxy_secret="sec-xyz"))
    assert "xyz" not in r


def test_sent_secret_is_stripped():
    secret = "s3cret-from-traefik-0123"
    ip = client_ip("172.22.0.1", "192.168.1.2", parse_addresses("172.22.0.1", "P"), f" {secret} ", secret)
    assert str(ip) == "192.168.1.2"
