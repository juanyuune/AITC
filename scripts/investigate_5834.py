#!/usr/bin/env python3
"""
investigate_5834.py
-------------------
Systematically investigates why 合庫金控 (5834) returns no filings
from the MOPS automated endpoint. Tries every known TYPEK value,
both stock codes (5834 and its subsidiary codes), and both report
types (consolidated C and standalone B).

Run this ONCE manually:
  python investigate_5834.py

Read the output carefully — it will tell you exactly why 5834 fails
and what to do about it.
"""

import requests
import time
import sys
from bs4 import BeautifulSoup
from datetime import datetime

MOPS_SEARCH_URL   = "https://mopsov.twse.com.tw/mops/web/ajax_t203sb01"
MOPS_DOWNLOAD_URL = "https://mopsov.twse.com.tw/server-java/FileDownLoad"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://mopsov.twse.com.tw/mops/web/t203sb01",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

# All known TYPEK values on MOPS
TYPEK_OPTIONS = ["sii", "otc", "rotc", "pub", "tpex"]

# 5834 itself plus related subsidiary codes to try
CODES_TO_TRY = ["5834", "2803"]   # 2803 = 合庫銀行 (the bank under 合庫金)

# Both report types
REPORT_IDS = ["C", "B"]   # C = consolidated, B = standalone (unconsolidated)


def search_mops(co_id: str, typek: str) -> list[tuple]:
    """Search MOPS for filings. Returns list of (year, season) tuples."""
    try:
        resp = SESSION.post(
            MOPS_SEARCH_URL,
            data={"co_id": co_id, "step": "1", "firstin": "1", "off": "1", "TYPEK": typek},
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    filings = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            y = cells[0].get_text(strip=True)
            s = cells[1].get_text(strip=True)
            if y.isdigit() and len(y) == 4 and s in ("1","2","3","4"):
                filings.append((y, s))
    return filings


def try_download(co_id: str, year: str, season: str, report_id: str) -> tuple[bool, int]:
    """Try to download one file. Returns (success, size_bytes)."""
    try:
        resp = SESSION.get(
            MOPS_DOWNLOAD_URL,
            params={
                "functionName": "t164sb01",
                "step": "9",
                "co_id": co_id,
                "year": year,
                "season": season,
                "report_id": report_id,
            },
            timeout=30,
        )
        content_type = resp.headers.get("Content-Type", "")
        if "html" in content_type.lower() or resp.text.strip().startswith("<!DOCTYPE"):
            return False, 0
        if len(resp.content) < 500:
            return False, len(resp.content)
        return True, len(resp.content)
    except requests.RequestException:
        return False, 0


def run_investigation():
    print("=" * 65)
    print("  合庫金控 (5834) — MOPS Filing Investigation")
    print(f"  Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    print()

    found_anything = False

    # ── Phase 1: Search endpoint with every TYPEK + code combo ──────────────
    print("PHASE 1 — Search endpoint (ajax_t203sb01)")
    print("-" * 65)
    for code in CODES_TO_TRY:
        for typek in TYPEK_OPTIONS:
            print(f"  Testing co_id={code}  TYPEK={typek} ...", end="  ")
            filings = search_mops(code, typek)
            time.sleep(0.5)
            if filings:
                print(f"✓  FOUND {len(filings)} filings: {filings[:3]}{'...' if len(filings)>3 else ''}")
                found_anything = True
            else:
                print("✗  no results")
    print()

    # ── Phase 2: Direct download attempt (bypass search) ────────────────────
    print("PHASE 2 — Direct download attempt (FileDownLoad endpoint)")
    print("-" * 65)
    print("  Trying to download 5834 2024Q3 directly with all report_id values ...")
    print()
    for code in CODES_TO_TRY:
        for rid in REPORT_IDS:
            print(f"  co_id={code}  year=2024  season=3  report_id={rid} ...", end="  ")
            ok, size = try_download(code, "2024", "3", rid)
            time.sleep(0.8)
            if ok:
                print(f"✓  SUCCESS  ({size:,} bytes) ← FILE EXISTS")
                found_anything = True
            else:
                print(f"✗  failed  ({size} bytes — HTML or empty)")

    # ── Phase 3: Try older quarters ─────────────────────────────────────────
    print()
    print("PHASE 3 — Try older quarters for 5834 (C report only)")
    print("-" * 65)
    for year in ["2023", "2022", "2021", "2020"]:
        for season in ["4", "3", "2", "1"]:
            print(f"  co_id=5834  year={year}  season={season}  report_id=C ...", end="  ")
            ok, size = try_download("5834", year, season, "C")
            time.sleep(0.5)
            if ok:
                print(f"✓  SUCCESS  ({size:,} bytes)")
                found_anything = True
            else:
                print(f"✗  not available")

    # ── Conclusion ───────────────────────────────────────────────────────────
    print()
    print("=" * 65)
    print("  CONCLUSION")
    print("=" * 65)
    if found_anything:
        print()
        print("  ✓ Filing(s) found. Note the co_id and TYPEK/report_id")
        print("    combination that worked above.")
        print("  → Update INS_COMPANIES in mops_ins_downloader.py with")
        print("    the correct typek value for 5834.")
        print("  → If a different co_id worked (e.g. 2803), add it as")
        print("    an alias in the downloader.")
    else:
        print()
        print("  ✗ No XBRL filings found for 5834 or 2803 with any")
        print("    TYPEK or report_id combination.")
        print()
        print("  This means one of:")
        print("  1. 合庫金控 files ONLY in PDF format on MOPS (most likely)")
        print("     → Manually confirm at: https://mopsov.twse.com.tw/mops/web/t203sb01")
        print("       Search 5834, Q4 2024. If the filing shows 'PDF' only,")
        print("       there is no XBRL to download.")
        print("  2. Filed under a holding company code not tried above")
        print("     → Ask your consultant or check MOPS manually.")
        print()
        print("  Recommended action for the report:")
        print("    Document this as a confirmed MOPS limitation, not a")
        print("    system error. State: '合庫金控 (5834) does not publish")
        print("    XBRL filings on MOPS. Manual import from PDF is required.'")
    print()


if __name__ == "__main__":
    run_investigation()
