"""Reading MIRCrew request topics (sections 53, 54, 57): search rows and topic pages.

Pure functions on HTML, so they run against saved pages. Nothing here keeps a link,
a session id or a cookie from the page: only ids, names, dates and text.
"""

import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

IMDB_ID = re.compile(r"tt\d{6,10}")
_TOPIC_ID = re.compile(r"[?&]t=(\d+)")
_POST_ID = re.compile(r"[?&]p=(\d+)")
_FORUM_ID = re.compile(r"[?&]f=(\d+)")
SNIPPET_CHARS = 300
USERNAME = "a.username, a.username-coloured, span.username"  # staff and coloured groups, unlinked users


def _datetime_of(node) -> Optional[str]:
    time_tag = node.select_one("time[datetime]") if node else None
    return time_tag["datetime"] if time_tag else None


_SECRET_PARAM = re.compile(r"[?&;](?:sid|form_token|hash|creation_time)=[\w.-]*", re.I)


def _text_of(node) -> str:
    """Visible text of a node on one line, without session or form tokens: members paste
    links from their own browser, and those carry a session id."""
    return _SECRET_PARAM.sub("", " ".join(node.get_text(" ").split())) if node else ""


def parse_search_rows(html: str, selectors: dict) -> List[Dict]:
    """Topics of a search.php results page: id, title, section, author and date of the
    first post, reply count, and the last post's id, author and date. A page with no rows
    and no results heading (a flood-limit or error page) raises ValueError: it is not "no hits"."""
    soup = BeautifulSoup(html, "lxml")
    if not soup.select(selectors["search_result_row"]) and not soup.select_one("h2.searchresults-title"):
        raise ValueError("no results section")
    rows = []
    for row in soup.select(selectors["search_result_row"]):
        link = row.select_one(selectors["topic_title_link"])
        topic = _TOPIC_ID.search(link.get("href", "")) if link else None
        if not topic:
            continue
        forum = row.select_one(selectors["forum_link"])
        forum_id = _FORUM_ID.search(forum.get("href", "")) if forum else None
        opened = row.select_one(".responsive-hide.left-box")
        author = opened.select_one(USERNAME) if opened else None
        count = re.match(r"\d+", _text_of(row.select_one("dd.posts")))
        last =row.select_one("dd.lastpost")
        last_link = last.select_one("a[href*='viewtopic.php']") if last else None
        last_post = _POST_ID.search(last_link.get("href", "")) if last_link else None
        last_author = last.select_one(USERNAME) if last else None
        rows.append({
            "topic_id": topic.group(1),
            "title": link.get_text(strip=True),
            "section": int(forum_id.group(1)) if forum_id else None,
            "author": author.get_text(strip=True) if author else None,
            "date": _datetime_of(opened),
            "replies": int(count.group()) if count else 0,
            "last_post_id": last_post.group(1) if last_post else None,
            "last_author": last_author.get_text(strip=True) if last_author else None,
            "last_date": _datetime_of(last),
        })
    return rows


def parse_topic_posts(html: str, selectors: dict) -> List[Dict]:
    """Posts of a viewtopic.php page, in order: id, author, date, the first 300 characters of
    the text and the IMDb id found in the post's links or text (None if there is none)."""
    soup = BeautifulSoup(html, "lxml")
    posts = []
    for post in soup.select(selectors["first_post"]):
        post_id = (post.get("id") or "").lstrip("p")
        if not post_id.isdigit():
            continue
        body = post.select_one("div.content")
        author_line = post.select_one("p.author:not(.post-number)")
        author = author_line.select_one(USERNAME) if author_line else None
        imdb = None
        if body:
            for anchor in body.select("a[href*='imdb.com']"):
                found = IMDB_ID.search(anchor.get("href", ""))
                if found:
                    imdb = found.group()
                    break
            if imdb is None:
                found = IMDB_ID.search(body.get_text(" "))
                imdb = found.group() if found else None
        posts.append({
            "post_id": post_id,
            "author": author.get_text(strip=True) if author else None,
            "date": _datetime_of(author_line),
            "text": _text_of(body)[:SNIPPET_CHARS],
            "imdb": imdb,
        })
    return posts


def parse_topic_overview(html: str, selectors: dict) -> Dict:
    """What a viewtopic.php page says about its topic: the title, the section it is in now
    (the last forum in the breadcrumbs), the number of posts, the offset of the last page
    (None when there is only one) and the posts shown."""
    soup = BeautifulSoup(html, "lxml")
    heading = soup.select_one("h2.topic-title")
    title = _text_of(heading) or re.sub(r"\s+-\s+MIRCrew\s*$", "", _text_of(soup.title))
    forums = [m.group(1) for a in soup.select("#nav-breadcrumbs a[href*='viewforum.php']")
              if (m := _FORUM_ID.search(a.get("href", "")))]
    pagination = soup.select_one(".pagination")
    total = re.search(r"(\d+)\s+messagg", _text_of(pagination))
    starts = [int(m.group(1)) for a in soup.select(".pagination a[href*='start=']")
              if (m := re.search(r"[?&]start=(\d+)", a.get("href", "")))]
    return {
        "title": title,
        "section": int(forums[-1]) if forums else None,
        "total_posts": int(total.group(1)) if total else None,
        "last_start": max(starts) if starts else None,
        "posts": parse_topic_posts(html, selectors),
    }
