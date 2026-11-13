"""An unreachable site answers with a Torznab error, not an empty feed, so Prowlarr can tell outage from no hits."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from config import Config
from sites.mircrew.site import MircrewSite
from torznab.server import SiteUnavailable, TorznabServer

from test_admin_auth import KEY, FakeSite


class DownSite(FakeSite):
    def search(self, query, categories, target_season, target_episode):
        raise SiteUnavailable("search returned HTTP 403 <cloudflare>")


def test_unreachable_site_answers_503_with_torznab_error():
    server = TorznabServer(api_key=KEY)
    server.register_site("down", DownSite())
    r = server.app.test_client().get(f"/down/api?t=search&q=x&apikey={KEY}")
    assert r.status_code == 503
    body = r.get_data(as_text=True)
    assert '<error code="900"' in body
    assert "HTTP 403 &lt;cloudflare&gt;" in body


def test_unreachable_site_debug_search_answers_503():
    server = TorznabServer(api_key=KEY)
    server.register_site("down", DownSite())
    r = server.app.test_client().get(f"/down/debug-search?q=x&apikey={KEY}")
    assert r.status_code == 503
    assert r.get_json() == {"query": "x", "error": "search returned HTTP 403 <cloudflare>"}


def test_reachable_site_without_hits_is_still_an_empty_feed():
    server = TorznabServer(api_key=KEY)
    server.register_site("fake", FakeSite())
    r = server.app.test_client().get(f"/fake/api?t=search&q=x&apikey={KEY}")
    assert r.status_code == 200 and "<channel>" in r.get_data(as_text=True)


def _site(tmp_path, get):
    session = MagicMock()
    session.ensure_logged_in.return_value = SimpleNamespace(get=get)
    return MircrewSite(session=session, config=Config(base_url="https://forum.test", data_dir=tmp_path))


@pytest.mark.parametrize("response", [
    SimpleNamespace(status_code=403, text="Just a moment..."),
    SimpleNamespace(status_code=200, text='<a href="./ucp.php?mode=login">Login</a>'),
])
def test_blocked_or_logged_out_search_raises(tmp_path, response):
    site = _site(tmp_path, lambda *a, **kw: response)
    with pytest.raises(SiteUnavailable):
        site.search("matrix", None, None, None)


def test_network_error_raises(tmp_path):
    def get(*a, **kw):
        raise requests.ConnectionError("byparr down")
    with pytest.raises(SiteUnavailable):
        _site(tmp_path, get).search("matrix", None, None, None)


def test_logged_out_page_still_forces_relogin(tmp_path):
    site = _site(tmp_path, lambda *a, **kw: SimpleNamespace(status_code=200, text="./ucp.php?mode=login"))
    with pytest.raises(SiteUnavailable):
        site.search("matrix", None, None, None)
    assert site.session.session_valid is False
