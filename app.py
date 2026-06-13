"""AgentLens web app — scan Shopify stores + monitor a watchlist."""

import os

from flask import Flask, Response, request, jsonify, send_from_directory

import db
import monitor
import scanner

app = Flask(__name__, static_folder="static")

# ---------- auth ----------
# Public: homepage + scan (lead magnet). Everything else needs the admin
# password. Password comes from admin_pass file next to the code (server)
# or AGENTLENS_PASS env var. If neither exists (local dev), auth is off.
OPEN_PATHS = {"/", "/scan", "/favicon.ico"}
_PASS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_pass")


def _admin_pass():
    if os.environ.get("AGENTLENS_PASS"):
        return os.environ["AGENTLENS_PASS"]
    try:
        with open(_PASS_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


@app.before_request
def _require_auth():
    if request.path in OPEN_PATHS:
        return None
    pw = _admin_pass()
    if pw is None:
        return None  # auth not configured (local dev)
    a = request.authorization
    if a and a.password == pw:
        return None
    return Response("Authentication required", 401,
                    {"WWW-Authenticate": 'Basic realm="AgentLens admin"'})


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
