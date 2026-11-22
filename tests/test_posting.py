"""#80: writing request topics, replies and thanks, each with a draft switch, against saved forum pages."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config import Config
from sites.mircrew import posting, site as site_module
from sites.mircrew.site import MircrewSite
from torznab.server import BaseSite, SiteRefused, SiteUnavailable, TorznabServer

FIXTURES = Path(__file__).parent / "fixtures"
KEY = "test-key"


def saved(*parts, status=200):
    return SimpleNamespace(status_code=status, text=FIXTURES.joinpath(*parts).read_text(encoding="utf-8"))


def html(body, status=200):
    return SimpleNamespace(status_code=status, text=f"<html><body>{body}</body></html>")


def message(text, link=""):
    return html(f'<div id="message" class="rules"><div class="inner"><h3>Informazione</h3><p>{text}{link}</p></div></div>'
                '<a href="./ucp.php?mode=logout&amp;sid=SECRETSID0123">Logout</a>')


POSTED = message("Questo messaggio e' stato inserito con successo.<br><br>",
                 '<a href="./viewtopic.php?f=53&amp;t=999000&amp;p=888000#p888000">Visualizza il tuo messaggio</a>')
DRAFT_SAVED = message("La bozza e' stata salvata correttamente.")
FLOOD = message("Il limite di inserimento messaggi e' stato superato. Riprova tra 15 secondi.")
BAD_TOKEN = message("Il modulo inviato non e' valido. Riprova.")
DRAFTS_EMPTY = html("<p>Nessuna bozza salvata.</p>")
SUBJECT = "Il miglio verde (1999)"
BODY = "Titolo: Il miglio verde\nAnno: 1999\nLingua: Ita"
DEAD = html('<a href="./ucp.php?mode=login">Login</a>')


def drafts_with(subject, draft_id="77"):
    return html(f'<ul class="topiclist"><li class="row"><input type="checkbox" name="d[{draft_id}]"> {subject}</li></ul>')


def topic_page(forum_id=53, thanks=False):
    text = saved("requests", "topic_207721.html").text.replace("viewforum.php?f=53", f"viewforum.php?f={forum_id}")
    if thanks:
        link = ('<a href="./viewtopic.php?f=53&amp;p=715902&amp;thanks=715902&amp;to_id=64103&amp;from_id=11681'
                '&amp;hash=abc123&amp;sid=SECRETSID0123">Grazie</a>')
        text = text.replace("</body>", link + "</body>")
    return SimpleNamespace(status_code=200, text=text)


class Forum:
    """Serves the saved pages and records every GET and POST."""

    def __init__(self, post_response=None, drafts=None, topic=None, search=None, thanks_pages=None):
        self.gets, self.posts, self.clicks = [], [], []
        self.post_response = post_response
        self.drafts = list(drafts or [DRAFTS_EMPTY])
        self.topic = topic or topic_page()
        self.search = search or html('<h2 class="searchresults-title">0</h2>')
        self.thanks_pages = list(thanks_pages or [])

    def get(self, url, params=None, timeout=None):
        params = params or {}
        self.gets.append((url.rsplit("/", 1)[-1].split("?")[0], dict(params)))
        if "thanks=" in url:
            self.clicks.append(url)
            return self.topic
        if url.endswith("posting.php"):
            return saved("posting", "form_topic.html" if params["mode"] == "post" else "form_reply.html")
        if url.endswith("ucp.php"):
            return self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]
        if url.endswith("search.php"):
            return self.search
        if url.endswith("viewtopic.php") and "p" in params and self.thanks_pages:
            return self.thanks_pages.pop(0) if len(self.thanks_pages) > 1 else self.thanks_pages[0]
        return self.topic

    def post(self, url, data=None, params=None, headers=None, timeout=None):
        self.posts.append({"url": url, "data": dict(data), "params": dict(params or {})})
        return self.post_response


@pytest.fixture
def make_site(tmp_path, monkeypatch):
    monkeypatch.setattr(site_module.time, "sleep", lambda _s: None)
    monkeypatch.delenv("MIRCREW_PUBLISH", raising=False)
    monkeypatch.delenv("MIRCREW_PUBLISH_PER_DAY", raising=False)

    def _make(forum):
        session = MagicMock()
        session.login_gen = 1
        session.last_login_secs = None
        session.login_blocked = None
        session.username = "user2"
        session.ensure_logged_in.return_value = forum
        return MircrewSite(session=session, config=Config(base_url="https://forum.test", data_dir=tmp_path))

    return _make


def enable_publishing(monkeypatch, per_day=None):
    monkeypatch.setenv("MIRCREW_PUBLISH", "1")
    if per_day is not None:
        monkeypatch.setenv("MIRCREW_PUBLISH_PER_DAY", str(per_day))


# --- drafts ---------------------------------------------------------------------------------

def test_a_topic_draft_is_saved_with_the_forms_tokens_and_nothing_is_published(make_site):
    forum = Forum(DRAFT_SAVED, drafts=[DRAFTS_EMPTY, drafts_with(SUBJECT)])
    result = make_site(forum).publish_topic(53, SUBJECT, BODY, draft=True)
    assert result == {"action": "topic", "draft": True, "done": True, "draft_id": "77"}
    sent = forum.posts[0]
    assert sent["params"] == {"mode": "post", "f": "53"}
    assert sent["data"]["save"] == "Salva bozza" and "post" not in sent["data"]
    assert sent["data"]["subject"] == SUBJECT and sent["data"]["message"] == BODY
    assert sent["data"]["form_token"] == "0123456789abcdef0123456789abcdef01234567"  # read from the form
    assert sent["data"]["creation_time"] == "1763800000" and sent["data"]["attach_sig"] == "on"
    assert "disable_bbcode" not in sent["data"] and "imgur_output_text" not in sent["data"]


def test_a_reply_draft_keeps_the_forms_hidden_fields(make_site):
    forum = Forum(DRAFT_SAVED, drafts=[DRAFTS_EMPTY, drafts_with("Re: I Swear (2025) ENG sub ita", "78")])
    result = make_site(forum).publish_reply("207721", "Mi associo alla richiesta!", draft=True)
    assert result["done"] is True and result["draft_id"] == "78" and result["topic_id"] == "207721"
    data = forum.posts[0]["data"]
    assert forum.posts[0]["params"] == {"mode": "reply", "t": "207721"}
    assert data["topic_cur_post_id"] == "715902" and data["subject"] == "Re: I Swear (2025) ENG sub ita"
    assert data["message"] == "Mi associo alla richiesta!" and "notify" not in data


def test_a_draft_the_forum_does_not_list_is_not_reported_as_saved(make_site):
    forum = Forum(FLOOD, drafts=[DRAFTS_EMPTY])  # still empty after the post
    with pytest.raises(posting.PostRefused) as e:
        make_site(forum).publish_topic(53, SUBJECT, BODY, draft=True)
    assert "Riprova" in e.value.forum_message


def test_drafts_need_no_publish_key_and_do_not_count_against_the_cap(make_site, tmp_path):
    forum = Forum(DRAFT_SAVED, drafts=[DRAFTS_EMPTY, drafts_with(SUBJECT)])
    site = make_site(forum)
    site.publish_topic(53, SUBJECT, BODY, draft=True)
    assert not site.publish_log_file.exists()


# --- publishing -----------------------------------------------------------------------------

def test_publishing_is_refused_without_the_environment_key_before_any_request(make_site):
    forum = Forum(POSTED)
    with pytest.raises(posting.PublishDisabled):
        make_site(forum).publish_topic(53, SUBJECT, BODY, draft=False)
    assert not forum.gets and not forum.posts


def test_a_published_topic_returns_its_ids_and_is_logged_against_the_cap(make_site, monkeypatch):
    enable_publishing(monkeypatch, per_day=1)
    forum = Forum(POSTED)
    site = make_site(forum)
    result = site.publish_topic(54, SUBJECT, BODY, draft=False)
    assert result == {"action": "topic", "draft": False, "done": True, "post_id": "888000", "topic_id": "999000"}
    assert forum.posts[0]["data"]["post"] == "Invia" and "save" not in forum.posts[0]["data"]
    assert len(json.loads(site.publish_log_file.read_text())) == 1
    with pytest.raises(posting.PublishLimit):  # the cap of 1 is spent
        site.publish_reply("207721", "Bump", draft=False)
    assert len(forum.posts) == 1


def test_a_request_the_account_already_made_is_not_posted_twice(make_site, monkeypatch):
    enable_publishing(monkeypatch)
    search = SimpleNamespace(status_code=200, text=saved("requests", "search_53_57.html").text.replace(
        "Il miglio verde (1999)", SUBJECT).replace('class="username-coloured">user3', 'class="username">user2')
        .replace("viewforum.php?f=57", "viewforum.php?f=53"))  # the same request, by this account, still open
    forum = Forum(POSTED, search=search)
    result = make_site(forum).publish_topic(53, SUBJECT, BODY, draft=False)
    assert result["already_exists"] is True and result["done"] is False
    assert result["topic_id"] == "100001" and not forum.posts


def test_a_flood_limit_comes_back_as_an_error_with_the_forums_message_and_is_not_counted(make_site, monkeypatch):
    enable_publishing(monkeypatch)
    site = make_site(Forum(FLOOD))
    with pytest.raises(posting.PostRefused) as e:
        site.publish_topic(53, SUBJECT, BODY, draft=False)
    assert "limite" in e.value.forum_message and e.value.status == 422
    assert not site.publish_log_file.exists()


def test_an_expired_form_token_is_an_error_not_a_success(make_site, monkeypatch):
    enable_publishing(monkeypatch)
    with pytest.raises(posting.PostRefused, match="refused") as e:
        make_site(Forum(BAD_TOKEN)).publish_reply("207721", "Bump", draft=False)
    assert "non e' valido" in e.value.forum_message


def test_a_form_without_tokens_is_refused_before_anything_is_sent():
    page = '<form id="postform"><input type="text" name="subject"><textarea name="message"></textarea></form>'
    with pytest.raises(posting.PostRefused, match="form_token"):
        posting.parse_post_form(page)


def test_a_session_that_died_while_posting_is_unavailable_and_nothing_is_counted(make_site, monkeypatch):
    enable_publishing(monkeypatch)
    site = make_site(Forum(DEAD))
    with pytest.raises(SiteUnavailable, match="nothing was sent"):
        site.publish_topic(53, SUBJECT, BODY, draft=False)
    assert not site.publish_log_file.exists()


# --- where it may write ---------------------------------------------------------------------

def test_a_topic_goes_only_to_the_open_request_sections(make_site):
    forum = Forum(POSTED)
    with pytest.raises(ValueError, match="section"):
        make_site(forum).publish_topic(57, SUBJECT, BODY)
    assert not forum.gets


def test_a_reply_goes_only_to_a_topic_in_a_request_section(make_site):
    forum = Forum(DRAFT_SAVED, topic=topic_page(forum_id=26))
    with pytest.raises(ValueError, match="not in a request section"):
        make_site(forum).publish_reply("207721", "Ciao", draft=True)
    assert not forum.posts


@pytest.mark.parametrize("subject,body", [("", BODY), (SUBJECT, " "), ("x" * 201, BODY), (SUBJECT, "x" * 10001), (None, BODY)])
def test_bad_text_is_refused_before_any_request(make_site, subject, body):
    forum = Forum(POSTED)
    with pytest.raises(ValueError):
        make_site(forum).publish_topic(53, subject, body)
    assert not forum.gets


# --- thanks ---------------------------------------------------------------------------------

def test_a_thanks_with_draft_on_is_not_sent(make_site):
    forum = Forum(thanks_pages=[topic_page(thanks=True)])
    result = make_site(forum).publish_thanks("715902", draft=True)
    assert result["would_thank"] is True and result["done"] is False and not forum.clicks


def test_a_thanks_click_is_checked_by_the_link_going_away(make_site, monkeypatch):
    enable_publishing(monkeypatch)
    forum = Forum(thanks_pages=[topic_page(thanks=True), topic_page(thanks=False)])
    result = make_site(forum).publish_thanks("715902", draft=False)
    assert result == {"action": "thanks", "draft": False, "done": True, "post_id": "715902"}
    assert len(forum.clicks) == 1 and "thanks=715902" in forum.clicks[0]


def test_a_thanks_that_did_not_register_is_an_error(make_site, monkeypatch):
    enable_publishing(monkeypatch)
    forum = Forum(thanks_pages=[topic_page(thanks=True)])  # the link is still there after the click
    with pytest.raises(posting.PostRefused):
        make_site(forum).publish_thanks("715902", draft=False)


def test_a_post_without_a_thanks_link_is_reported_not_clicked(make_site, monkeypatch):
    enable_publishing(monkeypatch)
    forum = Forum(thanks_pages=[topic_page(thanks=False)])
    result = make_site(forum).publish_thanks("715902", draft=False)
    assert result["done"] is False and "already thanked" in result["reason"] and not forum.clicks


def test_a_thanks_needs_the_publish_key(make_site):
    with pytest.raises(posting.PublishDisabled):
        make_site(Forum()).publish_thanks("715902", draft=False)


# --- the routes -----------------------------------------------------------------------------

class StubSite(BaseSite):
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def publish_topic(self, section, subject, body, draft):
        self.calls.append(("topic", section, subject, body, draft))
        if self.error:
            raise self.error
        return {"action": "topic", "draft": draft, "done": True}

    def publish_reply(self, topic_id, body, draft):
        self.calls.append(("reply", topic_id, body, draft))
        return {"action": "reply", "draft": draft, "done": True}

    def publish_thanks(self, post_id, draft):
        self.calls.append(("thanks", post_id, draft))
        return {"action": "thanks", "draft": draft, "done": False}

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


def test_a_call_without_draft_is_a_draft():
    site = StubSite()
    client = client_for(site)
    assert client.post(f"/mircrew/requests/topic?apikey={KEY}", json={"section": 53, "subject": "S", "body": "B"}).status_code == 200
    assert client.post(f"/mircrew/requests/reply?apikey={KEY}", json={"topic_id": 5, "body": "B"}).status_code == 200
    assert client.post(f"/mircrew/requests/thanks?apikey={KEY}", json={"post_id": 9}).status_code == 200
    assert [c[-1] for c in site.calls] == [True, True, True]  # draft=True for all three


def test_publishing_needs_an_explicit_false_and_a_real_boolean():
    site = StubSite()
    client = client_for(site)
    ok = client.post(f"/mircrew/requests/topic?apikey={KEY}", json={"section": 53, "subject": "S", "body": "B", "draft": False})
    assert ok.status_code == 200 and site.calls[-1][-1] is False
    for bad in ("false", 0, None):
        r = client.post(f"/mircrew/requests/topic?apikey={KEY}", json={"section": 53, "subject": "S", "body": "B", "draft": bad})
        assert r.status_code == 400


def test_the_write_routes_need_the_api_key_a_json_object_and_post():
    client = client_for(StubSite())
    assert client.post("/mircrew/requests/topic", json={}).status_code == 401
    assert client.post(f"/mircrew/requests/topic?apikey={KEY}", data="x").status_code == 400
    assert client.post(f"/mircrew/requests/topic?apikey={KEY}", json={"section": "53"}).status_code == 400
    assert client.get(f"/mircrew/requests/topic?apikey={KEY}").status_code == 405  # a write is never a GET


@pytest.mark.parametrize("error,status", [
    (posting.PublishDisabled("off"), 403),
    (posting.PublishLimit("cap"), 429),
    (posting.PostRefused("refused", "Riprova tra 15 secondi"), 422),
    (SiteUnavailable("down"), 503),
    (ValueError("bad"), 400),
])
def test_refusals_map_to_their_status_with_the_forums_message(error, status):
    r = client_for(StubSite(error)).post(f"/mircrew/requests/topic?apikey={KEY}",
                                         json={"section": 53, "subject": "S", "body": "B"})
    assert r.status_code == status
    if isinstance(error, SiteRefused):
        assert r.get_json()["forum_message"] == error.detail
