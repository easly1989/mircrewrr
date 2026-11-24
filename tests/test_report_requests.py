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
    assert topic["first_post_id"] == "800001"  # from page 1, not the last page that was also read
    assert topic["last_reply"]["author"] == "user4" and "Richiesta soddisfatta" in topic["last_reply"]["text"]
    assert forum.requests == [{"t": "100001"}, {"t": "100001", "start": 2}]  # the last reply is on page 2


def report_as(make_site, username, topic_id, forum=None):
    site = make_site(forum or SavedForum())
    site.session.username = username
    return by_id(site.report_requests([topic_id]))[topic_id]


def test_the_accounts_last_reply_and_newest_post_are_marked_mine(make_site):
    topic = report_as(make_site, "USER4", "100001")  # the name is compared case-insensitively
    assert topic["last_reply"]["mine"] is True
    assert topic["mine_last"] == {"post_id": "800003", "date": "2025-03-12T18:30:00+00:00"}  # from the last page


def test_mine_last_is_the_newest_post_of_the_account_not_the_last_reply(make_site):
    topic = report_as(make_site, "user6", "207721")  # wrote the second post, user1 wrote the last
    assert topic["last_reply"]["mine"] is False
    assert topic["mine_last"] == {"post_id": "603672", "date": "2026-02-26T08:10:00+00:00"}
    assert report_as(make_site, "user3", "100001")["mine_last"]["post_id"] == "800001"  # page 1 counts too


def test_mine_last_takes_the_newest_of_several_posts_by_the_account(make_site):
    last_page = saved("topic_100001_lastpage.html").text.replace(">user4<", ">user3<")  # user3 also wrote post 1
    forum = SavedForum({("100001", 2): SimpleNamespace(status_code=200, text=last_page)})
    assert report_as(make_site, "user3", "100001", forum)["mine_last"]["post_id"] == "800003"


def long_topic(by_page, **extra):
    """Topic 100001 with 4 pages (starts 2, 4, 6) of 2 posts: `by_page` = {start: [(post_id, author)]}."""
    import re
    links = "".join(f'<li><a href="./viewtopic.php?t=100001&amp;start={s}">x</a></li>' for s in (2, 4, 6))
    first = re.sub(r'<div class="pagination">.*?</div>',
                   f'<div class="pagination">7 messaggi &bull; Pagina 1 di 4 <ul>{links}</ul></div>',
                   saved("topic_100001.html").text, flags=re.S)
    pages = {("100001", None): SimpleNamespace(status_code=200, text=first)}
    for start, posts in by_page.items():
        body = "".join(NEW_POST.replace("716000", pid).replace(">user8<", f">{who}<") for pid, who in posts)
        pages[("100001", start)] = SimpleNamespace(status_code=200, text=f"<html><body>{body}</body></html>")
    return SavedForum({**pages, **extra})


def test_mine_last_walks_back_from_the_last_page_and_stops_at_the_first_hit(make_site):
    forum = long_topic({6: [("1", "a"), ("2", "b")], 4: [("3", "user9"), ("4", "c")], 2: [("5", "user9")]})
    topic = report_as(make_site, "user9", "100001", forum)
    assert topic["mine_last"]["post_id"] == "3"  # the newest of the account, on a middle page
    assert [r.get("start") for r in forum.requests] == [None, 6, 4]  # page 2 was not needed


def test_without_posts_in_between_mine_last_is_the_one_on_page_1(make_site):
    forum = long_topic({6: [("1", "a")], 4: [("2", "b")], 2: [("3", "c")]})
    assert report_as(make_site, "user3", "100001", forum)["mine_last"]["post_id"] == "800001"
    assert len(forum.requests) == 4  # every page was read to know it


def test_an_unknown_account_does_not_walk_back_through_pages(make_site):
    forum = long_topic({6: [("1", "a")], 4: [("2", "b")], 2: [("3", "c")]})
    topic = report_as(make_site, None, "100001", forum)
    assert topic["mine_last"] is None and [r.get("start") for r in forum.requests] == [None, 6]


def test_mine_last_is_left_out_when_the_page_cap_stops_the_walk_back(make_site):
    forum = long_topic({6: [("1", "a")], 4: [("2", "b")], 2: [("3", "user3")]})
    site = make_site(forum)
    site.session.username = "user3"
    site.MAX_MIDDLE_PAGES = 1  # only page 4 is read: page 2 could hold a newer post
    topic = by_id(site.report_requests(["100001"]))["100001"]
    assert "mine_last" not in topic and [r.get("start") for r in forum.requests] == [None, 6, 4]


def test_the_middle_pages_of_a_whole_report_are_capped_and_the_topic_is_still_reported(make_site):
    forum = long_topic({6: [("1", "a")], 4: [("2", "b")], 2: [("3", "user3")]})
    site = make_site(forum)
    site.session.username = "user3"
    site.MAX_MIDDLE_TOTAL = 1  # one middle page for the whole report, the topic needs two
    topic = by_id(site.report_requests(["100001"]))["100001"]
    assert "mine_last" not in topic and topic["section"] == 57 and topic["last_reply"]["author"] == "a"


def test_a_spent_budget_in_the_walk_back_truncates_and_leaves_mine_last_out(make_site):
    def spend(params):
        if params.get("start") == 4:
            raise site_module.BudgetSpent("spent")

    forum = long_topic({6: [("1", "a")], 4: [("2", "b")]})
    forum.on_get = spend
    site = make_site(forum)
    site.session.username = "user3"
    report = site.report_requests(["100001"])
    assert report["truncated"] is True and "mine_last" not in by_id(report)["100001"]


def test_a_hit_on_the_last_page_reads_no_middle_page(make_site):
    forum = long_topic({6: [("1", "user9")], 4: [("2", "b")]})
    assert report_as(make_site, "user9", "100001", forum)["mine_last"]["post_id"] == "1"
    assert [r.get("start") for r in forum.requests] == [None, 6]


def test_mine_last_is_left_out_when_a_middle_page_fails(make_site):
    forum = long_topic({6: [("1", "a")], 2: [("3", "c")]})  # page 4 answers 404
    assert "mine_last" not in report_as(make_site, "user3", "100001", forum)


def test_mine_last_is_left_out_when_the_last_page_was_not_read(make_site):
    forum = SavedForum({("100001", 2): SimpleNamespace(status_code=404, text="")})
    topic = report_as(make_site, "user3", "100001", forum)  # the older post on page 1 would look like the newest
    assert "mine_last" not in topic and topic["last_reply"] is None


def test_mine_flags_are_false_and_null_for_other_accounts_or_an_unknown_one(make_site):
    for username in ("user99", None, ""):
        topic = report_as(make_site, username, "207721")
        assert topic["last_reply"]["mine"] is False and topic["mine_last"] is None


def test_an_open_request_reports_its_section_and_last_reply(make_site):
    forum = SavedForum()
    topic = by_id(make_site(forum).report_requests(["207721"]))["207721"]
    assert topic["section"] == 53 and topic["replies"] == 4
    assert topic["first_post_id"] == "603517"
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


def test_a_topic_the_account_cannot_read_does_not_sink_the_report(make_site):
    forum = SavedForum({("303", None): SimpleNamespace(status_code=403, text="")})
    report = make_site(forum).report_requests(["303", "207721"])
    assert by_id(report)["303"]["gone"] is False and "403" in by_id(report)["303"]["error"]
    assert by_id(report)["207721"]["section"] == 53


def test_a_message_page_is_not_reported_as_a_topic(make_site):
    message = SimpleNamespace(status_code=200, text="<html><head><title>Informazione</title></head>"
                                                    "<body><div id=\"message\"><p>Solo dopo 5 secondi.</p></div></body></html>")
    topic = by_id(make_site(SavedForum({("304", None): message})).report_requests(["304"]))["304"]
    assert topic["gone"] is False and topic["error"] == "not a topic page" and "title" not in topic


def test_without_the_message_count_the_reply_count_is_unknown_not_guessed(make_site):
    import re
    text = re.sub(r'<div class="pagination">.*?</div>', "", saved("topic_207721.html").text, flags=re.S)
    topic = by_id(make_site(SavedForum({("207721", None): SimpleNamespace(status_code=200, text=text)}))
                  .report_requests(["207721"]))["207721"]
    assert topic["replies"] is None and topic["last_reply"] is None and topic["section"] == 53


def test_the_italian_thousands_separator_in_the_message_count():
    from sites.mircrew import request_topics
    html = ('<h2 class="topic-title">x</h2><div class="pagination">1.234 messaggi &bull; Pagina 1 di 83</div>'
            '<div id="p1" class="post"><p class="author"><a class="username">u</a></p><div class="content">t</div></div>')
    overview = request_topics.parse_topic_overview(html, {"first_post": "div.post"})
    assert overview["total_posts"] == 1234


def test_a_failed_last_page_keeps_the_topic_without_its_last_reply(make_site):
    forum = SavedForum({("100001", 2): SimpleNamespace(status_code=404, text="")})
    topic = by_id(make_site(forum).report_requests(["100001"]))["100001"]
    assert topic["gone"] is False and topic["section"] == 57 and topic["replies"] == 2 and topic["last_reply"] is None


def test_the_budget_counts_from_arrival_not_from_the_free_slot(make_site, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(site_module.time, "monotonic", lambda: clock[0])
    site = make_site(SavedForum())

    class SlowSlots:
        def acquire(self, timeout=None):
            clock[0] += 60  # a minute in the queue
            return True

        def release(self):
            pass

    site._search_slots = SlowSlots()
    site.report_requests(["207721"])
    assert site.session.set_deadline.call_args_list[0].args == (1000.0 + site.SEARCH_BUDGET,)


def dying_at(topic_id, start=None):
    def on_get(params):
        if params["t"] == topic_id and params.get("start") == start:
            raise SiteUnavailable("login blocked after repeated refusals")
    return on_get


def test_a_session_that_dies_part_way_returns_the_topics_already_read(make_site):
    forum = SavedForum(on_get=dying_at("100001"))
    report = make_site(forum).report_requests(["207721", "100001"])
    assert list(by_id(report)) == ["207721"] and report["truncated"] is True


def test_a_session_that_dies_on_the_last_page_of_the_first_topic_keeps_that_topic(make_site):
    report = make_site(SavedForum(on_get=dying_at("100001", 2))).report_requests(["100001"])
    topic = by_id(report)["100001"]
    assert report["truncated"] is True and topic["section"] == 57
    assert topic["last_reply"] is None and "mine_last" not in topic


def test_a_session_that_is_dead_from_the_start_is_still_a_503(make_site):
    with pytest.raises(SiteUnavailable, match="login blocked"):
        make_site(SavedForum(on_get=dying_at("207721"))).report_requests(["207721", "100001"])


def test_the_slot_comes_back_after_an_error(make_site):
    site = make_site(SavedForum({("505", None): SimpleNamespace(status_code=200, text="<html></html>")}))
    site.session.ensure_logged_in.side_effect = SiteUnavailable("down")
    with pytest.raises(SiteUnavailable):
        site.report_requests(["505"])
    assert site._search_slots.acquire(blocking=False)
    site._search_slots.release()


def test_the_real_site_answers_through_the_route(make_site):
    server = TorznabServer(api_key=KEY)
    server.register_site("mircrew", make_site(SavedForum()))
    r = server.app.test_client().get(f"/mircrew/requests/report?topic_id=207721&topic_id=424242&apikey={KEY}")
    topics = {t["topic_id"]: t for t in r.get_json()["topics"]}
    assert r.status_code == 200 and topics["207721"]["section"] == 53 and topics["424242"]["gone"] is True


@pytest.mark.parametrize("ids", [[], [" "], ["12x"], ["٣"], ["1" * 11], [str(i) for i in range(26)]])
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
