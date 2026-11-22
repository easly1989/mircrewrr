"""#81: report on tracked MIRCrew request topics (section now, title, replies, last reply), read-only."""

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

NEW_POST = ('<div id="p716000" class="post has-profile bg1"> <div class="inner"> <div class="postbody">'
            ' <div id="post_content716000"> <p class="author post-number"> <a href="">#716000</a> </p>'
            ' <p class="author"> <span class="responsive-hide">da <strong>'
            '<a href="./memberlist.php?mode=viewprofile&amp;u=30002" class="username">user8</a></strong> &raquo; </span>'
            '<time datetime="2026-11-22T09:00:00+00:00">22/11/2026, 10:00</time> </p>'
            ' <div class="content">Aggiornamento: trovato su una piattaforma.</div> </div> </div> </div> </div>')


def saved(name):
    return SimpleNamespace(status_code=200, text=(FIXTURES / name).read_text(encoding="utf-8"))


def with_new_reply():
    text = saved("topic_207721.html").text.replace("5 messaggi", "6 messaggi").replace("</body>", NEW_POST + "</body>")
    return SimpleNamespace(status_code=200, text=text)


class SavedForum:
    """Answers viewtopic.php with the saved pages and records every request."""

    def __init__(self, pages=None, on_get=None):
        self.requests = []
        self.pages = pages or {}
        self.on_get = on_get

    def get(self, url, params=None, timeout=None):
        params = params or {}
        self.requests.append(params)
        if self.on_get:
            self.on_get(params)
        # A topic page is read by topic id and page offset and nothing else: no Thanks, no posting.
        assert url.endswith("viewtopic.php") and set(params) <= {"t", "start"}, f"unexpected request {url} {params}"
        key = (params["t"], params.get("start"))
        if key in self.pages:
            return self.pages[key]
        known = {("207721", None): "topic_207721.html", ("100001", None): "topic_100001.html",
                 ("100001", 2): "topic_100001_lastpage.html"}
        if key in known:
            return saved(known[key])
        return SimpleNamespace(status_code=404, text="")


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


def by_id(report):
    return {t["topic_id"]: t for t in report["topics"]}


def test_a_request_moved_to_57_reports_section_57_and_its_last_reply_from_the_last_page(make_site):
    forum = SavedForum()
    topic = by_id(make_site(forum).report_requests(["100001"]))["100001"]
    assert topic["section"] == 57 and topic["gone"] is False
    assert topic["title"] == "[SODDISFATTA] Il miglio verde (1999)"
    assert topic["replies"] == 2
    assert topic["last_reply"]["author"] == "user4" and "Richiesta soddisfatta" in topic["last_reply"]["text"]
    assert forum.requests == [{"t": "100001"}, {"t": "100001", "start": 2}]  # the last reply is on page 2


def test_an_open_request_reports_its_section_and_last_reply(make_site):
    forum = SavedForum()
    topic = by_id(make_site(forum).report_requests(["207721"]))["207721"]
    assert topic["section"] == 53 and topic["replies"] == 4
    assert topic["last_reply"]["author"] == "user1" and "Ancora disponibile" in topic["last_reply"]["text"]
    assert len(forum.requests) == 1  # one page for a topic that fits on one page


def test_a_new_reply_shows_in_the_count_and_the_last_reply(make_site):
    topic = by_id(make_site(SavedForum({("207721", None): with_new_reply()})).report_requests(["207721"]))["207721"]
    assert topic["replies"] == 5
    assert topic["last_reply"]["author"] == "user8" and "Aggiornamento" in topic["last_reply"]["text"]


def test_a_deleted_topic_is_reported_as_gone_and_the_others_still_are(make_site):
    report = make_site(SavedForum()).report_requests(["424242", "207721"])
    assert by_id(report)["424242"] == {"topic_id": "424242", "gone": True}
    assert by_id(report)["207721"]["section"] == 53


def test_nothing_in_the_report_carries_a_session_id(make_site):
    assert "SECRETSID" not in json.dumps(make_site(SavedForum()).report_requests(["207721", "100001"]))


def test_pages_are_spaced_one_second_apart(make_site, monkeypatch):
    pauses = []
    monkeypatch.setattr(site_module.time, "sleep", pauses.append)
    forum = SavedForum()
    make_site(forum).report_requests(["207721", "100001"])
    assert len(forum.requests) == 3 and pauses == [1.0, 1.0]


@pytest.mark.parametrize("ids", [[], [" "], ["12x"], [str(i) for i in range(26)]])
def test_bad_input_is_refused_before_any_request(make_site, ids):
    forum = SavedForum()
    with pytest.raises(ValueError):
        make_site(forum).report_requests(ids)
    assert not forum.requests


def test_a_spent_budget_returns_the_topics_read_so_far_and_says_so(make_site, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(site_module.time, "monotonic", lambda: clock[0])
    forum = SavedForum(on_get=lambda params: clock.__setitem__(0, clock[0] + 95) if params["t"] == "207721" else None)
    report = make_site(forum).report_requests(["207721", "100001"])
    assert list(by_id(report)) == ["207721"] and report["truncated"] is True


class StubSite(BaseSite):
    def __init__(self, error=None):
        self.error = error

    def report_requests(self, topic_ids):
        if self.error:
            raise self.error
        return {"truncated": False, "topics": [{"topic_id": t} for t in topic_ids]}

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


def test_route_needs_the_api_key_and_passes_repeated_ids():
    client = client_for(StubSite())
    assert client.get("/mircrew/requests/report?topic_id=1").status_code == 401
    r = client.get(f"/mircrew/requests/report?topic_id=1&topic_id=2&apikey={KEY}")
    assert r.status_code == 200 and [t["topic_id"] for t in r.get_json()["topics"]] == ["1", "2"]


def test_route_answers_400_for_bad_input_and_503_when_the_site_is_down():
    assert client_for(StubSite(ValueError("bad"))).get(f"/mircrew/requests/report?apikey={KEY}").status_code == 400
    down = client_for(StubSite(SiteUnavailable("login blocked")))
    r = down.get(f"/mircrew/requests/report?topic_id=1&apikey={KEY}")
    assert r.status_code == 503 and "login blocked" in r.get_json()["error"]
