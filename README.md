# MIRCrew Proxy per Prowlarr

[![Docker Build](https://github.com/easly1989/mircrewrr/actions/workflows/docker-build.yml/badge.svg)](https://github.com/easly1989/mircrewrr/actions/workflows/docker-build.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Made with Claude Code](https://img.shields.io/badge/Made%20with-Claude%20Code-blueviolet)](https://claude.ai/code)
[![Donate](https://img.shields.io/badge/Donate-PayPal-blue.svg)](https://paypal.me/ruggierocarlo)

Microservizio proxy che permette a [Prowlarr](https://prowlarr.com/) di utilizzare l'indexer [mircrew-releases.org](https://mircrew-releases.org) tramite API Torznab.

---

## Perché serve questo proxy?

MIRCrew è protetto da **Cloudflare Turnstile**, che blocca qualsiasi client automatizzato (requests, cloudscraper, browser headless, ecc.). L'indexer ufficiale Cardigann di Prowlarr fallisce con errori di login anche usando FlareSolverr/Byparr, perché non gestisce correttamente il flusso di autenticazione phpBB (CSRF token).

**Questo proxy risolve entrambi i problemi:**
1. Usa [Byparr](https://github.com/ThePhaseless/Byparr) (o qualsiasi servizio compatibile FlareSolverr) per superare il challenge Cloudflare
2. Gestisce correttamente il login phpBB con CSRF token, session ID e cookie persistence

> **Requisito:** È necessario un servizio **Byparr** (o FlareSolverr) funzionante. Il proxy non può bypassare Cloudflare da solo.

---

## Funzionalità

- **Pannello Admin web** — interfaccia completa per gestire siti, configurazione e log in tempo reale
- **Architettura multi-sito a plugin** — supporto per più siti tramite sistema a manifest
- **Configurazione persistente** — salvata su file JSON, modificabile dall'admin panel
- **Bypass Cloudflare** tramite Byparr/FlareSolverr (servizio esterno)
- **API Torznab** completa per Prowlarr/Sonarr/Radarr
- **Gestione intelligente del Thanks** — click solo al download, non durante la ricerca
- **Espansione episodi** per serie TV già ringraziate
- **Risultati sintetici** per serie non ancora ringraziate
- **Riconoscimento season pack** con attributi Torznab corretti per Sonarr
- **Cache cookie CF** su disco (12h TTL) — richieste veloci dopo il primo bypass
- **Log streaming** in tempo reale via Server-Sent Events
- **Multi-platform** Docker (amd64, arm64)

---

## Quick Start

### 1. Configura le credenziali

```bash
cp .env.example .env
nano .env
```

Contenuto `.env`:
```env
MIRCREW_USERNAME=tuo_username
MIRCREW_PASSWORD=tua_password
MIRCREW_API_KEY=una-chiave-a-tua-scelta
```

### 2. Avvia il proxy

```bash
docker compose up -d
```

Questo avvia sia il proxy che Byparr. Il proxy sarà disponibile su `http://localhost:9696`.

### 3. Accedi al pannello admin

Apri `http://localhost:9696/admin` nel browser per gestire siti, configurazione e visualizzare i log in tempo reale.

Se hai configurato le variabili d'ambiente, il sito `mircrew` viene creato automaticamente al primo avvio.

### 4. Configura Prowlarr

1. Vai su **Prowlarr** → **Indexers** → **Add Indexer**
2. Seleziona **Generic Torznab**
3. Configura:
   - **Name**: `MIRCrew`
   - **URL**: `http://<IP_SERVER>:9696/mircrew`
   - **API Path**: `/api`
   - **API Key**: la chiave configurata in `.env` o nel pannello admin
   - **Categories**: `2000,5000,5070,3000,7000`
4. Clicca **Test** e poi **Save**

> **Nota:** L'URL include il nome del sito (es. `/mircrew`). Se aggiungi più siti dal pannello admin, ognuno avrà il suo endpoint dedicato: `/{nome_sito}/api`.

> **Nota:** Non serve configurare tag FlareSolverr/Byparr in Prowlarr. Il bypass CF è gestito internamente dal proxy.

---

## Pannello Admin

Accessibile su `http://localhost:9696/admin`, il pannello offre quattro sezioni:

### Dashboard
Panoramica dello stato del sistema: siti attivi, stato del bypass Cloudflare, stato login, cache thanks e uptime.

### Sites (Siti)
Gestione completa dei siti configurati:
- **Aggiungi sito** — seleziona un plugin disponibile (es. `mircrew`), configura URL, credenziali e parametri custom
- **Modifica sito** — modifica connessione, mappature categorie, selettori CSS, parametri di ricerca e capabilities XML
- **Abilita/Disabilita** — attiva o disattiva un sito senza eliminarlo
- **Elimina sito** — rimuove completamente la configurazione

La configurazione di ogni sito è organizzata in tab:
| Tab | Contenuto |
|-----|-----------|
| **Connessione** | URL base, username, password |
| **Mappature** | Mappatura Forum ID → Categoria Torznab, Forum IDs per serie TV |
| **Scraping** | Selettori CSS per il parsing delle pagine, parametri di ricerca |
| **Avanzate** | Capabilities XML (editabile con syntax highlighting) |

### Configurazione
Impostazioni globali del proxy:
- **API Key** — chiave di autenticazione per gli endpoint Torznab
- **CF Bypass URL** — URL del servizio Byparr/FlareSolverr
- **CF Bypass Timeout** — timeout in millisecondi
- **Log Level** — livello di log (DEBUG, INFO, WARNING, ERROR)

### Logs
Visualizzazione log in tempo reale via Server-Sent Events, con filtro per livello e funzionalità pausa/ripresa.

---

## Installazione

### Opzione 1: Docker Compose con Byparr incluso (consigliato)

```yaml
services:
  mircrew-proxy:
    image: ghcr.io/easly1989/mircrewrr:latest
    container_name: mircrew-proxy
    restart: unless-stopped
    ports:
      - "9696:9696"
    volumes:
      - ./mircrew-data:/app/data
    environment:
      - MIRCREW_USERNAME=your_username
      - MIRCREW_PASSWORD=your_password
      - MIRCREW_API_KEY=your_api_key
      - FLARESOLVERR_URL=http://byparr:8191
    depends_on:
      - byparr

  byparr:
    image: ghcr.io/thephaseless/byparr:latest
    container_name: byparr
    restart: unless-stopped
```

### Opzione 2: Con Byparr già presente nello stack

Se hai già Byparr nel tuo docker-compose (es. per altri indexer), aggiungi solo il proxy e puntalo al Byparr esistente:

```yaml
  mircrew-proxy:
    image: ghcr.io/easly1989/mircrewrr:latest
    container_name: mircrew-proxy
    restart: unless-stopped
    ports:
      - "9696:9696"
    volumes:
      - ./mircrew-data:/app/data
    environment:
      - MIRCREW_USERNAME=your_username
      - MIRCREW_PASSWORD=your_password
      - MIRCREW_API_KEY=your_api_key
      - FLARESOLVERR_URL=http://byparr:8191
```

Assicurati che `mircrew-proxy` e `byparr` siano sulla stessa rete Docker.

---

## Come Funziona

```
Prowlarr ──HTTP──▶ mircrew-proxy:9696 ──HTTP──▶ Byparr:8191 ──browser──▶ mircrew-releases.org
                   (Torznab API)                (CF bypass)               (Cloudflare + phpBB)
```

1. **Prowlarr** chiama il proxy con una richiesta Torznab standard
2. Il proxy prova a raggiungere MIRCrew con `requests` (veloce)
3. Se Cloudflare blocca (403/503), il proxy chiede a **Byparr** di risolvere il challenge
4. Byparr usa un browser reale per superare Turnstile e ritorna i cookie
5. Il proxy salva i cookie e li riusa per le richieste successive (veloci)
6. I cookie CF sono cachati su disco con TTL di 12 ore

---

## Variabili d'Ambiente

| Variabile | Descrizione | Default |
|-----------|-------------|---------|
| `MIRCREW_USERNAME` | Username MIRCrew | *(obbligatorio)* |
| `MIRCREW_PASSWORD` | Password MIRCrew | *(obbligatorio)* |
| `MIRCREW_API_KEY` | API key per Prowlarr | `mircrew-api-key` |
| `MIRCREW_URL` | URL base del sito | `https://mircrew-releases.org` |
| `FLARESOLVERR_URL` | URL di Byparr/FlareSolverr | `http://byparr:8191` |
| `FLARESOLVERR_TIMEOUT` | Timeout per Byparr (ms) | `60000` |
| `PROXY_PORT` | Porta del proxy | `9696` |
| `ENABLED_SITES` | Siti da attivare all'avvio (separati da virgola) | `mircrew` |
| `LOG_LEVEL` | Livello log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |

> **Nota:** Le variabili d'ambiente vengono usate come configurazione iniziale. Una volta modificata la configurazione dal pannello admin, i valori salvati nel file `config.json` hanno la precedenza sulle variabili d'ambiente.

---

## API Endpoints

### Endpoint Torznab (per sito)

Ogni sito registrato espone i propri endpoint sotto `/{nome_sito}/`:

| Endpoint | Descrizione |
|----------|-------------|
| `GET /{nome_sito}/api?t=caps` | Capabilities Torznab |
| `GET /{nome_sito}/api?t=search&q=...` | Ricerca generale |
| `GET /{nome_sito}/api?t=tvsearch&q=...&season=X&ep=Y` | Ricerca serie TV |
| `GET /{nome_sito}/api?t=movie&q=...` | Ricerca film |
| `GET /{nome_sito}/download?topic_id=...` | Ottiene magnet link |

Esempio con il sito `mircrew`: `GET /mircrew/api?t=search&q=avatar&apikey=YOUR_KEY`

### Endpoint Globali

| Endpoint | Descrizione |
|----------|-------------|
| `GET /` | Info servizio e lista siti attivi |
| `GET /health` | Health check con stato di tutti i siti |
| `GET /admin` | Pannello di amministrazione web |

### Endpoint Admin API

| Endpoint | Descrizione |
|----------|-------------|
| `GET /admin/api/status` | Stato del sistema (versione, uptime, siti) |
| `GET /admin/api/plugins` | Lista plugin disponibili |
| `GET /admin/api/config` | Configurazione globale |
| `PUT /admin/api/config` | Aggiorna configurazione globale |
| `GET /admin/api/sites` | Lista siti configurati |
| `POST /admin/api/sites` | Aggiungi un nuovo sito |
| `PUT /admin/api/sites/<name>` | Aggiorna configurazione sito |
| `DELETE /admin/api/sites/<name>` | Elimina sito |
| `POST /admin/api/sites/<name>/toggle` | Abilita/disabilita sito |
| `GET /admin/api/logs` | Stream log in tempo reale (SSE) |

---

## Architettura Plugin

Il sistema utilizza un'architettura a plugin basata su file `manifest.json`. Ogni plugin si trova in `src/sites/<nome_plugin>/` e contiene:

```
src/sites/mircrew/
├── manifest.json    # Definizione del plugin (schema config, file editabili)
├── site.py          # Implementazione principale (sessione, ricerca, download)
├── parser.py        # Parsing titoli, estrazione magnet, rilevamento episodi
└── constants.py     # Mappature categorie e valori di default
```

Il `manifest.json` definisce:
- **Metadati**: id, nome, descrizione, versione
- **Schema di connessione** (`config_schema`): campi necessari (URL, username, password)
- **Configurazione custom** (`custom_config`): mappature categorie, selettori CSS, parametri di ricerca, capabilities XML — tutti con valori di default
- **File editabili** (`editable_files`): file Python del plugin modificabili dall'admin panel

---

## Categorie Supportate

| Categoria | Torznab ID | Forum MIRCrew |
|-----------|------------|---------------|
| Film | 2000 | 25, 26, 34, 36 |
| Serie TV | 5000 | 51, 52, 29, 30, 31 |
| Anime | 5070 | 33, 35, 37 |
| Musica | 3000 | 45, 46, 47 |
| Libri | 7000 | 39, 40, 41, 42, 43 |

---

## Limitazioni Note

- **Creazione plugin**: non è possibile creare nuovi plugin (cioè nuovi `manifest.json`) dall'interfaccia admin. I plugin devono essere aggiunti manualmente nel filesystem sotto `src/sites/`. Dal pannello admin è possibile solo creare *istanze* (siti) basate su plugin esistenti.
- **Editor codice**: la tab di modifica dei file del plugin (site.py, parser.py, constants.py) nell'interfaccia admin può risultare non funzionante — cliccando sui file l'area di editing rimane vuota.

---

## Comandi Utili

```bash
# Avvia
docker compose up -d

# Vedi i log
docker compose logs -f mircrew-proxy

# Riavvia
docker compose restart mircrew-proxy

# Stop
docker compose down

# Test manuale (nota: endpoint multi-sito)
curl "http://localhost:9696/mircrew/api?t=search&q=avatar&apikey=YOUR_API_KEY"

# Health check
curl "http://localhost:9696/health"

# Pannello admin
# Apri http://localhost:9696/admin nel browser
```

---

## Troubleshooting

| Problema | Soluzione |
|----------|-----------|
| `Cannot connect to Byparr` | Verifica che Byparr sia avviato e raggiungibile all'URL configurato |
| `CF challenge failed` | Byparr potrebbe non riuscire a risolvere il challenge — controlla i log di Byparr |
| `Login failed - check credentials` | Username/password MIRCrew errati |
| `Login form not found` | CF non è stato bypassato — Byparr non ha risolto il challenge |
| Health check mostra `cf_valid: false` | Nessun cookie CF valido — verrà risolto alla prima richiesta |
| Pannello admin: editor codice vuoto | Limitazione nota — usare un editor esterno per modificare i file del plugin |
| `No sites loaded` | Configura almeno un sito dal pannello admin (`/admin`) o tramite variabili d'ambiente |

---

## Build Locale

```bash
git clone https://github.com/easly1989/mircrewrr.git
cd mircrewrr
docker compose build
docker compose up -d
```

---

## Credits

Questo progetto è stato sviluppato interamente con [Claude Code](https://claude.ai/code), l'assistente AI di Anthropic per lo sviluppo software.

---

## Supporta il Progetto

Se questo progetto ti è utile, considera una donazione:

[![PayPal](https://img.shields.io/badge/PayPal-Donate-blue.svg?style=for-the-badge&logo=paypal)](https://paypal.me/ruggierocarlo)

---

## Licenza

[MIT License](LICENSE)
