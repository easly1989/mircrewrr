#!/usr/bin/env python3
"""
Quick test script per MIRCrew - test senza Docker
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Setup
BASE_URL = "https://mircrew-releases.org"
DATA_DIR = Path("/home/user/mircrewrr/data")
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def save_response(response, name):
    """Salva risposta per analisi."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = DATA_DIR / f"{name}_{timestamp}.html"
    filepath.write_text(response.text, encoding="utf-8")
    print(f"  Risposta salvata: {filepath}")
    return filepath


def print_response_info(response, title):
    """Stampa info sulla risposta."""
    print(f"\n{'='*60}")
    print(f"{title}")
    print(f"{'='*60}")
    print(f"URL: {response.url}")
    print(f"Status: {response.status_code} {response.reason}")
    print(f"Content-Type: {response.headers.get('Content-Type', 'N/A')}")
    print(f"Content-Length: {len(response.text)} chars")
    print(f"Server: {response.headers.get('Server', 'N/A')}")

    # Check per CloudFlare
    cf_headers = [h for h in response.headers if 'cf-' in h.lower() or 'cloudflare' in h.lower()]
    if cf_headers:
        print(f"CloudFlare Headers: {cf_headers}")

    # Check cookies
    if response.cookies:
        print(f"Cookies: {list(response.cookies.keys())}")


def test_requests():
    """Test 1: Richiesta standard con requests."""
    print("\n" + "="*60)
    print("TEST 1: Richiesta HTTP standard (requests)")
    print("="*60)

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        # Homepage
        print(f"\nGET {BASE_URL}")
        response = session.get(BASE_URL, timeout=30)
        print_response_info(response, "Homepage Response")
        save_response(response, "requests_homepage")

        # Verifica contenuto
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "lxml")
            title = soup.find("title")
            print(f"Page Title: {title.get_text() if title else 'N/A'}")

            # Cerca indicatori di protezione
            if "checking your browser" in response.text.lower():
                print(">>> RILEVATO: CloudFlare browser check")
            if "captcha" in response.text.lower():
                print(">>> RILEVATO: CAPTCHA")
            if "access denied" in response.text.lower():
                print(">>> RILEVATO: Access Denied")

            return session, True
        else:
            print(f">>> ERRORE: Status {response.status_code}")
            return session, False

    except Exception as e:
        print(f">>> ECCEZIONE: {e}")
        return None, False


def test_login_page(session):
    """Test 2: Pagina di login."""
    print("\n" + "="*60)
    print("TEST 2: Pagina di login")
    print("="*60)

    if not session:
        print("Nessuna sessione disponibile")
        return None

    login_url = f"{BASE_URL}/ucp.php?mode=login"

    try:
        print(f"\nGET {login_url}")
        response = session.get(login_url, timeout=30)
        print_response_info(response, "Login Page Response")
        save_response(response, "requests_login")

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "lxml")

            # Cerca form login
            login_form = soup.find("form", {"id": "login"})
            if login_form:
                print("\n>>> Form login TROVATO!")

                # Estrai campi
                inputs = login_form.find_all("input")
                print(f"Campi trovati: {len(inputs)}")
                for inp in inputs:
                    name = inp.get("name", "[no name]")
                    inp_type = inp.get("type", "text")
                    print(f"  - {name} ({inp_type})")

                return {"form": login_form, "soup": soup}
            else:
                print(">>> Form login NON trovato")

                # Cerca altri form
                forms = soup.find_all("form")
                print(f"Altri form trovati: {len(forms)}")
                for f in forms:
                    print(f"  - action: {f.get('action', 'N/A')}, id: {f.get('id', 'N/A')}")

        return None

    except Exception as e:
        print(f">>> ECCEZIONE: {e}")
        return None


def test_cloudscraper():
    """Test 3: CloudScraper per bypass."""
    print("\n" + "="*60)
    print("TEST 3: CloudScraper (bypass anti-bot)")
    print("="*60)

    try:
        import cloudscraper

        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )

        print(f"\nGET {BASE_URL} (cloudscraper)")
        response = scraper.get(BASE_URL, timeout=30)
        print_response_info(response, "CloudScraper Response")
        save_response(response, "cloudscraper_homepage")

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "lxml")
            title = soup.find("title")
            print(f"Page Title: {title.get_text() if title else 'N/A'}")

            # Test login page
            print(f"\nGET {BASE_URL}/ucp.php?mode=login (cloudscraper)")
            login_resp = scraper.get(f"{BASE_URL}/ucp.php?mode=login", timeout=30)
            print_response_info(login_resp, "CloudScraper Login Response")
            save_response(login_resp, "cloudscraper_login")

            if login_resp.status_code == 200:
                soup = BeautifulSoup(login_resp.text, "lxml")
                login_form = soup.find("form", {"id": "login"})
                if login_form:
                    print("\n>>> CloudScraper: Form login TROVATO!")
                    return scraper

        return None

    except ImportError:
        print("CloudScraper non installato")
        return None
    except Exception as e:
        print(f">>> ECCEZIONE: {e}")
        return None


def analyze_protection(response_file):
    """Analizza la risposta per identificare il tipo di protezione."""
    print("\n" + "="*60)
    print("ANALISI PROTEZIONE")
    print("="*60)

    if not response_file or not response_file.exists():
        print("Nessun file da analizzare")
        return

    content = response_file.read_text(encoding="utf-8").lower()

    protections = []

    # CloudFlare
    if "cloudflare" in content or "cf-ray" in content:
        protections.append("CloudFlare")
    if "checking your browser" in content:
        protections.append("CloudFlare Browser Check")
    if "cf_clearance" in content:
        protections.append("CloudFlare Challenge")

    # DDoS-Guard
    if "ddos-guard" in content or "ddos_guard" in content:
        protections.append("DDoS-Guard")

    # CAPTCHA
    if "captcha" in content or "recaptcha" in content or "hcaptcha" in content:
        protections.append("CAPTCHA")

    # Generic
    if "access denied" in content:
        protections.append("Access Denied")
    if "forbidden" in content:
        protections.append("Forbidden")
    if "bot" in content and "detected" in content:
        protections.append("Bot Detection")

    if protections:
        print(f"Protezioni rilevate: {', '.join(protections)}")
    else:
        print("Nessuna protezione specifica rilevata")

    # Cerca meta refresh o JavaScript redirect
    if "meta http-equiv=\"refresh\"" in content:
        print(">>> Meta refresh rilevato")
    if "window.location" in content or "document.location" in content:
        print(">>> JavaScript redirect rilevato")


def main():
    print("\n" + "#"*60)
    print("# MIRCrew Indexer - Quick Debug Test")
    print(f"# Target: {BASE_URL}")
    print(f"# Time: {datetime.now().isoformat()}")
    print("#"*60)

    # Test 1: Requests standard
    session, success = test_requests()

    # Test 2: Pagina login
    if success:
        test_login_page(session)

    # Test 3: CloudScraper
    scraper = test_cloudscraper()

    # Analisi protezioni
    latest_files = sorted(DATA_DIR.glob("*.html"), key=lambda x: x.stat().st_mtime, reverse=True)
    if latest_files:
        analyze_protection(latest_files[0])

    # Riepilogo
    print("\n" + "="*60)
    print("RIEPILOGO")
    print("="*60)
    print(f"Requests standard: {'OK' if success else 'FALLITO'}")
    print(f"CloudScraper: {'OK' if scraper else 'FALLITO'}")
    print(f"\nFile salvati in: {DATA_DIR}")
    print(f"Files: {[f.name for f in DATA_DIR.glob('*.html')]}")


if __name__ == "__main__":
    main()
