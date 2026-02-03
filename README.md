# MIRCrew Proxy per Prowlarr

Microservizio proxy che permette a [Prowlarr](https://prowlarr.com/) di utilizzare l'indexer [mircrew-releases.org](https://mircrew-releases.org) bypassando la protezione CloudFlare.

## Problema Risolto

L'indexer ufficiale di Prowlarr per MIRCrew non funziona a causa della protezione CloudFlare:
```
HTTP request failed: [403:Forbidden] [GET] at [https://mircrew-releases.org/ucp.php?mode=login]
```

**Soluzione**: Questo proxy usa CloudScraper per gestire automaticamente CloudFlare e espone un'API Torznab compatibile con Prowlarr.

## Quick Start

### 1. Configura le credenziali

```bash
cp .env.example .env
# Modifica .env con le tue credenziali MIRCrew
```

Contenuto `.env`:
```
MIRCREW_USERNAME=tuo_username
MIRCREW_PASSWORD=tua_password
MIRCREW_API_KEY=una-chiave-a-tua-scelta
```

### 2. Avvia il proxy

```bash
docker-compose up -d mircrew-proxy
```

Il proxy sarà disponibile su `http://localhost:9696`

### 3. Configura Prowlarr

1. Vai su **Prowlarr** → **Indexers** → **Add Indexer**
2. Seleziona **Generic Torznab**
3. Configura:
   - **Name**: MIRCrew
   - **URL**: `http://localhost:9696/api` (o l'IP del server Docker)
   - **API Key**: la chiave configurata in `.env`
   - **Categories**: 2000 (Movies), 5000 (TV), 5070 (Anime), 3000 (Audio), 7000 (Books)

4. Clicca **Test** e poi **Save**

## API Endpoints

| Endpoint | Descrizione |
|----------|-------------|
| `GET /` | Health check |
| `GET /health` | Health check dettagliato |
| `GET /api?t=caps` | Capabilities (Torznab) |
| `GET /api?t=search&q=...` | Ricerca generale |
| `GET /api?t=tvsearch&q=...` | Ricerca serie TV |
| `GET /api?t=movie&q=...` | Ricerca film |
| `GET /download?url=...` | Ottiene magnet link |

## Struttura Progetto

```
mircrewrr/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── src/
│   ├── proxy_server.py     # Server proxy principale
│   ├── test_login.py       # Script test login
│   ├── quick_test.py       # Test rapidi
│   └── debug_indexer.py    # Debug completo
├── config/
│   └── mircrew_original.yml
└── data/                   # Logs e cache
```

## Variabili d'Ambiente

| Variabile | Descrizione | Default |
|-----------|-------------|---------|
| `MIRCREW_USERNAME` | Username MIRCrew | (obbligatorio) |
| `MIRCREW_PASSWORD` | Password MIRCrew | (obbligatorio) |
| `MIRCREW_URL` | URL base del sito | `https://mircrew-releases.org` |
| `MIRCREW_API_KEY` | API key per Prowlarr | `mircrew-api-key` |
| `PROXY_PORT` | Porta del proxy | `9696` |
| `LOG_LEVEL` | Livello log | `INFO` |

## Comandi Utili

```bash
# Avvia solo il proxy
docker-compose up -d mircrew-proxy

# Vedi i log
docker-compose logs -f mircrew-proxy

# Riavvia
docker-compose restart mircrew-proxy

# Test manuale
curl "http://localhost:9696/api?t=search&q=avatar&apikey=mircrew-api-key"

# Stop
docker-compose down
```

## Debug

Per debug avanzato:

```bash
# Shell interattiva
docker-compose run --rm mircrew-debug /bin/bash

# Test login manuale
MIRCREW_USERNAME=user MIRCREW_PASSWORD=pass python3 src/test_login.py

# Analisi traffico con mitmproxy
docker-compose --profile proxy up mitmproxy
# Accedi a http://localhost:8081
```

## Categorie Supportate

| Forum ID | Categoria | Torznab ID |
|----------|-----------|------------|
| 25, 26 | Film | 2000 |
| 51, 52 | Serie TV | 5000 |
| 29, 30, 31 | Documentari/TV Show | 5000 |
| 33, 35, 37 | Anime/Cartoon Serie | 5070 |
| 34, 36 | Anime/Cartoon Film | 2000 |
| 39-43 | Libri/Edicola | 7000 |
| 45-47 | Musica | 3000 |

## Licenza

MIT License
