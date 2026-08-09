from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from web.services import ENGINE, _pct

ROOT = Path(__file__).resolve().parents[1]


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    @app.context_processor
    def inject_helpers():
        return {"pct": _pct, "universe_name": lambda c: __import__("config", fromlist=["UNIVERSE"]).UNIVERSE.get(c, {}).get("name", c)}

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/research")
    def research():
        ctx = ENGINE.research_context()
        return render_template("research.html", **ctx)

    @app.route("/monitor")
    def monitor():
        ctx = ENGINE.monitor_context()
        return render_template("monitor.html", **ctx)

    @app.route("/forecast")
    def forecast():
        ctx = ENGINE.forecast_context()
        return render_template("forecast.html", **ctx)

    @app.route("/api/refresh", methods=["POST"])
    def api_refresh():
        force = bool(request.json.get("force_download")) if request.is_json else False
        ENGINE.refresh(force_download=force)
        return jsonify({"ok": True})

    @app.route("/api/monitor")
    def api_monitor():
        return jsonify(ENGINE.monitor_context())

    @app.route("/api/forecast")
    def api_forecast():
        return jsonify(ENGINE.forecast_context())

    @app.route("/api/summary")
    def api_summary():
        s = ENGINE.ensure()
        return jsonify({"stats": s.stats, "order": s.order, "params": __import__("config", fromlist=["PARAMS"]).PARAMS})

    return app


app = create_app()


if __name__ == "__main__":
    # 预热引擎
    ENGINE.refresh(force_download=False)
    app.run(host="0.0.0.0", port=8080, debug=False)
