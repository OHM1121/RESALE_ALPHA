#!/usr/bin/env python3
# ARTITRADE S21+ phone hub transport helper (Python 3.9+, standard library).
# This is NOT integrated into the existing Windows EXE trading engine yet.
# Never upload credentials, balances, or customer data to public GitHub.
import argparse
import getpass
import ipaddress
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def validated_url(raw):
    parsed = urllib.parse.urlsplit(raw.rstrip("/"))
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use HTTP on 127.0.0.1 or an encrypted Tailscale tunnel only")
    try:
        addr = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        raise ValueError("Use the numeric Tailscale address 100.64.0.0/10, not a public DNS name")
    if not (addr.is_loopback or addr in ipaddress.ip_network("100.64.0.0/10")):
        raise ValueError("Phone Hub disallows direct public or unencrypted Wi-Fi endpoints")
    if parsed.port != 8765 or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Expected http://<Tailscale-IP>:8765 without a path")
    return f"http://{parsed.hostname}:8765"


def request(base, token, method, endpoint, payload=None, params=None, timeout=8):
    base = validated_url(base)
    path = endpoint
    if params:
        path += "?" + urllib.parse.urlencode(params)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                 "Accept": "application/json", "Cache-Control": "no-store"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = {"error": "HTTP_" + str(e.code)}
        return dict(body, http_status=e.code)


def get_market(args):
    if args.market not in ("stock", "coin"):
        raise ValueError("market must be stock or coin")
    return args.market


def cli():
    ap = argparse.ArgumentParser(description="ARTITRADE private S21+ phone sync server")
    ap.add_argument("--url", default=os.getenv("ARTITRADE_HUB_URL", ""), help="http://100.x.y.z:8765 (over Tailscale only)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    get = sub.add_parser("get")
    put = sub.add_parser("put")
    for p in (get, put):
        p.add_argument("market", choices=["stock", "coin"])
        p.add_argument("strategy", help="stock: at,cw,shared / coin: general,ma,shared")
        p.add_argument("bucket", choices=["settings", "ledger", "research", "errors"])
    put.add_argument("json_file", help="local JSON file with an object")
    put.add_argument("--expected-revision", type=int, required=True)
    prefer = sub.add_parser("prefer")
    prefer.add_argument("market", choices=["stock", "coin"])
    prefer.add_argument("device", choices=["pc", "phone", "off"])
    sub.add_parser("health")
    args = ap.parse_args()
    if not args.url:
        ap.error("Set --url or ARTITRADE_HUB_URL to the phone Tailscale IP and port 8765")
    try:
        base = validated_url(args.url)
        token = "" if args.command == "health" else (os.getenv("ARTITRADE_HUB_TOKEN") or getpass.getpass("Phone Hub pairing token: "))
        if args.command == "status":
            result = request(base, token, "GET", "/v1/status")
        elif args.command == "health":
            result = request(base, "", "GET", "/health")
        elif args.command == "get":
            result = request(base, token, "GET", "/v1/record",
                params={"market": args.market, "strategy": args.strategy, "bucket": args.bucket})
        elif args.command == "put":
            with open(args.json_file, "r", encoding="utf-8") as f:
                value = json.load(f)
            if not isinstance(value, dict):
                raise ValueError("JSON payload must be an object")
            result = request(base, token, "PUT", "/v1/record", {
                "market": args.market, "strategy": args.strategy,
                "bucket": args.bucket, "expected_revision": args.expected_revision,
                "data": value
            })
        elif args.command == "prefer":
            result = request(base, token, "POST", "/v1/control",
                             {"market": args.market, "desired": args.device})
        else:
            raise ValueError("Unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    except (ValueError, OSError, urllib.error.URLError) as e:
        print("Phone Hub connection failed:", str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(cli())
