#!/usr/bin/env python3
"""GitHub Actions on-device ARTITRADE UI smoke. No broker login or live orders.

Phase A: install and launch real unified APK, tap 5 tabs, inspect AndroidRuntime.
Phase B: disable service ONLY inside disposable emulator, inject read-only
synthetic ledger fixtures, verify all six strategy dashboard/list/research
routes and actual Korean/KST SELL explanations. Save screenshots and UI trees.
"""
import json
import os
import re
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

PACKAGE="com.artitrade.unified.s21dex"
ACTIVITY="com.artitrade.mobile.UnifiedVisualActivity"
SERVICE="com.artitrade.mobile.UnifiedTradingService"
BASE=Path(os.environ.get("ARTITRADE_SMOKE_DIR","ui-smoke"))
BASE.mkdir(exist_ok=True,parents=True)
FAILURES=[]
SCREENSHOT_COUNT=0

def run(*argv,timeout=50,input_data=None,check=True):
    data=subprocess.run([str(x) for x in argv],input=input_data,
                        capture_output=True,timeout=timeout)
    if check and data.returncode:
        raise RuntimeError(" ".join(map(str,argv))+" rc="+str(data.returncode)
                           +"\n"+data.stdout.decode("utf-8","replace")[-1200:]
                           +"\n"+data.stderr.decode("utf-8","replace")[-1200:])
    return data.stdout.decode("utf-8","replace")

def adb(*argv,**kwargs):
    return run("adb",*argv,**kwargs)

def verify_no_fatal(stage):
    log=adb("logcat","-d","-v","brief","AndroidRuntime:E","*:S",timeout=50,check=False)
    (BASE/(stage+"_androidruntime.log")).write_text(log,encoding="utf-8")
    if "FATAL EXCEPTION" in log and (PACKAGE in log or "com.artitrade.mobile" in log):
        raise AssertionError("App AndroidRuntime FATAL EXCEPTION at "+stage+"\n"+log[-2300:])

def capture(label):
    global SCREENSHOT_COUNT
    SCREENSHOT_COUNT+=1
    name=re.sub(r"[^A-Za-z0-9._-]","_",label)
    try:
        data=subprocess.run(["adb","exec-out","screencap","-p"],
                            capture_output=True,timeout=25,check=True).stdout
        if len(data)>2000:
            (BASE/f"{SCREENSHOT_COUNT:02d}_{name}.png").write_bytes(data)
    except Exception as error:
        (BASE/f"{SCREENSHOT_COUNT:02d}_{name}.error.txt").write_text(str(error))
    return name

def dump(label):
    name=capture(label)
    for attempt in range(4):
        try:
            adb("shell","uiautomator","dump","/sdcard/artitrade_ui.xml",timeout=35)
            raw=subprocess.run(["adb","exec-out","cat","/sdcard/artitrade_ui.xml"],
                               capture_output=True,timeout=25,check=True).stdout
            if b"<hierarchy" not in raw:raise AssertionError("UI hierarchy missing")
            (BASE/f"{SCREENSHOT_COUNT:02d}_{name}.xml").write_bytes(raw)
            return ET.fromstring(raw)
        except Exception as exc:
            if attempt==3:raise
            time.sleep(1.5)
    raise RuntimeError("Unreachable")

def nodes(tree):
    return list(tree.iter("node"))

def contains(tree, text):
    return any(text in (n.get("text","")+" "+n.get("content-desc",""))
               for n in nodes(tree))

def describe(tree):
    items=[]
    for n in nodes(tree):
        text=(n.get("text","")+" "+n.get("content-desc","")).strip()
        if text:items.append(text[:135])
    return items

def tap(tree,query):
    options=[n for n in nodes(tree) if query in
             (n.get("text","")+" "+n.get("content-desc",""))]
    if not options:
        raise AssertionError("Missing UI control "+repr(query)+"; visible="+str(describe(tree)[-45:]))
    # Prefer clickable parent rectangle, not an inaccessible child text label.
    elem=next((n for n in options if n.get("clickable")=="true"),options[0])
    parent_by_bounds=next((n for n in options if n.get("clickable")=="true"),elem)
    bounds=parent_by_bounds.get("bounds",elem.get("bounds",""))
    pos=re.findall(r"\d+",bounds)
    if len(pos)!=4:raise AssertionError("Invalid bounds for "+query+": "+bounds)
    x=(int(pos[0])+int(pos[2]))//2
    y=(int(pos[1])+int(pos[3]))//2
    adb("shell","input","tap",str(x),str(y),timeout=15)
    time.sleep(.65)

def choose(label,old=None,checks=4):
    tree=old
    for i in range(checks):
        if tree is None:tree=dump("before_"+label+"_"+str(i))
        if contains(tree,label):
            tap(tree,label)
            return dump("tap_"+label)
        adb("shell","input","swipe","1000","840","1000","420","340",timeout=15)
        time.sleep(.4)
        tree=dump("scroll_to_"+label+"_"+str(i))
    raise AssertionError("Could not navigate to "+label+"; visible="+str(describe(tree)[-45:]))

def check_screen(label,expected,scroll=2):
    tree=dump(label)
    seen=list(expected)
    for i in range(scroll+1):
        seen=[word for word in seen if not contains(tree,word)]
        if not seen:return tree
        if i<scroll:
            adb("shell","input","swipe","1050","900","1050","380","330")
            time.sleep(.4)
            tree=dump(label+"_scroll_"+str(i))
    raise AssertionError(label+" missing "+str(seen)+"; visible="+str(describe(tree)[-50:]))

def make_rows(label,symbol,tf):
    prefix=label.split("_")[0]
    return {
        "positions":[{
            "name":label+"_보유","code":symbol,"market":symbol,
            "tf":tf,"strategy":prefix,"qty":2,"entry_price":51000,
            "net_pnl":2400,"entry_at":"2026-11-13T09:00:00+09:00",
            "entry_reason":"BUY_SIGNAL","first_sold":False}],
        "pending":[{
            "name":label+"_대기","code":symbol,"market":symbol,
            "tf":tf,"strategy":prefix,"qty":1,"limit_price":48000,
            "submitted_at":"2026-11-13T09:01:00+09:00",
            "reason":"NO_BID"}],
        "executions":[{
            "name":label+"_매도","code":symbol,"market":symbol,
            "tf":tf,"strategy":prefix,"side":"SELL","qty":1,
            "net_profit":51000,"time":"2026-11-13T00:30:00Z",
            "ts":"2026-11-13T00:30:00Z","exit_reason":"STOP_LOSS"}],
        "realized_pnl":51000,"unrealized_pnl":2400,
        "actual_target":{"evaluated":1,"hit":0,"miss":1,"pending":0,"rate_pct":0}
    }

def make_research(label,symbol,tf):
    return {"items":[{"name":label+"_연구","code":symbol,"market":symbol,
        "tf":tf,"strategy":label.split("_")[0],"score":71,
        "bought":False,"entry_observed":True,"outcome":"MISS",
        "reason":"NO_BID","at":"2026-11-13T00:00:00Z"}],
        "signals":1,"source_total":1,"loaded_items":1,"load_status":"OK",
        "actual_summary":{"evaluated":0,"hit":0,"miss":0,"pending":0,"rate_pct":None},
        "virtual_summary":{"evaluated":1,"hit":0,"miss":1,"pending":0,"rate_pct":0}}

def fixture():
    at=make_rows("AT_삼성전자","005930","5m")
    ma=make_rows("MA_SK하이닉스","000660","30m")
    gmo=make_rows("GMO_기아","000270","gmo")
    close=make_rows("CLOSE_네이버","035420","close")
    coin=make_rows("COIN_비트코인","KRW-BTC","5m")
    coinma=make_rows("COINMA_이더리움","KRW-ETH","60m")
    at["ma_subledger"]=ma
    at["gmo"]=gmo
    at_research=make_research("AT_삼성전자","005930","5m")
    at_research["ma"]=make_research("MA_SK하이닉스","000660","30m")
    at_research["gmo"]=make_research("GMO_기아","000270","gmo")
    return {
        "stock":{
            "at":{"ledger":at,"research":at_research},
            "cw":{"ledger":close,"research":make_research("CLOSE_네이버","035420","close")}
        },
        "coin":{
            "general":{"ledger":coin,"research":make_research("COIN_비트코인","KRW-BTC","5m")},
            "ma":{"ledger":coinma,"research":make_research("COINMA_이더리움","KRW-ETH","60m")}
        }
    }

def put_pref(name,entries):
    contents=['<?xml version="1.0" encoding="utf-8" standalone="yes" ?>','<map>']
    for key,value in entries.items():
        if isinstance(value,str):
            contents.append(f'<string name="{escape(key)}">{escape(value)}</string>')
        else:
            contents.append(f'<long name="{escape(key)}" value="{int(value)}" />')
    contents.append("</map>")
    body=("\n".join(contents)+"\n").encode("utf-8")
    cmd="mkdir -p shared_prefs && cat > shared_prefs/"+name+".xml"
    run("adb","shell","run-as",PACKAGE,"sh","-c",cmd,input_data=body,timeout=20)

def start_app(stage):
    adb("shell","am","start","-W","-n",PACKAGE+"/"+ACTIVITY,timeout=60)
    time.sleep(2.8)
    tree=dump(stage+"_home")
    if not contains(tree,"ARTITRADE"):
        raise AssertionError("No ARTITRADE initial frame; "+str(describe(tree)[:60]))
    return tree

def real_boot():
    # Two APKs exist deliberately: real service-enabled boot and an isolated
    # synthetic-fixture build. Boot the actual service-enabled one first.
    apk=Path("downloaded-apks/ARTITRADE_UNIFIED_EMULATOR_x86_64.apk")
    if not apk.is_file():
        raise AssertionError("Real-boot APK missing: "+str(apk))
    print("APK_INSTALLING",apk,apk.stat().st_size,flush=True)
    print(adb("install","-r",str(apk),timeout=180),flush=True)
    adb("shell","pm","grant",PACKAGE,"android.permission.POST_NOTIFICATIONS",check=False)
    adb("logcat","-c",check=False)
    tree=start_app("real_boot")
    check_screen("real_home",["대시보드"],scroll=0)
    for title in ("보유종목","매수대기","체결이력","연구자료","대시보드"):
        tree=choose(title,tree)
        if not contains(tree,title):
            raise AssertionError("Navigation failed: "+title)
    verify_no_fatal("real_boot")

def fixtures():
    # Replace the same-package emulator APK with a test-ONLY manifest that
    # has the trading service disabled. This avoids privileged 'pm disable'
    # (forbidden to the shell on Android 15) and prevents the paper engine
    # from overwriting synthetic local-record fixtures.
    fixture_apk=Path("downloaded-apks/ARTITRADE_UNIFIED_EMULATOR_FIXTURE_x86_64.apk")
    if not fixture_apk.is_file():
        raise AssertionError("Missing offline fixture variant, not safe to seed UI data")
    adb("shell","am","force-stop",PACKAGE)
    print(adb("install","-r",str(fixture_apk),timeout=180),flush=True)
    adb("shell","am","force-stop",PACKAGE)
    put_pref("artitrade_local_records_v075",{
        "records":json.dumps(fixture(),ensure_ascii=False,separators=(",",":")),
        "updated_at":int(time.time()*1000)})
    put_pref("artitrade_ui_navigation_v1",{
        "market":"stock","strategy":"at","page":"home","stock_filter_v2":"current",
        "coin_filter_v2":"current"})
    tree=start_app("fixture_boot")
    # Load source data asynchronously before checking the first strategy.
    time.sleep(7.0)
    configurations=[
        ("주식","기준봉","AT_삼성전자"),
        ("주식","MA","MA_SK하이닉스"),
        ("주식","종가베팅","CLOSE_네이버"),
        ("주식","수급매매","GMO_기아"),
        ("코인","기준봉","COIN_비트코인"),
        ("코인","MA","COINMA_이더리움")
    ]
    for market,strategy,label in configurations:
        tree=dump("before_"+label)
        if not contains(tree,market):
            tree=choose(market,tree)
        elif market=="코인" and not contains(tree,"KRW TOP35"):
            tree=choose("코인",tree)
        elif market=="주식" and contains(tree,"업비트"):
            tree=choose("주식",tree)
        tree=choose(strategy,tree)
        tree=choose("보유종목",tree)
        check_screen(label+"_positions",[label+"_보유"])
        tree=choose("매수대기")
        check_screen(label+"_pending",[label+"_대기"])
        tree=choose("체결이력")
        check_screen(label+"_executions",[label+"_매도","매도 원인","09:30:00 KST"])
        tree=choose("연구자료")
        check_screen(label+"_research",["개별 연구 판단 근거",label+"_연구"],scroll=6)
        tree=choose("대시보드")
        print("FIXTURE_STRATEGY_OK",market,strategy,label,flush=True)
    verify_no_fatal("fixtures")

def main():
    print("DEVICE",adb("shell","getprop","ro.product.model").strip(),flush=True)
    print("API",adb("shell","getprop","ro.build.version.sdk").strip(),flush=True)
    try:
        real_boot()
        fixtures()
        (BASE/"SMOKE_RESULT.json").write_text(json.dumps({
            "ok":True,"real_boot":True,"all_six_strategy_fixture_views":True,
            "navigated_pages":["home","positions","pending","executions","research"],
            "no_real_broker_api":True,"source":"synthetic fixtures only"},
            ensure_ascii=False,indent=2))
        print("ANDROID_EMULATOR_REAL_BOOT_AND_ALL_6_STRATEGY_UI_PASS",flush=True)
    except Exception as error:
        traceback.print_exc()
        (BASE/"SMOKE_FAILURE.txt").write_text(traceback.format_exc())
        try:dump("failure_"+type(error).__name__)
        except Exception:pass
        try:verify_no_fatal("failure")
        except Exception as detail:
            (BASE/"SMOKE_ANDROID_FATAL.txt").write_text(str(detail))
        raise
    finally:
        try:adb("logcat","-d","-v","threadtime",timeout=65,check=False)
        except Exception:pass

if __name__=="__main__":main()
