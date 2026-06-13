"""
AgentLens — Shopify agent-readiness scanner.
Checks how visible/usable a store is to AI shopping agents.
"""

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin

import requests

UA = "AgentLensScanner/0.1 (+agent-readiness audit; contact: owner)"
TIMEOUT = 10

# AI agent crawlers that matter for agentic commerce visibility
AI_AGENTS = [
    "GPTBot", "OAI-SearchBot", "ChatGPT-User",
    "ClaudeBot", "Claude-Web", "anthropic-ai",
    "PerplexityBot", "Perplexity-User",
    "Google-Extended", "Amazonbot", "Applebot-Extended",
    "CCBot", "Bytespider", "meta-externalagent",
]

# JSON-LD Product fields agents rely on, with weights inside the category
PRODUCT_FIELDS_REQUIRED = ["name", "image", "description", "offers"]
OFFER_FIELDS_REQUIRED = ["price", "priceCurrency", "availability"]
PRODUCT_FIELDS_RECOMMENDED = ["brand", "sku", "aggregateRating"]
OFFER_FIELDS_RECOMMENDED = ["shippingDetails", "hasMerchantReturnPolicy"]


def _get(session, url, **kw):
    try:
        r = session.get(url, timeout=TIMEOUT, headers={"User-Agent": UA},
                        allow_redirects=True, **kw)
        return r
    except requests.RequestException:
        return None


def _extract_jsonld(html):
    """Return list of parsed JSON-LD objects found in HTML."""
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE)
    out = []
    for b in blocks:
        try:
            data = json.loads(b.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            out.extend(data)
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                out.extend(data["@graph"])
            else:
                out.append(data)
    return out


def _find_product_ld(objs):
    """Find Product or ProductGroup JSON-LD. ProductGroup (Shopify's default for
    products with variants) is normalized: missing fields are filled from the
    first hasVariant Product."""
    prod = group = None
    for o in objs:
        t = o.get("@type", "")
        types = [str(x).lower() for x in (t if isinstance(t, list) else [t])]
        if "product" in types and prod is None:
            prod = o
        if "productgroup" in types and group is None:
            group = o
    if prod:
        return prod
    if group:
        norm = dict(group)
        variants = group.get("hasVariant") or []
        if isinstance(variants, dict):
            variants = [variants]
        v0 = variants[0] if variants and isinstance(variants[0], dict) else {}
        for f in ("name", "image", "description", "offers",
                  "sku", "brand", "aggregateRating"):
            if not norm.get(f) and v0.get(f):
                norm[f] = v0[f]
        return norm
    return None


class Check:
    def __init__(self, cid, category, title, status, detail, fix=None, points=0, max_points=0):
        self.id = cid
        self.category = category
        self.title = title
        self.status = status  # pass | warn | fail | info
        self.detail = detail
        self.fix = fix
        self.points = points
        self.max_points = max_points

    def as_dict(self):
        return self.__dict__


def scan(store_url):
    """Run all checks against a store. Returns dict report."""
    start = time.time()
    if not store_url.startswith("http"):
        store_url = "https://" + store_url
    parsed = urlparse(store_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    s = requests.Session()
    checks = []

    # Resolve canonical host (e.g. gymshark.com -> www.gymshark.com). Apex
    # domains sometimes 301 product URLs to checkout subdomains with no markup.
    home_html = None
    r = _get(s, base)
    if r is not None and r.status_code == 200:
        home_html = r.text
        rp = urlparse(r.url)
        if rp.netloc:
            base = f"{rp.scheme}://{rp.netloc}"

    # ---------- 1. Platform + feed access (25 pts) ----------
    products_json = None
    r = _get(s, urljoin(base, "/products.json?limit=10"))
    if r is not None and r.status_code == 200:
        try:
            products_json = r.json().get("products", [])
        except (json.JSONDecodeError, AttributeError):
            products_json = None

    if products_json:
        checks.append(Check(
            "feed_access", "Feed access", "Product feed publicly accessible",
            "pass",
            f"/products.json returns {len(products_json)} products — agents can read your catalog directly.",
            points=12, max_points=12))

        # freshness
        newest = None
        for p in products_json:
            u = p.get("updated_at")
            if u:
                try:
                    dt = datetime.fromisoformat(u.replace("Z", "+00:00"))
                    newest = max(newest, dt) if newest else dt
                except ValueError:
                    pass
        if newest:
            age_days = (datetime.now(timezone.utc) - newest).days
            if age_days <= 30:
                checks.append(Check(
                    "feed_fresh", "Feed access", "Feed freshness",
                    "pass", f"Most recent product update {age_days} day(s) ago.",
                    points=7, max_points=7))
            else:
                checks.append(Check(
                    "feed_fresh", "Feed access", "Feed freshness",
                    "warn", f"No product updated in {age_days} days. Stale feeds rank lower with agents.",
                    fix="Update product data regularly; stale catalogs read as unreliable to ranking agents.",
                    points=3, max_points=7))
        else:
            checks.append(Check(
                "feed_fresh", "Feed access", "Feed freshness",
                "warn", "Could not determine product update recency.",
                points=3, max_points=7))

        # completeness
        missing = []
        p0 = products_json[0]
        for field in ["title", "body_html", "variants", "images", "product_type", "vendor"]:
            if not p0.get(field):
                missing.append(field)
        if not missing:
            checks.append(Check(
                "feed_complete", "Feed access", "Feed completeness",
                "pass", "Sampled products include title, description, variants, images, type, vendor.",
                points=6, max_points=6))
        else:
            checks.append(Check(
                "feed_complete", "Feed access", "Feed completeness",
                "warn", f"Sampled product missing: {', '.join(missing)}.",
                fix="Fill in missing catalog fields in Shopify admin — agents skip products they can't fully parse.",
                points=2, max_points=6))
    else:
        # Generic mode: don't penalize non-Shopify stores for the feed —
        # grade them on the platform-agnostic checks instead.
        checks.append(Check(
            "feed_access", "Feed access", "Shopify product feed",
            "info",
            "No Shopify /products.json feed detected — running a generic storefront scan "
            "(structured data, agent access, discoverability, render independence).",
            fix="If this store IS on Shopify, the storefront may be password-protected or "
                "the feed blocked — that alone hides the catalog from agents.",
            points=0, max_points=0))

    # ---------- 2. robots.txt — agent access (20 pts) ----------
    blocked_agents, robots_found, sitemap_in_robots = [], False, False
    r = _get(s, urljoin(base, "/robots.txt"))
    if r is not None and r.status_code == 200:
        robots_found = True
        txt = r.text
        sitemap_in_robots = "sitemap" in txt.lower()
        # crude but effective: find user-agent blocks that Disallow: /
        sections = re.split(r"(?i)user-agent:", txt)
        for sec in sections[1:]:
            lines = sec.strip().splitlines()
            agent = lines[0].strip() if lines else ""
            body = "\n".join(lines[1:]).lower()
            if re.search(r"disallow:\s*/\s*$", body, re.MULTILINE):
                for known in AI_AGENTS:
                    if known.lower() == agent.lower():
                        blocked_agents.append(known)

    if not robots_found:
        checks.append(Check(
            "robots", "Agent access", "robots.txt",
            "warn", "No robots.txt found.",
            fix="Serve a robots.txt that explicitly allows AI shopping agents and lists your sitemap.",
            points=10, max_points=20))
    elif blocked_agents:
        checks.append(Check(
            "robots", "Agent access", "AI agents blocked in robots.txt",
            "fail",
            f"Blocking: {', '.join(blocked_agents)}. These agents cannot see your store at all.",
            fix="Remove Disallow rules for AI shopping agents (GPTBot, ClaudeBot, PerplexityBot, etc.). Blocking them removes you from agent-mediated purchases entirely.",
            points=0, max_points=20))
    else:
        checks.append(Check(
            "robots", "Agent access", "AI agents allowed",
            "pass", "No AI shopping agents are blocked in robots.txt.",
            points=20, max_points=20))

    # ---------- 3. Structured data on product page (30 pts) ----------
    product_url = None
    if products_json:
        handle = products_json[0].get("handle")
        if handle:
            product_url = urljoin(base, f"/products/{handle}")
    if not product_url and home_html:
        # fall back to a product link found on the homepage
        m = re.search(r'href=["\'](/products/[a-z0-9\-]+)["\']', home_html, re.I)
        if m:
            product_url = urljoin(base, m.group(1))

    product_html = None
    if product_url:
        r = _get(s, product_url)
        if r is not None and r.status_code == 200:
            product_html = r.text
            product_url = str(r.url)

    # Follow rel=canonical / og:url to a different host if present. Some apex
    # domains (e.g. gymshark.com) send traffic to checkout subdomains that
    # serve stripped pages; the canonical storefront carries the real markup.
    if product_html:
        m = (re.search(r'<link[^>]+rel=["\']canonical["\'][^>]*href=["\']([^"\']+)["\']',
                       product_html, re.I)
             or re.search(r'<link[^>]+href=["\']([^"\']+)["\'][^>]*rel=["\']canonical["\']',
                          product_html, re.I)
             or re.search(r'property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
                          product_html, re.I))
        if m:
            canon = urljoin(product_url, m.group(1).strip())
            if urlparse(canon).netloc != urlparse(product_url).netloc:
                r = _get(s, canon)
                if r is not None and r.status_code == 200:
                    product_html = r.text
                    product_url = canon
                    cp = urlparse(canon)
                    base = f"{cp.scheme}://{cp.netloc}"

    # Last resort: no Product JSON-LD on this host -> try the www storefront.
    # Catches apex domains that shunt all traffic to markup-free checkout
    # subdomains (e.g. gymshark.com -> us.checkout.gymshark.com).
    if product_html and not _find_product_ld(_extract_jsonld(product_html)):
        host = urlparse(product_url).netloc
        apex = ".".join(host.split(".")[-2:])  # naive; fine for .com MVP
        if host != f"www.{apex}":
            alt_url = f"https://www.{apex}" + urlparse(product_url).path
            r = _get(s, alt_url)
            if r is not None and r.status_code == 200 and \
                    _find_product_ld(_extract_jsonld(r.text)):
                product_html = r.text
                product_url = alt_url
                base = f"https://www.{apex}"

    if product_html:
        ld = _find_product_ld(_extract_jsonld(product_html))
        if ld:
            missing_req = [f for f in PRODUCT_FIELDS_REQUIRED if not ld.get(f)]
            offers = ld.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            missing_offer = [f for f in OFFER_FIELDS_REQUIRED if not offers.get(f)]
            missing_rec = [f for f in PRODUCT_FIELDS_RECOMMENDED if not ld.get(f)]
            missing_offer_rec = [f for f in OFFER_FIELDS_RECOMMENDED if not offers.get(f)]

            if not missing_req and not missing_offer:
                checks.append(Check(
                    "ld_required", "Structured data", "Product JSON-LD core fields",
                    "pass",
                    "name, image, description, offers.price, priceCurrency, availability all present.",
                    points=20, max_points=20))
            else:
                miss = missing_req + [f"offers.{f}" for f in missing_offer]
                checks.append(Check(
                    "ld_required", "Structured data", "Product JSON-LD core fields",
                    "fail", f"Missing required fields: {', '.join(miss)}.",
                    fix="Add complete Schema.org Product JSON-LD. Agents that can't parse price/availability skip the product.",
                    points=max(0, 20 - 5 * len(miss)), max_points=20))

            rec_missing = missing_rec + [f"offers.{f}" for f in missing_offer_rec]
            if not rec_missing:
                checks.append(Check(
                    "ld_recommended", "Structured data", "Recommended fields",
                    "pass", "brand, sku, ratings, shipping and return-policy markup present.",
                    points=10, max_points=10))
            else:
                checks.append(Check(
                    "ld_recommended", "Structured data", "Recommended fields",
                    "warn", f"Missing: {', '.join(rec_missing)}.",
                    fix="Add brand/sku/aggregateRating and OfferShippingDetails + MerchantReturnPolicy — agents use these to rank between comparable products, and some surfaces require them for cart-ready listings.",
                    points=max(2, 10 - 2 * len(rec_missing)), max_points=10))
        else:
            checks.append(Check(
                "ld_required", "Structured data", "Product JSON-LD",
                "fail", f"No Schema.org Product JSON-LD found on {product_url}.",
                fix="Add Product JSON-LD to product templates. Without it, agents must guess at your product data — and they don't guess in your favor.",
                points=0, max_points=30))
    else:
        checks.append(Check(
            "ld_required", "Structured data", "Product page reachable",
            "fail", "Could not locate or fetch a product page to inspect.",
            points=0, max_points=30))

    # ---------- 4. Discoverability (10 pts) ----------
    r = _get(s, urljoin(base, "/sitemap.xml"))
    if r is not None and r.status_code == 200 and \
            ("<urlset" in r.text[:2000] or "sitemap" in r.text[:2000].lower()):
        has_products_map = "sitemap_products" in r.text or "/products/" in r.text
        checks.append(Check(
            "sitemap", "Discoverability", "Sitemap",
            "pass" if has_products_map else "warn",
            "Product sitemap present." if has_products_map else "Sitemap found but no product sitemap detected.",
            fix=None if has_products_map else "Ensure products are included in your sitemap index.",
            points=6 if has_products_map else 3, max_points=6))
    else:
        checks.append(Check(
            "sitemap", "Discoverability", "Sitemap",
            "fail", "No sitemap.xml found.",
            fix="Publish a sitemap and reference it in robots.txt.",
            points=0, max_points=6))

    r = _get(s, urljoin(base, "/llms.txt"))
    if r is not None and r.status_code == 200 and len(r.text.strip()) > 0 and "<html" not in r.text[:200].lower():
        checks.append(Check(
            "llms", "Discoverability", "llms.txt",
            "pass", "llms.txt present — gives agents a curated map of your store.",
            points=4, max_points=4))
    else:
        checks.append(Check(
            "llms", "Discoverability", "llms.txt",
            "warn", "No llms.txt. Emerging convention; cheap to add, signals agent-friendliness.",
            fix="Publish /llms.txt with links to your catalog, policies, and shipping info in plain markdown.",
            points=0, max_points=4))

    # ---------- 5. Render independence (15 pts) ----------
    if product_html:
        title = ""
        if products_json:
            title = products_json[0].get("title", "")
        title_in_html = bool(title) and title.split("|")[0].strip()[:20].lower() in product_html.lower()
        if not title:
            # generic mode: no feed title to compare — use a server-rendered
            # <h1> with text as the proxy for title presence in raw HTML
            title_in_html = bool(re.search(r"<h1[^>]*>\s*[^<\s]", product_html))
        price_in_html = bool(re.search(r'(itemprop=["\']price["\']|"price"\s*:\s*"?\d|class=["\'][^"\']*price)', product_html, re.I))
        og = bool(re.search(r'property=["\']og:(title|image)["\']', product_html, re.I))

        pts = (6 if title_in_html else 0) + (6 if price_in_html else 0) + (3 if og else 0)
        status = "pass" if pts >= 12 else ("warn" if pts >= 6 else "fail")
        details = []
        details.append("product title in raw HTML" if title_in_html else "product title NOT in raw HTML")
        details.append("price present in raw HTML" if price_in_html else "price NOT in raw HTML")
        details.append("OpenGraph tags present" if og else "OpenGraph tags missing")
        checks.append(Check(
            "render", "Render independence", "Content readable without JavaScript",
            status, "; ".join(details) + ".",
            fix=None if status == "pass" else "Ensure product name, price, and availability are server-rendered. Many agents read raw HTML and never execute your JavaScript.",
            points=pts, max_points=15))
    else:
        checks.append(Check(
            "render", "Render independence", "Content readable without JavaScript",
            "fail", "No product page available to test.", points=0, max_points=15))

    # ---------- score ----------
    total = sum(c.points for c in checks)
    max_total = sum(c.max_points for c in checks)
    score = round(100 * total / max_total) if max_total else 0
    grade = ("A" if score >= 90 else "B" if score >= 75 else
             "C" if score >= 60 else "D" if score >= 40 else "F")

    return {
        "store": base,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(time.time() - start, 1),
        "score": score,
        "grade": grade,
        "is_shopify": bool(products_json),
        "mode": "shopify" if products_json else "generic",
        "unsupported": not products_json and not product_html,
        "product_page_tested": product_url,
        "checks": [c.as_dict() for c in checks],
        "fixes": [{"title": c.title, "fix": c.fix} for c in checks if c.fix],
    }


if __name__ == "__main__":  # pragma: no cover
    import sys
    print(json.dumps(scan(sys.argv[1]), indent=2))
