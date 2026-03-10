#!/usr/bin/env python3
"""
Test login effettivo con CloudScraper - versione corretta per phpBB
"""

import os
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlencode

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
        return None

    # Crea scraper
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True,
        }
    )

    try:
        # 1. Prima visita alla homepage per inizializzare i cookies
        print(f"\n1. GET {BASE_URL} (inizializzazione sessione)")
        response = scraper.get(BASE_URL, timeout=30)
        print(f"   Status: {response.status_code}")
        cookies_list = [c.name for c in scraper.cookies]
        print(f"   Cookies: {cookies_list}")

        # Piccola pausa
        time.sleep(1)

        # 2. Ottieni pagina login
        login_url = f"{BASE_URL}/ucp.php?mode=login"
        print(f"\n2. GET {login_url}")
        response = scraper.get(login_url, timeout=30)
        print(f"   Status: {response.status_code}")

        if response.status_code != 200:
            print(f"   ERRORE: {response.status_code}")
            return None

        # Salva per debug
        (DATA_DIR / "debug_login_form.html").write_text(response.text, encoding="utf-8")

        # Parse form
        soup = BeautifulSoup(response.text, "lxml")
        login_form = soup.find("form", {"id": "login"})

        if not login_form:
            print("   ERRORE: Form login non trovato")
            return None

        print("   Form login trovato")

        # Estrai tutti i campi hidden nell'ordine esatto in cui appaiono
        form_fields = []

        for inp in login_form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue

            inp_type = inp.get("type", "text")
            value = inp.get("value", "")

            if inp_type == "hidden":
                form_fields.append((name, value))
                print(f"   Hidden: {name}={value[:30]}..." if len(value) > 30 else f"   Hidden: {name}={value}")

        # Trova il SID per l'URL del POST
        sid = ""
        creation_time = ""
        form_token = ""
        redirect_value = "index.php"

        for name, value in form_fields:
            if name == "sid":
                sid = value
            elif name == "creation_time":
                creation_time = value
            elif name == "form_token":
                form_token = value
            elif name == "redirect":
                redirect_value = value  # prendi l'ultimo

        # Costruisci form_data nel formato che phpBB si aspetta
        # Usa un dizionario normale poiché requests lo serializza correttamente
        form_data = {
            "username": USERNAME,
            "password": PASSWORD,
            "redirect": redirect_value,
            "creation_time": creation_time,
            "form_token": form_token,
            "sid": sid,
            "login": "Login"
        }

        # NON includere autologin e viewonline se sono checkbox non selezionati
        # (ma l'utente probabilmente vuole ricordare il login)
        # form_data["autologin"] = "on"

        print(f"\n   Form data keys: {list(form_data.keys())}")

        # 3. POST login - IMPORTANTE: include sid nella query string
        post_url = f"{BASE_URL}/ucp.php?mode=login&sid={sid}"
        print(f"\n3. POST {post_url}")

        # Headers come un browser reale
        headers = {
            "Referer": login_url,
            "Origin": BASE_URL,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "max-age=0",
            "Upgrade-Insecure-Requests": "1",
        }

        # Piccola pausa prima del POST
        time.sleep(0.5)

        response = scraper.post(
            post_url,
            data=form_data,
            headers=headers,
            timeout=30,
            allow_redirects=True
        )

        print(f"   Status: {response.status_code}")
        print(f"   Final URL: {response.url}")
        print(f"   Cookies: {[c.name for c in scraper.cookies]}")

        # Controlla se c'è stato un redirect
        if response.history:
            print(f"   Redirect chain: {[r.url for r in response.history]}")

        # Salva risposta
        (DATA_DIR / "login_response.html").write_text(response.text, encoding="utf-8")

        # 4. Verifica login
        soup = BeautifulSoup(response.text, "lxml")

        # Cerca link logout
        logout_link = soup.find("a", href=lambda x: x and "logout" in x.lower() if x else False)
        error_div = soup.find("div", {"class": "error"})

        if logout_link or ("mode=logout" in response.text):
            print("\n*** LOGIN RIUSCITO! ***")
            return scraper

        elif error_div:
            error_text = error_div.get_text(strip=True)
            print(f"\n*** LOGIN FALLITO ***")
            print(f"   Errore: {error_text}")

            # Prova a capire il problema
            if "non valido" in error_text.lower():
                print("\n   DEBUG: Il token CSRF non corrisponde.")
                print("   Possibili cause:")
                print("   - Cookie di sessione cambiato tra GET e POST")
                print("   - Protezione anti-bot attiva")
                print("   - Tempo scaduto per il form")

            return None

        else:
            title = soup.find("title")
            print(f"\n   Page title: {title.get_text() if title else 'N/A'}")

            # Verifica se siamo loggati controllando il testo
            page_text = response.text.lower()
            if USERNAME.lower() in page_text:
                print(f"\n   Username trovato nella pagina - possibile login riuscito")
                return scraper

            print("\n*** STATO INCERTO ***")
            return None

    except Exception as e:
        print(f"\nECCEZIONE: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_search(scraper, query="2024"):
    """Test ricerca dopo login."""
    print(f"\n4. Test ricerca: '{query}'")

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

    for fid in [25, 26, 51, 52]:
        params[f"fid[{fid}]"] = str(fid)

    response = scraper.get(search_url, params=params, timeout=30)
    print(f"   Status: {response.status_code}")

    (DATA_DIR / "search_response.html").write_text(response.text, encoding="utf-8")

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

    return len(results) > 0


if __name__ == "__main__":
    scraper = test_login()

    if scraper:
        test_search(scraper, "2024")
        sys.exit(0)
    else:
        sys.exit(1)
