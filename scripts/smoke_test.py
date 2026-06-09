"""End-to-end smoke test for the KV comp-analysis API.

Hits a *running* API against live Atlas and prints a PASS/FAIL summary:
    1. GET  /health
    2. GET  /subject-search   (discovers a real property_id)
    3. POST /rank-comps       (ranks comps for that property_id)

Usage (API must already be running):
    .venv/Scripts/python.exe scripts/smoke_test.py
    .venv/Scripts/python.exe scripts/smoke_test.py --base-url http://localhost:8000 \
        --q "10 EXAMPLE ST NW"
"""

from __future__ import annotations

import argparse
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the KV comp-analysis API.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument(
        "--q",
        default="",
        help="Subject search term. If omitted, a broad search is used to find any subject.",
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--api-key", default=None, help="x-api-key header, if the API requires one.")
    args = parser.parse_args()

    headers = {"x-api-key": args.api_key} if args.api_key else {}
    base = args.base_url.rstrip("/")
    passed = 0
    failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"PASS  {name}")
        else:
            failed += 1
            print(f"FAIL  {name}  {detail}")

    with httpx.Client(timeout=30.0, headers=headers) as client:
        # 1) health
        try:
            r = client.get(f"{base}/health")
            body = r.json()
            check(
                "GET /health",
                r.status_code == 200 and body.get("mongo_connected") is True,
                f"status={r.status_code} body={body}",
            )
        except Exception as exc:  # noqa: BLE001
            check("GET /health", False, repr(exc))
            print("\nAPI not reachable — start it first. See README_API.md.")
            return 1

        # 2) subject-search — find a real subject to rank
        query = args.q or "NW"  # broad fallback to surface any Calgary address
        subject_id = None
        try:
            r = client.get(f"{base}/subject-search", params={"q": query, "limit": args.limit})
            body = r.json()
            results = body.get("results", [])
            subject_id = results[0]["property_id"] if results else None
            check(
                "GET /subject-search",
                r.status_code == 200 and subject_id is not None,
                f"status={r.status_code} count={body.get('count')}",
            )
        except Exception as exc:  # noqa: BLE001
            check("GET /subject-search", False, repr(exc))

        # 3) rank-comps
        if subject_id:
            try:
                r = client.post(
                    f"{base}/rank-comps",
                    json={"subject_property_id": subject_id, "limit": args.limit},
                )
                body = r.json()
                comps = body.get("comparables", [])
                check(
                    "POST /rank-comps",
                    r.status_code == 200 and "subject" in body,
                    f"status={r.status_code} returned={body.get('returned_count')}",
                )
                print(
                    f"      subject={subject_id} "
                    f"comps_returned={len(comps)} candidate_count={body.get('candidate_count')}"
                )
                if comps:
                    top = comps[0]
                    print(
                        f"      top comp: score={top.get('score')} "
                        f"sale_price={top.get('sale_price')} reasons={top.get('reasons')}"
                    )
            except Exception as exc:  # noqa: BLE001
                check("POST /rank-comps", False, repr(exc))
        else:
            check("POST /rank-comps", False, "no subject_id resolved from search")

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
