#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, json, sys, time
from pathlib import Path
from typing import Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TOKEN_STORE = Path.home() / ".efa_tokens.json"
DEFAULT_BASE = "https://10.53.2.23"

# ---------------- token store ----------------
def _load_tokens():
    if TOKEN_STORE.exists():
        try:
            return json.loads(TOKEN_STORE.read_text())
        except Exception:
            return {}
    return {}

def _save_tokens(db):
    TOKEN_STORE.write_text(json.dumps(db, indent=2))

def _host_key(base: str) -> str:
    return base.rstrip("/")

def _store_tokens(base: str, access: str, refresh: str):
    db = _load_tokens()
    db[_host_key(base)] = {"access-token": access, "refresh-token": refresh, "ts": int(time.time())}
    _save_tokens(db)

def _read_tokens(base: str) -> Tuple[Optional[str], Optional[str]]:
    ent = _load_tokens().get(_host_key(base), {})
    return ent.get("access-token"), ent.get("refresh-token")

# ---------------- HTTP helpers ---------------
def _session(verify: bool) -> requests.Session:
    s = requests.Session()
    r = Retry(total=3, backoff_factor=0.3, status_forcelist=[429, 502, 503, 504], allowed_methods=["GET","POST","DELETE"])
    s.mount("http://", HTTPAdapter(max_retries=r))
    s.mount("https://", HTTPAdapter(max_retries=r))
    s.verify = verify
    if not verify:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return s

def _api(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"

# ---------------- auth endpoints -------------
def _token_ep(base: str) -> str:   return f"{base.rstrip('/')}/v1/auth/token/access-token"
def _refresh_ep(base: str) -> str: return f"{base.rstrip('/')}/v1/auth/token/refresh"

# ---------------- auth flows -----------------
def obtain_token(base: str, user: str, pwd: str, verify: bool):
    s = _session(verify)
    r = s.post(_token_ep(base), json={"username": user, "password": pwd}, timeout=15)
    r.raise_for_status()
    j = r.json()
    access, refresh = j.get("access-token"), j.get("refresh-token")
    if not access or not refresh:
        raise RuntimeError(f"Unexpected token response: {j}")
    _store_tokens(base, access, refresh)

def _refresh(base: str, verify: bool) -> str:
    s = _session(verify)
    access, refresh = _read_tokens(base)
    if not access or not refresh:
        raise RuntimeError("No saved tokens; run 'token' first.")
    r = s.post(_refresh_ep(base),
               headers={"Authorization": f"Bearer {access}"},
               json={"grant-type":"refresh_token","refresh-token":refresh},
               timeout=15)
    r.raise_for_status()
    j = r.json()
    access2, refresh2 = j.get("access-token"), j.get("refresh-token")
    if not access2 or not refresh2:
        raise RuntimeError(f"Unexpected refresh response: {j}")
    _store_tokens(base, access2, refresh2)
    return access2

# ---------------- raw GET --------------------
def raw_get(base: str, path: str, verify: bool, pretty: bool):
    s = _session(verify)
    access, _ = _read_tokens(base)
    if not access:
        raise RuntimeError("No saved access token; run 'token' first.")
    url = _api(base, path)
    hdr = {"Authorization": f"Bearer {access}"}
    r = s.get(url, headers=hdr, timeout=30)
    if r.status_code == 401:
        access = _refresh(base, verify)
        hdr["Authorization"] = f"Bearer {access}"
        r = s.get(url, headers=hdr, timeout=30)
    r.raise_for_status()

    ct = r.headers.get("content-type","").lower()
    if "application/json" in ct:
        if pretty:
            try:
                obj = r.json()
                print(json.dumps(obj, indent=2, ensure_ascii=False))
            except Exception:
                # fallback: pretty the raw text if possible
                try:
                    print(json.dumps(json.loads(r.text), indent=2, ensure_ascii=False))
                except Exception:
                    print(r.text)
        else:
            print(r.text)
    else:
        print(r.text)

# ---------------- CLI ------------------------
def main():
    ap = argparse.ArgumentParser(description="EFA/XCO raw fetcher (JSON pretty-print optional)")
    ap.add_argument("--base", default=DEFAULT_BASE, help=f"Base URL (default: {DEFAULT_BASE})")
    ap.add_argument("--insecure", action="store_true", help="Skip TLS verification")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("token", help="Get access/refresh tokens")
    sp.add_argument("--user", required=True)
    sp.add_argument("--password", required=True)

    sp = sub.add_parser("get", help="GET an endpoint; prints JSON (pretty by default)")
    sp.add_argument("--path", required=True, help="e.g. /v1/fabric/fabrics")
    # pretty by default; use --raw to disable
    sp.add_argument("--raw", action="store_true", help="Print server response as-is (disable pretty JSON)")
    sp.add_argument("--pretty", action="store_true", help=argparse.SUPPRESS)  # kept for symmetry if you want to force

    args = ap.parse_args()
    verify = not args.insecure

    try:
        if args.cmd == "token":
            obtain_token(args.base, args.user, args.password, verify)
            print("Access/Refresh tokens saved.")
        elif args.cmd == "get":
            pretty = not args.raw or args.pretty
            raw_get(args.base, args.path, verify, pretty)
    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else ""
        print(f"HTTP {e.response.status_code if e.response else 'ERR'}: {body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()
