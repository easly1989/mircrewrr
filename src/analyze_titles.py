#!/usr/bin/env python3
"""Analisi pattern titoli thread su MIRCrew per capire le convenzioni."""

import os
import re
import cloudscraper
from bs4 import BeautifulSoup

BASE_URL = "https://mircrew-releases.org"
USERNAME = os.getenv("MIRCREW_USERNAME", "amon2126")
PASSWORD = os.getenv("MIRCREW_PASSWORD", "I4vodyLwon9XjQQB")

TV_FORUM_IDS = {51, 52, 29, 30, 31, 33, 35, 37}


def login(scraper):
    import time
    # First hit homepage
    scraper.get(BASE_URL, timeout=30)
    time.sleep(1)

    r = scraper.get(f"{BASE_URL}/ucp.php?mode=login", timeout=30)
    soup = BeautifulSoup(r.text, "lxml")
    form = soup.find("form", {"id": "login"})
    if not form:
        return False

    fields = {}
    for inp in form.find_all("input", {"type": "hidden"}):
        if inp.get("name"):
            fields[inp["name"]] = inp.get("value", "")

    sid = fields.get("sid", "")
    data = {
        "username": USERNAME,
        "password": PASSWORD,
        "autologin": "on",
        "viewonline": "on",
        "redirect": fields.get("redirect", "index.php"),
        "creation_time": fields.get("creation_time", ""),
        "form_token": fields.get("form_token", ""),
        "sid": sid,
        "login": "Login"
    }

    time.sleep(0.5)
    r = scraper.post(f"{BASE_URL}/ucp.php?mode=login&sid={sid}", data=data,
                     headers={'Referer': f'{BASE_URL}/ucp.php?mode=login'}, timeout=30)
    return "mode=logout" in r.text


def search_and_analyze_titles(scraper, query):
    print(f"\n{'='*70}")
    print(f"QUERY: {query}")
    print('='*70)

    keywords = " ".join(f"+{w}" for w in query.split())
    params = {
        "keywords": keywords,
        "terms": "all",
        "sf": "titleonly",
        "sr": "topics",
        "sk": "t",
        "sd": "d",
        "ch": "300",
        "submit": "Cerca",
    }
    for fid in TV_FORUM_IDS:
        params[f"fid[{fid}]"] = str(fid)

    r = scraper.get(f"{BASE_URL}/search.php", params=params, timeout=30)
    soup = BeautifulSoup(r.text, "lxml")

    titles = []
    for row in soup.select("li.row")[:15]:
        link = row.select_one("a.topictitle")
        if link:
            title = link.get_text(strip=True)
            titles.append(title)
            print(f"\n📌 {title}")
            analyze_title(title)

    return titles


def analyze_title(title):
    """Analizza un singolo titolo per estrarre pattern."""

    # Pattern per stagione
    season_patterns = [
        (r'[Ss]tagione?\s*(\d+)', 'Stagione X'),
        (r'[Ss]eason\s*(\d+)', 'Season X'),
        (r'\b[Ss](\d{1,2})\b', 'SXX'),
        (r'[Ss]tagion[ei]\s*(\d+)\s*[-–]\s*(\d+)', 'Stagione X-Y (multi)'),
        (r'[Ss](\d+)\s*[-–]\s*[Ss]?(\d+)', 'SX-SY (multi)'),
    ]

    # Pattern per episodi nel titolo (stato rilascio)
    episode_status_patterns = [
        (r'\[(\d+)/(\d+)\]', '[current/total]'),
        (r'\[IN CORSO[^\]]*(\d+)/(\d+)\]', '[IN CORSO X/Y]'),
        (r'\[COMPLET[AEO]\]', '[COMPLETA]'),
        (r'\[IN CORSO\]', '[IN CORSO]'),
        (r'\((\d+)/(\d+)\)', '(current/total)'),
        (r'[Ee]p?\.?\s*(\d+)[-–](\d+)', 'Ep X-Y'),
        (r'[Ee](\d+)[-–][Ee]?(\d+)', 'EX-EY'),
    ]

    # Cerca stagione
    season_found = None
    is_multi_season = False
    for pattern, name in season_patterns:
        match = re.search(pattern, title, re.I)
        if match:
            if 'multi' in name or (len(match.groups()) > 1 and match.group(2)):
                is_multi_season = True
                print(f"   ⚠️  MULTI-SEASON: {name} = {match.group()}")
            else:
                season_found = int(match.group(1))
                print(f"   📺 Stagione: {season_found} (pattern: {name})")
            break

    if is_multi_season:
        print(f"   🚫 SKIP: thread multi-stagione")
        return

    # Cerca stato episodi
    for pattern, name in episode_status_patterns:
        match = re.search(pattern, title, re.I)
        if match:
            if len(match.groups()) >= 2:
                print(f"   📊 Episodi: {match.group(1)}/{match.group(2)} (pattern: {name})")
            else:
                print(f"   📊 Stato: {match.group()} (pattern: {name})")
            break

    # Cerca anno
    year_match = re.search(r'\((\d{4})\)', title)
    if year_match:
        print(f"   📅 Anno: {year_match.group(1)}")

    # Cerca qualità
    quality_match = re.search(r'\b(2160p|1080p|720p|4K|UHD|HDR)\b', title, re.I)
    if quality_match:
        print(f"   🎬 Qualità: {quality_match.group()}")


def main():
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )

    print("🔐 Login...")
    if not login(scraper):
        print("❌ Login failed")
        return
    print("✅ Login OK")

    # Test con diverse query
    queries = [
        "masterchef italia",
        "the pitt",
        "one piece",
        "game of thrones",
        "breaking bad",
    ]

    all_titles = []
    for q in queries:
        titles = search_and_analyze_titles(scraper, q)
        all_titles.extend(titles)

    # Summary
    print("\n" + "="*70)
    print("SUMMARY PATTERN ANALYSIS")
    print("="*70)

    multi_season = 0
    with_episode_count = 0
    completa = 0
    in_corso = 0

    for t in all_titles:
        if re.search(r'[Ss]tagion[ei]\s*\d+\s*[-–]\s*\d+|[Ss]\d+\s*[-–]\s*[Ss]?\d+', t, re.I):
            multi_season += 1
        if re.search(r'[\[\(]\d+/\d+[\]\)]', t):
            with_episode_count += 1
        if re.search(r'\[COMPLET[AEO]\]', t, re.I):
            completa += 1
        if re.search(r'\[IN CORSO', t, re.I):
            in_corso += 1

    print(f"📊 Totale titoli analizzati: {len(all_titles)}")
    print(f"   Multi-stagione: {multi_season}")
    print(f"   Con conteggio episodi [X/Y]: {with_episode_count}")
    print(f"   [COMPLETA]: {completa}")
    print(f"   [IN CORSO]: {in_corso}")


if __name__ == "__main__":
    main()
