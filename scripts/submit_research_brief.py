#!/usr/bin/env python3
"""Validate, normalize, and submit an Open Equity research brief."""

import argparse
import json
import sys
from pathlib import Path

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["US", "HK"], required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--input", help="Path to JSON file. Defaults to stdin.")
    args = parser.parse_args()

    payload = _load_payload(args.input)
    payload["market"] = args.market

    response = requests.post(f"{args.base_url.rstrip('/')}/research", json=payload, timeout=30)
    response.raise_for_status()
    print(json.dumps(response.json(), indent=2, sort_keys=True))
    return 0



def _load_payload(path: str | None) -> dict:
    raw = Path(path).read_text() if path else sys.stdin.read()
    if not raw.strip():
        raise SystemExit("No JSON payload provided")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise SystemExit("Research brief payload must be a JSON object")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
