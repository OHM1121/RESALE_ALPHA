"""Android 15 negative-manual-exit plus cold-restart smoke.

This is an isolated emulator with no authenticated broker and no real holdings.
A rejected manual request must not fatal-crash the app; this does NOT prove
a user's real device holding state is free from crashes.
"""
import json
import time
from uuid import uuid4
import android_emulator_smoke_v054 as smoke
import android_emulator_smoke_v089 as base


def main():
    base.main()
    request_id=str(uuid4())
    payload=json.dumps({
        "market":"coin","strategy":"ma","code":"KRW-ERA",
        "position_id":742614,"expected_qty":"40","percent":100,
        "request_id":request_id,"user_confirmed":True},separators=(",",":"))
    result=smoke.adb("shell","am","start-foreground-service","-n",
        smoke.PACKAGE+"/"+smoke.SERVICE,
        "-a","com.artitrade.mobile.MANUAL_PAPER_SELL",
        "--es","request","'"+payload+"'",timeout=35,check=False)
    (smoke.BASE/"v084_manual_negative_dispatch.txt").write_text(result,encoding="utf-8")
    # Wait for the service's durable UUID result, not a synthetic completion.
    data=""
    for _ in range(15):
        data=smoke.adb("shell","run-as",smoke.PACKAGE,"cat",
            "shared_prefs/artitrade_manual_sale_v085.xml",
            timeout=12,check=False)
        if "result_"+request_id in data and (
                "PAPER_STRATEGY_NOT_RUNNING" in data or
                "COIN_POSITION_NOT_OPEN_OR_MISMATCH" in data or
                "FRESH_COIN_ORDERBOOK_MISSING" in data or
                "STRATEGY_NOT_RUNNING" in data or
                "PAPER_SELL_RUNTIME_NOT_READY" in data):
            break
        time.sleep(1.0)
    (smoke.BASE/"v084_manual_negative_result.xml").write_text(data,encoding="utf-8")
    if "result_"+request_id not in data:
        raise AssertionError("Manual negative request was not persisted by service")
    smoke.verify_no_fatal("v084_after_rejected_manual_exit")
    smoke.adb("shell","am","force-stop",smoke.PACKAGE)
    smoke.adb("shell","am","start","-W","-n",
        smoke.PACKAGE+"/"+smoke.ACTIVITY,timeout=60)
    time.sleep(9)
    pid=smoke.adb("shell","pidof",smoke.PACKAGE,timeout=15,check=False).strip()
    if not pid:
        raise AssertionError("App process died after cold restart following rejected exit")
    smoke.verify_no_fatal("v084_after_rejected_exit_restart")
    print("V084_ANDROID15_REJECTED_MANUAL_EXIT_AND_RESTART_NO_FATAL_PASS",flush=True)


if __name__=="__main__":main()
