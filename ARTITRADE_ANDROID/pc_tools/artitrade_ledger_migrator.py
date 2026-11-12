#!/usr/bin/env python3
# ARTITRADE one-time Windows EXE ledger -> S21+ MSERVER migration.
# Standard library only. SQLite files are copied with sqlite3.backup for consistency.
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

SCHEMA = "ARTITRADE_PC_LEDGER_MIGRATION_V1"
UPLOAD_CHUNK = 1_000_000
# Limit is shared with MSERVER and the phone importer. All I/O stays chunked.
MAX_ARCHIVE = 4 * 1024 * 1024 * 1024
MAX_UPLOAD_CHUNKS = 5000


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validated_url(raw: str) -> str:
    parsed = urllib.parse.urlsplit((raw or "").strip().rstrip("/"))
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("MSERVER 주소는 http://100.x.y.z:8765 형식이어야 합니다.")
    try:
        addr = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("숫자형 Tailscale IP(100.64.0.0/10)를 입력하십시오.") from exc
    if not (str(addr) == "127.0.0.1" or addr in ipaddress.ip_network("100.64.0.0/10")):
        raise ValueError("공개/Wi-Fi IP가 아닌 Tailscale IP만 허용됩니다.")
    if parsed.port != 8765 or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("주소는 http://<Tailscale-IP>:8765 까지만 입력하십시오.")
    return f"http://{parsed.hostname}:8765"


def request(base: str, token: str, method: str, endpoint: str, payload=None, params=None, timeout=20):
    base = validated_url(base)
    path = endpoint
    if params:
        path += "?" + urllib.parse.urlencode(params)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json", "Cache-Control": "no-store"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    # Tailscale/ADB endpoints must be reached directly, not via Windows/enterprise HTTP proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"ok": False, "error": "HTTP_" + str(exc.code)}
        body["http_status"] = exc.code
        return body


# A phone's 127.0.0.1 is never a Windows PC's 127.0.0.1. The only accepted
# localhost path is a verified Android Debug Bridge (ADB) USB port forward.
TAILNET = ipaddress.ip_network("100.64.0.0/10")
MSERVER_HEALTH = "ARTITRADE MSERVER"


def _executable(name: str, relative_paths: tuple[str, ...]) -> str:
    candidates = [shutil.which(name)]
    for candidate in relative_paths:
        candidates.append(os.path.expandvars(candidate))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return ""


def _command(program: str, *args: str, timeout: int = 6) -> tuple[int, str]:
    if not program:
        return -1, ""
    try:
        run = subprocess.run(
            [program, *args],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        return run.returncode, run.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def _tailscale_program() -> str:
    return _executable("tailscale", (
        r"%ProgramFiles%\Tailscale\tailscale.exe",
        r"%ProgramFiles(x86)%\Tailscale\tailscale.exe",
        r"%LOCALAPPDATA%\Tailscale\tailscale.exe",
    ))


def _adb_program() -> str:
    return _executable("adb", (
        r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe",
        r"%ANDROID_HOME%\platform-tools\adb.exe",
        r"%ANDROID_SDK_ROOT%\platform-tools\adb.exe",
        r"%ProgramFiles%\Android\platform-tools\adb.exe",
    ))


def tailscale_candidates() -> list[tuple[str, str]]:
    """Return Android peers only; do not scan unrelated tailnet devices."""
    program = _tailscale_program()
    code, output = _command(program, "status", "--json")
    if code != 0 or not output:
        return []
    try:
        state = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return []
    if state.get("BackendState", "Running") != "Running":
        return []
    peers = state.get("Peer") or {}
    if isinstance(peers, dict):
        peers = list(peers.values())
    if not isinstance(peers, list):
        return []
    result = []
    for peer in peers:
        if not isinstance(peer, dict) or peer.get("Online", True) is False:
            continue
        system = str(peer.get("OS") or "").lower()
        nickname = str(peer.get("HostName") or peer.get("DNSName") or "Android 기기")
        if system != "android":
            continue
        for ip in peer.get("TailscaleIPs") or []:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if addr in TAILNET:
                result.append((f"http://{ip}:8765", nickname))
    # A limit prevents a large tailnet from causing excessive network checks.
    return list(dict.fromkeys(result))[:20]


def _adb_forward_status(adb: str, serial: str) -> bool:
    code, out = _command(adb, "-s", serial, "forward", "--list", timeout=6)
    if code != 0:
        return False
    for line in out.splitlines():
        cols = line.strip().split()
        if len(cols) == 3 and cols[0] == serial and cols[1] == "tcp:8765" and cols[2] == "tcp:8765":
            return True
    return False


def usb_forward_available() -> bool:
    """Bind PC 127.0.0.1 only to one authorized phone, using adb forward."""
    adb = _adb_program()
    code, devices = _command(adb, "devices", timeout=8)
    if code != 0:
        return False
    serials = []
    for line in devices.splitlines():
        columns = line.strip().split()
        if len(columns) >= 2 and columns[1] == "device" and not line.startswith("List of"):
            serials.append(columns[0])
    if len(serials) != 1:
        return False
    serial = serials[0]
    if _adb_forward_status(adb, serial):
        return True
    code, _ = _command(adb, "-s", serial, "forward", "--no-rebind", "tcp:8765", "tcp:8765", timeout=8)
    return code == 0 and _adb_forward_status(adb, serial)


def probe_mserver(base: str) -> bool:
    """Verify product identity before sending a bearer token or database."""
    try:
        health = request(base, "", "GET", "/health", timeout=2.4)
        return (isinstance(health, dict) and health.get("ok") is True
                and health.get("name") == MSERVER_HEALTH and health.get("api") == 1)
    except (OSError, ValueError, TimeoutError, urllib.error.URLError):
        return False


def resolve_server(raw: str, progress=lambda message: None) -> str:
    """Auto-discover MSERVER over tailnet, then an authorized USB/ADB tunnel."""
    manual = (raw or "").strip()
    explicit = ""
    if manual and manual != "자동 검색":
        try:
            explicit = validated_url(manual)
        except (ValueError, TypeError):
            progress("직접 입력한 주소를 확인할 수 없어 연결된 기기를 자동 검색합니다.")
    if explicit and explicit != "http://127.0.0.1:8765":
        if probe_mserver(explicit):
            progress("MSERVER 연결 확인: " + explicit)
            return explicit
        progress("입력한 주소에 MSERVER가 응답하지 않아 자동 검색으로 전환합니다.")

    progress("Tailscale에서 연결된 안드로이드 기기를 찾는 중")
    candidates = tailscale_candidates()
    hits = []
    for base, device in candidates:
        if probe_mserver(base):
            hits.append((base, device))
    if len(hits) == 1:
        base, device = hits[0]
        progress("MSERVER 자동 발견: " + device + " (" + base + ")")
        return base
    if len(hits) > 1:
        raise ConnectionError(
            "MSERVER가 설치된 Android 기기가 여러 대 감지되었습니다. "
            "주소를 직접 선택하십시오: " + ", ".join(base for base, _ in hits)
        )

    progress("Tailscale 연결 없음 · USB 디버깅 연결을 확인하는 중")
    if usb_forward_available() and probe_mserver("http://127.0.0.1:8765"):
        progress("USB로 연결된 S21+ MSERVER를 발견했습니다. PC 내부 터널 사용 중")
        return "http://127.0.0.1:8765"

    raise ConnectionError(
        "휴대폰 MSERVER를 찾을 수 없습니다.\n"
        "휴대폰에서 Tailscale 연결 및 MSERVER의 'Tailscale 접속 켜기'를 확인하십시오.\n"
        "USB로 연결했다면 Android USB 디버깅 허용 및 PC ADB 설치가 필요합니다.\n"
        "중요: 휴대폰의 127.0.0.1은 PC에서 직접 접속할 수 없습니다."
    )


def verified_connection(raw: str, token: str, progress=lambda message: None) -> str:
    if len((token or "").strip()) != 64:
        raise ValueError("MSERVER 연결 토큰 64자리를 먼저 입력하십시오.")
    base = resolve_server(raw, progress)
    answer = request(base, token.strip(), "GET", "/v1/status", timeout=5)
    if not isinstance(answer, dict):
        raise ConnectionError("MSERVER 응답이 JSON 객체가 아니어서 전송을 차단했습니다.")
    if not answer.get("ok"):
        if answer.get("http_status") == 401 or answer.get("error") == "unauthorized":
            raise PermissionError("연결 성공, 인증 실패: 휴대폰 MSERVER의 최신 연결 토큰을 확인하십시오.")
        raise ConnectionError("MSERVER 인증 오류: " + str(answer.get("error", "unknown")))
    if answer.get("owner") != "phone":
        raise ConnectionError("S21+ MSERVER 이외의 장치가 응답하여 전송을 차단했습니다.")
    progress("MSERVER 연결과 인증 정상: " + base)
    return base


def stock_root() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return local / "ARTITRADE_V12_D_DIVERSE_1_1"


def coin_root() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return local / "AT_COIN_B_V1_0"


def source_map() -> dict[str, tuple[Path, bool]]:
    sr = stock_root()
    cr = coin_root()
    return {
        "stock/ARTITRADE_D.sqlite3": (sr / "ARTITRADE_D.sqlite3", True),
        "stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3": (sr / "STOCK_MA_ENGINE_V1" / "ma_paper.sqlite3", True),
        "stock/AT_CLOSE/AT_CLOSE_PAPER.sqlite3": (sr / "AT_CLOSE_PAPER.sqlite3", True),
        "stock/PROJECTGMO_V1.0_research.sqlite3": (sr / "PROJECTGMO_V1.0_research.sqlite3", True),
        "stock/PROJECTGMO_V1.0_account_state.json": (sr / "PROJECTGMO_V1.0_account_state.json", False),
        "coin/base/upbit_paper.sqlite3": (cr / "upbit_paper.sqlite3", True),
        "coin/ma/upbit_paper.sqlite3": (cr / "MA_ENGINE_V1" / "upbit_paper.sqlite3", True),
    }


def sqlite_snapshot(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    src = sqlite3.connect(str(source), timeout=30)
    dst = sqlite3.connect(str(target), timeout=30)
    try:
        src.execute("PRAGMA busy_timeout=30000")
        src.backup(dst, pages=256, sleep=0.03)
        dst.commit()
    finally:
        dst.close()
        src.close()


def sqlite_counts(path: Path) -> dict:
    out = {}
    try:
        con = sqlite3.connect("file:" + path.resolve().as_posix() + "?mode=ro", uri=True, timeout=5)
        try:
            con.execute("PRAGMA query_only=ON")
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                if not table.replace("_", "").isalnum():
                    continue
                try:
                    out[table] = int(con.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0])
                except Exception:
                    pass
        finally:
            con.close()
    except Exception as exc:
        out["_error"] = type(exc).__name__ + ": " + str(exc)[:180]
    return out


def gmo_state_counts(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pos = data.get("positions") or {}
        fills = data.get("fills") or []
        orders = data.get("orders") or {}
        return {
            "positions": len(pos) if isinstance(pos, dict) else len(pos or []),
            "fills": len(fills or []),
            "orders": len(orders) if isinstance(orders, dict) else len(orders or []),
        }
    except Exception as exc:
        return {"_error": type(exc).__name__ + ": " + str(exc)[:180]}


def build_archive(progress=lambda msg: None) -> tuple[Path, dict]:
    sources = source_map()
    required = [
        "stock/ARTITRADE_D.sqlite3",
        "stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
        "coin/base/upbit_paper.sqlite3",
        "coin/ma/upbit_paper.sqlite3",
    ]
    missing_required = [arc for arc in required if not sources[arc][0].is_file()]
    if missing_required:
        raise FileNotFoundError("필수 원장을 찾지 못함: " + ", ".join(missing_required))

    work = Path(tempfile.mkdtemp(prefix="artitrade_pc_ledger_"))
    try:
        stage = work / "stage"
        stage.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema": SCHEMA,
            "created_at_epoch_ms": int(time.time() * 1000),
            "stock_root": str(stock_root()),
            "coin_root": str(coin_root()),
            "files": {},
        }
    
        for arc, (source, is_db) in sources.items():
            if not source.is_file():
                progress("선택 원장 없음: " + str(source))
                continue
            target = stage / arc
            target.parent.mkdir(parents=True, exist_ok=True)
            progress("스냅샷: " + arc)
            if is_db:
                sqlite_snapshot(source, target)
                counts = sqlite_counts(target)
            else:
                target.write_bytes(source.read_bytes())
                counts = gmo_state_counts(target)
            manifest["files"][arc] = {
                "source": str(source),
                "size": target.stat().st_size,
                "sha256": sha256_file(target),
                "counts": counts,
            }
    
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        archive = work / "ARTITRADE_PC_LEDGER_MIGRATION.zip"
        progress("승계 ZIP 생성 중")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(stage).as_posix())
        archive_size = archive.stat().st_size
        if archive_size > MAX_ARCHIVE:
            raise ValueError("승계 ZIP이 4GB 안전 한도를 초과함. 대용량 파일 분할이 추가로 필요합니다.")
        progress(f"승계 ZIP 생성 완료 · {archive_size / (1024*1024):,.1f}MB")
        manifest["archive_size"] = archive.stat().st_size
        manifest["archive_sha256"] = sha256_file(archive)
        return archive, manifest
    except BaseException:
        # Never leave >250MB copies behind after an archive/build error.
        shutil.rmtree(work, ignore_errors=True)
        raise


def upload_archive(base: str, token: str, archive: Path, manifest: dict, progress=lambda msg: None) -> dict:
    raw_size = archive.stat().st_size
    if raw_size <= 0 or raw_size > MAX_ARCHIVE:
        raise ValueError("승계 ZIP 크기가 안전한 범위를 벗어났습니다.")
    total_chunks = (raw_size + UPLOAD_CHUNK - 1) // UPLOAD_CHUNK
    if total_chunks > MAX_UPLOAD_CHUNKS:
        raise ValueError("서버가 처리할 수 있는 전송 조각 수를 초과했습니다.")
    session = "pcledger_" + uuid.uuid4().hex
    begin = request(base, token, "POST", "/v1/migration/begin", {
        "session": session,
        "total_chunks": total_chunks,
        "total_bytes": raw_size,
        "archive_sha256": manifest["archive_sha256"],
        "source": {
            "schema": SCHEMA,
            "stock_root": manifest.get("stock_root", ""),
            "coin_root": manifest.get("coin_root", ""),
            "file_count": len(manifest.get("files", {})),
            "chunk_bytes": UPLOAD_CHUNK,
            "uncompressed_bytes": sum(x.get("size", 0) for x in manifest.get("files", {}).values()),
        },
    })
    if not begin.get("ok"):
        code = begin.get("error", "unknown")
        if code == "not_enough_phone_storage":
            free = begin.get("available_bytes", 0) / (1024**3)
            required = begin.get("required_bytes", 0) / (1024**3)
            raise RuntimeError(f"S21+ 저장공간 부족: 필요 {required:.2f}GB / 잔여 {free:.2f}GB")
        if code == "previous_migration_pending":
            raise RuntimeError("이전 원장이 S21+에 전송 완료 상태로 남아 있습니다. "
                               "휴대폰 ARTITRADE를 재실행해 승계한 후 다시 시도하십시오.")
        if code == "invalid_migration_manifest":
            raise RuntimeError("MSERVER 수신 버전이 오래되었거나 용량이 허용 한도를 초과했습니다. "
                               "신규 MSERVER APK를 덮어쓰기 설치하십시오. 상세: " + json.dumps(begin, ensure_ascii=False))
        raise RuntimeError("MSERVER 승계 준비 실패: " + json.dumps(begin, ensure_ascii=False))

    with archive.open("rb") as f:
        for index in range(total_chunks):
            data = f.read(UPLOAD_CHUNK)
            payload = {
                "session": session, "index": index,
                "data_b64": base64.b64encode(data).decode("ascii"),
                "sha256": sha256_bytes(data),
            }
            # The server accepts an identical chunk again if the ACK was lost.
            for attempt in range(4):
                try:
                    result = request(base, token, "POST", "/v1/migration/chunk", payload, timeout=45)
                    if result.get("ok"):
                        break
                    if result.get("error") not in ("server_storage_failed",):
                        raise RuntimeError("MSERVER 전송 거절: " + json.dumps(result, ensure_ascii=False))
                    if attempt == 3:
                        raise RuntimeError("MSERVER 저장 오류: " + json.dumps(result, ensure_ascii=False))
                except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                    if attempt == 3:
                        raise ConnectionError(f"MSERVER {index+1}/{total_chunks} 전송 실패: {exc}") from exc
                progress(f"전송 재시도: {index+1}/{total_chunks} · {attempt+2}회")
                time.sleep(min(2.0, 0.4*(attempt+1)))
            if index == 0 or (index+1) % 10 == 0 or index + 1 == total_chunks:
                progress(f"MSERVER 전송 {index+1}/{total_chunks}")

    progress("승계 ZIP 최종 무결성 검사 중")
    commit = request(base, token, "POST", "/v1/migration/commit", {"session": session}, timeout=180)
    if not commit.get("ok"):
        raise RuntimeError("MSERVER commit 실패: " + json.dumps(commit, ensure_ascii=False))
    commit["session"] = session
    return commit


def summary_text(manifest: dict) -> str:
    lines = []
    for arc, meta in manifest.get("files", {}).items():
        counts = meta.get("counts", {})
        important = []
        for key in ("pending", "candidates", "orders", "positions", "executions", "signals", "research",
                    "research_candidates", "gmo_candidates", "fills"):
            if key in counts:
                important.append(f"{key}={counts[key]}")
        lines.append(f"{arc}: " + (", ".join(important) if important else f"{meta.get('size',0):,} bytes"))
    return "\n".join(lines)


def migrate(base: str, token: str, progress=lambda msg: None) -> tuple[dict, dict]:
    token = (token or "").strip()
    if len(token) != 64:
        raise ValueError("MSERVER 연결 토큰은 64자리여야 합니다.")
    base = verified_connection(base, token, progress)
    archive, manifest = build_archive(progress)
    try:
        progress(summary_text(manifest))
        result = upload_archive(base, token, archive, manifest, progress)
        progress("전송 완료. S21+ ARTITRADE를 다시 열면 기존 EXE 원장을 자동 승계합니다.")
        return manifest, result
    finally:
        try:
            import shutil
            shutil.rmtree(archive.parent, ignore_errors=True)
        except Exception:
            pass


def run_gui():
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("ARTITRADE 기존 EXE 원장 → S21+ 승계 FIX3 (대용량)")
    root.geometry("760x680")
    root.minsize(700, 540)

    frm = ttk.Frame(root, padding=18)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="ARTITRADE 기존 EXE 원장 승계 V2", font=("Malgun Gothic", 17, "bold")).pack(anchor="w")
    ttk.Label(frm, text="최신 주식/코인 EXE의 SQLite · GMO 상태를 읽기 전용 스냅샷으로 만들어 MSERVER에 전송합니다.").pack(anchor="w", pady=(4, 14))

    roots = ttk.LabelFrame(frm, text="자동 탐색 결과", padding=10)
    roots.pack(fill="x")
    source_var = tk.StringVar()
    sources = source_map()
    found = [f"{arc}\n  {path}" for arc, (path, _) in sources.items() if path.is_file()]
    missing = [f"{arc}\n  없음: {path}" for arc, (path, _) in sources.items() if not path.is_file()]
    source_var.set("\n".join(found + missing))
    # Keep the source list scrollable so it cannot hide the action buttons.
    source_frame = ttk.Frame(roots)
    source_frame.pack(fill="x")
    source_list = tk.Text(source_frame, height=7, wrap="none", font=("Consolas", 9))
    source_scroll = ttk.Scrollbar(source_frame, orient="vertical", command=source_list.yview)
    source_list.configure(yscrollcommand=source_scroll.set)
    source_list.pack(side="left", fill="x", expand=True)
    source_scroll.pack(side="right", fill="y")
    source_list.insert("1.0", source_var.get())
    source_list.configure(state="disabled")

    conn = ttk.LabelFrame(frm, text="S21+ MSERVER", padding=10)
    conn.pack(fill="x", pady=(12, 0))
    row = ttk.Frame(conn); row.pack(fill="x")
    ttk.Label(row, text="휴대폰 주소", width=16).pack(side="left")
    url_var = tk.StringVar(value=os.getenv("ARTITRADE_HUB_URL", "자동 검색"))
    ttk.Entry(row, textvariable=url_var).pack(side="left", fill="x", expand=True)
    auto_btn = ttk.Button(row, text="자동 찾기", command=lambda: auto_scan())
    auto_btn.pack(side="left", padx=(6, 0))
    row2 = ttk.Frame(conn); row2.pack(fill="x", pady=(8, 0))
    ttk.Label(row2, text="연결 토큰", width=16).pack(side="left")
    token_var = tk.StringVar(value=os.getenv("ARTITRADE_HUB_TOKEN", ""))
    ttk.Entry(row2, textvariable=token_var, show="●").pack(side="left", fill="x", expand=True)
    connection_hint = tk.StringVar(value="휴대폰 주소 입력 없이 자동 찾기 가능 · Tailscale 또는 USB/ADB")
    ttk.Label(conn, textvariable=connection_hint, wraplength=670).pack(anchor="w", pady=(7, 0))

    progress_box = ttk.Frame(frm)
    progress_box.pack(fill="x", pady=(12, 2))
    progress_text = tk.StringVar(value="승계 준비 0%")
    ttk.Label(progress_box, textvariable=progress_text).pack(anchor="w")
    progress_value = tk.IntVar(value=0)
    progress_bar = ttk.Progressbar(progress_box, orient="horizontal", mode="determinate", maximum=100, variable=progress_value)
    progress_bar.pack(fill="x", pady=(4, 0))

    # Buttons are deliberately ABOVE the growing log and remain visible in 540px windows.
    actions = ttk.Frame(frm)
    actions.pack(fill="x", pady=(9, 0))
    test_btn = ttk.Button(actions, text="MSERVER 연결 확인", command=lambda: check_connection())
    test_btn.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=6)
    btn = ttk.Button(actions, text="기존 EXE 원장을 S21+로 승계 시작", command=lambda: start())
    btn.pack(side="left", fill="x", expand=True, ipady=6)

    status = tk.Text(frm, height=9, wrap="word", font=("Consolas", 10))
    status.pack(fill="both", expand=True, pady=(12, 10))
    status.insert("end", "준비됨\n")
    status.configure(state="disabled")

    def log(msg):
        text = str(msg)
        def put():
            m = re.search(r"MSERVER 전송\s+(\d+)/(\d+)", text)
            if m:
                done,total = int(m.group(1)), max(1,int(m.group(2)))
                pct = max(0,min(100,int(done*100/total)))
                progress_value.set(pct); progress_text.set(f"MSERVER 전송 {pct}% · {done}/{total}")
            elif text.startswith("스냅샷:"):
                progress_value.set(max(progress_value.get(),5)); progress_text.set("최신 EXE 원장 스냅샷 생성 중")
            elif "승계 ZIP 생성" in text:
                progress_value.set(max(progress_value.get(),15)); progress_text.set("승계 ZIP 생성 중")
            elif "전송 완료" in text:
                progress_value.set(100); progress_text.set("MSERVER 전송 완료 · S21+ 자동승계 대기")
            status.configure(state="normal")
            status.insert("end", text + "\n")
            status.see("end")
            status.configure(state="disabled")
        root.after(0, put)

    def auto_scan():
        raw = url_var.get()
        auto_btn.configure(state="disabled")
        connection_hint.set("연결된 S21+ MSERVER 자동 검색 중…")
        def worker():
            try:
                base = resolve_server(raw, log)
                root.after(0, lambda: url_var.set(base))
                root.after(0, lambda: connection_hint.set("자동 연결됨: " + base + " · 토큰 입력 후 연결 확인"))
            except Exception as exc:
                log("기기 자동 검색: " + str(exc))
                root.after(0, lambda: connection_hint.set("기기 검색 실패 · Tailscale 또는 USB 연결 확인"))
            finally:
                root.after(0, lambda: auto_btn.configure(state="normal"))
        threading.Thread(target=worker, daemon=True).start()

    def check_connection():
        requested = url_var.get()
        token = token_var.get().strip()
        test_btn.configure(state="disabled")
        def worker():
            try:
                base = verified_connection(requested, token, log)
                root.after(0, lambda: url_var.set(base))
                root.after(0, lambda: connection_hint.set("인증 성공: " + base))
                root.after(0, lambda: messagebox.showinfo("연결 확인", "휴대폰 MSERVER 연결과 인증이 정상입니다.\n원장 승계를 시작할 수 있습니다."))
            except Exception as exc:
                log("MSERVER 연결 확인 실패: " + str(exc))
                root.after(0, lambda msg=str(exc): messagebox.showerror("연결 확인 실패", msg))
            finally:
                root.after(0, lambda: test_btn.configure(state="normal"))
        threading.Thread(target=worker, daemon=True).start()

    def start():
        requested = url_var.get()
        token = token_var.get().strip()
        btn.configure(state="disabled")
        def work():
            try:
                # Do not snapshot or transmit account data before a successful
                # MSERVER identity AND token check.
                base = verified_connection(requested, token, log)
                root.after(0, lambda: url_var.set(base))
                manifest, result = migrate(base, token, log)
                log("완료 SHA256: " + manifest["archive_sha256"])
                root.after(0, lambda: messagebox.showinfo(
                    "승계 전송 완료",
                    "기존 EXE 원장을 MSERVER에 안전하게 전송했습니다.\n\n"
                    "이제 S21+ ARTITRADE 앱을 완전히 종료 후 다시 실행하십시오.\n"
                    "앱 시작 전에 기존 체결·보유·대기·연구 DB가 자동 승계됩니다."
                ))
            except Exception as exc:
                log("승계 실패: " + str(exc))
                root.after(0, lambda msg=str(exc): messagebox.showerror("승계 실패", msg))
            finally:
                root.after(0, lambda: btn.configure(state="normal"))
        threading.Thread(target=work, daemon=True).start()

    # Probe the tailnet on launch. Never sends the token during discovery.
    root.after(700, auto_scan)

    ttk.Label(frm, text="원본 EXE 데이터는 읽기 전용 · 외부 접속은 Tailscale 전용 · USB는 ADB 로컬 터널만 사용").pack(anchor="w", pady=(8, 0))
    root.mainloop()


def cli():
    ap = argparse.ArgumentParser(description="ARTITRADE PC ledger -> S21+ MSERVER migration")
    ap.add_argument("--url", default=os.getenv("ARTITRADE_HUB_URL", "자동 검색"))
    ap.add_argument("--token", default=os.getenv("ARTITRADE_HUB_TOKEN", ""))
    ap.add_argument("--no-gui", action="store_true")
    args = ap.parse_args()
    if not args.no_gui:
        run_gui()
        return 0
    if not args.token:
        ap.error("--token (or ARTITRADE_HUB_TOKEN) is required with --no-gui")
    manifest, result = migrate(args.url, args.token, print)
    print(json.dumps({"manifest": manifest, "mserver": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(cli())
