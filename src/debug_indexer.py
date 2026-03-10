#!/usr/bin/env python3
"""
MIRCrew Indexer Debug Tool

Strumento per testare e debuggare l'autenticazione e la ricerca
sul sito mircrew-releases.org per Prowlarr.
"""

import os
import sys
import json
import logging
import pickle
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

# Setup logging
console = Console()
logging.basicConfig(
    level=os.getenv("DEBUG_LEVEL", "INFO"),
    format="%(message)s",
    handlers=[RichHandler(console=console, rich_tracebacks=True)]
)
logger = logging.getLogger("mircrew")

# Percorsi
DATA_DIR = Path("/app/data")
COOKIES_FILE = DATA_DIR / "cookies.pkl"


class MircrewDebugger:
    """Classe principale per il debugging dell'indexer MIRCrew."""

    DEFAULT_HEADERS = {
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
        "Cache-Control": "max-age=0",
    }

    def __init__(self):
        self.base_url = os.getenv("MIRCREW_BASE_URL", "https://mircrew-releases.org")
        self.username = os.getenv("MIRCREW_USERNAME", "")
        self.password = os.getenv("MIRCREW_PASSWORD", "")
        self.session: Optional[requests.Session] = None

        # Crea directory dati se non esiste
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _log_response(self, response: requests.Response, title: str = "Response"):
        """Log dettagliato della risposta HTTP."""
        table = Table(title=title)
        table.add_column("Campo", style="cyan")
        table.add_column("Valore", style="green")

        table.add_row("URL", str(response.url))
        table.add_row("Status Code", f"{response.status_code} {response.reason}")
        table.add_row("Content-Type", response.headers.get("Content-Type", "N/A"))
        table.add_row("Content-Length", response.headers.get("Content-Length", "N/A"))
        table.add_row("Server", response.headers.get("Server", "N/A"))
        table.add_row("Set-Cookie", str(response.headers.get("Set-Cookie", "N/A"))[:100])

        console.print(table)

        # Log headers completi a livello DEBUG
        logger.debug(f"Response Headers: {dict(response.headers)}")

        # Salva risposta per analisi
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        response_file = DATA_DIR / f"response_{timestamp}.html"
        response_file.write_text(response.text, encoding="utf-8")
        logger.info(f"Risposta salvata in: {response_file}")

    def test_basic_request(self) -> bool:
        """Test 1: Richiesta HTTP base con requests."""
        console.print(Panel("[bold blue]Test 1: Richiesta HTTP base (requests)[/bold blue]"))

        try:
            self.session = requests.Session()
            self.session.headers.update(self.DEFAULT_HEADERS)

            # Prima richiesta alla homepage
            logger.info(f"GET {self.base_url}")
            response = self.session.get(self.base_url, timeout=30)
            self._log_response(response, "Homepage Response")

            if response.status_code == 200:
                console.print("[green]Homepage accessibile![/green]")
                return True
            elif response.status_code == 403:
                console.print("[red]Errore 403 - Accesso negato (probabilmente protezione anti-bot)[/red]")
                return False
            else:
                console.print(f"[yellow]Status code inatteso: {response.status_code}[/yellow]")
                return False

        except Exception as e:
            logger.error(f"Errore nella richiesta: {e}")
            return False

    def test_cloudscraper(self) -> bool:
        """Test 2: Richiesta con cloudscraper (bypass CloudFlare)."""
        console.print(Panel("[bold blue]Test 2: Richiesta con CloudScraper[/bold blue]"))

        try:
            import cloudscraper

            scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'desktop': True
                }
            )

            logger.info(f"GET {self.base_url} (cloudscraper)")
            response = scraper.get(self.base_url, timeout=30)
            self._log_response(response, "CloudScraper Response")

            if response.status_code == 200:
                console.print("[green]CloudScraper funziona![/green]")
                self.session = scraper
                return True
            else:
                console.print(f"[red]CloudScraper fallito: {response.status_code}[/red]")
                return False

        except Exception as e:
            logger.error(f"Errore CloudScraper: {e}")
            return False

    def test_login_page(self) -> Dict[str, Any]:
        """Test 3: Accesso alla pagina di login e analisi form."""
        console.print(Panel("[bold blue]Test 3: Analisi pagina login[/bold blue]"))

        result = {"success": False, "form_data": {}, "errors": []}

        if not self.session:
            result["errors"].append("Nessuna sessione attiva")
            return result

        try:
            login_url = urljoin(self.base_url, "ucp.php?mode=login")
            logger.info(f"GET {login_url}")

            response = self.session.get(login_url, timeout=30)
            self._log_response(response, "Login Page Response")

            if response.status_code != 200:
                result["errors"].append(f"Status code: {response.status_code}")
                return result

            # Parse HTML
            soup = BeautifulSoup(response.text, "lxml")

            # Cerca form di login
            login_form = soup.find("form", {"id": "login"})
            if not login_form:
                # Prova a cercare altri form
                login_form = soup.find("form", {"action": lambda x: x and "login" in x.lower()})

            if login_form:
                console.print("[green]Form di login trovato![/green]")

                # Estrai campi del form
                inputs = login_form.find_all("input")
                form_data = {}
                for inp in inputs:
                    name = inp.get("name", "")
                    value = inp.get("value", "")
                    inp_type = inp.get("type", "text")
                    if name:
                        form_data[name] = {"value": value, "type": inp_type}

                result["form_data"] = form_data
                result["form_action"] = login_form.get("action", "")
                result["form_method"] = login_form.get("method", "POST")

                # Mostra campi trovati
                table = Table(title="Campi Form Login")
                table.add_column("Nome", style="cyan")
                table.add_column("Tipo", style="yellow")
                table.add_column("Valore Default", style="green")

                for name, data in form_data.items():
                    table.add_row(name, data["type"], data["value"][:50] if data["value"] else "")

                console.print(table)
                result["success"] = True
            else:
                result["errors"].append("Form di login non trovato")

                # Analizza la pagina per capire il problema
                if "cloudflare" in response.text.lower():
                    result["errors"].append("Rilevata protezione CloudFlare")
                if "captcha" in response.text.lower():
                    result["errors"].append("Rilevato CAPTCHA")

        except Exception as e:
            result["errors"].append(str(e))
            logger.error(f"Errore analisi login: {e}")

        return result

    def test_login(self) -> bool:
        """Test 4: Esegui login effettivo."""
        console.print(Panel("[bold blue]Test 4: Login effettivo[/bold blue]"))

        if not self.username or not self.password:
            console.print("[red]Username o password non configurati![/red]")
            console.print("Configura MIRCREW_USERNAME e MIRCREW_PASSWORD nelle variabili d'ambiente")
            return False

        if not self.session:
            console.print("[red]Nessuna sessione attiva[/red]")
            return False

        try:
            # Prima ottieni la pagina di login per eventuali token CSRF
            login_url = urljoin(self.base_url, "ucp.php?mode=login")
            response = self.session.get(login_url, timeout=30)

            soup = BeautifulSoup(response.text, "lxml")
            login_form = soup.find("form", {"id": "login"})

            if not login_form:
                console.print("[red]Form di login non trovato[/red]")
                return False

            # Prepara dati login
            form_data = {
                "username": self.username,
                "password": self.password,
                "autologin": "on",
                "viewonline": "on",
                "login": "Login",  # o "Accedi" in italiano
            }

            # Aggiungi eventuali campi hidden (CSRF, SID, etc.)
            for inp in login_form.find_all("input", {"type": "hidden"}):
                name = inp.get("name")
                value = inp.get("value", "")
                if name and name not in form_data:
                    form_data[name] = value

            # Cerca anche il campo redirect
            redirect_inp = login_form.find("input", {"name": "redirect"})
            if redirect_inp:
                form_data["redirect"] = redirect_inp.get("value", "index.php")

            # Ottieni action URL
            action = login_form.get("action", "ucp.php?mode=login")
            if action.startswith("./"):
                action = action[2:]
            post_url = urljoin(self.base_url, action)

            logger.info(f"POST {post_url}")
            logger.debug(f"Form data keys: {list(form_data.keys())}")

            # Aggiungi Referer header
            headers = {"Referer": login_url}

            response = self.session.post(
                post_url,
                data=form_data,
                headers=headers,
                timeout=30,
                allow_redirects=True
            )

            self._log_response(response, "Login Response")

            # Verifica login
            if "logout" in response.text.lower() or "disconnetti" in response.text.lower():
                console.print("[green]Login effettuato con successo![/green]")

                # Salva cookies
                self._save_cookies()
                return True
            elif "password errata" in response.text.lower() or "invalid" in response.text.lower():
                console.print("[red]Credenziali non valide[/red]")
                return False
            else:
                console.print("[yellow]Stato login incerto - controlla i log[/yellow]")

                # Cerca messaggi di errore
                soup = BeautifulSoup(response.text, "lxml")
                error_div = soup.find("div", {"class": "error"})
                if error_div:
                    console.print(f"[red]Errore: {error_div.get_text(strip=True)}[/red]")

                return False

        except Exception as e:
            logger.error(f"Errore login: {e}")
            return False

    def test_search(self, query: str = "test") -> bool:
        """Test 5: Esegui una ricerca."""
        console.print(Panel(f"[bold blue]Test 5: Ricerca '{query}'[/bold blue]"))

        if not self.session:
            console.print("[red]Nessuna sessione attiva[/red]")
            return False

        try:
            search_url = urljoin(self.base_url, "search.php")

            params = {
                "keywords": query,
                "terms": "all",
                "sc": "0",
                "sf": "titleonly",
                "sr": "topics",
                "sk": "t",
                "sd": "d",
                "st": "0",
                "ch": "300",
                "t": "0",
                "submit": "Cerca",
            }

            # Aggiungi categorie (forum IDs)
            category_ids = [25, 26, 51, 52, 29, 30, 31, 33, 34, 35, 36, 37, 39, 40, 41, 42, 43, 45, 46, 47]
            for fid in category_ids:
                params[f"fid[{fid}]"] = fid

            logger.info(f"GET {search_url}")
            response = self.session.get(search_url, params=params, timeout=30)
            self._log_response(response, "Search Response")

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "lxml")

                # Cerca risultati
                results = soup.find_all("li", {"class": "row"})
                console.print(f"[green]Trovati {len(results)} risultati[/green]")

                # Mostra primi 5 risultati
                if results:
                    table = Table(title="Primi risultati")
                    table.add_column("Titolo", style="cyan")
                    table.add_column("Link", style="blue")

                    for result in results[:5]:
                        title_link = result.find("a", {"class": "topictitle"})
                        if title_link:
                            table.add_row(
                                title_link.get_text(strip=True)[:60],
                                title_link.get("href", "")[:50]
                            )

                    console.print(table)

                return len(results) > 0
            else:
                console.print(f"[red]Ricerca fallita: {response.status_code}[/red]")
                return False

        except Exception as e:
            logger.error(f"Errore ricerca: {e}")
            return False

    def test_selenium(self) -> bool:
        """Test 6: Usa Selenium con undetected-chromedriver."""
        console.print(Panel("[bold blue]Test 6: Selenium (undetected-chromedriver)[/bold blue]"))

        try:
            import undetected_chromedriver as uc
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            options = uc.ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")

            logger.info("Avvio Chrome undetected...")
            driver = uc.Chrome(options=options)

            try:
                driver.get(self.base_url)

                # Attendi caricamento pagina
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )

                console.print(f"[green]Pagina caricata: {driver.title}[/green]")

                # Salva screenshot
                screenshot_path = DATA_DIR / "selenium_screenshot.png"
                driver.save_screenshot(str(screenshot_path))
                console.print(f"Screenshot salvato: {screenshot_path}")

                # Salva HTML
                html_path = DATA_DIR / "selenium_page.html"
                html_path.write_text(driver.page_source, encoding="utf-8")

                # Se abbiamo credenziali, prova login
                if self.username and self.password:
                    login_url = urljoin(self.base_url, "ucp.php?mode=login")
                    driver.get(login_url)

                    # Attendi form
                    username_field = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.NAME, "username"))
                    )

                    username_field.send_keys(self.username)
                    driver.find_element(By.NAME, "password").send_keys(self.password)

                    # Cerca e clicca submit
                    submit_btn = driver.find_element(By.CSS_SELECTOR, "input[type='submit'][name='login']")
                    submit_btn.click()

                    # Attendi redirect
                    import time
                    time.sleep(3)

                    # Verifica login
                    if "logout" in driver.page_source.lower():
                        console.print("[green]Login Selenium riuscito![/green]")

                        # Estrai cookies per uso successivo
                        cookies = driver.get_cookies()
                        cookies_path = DATA_DIR / "selenium_cookies.json"
                        cookies_path.write_text(json.dumps(cookies, indent=2))
                        console.print(f"Cookies salvati: {cookies_path}")

                return True

            finally:
                driver.quit()

        except ImportError:
            console.print("[yellow]undetected-chromedriver non disponibile[/yellow]")
            return False
        except Exception as e:
            logger.error(f"Errore Selenium: {e}")
            return False

    def _save_cookies(self):
        """Salva cookies della sessione."""
        if self.session:
            COOKIES_FILE.write_bytes(pickle.dumps(self.session.cookies))
            logger.info(f"Cookies salvati in {COOKIES_FILE}")

    def _load_cookies(self) -> bool:
        """Carica cookies salvati."""
        if COOKIES_FILE.exists():
            try:
                cookies = pickle.loads(COOKIES_FILE.read_bytes())
                if self.session:
                    self.session.cookies = cookies
                    logger.info("Cookies caricati")
                    return True
            except Exception as e:
                logger.warning(f"Errore caricamento cookies: {e}")
        return False

    def run_all_tests(self):
        """Esegue tutti i test in sequenza."""
        console.print(Panel(
            "[bold green]MIRCrew Indexer Debug Tool[/bold green]\n"
            f"Base URL: {self.base_url}\n"
            f"Username: {'***' if self.username else '[non configurato]'}",
            title="Configurazione"
        ))

        results = {}

        # Test 1: Richiesta base
        results["basic_request"] = self.test_basic_request()

        # Test 2: CloudScraper (se test 1 fallisce con 403)
        if not results["basic_request"]:
            results["cloudscraper"] = self.test_cloudscraper()
        else:
            results["cloudscraper"] = "skipped"

        # Test 3: Analisi pagina login
        login_analysis = self.test_login_page()
        results["login_page"] = login_analysis["success"]

        # Test 4: Login effettivo
        if login_analysis["success"] and self.username:
            results["login"] = self.test_login()
        else:
            results["login"] = "skipped"

        # Test 5: Ricerca
        if results.get("login") == True:
            results["search"] = self.test_search("2024")
        else:
            results["search"] = "skipped"

        # Test 6: Selenium (opzionale)
        use_selenium = os.getenv("USE_SELENIUM", "false").lower() == "true"
        if use_selenium:
            results["selenium"] = self.test_selenium()
        else:
            results["selenium"] = "skipped"

        # Riepilogo
        console.print("\n")
        table = Table(title="Riepilogo Test")
        table.add_column("Test", style="cyan")
        table.add_column("Risultato", style="bold")

        for test_name, result in results.items():
            if result == True:
                status = "[green]PASSED[/green]"
            elif result == False:
                status = "[red]FAILED[/red]"
            else:
                status = "[yellow]SKIPPED[/yellow]"
            table.add_row(test_name, status)

        console.print(table)

        return results


def main():
    """Entry point."""
    debugger = MircrewDebugger()

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "basic":
            debugger.test_basic_request()
        elif command == "cloudscraper":
            debugger.test_cloudscraper()
        elif command == "login":
            debugger.test_basic_request() or debugger.test_cloudscraper()
            debugger.test_login_page()
            debugger.test_login()
        elif command == "search":
            query = sys.argv[2] if len(sys.argv) > 2 else "2024"
            debugger.test_basic_request() or debugger.test_cloudscraper()
            debugger.test_login()
            debugger.test_search(query)
        elif command == "selenium":
            debugger.test_selenium()
        else:
            console.print(f"Comando sconosciuto: {command}")
            console.print("Comandi disponibili: basic, cloudscraper, login, search, selenium")
    else:
        debugger.run_all_tests()


if __name__ == "__main__":
    main()
