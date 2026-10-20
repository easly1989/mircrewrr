# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python 3.11 / Flask **Torznab proxy** that lets Prowlarr (and through it Sonarr/Radarr) use sites that Prowlarr cannot scrape directly. The only site today is MIRCrew (`mircrew-releases.org`), a phpBB forum behind Cloudflare Turnstile with a login and a "thanks" wall before magnets are visible. Cloudflare is solved by an external FlareSolverr-compatible service (Byparr) reached through `FLARESOLVERR_URL`. README and UI text are in Italian.

## Commands

```bash
docker compose up -d                       # proxy + Byparr
docker compose logs -f mircrew-proxy
curl "http://localhost:9696/mircrew/api?t=search&q=avatar&apikey=<key>"
PYTHONPATH=src python src/main.py          # run locally (needs the env vars from .env.example)
```

The image default port is 9696, but the deployment on the NAS overrides it to **9697** (`PROXY_PORT`) because Prowlarr owns 9696.

## Architecture

- `src/main.py` builds the global config, discovers plugins, and registers every enabled site on a single `TorznabServer`.
- `src/torznab/` is site-agnostic: `server.py` defines the `BaseSite` interface (`search`, `download`, `get_capabilities_xml`, `health_info`) and mounts `/<site>/api`, `/<site>/download` and debug routes per registered site; `models.py` renders `TorznabResult` to RSS items. Downloads answer with a 302 to the magnet.
- `src/sites/<plugin>/` is a plugin: `manifest.json` declares the config schema, the editable defaults (category map, CSS selectors, search params, caps XML) and which files the admin panel may edit. `site.py` implements `BaseSite`; `parser.py` holds title normalisation, relevance scoring, season/episode and magnet extraction.
- `src/session/byparr.py` owns the HTTP session: plain `requests` first, Byparr on a 403/503 challenge, CF cookies cached on disk with a TTL, then the phpBB login (CSRF token, session cookies).
- `src/admin/` is the web panel at `/admin`: `config_store.py` persists global and per-site config as JSON in the data dir, where saved values **override** env vars after the first save; `log_handler.py` feeds the live log stream (SSE); `routes.py` hot-registers and unregisters sites on the running Flask app.

Behaviour that spans files:

- Search in `site.py` widens in stages (`terms=all`, Italian apostrophe variant, `terms=any`, keyword subsets, no forum filter) and then sorts by relevance. An empty query searches the current year, which is what Prowlarr's test and Sonarr's RSS sync hit.
- The "thanks" click happens only on download, never during search. TV threads that are not thanked yet produce synthetic per-episode results, expanded after the thanks.

## Agent skills

### Issue tracker

GitHub Issues on `easly1989/mircrewrr`, via `gh`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the root. See `docs/agents/domain.md`.

### Token tools

CBM: call `index_repository` once, then `search_graph` / `trace_path` before reading files.
