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

- **Bypass Cloudflare** tramite Byparr/FlareSolverr (servizio esterno)
- **API Torznab** completa per Prowlarr/Sonarr/Radarr
- **Gestione intelligente del Thanks** — click solo al download, non durante la ricerca
- **Espansione episodi** per serie TV già ringraziate
- **Risultati sintetici** per serie non ancora ringraziate
- **Riconoscimento season pack** con attributi Torznab corretti per Sonarr
- **Cache cookie CF** su disco (12h TTL) — richieste veloci dopo il primo bypass
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

### 3. Configura Prowlarr

1. Vai su **Prowlarr** → **Indexers** → **Add Indexer**
2. Seleziona **Generic Torznab**
3. Configura:
   - **Name**: `MIRCrew`
   - **URL**: `http://<IP_SERVER>:9696`
   - **API Path**: `/api`
   - **API Key**: la chiave configurata in `.env`
   - **Categories**: `2000,5000,5070,3000,7000`
4. Clicca **Test** e poi **Save**

> **Nota:** Non serve configurare tag FlareSolverr/Byparr in Prowlarr. Il bypass CF è gestito internamente dal proxy.

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
| `LOG_LEVEL` | Livello log (`INFO`, `DEBUG`) | `INFO` |

---

## API Endpoints

| Endpoint | Descrizione |
|----------|-------------|
| `GET /` | Info servizio |
| `GET /health` | Health check (mostra stato CF e login) |
| `GET /api?t=caps` | Capabilities Torznab |
| `GET /api?t=search&q=...` | Ricerca generale |
| `GET /api?t=tvsearch&q=...&season=X&ep=Y` | Ricerca serie TV |
| `GET /api?t=movie&q=...` | Ricerca film |
| `GET /download?topic_id=...` | Ottiene magnet link |

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

# Test manuale
curl "http://localhost:9696/api?t=search&q=avatar&apikey=YOUR_API_KEY"

# Health check
curl "http://localhost:9696/health"
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
