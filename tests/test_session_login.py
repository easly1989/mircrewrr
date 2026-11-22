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


def test_concurrent_callers_log_in_once(tmp_path):
    s = FakeSession(tmp_path)
    s.release_login.clear()
    threads = [threading.Thread(target=s.ensure_logged_in) for _ in range(8)]
    for t in threads:
        t.start()
    assert s.in_login.wait(5)
    time.sleep(0.1)  # let the others reach the lock
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
