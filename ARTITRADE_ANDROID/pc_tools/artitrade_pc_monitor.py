#!/usr/bin/env python3
"""ARTITRADE PC: read-only MSERVER mobile monitor (stock & coin).

This app intentionally has NO buy/sell engine, broker auth, paper trading,
SQLite connection, MSERVER writes, or remote control endpoints.
Close the legacy trading EXE separately; this app does not control it.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

from artitrade_pc_monitor_core import (
    LABELS, MARKETS, ReadOnlyHubClient, BAND_NAMES, FRAMES,
    bool_value, event_time, flatten_items, normalize_frame, outcome_of,
    research_rows, score_band, summarize_research, time_from_row,
    validate_hub_url, automatic_preload,
)

TITLE = "ARTITRADE PC | 모바일 원장 모니터 (거래 기능 없음)"
BG, PANEL, PANEL2 = "#0b1220", "#131f30", "#1b2b41"
FG, MUTED, BLUE, GREEN, AMBER, RED = "#e9f0fa", "#a5b9d1", "#70a6ff", "#57ddb0", "#f5bb64", "#fb788b"
CFG = Path(os.environ.get("APPDATA") or Path.home()) / "ARTITRADE_PC_VIEWER" / "settings.json"
REFRESH_SECONDS = 10
PAGE_SIZE = 250

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI; config must not contain a plaintext bearer token."""
    if os.name != "nt":
        raise RuntimeError("Credential persistence is Windows-only")
    source = ctypes.create_string_buffer(data)
    input_blob = DATA_BLOB(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = DATA_BLOB()
    crypt = ctypes.windll.crypt32
    func = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    if not func(ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)

def load_settings() -> dict:
    try:
        settings = json.loads(CFG.read_text(encoding="utf-8"))
        raw = settings.get("dpapi_token")
        if raw:
            try:
                settings["token"] = _dpapi(base64.b64decode(raw), False).decode("ascii")
            except Exception:
                settings["token"] = ""
        return settings
    except (OSError, ValueError):
        return {}

def save_settings(url: str, token: str, remember: bool):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    data = {"url": url, "last_opened": int(time.time())}
    if remember and token:
        try:
            data["dpapi_token"] = base64.b64encode(_dpapi(token.encode("ascii"), True)).decode("ascii")
        except Exception:
            # Never fall back to plaintext secrets on failed encryption.
            pass
    temp = CFG.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(CFG)

def fmt_num(value) -> str:
    try:
        return f"{float(str(value).replace(',', '')):,.0f}"
    except (ValueError, TypeError):
        return "—"

def fmt_money(value) -> str:
    n = fmt_num(value)
    return n + "원" if n != "—" else "—"

def fmt_pct(value) -> str:
    return f"{value:.1f}%" if value is not None else "—"

def source_label(source: str) -> str:
    return LABELS.get(source, source)

def choose_value(row: dict, *keys):
    for key in keys:
        val = row.get(key)
        if val is not None and str(val).strip() not in ("", "None", "null"):
            return val
    return None

def view_age(ms: int) -> str:
    if not ms: return "미전송"
    seconds = max(0, (int(time.time() * 1000) - int(ms)) // 1000)
    if seconds < 60: return f"{seconds}초 전"
    return f"{seconds // 60}분 전"

def guess_tailscale_ip() -> str:
    candidates = []
    program = "tailscale.exe"
    for environment in ("ProgramFiles", "LOCALAPPDATA", "ProgramFiles(x86)"):
        base = os.environ.get(environment, "")
        if base:
            path = Path(base) / "Tailscale" / "tailscale.exe"
            if path.is_file():
                program = str(path)
                break
    try:
        r = subprocess.run([program, "status", "--json"], capture_output=True,
                           text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0: return ""
        obj = json.loads(r.stdout)
        for peer in (obj.get("Peer") or {}).values():
            if not isinstance(peer, dict) or str(peer.get("OS", "")).lower() != "android":
                continue
            for ip in peer.get("TailscaleIPs") or []:
                try:
                    url = validate_hub_url(f"http://{ip}:8765")
                    candidates.append(url)
                except Exception:
                    pass
    except Exception:
        return ""
    return candidates[0] if len(set(candidates)) == 1 else ""

class MonitorWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(TITLE)
        self.root.geometry("1410x860")
        self.root.minsize(1080, 670)
        self.root.configure(bg=BG)
        self.closed = False
        self.syncing = False
        self.client = None
        self.snapshot = None
        self.visible_offset = {}
        self.auto = tk.BooleanVar(value=True)
        self.remember = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="사전로딩 시작 · 휴대폰 MSERVER 연결 준비 중")
        self.page_info = tk.StringVar(value="")
        self.url = tk.StringVar()
        self.token = tk.StringVar()
        self.source = tk.StringVar(value="전체")
        self.frame = tk.StringVar(value="전체")
        self.band = tk.StringVar(value="전체")
        self.only = tk.StringVar(value="전체")
        self.setup_style()
        saved = load_settings()
        self.url.set(saved.get("url", ""))
        self.token.set(saved.get("token") or os.environ.get("ARTITRADE_HUB_TOKEN", ""))
        self.url.set(saved.get("url") or os.environ.get("ARTITRADE_HUB_URL", ""))
        self.first_pair_prompt = False
        self.connection_visible = False
        self.token.trace_add("write", self.token_changed)
        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(0, self.startup)
        self.root.after(REFRESH_SECONDS * 1000, self.automatic_refresh)

    def setup_style(self):
        s = ttk.Style(self.root)
        try: s.theme_use("clam")
        except tk.TclError: pass
        s.configure(".", font=("Malgun Gothic", 10), background=BG, foreground=FG)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG, font=("Malgun Gothic", 10))
        s.configure("Muted.TLabel", background=BG, foreground=MUTED)
        s.configure("Good.TLabel", background=BG, foreground=GREEN)
        s.configure("Warn.TLabel", background=BG, foreground=AMBER)
        s.configure("Header.TLabel", background=BG, foreground=FG, font=("Malgun Gothic", 17, "bold"))
        s.configure("TButton", font=("Malgun Gothic", 10, "bold"), padding=(12, 8),
                    background=PANEL2, foreground=FG, borderwidth=0)
        s.map("TButton", background=[("active", "#274564"), ("disabled", "#1a2738")])
        s.configure("TEntry", fieldbackground=PANEL2, foreground=FG, insertcolor=FG,
                    bordercolor="#3a506d", padding=5)
        s.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=FG,
                    bordercolor="#3a506d", arrowsize=15)
        s.map("TCombobox", fieldbackground=[("readonly", PANEL2)], foreground=[("readonly", FG)])
        s.configure("TCheckbutton", background=BG, foreground=MUTED)
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=PANEL2, foreground=MUTED, padding=(16, 11))
        s.map("TNotebook.Tab", background=[("selected", "#224b70")], foreground=[("selected", FG)])
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                    rowheight=28, font=("Malgun Gothic", 9), borderwidth=0)
        s.configure("Treeview.Heading", background=PANEL2, foreground=MUTED,
                    font=("Malgun Gothic", 9, "bold"), padding=(4, 7))
        s.map("Treeview", background=[("selected", "#285b7d")])

    def setup_ui(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True, padx=16, pady=12)
        heading = ttk.Frame(outer)
        heading.pack(fill="x", pady=(0, 9))
        ttk.Label(heading, text="ARTITRADE  |  PC 모니터", style="Header.TLabel").pack(side="left")
        ttk.Label(heading, text="거래 · 가상매매 · 주문 전송 기능 없음", foreground=RED,
                  background=BG, font=("Malgun Gothic", 10, "bold")).pack(side="right", padx=6)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="실행 즉시 자동 사전로딩 · Tailscale → MSERVER → 주식·코인 원장",
                  style="Good.TLabel").pack(side="left", padx=(2, 14))
        ttk.Button(toolbar, text="지금 갱신", command=self.refresh).pack(side="left", padx=4)
        ttk.Button(toolbar, text="연결 설정", command=self.toggle_connection_settings).pack(side="left", padx=4)
        ttk.Checkbutton(toolbar, text="10초마다 자동", variable=self.auto).pack(side="left", padx=8)
        self.connection_frame = ttk.Frame(outer)
        ttk.Label(self.connection_frame, text="휴대폰 Tailscale", style="Muted.TLabel").pack(side="left")
        ttk.Entry(self.connection_frame, textvariable=self.url, width=28).pack(side="left", padx=(8, 13))
        ttk.Label(self.connection_frame, text="MSERVER 최초 페어링 토큰", style="Muted.TLabel").pack(side="left")
        ttk.Entry(self.connection_frame, textvariable=self.token, show="●", width=24).pack(side="left", padx=(8, 12))
        ttk.Checkbutton(self.connection_frame, text="인증정보 암호화 저장", variable=self.remember).pack(side="left")
        self.status_label = ttk.Label(outer, textvariable=self.status, style="Muted.TLabel")
        self.status_label.pack(fill="x", pady=(0, 7))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        self.tabs = {}
        for label in ["통합 현황", "리서치 분석", "보유 종목", "체결 이력", "연결·원장 진단"]:
            frame = ttk.Frame(tabs)
            tabs.add(frame, text=label)
            self.tabs[label] = frame
        tabs.bind("<<NotebookTabChanged>>", lambda event: self.render())
        self.notebook = tabs
        self.dash_info = tk.StringVar(value="PC EXE는 주문하지 않음 · 최신 휴대폰 원장 수신 전")
        ttk.Label(self.tabs["통합 현황"], textvariable=self.dash_info,
                  style="Muted.TLabel").pack(fill="x", padx=8, pady=8)
        self.tables = {}
        self.tables["통합 현황"] = self.create_table(self.tabs["통합 현황"],
            [("strategy", "시장·전략", 210), ("positions", "보유", 75), ("executions", "체결", 75),
             ("research", "리서치", 85), ("realized", "실현손익", 135),
             ("origin", "리서치 상태", 210), ("updated", "MSERVER 갱신", 130)])
        # Shared filter bar for per-account research and history.
        self.filter_row = ttk.Frame(self.tabs["리서치 분석"])
        self.filter_row.pack(fill="x", padx=6, pady=7)
        self.add_filters(self.filter_row)
        self.research_counts = tk.StringVar(value="신호 0건 · 실제/가상 목표달성률은 확정 판정만 분모에 포함")
        ttk.Label(self.tabs["리서치 분석"], textvariable=self.research_counts,
                  style="Muted.TLabel").pack(fill="x", padx=7, pady=(0, 7))
        ttk.Label(self.tabs["리서치 분석"], text="시간봉 × 점수대 · 실제 매수 / 가상진입 목표달성률",
                  style="Good.TLabel").pack(anchor="w", padx=7)
        self.tables["리서치 요약"] = self.create_table(self.tabs["리서치 분석"],
            [("source", "전략", 130), ("tf", "시간봉", 80), ("band", "점수대", 90),
             ("signals", "신호", 65), ("actual", "실매수", 75), ("ahit", "실제 달성", 76),
             ("amiss", "실제 미달", 76), ("apending", "실제 보류", 76), ("arate", "실제 달성률", 95),
             ("virtual", "가상진입", 80), ("vhit", "가상 달성", 76), ("vmiss", "가상 미달", 76),
             ("vpending", "가상 보류", 76), ("vrate", "가상 달성률", 95)], height=8)
        ttk.Label(self.tabs["리서치 분석"], text="개별 연구 신호 · 미진입 가상후보는 달성률 계산에서 제외",
                  style="Muted.TLabel").pack(anchor="w", padx=7, pady=(8, 1))
        self.tables["리서치 상세"] = self.create_table(self.tabs["리서치 분석"],
            [("source", "전략", 150), ("tf", "시간봉", 80), ("time", "기준시각", 153),
             ("stock", "종목", 155), ("score", "점수", 62), ("band", "점수대", 90),
             ("mode", "구분", 100), ("outcome", "판정", 90), ("reason", "상세", 370)], height=9)
        hfilters = ttk.Frame(self.tabs["보유 종목"])
        hfilters.pack(fill="x", padx=7, pady=7)
        self.add_source_only_filter(hfilters)
        self.tables["보유 종목"] = self.create_table(self.tabs["보유 종목"],
            [("source", "전략", 170), ("tf", "시간봉", 80), ("name", "종목", 205),
             ("time", "기준시각", 165), ("entry", "매수가", 130), ("last", "현재가", 130),
             ("qty", "잔고", 112), ("pnl", "평가손익", 125)], height=19)
        self.tables["체결 이력"] = self.create_table(self.tabs["체결 이력"],
            [("source", "전략", 165), ("tf", "시간봉", 80), ("name", "종목", 205),
             ("time", "체결시각", 165), ("side", "매수/매도", 92),
             ("price", "체결가", 130), ("qty", "체결수량", 110),
             ("pnl", "실현손익", 135)], height=20)
        self.tables["연결·원장 진단"] = self.create_table(self.tabs["연결·원장 진단"],
            [("source", "MSERVER 원장", 245), ("rev", "Revision", 105),
             ("exist", "존재", 110), ("time", "갱신 시각", 193),
             ("age", "최근 갱신", 132), ("count", "연구/보유", 190)], height=20)
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(7, 0))
        ttk.Label(footer, text="원본은 MSERVER · PC EXE는 읽기 전용 · 기존 매매 EXE는 반드시 별도로 종료",
                  style="Muted.TLabel").pack(side="left")
        ttk.Label(footer, textvariable=self.page_info, style="Muted.TLabel").pack(side="right")
        self.page_buttons = {}
        for title in ("리서치 상세", "보유 종목", "체결 이력"):
            pane = self.tabs["리서치 분석"] if title == "리서치 상세" else self.tabs[title]
            controls = ttk.Frame(pane)
            controls.pack(fill="x", padx=8, pady=(2, 6))
            ttk.Button(controls, text="◀ 이전 250건", command=lambda name=title:self.change_page(name, -1)).pack(side="left", padx=6)
            ttk.Button(controls, text="다음 250건 ▶", command=lambda name=title:self.change_page(name, 1)).pack(side="left", padx=6)
            label = tk.StringVar(value="0건")
            ttk.Label(controls, textvariable=label, style="Muted.TLabel").pack(side="left", padx=10)
            self.page_buttons[title] = label

    def create_table(self, parent, columns, height=16):
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True, padx=7, pady=3)
        view = ttk.Treeview(container, show="headings", height=height,
                            columns=[name for name, _, _ in columns], selectmode="browse")
        for key, label, width in columns:
            view.heading(key, text=label)
            view.column(key, width=width, minwidth=60, anchor="w" if key in ("source", "name", "reason", "origin") else "center")
        view.tag_configure("ok", foreground=GREEN)
        view.tag_configure("bad", foreground=RED)
        view.tag_configure("wait", foreground=AMBER)
        scy = ttk.Scrollbar(container, orient="vertical", command=view.yview)
        scx = ttk.Scrollbar(container, orient="horizontal", command=view.xview)
        view.configure(yscrollcommand=scy.set, xscrollcommand=scx.set)
        view.grid(row=0, column=0, sticky="nsew")
        scy.grid(row=0, column=1, sticky="ns")
        scx.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        return view

    def add_filters(self, parent):
        ttk.Label(parent, text="시장·전략", style="Muted.TLabel").pack(side="left", padx=4)
        options = ["전체"] + [key for key, _ in MARKETS]
        src = ttk.Combobox(parent, textvariable=self.source, values=options, state="readonly", width=18)
        src.pack(side="left", padx=(0, 13))
        ttk.Label(parent, text="시간봉", style="Muted.TLabel").pack(side="left", padx=4)
        ttk.Combobox(parent, textvariable=self.frame, values=["전체", *FRAMES],
                     state="readonly", width=9).pack(side="left", padx=(0, 13))
        ttk.Label(parent, text="점수대", style="Muted.TLabel").pack(side="left", padx=4)
        ttk.Combobox(parent, textvariable=self.band,
                     values=["전체", *BAND_NAMES, "점수 미기록", "기타 점수"],
                     state="readonly", width=13).pack(side="left", padx=(0, 13))
        for variable in (self.source, self.frame, self.band):
            variable.trace_add("write", lambda *_:self.reset_pages_and_render())

    def add_source_only_filter(self, parent):
        ttk.Label(parent, text="전략 필터 (리서치와 공통)", style="Muted.TLabel").pack(side="left", padx=(0, 9))
        ttk.Combobox(parent, textvariable=self.source,
                     values=["전체"]+[key for key, _ in MARKETS], state="readonly", width=22).pack(side="left")

    def reset_pages_and_render(self):
        self.visible_offset.clear()
        self.render()

    def selected(self, row, use_band=False) -> bool:
        if self.source.get() != "전체" and row.get("_source") != self.source.get(): return False
        tf = normalize_frame(row.get("tf") or row.get("frame"))
        if self.frame.get() != "전체" and tf != self.frame.get(): return False
        if use_band and self.band.get() != "전체" and score_band(row.get("score")) != self.band.get(): return False
        return True

    def set_rows(self, title, rows, paged=False):
        view = self.tables[title]
        view.delete(*view.get_children())
        offset = max(0, self.visible_offset.get(title, 0))
        total = len(rows)
        if paged:
            offset = min(offset, max(0, (total - 1) // PAGE_SIZE * PAGE_SIZE))
            self.visible_offset[title] = offset
            window = rows[offset:offset + PAGE_SIZE]
        else:
            window = rows[:5000]
        for cells, tag in window:
            view.insert("", "end", values=cells, tags=(tag,) if tag else ())
        if title in self.page_buttons:
            self.page_buttons[title].set(f"{offset + (1 if total else 0):,}~{min(total, offset + PAGE_SIZE):,} / {total:,}건")

    def change_page(self, name, direction):
        self.visible_offset[name] = max(0, self.visible_offset.get(name, 0) + direction * PAGE_SIZE)
        self.render()

    def toggle_connection_settings(self):
        self.show_connection_settings(not self.connection_visible)

    def show_connection_settings(self, visible: bool):
        if visible == self.connection_visible: return
        if visible:
            self.connection_frame.pack(fill="x", before=self.status_label, pady=(0, 8))
        else:
            self.connection_frame.pack_forget()
        self.connection_visible = visible

    def token_changed(self, *_):
        # Pasting a full pairing token triggers the preload automatically.
        # The user never has to click '조회' after the first pairing.
        if self.closed or self.syncing: return
        if re.fullmatch(r"[0-9a-fA-F]{64}", self.token.get().strip()):
            self.root.after(25, lambda:self.start_preload(silent=True))

    def startup(self):
        # The first screen is already constructed; all network and JSON loading
        # happens on a worker so Windows tabs remain responsive.
        self.start_preload(silent=True)

    def request_first_pairing(self):
        if self.first_pair_prompt or self.closed: return
        self.first_pair_prompt = True
        self.show_connection_settings(True)
        self.status.set("최초 1회: MSERVER 페어링 토큰 입력 필요 · 이후 실행부터 자동 사전로딩")
        self.root.after(100, self.prompt_token_once)

    def prompt_token_once(self):
        if self.closed or re.fullmatch(r"[0-9a-fA-F]{64}", self.token.get().strip()): return
        raw = simpledialog.askstring(
            "ARTITRADE 첫 연결 · 최초 1회",
            "휴대폰 MSERVER의 페어링 토큰(64자리)을 한 번 입력하면\n"
            "Windows 암호화 저장 후 다음부터 자동 연결합니다.",
            parent=self.root, show="●")
        if raw is None:
            self.status.set("페어링 정보가 없어 사전로딩 대기 · 연결 설정에 토큰 입력")
            return
        token = raw.strip()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", token):
            self.status.set("토큰 형식 오류 · MSERVER의 64자리 토큰을 연결 설정에 붙여넣으면 자동 시작")
            return
        self.token.set(token)
        self.start_preload(silent=True)

    def automatic_refresh(self):
        if self.closed: return
        if self.auto.get() and re.fullmatch(r"[0-9a-fA-F]{64}", self.token.get().strip()):
            self.start_preload(silent=True)
        self.root.after(REFRESH_SECONDS * 1000, self.automatic_refresh)

    def refresh(self, silent=False):
        # Optional retry action, never required for normal startup.
        self.start_preload(silent=silent)

    def start_preload(self, silent=False):
        if self.closed or self.syncing: return
        token = self.token.get().strip()
        if not re.fullmatch(r"[0-9a-fA-F]{64}", token):
            self.request_first_pairing()
            return
        saved_url = self.url.get().strip()
        existing = self.client
        self.syncing = True
        self.status.set("사전로딩 · Tailscale/MSERVER 확인 → 최신 원장 자동 수신 중")
        threading.Thread(
            target=self.preload_worker,
            args=(saved_url, token, existing),
            daemon=True, name="artitrade-pc-automatic-ledger-preload").start()

    def preload_worker(self, url, token, prior):
        try:
            client, snapshot, resolved = automatic_preload(
                url, token, guess_tailscale_ip,
                existing_client=prior,
                progress=self.preload_progress_worker)
            if not self.closed:
                self.root.after(0, lambda c=client, x=snapshot, b=resolved, k=token:
                    self.preload_complete(c, x, b, k))
        except Exception as exc:
            message = f"{type(exc).__name__}: {str(exc)[:220]}"
            if not self.closed:
                self.root.after(0, lambda v=message:self.preload_failed(v))

    def preload_progress_worker(self, done, total, key):
        if not self.closed:
            self.root.after(0, lambda d=done, t=total, k=key:
                self.status.set(f"사전로딩 · MSERVER 원장 {d}/{t}개 수신/검증 · {k}"))

    def preload_complete(self, client, snapshot, resolved, token):
        if self.closed: return
        self.client = client
        self.snapshot = snapshot
        self.syncing = False
        self.url.set(resolved)
        # Only remember credentials after an authenticated successful GET.
        # DPAPI errors must never lead to plaintext fallback storage.
        try:
            save_settings(resolved, token, self.remember.get())
        except Exception as exc:
            self.status.set("원장 수신 성공 · 암호화 설정 저장 실패: "+type(exc).__name__)
        else:
            warning_count = len(snapshot.get("warnings") or [])
            count = len(snapshot.get("records") or {})
            status = f"사전로딩 완료 · MSERVER 원장 {count}개 · "+time.strftime("%H:%M:%S")
            if warning_count:
                status += f" · 일부 조회 오류 {warning_count}건 (진단 탭 확인)"
            self.status.set(status)
        self.show_connection_settings(False)
        self.render()

    def preload_failed(self, message):
        if self.closed: return
        self.syncing = False
        self.status.set("자동 사전로딩 실패 · 10초 후 재시도 · "+message)
        # Do not erase prior displayed data, or claim stale data is current.
        if self.snapshot:
            self.dash_info.set("MSERVER 재연결 실패 · 직전 조회 원장 표시 중 · 실시간 아님")
        else:
            self.show_connection_settings(True)

    def render(self):
        if not self.snapshot: return
        active = self.notebook.tab(self.notebook.select(), "text")
        if active == "통합 현황": self.render_overview()
        elif active == "리서치 분석": self.render_research()
        elif active == "보유 종목": self.render_holdings()
        elif active == "체결 이력": self.render_executions()
        else: self.render_diagnostic()

    def render_overview(self):
        snap = self.snapshot
        source = snap["segments"]
        out = []
        for key, label in MARKETS:
            part = source.get(key) or {}
            ledger = part.get("ledger") or {}
            research = part.get("research") or {}
            positions = ledger.get("positions") or []
            executions = ledger.get("executions") or []
            items = research.get("items") or []
            if not isinstance(items, list): items = []
            if not isinstance(positions, list): positions = []
            if not isinstance(executions, list): executions = []
            source_meta = ("stock", "cw") if key == "stock/close" else (
                ("stock", "at") if key.startswith("stock/") else tuple(key.split("/")))
            meta = snap["metas"].get((source_meta[0], source_meta[1], "research"), {})
            updated = view_age(meta.get("updated_at", 0))
            if not research or not items:
                label_state = "리서치 미수신 / 0건 (원장 진단 필요)"
                color = "bad"
            else:
                label_state = f"MSERVER 연구 {len(items):,}건"
                color = "ok"
            out.append(((label, len(positions), len(executions), len(items),
                         fmt_money(ledger.get("realized_pnl")), label_state, updated), color))
        self.set_rows("통합 현황", out)
        state = snap.get("status", {})
        stock_owner = (state.get("stock") or {}).get("desired", "미확인")
        coin_owner = (state.get("coin") or {}).get("desired", "미확인")
        self.dash_info.set(
            f"모바일 서버 조회 전용 · 주식 실행권 {stock_owner} / 코인 실행권 {coin_owner} · "
            f"리서치 0건은 정상으로 간주하지 않음 · 갱신 {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def included_research(self):
        rows = []
        for source, part in self.snapshot["segments"].items():
            for item in research_rows(part):
                row = dict(item)
                row["_source"] = source
                if self.selected(row, use_band=True):
                    rows.append(row)
        return rows

    def render_research(self):
        rows = self.included_research()
        grouped = {}
        for row in rows:
            grouped.setdefault(row["_source"], []).append(row)
        summary_rows = []
        for source, items in grouped.items():
            for x in summarize_research(items):
                summary_rows.append(((
                    source_label(source), x["frame"], x["band"], x["signals"],
                    x["actual_count"], x["actual_hit"], x["actual_miss"], x["actual_pending"],
                    fmt_pct(x["actual_rate"]), x["virtual_entries"], x["virtual_hit"],
                    x["virtual_miss"], x["virtual_pending"], fmt_pct(x["virtual_rate"]),
                ), "ok" if (x["actual_rate"] is not None and x["actual_rate"] >= 50) else
                    "bad" if (x["actual_rate"] is not None and x["actual_rate"] < 30) else "wait"))
        summary_rows.sort(key=lambda x:(x[0][0], x[0][1], x[0][2]))
        self.set_rows("리서치 요약", summary_rows)
        rendered = []
        for row in rows:
            actual = bool_value(row.get("bought"))
            entry = bool_value(row.get("entry_observed"))
            outcome = outcome_of(row)
            mode = "실제 체결" if actual else "가상 진입" if entry else "가상 미진입"
            color = ("ok" if outcome == "HIT" else "bad" if outcome == "MISS" else "wait")
            rendered.append(((
                source_label(row["_source"]), normalize_frame(row.get("tf") or row.get("frame")) or "미분류",
                time_from_row(row), choose_value(row, "name", "code", "market", "symbol") or "미기록",
                choose_value(row, "score", "gmo_score_at_signal") or "—",
                score_band(row.get("score")), mode, outcome,
                str(choose_value(row, "reason", "decision", "entry_status") or "")[:150]
            ), color))
        rendered.sort(key=lambda x:x[0][2], reverse=True)
        self.set_rows("리서치 상세", rendered, paged=True)
        self.research_counts.set(
            f"선택한 연구 신호 {len(rows):,}건 · 점수대 {len(summary_rows)}개 행 · "
            "실제/가상 달성률은 HIT/(HIT+MISS), 보류 제외"
            + (" · 수신 실패 기록 있음" if self.snapshot.get("warnings") else "")
        )

    def render_holdings(self):
        rows = []
        for item in flatten_items(self.snapshot["segments"], "positions"):
            if not self.selected(item): continue
            pnl = choose_value(item, "paper_pnl", "unrealized_pnl", "net_pnl", "pnl")
            try: color = "bad" if float(pnl) < 0 else "ok" if float(pnl) > 0 else "wait"
            except (TypeError, ValueError): color = "wait"
            rows.append(((
                source_label(item["_source"]), item["_frame"] or "미기록",
                choose_value(item, "name", "market", "code", "symbol") or "미기록",
                time_from_row(item),
                fmt_num(choose_value(item, "entry_price", "entry", "avg_buy_price")),
                fmt_num(choose_value(item, "last_price", "mark", "current_price")),
                fmt_num(choose_value(item, "qty", "quantity", "balance")),
                fmt_money(pnl),
            ), color))
        rows.sort(key=lambda x:x[0][3], reverse=True)
        self.set_rows("보유 종목", rows, paged=True)

    def render_executions(self):
        rows = []
        for item in flatten_items(self.snapshot["segments"], "executions"):
            if not self.selected(item): continue
            profit = choose_value(item, "net_profit", "net_realized", "net_pnl", "pnl")
            try: color = "bad" if float(profit) < 0 else "ok" if float(profit) > 0 else "wait"
            except (TypeError, ValueError): color = "wait"
            rows.append(((
                source_label(item["_source"]), item["_frame"] or "미기록",
                choose_value(item, "name", "market", "code", "symbol") or "미기록",
                time_from_row(item), choose_value(item, "side", "type", "action") or "—",
                fmt_num(choose_value(item, "price", "fill_price", "executed_price")),
                fmt_num(choose_value(item, "qty", "quantity", "volume")),
                fmt_money(profit),
            ), color))
        rows.sort(key=lambda x:x[0][3], reverse=True)
        self.set_rows("체결 이력", rows, paged=True)

    def render_diagnostic(self):
        snap = self.snapshot
        rows = []
        for market, strategy in [
            ("stock","at"), ("stock","cw"), ("stock","shared"),
            ("coin","general"), ("coin","ma"), ("coin","shared"),
        ]:
            for bucket in (("ledger",) if strategy == "shared" else ("ledger", "research", "errors")):
                meta = snap["metas"].get((market,strategy,bucket)) or {}
                data = snap["records"].get((market,strategy,bucket)) or {}
                updated = int(meta.get("updated_at", 0) or 0)
                updated_text = event_time(str(updated)) if updated else "미전송"
                exist = bool(meta.get("exists"))
                count = len(data.get("items") or []) if bucket == "research" else len(data.get("positions") or []) if bucket == "ledger" else len(data.get("items") or [])
                age = view_age(updated)
                rows.append(((
                    market+"/"+strategy+"/"+bucket, meta.get("revision",0),
                    "존재" if exist else "미수신", updated_text, age,
                    f"{count:,}건 (해당 저장 키 최상위)"
                ), "bad" if not exist else "wait" if updated and int(time.time()*1000)-updated>5*60*1000 else "ok"))
        for warning in snap.get("warnings") or []:
            rows.append(((warning[:120],"—","조회 오류","—","—","원본 미변경"),"bad"))
        self.set_rows("연결·원장 진단", rows)
        self.page_info.set(f"원장 소스 {len(snap['records'])}개 · 연결시각 {time.strftime('%H:%M:%S',time.localtime(snap['fetched_at']))}")

    def close(self):
        self.closed = True
        # Never persist unauthenticated credentials or overwrite a verified pairing
        # with a mistyped address/token during shutdown.
        if self.client is not None and self.snapshot is not None:
            try: save_settings(self.client.base, self.client.token, self.remember.get())
            except Exception: pass
        self.root.destroy()

def main():
    root = tk.Tk()
    try:
        MonitorWindow(root)
    except Exception as exc:
        messagebox.showerror("ARTITRADE PC 모니터 오류",str(exc))
        raise
    root.mainloop()

if __name__ == "__main__":
    main()
