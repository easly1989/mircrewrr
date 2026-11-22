"""#84: expanding thanked threads inside a search must not hide a dead session behind a partial 200 feed."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from config import Config
from sites.mircrew import site as site_module
from sites.mircrew.site import MircrewSite
from torznab.server import SiteUnavailable

PADDING = "<div>" + "x" * 7000 + "</div>"  # real pages carry the login or logout link far from the top
MAGNET = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=Film.ITA.1080p"
LIVE_THREAD = SimpleNamespace(status_code=200, text=PADDING + '<a href="./ucp.php?mode=logout">Logout</a>'
                              f'<div class="post"><div class="content"><a href="{MAGNET}">m</a></div></div>')
DEAD = SimpleNamespace(status_code=200, text=PADDING + '<a href="./ucp.php?mode=login">Login</a>')
GONE = SimpleNamespace(status_code=404, text="")


def row(topic_id, title):
    return (f'<li class="row"><a class="topictitle" href="./viewtopic.php?f=26&t={topic_id}">{title}</a>'
            f'<a href="./viewforum.php?f=26">forum</a></li>')


SEARCH = SimpleNamespace(status_code=200, text=PADDING + '<a href="./ucp.php?mode=logout">Logout</a><ul>'
                         + row(1000, "Film ITA (2020)") + row(1001, "Film ITA (2021)") + "</ul>")


class Forum:
    """Search page, then thread pages by topic id; `threads` maps a topic id to the answers in turn."""

    def __init__(self, threads, on_thread=None):
        self.threads = {k: list(v) for k, v in threads.items()}
        self.on_thread = on_thread
        self.thread_requests = []

    def get(self, url, params=None, timeout=None):
        if url.endswith("search.php"):
            return SEARCH
        topic = url.split("t=")[-1]
        self.thread_requests.append(topic)
        if self.on_thread:
            self.on_thread(topic)
        answers = self.threads[topic]
        return answers.pop(0) if len(answers) > 1 else answers[0]


@pytest.fixture
def make_site(tmp_path, monkeypatch):
    monkeypatch.setattr(site_module.time, "sleep", lambda _s: None)

    def _make(forum):
        session = MagicMock()
        session.login_gen = 1
        session.session_valid = True
        session.last_login_secs = None
        session.login_blocked = None
        session.invalidate.return_value = True
        session.ensure_logged_in.return_value = forum
        site = MircrewSite(session=session, config=Config(base_url="https://forum.test", data_dir=tmp_path))
        site.thanks_cache.update({"1000", "1001"})  # both threads are already thanked: the search expands them
        return site

    return _make


def test_a_dead_session_met_twice_while_expanding_makes_the_search_unavailable(make_site):
    site = make_site(Forum({"1000": [DEAD], "1001": [LIVE_THREAD]}))
    with pytest.raises(SiteUnavailable):
        site.search("Film", None, None, None)  # not a 200 feed without the thread's magnets


@pytest.mark.parametrize("broken", [GONE, SimpleNamespace(status_code=500, text="")])
def test_a_deleted_or_broken_thread_is_left_out_and_the_rest_of_the_feed_comes_back(make_site, broken):
    results = make_site(Forum({"1000": [broken], "1001": [LIVE_THREAD]})).search("Film", None, None, None)
    # the broken thread stays a plain result (as when an expansion finds no magnet), the other one expands
    assert sorted(r.title for r in results) == ["Film ITA (2020)", "Film.ITA.1080p"]


def test_a_timeout_while_expanding_makes_the_search_unavailable(make_site):
    class Slow(Forum):
        def get(self, url, params=None, timeout=None):
            if url.endswith("search.php"):
                return super().get(url, params, timeout)
            raise requests.ReadTimeout("slow")

    with pytest.raises(SiteUnavailable, match="thread request failed"):
        make_site(Slow({})).search("Film", None, None, None)


def test_a_budget_spent_before_any_thread_is_expanded_is_unavailable(make_site, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(site_module.time, "monotonic", lambda: clock[0])

    class Late(Forum):
        def get(self, url, params=None, timeout=None):
            clock[0] += 95  # the search page itself used the whole budget
            return super().get(url, params, timeout)

    with pytest.raises(SiteUnavailable, match="budget"):
        make_site(Late({"1000": [LIVE_THREAD], "1001": [LIVE_THREAD]})).search("Film", None, None, None)


def test_a_spent_budget_while_expanding_returns_the_results_so_far(make_site, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(site_module.time, "monotonic", lambda: clock[0])
    forum = Forum({"1000": [LIVE_THREAD], "1001": [LIVE_THREAD]},
                  on_thread=lambda topic: clock.__setitem__(0, clock[0] + 95))
    results = make_site(forum).search("Film", None, None, None)
    assert forum.thread_requests == ["1000"]  # no time left for the second thread
    assert [r.title for r in results] == ["Film.ITA.1080p"]


def test_the_thread_debug_endpoint_still_reports_a_failed_load(make_site):
    site = make_site(Forum({"1000": [DEAD]}))
    assert site.debug_thread("1000")["error"].startswith("Failed to load thread")
