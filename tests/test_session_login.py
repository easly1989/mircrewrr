"""Login race (#75): one login at a time, on a private session copy that replaces the shared one only on success."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config import Config
from session.base import BaseSession
from sites.mircrew.site import MircrewSite, login_failure_reason
from torznab.server import SiteUnavailable


class FakeSession(BaseSession):
    def __init__(self, tmp_path):
        super().__init__("https://forum.test", "u", "p", tmp_path / "cookies.json")
        self.logins = 0
        self.login_ok = True
        self.in_login = threading.Event()
        self.release_login = threading.Event()
        self.release_login.set()

    def get(self, url, **kwargs):
        return SimpleNamespace(text="logged out", status_code=200)

    def _check_logged_in(self, html):
        return "mode=logout" in html

    def _do_login(self):
        self.logins += 1
        self.in_login.set()
        self.release_login.wait(5)
        self.http.cookies.set("sid" if self.login_ok else "junk", "x")
        return self.login_ok


def _cookie_names(session):
    return {c.name for c in session.http.cookies}


class CountingLock:
    """Wraps the login lock and signals once `n` threads have reached acquire()."""

    def __init__(self, lock, n):
        self._lock, self._n, self._count = lock, n, 0
        self._guard = threading.Lock()
        self.all_arrived = threading.Event()

    def acquire(self, *args, **kwargs):
        with self._guard:
            self._count += 1
            if self._count == self._n:
                self.all_arrived.set()
        return self._lock.acquire(*args, **kwargs)

    def release(self):
        self._lock.release()


def test_concurrent_callers_log_in_once(tmp_path):
    s = FakeSession(tmp_path)
    s.release_login.clear()
    s._login_lock = lock = CountingLock(s._login_lock, 8)
    threads = [threading.Thread(target=s.ensure_logged_in) for _ in range(8)]
    for t in threads:
        t.start()
    assert s.in_login.wait(5)
    assert lock.all_arrived.wait(5)  # the other seven are queued on the lock
    s.release_login.set()
    for t in threads:
        t.join(5)
    assert s.logins == 1
    assert s.session_valid and "sid" in _cookie_names(s)


def test_failed_login_leaves_the_shared_session_untouched(tmp_path):
    s = FakeSession(tmp_path)
    s.login_ok = False
    shared = s.http
    s.ensure_logged_in()
    assert s.http is shared and "junk" not in _cookie_names(s)
    assert s.session_valid is False


def test_requests_in_flight_keep_the_old_session_while_logging_in(tmp_path):
    s = FakeSession(tmp_path)
    s.release_login.clear()
    old = s.http
    t = threading.Thread(target=s.ensure_logged_in)
    t.start()
    assert s.in_login.wait(5)
    assert s.http is old and "sid" not in _cookie_names(s)  # another thread still sees the shared session
    s.release_login.set()
    t.join(5)
    assert s.http is not old and "sid" in _cookie_names(s)


def test_cookies_set_on_the_shared_session_during_a_login_survive_the_swap(tmp_path):
    class Racing(FakeSession):
        def _do_login(self):
            self._http.cookies.set("late", "1")  # a request in flight, answered mid-login
            self._http.cookies.set("sid", "old")
            return super()._do_login()

    s = Racing(tmp_path)
    s.ensure_logged_in()
    assert {c.name: c.value for c in s.http.cookies} == {"late": "1", "sid": "x"}  # new sid wins


def test_invalidate_ignores_a_session_renewed_since_the_request_started(tmp_path):
    s = FakeSession(tmp_path)
    seen = s.login_gen
    s.ensure_logged_in()
    assert s.invalidate(seen) is False and s.session_valid is True
    assert s.invalidate(s.login_gen) is True and s.session_valid is False
    s.ensure_logged_in()  # a dead session really logs in again
    assert s.logins == 2 and s.session_valid is True


def test_login_failure_reason_reports_phpbb_error_without_form_fields():
    html = ('<html><head><title>MIRCrew - Login</title></head><body>'
            '<div class="error">Hai superato il numero massimo\n di tentativi.</div>'
            '<input type="hidden" name="form_token" value="SECRET"></body></html>')
    reason = login_failure_reason(html)
    assert "MIRCrew - Login" in reason and "Hai superato il numero massimo di tentativi." in reason
    assert "SECRET" not in reason
    assert "no error element" in login_failure_reason("<html></html>")


DEAD = SimpleNamespace(status_code=200, text='<a href="./ucp.php?mode=login">Login</a>')
LIVE = SimpleNamespace(status_code=200, text="<html><body><ul></ul></body></html>")


def _site(tmp_path, responses):
    calls = []

    def get(*a, **kw):
        calls.append(1)
        return responses[min(len(calls), len(responses)) - 1]

    session = MagicMock()
    session.login_gen = 7
    session.last_login_secs = None
    session.ensure_logged_in.return_value = SimpleNamespace(get=get)
    site = MircrewSite(session=session, config=Config(base_url="https://forum.test", data_dir=tmp_path))
    return site, calls


def test_logged_out_page_logs_in_again_and_retries_once(tmp_path):
    site, calls = _site(tmp_path, [DEAD, LIVE])
    assert site.search("matrix", None, None, None) == []
    assert len(calls) == 2
    site.session.invalidate.assert_called_once_with(7)


def test_second_logged_out_page_raises(tmp_path):
    site, calls = _site(tmp_path, [DEAD, DEAD])
    with pytest.raises(SiteUnavailable):
        site.search("matrix", None, None, None)
    assert len(calls) == 2


def test_no_retry_when_the_budget_left_cannot_cover_a_new_login(tmp_path):
    site, calls = _site(tmp_path, [DEAD, LIVE])
    site.SEARCH_BUDGET = 5
    site.session.last_login_secs = 10  # the last login took longer than what is left
    with pytest.raises(SiteUnavailable, match="too little of the budget"):
        site.search("matrix", None, None, None)
    assert len(calls) == 1 and site.session.ensure_logged_in.call_count == 1


def test_search_with_an_empty_budget_makes_no_request(tmp_path):
    site, calls = _site(tmp_path, [LIVE])
    site.SEARCH_BUDGET = 0
    with pytest.raises(SiteUnavailable, match="budget"):
        site.search("matrix", None, None, None)
    assert not calls


PADDING = "<div>" + "x" * 7000 + "</div>"  # real phpBB pages carry the login/logout link 7-12 KB in


def test_logged_out_page_is_detected_when_the_login_link_is_deep_in_the_page(tmp_path):
    deep_dead = SimpleNamespace(status_code=200, text=PADDING + '<a href="./ucp.php?mode=login">Login</a>')
    site, calls = _site(tmp_path, [deep_dead, LIVE])
    site.search("matrix", None, None, None)
    assert len(calls) == 2  # logged out: logs in again and retries


def test_logged_in_page_with_a_deep_logout_link_is_not_retried(tmp_path):
    deep_live = SimpleNamespace(status_code=200, text=PADDING + '<a href="./ucp.php?mode=logout">Logout</a><ul></ul>')
    site, calls = _site(tmp_path, [deep_live])
    site.search("matrix", None, None, None)
    assert len(calls) == 1


def test_failed_login_is_not_repeated_by_every_waiting_thread(tmp_path):
    s = FakeSession(tmp_path)
    s.login_ok = False
    s.release_login.clear()
    s._login_lock = lock = CountingLock(s._login_lock, 8)
    threads = [threading.Thread(target=s.ensure_logged_in) for _ in range(8)]
    for t in threads:
        t.start()
    assert s.in_login.wait(5)
    assert lock.all_arrived.wait(5)
    s.release_login.set()
    for t in threads:
        t.join(5)
    assert s.logins == 1  # phpBB counts every failed attempt


class SearchSession(FakeSession):
    """get() answers search.php with the pages the test queued; other pages look logged out."""

    def __init__(self, tmp_path, pages):
        super().__init__(tmp_path)
        self.pages = list(pages)
        self.search_gets = 0

    def get(self, url, **kwargs):
        if "search.php" not in url:
            return super().get(url, **kwargs)
        self.search_gets += 1
        return self.pages.pop(0)


class RefusedSession(FakeSession):
    """The forum answers every login with a refusal."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.login_ok = False

    def _do_login(self):
        self.login_refusal = "page title 'Login', error 'wrong password'"
        return super()._do_login()


def test_repeated_refusals_stop_login_attempts_until_retry_login(tmp_path):
    s = RefusedSession(tmp_path)
    for _ in range(5):
        s.ensure_logged_in()
    assert s.logins == 2 and "wrong password" in s.login_blocked  # LOGIN_MAX_REFUSALS, then nothing
    s.login_ok = True
    assert s.retry_login() is True
    assert s.logins == 3 and s.login_blocked is None and s.session_valid


def test_zero_max_refusals_turns_the_block_off(tmp_path, monkeypatch):
    monkeypatch.setattr("session.base.LOGIN_MAX_REFUSALS", 0)
    s = RefusedSession(tmp_path)
    for _ in range(5):
        s.ensure_logged_in()
    assert s.logins == 5 and s.login_blocked is None


def test_failures_that_never_reached_the_site_do_not_block_logins(tmp_path):
    s = FakeSession(tmp_path)
    s.login_ok = False  # no login_refusal: network or Cloudflare trouble
    for _ in range(5):
        s.ensure_logged_in()
    assert s.logins == 5 and s.login_blocked is None


def test_blocked_login_answers_unavailable_without_touching_the_forum(tmp_path):
    s = SearchSession(tmp_path, [])
    s.login_blocked, s.session_valid = "wrong password", False
    with pytest.raises(SiteUnavailable, match="login blocked"):
        _real_site(tmp_path, s).search("matrix", None, None, None)
    assert s.logins == 0 and s.search_gets == 0


def _real_site(tmp_path, session):
    return MircrewSite(session=session, config=Config(base_url="https://forum.test", data_dir=tmp_path))


def test_search_with_a_failing_login_makes_one_login_attempt(tmp_path):
    s = SearchSession(tmp_path, [])
    s.login_ok = False
    site = _real_site(tmp_path, s)
    for _ in range(2):
        s.pages = [DEAD]
        with pytest.raises(SiteUnavailable):
            site.search("matrix", None, None, None)
    assert s.logins == 2 and s.search_gets == 2  # one attempt per search, no retry on top


def test_search_retries_after_a_valid_session_dies_on_the_server(tmp_path):
    s = SearchSession(tmp_path, [DEAD, LIVE])
    s.session_valid, s.last_login = True, time.time()  # looks valid, but the forum dropped it
    assert _real_site(tmp_path, s).search("matrix", None, None, None) == []
    assert s.logins == 1 and s.search_gets == 2 and s.session_valid


MAGNET = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=Show"
LIVE_THREAD = SimpleNamespace(status_code=200, text=PADDING + '<a href="./ucp.php?mode=logout">Logout</a>'
                              f'<div class="post"><div class="content"><a href="{MAGNET}">m</a></div></div>')
DEAD_THREAD = SimpleNamespace(status_code=200, text=PADDING + '<a href="./ucp.php?mode=login">Login</a>')


class ThreadSession(FakeSession):
    def __init__(self, tmp_path, pages):
        super().__init__(tmp_path)
        self.pages = list(pages)

    def get(self, url, **kwargs):
        return self.pages.pop(0) if "viewtopic" in url else super().get(url, **kwargs)


def test_download_logs_in_again_when_the_thread_page_shows_a_dead_session(tmp_path):
    s = ThreadSession(tmp_path, [DEAD_THREAD, LIVE_THREAD])
    s.session_valid, s.last_login = True, time.time()  # looks valid, but the forum dropped it
    assert _real_site(tmp_path, s).download("123", None, None, None) == MAGNET
    assert s.logins == 1 and not s.pages


UNTHANKED = SimpleNamespace(status_code=200, text=PADDING + '<a href="./ucp.php?mode=logout">Logout</a>'
                            '<div class="post"><div class="content">hidden</div>'
                            '<a href="./posting.php?mode=quote&f=1&p=55">q</a>'
                            '<a href="./viewtopic.php?t=123&p=55&thanks=55">Thanks</a></div>')


def test_thanks_that_hit_a_dead_session_is_not_cached_and_the_download_clicks_again(tmp_path, monkeypatch):
    monkeypatch.setattr("sites.mircrew.site.time.sleep", lambda _s: None)
    # First pass: thread, thanks click and refetch all meet a dead session; the page getter
    # logs in again and returns an unthanked page. Second pass: the click works.
    pages = [UNTHANKED, DEAD_THREAD, DEAD_THREAD, UNTHANKED, UNTHANKED, UNTHANKED, LIVE_THREAD]
    s = ThreadSession(tmp_path, pages)
    s.session_valid, s.last_login = True, time.time()
    site = _real_site(tmp_path, s)
    assert site.download("123", None, None, None) == MAGNET
    assert s.logins == 1 and not s.pages and "123" in site.thanks_cache


def test_topic_cached_as_thanked_without_visible_magnets_is_thanked_again(tmp_path, monkeypatch):
    monkeypatch.setattr("sites.mircrew.site.time.sleep", lambda _s: None)
    s = ThreadSession(tmp_path, [UNTHANKED, UNTHANKED, LIVE_THREAD])  # topic, thanks click, refetch
    s.session_valid, s.last_login = True, time.time()
    site = _real_site(tmp_path, s)
    site.thanks_cache.add("123")  # left by an older build that cached a click a dead session ate
    assert site.download("123", None, None, None) == MAGNET
    assert not s.pages and "123" in site.thanks_cache


def test_no_retry_when_a_login_that_began_after_the_request_already_failed(tmp_path):
    site, calls = _site(tmp_path, [DEAD])
    site.session.session_valid = True
    site.session.invalidate.return_value = False  # a newer login finished since this request started

    def get(*a, **kw):
        calls.append(1)
        site.session.session_valid = False  # and it failed
        return DEAD

    site.session.ensure_logged_in.return_value = SimpleNamespace(get=get)
    with pytest.raises(SiteUnavailable):
        site.search("matrix", None, None, None)
    assert len(calls) == 1 and site.session.ensure_logged_in.call_count == 1


def test_download_repeats_once_when_the_session_was_renewed_midway(tmp_path):
    from bs4 import BeautifulSoup
    s = FakeSession(tmp_path)
    site = _real_site(tmp_path, s)
    calls = []

    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            s.login_gen += 1  # a login ran while the thread was fetched: nothing visible yet
            return None, None, False
        return BeautifulSoup(LIVE_THREAD.text, "lxml"), LIVE_THREAD.text, False

    site._fetch_thread_and_click_thanks = fetch
    assert site.download("123", None, None, None) == MAGNET and len(calls) == 2


def test_cloudflare_refresh_is_published_only_when_it_succeeds(tmp_path):
    from session.byparr import ByparrSession
    s = ByparrSession("https://forum.test", "u", "p", tmp_path / "c.json", "http://byparr:8191")
    shared = s.http

    def refresh(ok):
        s.http.cookies.set("cf", "1")
        return ok

    assert s._refresh_cf(lambda: refresh(False)) is False
    assert s.http is shared and not list(shared.cookies)  # failed refresh: nothing leaks
    assert s._refresh_cf(lambda: refresh(True)) is True
    assert s.http is not shared and {c.name for c in s.http.cookies} == {"cf"}


BLOCKED = SimpleNamespace(status_code=403, text="Just a moment")


def _byparr(tmp_path, monkeypatch, pages):
    """A ByparrSession whose HTTP answers come from `pages` (last one repeats); returns it
    with the list of URLs it sent to Byparr."""
    import requests
    from session.byparr import ByparrSession
    s = ByparrSession("https://forum.test", "u", "p", tmp_path / "c.json", "http://byparr:8191")
    queue = list(pages)
    monkeypatch.setattr(requests.Session, "get", lambda self, url, **kw: queue.pop(0) if len(queue) > 1 else queue[0])
    sent = []
    s._byparr_request = lambda url, method="GET", post_data=None: sent.append(url) or {"status": 200, "response": "guest"}
    return s, sent


def test_cloudflare_blocked_get_sends_the_query_string_to_byparr(tmp_path, monkeypatch):
    s, sent = _byparr(tmp_path, monkeypatch, [BLOCKED])  # still blocked after the solve
    r = s.get("https://forum.test/search.php", params={"keywords": "dune", "fid[]": ["26", "51"]})
    assert r.text == "guest"  # last resort: Byparr's own page
    assert sent == ["https://forum.test/search.php?keywords=dune&fid%5B%5D=26&fid%5B%5D=51"]


def test_after_a_cloudflare_solve_the_page_is_asked_again_with_the_new_cookies(tmp_path, monkeypatch):
    s, sent = _byparr(tmp_path, monkeypatch, [BLOCKED, LIVE])
    assert s.get("https://forum.test/search.php") is LIVE  # not Byparr's logged-out copy
    assert len(sent) == 1


def test_a_thread_that_waited_for_the_lock_does_not_solve_cloudflare_again(tmp_path, monkeypatch):
    s, sent = _byparr(tmp_path, monkeypatch, [BLOCKED, LIVE])
    called = []
    assert s._refresh_cf(lambda: called.append(1), s.cf_gen - 1) is True  # solved since it started
    assert not called


def test_lock_wait_stops_at_the_search_deadline(tmp_path):
    s = FakeSession(tmp_path)
    held, done = threading.Event(), threading.Event()

    def hold():
        with s._login_lock:
            held.set()
            done.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    assert held.wait(5)
    s.set_deadline(time.monotonic() + 0.1)
    started = time.monotonic()
    s.ensure_logged_in()  # would wait 60 s without a deadline
    assert time.monotonic() - started < 2 and s.logins == 0
    s.set_deadline(None)
    done.set()
    t.join(5)


def test_cookie_the_login_expired_is_not_brought_back_by_the_merge(tmp_path):
    class Expiring(FakeSession):
        def _do_login(self):
            self.http.cookies.clear()  # the login drops the old cookies
            return super()._do_login()

    s = Expiring(tmp_path)
    s.http.cookies.set("old", "1")
    s.ensure_logged_in()
    assert _cookie_names(s) == {"sid"}
