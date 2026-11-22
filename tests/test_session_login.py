"""Login race (#75): one login at a time, on a private session copy that replaces the shared one only on success."""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from config import Config
from session.byparr import BYPASS_ERROR_TTL, ByparrSession
from session.base import MAX_FORM_REJECTIONS, BaseSession
from sites.mircrew.site import MircrewSession, MircrewSite, login_failure_reason, login_form_rejected
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
                            '<div id="p55" class="post"><div class="content">hidden</div>'
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


def test_download_of_a_deleted_topic_is_not_found_not_unavailable(tmp_path):
    s = ThreadSession(tmp_path, [SimpleNamespace(status_code=404, text="")])
    s.session_valid, s.last_login = True, time.time()
    assert _real_site(tmp_path, s).download("123", None, None, None) is None  # the route answers 404


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
    s.timeouts = []

    def fake_get(self, url, **kw):
        s.timeouts.append(kw.get("timeout"))
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(requests.Session, "get", fake_get)
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


def test_requests_after_a_cloudflare_solve_stay_inside_the_deadline(tmp_path, monkeypatch):
    s, _ = _byparr(tmp_path, monkeypatch, [BLOCKED, LIVE])
    s.set_deadline(time.monotonic() + 5)
    s.get("https://forum.test/search.php", timeout=30)
    s.set_deadline(None)
    assert len(s.timeouts) == 2 and all(t <= 5 for t in s.timeouts)  # the repeat GET too


def test_cf_gen_counts_a_solve_only_when_its_cookies_are_published(tmp_path, monkeypatch):
    s, _ = _byparr(tmp_path, monkeypatch, [LIVE])

    def solved(ok):
        s._private.cf_solved = True
        return ok

    before = s.cf_gen
    assert s._refresh_cf(lambda: solved(False)) is False and s.cf_gen == before  # discarded copy
    assert s._refresh_cf(lambda: solved(True)) is True and s.cf_gen == before + 1


def test_login_cut_short_by_the_deadline_is_not_recorded_as_a_failed_attempt(tmp_path):
    s = FakeSession(tmp_path)
    s.login_ok = False
    s.set_deadline(time.monotonic() - 1)  # already spent
    s.ensure_logged_in()
    assert s.logins == 1 and s.login_gen == 0 and s.login_refusals == 0


def test_a_refusal_that_arrives_after_the_deadline_still_counts(tmp_path):
    s = RefusedSession(tmp_path)
    s.set_deadline(time.monotonic() - 1)
    s.ensure_logged_in()
    assert s.login_refusals == 1 and s.login_gen == 1


def test_cookie_the_login_expired_stays_gone_even_if_a_response_rotated_it(tmp_path):
    class Expiring(FakeSession):
        def _do_login(self):
            self._http.cookies.set("old", "2")  # rotated by an in-flight response
            self.http.cookies.clear()  # the login drops it
            return super()._do_login()

    s = Expiring(tmp_path)
    s.http.cookies.set("old", "1")
    s.ensure_logged_in()
    assert _cookie_names(s) == {"sid"}


def test_cookie_rotated_on_the_shared_session_during_a_login_is_kept(tmp_path):
    class Rotating(FakeSession):
        def _do_login(self):
            self._http.cookies.set("rot", "2")  # an in-flight response refreshed it
            return super()._do_login()

    s = Rotating(tmp_path)
    s.http.cookies.set("rot", "1")
    s.ensure_logged_in()
    assert {c.name: c.value for c in s.http.cookies} == {"rot": "2", "sid": "x"}


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


FORM = '<html><body><form id="login"><input type="hidden" name="sid" value="s1"><input type="hidden" name="form_token" value="t"></form></body></html>'
ERROR_PAGE = '<html><head><title>MIRCrew - Login</title></head><body><div class="error">{}</div></body></html>'


def _login_with_answer(monkeypatch, answer_html):
    monkeypatch.setattr("sites.mircrew.site.time.sleep", lambda _s: None)
    fake = SimpleNamespace(
        cf_valid=True, base_url="https://forum.test", username="u", password="p", login_refusal=None,
        get=lambda url: SimpleNamespace(text=FORM, status_code=200),
        post=lambda url, **kw: SimpleNamespace(text=answer_html, status_code=200),
        _check_logged_in=lambda html: "mode=logout" in html, _save_cookies=lambda: None,
        _is_cf_blocked=lambda r: False, login_form_rejected=False,
        http=SimpleNamespace(cookies=requests.cookies.RequestsCookieJar()))
    return MircrewSession._do_login(fake), fake


def test_a_rejected_form_token_is_not_a_refusal_of_the_credentials(monkeypatch):
    """A cold-start race answers "form non valido"; it must not count towards LOGIN_MAX_REFUSALS."""
    ok, fake = _login_with_answer(monkeypatch, ERROR_PAGE.format("Il form inviato non è valido. Prova a inviarlo di nuovo."))
    assert ok is False and fake.login_refusal is None and fake.login_form_rejected is True


def test_a_wrong_password_still_counts_as_a_refusal(monkeypatch):
    ok, fake = _login_with_answer(monkeypatch, ERROR_PAGE.format("La password inserita non è corretta."))
    assert ok is False and "non è corretta" in fake.login_refusal


def test_login_drops_old_forum_cookies_under_every_domain_form_and_keeps_cloudflare_ones(monkeypatch):
    """#109: a stale phpBB sid saved under ".host" next to the new host-only one is sent first and
    PHP reads it, so the fresh form token never matched. Only Cloudflare's cookies survive."""
    monkeypatch.setattr("sites.mircrew.site.time.sleep", lambda _s: None)
    jar = requests.cookies.RequestsCookieJar()
    for name, domain in [("phpbb3_x_sid", ".forum.test"), ("phpbb3_x_sid", "forum.test"), ("phpbb3_x_u", ".forum.test"),
                         ("cf_clearance", ".forum.test"), ("__cf_bm", ".forum.test")]:
        jar.set(name, "v", domain=domain, path="/")
    seen = {}

    def get(url):
        seen["names"] = sorted(c.name for c in jar)
        return SimpleNamespace(text=FORM, status_code=200)

    fake = SimpleNamespace(
        cf_valid=True, base_url="https://forum.test", username="u", password="p", login_refusal=None, get=get,
        post=lambda url, **kw: SimpleNamespace(text="<a href='ucp.php?mode=logout'>", status_code=200),
        _check_logged_in=lambda html: "mode=logout" in html, _save_cookies=lambda: None,
        _is_cf_blocked=lambda r: False, login_form_rejected=False, http=SimpleNamespace(cookies=jar))
    assert MircrewSession._do_login(fake) is True
    assert seen["names"] == ["__cf_bm", "cf_clearance"]


class TokenRejectedSession(FakeSession):
    """Every login ends in a rejected form token, as a persistent cookie or sid mismatch would."""

    def _do_login(self):
        self.logins += 1
        self.login_form_rejected = True
        return False


def test_repeated_form_token_rejections_stop_after_a_few_attempts_without_counting_refusals(tmp_path):
    s = TokenRejectedSession(tmp_path)
    for _ in range(MAX_FORM_REJECTIONS):
        assert s.login_blocked is None  # not yet: a race is normal
        s.ensure_logged_in()
    assert s.logins == MAX_FORM_REJECTIONS and s.login_refusals == 0
    assert "stale form token" in s.login_blocked
    s.ensure_logged_in()
    assert s.logins == MAX_FORM_REJECTIONS  # no more login POSTs until a person acts


def test_a_good_login_and_retry_login_reset_the_form_rejection_count(tmp_path):
    s = TokenRejectedSession(tmp_path)
    s.ensure_logged_in()
    s.ensure_logged_in()
    assert s.login_form_rejections == 2
    s.retry_login()  # the panel's button: the count starts again
    assert s.login_form_rejections == 1 and s.login_blocked is None
    ok = FakeSession(tmp_path)
    ok.login_form_rejections = 2
    ok.ensure_logged_in()
    assert ok.login_form_rejections == 0


def test_login_form_rejected_recognises_both_languages_and_nothing_else():
    assert login_form_rejected(ERROR_PAGE.format("Il form inviato non è valido. Prova a inviarlo di nuovo."))
    assert login_form_rejected(ERROR_PAGE.format("The submitted form was invalid. Try submitting again."))
    assert not login_form_rejected(ERROR_PAGE.format("La password inserita non è corretta."))
    assert not login_form_rejected("<html></html>")
    assert login_form_rejected("<html><body><p>Il form inviato non è valido. Prova a inviarlo di nuovo.</p></body></html>")  # a message page


# --- the bypass proxy reports when it is down (Dashboard: "Cloudflare solver" box) ---

def _fresh_byparr(tmp_path):
    return ByparrSession("https://forum.test", "u", "p", tmp_path / "c.json", "http://byparr:8191")


def test_bypass_error_is_set_when_byparr_is_down_and_cleared_when_it_answers(tmp_path, monkeypatch):
    s = _fresh_byparr(tmp_path)
    assert s.bypass_error is None

    def refuse(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr("session.byparr.requests.post", refuse)
    assert s._byparr_request("https://forum.test") is None
    assert "cannot connect" in s.bypass_error

    def slow(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr("session.byparr.requests.post", slow)
    s._byparr_request("https://forum.test")
    assert s.bypass_error == "no answer in time"

    ok = SimpleNamespace(status_code=200, json=lambda: {"status": "ok", "solution": {"status": 200, "cookies": []}})
    monkeypatch.setattr("session.byparr.requests.post", lambda *a, **k: ok)
    assert s._byparr_request("https://forum.test")["status"] == 200
    assert s.bypass_error is None


def test_a_timeout_shortened_by_the_search_deadline_does_not_mark_byparr_down(tmp_path, monkeypatch):
    s = _fresh_byparr(tmp_path)
    s.set_deadline(time.monotonic() + 2)  # 2 s left: the call is cut far below Byparr's own timeout

    def slow(*a, **k):
        raise requests.Timeout("cut short")

    monkeypatch.setattr("session.byparr.requests.post", slow)
    s._byparr_request("https://forum.test")
    assert s.bypass_problem() is None
    s.set_deadline(None)
    s._byparr_request("https://forum.test")  # a full-length call that times out is a real signal
    assert s.bypass_problem() == "no answer in time"


def test_any_http_answer_from_byparr_clears_the_error_even_if_it_is_an_error_page(tmp_path, monkeypatch):
    s = _fresh_byparr(tmp_path)
    s._set_bypass_error("cannot connect, is it running?")
    bad = SimpleNamespace(status_code=502, text="<html>bad gateway</html>",
                          json=lambda: (_ for _ in ()).throw(ValueError("not json")))
    monkeypatch.setattr("session.byparr.requests.post", lambda *a, **k: bad)
    assert s._byparr_request("https://forum.test") is None
    assert s.bypass_problem() is None  # it answered: it is up


def test_an_old_bypass_error_is_dropped_from_the_dashboard(tmp_path, monkeypatch):
    s = _fresh_byparr(tmp_path)
    s._set_bypass_error("cannot connect, is it running?")
    assert s.bypass_problem() == "cannot connect, is it running?"
    real = time.monotonic()
    monkeypatch.setattr("session.byparr.time.monotonic", lambda: real + BYPASS_ERROR_TTL + 1)
    assert s.bypass_problem() is None


def test_health_info_carries_the_bypass_error(tmp_path):
    session = MagicMock()
    session.bypass_problem.return_value = "cannot connect, is it running?"
    session.flaresolverr_url = "http://byparr:8191"
    session.last_login = 0
    site = MircrewSite(session=session, config=Config(base_url="https://forum.test", data_dir=tmp_path))
    info = site.health_info()
    assert info["cf_bypass_error"] == "cannot connect, is it running?" and info["cf_bypass_url"] == "http://byparr:8191"
