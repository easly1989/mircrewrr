"""Writing to MIRCrew: reading phpBB posting forms and what the forum answers to them.

Pure functions on HTML, so they run against saved pages. Nothing here sends anything.
"""

import re
from typing import Dict, Optional

from bs4 import BeautifulSoup

from torznab.server import SiteRefused

from .request_topics import _text_of

_POST_ID = re.compile(r"[?&]p=(\d+)")
_TOPIC_ID = re.compile(r"[?&]t=(\d+)")
_DRAFT_ID = re.compile(r"[?&]d=(\d+)|^d\[(\d+)\]$")
# Fields of the editor that are not part of the message: buttons, emoji search, image upload.
_SKIP_FIELD = re.compile(r"^(imgur|emoji|abbc3|addbbcode)|^$")


class PublishDisabled(SiteRefused):
    """Publishing is switched off for this proxy (MIRCREW_PUBLISH is not 1); drafts still work."""

    status = 403


class PublishLimit(SiteRefused):
    """The per-day cap on published topics and replies is reached."""

    status = 429


class PostRefused(SiteRefused):
    """The forum did not take the post or the draft; `forum_message` is what it said."""

    status = 422

    def __init__(self, message: str, forum_message: Optional[str] = None):
        super().__init__(message, forum_message)
        self.forum_message = forum_message


def parse_post_form(html: str) -> Dict:
    """The posting form of a posting.php page: its action path and the fields as the browser
    would send them (hidden tokens, checked boxes, the subject and message as prefilled).
    Raises PostRefused when the page has no form or no tokens (a message page, a refusal)."""
    soup = BeautifulSoup(html, "lxml")
    form = soup.select_one("form#postform")
    if not form:
        raise PostRefused("no posting form on the page", forum_message=forum_message(html))
    fields: Dict[str, str] = {}
    for el in form.select("input, textarea, select"):
        name = el.get("name") or ""
        kind = el.get("type", "text") if el.name == "input" else el.name
        if _SKIP_FIELD.search(name) or kind in ("button", "submit", "file", "select", "radio"):
            continue
        if kind == "checkbox":
            if el.has_attr("checked"):
                fields[name] = el.get("value", "on")
        elif kind == "textarea":
            fields[name] = el.get_text()
        else:
            fields[name] = el.get("value", "")
    if not fields.get("form_token") or not fields.get("creation_time"):
        raise PostRefused("the posting form has no form_token or creation_time",
                          forum_message=forum_message(html))
    return {"action": (form.get("action") or "./posting.php").split("?")[0], "fields": fields}


def forum_message(html: str) -> Optional[str]:
    """What the forum says on a page: its error list, or the text of its message box."""
    soup = BeautifulSoup(html, "lxml")
    errors = [_text_of(e) for e in soup.select(".error, .errorbox") if _text_of(e)]
    if errors:
        return " ".join(errors)[:400]
    box = soup.select_one("#message")
    return _text_of(box)[:400] if box else None


def posted_ids(html: str) -> Dict[str, Optional[str]]:
    """Topic and post id of a message page that links to the new post ("view your post"),
    or Nones when the page has no such link (a refusal, a flood limit)."""
    soup = BeautifulSoup(html, "lxml")
    box = soup.select_one("#message") or soup
    for a in box.select("a[href*='viewtopic.php']"):
        href = a.get("href", "")
        post, topic = _POST_ID.search(href), _TOPIC_ID.search(href)
        if post or topic:
            return {"post_id": post.group(1) if post else None, "topic_id": topic.group(1) if topic else None}
    return {"post_id": None, "topic_id": None}


def count_in_drafts(html: str, subject: str) -> int:
    """How many times `subject` appears on the user's drafts page (a draft is listed by its title)."""
    return _text_of(BeautifulSoup(html, "lxml")).count(subject)


def newest_draft_id(html: str) -> Optional[str]:
    """The highest draft id on the drafts page, from its delete checkboxes (d[12]) or its links (d=12)."""
    soup = BeautifulSoup(html, "lxml")
    ids = []
    for el in soup.select("input[name^='d[']"):
        m = _DRAFT_ID.search(el.get("name", ""))
        if m:
            ids.append(int(m.group(2)))
    for a in soup.select("a[href*='d=']"):
        m = _DRAFT_ID.search(a.get("href", ""))
        if m and m.group(1):
            ids.append(int(m.group(1)))
    return str(max(ids)) if ids else None


def find_thanks_link(html: str, post_id: str) -> Optional[str]:
    """The thanks link of one post (its `thanks=` or `p=` parameter is the post id), or None
    when the post has none: already thanked, or the account may not thank it."""
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=lambda x: x and "thanks=" in str(x)):
        href = a.get("href", "")
        if f"thanks={post_id}" in href or f"p={post_id}" in href:
            return href
    return None
