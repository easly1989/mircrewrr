"""#79: find existing MIRCrew requests (sections 53, 54, 57), read-only, against saved forum pages."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config import Config
from sites.mircrew import site as site_module
from sites.mircrew.site import MircrewSite
from torznab.server import BaseSite, SiteUnavailable, TorznabServer

FIXTURES = Path(__file__).parent / "fixtures" / "requests"
KEY = "test-key"


def page(name, status=200):
    text = (FIXTURES / name).read_text(encoding="utf-8") if name else ""
    return SimpleNamespace(status_code=status, text=text)


class SavedForum:
    """Answers with the saved pages and records every request."""

    def __init__(self, gone=()):
        self.requests = []
        self.gone = set(gone)

    def get(self, url, params=None, timeout=None):
        params = params or {}
        self.requests.append((url.rsplit("/", 1)[-1], params))
        if url.endswith("search.php"):
            return page("search_53_57.html")
        topic, post = params.get("t"), params.get("p")
        if topic in self.gone:
            return page(None, status=404)
        if topic == "207721":
            return page("topic_207721.html")
        if topic == "100001":
            return page("topic_100001.html")
        if post == "800003":
            return page("topic_100001_lastpage.html")
        raise AssertionError(f"unexpected request {url} {params}")


@pytest.fixture
def make_site(tmp_path, monkeypatch):
    monkeypatch.setattr(site_module.time, "sleep", lambda _s: None)

    def _make(forum):
        session = MagicMock()
        session.login_gen = 1
        session.last_login_secs = None
        session.login_blocked = None
        session.ensure_logged_in.return_value = forum
        return MircrewSite(session=session, config=Config(base_url="https://forum.test", data_dir=tmp_path))

    return _make


def by_id(result):
    return {t["topic_id"]: t for t in result["topics"]}


def test_open_request_comes_back_with_its_reply_data_and_the_imdb_match(make_site):
    forum = SavedForum()
    result = make_site(forum).find_requests(53, ["I Swear"], "tt31514146")
    topic = by_id(result)["207721"]
    assert result["topics"][0] is topic  # the match comes first
    assert topic["match"] is True and topic["imdb"] == "tt31514146"
    assert topic["section"] == 53 and topic["title"] == "I Swear (2025) ENG sub ita"
    assert topic["author"] == "user2" and topic["date"] == "2026-02-25T10:35:37+00:00"
    assert topic["replies"] == 4
    assert topic["last_reply"]["author"] == "user1" and "Ancora disponibile" in topic["last_reply"]["text"]
    assert topic["url"] == "https://forum.test/viewtopic.php?t=207721"


def test_satisfied_request_in_57_is_returned_with_its_section(make_site):
    forum = SavedForum()
    result = make_site(forum).find_requests(53, ["Il miglio verde"], "tt0120689")
    topic = by_id(result)["100001"]
    assert topic["section"] == 57 and topic["match"] is True
    assert topic["replies"] == 2 and topic["last_reply"]["author"] == "user4"
    assert "Richiesta soddisfatta" in topic["last_reply"]["text"]  # read from the topic's later page
    assert "100002" not in by_id(result)  # section 54 is not asked for
    search = forum.requests[0]
    assert search[0] == "search.php" and search[1]["fid[]"] == ["53", "57"] and search[1]["sf"] == "titleonly"


def test_no_match_without_the_requested_imdb_id(make_site):
    result = make_site(SavedForum()).find_requests(53, ["I Swear"], "tt9999999")
    assert not any(t["match"] for t in result["topics"])


def test_nothing_in_the_answer_carries_a_session_id(make_site):
    text = json.dumps(make_site(SavedForum()).find_requests(53, ["I Swear", "Il miglio verde"], None))
    assert "SECRETSID" not in text and "sid=" not in text


def test_only_reads_pages_and_spaces_them(make_site, monkeypatch):
    pauses = []
    monkeypatch.setattr(site_module.time, "sleep", pauses.append)
    forum = SavedForum()
    make_site(forum).find_requests(53, ["I Swear"], None)
    assert [name for name, _ in forum.requests] == ["search.php", "viewtopic.php", "viewtopic.php", "viewtopic.php"]
    assert len(pauses) == len(forum.requests) - 1  # a pause before every page but the first


def test_a_topic_that_vanished_is_skipped(make_site):
    result = make_site(SavedForum(gone={"100001"})).find_requests(53, ["Il miglio verde"], None)
    assert "100001" not in by_id(result)


@pytest.mark.parametrize("section,titles,imdb", [(99, ["x"], None), (53, [], None), (53, ["  "], None), (53, ["x"], "12345")])
def test_bad_input_is_refused_before_any_request(make_site, section, titles, imdb):
    forum = SavedForum()
    with pytest.raises(ValueError):
        make_site(forum).find_requests(section, titles, imdb)
    assert not forum.requests


class StubSite(BaseSite):
    def __init__(self, error=None):
        self.error = error

    def find_requests(self, section, titles, imdb):
        if self.error:
            raise self.error
        return {"section": section, "titles": titles, "imdb": imdb, "topics": []}

    def search(self, *a):
        return []

    def download(self, *a):
        return None

    def get_capabilities_xml(self):
        return "<caps/>"

    def health_info(self):
        return {"status": "ok"}


def client_for(site):
    server = TorznabServer(api_key=KEY)
    server.register_site("mircrew", site)
    return server.app.test_client()


def test_route_needs_the_api_key_and_passes_repeated_titles():
    client = client_for(StubSite())
    assert client.get("/mircrew/requests?section=53&title=a").status_code != 200
    r = client.get(f"/mircrew/requests?section=53&title=A&title=B&imdb=tt1234567&apikey={KEY}")
    assert r.status_code == 200 and r.get_json() == {"section": 53, "titles": ["A", "B"], "imdb": "tt1234567", "topics": []}


def test_route_answers_400_for_bad_input_and_503_when_the_site_is_down():
    assert client_for(StubSite()).get(f"/mircrew/requests?section=abc&apikey={KEY}").status_code == 400
    assert client_for(StubSite(ValueError("bad"))).get(f"/mircrew/requests?section=53&apikey={KEY}").status_code == 400
    down = client_for(StubSite(SiteUnavailable("login blocked")))
    r = down.get(f"/mircrew/requests?section=53&title=a&apikey={KEY}")
    assert r.status_code == 503 and "login blocked" in r.get_json()["error"]
