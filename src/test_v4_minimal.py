#!/usr/bin/env python3
"""Test v4.0 minimal - senza Flask."""

import os
import re
import json
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urljoin, unquote

import cloudscraper
from bs4 import BeautifulSoup

BASE_URL = "https://mircrew-releases.org"
USERNAME = os.getenv("MIRCREW_USERNAME", "amon2126")
PASSWORD = os.getenv("MIRCREW_PASSWORD", "I4vodyLwon9XjQQB")

TV_FORUM_IDS = {51, 52, 29, 30, 31, 33, 35, 37}


def get_infohash(magnet: str) -> Optional[str]:
    match = re.search(r'btih:([a-fA-F0-9]{40}|[a-zA-Z2-7]{32})', magnet)
    return match.group(1).upper() if match else None


def extract_name_from_magnet(magnet: str) -> str:
    match = re.search(r'dn=([^&]+)', magnet)
    return unquote(match.group(1)).replace('+', ' ') if match else ""


def extract_episode_info(text: str) -> Optional[Dict]:
    patterns = [
        r'[Ss](\d{1,2})[Ee](\d{1,3})(?:-[Ee]?(\d{1,3}))?',
        r'(\d{1,2})[xX](\d{1,3})(?:-(\d{1,3}))?',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            return {
                'season': int(groups[0]),
                'episode': int(groups[1]),
            }
    return None


def login(scraper):
    print("🔐 Login...")
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
    data = {"username": USERNAME, "password": PASSWORD, "autologin": "on", "sid": sid, "login": "Login"}
    data.update(fields)

    r = scraper.post(f"{BASE_URL}/ucp.php?mode=login&sid={sid}", data=data, timeout=30)
    if "ucp.php?mode=logout" in r.text:
        print("✅ Login OK")
        return True
    print("❌ Login failed")
    return False


def click_thanks(scraper, soup, topic_url):
    first_post = soup.select_one("div.post")
    if not first_post:
        return soup

    quote_link = first_post.select_one("a[href*='mode=quote']")
    if not quote_link:
        return soup

    post_id_match = re.search(r'[?&]p=(\d+)', quote_link.get("href", ""))
    if not post_id_match:
        return soup

    first_post_id = post_id_match.group(1)

    for a in soup.find_all("a", href=lambda x: x and "thanks=" in str(x)):
        href = a.get("href", "")
        if f"p={first_post_id}" in href or f"thanks={first_post_id}" in href:
            thanks_url = href if href.startswith("http") else f"{BASE_URL}/{href}"
            print("  🙏 Clicking Thanks...")
            scraper.get(thanks_url, timeout=30)
            time.sleep(1)
            r = scraper.get(topic_url, timeout=30)
            return BeautifulSoup(r.text, "lxml")

    return soup


def get_magnets_from_thread(scraper, topic_url):
    r = scraper.get(topic_url, timeout=30)
    soup = BeautifulSoup(r.text, "lxml")

    # Click thanks if needed
    soup = click_thanks(scraper, soup, topic_url)

    first_post = soup.select_one("div.post div.content")
    if not first_post:
        return []

    results = []
    magnet_links = first_post.find_all("a", href=lambda x: x and str(x).startswith("magnet:"))

    for link in magnet_links:
        magnet = re.sub(r'\s+', '', link.get("href", ""))
        infohash = get_infohash(magnet)
        if not infohash:
            continue

        name = extract_name_from_magnet(magnet)
        if not name:
            name = link.get_text(strip=True)

        ep_info = extract_episode_info(name)

        results.append({
            "magnet": magnet,
            "infohash": infohash,
            "name": name,
            "episode_info": ep_info,
        })

    # Dedup
    seen = set()
    unique = []
    for r in results:
        if r["infohash"] not in seen:
            seen.add(r["infohash"])
            unique.append(r)

    return unique


def search_and_expand(scraper, query):
    print(f"🔍 Searching: {query}")

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

    results = []
    seen_threads = set()

    for row in soup.select("li.row")[:5]:  # Max 5 threads
        link = row.select_one("a.topictitle")
        if not link:
            continue

        thread_title = link.get_text(strip=True)
        href = link.get("href", "")
        url = urljoin(BASE_URL, href)
        url = re.sub(r'&sid=[^&]*', '', url)

        topic_match = re.search(r'[?&]t=(\d+)', url)
        if not topic_match:
            continue
        topic_id = topic_match.group(1)

        if topic_id in seen_threads:
            continue
        seen_threads.add(topic_id)

        print(f"\n📁 Thread: {thread_title[:50]}...")
        print(f"   URL: {url}")

        magnets = get_magnets_from_thread(scraper, url)
        print(f"   🧲 Magnets found: {len(magnets)}")

        for mag in magnets:
            ep_info = mag.get("episode_info")
            title = mag["name"] if mag["name"] else thread_title

            results.append({
                "title": title,
                "topic_id": topic_id,
                "infohash": mag["infohash"],
                "guid": f"{topic_id}-{mag['infohash'][:8]}",
                "episode_info": ep_info,
            })

            if ep_info:
                print(f"   ✅ S{ep_info['season']:02d}E{ep_info['episode']:02d} - infohash: {mag['infohash'][:12]}...")
            else:
                print(f"   📦 {title[:40]}... - infohash: {mag['infohash'][:12]}...")

    return results


def main():
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )

    if not login(scraper):
        return

    print("\n" + "=" * 60)
    print("TEST: MasterChef Italia 15")
    print("=" * 60)

    results = search_and_expand(scraper, "masterchef italia 15")

    print("\n" + "=" * 60)
    print(f"📊 TOTALE RISULTATI: {len(results)}")
    print("=" * 60)

    # Check for specific episodes
    episodes_found = {}
    for r in results:
        ep = r.get("episode_info")
        if ep:
            key = f"S{ep['season']:02d}E{ep['episode']:02d}"
            if key not in episodes_found:
                episodes_found[key] = []
            episodes_found[key].append(r)

    print(f"\n📺 Episodi unici trovati: {len(episodes_found)}")
    for key in sorted(episodes_found.keys()):
        eps = episodes_found[key]
        print(f"   {key}: {len(eps)} release(s)")
        for e in eps:
            print(f"      └─ GUID: {e['guid']}")

    # Verify E17 and E18
    print("\n🎯 VERIFICA E17/E18:")
    for target in ["S15E17", "S15E18"]:
        if target in episodes_found:
            print(f"   ✅ {target} trovato!")
            for e in episodes_found[target]:
                print(f"      Download: /download?topic_id={e['topic_id']}&infohash={e['infohash']}")
        else:
            print(f"   ❌ {target} NON trovato")


if __name__ == "__main__":
    main()
