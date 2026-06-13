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


# ---------- rate limiting (in-memory, per IP) ----------
from collections import defaultdict, deque
import time as _time

_hits = defaultdict(deque)


def _limited(key, limit, window=60):
    q = _hits[key]
    now = _time.time()
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def _is_admin(req):
    pw = _admin_pass()
    return pw is None or bool(req.authorization and req.authorization.password == pw)


def _valid_email(e):
    return "@" in e and "." in e.split("@")[-1] and 5 <= len(e) <= 200


@app.route("/scan")
def scan():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url parameter"}), 400
    if _limited("scan:" + (request.remote_addr or "?"), 8):
        return jsonify({"error": "Rate limit: try again in a minute."}), 429
    try:
        report = scanner.scan(url)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Scan failed: {e}"}), 500

    if _is_admin(request):
        return jsonify(report)

    email = request.args.get("email", "").strip().lower()
    if email and _valid_email(email):
        db.add_lead(email, report["store"])
        return jsonify(report)

    # public, no email: summary teaser — statuses and scores, no details/fixes
    slim = dict(report)
    slim["gated"] = True
    slim["checks"] = [{
        "id": c["id"], "category": c["category"], "title": c["title"],
        "status": c["status"], "points": c["points"],
        "max_points": c["max_points"], "detail": "", "fix": None,
    } for c in report["checks"]]
    slim["fixes"] = []
    return jsonify(slim)


@app.route("/leads")
def leads():
    return jsonify(db.list_leads())


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
