#!/usr/bin/env python3
"""Read-only continuous PC mirror of phone MSERVER strategy-record revisions.

This companion never edits ARTITRADE Windows EXE SQLite files, sends trade
orders, enables broker leases or sends PC history back to the phone. Use a
private Tailscale/USB loopback route and pair with the MSERVER bearer token.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from artitrade_hub_client import request, validated_url

STRATEGIES={"stock":{"shared","at","cw"},"coin":{"shared","general","ma"}}
BUCKETS={"ledger","research","errors","settings"}
STATE=".artitrade_mserver_mirror_index.json"


def api(server,token,path,params=None):
    return request(server,token,"GET",path,params=params,timeout=20)


def _atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(value,ensure_ascii=False,separators=(",",":"),default=str).encode("utf8")
    name=None
    try:
        with tempfile.NamedTemporaryFile(dir=str(path.parent),prefix=".mirror_",suffix=".tmp",
                                         delete=False) as target:
            name=target.name
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(name,path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def sync_once(server,token,output):
    """Compare one small index, GET only changed revisions; return changed count."""
    server=validated_url(server)
    if not isinstance(token,str) or not __import__("re").fullmatch(r"[0-9a-fA-F]{64}",token):
        raise ValueError("Invalid MSERVER pairing token")
    output=Path(output).resolve()
    index=api(server,token,"/v1/records/index")
    if not index.get("ok") or not isinstance(index.get("records"),list):
        raise RuntimeError("MSERVER change index unavailable: "+str(index.get("error")))
    entries=index["records"]
    if len(entries)>100:
        raise ValueError("Unexpected MSERVER index size")
    # Reject the whole index before touching disk if any path is suspicious.
    for item in entries:
        if not isinstance(item,dict):
            raise ValueError("Invalid record index entry")
        market=item.get("market");strategy=item.get("strategy");bucket=item.get("bucket")
        if strategy not in STRATEGIES.get(market,set()) or bucket not in BUCKETS:
            raise ValueError("Invalid record key in MSERVER response")
        if type(item.get("revision")) is not int or item["revision"]<1:
            raise ValueError("Invalid MSERVER revision")
    scope=hashlib.sha256((server+"|"+token).encode("ascii")).hexdigest()
    statefile=output/STATE
    try:
        state=json.loads(statefile.read_text(encoding="utf8"))
        revisions=state.get("revisions",{}) if state.get("server")==scope else {}
        if not isinstance(revisions,dict):revisions={}
    except (OSError,ValueError,TypeError):
        revisions={}
    changes=0
    for item in entries:
        market,strategy,bucket=(item[k] for k in ("market","strategy","bucket"))
        revision=item["revision"]
        key="/".join((market,strategy,bucket))
        destination=output/market/strategy/(bucket+".json")
        if revisions.get(key)==revision and destination.is_file():
            continue
        response=api(server,token,"/v1/record",
                     params={"market":market,"strategy":strategy,"bucket":bucket})
        if not response.get("ok") or not response.get("exists"):
            raise RuntimeError("MSERVER source record unavailable: "+key)
        if int(response.get("revision") or 0)!=revision:
            # The index changed between requests. Retry next poll, never
            # checkpoint an older/partial record under the new revision.
            raise RuntimeError("MSERVER source revision changed: "+key)
        data=response.get("data")
        if not isinstance(data,dict):
            raise ValueError("Invalid original strategy ledger response: "+key)
        _atomic_json(destination,data)
        revisions[key]=revision
        changes+=1
    _atomic_json(statefile,{"schema":"ARTITRADE_PC_LIVE_MIRROR_V1",
        "server":scope,"revisions":revisions,"received_at":int(time.time())})
    return changes


def main():
    parser=argparse.ArgumentParser(description="ARTITRADE PC read-only live ledger mirror")
    parser.add_argument("--server",required=True,help="http://127.0.0.1:8765 (USB) or Tailscale IP")
    parser.add_argument("--output",required=True,help="Dedicated folder; never use EXE live DB location")
    parser.add_argument("--interval",type=float,default=2.0)
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    if not 0.5<=args.interval<=60:
        parser.error("--interval must be 0.5..60 seconds")
    token=os.environ.get("ARTITRADE_MSERVER_TOKEN") or getpass.getpass("MSERVER token: ").strip()
    target=Path(args.output).expanduser().resolve()
    print("Read-only PC mirror started; unchanged revisions are not re-downloaded.",flush=True)
    while True:
        try:
            changed=sync_once(args.server,token,target)
            if changed:print(f"Updated {changed} record(s) under {target}",flush=True)
        except (OSError,ValueError,RuntimeError) as error:
            print("Mirror sync skipped:",str(error)[:250],flush=True)
            if args.once:raise
        if args.once:break
        time.sleep(args.interval)


if __name__=="__main__":
    main()
