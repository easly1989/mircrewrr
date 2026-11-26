"""Plugin discovery (built-in and data/plugins) and the panel's site reload."""

import json
import shutil
import sys
import types
from importlib import import_module
from pathlib import Path

import pytest

from admin.auth import init_auth
from admin.config_store import ConfigStore
from admin.routes import admin_bp, init_admin
from config import Config
from plugins import discover_plugins
from torznab.server import TorznabServer

BUILTIN_DIR = Path(__file__).parent.parent / "src" / "sites"

SITE_PY = "def create_site(config):\n    return object()\n"


def write_plugin(root, pid, *, manifest=None, site=SITE_PY, folder=None):
    folder = root / (folder or pid)
    folder.mkdir(parents=True)
    (folder / "__init__.py").write_text("")
    (folder / "site.py").write_text(site)
    if manifest is None:
        manifest = {"id": pid, "name": pid.title(), "module": f"sites.{pid}.site"}
    if manifest is not False:
        (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def by_folder(report):
    return {r["folder"]: r for r in report}


def test_built_in_plugin_is_found(tmp_path):
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path / "none")
    assert "mircrew" in plugins
    assert by_folder(report)["src/sites/mircrew"]["loaded"] is True


def test_operator_plugin_loads_from_data_folder(tmp_path):
    write_plugin(tmp_path, "opdemo_ok")
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path)
    assert "opdemo_ok" in plugins and "mircrew" in plugins
    entry = by_folder(report)["data/plugins/opdemo_ok"]
    assert entry["loaded"] is True and entry["error"] is None and entry["source"] == "data/plugins"
    assert plugins["opdemo_ok"]["module"] == "sites.opdemo_ok.site"


@pytest.mark.parametrize("name, kwargs, reason", [
    ("opdemo_nomanifest", {"manifest": False}, "no manifest.json"),
    ("opdemo_nomodule", {"manifest": {"id": "opdemo_nomodule", "name": "x"}}, 'manifest "module"'),
    ("opdemo_wrongid", {"manifest": {"id": "other", "module": "sites.other.site"}}, 'but the folder is "opdemo_wrongid"'),
    ("opdemo_badimport", {"site": "raise RuntimeError('boom')\n"}, "import of sites.opdemo_badimport.site failed"),
    ("opdemo_nofactory", {"site": "x = 1\n"}, "no create_site"),
])
def test_broken_plugin_is_reported_and_does_not_stop_the_others(tmp_path, name, kwargs, reason):
    write_plugin(tmp_path, name, **kwargs)
    write_plugin(tmp_path, "opdemo_fine")
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path)
    entry = by_folder(report)[f"data/plugins/{name}"]
    assert entry["loaded"] is False and reason in entry["error"]
    assert name not in plugins
    assert "opdemo_fine" in plugins and "mircrew" in plugins


def test_documented_template_plugin_loads_and_runs(tmp_path):
    shutil.copytree(Path(__file__).parent.parent / "docs" / "plugin-template" / "example", tmp_path / "example")
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path)
    assert by_folder(report)["data/plugins/example"]["loaded"] is True
    module = import_module(plugins["example"]["module"])
    site = module.create_site(Config(data_dir=tmp_path, api_key="k", base_url="https://example.org"))
    assert site.search("x", None, None, None) == []
    assert site.health_info()["status"] == "ok" and "<caps>" in site.get_capabilities_xml()


def test_built_in_id_cannot_be_taken_by_an_operator_plugin(tmp_path):
    write_plugin(tmp_path, "mircrew", site="raise RuntimeError('must not run')\n")
    plugins, report = discover_plugins(BUILTIN_DIR, tmp_path)
    entry = by_folder(report)["data/plugins/mircrew"]
    assert entry["loaded"] is False and "built-in" in entry["error"]
    assert plugins["mircrew"]["_source"] == "built-in"


def test_invalid_json_is_reported(tmp_path):
    folder = write_plugin(tmp_path, "opdemo_badjson")
    (folder / "manifest.json").write_text("{not json")
    _, report = discover_plugins(BUILTIN_DIR, tmp_path)
    assert "not valid JSON" in by_folder(report)["data/plugins/opdemo_badjson"]["error"]


# --- the panel reloads a site when its settings are saved ---

class FakeSite:
    def __init__(self, tag):
        self.tag = tag

    def search(self, *a):
        return []

    def download(self, *a):
        return None

    def get_capabilities_xml(self):
        return "<caps/>"

    def health_info(self):
        return {"status": "ok"}


@pytest.fixture
def panel(tmp_path):
    state = {"calls": 0, "fail": False}
    module = types.ModuleType("sites.reload_demo.site")

    def create_site(config):
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("login failed")
        return FakeSite(state["calls"])

    module.create_site = create_site
    sys.modules["sites.reload_demo.site"] = module

    config = Config(data_dir=tmp_path, api_key="k")
    store = ConfigStore(tmp_path / "config.json", config)
    store.add_site("demo", {"enabled": True, "plugin": "reload_demo", "base_url": "https://a.example"})
    server = TorznabServer(api_key="k")
    init_auth(server.app, server, tmp_path)
    init_admin(server, store, {"reload_demo": "sites.reload_demo.site"}, {}, [])
    server.app.register_blueprint(admin_bp)
    server.register_site("demo", FakeSite(0))
    client = server.app.test_client()
    client.environ_base["HTTP_ORIGIN"] = "http://localhost"
    assert client.post("/admin/login", data={"api_key": "k"}).status_code == 302
    yield client, server, state
    sys.modules.pop("sites.reload_demo.site", None)


def test_saving_a_site_replaces_it_without_a_gap(panel):
    client, server, state = panel
    r = client.put("/admin/api/sites/demo", json={"base_url": "https://b.example"})
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert server.sites["demo"].tag == 1


def test_failed_reload_keeps_the_running_site(panel):
    client, server, state = panel
    old = server.sites["demo"]
    state["fail"] = True
    r = client.put("/admin/api/sites/demo", json={"username": "wrong"})
    assert r.status_code == 200 and "reload failed" in r.get_json()["warning"]
    assert server.sites["demo"] is old


def test_saving_a_site_that_failed_at_startup_starts_it(panel):
    client, server, state = panel
    server.unregister_site("demo")
    r = client.put("/admin/api/sites/demo", json={"password": "fixed"})
    assert r.get_json() == {"ok": True} and "demo" in server.sites


def test_new_plugin_gets_a_site_once(tmp_path):
    store = ConfigStore(tmp_path / "config.json", Config(data_dir=tmp_path, api_key="k"))
    manifest = {"id": "nyaa", "config_schema": {"base_url": {"default": "https://nyaa.example"}},
                "custom_config": {"category_map": {"default": {"1": 2000}}, "no_default": {}}}
    plugins = {"nyaa": manifest, "mircrew": {"id": "mircrew"}}
    assert store.seed_plugin_sites(plugins) == ["nyaa"]  # mircrew already has its site
    site = store.get_site("nyaa")
    assert site == {"enabled": True, "plugin": "nyaa", "base_url": "https://nyaa.example",
                    "username": "", "password": "", "custom": {"category_map": {"1": 2000}}}
    assert store.seed_plugin_sites(plugins) == []  # nothing new on the next start
    assert "nyaa" in store.get_enabled_sites()


def test_status_lists_plugin_folders(tmp_path):
    config = Config(data_dir=tmp_path, api_key="k")
    server = TorznabServer(api_key="k")
    init_auth(server.app, server, tmp_path)
    folders = [{"folder": "data/plugins/x", "loaded": False, "error": "no manifest.json"}]
    init_admin(server, ConfigStore(tmp_path / "config.json", config), {}, {}, folders)
    server.app.register_blueprint(admin_bp)
    c = server.app.test_client()
    c.environ_base["HTTP_ORIGIN"] = "http://localhost"
    c.post("/admin/login", data={"api_key": "k"})
    assert c.get("/admin/api/status").get_json()["plugin_folders"] == folders
