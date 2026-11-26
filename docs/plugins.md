# Writing a plugin

An indexer is a plugin: a folder with a `manifest.json` and a Python module that has `create_site(config)`.

## Where it goes

| Folder | For | Survives an image update |
|---|---|---|
| `src/sites/<id>/` | plugins shipped with the proxy (MIRCrew) | yes, it is in the image |
| `data/plugins/<id>/` | plugins you add | yes, it is on the `./data` volume |

To add one: copy [`docs/plugin-template/example/`](plugin-template/example) to `data/plugins/<id>/`, rename the id inside
(`manifest.json`, the module path, `site.py`), and restart the proxy. Plugins load at startup, never while it runs.

After the restart the plugin gets a site named after its id, enabled and filled with the manifest defaults. Open
**Indexers**, press **Settings**, enter the credentials and save: the proxy reloads it, and it answers on `/<id>/api`.
A plugin that fails to load is listed under **Indexers, Plugin folder** with the reason, and the log has the same line.
The other plugins keep running.

A plugin is Python that runs inside the proxy with the site's credentials. Only put there code you trust. The panel
does not upload or edit plugin code on purpose. If the plugin needs a pip package the image does not have, the image
has to be rebuilt.

## manifest.json

| Key | Meaning |
|---|---|
| `id` | lowercase letters, digits and `_`, starting with a letter. Must equal the folder name. A built-in id is refused. |
| `name`, `description`, `version` | shown on the Indexers page |
| `module` | must be `sites.<id>.<module>`, for example `sites.example.site` |
| `config_schema` | connection fields: `base_url`, `username`, `password`, each with `type`, `label`, `default`, `group: "connection"` |
| `custom_config` | extra settings, each with `type`, `label`, `description`, `group` and a `default` |

`custom_config` types the Settings dialog can edit: `key_value_map` (rows of key and value), `integer_list`, and `code`
(an XML or Python text box). The `group` picks the tab: `mappings`, `scraping` or `advanced`. The saved values reach the
plugin as `config.custom[<key>]`.

## The module

`create_site(config)` returns an object that implements `torznab.server.BaseSite`:

| Method | Contract |
|---|---|
| `search(query, categories, target_season, target_episode)` | list of `TorznabResult`. Raise `SiteUnavailable` when the site cannot be reached (the proxy answers 503 and Prowlarr records a failure). Return `[]` when it is reachable and has no hits. |
| `download(topic_id, infohash, season, episode)` | the magnet URI, or `None` |
| `get_capabilities_xml()` | the Torznab caps XML |
| `health_info()` | a dict for the panel: `status` (`"ok"` or an error text), and optionally `logged_in`, `cf_valid`, `cf_bypass_url`, `login_blocked`, `thanks_cached` |

`config` is a `config.Config`: `base_url`, `username`, `password`, `data_dir`, `cf_bypass_url`, `cf_bypass_timeout` and
`custom`. `session.ByparrSession` gives the Cloudflare handling through Byparr and the cookie cache; subclass it and
implement `_check_logged_in` and `_do_login`. `create_site` is called again every time the Settings are saved, and the
old instance keeps answering until the new one is ready.

## Check it

```
curl "http://localhost:9696/<id>/api?t=caps&apikey=<key>"
```

returns the capabilities, and `t=search&q=...` runs a search. Add tests the way `tests/test_plugins.py` does.
