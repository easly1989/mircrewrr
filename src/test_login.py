#!/usr/bin/env python3
"""
Test login effettivo con CloudScraper
"""

import os
import sys
from pathlib import Path
from urllib.parse import urljoin

import cloudscraper
from bs4 import BeautifulSoup

BASE_URL = "https://mircrew-releases.org"
DATA_DIR = Path("/home/user/mircrewrr/data")

# Credenziali da variabili d'ambiente
USERNAME = os.getenv("MIRCREW_USERNAME", "")
PASSWORD = os.getenv("MIRCREW_PASSWORD", "")


def test_login():
    print(f"Testing login to {BASE_URL}")
    print(f"Username: {USERNAME[:3]}***" if USERNAME else "Username: NOT SET")

    if not USERNAME or not PASSWORD:
        print("\nERRORE: Configura MIRCREW_USERNAME e MIRCREW_PASSWORD")
        print("Esempio: MIRCREW_USERNAME=user MIRCREW_PASSWORD=pass python3 src/test_login.py")
        return False

    # Crea scraper
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )

    try:
        # 1. Ottieni pagina login
        login_url = f"{BASE_URL}/ucp.php?mode=login"
        print(f"\n1. GET {login_url}")
        response = scraper.get(login_url, timeout=30)
        print(f"   Status: {response.status_code}")

        if response.status_code != 200:
            print(f"   ERRORE: {response.status_code}")
            return False

        # Parse form
        soup = BeautifulSoup(response.text, "lxml")
        login_form = soup.find("form", {"id": "login"})

        if not login_form:
            print("   ERRORE: Form login non trovato")
            return False

        print("   Form login trovato")

        # Estrai campi hidden
        form_data = {
            "username": USERNAME,
            "password": PASSWORD,
            "autologin": "on",
            "viewonline": "on",
            "login": "Entra",  # Button text in italiano
        }

        # Aggiungi campi hidden (CSRF, SID, etc.)
        for inp in login_form.find_all("input", {"type": "hidden"}):
            name = inp.get("name")
            value = inp.get("value", "")
            if name:
                form_data[name] = value
                print(f"   Hidden field: {name}={value[:20]}..." if len(value) > 20 else f"   Hidden field: {name}={value}")

        # Cerca campo redirect
        redirect_inp = login_form.find("input", {"name": "redirect"})
        if redirect_inp:
            form_data["redirect"] = redirect_inp.get("value", "./index.php")

        # 2. POST login
        action = login_form.get("action", "./ucp.php?mode=login")
        if action.startswith("./"):
            action = action[2:]
        post_url = urljoin(BASE_URL, action)

        print(f"\n2. POST {post_url}")
        headers = {"Referer": login_url}

        response = scraper.post(
            post_url,
            data=form_data,
            headers=headers,
            timeout=30,
            allow_redirects=True
        )

        print(f"   Status: {response.status_code}")
        print(f"   Final URL: {response.url}")

        # Salva risposta
        (DATA_DIR / "login_response.html").write_text(response.text, encoding="utf-8")
        print(f"   Risposta salvata in data/login_response.html")

        # 3. Verifica login
        if "logout" in response.text.lower() or "disconnetti" in response.text.lower():
            print("\n*** LOGIN RIUSCITO! ***")

            # Trova username visualizzato
            soup = BeautifulSoup(response.text, "lxml")

            # Cerca link logout per conferma
            logout_link = soup.find("a", href=lambda x: x and "logout" in x)
            if logout_link:
                print(f"   Logout link trovato: {logout_link.get('href')[:50]}...")

            # Mostra cookies
            print(f"\n   Cookies attivi:")
            for cookie in scraper.cookies:
                print(f"   - {cookie.name}: {cookie.value[:30]}...")

            return True

        else:
            print("\n*** LOGIN FALLITO ***")

            # Cerca errori
            soup = BeautifulSoup(response.text, "lxml")
            error_div = soup.find("div", {"class": "error"})
            if error_div:
                print(f"   Errore: {error_div.get_text(strip=True)}")

            # Cerca altri messaggi
            if "password" in response.text.lower() and "errat" in response.text.lower():
                print("   Possibile causa: Password errata")
            if "utente" in response.text.lower() and "non" in response.text.lower():
                print("   Possibile causa: Utente non trovato")

            return False

    except Exception as e:
        print(f"\nECCEZIONE: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_search(scraper, query="2024"):
    """Test ricerca dopo login."""
    print(f"\n3. Test ricerca: '{query}'")

    search_url = f"{BASE_URL}/search.php"

    params = {
        "keywords": f"+{query}",
        "terms": "all",
        "sc": "0",
        "sf": "titleonly",
        "sr": "topics",
        "sk": "t",
        "sd": "d",
        "st": "0",
        "ch": "50",
        "t": "0",
        "submit": "Cerca",
    }

    # Aggiungi alcune categorie
    for fid in [25, 26, 51, 52]:
        params[f"fid[{fid}]"] = str(fid)

    response = scraper.get(search_url, params=params, timeout=30)
    print(f"   Status: {response.status_code}")

    # Salva
    (DATA_DIR / "search_response.html").write_text(response.text, encoding="utf-8")
    print(f"   Risposta salvata in data/search_response.html")

    # Parse risultati
    soup = BeautifulSoup(response.text, "lxml")
    results = soup.find_all("li", {"class": "row"})

    print(f"\n   Trovati {len(results)} risultati")

    if results:
        print("\n   Primi 5 risultati:")
        for i, result in enumerate(results[:5], 1):
            title_link = result.find("a", {"class": "topictitle"})
            if title_link:
                title = title_link.get_text(strip=True)[:60]
                print(f"   {i}. {title}")


if __name__ == "__main__":
    success = test_login()
    sys.exit(0 if success else 1)
