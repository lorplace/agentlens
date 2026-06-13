"""Rescan all watched stores, diff against previous scan, record alerts.

Run manually:           python monitor.py
Run by Task Scheduler:  daily, see README.
"""

import db
import mailer
import scanner


def diff_reports(prev, curr):
    """Compare two scan reports. Returns (regressions, improvements) lists."""
    regressions, improvements = [], []

    prev_checks = {c["id"]: c for c in prev.get("checks", [])}
    curr_checks = {c["id"]: c for c in curr.get("checks", [])}
    rank = {"pass": 0, "warn": 1, "fail": 2, "info": 0}

    for cid, cc in curr_checks.items():
        pc = prev_checks.get(cid)
        if pc is None:
            continue
        worse = rank.get(cc["status"], 0) > rank.get(pc["status"], 0)
        better = rank.get(cc["status"], 0) < rank.get(pc["status"], 0)
        if worse:
            regressions.append({
                "check": cc["title"], "from": pc["status"], "to": cc["status"],
                "detail": cc["detail"], "fix": cc.get("fix")})
        elif better:
            improvements.append({
                "check": cc["title"], "from": pc["status"], "to": cc["status"]})

    score_delta = curr.get("score", 0) - prev.get("score", 0)
    return regressions, improvements, score_delta


def run_pass(verbose=True):
    """Scan every active store; record scans + alerts. Returns summary list."""
    results = []
    regression_lines = []
    for store in db.list_stores():
        url = store["url"]
        try:
            report = scanner.scan(url)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"[ERROR] {url}: {e}")
            continue

        prev = db.last_scan(store["id"])
        db.record_scan(store["id"], report)

        line = f"{url}: {report['score']}/{report['grade']}"
        if prev:
            regs, imps, delta = diff_reports(prev, report)
            if regs:
                summary = (f"Score {prev['score']}→{report['score']} "
                           f"({len(regs)} check(s) regressed: "
                           + ", ".join(r["check"] for r in regs) + ")")
                db.record_alert(store["id"], "regression", summary,
                                {"regressions": regs, "score_delta": delta})
                line += f"  ⚠ REGRESSION: {summary}"
                regression_lines.append(f"{url}: {summary}")
                for reg in regs:
                    regression_lines.append(
                        f"   - {reg['check']}: {reg['from']} -> {reg['to']}. "
                        f"{reg.get('fix') or ''}")
            elif imps:
                summary = (f"Score {prev['score']}→{report['score']} "
                           f"({len(imps)} check(s) improved)")
                db.record_alert(store["id"], "improvement", summary,
                                {"improvements": imps, "score_delta": delta})
                line += f"  ✓ improved: {summary}"
        if verbose:
            print(line)
        results.append({"url": url, "score": report["score"],
                        "grade": report["grade"]})

    if regression_lines and mailer.configured():
        sent = mailer.send(
            f"AgentLens: {len(regression_lines)} regression line(s) detected",
            "Regressions found in today's monitoring pass:\n\n"
            + "\n".join(regression_lines)
            + "\n\nFull details in the dashboard.")
        if verbose:
            print(f"[email] alert {'sent' if sent else 'FAILED'}")
    return results


if __name__ == "__main__":
    run_pass()
