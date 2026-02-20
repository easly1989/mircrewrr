# MIRCrew Proxy per Prowlarr

[![Docker Build](https://github.com/easly1989/mircrewrr/actions/workflows/docker-build.yml/badge.svg)](https://github.com/easly1989/mircrewrr/actions/workflows/docker-build.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Made with Claude Code](https://img.shields.io/badge/Made%20with-Claude%20Code-blueviolet)](https://claude.ai/code)
[![Donate](https://img.shields.io/badge/Donate-PayPal-blue.svg)](https://paypal.me/ruggierocarlo)

Microservizio proxy che permette a [Prowlarr](https://prowlarr.com/) di utilizzare l'indexer [mircrew-releases.org](https://mircrew-releases.org) bypassando la protezione CloudFlare.

---

## Problema Risolto

L'indexer ufficiale di Prowlarr per MIRCrew non funziona a causa della protezione CloudFlare:

```
HTTP request failed: [403:Forbidden] [GET] at [https://mircrew-releases.org/ucp.php?mode=login]
```

**Soluzione**: Questo proxy usa [CloudScraper](https://github.com/VeNoMouS/cloudscraper) per gestire automaticamente CloudFlare e espone un'API Torznab compatibile con Prowlarr.

---

## Funzionalità

- **Bypass CloudFlare** automatico con CloudScraper
- **API Torznab** completa per Prowlarr/Sonarr/Radarr
- **Gestione intelligente del Thanks** - click solo al download, non durante la ricerca
- **Espansione episodi** per serie TV già ringraziate
- **Risultati sintetici** per serie non ancora ringraziate
- **Riconoscimento season pack** con attributi Torznab corretti
- **Multi-platform** Docker (amd64, arm64)

---

## Quick Start

### 1. Configura le credenziali

```bash
cp .env.example .env
nano .env  # Modifica con le tue credenziali MIRCrew
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

Il proxy sarà disponibile su `http://localhost:9696`

### 3. Configura Prowlarr

1. Vai su **Prowlarr** → **Indexers** → **Add Indexer**
2. Seleziona **Generic Torznab**
3. Configura:
   - **Name**: `MIRCrew`
   - **URL**: `http://<IP_SERVER>:9696/api`
   - **API Key**: la chiave configurata in `.env`
   - **Categories**: `2000,5000,5070,3000,7000`
4. Clicca **Test** e poi **Save**

---

## Installazione con Docker

### Opzione 1: Docker Compose (consigliato)

Il proxy richiede [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) per bypassare il Cloudflare managed challenge del sito. Utilizziamo il fork [nodriver](https://github.com/21hsmw/FlareSolverr) che è più efficace contro le protezioni Cloudflare moderne.

```yaml
services:
  flaresolverr:
    image: 21hsmw/flaresolverr:nodriver
    container_name: flaresolverr
    restart: unless-stopped
    ports:
      - "8191:8191"

  mircrew-proxy:
    image: ghcr.io/easly1989/mircrewrr:latest
    container_name: mircrew-proxy
    restart: unless-stopped
    ports:
      - "9696:9696"
    volumes:
      - ./data:/app/data
    environment:
      - MIRCREW_USERNAME=${MIRCREW_USERNAME}
      - MIRCREW_PASSWORD=${MIRCREW_PASSWORD}
      - MIRCREW_API_KEY=${MIRCREW_API_KEY}
      - FLARESOLVERR_URL=http://flaresolverr:8191
    depends_on:
      - flaresolverr
```

```bash
docker compose up -d
```

### Opzione 2: Docker Run

```bash
docker run -d \
  --name mircrew-proxy \
  --restart unless-stopped \
  -p 9696:9696 \
  -v ./data:/app/data \
  -e MIRCREW_USERNAME=your_username \
  -e MIRCREW_PASSWORD=your_password \
  -e MIRCREW_API_KEY=your_api_key \
  ghcr.io/easly1989/mircrewrr:latest
```

---

## Variabili d'Ambiente

| Variabile | Descrizione | Default |
|-----------|-------------|---------|
| `MIRCREW_USERNAME` | Username MIRCrew | *(obbligatorio)* |
| `MIRCREW_PASSWORD` | Password MIRCrew | *(obbligatorio)* |
| `MIRCREW_API_KEY` | API key per Prowlarr | `mircrew-api-key` |
| `MIRCREW_URL` | URL base del sito | `https://mircrew-releases.org` |
| `PROXY_HOST` | Host di ascolto | `0.0.0.0` |
| `PROXY_PORT` | Porta del proxy | `9696` |
| `LOG_LEVEL` | Livello log (`INFO`, `DEBUG`) | `INFO` |

---

## API Endpoints

| Endpoint | Descrizione |
|----------|-------------|
| `GET /` | Info servizio |
| `GET /health` | Health check dettagliato |
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

## Build Locale

Per buildare l'immagine localmente:

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
