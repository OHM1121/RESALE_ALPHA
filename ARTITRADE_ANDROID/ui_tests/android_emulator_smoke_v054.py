#!/usr/bin/env python3
"""GitHub Actions on-device ARTITRADE UI smoke. No broker login or live orders.

Phase A: install and launch real unified APK, tap 5 tabs, inspect AndroidRuntime.
Phase B: disable service ONLY inside disposable emulator, inject read-only
synthetic ledger fixtures, verify all eight strategy dashboard/list/research
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
            # Android 15 uiautomator intermittently ignores a custom XML
            # destination. The supported default is /sdcard/window.xml.
            # Remove it BEFORE every dump so a stale file cannot pass a test.
            xml_target="/sdcard/window.xml"
            adb("shell","rm","-f",xml_target,timeout=15)
            diagnostic=adb("shell","uiautomator","dump",timeout=35,check=False)
            raw=subprocess.run(["adb","exec-out","cat",xml_target],
                               capture_output=True,timeout=25,check=False).stdout
            if b"<hierarchy" not in raw:
                (BASE/f"{SCREENSHOT_COUNT:02d}_{name}_xml_failure.txt").write_text(
                    diagnostic+"\n"+adb("shell","ls","-l",xml_target,check=False),encoding="utf-8")
                raise AssertionError("Fresh default UI hierarchy unavailable")
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

def check_screen(label,expected,scroll=2,drawer=False):
    tree=dump(label)
    seen=list(expected)
    for i in range(scroll+1):
        seen=[word for word in seen if not contains(tree,word)]
        if not seen:return tree
        if i<scroll:
            if drawer:
                # The settings panel is right-aligned and only dp(380) wide.
                # The old fixed x=1050 swipe was OUTSIDE the drawer on
                # 2340px-wide Android 15 and never scrolled the export bars.
                # Target the actual right-hand ScrollView, not its dim scrim.
                size=adb("shell","wm","size",check=False)
                import re
                found=re.search(r"(?:Override|Physical) size:\\s*(\\d+)x(\\d+)",size)
                width=int(found.group(1)) if found else 1920
                height=int(found.group(2)) if found else 1080
                x=width-max(80,min(140,width//12))
                y1=max(180,min(height-90,int(height*.82)))
                y2=max(50,min(int(height*.26),y1-160))
                adb("shell","input","swipe",str(x),str(y1),str(x),
                    str(y2),"460")
            else:
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
    stocktrend=make_rows("TRENDSTOCK_삼성전자","005930","5m")
    cointrend=make_rows("TRENDCOIN_비트코인","KRW-BTC","30m")
    for ledger in (stocktrend,cointrend):
        ledger["positions"][0]["status"]="TREND_OPEN"
        ledger["executions"][0]["status"]="CLOSED"
    def watch_for(label,code,frame):
        return {"code":label+"_대기","frame":frame,"status":"WATCH_PENDING",
                "market":code,"rsi20_live":34.5,"armed":True,
                "price":12500,"touched_30":False,"bar_end":"2026-11-13T10:05:00+09:00"}
    payload = {
        "market_trend_watch":{
            "stock":[watch_for("TRENDSTOCK_삼성전자","005930","5m")],
            "coin":[watch_for("TRENDCOIN_비트코인","KRW-BTC","30m")]},
        "market_trend_research":{
            "stock":[{"id":"MT54:SIG:stock:005930:5m:1","tf":"5m",
                      "code":"005930","entry_mode":"MARKET_TREND","bought":True,
                      "execution_mode":"PAPER","reason":"BOUGHT","rsi20_live":29}],
            "coin":[{"id":"MT54:SIG:coin:KRW-BTC:30m:1","tf":"30m",
                     "code":"KRW-BTC","entry_mode":"MARKET_TREND","bought":True,
                     "execution_mode":"PAPER","reason":"BOUGHT","rsi20_live":28}]},
        "stock":{
            "at":{"ledger":at,"research":at_research},
            "cw":{"ledger":close,"research":make_research("CLOSE_네이버","035420","close")},
            "trend":{"ledger":stocktrend,"research":{}}
        },
        "coin":{
            "general":{"ledger":coin,"research":make_research("COIN_비트코인","KRW-BTC","5m")},
            "ma":{"ledger":coinma,"research":make_research("COINMA_이더리움","KRW-ETH","60m")},
            "trend":{"ledger":cointrend,"research":{}}
        }
    }
    # A current independent signal is also emitted by the shared MT journal.
    # Deduplicate the repeated MT54 signal but preserve legacy MT43 signals
    # attributed to AT/MA. They must appear ONLY in standalone Trend research.
    for market,legacy_strategy,code in (
            ("stock","at","005930"),("coin","ma","KRW-BTC")):
        recent=payload["market_trend_research"][market][0]
        recent["strategy"]="trend"
        payload[market]["trend"]["research"]={"items":[dict(recent)]}
        payload["market_trend_research"][market].append({
            "id":f"MT43:SIG:{market}:{code}:30m:LEGACY",
            "strategy":legacy_strategy,"tf":"30m","code":code,
            "entry_mode":"MARKET_TREND","bought":False,
            "entry_observed":True,"execution_mode":"SHADOW_UNFILLED",
            "at":"2026-11-12T23:30:00+09:00","reason":"RSI30_VIRTUAL"})
        payload["market_trend_research"][market].append({
            "id":f"AT:SIG:{market}:{code}:5m:UNRELATED",
            "strategy":"at","tf":"5m","code":code,
            "entry_mode":"AT","bought":True,"reason":"AT_ONLY"})
    return payload


def put_pref(name,entries):
    contents=['<?xml version="1.0" encoding="utf-8" standalone="yes" ?>','<map>']
    for key,value in entries.items():
        if isinstance(value,str):
            contents.append(f'<string name="{escape(key)}">{escape(value)}</string>')
        else:
            contents.append(f'<long name="{escape(key)}" value="{int(value)}" />')
    contents.append("</map>")
    body=("\n".join(contents)+"\n").encode("utf-8")
    # Android 15 adb-shell re-tokenizes sh -c and loses its quoted command.
    # Use two unambiguous argv commands instead; tee reads the XML on stdin.
    adb("shell","run-as",PACKAGE,"mkdir","-p","shared_prefs",timeout=20)
    run("adb","shell","run-as",PACKAGE,"tee",
        "shared_prefs/"+name+".xml",input_data=body,timeout=20)

def _launcher_not_responding(tree):
    # Android 15 system image may cold-boot with Quickstep/Pixel Launcher ANR
    # before ARTITRADE draws its first frame. Never waive an ARTITRADE crash.
    return any(contains(tree,name+" isn't responding")
               for name in ("Quickstep","Pixel Launcher","Launcher3"))

def start_app(stage):
    adb("shell","am","start","-W","-n",PACKAGE+"/"+ACTIVITY,timeout=60)
    time.sleep(3.0)
    tree=dump(stage+"_home")
    for retry in range(2):
        if contains(tree,"ARTITRADE") or not _launcher_not_responding(tree):
            break
        print("ANDROID15_SYSTEM_LAUNCHER_ANR_RETRY",retry+1,
              str(describe(tree)[:15]),flush=True)
        # Close ONLY the system HOME process. Never force-stop ARTITRADE or
        # suppress a startup exception; verify_no_fatal still runs afterwards.
        adb("shell","input","keyevent","KEYCODE_BACK",check=False)
        for home_pkg in ("com.android.launcher3",
                         "com.google.android.apps.nexuslauncher"):
            adb("shell","am","force-stop",home_pkg,check=False)
        time.sleep(5)
        adb("shell","am","start","-W","-n",
            PACKAGE+"/"+ACTIVITY,timeout=60)
        time.sleep(4)
        tree=dump(stage+"_home_launcher_retry_"+str(retry+1))
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
    # A cold v081 boot waits for authentic two-exchange subscriptions and
    # quote-safe ledger readiness; the emulator has no broker credentials.
    # It must eventually show an explicitly degraded UI, never fake LIVE.
    for attempt in range(55):
        tree=dump("v081_cold_preload_"+str(attempt))
        if contains(tree,"대시보드"):break
        if not contains(tree,"ARTITRADE"):
            raise AssertionError("Cold-start app disappeared before preload")
        time.sleep(1.0)
    if not contains(tree,"대시보드"):
        raise AssertionError("V081_COLD_PRELOAD_NOT_BOUNDED_TO_DASHBOARD")
    check_screen("real_home",["대시보드"],scroll=0)
    for title in ("보유종목","매수대기","체결이력","연구자료","대시보드"):
        tree=choose(title,tree)
        if not contains(tree,title):
            raise AssertionError("Navigation failed: "+title)
    # v057: briefly go Home and reopen. A live service must keep the native
    # dashboard visible without returning to the blocking first-run preloader.
    adb("shell","input","keyevent","KEYCODE_HOME")
    time.sleep(1.5)
    tree=start_app("warm_return")
    check_screen("warm_return_no_preload",["대시보드"],scroll=0)
    if contains(tree,"전략별 원장 데이터 확인"):
        raise AssertionError("WARM_RETURN_SHOULD_NOT_SHOW_BLOCKING_PRELOADER")
    print("WARM_RESUME_NO_PRELOAD_PASS",flush=True)
    # v076: actual Android 15 right drawer must show BOTH horizontal
    # export progress controls. No ZIP request or broker call in this smoke.
    tree=choose("설정 열기",tree)
    # Cold Android 15 service/activity refresh can invalidate a queued gear
    # tap. Verify the drawer actually opened; retry only the real accessible
    # Settings control (no false success from source-string assertions).
    opened=False
    for attempt in range(5):
        tree=dump("v080_settings_drawer_probe_"+str(attempt))
        if contains(tree,"주식 설정") and contains(tree,"운용 설정   ·"):
            opened=True
            break
        print("V080_DRAWER_GEAR_TAP_RETRY",attempt+1,
              str(describe(tree)[-16:]),flush=True)
        if contains(tree,"설정 열기"):
            tap(tree,"설정 열기")
        time.sleep(1.4)
    if not opened:
        raise AssertionError("REAL_SETTINGS_DRAWER_NOT_OPENING_AFTER_RETRIES")
    check_screen("v076_export_progress_drawer",
                 ["오류이력 ZIP 추출","연구이력 ZIP 추출"],scroll=4,drawer=True)
    check_screen("v088_scoped_error_buttons",
                 ["코인 오류 추출","주식 오류 추출","통합 오류 추출"],
                 scroll=4,drawer=True)
    if contains(dump("v088_error_screen_absent"),"오류 이력   ·"):
        raise AssertionError("V088_PRIVATE_ERRORS_MUST_NOT_HAVE_VISIBLE_ERROR_HISTORY_TAB")
    print("V088_THREE_SCOPED_ERROR_EXPORT_BUTTONS_AND_NO_ERROR_HISTORY_TAB_PASS",flush=True)
    # Touch the dark scrim to the left of the right-aligned drawer.
    # Its close icon may be offscreen after scrolling the long drawer.
    adb("shell","input","tap","14","430")
    time.sleep(.7)
    tree=dump("v076_export_drawer_closed")
    if contains(tree,"오류이력 ZIP 추출"):
        raise AssertionError("RIGHT_DRAWER_DID_NOT_CLOSE")
    # v053: the actual service-enabled app must initialize the new research
    # journal. Dashboard-only checks can pass even when Python failed to start.
    tracker="files/ARTITRADE_PHONE_MAIN_V070/POST_EXIT_HIGH_V053.sqlite3"
    found=False
    for attempt in range(20):
        listing=adb("shell","run-as",PACKAGE,"ls","-l",tracker,
                    check=False,timeout=15)
        if "POST_EXIT_HIGH_V053.sqlite3" in listing:
            print("POST_SALE_V053_JOURNAL_INITIALIZED",listing.strip(),flush=True)
            found=True;break
        time.sleep(2)
    if not found:
        raise AssertionError("V053_RUNTIME_JOURNAL_NOT_INITIALIZED_AFTER_REAL_BOOT")
    wallet="files/ARTITRADE_PHONE_MAIN_V070/coin/trend/market_trend_wallet_v054.sqlite3"
    independent=adb("shell","run-as",PACKAGE,"ls","-l",wallet,check=False,timeout=15)
    if "market_trend_wallet_v054.sqlite3" not in independent:
        raise AssertionError("MT54_INDEPENDENT_WALLET_NOT_INITIALIZED_AFTER_REAL_BOOT")
    # This is the actual service-enabled APK, with both strategy engines
    # started. Verify the new atomic display snapshot was published without
    # blocking foreground order workers or restarting the Android process.
    view_file="files/artitrade_view_record_snapshot_v087.json"
    view=adb("shell","run-as",PACKAGE,"ls","-l",view_file,check=False,timeout=15)
    if "artitrade_view_record_snapshot_v087.json" not in view:
        raise AssertionError("V087_SERVICE_ATOMIC_UI_RECORD_NOT_PUBLISHED")
    health=adb("shell","run-as",PACKAGE,"cat",
        "shared_prefs/artitrade_process_health_v087.xml",check=False,timeout=15)
    if "last_memory_sample" not in health:
        raise AssertionError("V087_ANDROID_MEMORY_SAMPLE_NOT_PERSISTED")
    memory=adb("shell","dumpsys","meminfo",PACKAGE,timeout=45,check=False)
    (BASE/"v087_service_memory_pss.txt").write_text(memory,encoding="utf-8")
    if "TOTAL" not in memory:
        raise AssertionError("V087_SERVICE_PROCESS_MEMORY_NOT_REPORTED")
    print("V087_REAL_PROCESS_HEALTH_AND_ATOMIC_VIEW_STORE_PASS",flush=True)
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
    # This is an isolated emulator fixture. Ensure seeded historical records
    # are selected instead of a real-boot private atomic file from the prior APK.
    adb("shell","run-as",PACKAGE,"rm","-f",
        "files/artitrade_view_record_snapshot_v087.json",check=False)
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
        ("주식","마켓트렌드","TRENDSTOCK_삼성전자"),
        ("코인","기준봉","COIN_비트코인"),
        ("코인","MA","COINMA_이더리움"),
        ("코인","마켓트렌드","TRENDCOIN_비트코인")
    ]
    for market,strategy,label in configurations:
        tree=dump("before_"+label)
        if not contains(tree,market):
            tree=choose(market,tree)
        elif market=="코인" and not contains(tree,"KRW TOP35"):
            tree=choose("코인",tree)
        elif market=="주식" and contains(tree,"업비트"):
            tree=choose("주식",tree)
        # Android 15's system status bar previously intercepted coin-tab taps:
        # the test was still showing the AT stock account when reading coin rows.
        if market=="코인" and not contains(tree,"업비트"):
            raise AssertionError("COIN_SWITCH_NOT_APPLIED: Android status bar overlaps coin market tab")
        if market=="주식" and contains(tree,"업비트"):
            raise AssertionError("STOCK_SWITCH_NOT_APPLIED: Android status bar overlaps market tab")
        tree=choose(strategy,tree)
        tree=choose("보유종목",tree)
        check_screen(label+"_positions",[label+"_보유"])
        tree=choose("매수대기")
        check_screen(label+"_pending",[label+"_대기"])
        tree=choose("체결이력")
        check_screen(label+"_executions",[label+"_매도","손절 기준 도달","09:30:00 KST"])
        tree=choose("연구자료")
        if strategy=="마켓트렌드":
            check_screen(label+"_research",["리서치 · 2"],scroll=0)
        else:
            check_screen(label+"_research",["리서치 · 1"],scroll=0)
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
            "ok":True,"real_boot":True,"v053_runtime_journal_created":True,"all_eight_strategy_fixture_views":True,
            "v076_export_progress_drawer":True,
            "navigated_pages":["home","positions","pending","executions","research"],
            "no_real_broker_api":True,"source":"synthetic fixtures only"},
            ensure_ascii=False,indent=2))
        print("ANDROID_EMULATOR_REAL_BOOT_AND_ALL_8_STRATEGY_UI_PASS",flush=True)
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