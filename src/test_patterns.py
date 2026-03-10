#!/usr/bin/env python3
"""
Test dei pattern di estrazione su thread reali di MIRCrew.
Verifica che i pattern per size e episodi funzionino correttamente.
"""

import os
import re
import sys
from urllib.parse import unquote

import cloudscraper
from bs4 import BeautifulSoup

BASE_URL = "https://mircrew-releases.org"
USERNAME = os.getenv("MIRCREW_USERNAME", "")
PASSWORD = os.getenv("MIRCREW_PASSWORD", "")

# Pattern da testare per la dimensione
SIZE_PATTERNS = [
    (r'File\s*size\s*:\s*([\d.,]+\s*[KMGTP]i?B)', "File size:"),
    (r'Dimensione\s*:\s*([\d.,]+\s*[KMGTP]i?B)', "Dimensione:"),
    (r'Size\s*:\s*([\d.,]+\s*[KMGTP]i?B)', "Size:"),
    (r'Filesize\s*:\s*([\d.,]+\s*[KMGTP]i?B)', "Filesize:"),
    (r'Peso\s*:\s*([\d.,]+\s*[KMGTP]i?B)', "Peso:"),
    (r'\b([\d.,]+)\s*(GB|GiB|MB|MiB|TB|TiB)\b', "Fallback X.XX GB"),
]

# Pattern per episodi
EPISODE_PATTERNS = [
    (r'[Ss](\d{1,2})[Ee](\d{1,3})(?:-[Ee]?(\d{1,3}))?', "S01E01"),
    (r'(\d{1,2})[xX](\d{1,3})(?:-(\d{1,3}))?', "1x01"),
    (r'[Ss]tagion[ei]\s*(\d{1,2}).*?[Ee]pisodio\s*(\d{1,3})', "Stagione X Episodio Y"),
    (r'[Ss]eason\s*(\d{1,2}).*?[Ee]pisode\s*(\d{1,3})', "Season X Episode Y"),
    (r'[Ee]pisodio?\s*(\d{1,3})', "Episodio X"),
    (r'[Ee]p\.?\s*(\d{1,3})', "Ep. X"),
    (r'Puntata\s*(\d{1,3})', "Puntata X"),
]


def login(scraper):
    """Login al forum."""
    print("🔐 Login in corso...")
    r = scraper.get(f"{BASE_URL}/ucp.php?mode=login", timeout=30)
    soup = BeautifulSoup(r.text, "lxml")

    form = soup.find("form", {"id": "login"})
    if not form:
        print("❌ Form login non trovato")
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
        "sid": sid,
        "login": "Login"
    }
    data.update(fields)

    r = scraper.post(f"{BASE_URL}/ucp.php?mode=login&sid={sid}", data=data, timeout=30)

    if "ucp.php?mode=logout" in r.text:
        print("✅ Login riuscito")
        return True
    print("❌ Login fallito")
    return False


def get_search_results(scraper, query=""):
    """Ottiene risultati di ricerca."""
    from datetime import datetime
    keywords = query if query else str(datetime.now().year)
    keywords = " ".join(f"+{w}" for w in keywords.split())

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

    # Forum IDs
    for fid in [25, 26, 51, 52, 29, 30, 31, 33, 35, 37]:
        params[f"fid[{fid}]"] = str(fid)

    r = scraper.get(f"{BASE_URL}/search.php", params=params, timeout=30)
    soup = BeautifulSoup(r.text, "lxml")

    results = []
    for row in soup.select("li.row")[:10]:  # Solo primi 10
        link = row.select_one("a.topictitle")
        if link:
            href = link.get("href", "")
            match = re.search(r'[?&]t=(\d+)', href)
            if match:
                results.append({
                    "title": link.get_text(strip=True),
                    "topic_id": match.group(1),
                    "url": f"{BASE_URL}/viewtopic.php?t={match.group(1)}"
                })

    return results


def click_thanks(scraper, soup, url):
    """Clicca il pulsante Thanks se presente."""
    # Trova primo post
    first_post = soup.select_one("div.post")
    if not first_post:
        return soup

    # Trova post_id del primo post
    quote_link = first_post.select_one("a[href*='mode=quote']")
    if not quote_link:
        return soup

    post_id_match = re.search(r'[?&]p=(\d+)', quote_link.get("href", ""))
    if not post_id_match:
        return soup

    first_post_id = post_id_match.group(1)

    # Cerca link thanks= per questo post
    for a in soup.find_all("a", href=lambda x: x and "thanks=" in str(x)):
        href = a.get("href", "")
        if f"p={first_post_id}" in href or f"thanks={first_post_id}" in href:
            thanks_url = href if href.startswith("http") else f"{BASE_URL}/{href}"
            print("  🙏 Cliccando Thanks...")
            scraper.get(thanks_url, timeout=30)
            # Ricarica pagina
            r = scraper.get(url, timeout=30)
            return BeautifulSoup(r.text, "lxml")

    return soup


def analyze_thread(scraper, url, title):
    """Analizza un singolo thread."""
    print(f"\n{'='*60}")
    print(f"📄 {title[:50]}...")
    print(f"🔗 {url}")

    r = scraper.get(url, timeout=30)
    soup = BeautifulSoup(r.text, "lxml")

    # Clicca Thanks per rivelare i magnet
    soup = click_thanks(scraper, soup, url)

    first_post = soup.select_one("div.post div.content")
    if not first_post:
        print("❌ Primo post non trovato")
        return None

    post_text = first_post.get_text()
    post_html = str(first_post)

    # Test pattern dimensione
    print("\n📏 TEST DIMENSIONE:")
    size_found = False
    for pattern, name in SIZE_PATTERNS:
        matches = re.findall(pattern, post_text, re.I)
        if matches:
            print(f"  ✅ {name}: {matches[:3]}")  # Max 3 match
            size_found = True
            break

    if not size_found:
        print("  ❌ Nessun pattern ha funzionato!")
        # Mostra contesto per debug
        print("\n  📝 Cercando numeri con unità nel testo...")
        all_sizes = re.findall(r'[\d.,]+\s*(?:GB|GiB|MB|MiB|TB|TiB|KB|KiB)', post_text, re.I)
        if all_sizes:
            print(f"  🔍 Trovati: {all_sizes[:5]}")
        else:
            print("  🔍 Nessun numero con unità trovato")

    # Test pattern episodi (nel titolo e nei magnet)
    print("\n📺 TEST EPISODI (nel titolo):")
    ep_found = False
    for pattern, name in EPISODE_PATTERNS:
        match = re.search(pattern, title)
        if match:
            print(f"  ✅ {name}: {match.group()}")
            ep_found = True
            break

    if not ep_found:
        print("  ℹ️  Nessun pattern episodio nel titolo (potrebbe essere un film)")

    # Cerca magnet e testa pattern episodi su di essi
    magnets = re.findall(r'magnet:\?xt=urn:btih:[a-zA-Z0-9]+[^\s"\'<>]*', post_html)
    if magnets:
        print(f"\n🧲 MAGNET TROVATI: {len(magnets)}")
        for i, mag in enumerate(magnets[:5]):  # Max 5
            dn_match = re.search(r'dn=([^&]+)', mag)
            if dn_match:
                name = unquote(dn_match.group(1))
                print(f"  [{i+1}] {name[:60]}...")

                # Test episodi nel nome magnet
                for pattern, pname in EPISODE_PATTERNS:
                    match = re.search(pattern, name)
                    if match:
                        print(f"      └─ Episodio: {match.group()}")
                        break
    else:
        print("\n🧲 MAGNET: Nessuno trovato (potrebbero essere nascosti)")

    return {
        "size_found": size_found,
        "episode_found": ep_found,
        "magnet_count": len(magnets)
    }


def main():
    if not USERNAME or not PASSWORD:
        print("❌ Imposta MIRCREW_USERNAME e MIRCREW_PASSWORD")
        sys.exit(1)

    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )

    if not login(scraper):
        sys.exit(1)

    # Query diverse per testare vari tipi di contenuto
    queries = [
        "",  # Ultimi rilasci
        "stagione",  # Serie TV italiane
        "season",  # Serie TV inglesi
        "complete",  # Stagioni complete
    ]

    stats = {"size_ok": 0, "size_fail": 0, "with_episodes": 0, "with_magnets": 0}

    for query in queries:
        print(f"\n\n{'#'*60}")
        print(f"# QUERY: '{query or '(ultimi rilasci)'}'")
        print('#'*60)

        results = get_search_results(scraper, query)
        print(f"\n📊 Trovati {len(results)} risultati")

        for r in results[:3]:  # Max 3 per query
            result = analyze_thread(scraper, r["url"], r["title"])
            if result:
                if result["size_found"]:
                    stats["size_ok"] += 1
                else:
                    stats["size_fail"] += 1
                if result["episode_found"]:
                    stats["with_episodes"] += 1
                if result["magnet_count"] > 0:
                    stats["with_magnets"] += 1

    # Report finale
    print("\n\n" + "="*60)
    print("📊 REPORT FINALE")
    print("="*60)
    print(f"✅ Dimensione estratta: {stats['size_ok']}")
    print(f"❌ Dimensione mancante: {stats['size_fail']}")
    print(f"📺 Con episodi: {stats['with_episodes']}")
    print(f"🧲 Con magnet visibili: {stats['with_magnets']}")

    if stats["size_fail"] > 0:
        print("\n⚠️  Alcuni thread non hanno dimensione - potrebbero servire pattern aggiuntivi!")


if __name__ == "__main__":
    main()
