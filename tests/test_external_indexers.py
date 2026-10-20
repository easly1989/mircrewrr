"""External indexers in the admin panel: status/log proxy, config, auth, secret handling."""

import json
import threading

import pytest
from werkzeug.serving import make_server

from admin.auth import init_auth
from admin.config_store import MASK, ConfigStore
from admin.routes import admin_bp, init_admin
from config import Config, parse_external_indexers
from fake_indexer import KEY as INDEXER_KEY, create_app
from torznab.server import TorznabServer

PANEL_KEY = "panel-key"
DEAD_URL = "http://127.0.0.1:1"  # nothing listens here


@pytest.fixture
def indexer():
    """The fake indexer on a real local port, so the proxy makes real HTTP calls."""
    app = create_app()
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def make_panel(tmp_path, indexers):
    config = Config(data_dir=tmp_path, api_key=PANEL_KEY, external_indexers=indexers)
    store = ConfigStore(tmp_path / "config.json", config)
    server = TorznabServer(api_key=PANEL_KEY)
    init_auth(server.app, server, tmp_path)
    init_admin(server, store, {}, {})
    server.app.register_blueprint(admin_bp)
    client = server.app.test_client()
    assert client.post("/admin/login", data={"api_key": PANEL_KEY}).status_code == 302
    return client, store, server


@pytest.fixture
def panel(tmp_path, indexer):
    indexers = [
        {"name": "animeworld", "url": indexer, "api_key": INDEXER_KEY},
        {"name": "ghost", "url": DEAD_URL, "api_key": "x"},
        {"name": "badkey", "url": indexer, "api_key": "wrong"},
    ]
    return make_panel(tmp_path, indexers)[0]


def test_status_happy_path(panel):
    r = panel.get("/admin/api/external/animeworld/status")
    assert r.status_code == 200
    d = r.get_json()
    assert d["up"] is True and d["version"] == "1.2.0" and d["status"] == "ok"
    assert d["uptime_seconds"] == 108125
    assert d["queue"][0]["name"] == "Frieren S01E03 [SUB ITA]" and d["queue"][0]["progress"] == 0.42
    assert "future_field" not in d
    assert INDEXER_KEY not in json.dumps(d)


def test_unreachable_indexer_is_down_not_500(panel):
    r = panel.get("/admin/api/external/ghost/status")
    assert r.status_code == 200
    d = r.get_json()
    assert d["up"] is False and d["error"] == "Connection failed"
    # The rest of the panel keeps working.
    assert panel.get("/admin/api/external/animeworld/status").get_json()["up"] is True


def test_rejected_api_key_reported(panel):
    d = panel.get("/admin/api/external/badkey/status").get_json()
    assert d["up"] is False and "rejected" in d["error"]


def test_unknown_indexer_404(panel):
    assert panel.get("/admin/api/external/nope/status").status_code == 404
    assert panel.get("/admin/api/external/nope/logs").status_code == 404


def test_logs_proxy_streams_entries(panel):
    r = panel.get("/admin/api/external/animeworld/logs")
    assert r.status_code == 200 and r.mimetype == "text/event-stream"
    body = r.get_data(as_text=True)
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert len(events[0]) == 2 and events[0][1]["level"] == "ERROR"  # initial batch
    assert events[1][0]["message"] == "episode done"  # then a live entry
    assert ": keepalive" in body


def test_logs_unreachable_is_502(panel):
    r = panel.get("/admin/api/external/ghost/logs")
    assert r.status_code == 502 and r.get_json()["error"] == "Connection failed"


@pytest.mark.parametrize("path", ["/admin/api/external/animeworld/status", "/admin/api/external/animeworld/logs"])
def test_external_routes_require_session(tmp_path, indexer, path):
    _, _, server = make_panel(tmp_path, [{"name": "animeworld", "url": indexer, "api_key": INDEXER_KEY}])
    assert server.app.test_client().get(path).status_code == 401


def test_get_config_never_exposes_indexer_key(panel):
    d = panel.get("/admin/api/config").get_json()
    keys = {e["name"]: e["api_key"] for e in d["external_indexers"]}
    assert keys == {"animeworld": MASK, "ghost": MASK, "badkey": MASK}
    assert INDEXER_KEY not in json.dumps(d)


def test_put_config_keeps_masked_key_and_accepts_new(panel, tmp_path):
    cfg = panel.get("/admin/api/config").get_json()
    cfg["external_indexers"][1]["api_key"] = "fresh"  # ghost gets a new key, others stay masked
    cfg["external_indexers"].append({"name": "extra", "url": "http://extra:1/", "api_key": "k"})
    assert panel.put("/admin/api/config", json=cfg).status_code == 200
    saved = {e["name"]: e for e in json.loads((tmp_path / "config.json").read_text())["external_indexers"]}
    assert saved["animeworld"]["api_key"] == INDEXER_KEY
    assert saved["ghost"]["api_key"] == "fresh"
    assert saved["extra"]["url"] == "http://extra:1"  # trailing slash stripped
    # still works against the real indexer after a masked round trip
    assert panel.get("/admin/api/external/animeworld/status").get_json()["up"] is True


@pytest.mark.parametrize("bad", [
    [{"name": "a b", "url": "http://x:1"}],
    [{"name": "a", "url": "ftp://x"}],
    [{"name": "a", "url": "http://x"}, {"name": "A", "url": "http://y"}],
    "nope",
])
def test_put_config_rejects_invalid_indexers(panel, bad):
    assert panel.put("/admin/api/config", json={"external_indexers": bad}).status_code == 400


def test_env_parsing():
    got = parse_external_indexers("animeworld=http://aw:9698/, other=https://o.example", "k")
    assert got == [
        {"name": "animeworld", "url": "http://aw:9698", "api_key": "k"},
        {"name": "other", "url": "https://o.example", "api_key": "k"},
    ]
    assert parse_external_indexers("", "k") == []
    assert parse_external_indexers("bad name=http://x", "k") == []  # invalid -> ignored, not fatal


def test_saved_config_overrides_env_and_legacy_config_is_seeded(tmp_path):
    env = [{"name": "animeworld", "url": "http://aw:9698", "api_key": "k"}]
    config = Config(data_dir=tmp_path, external_indexers=env)
    # A config.json from before this feature: seeded from env once.
    (tmp_path / "config.json").write_text(json.dumps({"api_key": "x", "sites": {}}))
    store = ConfigStore(tmp_path / "config.json", config)
    assert store.get_external_indexer("animeworld")["url"] == "http://aw:9698"
    # Once saved, the saved value wins over env on the next start.
    store.update({"external_indexers": []})
    assert ConfigStore(tmp_path / "config.json", config).get()["external_indexers"] == []
