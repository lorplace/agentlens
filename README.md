# AgentLens — AI Agent Readiness Scanner for Shopify Stores

Paste a Shopify store URL, get a 0–100 score on how visible and usable the store
is to AI shopping agents, with specific fixes.

## Run it

```
pip install flask requests
python app.py
```

Open http://127.0.0.1:5050 and scan any Shopify store (e.g. `allbirds.com`).

## What it checks (9 checks, 5 categories)

| Category | Weight | Checks |
|---|---|---|
| Structured data | 30 | Schema.org Product JSON-LD: required fields (name, image, description, price, currency, availability) + recommended (brand, sku, ratings, shipping/return policy markup) |
| Feed access | 25 | `/products.json` accessible, feed freshness, catalog field completeness |
| Agent access | 20 | robots.txt blocking of 14 AI agents (GPTBot, ClaudeBot, PerplexityBot, …) |
| Render independence | 15 | Title/price readable in raw HTML without JS; OpenGraph fallback |
| Discoverability | 10 | Product sitemap, llms.txt |

## Architecture

- `scanner.py` — platform-agnostic check engine + Shopify adapter (the `/products.json` probe). Adding WooCommerce later = new adapter, same checks.
- `app.py` — Flask: `GET /scan?url=` returns the JSON report.
- `static/index.html` — single-page UI rendering the scored report.
- `tests/mock_store/` — fixture Shopify store for offline testing:
  `cd tests/mock_store && python3 -m http.server 8765`, then scan `http://127.0.0.1:8765`.

Every scan result is structured JSON — store these from day one (add a DB write in
`app.py:/scan`) and the longitudinal dataset becomes the 2027 data product.

## Monitoring (v2)

- Scan a store, click **+ Watch this store** → it's added to the watchlist with a baseline scan.
- **Monitoring** tab: watchlist with last scores, unseen-alert badges, alert feed, "Re-scan all now".
- `python monitor.py` re-scans every watched store, diffs against the previous scan,
  and records a regression/improvement alert when any check changes status.
- Data lives in `agentlens.db` (SQLite) next to the code.

### Daily automatic re-scans (Windows Task Scheduler)

Run once in PowerShell:

```powershell
$py = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $py -Argument "monitor.py" -WorkingDirectory "$HOME\Documents\agentlens"
$trigger = New-ScheduledTaskTrigger -Daily -At 7:30am
Register-ScheduledTask -TaskName "AgentLens Daily Monitor" -Action $action -Trigger $trigger
```

Remove with: `Unregister-ScheduledTask -TaskName "AgentLens Daily Monitor"`.

## Roadmap to the real product

1. **Lead magnet (now):** host this; free scans, email-gated full report.
2. **Monitoring (the business):** scheduled re-scans per store, diff against last
   result, alert on regressions (markup drift, feed staleness, new agent blocks).
   Subscription: ~$29–99/mo per store.
3. **Data product (2027):** cross-store feed-reliability and agent-visibility
   time series, sold to agent platforms/merchants as the rails standardize.

## Known MVP limitations

- Heuristic checks (regex-based JSON-LD extraction, crude robots.txt parsing) —
  good enough for scoring, not for certification.
- Tests only the first product found; real monitoring should sample N products.
- No ACP/UCP endpoint detection yet (protocols still settling).
- Stores behind aggressive bot protection (Cloudflare challenge) may fail to scan.
