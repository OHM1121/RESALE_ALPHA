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
import sqlite3
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
UPLOAD_CHUNK = 300_000
MAX_ARCHIVE = 250 * 1024 * 1024


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
    if not (addr.is_loopback or addr in ipaddress.ip_network("100.64.0.0/10")):
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            body = {"ok": False, "error": "HTTP_" + str(exc.code)}
        body["http_status"] = exc.code
        return body


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
    if archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError(f"승계 ZIP이 {MAX_ARCHIVE // (1024*1024)}MB 제한을 초과함")
    manifest["archive_size"] = archive.stat().st_size
    manifest["archive_sha256"] = sha256_file(archive)
    return archive, manifest


def upload_archive(base: str, token: str, archive: Path, manifest: dict, progress=lambda msg: None) -> dict:
    raw_size = archive.stat().st_size
    total_chunks = (raw_size + UPLOAD_CHUNK - 1) // UPLOAD_CHUNK
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
        },
    })
    if not begin.get("ok"):
        raise RuntimeError("MSERVER begin 실패: " + json.dumps(begin, ensure_ascii=False))

    with archive.open("rb") as f:
        for index in range(total_chunks):
            data = f.read(UPLOAD_CHUNK)
            result = request(base, token, "POST", "/v1/migration/chunk", {
                "session": session,
                "index": index,
                "data_b64": base64.b64encode(data).decode("ascii"),
                "sha256": sha256_bytes(data),
            }, timeout=30)
            if not result.get("ok"):
                raise RuntimeError(f"MSERVER chunk {index} 실패: " + json.dumps(result, ensure_ascii=False))
            progress(f"폰 전송 {index+1}/{total_chunks}")

    commit = request(base, token, "POST", "/v1/migration/commit", {"session": session}, timeout=30)
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
    root.title("ARTITRADE 기존 EXE 원장 → S21+ 승계 V2")
    root.geometry("760x620")
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
    ttk.Label(roots, textvariable=source_var, justify="left").pack(anchor="w")

    conn = ttk.LabelFrame(frm, text="S21+ MSERVER", padding=10)
    conn.pack(fill="x", pady=(12, 0))
    row = ttk.Frame(conn); row.pack(fill="x")
    ttk.Label(row, text="Tailscale 주소", width=16).pack(side="left")
    url_var = tk.StringVar(value=os.getenv("ARTITRADE_HUB_URL", "http://100.x.x.x:8765"))
    ttk.Entry(row, textvariable=url_var).pack(side="left", fill="x", expand=True)
    row2 = ttk.Frame(conn); row2.pack(fill="x", pady=(8, 0))
    ttk.Label(row2, text="연결 토큰", width=16).pack(side="left")
    token_var = tk.StringVar(value=os.getenv("ARTITRADE_HUB_TOKEN", ""))
    ttk.Entry(row2, textvariable=token_var, show="●").pack(side="left", fill="x", expand=True)

    status = tk.Text(frm, height=12, wrap="word", font=("Consolas", 10))
    status.pack(fill="both", expand=True, pady=(12, 10))
    status.insert("end", "준비됨\n")
    status.configure(state="disabled")

    def log(msg):
        def put():
            status.configure(state="normal")
            status.insert("end", str(msg) + "\n")
            status.see("end")
            status.configure(state="disabled")
        root.after(0, put)

    def start():
        btn.configure(state="disabled")
        def work():
            try:
                manifest, result = migrate(url_var.get(), token_var.get(), log)
                log("완료 SHA256: " + manifest["archive_sha256"])
                root.after(0, lambda: messagebox.showinfo(
                    "승계 전송 완료",
                    "기존 EXE 원장을 MSERVER에 안전하게 전송했습니다.\n\n"
                    "이제 S21+ ARTITRADE 앱을 완전히 종료 후 다시 실행하십시오.\n"
                    "앱 시작 전에 기존 체결·보유·대기·연구 DB가 자동 승계됩니다."
                ))
            except Exception as exc:
                log(type(exc).__name__ + ": " + str(exc))
                root.after(0, lambda: messagebox.showerror("승계 실패", str(exc)))
            finally:
                root.after(0, lambda: btn.configure(state="normal"))
        threading.Thread(target=work, daemon=True).start()

    btn = ttk.Button(frm, text="기존 EXE 원장을 S21+로 승계", command=start)
    btn.pack(fill="x", ipady=7)
    ttk.Label(frm, text="원본 EXE 원장은 읽기만 하며 수정하지 않습니다. MSERVER는 Tailscale 사설망에서만 전송을 허용합니다.").pack(anchor="w", pady=(8, 0))
    root.mainloop()


def cli():
    ap = argparse.ArgumentParser(description="ARTITRADE PC ledger -> S21+ MSERVER migration")
    ap.add_argument("--url", default=os.getenv("ARTITRADE_HUB_URL", ""))
    ap.add_argument("--token", default=os.getenv("ARTITRADE_HUB_TOKEN", ""))
    ap.add_argument("--no-gui", action="store_true")
    args = ap.parse_args()
    if not args.no_gui:
        run_gui()
        return 0
    if not args.url or not args.token:
        ap.error("--url and --token (or env vars) are required with --no-gui")
    manifest, result = migrate(args.url, args.token, print)
    print(json.dumps({"manifest": manifest, "mserver": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(cli())
