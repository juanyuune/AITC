#!/usr/bin/env python3
"""
diagnose_mops_response.py
--------------------------
Prints the RAW HTML response from MOPS for a given company so we can
see exactly what is being returned, instead of guessing why the
table parser finds nothing.

This will tell us one of three things:
  A) MOPS returns an empty/error page  -> company truly has no filings
  B) MOPS returns a table but in a different HTML structure
     -> our parser needs fixing, not the company
  C) MOPS returns a redirect or different page entirely
     -> we are hitting the wrong endpoint/parameters

Usage:
  python diagnose_mops_response.py 2850
  python diagnose_mops_response.py 2850 --typek sii
"""

import sys
import argparse
import requests

MOPS_SEARCH_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t203sb01"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://mopsov.twse.com.tw/mops/web/t203sb01",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("co_id", help="Stock code to test")
    parser.add_argument("--typek", default="sii", help="TYPEK value (default: sii)")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)

    payload = {
        "co_id": args.co_id,
        "step": "1",
        "firstin": "1",
        "off": "1",
        "TYPEK": args.typek,
    }

    print("=" * 70)
    print(f"  Raw MOPS response — co_id={args.co_id}  TYPEK={args.typek}")
    print("=" * 70)
    print(f"  POST {MOPS_SEARCH_URL}")
    print(f"  payload: {payload}")
    print()

    try:
        resp = session.post(MOPS_SEARCH_URL, data=payload, timeout=20)
    except requests.RequestException as e:
        print(f"  REQUEST FAILED: {e}")
        sys.exit(1)

    print(f"  HTTP status code : {resp.status_code}")
    print(f"  Content-Type     : {resp.headers.get('Content-Type')}")
    print(f"  Content length   : {len(resp.text)} chars")
    print(f"  Encoding detected: {resp.encoding}")
    print()
    print("-" * 70)
    print("  RAW RESPONSE BODY (first 3000 characters):")
    print("-" * 70)
    print(resp.text[:3000])
    print()
    print("-" * 70)
    print("  Does response contain a <table>?  ", "<table" in resp.text.lower())
    print("  Does response contain '查無資料'?  ", "查無資料" in resp.text)
    print("  Does response contain '無此公司'?  ", "無此公司" in resp.text)
    print("  Does response contain 'error'?     ", "error" in resp.text.lower())
    print("-" * 70)

    # Save full response to file for inspection
    out_file = f"/tmp/mops_response_{args.co_id}_{args.typek}.html"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(resp.text)
    print(f"\n  Full response saved to: {out_file}")
    print(f"  Inspect with: cat {out_file}")


if __name__ == "__main__":
    main()