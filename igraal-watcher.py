#!/usr/bin/env python3
"""
iGraal Cashback Watcher — zero-dependency version.

Only uses Python's standard library. No pip install needed.

Setup:
  1. Run: python igraal-watcher.py
  2. On first launch, paste your Discord webhook when prompted
  3. That's it! The webhook is saved in config.json (gitignored)
"""

import json
import sys
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ============================================================
# FILES
# ============================================================
CONFIG_FILE = Path("config.json")
SEEN_FILE = Path("seen.json")

# ============================================================
# GENERAL CONFIG
# ============================================================
THRESHOLD = 100
TOP_SIZE = 10

# Test mode: only scan the first page of retailers (fast validation)
TEST_MODE = False
TEST_LIMIT = 50

# Performance: 1 (safe), 2 (moderate), 4 (fast)
MAX_WORKERS = 4
MIN_DELAY_BETWEEN_REQUESTS = 0.15
REQUEST_TIMEOUT = 20
MAX_RETRIES = 4
BACKOFF_BASE = 2.0

# ============================================================
# API CONFIG
# ============================================================
GRAPHQL_URL = "https://graphql-router-public-euc1.foundation.gsg.direct/"
BASE_URL = "https://fr.igraal.com"
ID_CLIENT = "a444a51901d85f3d9eb84ada70cef7f3"
COUNTRY = "fr"
RETAILER_PAGE_SIZE = 100
MAX_RETAILER_PAGES = 50

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "apollographql-client-name": "browser-extension",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

QUERY_RETAILERS = """
query RetailersByFilter($input: SearchRetailersInput) {
  retailersByFilter(input: $input) {
    retailers {
      data {
        retailer { country merchantUrl name idPool }
      }
    }
  }
}
"""

QUERY_CASHBACK = """
query ActiveCashbackWithMaxCommission($criteria: CashbackRetailerPoolIdInput) {
  activeCashbackWithMaxCommission(criteria: $criteria) {
    data {
      retailer {
        name
        idPool
        activeCashback {
          data {
            maxCommission { type value }
            offers { cashbackDisplayText outgoingAmount { type value } }
          }
        }
        landingPage { url }
      }
    }
  }
}
"""


# ============================================================
# CONFIG MANAGEMENT
# ============================================================

def load_or_create_config():
    """Load config.json, or create it interactively on first run."""
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if cfg.get("discord_webhook"):
                return cfg
        except (json.JSONDecodeError, IOError):
            print("config.json is corrupted, recreating...")

    print("=" * 60)
    print("  First-time setup")
    print("=" * 60)
    print()
    print("Please paste your Discord webhook URL.")
    print("(Server Settings -> Integrations -> Webhooks -> New Webhook)")
    print()
    webhook = input("Webhook URL: ").strip()

    if not webhook.startswith("https://discord.com/api/webhooks/"):
        print("\nERROR: that doesn't look like a valid Discord webhook URL.")
        print("       It should start with https://discord.com/api/webhooks/")
        sys.exit(1)

    print()
    print("Optional: role or user ID to ping on 100% alerts.")
    print("(Leave empty to disable ping)")
    ping_id = input("Ping ID (optional): ").strip()

    cfg = {
        "discord_webhook": webhook,
        "ping_id": ping_id,
        "ping_type": "role",
    }

    CONFIG_FILE.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print()
    print("Saved to config.json (this file is gitignored).")
    print("=" * 60)
    print()
    return cfg


CONFIG = load_or_create_config()
DISCORD_WEBHOOK = CONFIG["discord_webhook"]
PING_ID = CONFIG.get("ping_id", "").strip()
PING_TYPE = CONFIG.get("ping_type", "role").strip()


# ============================================================
# RATE LIMITER
# ============================================================
_rate_lock = threading.Lock()
_last_request_time = 0.0


def rate_limit():
    global _last_request_time
    with _rate_lock:
        now = time.time()
        wait = MIN_DELAY_BETWEEN_REQUESTS - (now - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.time()


# ============================================================
# HELPERS
# ============================================================

def get_ping():
    if not PING_ID:
        return ""
    return f"<@&{PING_ID}>" if PING_TYPE == "role" else f"<@{PING_ID}>"


def webhook_url():
    base = DISCORD_WEBHOOK.rstrip("/")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}with_components=true"


def format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def render_bar(done, total, label="", width=28):
    ratio = done / total if total else 0
    filled = int(width * ratio)
    bar = "█" * filled + "░" * (width - filled)
    pct = int(ratio * 100)
    sys.stdout.write("\r" + " " * 140 + "\r")
    sys.stdout.write(f"  [{bar}]  {done:>4}/{total}  {pct:>3}%  {label}")
    sys.stdout.flush()


# ============================================================
# HTTP (stdlib only)
# ============================================================

def http_post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.status, resp.read().decode("utf-8")


def gql_request(query, variables):
    last_err = None
    payload = {"query": query, "variables": variables}

    for attempt in range(MAX_RETRIES + 1):
        rate_limit()
        try:
            status, body = http_post_json(GRAPHQL_URL, payload)

            if status == 200:
                data = json.loads(body)
                if "errors" in data:
                    return None, f"GraphQL: {data['errors'][0].get('message', '?')}"
                return data.get("data"), None

            if status == 429:
                wait = BACKOFF_BASE ** attempt
                print(f"\n  429 — waiting {wait:.0f}s "
                      f"(attempt {attempt + 1}/{MAX_RETRIES + 1})", flush=True)
                time.sleep(wait)
                last_err = "HTTP 429"
                continue

            return None, f"HTTP {status}"

        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = BACKOFF_BASE ** attempt
                print(f"\n  429 — waiting {wait:.0f}s", flush=True)
                time.sleep(wait)
                last_err = "HTTP 429"
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:
            time.sleep(BACKOFF_BASE ** attempt)
            last_err = str(e)
            continue

    return None, last_err or "failed after retries"


def discord_post(payload):
    try:
        status, _ = http_post_json(webhook_url(), payload)
        return status in (200, 204)
    except Exception as e:
        print(f"  Discord error: {e}")
        return False


# ============================================================
# SEEN STORAGE
# ============================================================

def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    return {}


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(seen, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ============================================================
# STEP 1: FETCH ALL RETAILERS
# ============================================================

def fetch_all_retailers():
    """Fetch all retailers. In TEST_MODE, only fetch one page."""
    max_pages = 1 if TEST_MODE else MAX_RETAILER_PAGES
    print(f"  Fetching retailers (size={RETAILER_PAGE_SIZE}, "
          f"max {max_pages} page(s))...")

    all_retailers = {}
    page = 1

    while page <= max_pages:
        variables = {"input": {
            "country": COUNTRY,
            "filter": {"published": True},
            "idClient": ID_CLIENT,
            "page": page,
            "size": RETAILER_PAGE_SIZE,
        }}
        data, err = gql_request(QUERY_RETAILERS, variables)
        if err:
            print(f"\n  Page {page}: {err}")
            break

        items = (data or {}).get("retailersByFilter", {}) \
                           .get("retailers", {}) \
                           .get("data", [])
        if not items:
            break

        for item in items:
            ret = item.get("retailer")
            if ret and ret.get("idPool"):
                all_retailers[ret["idPool"]] = ret

        print(f"    Page {page:>2}: {len(items):>3} (total {len(all_retailers)})")

        if len(items) < RETAILER_PAGE_SIZE:
            break
        page += 1

    print(f"  {len(all_retailers)} retailers fetched.")
    return list(all_retailers.values())


# ============================================================
# STEP 2: FETCH CASHBACK PER RETAILER
# ============================================================

def fetch_cashback(retailer):
    variables = {"criteria": {"idClient": ID_CLIENT, "idPool": retailer["idPool"]}}
    data, err = gql_request(QUERY_CASHBACK, variables)
    if err or not data:
        return None

    container = data.get("activeCashbackWithMaxCommission") or {}
    inner = (container.get("data") or {}).get("retailer")
    if not inner:
        return None

    cash_data = (inner.get("activeCashback") or {}).get("data")
    if not cash_data:
        return None

    max_comm = cash_data.get("maxCommission") or {}
    value = max_comm.get("value")
    ctype = max_comm.get("type")
    if value is None:
        return None

    landing = (inner.get("landingPage") or {}).get("url", "")
    url = f"{BASE_URL}/{landing.lstrip('/')}" if landing else ""

    return {
        "idPool": inner.get("idPool"),
        "name": inner.get("name"),
        "value": value,
        "type": ctype,
        "url": url,
        "merchantUrl": retailer.get("merchantUrl", ""),
    }


def fetch_all_cashbacks(retailers):
    total = len(retailers)
    print(f"\n  Fetching cashback for {total} retailers "
          f"({MAX_WORKERS} workers, delay {MIN_DELAY_BETWEEN_REQUESTS}s)...\n")

    results = []
    failed = 0
    done = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_cashback, r): r for r in retailers}
        for future in as_completed(futures):
            done += 1
            try:
                res = future.result()
                if res:
                    results.append(res)
                else:
                    failed += 1
            except Exception:
                failed += 1

            if done % 25 == 0 or done == total:
                elapsed = time.time() - start
                render_bar(done, total,
                           label=(f"{len(results)} OK, {failed} without "
                                  f"— {format_duration(elapsed)}"))

    elapsed = time.time() - start
    render_bar(done, total, label=f"done in {format_duration(elapsed)}")
    print()
    print(f"  {len(results)} retailers with cashback, {failed} without")
    return results


# ============================================================
# DISCORD MESSAGES
# ============================================================

def build_top_field(items, is_percent=True):
    lines = []
    for i, m in enumerate(items, start=1):
        medal = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"`{i:>2}.`"
        val_str = f"{m['value']:g}%" if is_percent else f"{m['value']:g} EUR"
        lines.append(f"{medal} **{m['name']}** — `{val_str}`")
    return "\n".join(lines) if lines else "_None_"


def send_summary(retailer_count, cashback_count, duration, top_pct, top_fix, hits_100):
    print("  Sending Discord summary...")

    fields = [{
        "name": "📊 Scan",
        "value": (
            f"• **{retailer_count}** retailers scanned\n"
            f"• **{cashback_count}** with active cashback\n"
            f"• Duration: **{format_duration(duration)}**"
        ),
        "inline": False,
    }]

    if hits_100:
        lines = [f"💯 **{m['name']}** — `{m['value']:g}%`" for m in hits_100]
        fields.append({
            "name": f"🎉 {len(hits_100)} offer(s) at {THRESHOLD}%+!",
            "value": "\n".join(lines[:10]),
            "inline": False,
        })

    if top_pct:
        fields.append({
            "name": f"🏆 Top {len(top_pct)} cashbacks by %",
            "value": build_top_field(top_pct, True),
            "inline": False,
        })

    if top_fix:
        fields.append({
            "name": f"💰 Top {len(top_fix)} cashbacks by EUR",
            "value": build_top_field(top_fix, False),
            "inline": False,
        })

    embed = {
        "title": "📋 iGraal scan summary",
        "description": f"Scan completed on {time.strftime('%Y-%m-%d at %H:%M:%S')}",
        "color": 0x00C853 if hits_100 else 0x3498DB,
        "fields": fields,
        "footer": {"text": f"iGraal Watcher · threshold {THRESHOLD}%"},
    }

    discord_post({
        "content": get_ping() if hits_100 else "",
        "embeds": [embed],
    })


def notify_100(m):
    embed = {
        "title": f"💯 {int(m['value'])}% CASHBACK  ·  {m['name']}",
        "url": m["url"],
        "color": 0x00C853,
        "description": "🚨 **100% offer detected!**",
    }
    return discord_post({
        "content": get_ping(),
        "embeds": [embed],
        "components": [{
            "type": 1,
            "components": [
                {"type": 2, "style": 5, "label": "View offer", "url": m["url"]},
            ],
        }],
    })


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("  iGraal Cashback Watcher")
    print("=" * 60)
    print(f"  Workers: {MAX_WORKERS}, delay: {MIN_DELAY_BETWEEN_REQUESTS}s")
    if TEST_MODE:
        print(f"  TEST MODE: {TEST_LIMIT} retailers max")
    print("=" * 60)

    print("\nSTEP 1/2: Retailers")
    t0 = time.time()
    retailers = fetch_all_retailers()
    t1 = time.time()

    if not retailers:
        print("No retailers. Aborting.")
        return

    if TEST_MODE:
        retailers = retailers[:TEST_LIMIT]
        print(f"  Test mode: {len(retailers)} retailers")

    print("\nSTEP 2/2: Cashback per retailer")
    cashbacks = fetch_all_cashbacks(retailers)
    t2 = time.time()

    if not cashbacks:
        print("No cashback. Aborting.")
        return

    top_pct = sorted(
        [m for m in cashbacks if m["type"] == "PERCENTAGE" and m["value"] > 0],
        key=lambda x: x["value"], reverse=True)[:TOP_SIZE]
    top_fix = sorted(
        [m for m in cashbacks if m["type"] != "PERCENTAGE" and m["value"] > 0],
        key=lambda x: x["value"], reverse=True)[:TOP_SIZE]
    hits_100 = [m for m in cashbacks
                if m["type"] == "PERCENTAGE" and m["value"] >= THRESHOLD]

    print(f"\n{len(cashbacks)} retailers with cashback")
    print(f"\n🏆 Top {len(top_pct)} by %:")
    for i, m in enumerate(top_pct, start=1):
        print(f"  {i:>2}. {m['name']} — {m['value']:g}%")

    if top_fix:
        print(f"\n💰 Top {len(top_fix)} by EUR:")
        for i, m in enumerate(top_fix, start=1):
            print(f"  {i:>2}. {m['name']} — {m['value']:g} EUR")

    print(f"\n💯 {len(hits_100)} retailer(s) at {THRESHOLD}%+:")
    for m in hits_100:
        print(f"  • {m['name']} — {m['value']:g}%")

    print()
    send_summary(
        len(retailers), len(cashbacks),
        (t1 - t0) + (t2 - t1),
        top_pct, top_fix, hits_100,
    )

    if hits_100:
        seen = load_seen()
        new_hits = [m for m in hits_100 if m["idPool"] not in seen]
        if new_hits:
            print(f"\n{len(new_hits)} NEW 100% offer(s)!")
            for m in new_hits:
                ok = notify_100(m)
                print(f"  {m['name']} -> {'OK' if ok else 'FAILED'}")
                seen[m["idPool"]] = {
                    "name": m["name"],
                    "value": m["value"],
                    "detectedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            save_seen(seen)
        else:
            print("\nNo new 100% offer since last run.")

    print("\nDone.")


if __name__ == "__main__":
    main()