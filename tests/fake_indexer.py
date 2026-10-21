"""A stand-in for the AnimeWorld indexer, implementing the #57 contract.

Used by the tests and, with follow=True, to demo the panel by hand.
"""

import json
import time

from flask import Flask, Response, jsonify, request

KEY = "indexer-key"

QUEUE = [
    {
        "hash": "a" * 40,
        "name": "Frieren S01E03 [SUB ITA]",
        "category": "tv-sonarr",
        "state": "downloading",
        "progress": 0.42,
        "size": 399962216,
        "downloaded": 167984130,
        "dlspeed": 5242880,
        "eta": 44,
        "added_at": "2026-10-20T21:30:00Z",
    },
    {
        "hash": "b" * 40,
        "name": "Frieren S01E02 [SUB ITA]",
        "category": "tv-sonarr",
        "state": "completed",
        "progress": 1.0,
        "size": 380000000,
        "downloaded": 380000000,
        "dlspeed": 0,
        "eta": 0,
        "added_at": "2026-10-20T21:10:00Z",
    },
]

LOGS = [
    {"timestamp": "2026-10-20 23:30:58", "level": "INFO", "logger": "torznab", "message": "search q=frieren: 12 results"},
    {"timestamp": "2026-10-20 23:31:02", "level": "ERROR", "logger": "scraper", "message": "file size lookup failed: timeout"},
]


def create_app(status="ok", queue=None, follow=False):
    app = Flask(__name__)
    app.config["INDEXER"] = {"status": status, "queue": QUEUE if queue is None else queue}

    @app.before_request
    def guard():
        if request.headers.get("X-Api-Key") != KEY:
            return jsonify({"error": "Unauthorized"}), 401

    @app.route("/api/status")
    def api_status():
        state = app.config["INDEXER"]
        return jsonify({
            "service": "animeworldtv-indexer",
            "version": "1.2.0",
            "uptime_seconds": 3600 * 30 + 125,
            "status": state["status"],
            "site": {"url": "https://www.animeworld.ac", "reachable": state["status"] == "ok"},
            "solver": {"configured": True, "url": "http://byparr:8191", "cookies_valid": True, "expires_at": None},
            "queue": state["queue"],
            "errors": [LOGS[1]],
            "future_field": {"ignored": True},
        })

    # Settings contract: animeworldtv-indexer#21.
    settings = {"MAX_DOWNLOADS": "2", "LOG_LEVEL": "INFO"}
    running = dict(settings)
    app.config["RESTARTS"] = 0

    def settings_body():
        items = [{"key": k, "label": k, "help": "", "value": v, "running": running[k], "saved": v != running[k]}
                 for k, v in settings.items()]
        return {"settings": items, "restart_required": settings != running}

    @app.route("/api/settings", methods=["GET", "PUT"])
    def api_settings():
        if request.method == "PUT":
            body = request.get_json()
            if body.get("MAX_DOWNLOADS") == "0":
                return jsonify({"error": "MAX_DOWNLOADS=\"0\" must be an integer >= 1"}), 400
            settings.update(body)
        return jsonify(settings_body())

    @app.route("/api/restart", methods=["POST"])
    def api_restart():
        app.config["RESTARTS"] += 1
        return jsonify({"restarting": True}), 202

    @app.route("/api/logs")
    def api_logs():
        def generate():
            yield f"data: {json.dumps(LOGS)}\n\n"
            if follow:
                n = 0
                while True:
                    time.sleep(3)
                    n += 1
                    entry = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "level": "INFO",
                             "logger": "downloader", "message": f"Frieren S01E03: chunk {n} done"}
                    yield f"data: {json.dumps([entry])}\n\n"
            else:
                yield ": keepalive\n\n"
                entry = {"timestamp": "2026-10-20 23:31:09", "level": "INFO", "logger": "downloader", "message": "episode done"}
                yield f"data: {json.dumps([entry])}\n\n"

        return Response(generate(), mimetype="text/event-stream")

    return app
