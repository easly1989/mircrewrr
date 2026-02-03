# MIRCrew Indexer Debug Tool

Container Docker per il debugging e lo sviluppo di un indexer personalizzato per [Prowlarr](https://prowlarr.com/) per il sito [mircrew-releases.org](https://mircrew-releases.org).

## Problema

L'indexer ufficiale di Prowlarr per MIRCrew non funziona, restituendo errore:
```
Unable to connect to indexer, check the log above the ValidationFailure for more details.
HTTP request failed: [403:Forbidden] [GET] at [https://mircrew-releases.org/ucp.php?mode=login]
```

Questo container fornisce strumenti per:
- Testare l'autenticazione al sito
- Analizzare le risposte HTTP
- Bypassare eventuali protezioni anti-bot (CloudFlare, etc.)
- Sviluppare un indexer funzionante

## Requisiti

- Docker
- Docker Compose
- Account su mircrew-releases.org

## Quick Start

1. **Clona il repository e configura le credenziali:**
   ```bash
   git clone <repo-url>
   cd mircrewrr
   cp .env.example .env
   # Modifica .env con le tue credenziali
   ```

2. **Build del container:**
   ```bash
   ./run.sh build
   # oppure
   docker-compose build
   ```

3. **Esegui i test:**
   ```bash
   ./run.sh run
   ```

## Comandi Disponibili

| Comando | Descrizione |
|---------|-------------|
| `./run.sh build` | Costruisce l'immagine Docker |
| `./run.sh run` | Esegue tutti i test di connessione |
| `./run.sh basic` | Test richiesta HTTP base |
| `./run.sh cloudscraper` | Test con CloudScraper (bypass CloudFlare) |
| `./run.sh login` | Test procedura di login |
| `./run.sh search "query"` | Test ricerca torrent |
| `./run.sh selenium` | Test con browser headless |
| `./run.sh shell` | Apre shell interattiva nel container |
| `./run.sh proxy` | Avvia mitmproxy per analisi traffico |
| `./run.sh clean` | Rimuove container e immagini |

## Struttura Progetto

```
mircrewrr/
├── Dockerfile              # Container con Python e tools
├── docker-compose.yml      # Orchestrazione servizi
├── requirements.txt        # Dipendenze Python
├── run.sh                  # Script helper
├── .env.example            # Template variabili d'ambiente
├── src/
│   └── debug_indexer.py    # Script principale di debug
├── config/
│   └── mircrew_original.yml # Indexer YAML originale (riferimento)
└── data/                   # Volume per dati persistenti
    ├── cookies.pkl         # Cookies sessione salvati
    ├── response_*.html     # Risposte HTTP salvate
    └── selenium_*.png      # Screenshot browser
```

## Variabili d'Ambiente

| Variabile | Descrizione | Default |
|-----------|-------------|---------|
| `MIRCREW_USERNAME` | Username account MIRCrew | (obbligatorio) |
| `MIRCREW_PASSWORD` | Password account MIRCrew | (obbligatorio) |
| `MIRCREW_BASE_URL` | URL base del sito | `https://mircrew-releases.org` |
| `DEBUG_LEVEL` | Livello log (DEBUG/INFO/WARNING/ERROR) | `INFO` |
| `USE_SELENIUM` | Abilita test Selenium | `false` |

## Analisi Traffico con mitmproxy

Per analizzare il traffico HTTP in dettaglio:

```bash
./run.sh proxy
```

Poi accedi a http://localhost:8081 per la web interface.

## Output Test

I test salvano automaticamente in `data/`:
- **response_*.html**: Risposte HTTP per analisi
- **cookies.pkl**: Cookies di sessione (riutilizzabili)
- **selenium_screenshot.png**: Screenshot del browser
- **selenium_cookies.json**: Cookies estratti da Selenium

## Sviluppo Indexer

Una volta identificata la soluzione per il 403, l'obiettivo è creare:
1. Un microservizio proxy che gestisca l'autenticazione
2. Un indexer YAML aggiornato per Prowlarr
3. Eventuale integrazione con FlareSolverr

## Licenza

MIT License
