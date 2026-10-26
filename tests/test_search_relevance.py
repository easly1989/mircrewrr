"""Relevance filtering of search() results (issue #58).

A tiny phpBB emulator serves a corpus of forum titles so the whole search()
fallback chain (terms=all, apostrophe variant, terms=any, progressive subsets,
no-forum-filter) runs end to end without network access.
"""

import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import Config  # noqa: E402
from sites.mircrew import parser, site as site_module  # noqa: E402
from sites.mircrew.site import MircrewSite  # noqa: E402

TV, MOVIES = 51, 25

# (title, forum id)
CORPUS = [
    ("Street Fighter II V (1995) ITA", MOVIES),
    ("Street Sharks - Stagione 1-3 ITA", TV),
    ("Street Hawk - Stagione 1 ITA", TV),
    ("Street Sharks - Quattro pinne all'attacco ITA", TV),
    ("L'agente segreto (2023) ITA 1080p", MOVIES),
    ("Nobody Wants This - Stagione 1 [COMPLETA] ITA", TV),
    ("Nobody Wants Pizza - Stagione 1 ITA", TV),
    ("The Leftovers - Stagione 1 ITA", TV),
    ("Dune Parte Due (2024) ITA 2160p", MOVIES),
    ("The Walking Dead Dixon - Stagione 1 ITA", TV),
    ("Marvel's Agents of S.H.I.E.L.D. - Stagione 1 ITA", TV),
]

# phpBB sometimes returns unrelated hits for terms=any (seen in the field)
ANY_NOISE = [
    ("Will & Grace - Stagione 1 ITA", TV),
    ("That Time I Got Reincarnated as a Slime - Stagione 1 ITA", TV),
    ("Perry Mason (2020) - Stagione 1 ITA", TV),
    ("Person Of Interest - Stagione 1 ITA", TV),
]

# phpBB ignores common words: a query containing one returns nothing with terms=all
PHPBB_STOP_WORDS = {"this"}


def _words(text):
    return set(re.findall(r"[\w']+", text.lower()))


class FakePhpBB:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((params["keywords"], params["terms"], params.get("fid[]")))
        words = _words(params["keywords"])
        fids = {int(f) for f in params.get("fid[]", [])}
        hits = []
        for i, (title, fid) in enumerate(CORPUS + (ANY_NOISE if params["terms"] == "any" else [])):
            if fids and fid not in fids:
                continue
            tw = _words(title)
            if params["terms"] == "all":
                ok = bool(words) and words <= tw and not (words & PHPBB_STOP_WORDS)
            else:
                ok = bool(words & tw) or title in dict(ANY_NOISE)
            if ok:
                hits.append(
                    f'<li class="row"><a class="topictitle" href="./viewtopic.php?f={fid}&t={1000 + i}">'
                    f'{title}</a><a href="./viewforum.php?f={fid}">forum</a></li>'
                )
        return SimpleNamespace(status_code=200, text=f"<html><body><ul>{''.join(hits)}</ul></body></html>")


@pytest.fixture
def make_site(tmp_path, monkeypatch):
    monkeypatch.setattr(site_module.time, "sleep", lambda _s: None)

    def _make():
        scraper = FakePhpBB()
        session = MagicMock()
        session.ensure_logged_in.return_value = scraper
        cfg = Config(base_url="https://forum.test", data_dir=tmp_path)
        return MircrewSite(session=session, config=cfg), scraper

    return _make


def _titles(results):
    # Synthetic episode titles carry the show name first; compare on that prefix.
    return sorted({r.title for r in results})


# (query, categories, season, episode, substrings every expected title set must contain, expect_empty)
SEARCH_CASES = [
    pytest.param("61st Street", None, 2, None, [], id="issue58-61st-street-season-no-forum-filter"),
    pytest.param("61st Street", [5000], 2, None, [], id="issue58-61st-street-season-with-tv-category"),
    pytest.param("61st Street", None, None, None, [], id="issue58-61st-street-no-season"),
    pytest.param("Completely Unknown Show", None, None, None, [], id="unknown-show"),
    # fallbacks that must keep working
    pytest.param("Lagente segreto", None, None, None, ["L'agente segreto"], id="stage1b-italian-apostrophe"),
    pytest.param("Nobody Wants This", [5000], 1, None, ["Nobody Wants This"], id="stopword-query-falls-through-to-any"),
    pytest.param("The Leftovers", [5000], 1, None, ["The Leftovers"], id="stage1-foreign-title"),
    pytest.param("Dune Parte Due", [5000], None, None, ["Dune Parte Due"], id="stage4-retry-without-forum-filter"),
    pytest.param("The Walking Dead Daryl Dixon", None, 1, None, ["The Walking Dead Dixon"],
                 id="stage2-terms-any-one-word-missing-of-four"),
]


@pytest.mark.parametrize("query,categories,season,episode,expected_substrings", SEARCH_CASES)
def test_search_relevance(make_site, query, categories, season, episode, expected_substrings):
    site, _ = make_site()
    results = site.search(query, categories, season, episode)
    titles = _titles(results)
    if not expected_substrings:
        assert titles == []
    else:
        assert len(titles) == len(expected_substrings), titles
        for sub in expected_substrings:
            assert any(sub in t for t in titles), (sub, titles)


def test_empty_query_is_not_filtered(make_site, monkeypatch):
    """RSS sync / Prowlarr test: empty query searches the current year, unfiltered."""
    site, _ = make_site()
    seen = []
    monkeypatch.setattr(site, "_do_search", lambda *a, **kw: seen.append((a, kw)) or [])
    site.search("", None, None, None)
    assert seen and all(kw.get("relevance_query") is None for _a, kw in seen)
    assert str(datetime.now().year) in seen[0][0][1]


def test_concurrent_searches_are_capped(make_site, monkeypatch):
    """#71: searches beyond MAX_CONCURRENT_SEARCHES wait instead of parsing pages at once."""
    import threading
    site, _ = make_site()  # patches time.sleep: wait on an Event instead
    running, peak, lock = [0], [0], threading.Lock()

    def slow(*_a, **_kw):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        threading.Event().wait(0.05)
        with lock:
            running[0] -= 1
        return []

    monkeypatch.setattr(site, "_do_search", slow)
    threads = [threading.Thread(target=site.search, args=("", None, None, None)) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == site.MAX_CONCURRENT_SEARCHES


def test_queued_search_gives_up_after_timeout(make_site, monkeypatch):
    """#72: a search that cannot get a slot returns nothing instead of blocking past Prowlarr's timeout."""
    site, _ = make_site()
    monkeypatch.setattr(site, "SEARCH_QUEUE_TIMEOUT", 0.01)
    called = []
    monkeypatch.setattr(site, "_search", lambda *a: called.append(a) or ["x"])
    for _ in range(site.MAX_CONCURRENT_SEARCHES):
        site._search_slots.acquire()
    assert site.search("", None, None, None) == []
    assert not called
    for _ in range(site.MAX_CONCURRENT_SEARCHES):
        site._search_slots.release()
    assert site.search("", None, None, None) == ["x"]


# query_coverage: (title, query, expected_coverage)
COVERAGE_CASES = [
    ("Street Fighter II V (1995)", "61st Street", 0.5),
    ("61st Street - Stagione 2 ITA", "61st Street", 1.0),
    ("Will & Grace", "61st Street", 0.0),
    ("Person Of Interest", "Person of Interest", 1.0),                     # stop word ignored
    ("L'agente segreto", "Lagente segreto", 1.0),                           # apostrophe dropped by client
    ("Marvel's Agents", "Marvels Agents", 1.0),
    ("Pokémon - Stagione 1", "Pokemon", 1.0),                               # accents
    ("Agents of S.H.I.E.L.D.", "Agents of SHIELD", 1.0),                    # dotted acronym
    ("Street Sharks", "Street Shark", 1.0),                                 # plural
    ("Dr.Stone.S01E01.ITA.1080p", "Dr Stone", 1.0),                         # dotted release name
    ("Show Name - Stagione 2", "Show Name S02", 1.0),                       # season marker ignored
    ("Show Name - Stagione 2", "Show Name Stagione 2", 1.0),
    ("Whatever", "", 1.0),                                                  # nothing to check
    ("Whatever", "the of", 1.0),                                            # only stop words
]


@pytest.mark.parametrize("title,query,expected", COVERAGE_CASES)
def test_query_coverage(title, query, expected):
    assert parser.query_coverage(title, query) == pytest.approx(expected)
