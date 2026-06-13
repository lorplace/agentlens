"""AgentLens web app — scan Shopify stores + monitor a watchlist."""

from flask import Flask, request, jsonify, send_from_directory

import db
import monitor
import scanner

app = Flask(__name__, static_folder="static")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/scan")
def scan():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400
    try:
        report = scanner.scan(url)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Scan failed: {e}"}), 500
    return jsonify(report)


# ---------- monitoring ----------

@app.route("/watch", methods=["POST"])
def watch():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url"}), 400
    store_id = db.add_store(url)
    # baseline scan so the first monitor pass has something to diff against
    try:
        report = scanner.scan(url)
        db.record_scan(store_id, report)
    except Exception:  # noqa: BLE001
        pass
    return jsonify({"ok": True})


@app.route("/unwatch", methods=["POST"])
def unwatch():
    url = (request.json or {}).get("url", "").strip()
    db.remove_store(url)
    return jsonify({"ok": True})


@app.route("/stores")
def stores():
    return jsonify(db.list_stores())


@app.route("/history")
def history():
    url = request.args.get("url", "").strip()
    for s in db.list_stores():
        if s["url"] == url:
            return jsonify(db.scan_history(s["id"]))
    return jsonify([])


@app.route("/alerts")
def alerts():
    return jsonify(db.list_alerts())


@app.route("/alerts/seen", methods=["POST"])
def alerts_seen():
    db.mark_alerts_seen()
    return jsonify({"ok": True})


@app.route("/run-monitor", methods=["POST"])
def run_monitor():
    results = monitor.run_pass(verbose=False)
    return jsonify({"ok": True, "scanned": len(results), "results": results})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
