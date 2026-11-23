#!/usr/bin/env python3
"""Trader-video learning loop: discover -> fetch caption text -> keyword digest (-> Claude extracts rules -> backtest).

Subcommands
  search  "<query>" [--n 10]        find videos (metadata only; works from a cloud IP)
  fetch   <video_id|url> ...        save caption text to <data>/transcripts/<id>.json
  inbox                              import every *.txt/*.vtt/*.srt dropped in <data>/inbox/ (manual or other tools)
  digest  [<video_id> ...]          keyword sentences (stop-loss, R:R, scale-in/out, close-bet, supply/demand ...)
  list                               show the index

Backends for `fetch` are tried in order: youtube-transcript-api, yt-dlp subtitles, inbox files.
YouTube blocks many datacenter IPs ("confirm you're not a bot"); this tool never bypasses that (no cookies, no spoofing).
Run `fetch` on a machine whose IP YouTube accepts (the owner's PC) or drop transcripts into <data>/inbox/.

Data dir: $YT_DATA_DIR or ./research_data/youtube (never commit: third-party captions, may be copyrighted).
"""
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

DATA = Path(os.environ.get("YT_DATA_DIR", "research_data/youtube"))
KEYS = {
    "손절": r"손절|칼손절|스탑|stop",
    "손익비": r"손익비|리스크|risk.?reward|R:R|1\s*:\s*\d",
    "분할": r"분할\s*(매수|매도|진입|청산)|물타기|불타기|추격",
    "종가베팅": r"종가\s*(베팅|배팅)|오버나이트|다음\s*날|익일",
    "수급": r"수급|외국인|기관|순매수|거래대금|거래량",
    "장세": r"하락장|상승장|횡보|200일|이평|정배열|역배열|지수",
    "진입": r"진입|매수\s*타점|돌파|눌림|신고가|상한가|반등",
    "청산": r"익절|매도\s*타점|이탈|수급\s*이탈|손익",
}


def vid(s):
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", s)
    return m.group(1) if m else s


def ensure():
    for d in ("transcripts", "inbox", "digests"):
        (DATA / d).mkdir(parents=True, exist_ok=True)


def load_index():
    p = DATA / "index.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_index(ix):
    (DATA / "index.json").write_text(json.dumps(ix, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_search(a):
    ensure()
    out = subprocess.run(["yt-dlp", "--skip-download", "--flat-playlist", "-J", f"ytsearch{a.n}:{a.query}"],
                         capture_output=True, text=True, timeout=180)
    raw = out.stdout
    try:
        entries = json.loads(raw[raw.index("{"):])["entries"]
    except Exception:
        print("search failed:", (out.stderr or raw)[-400:]); return 1
    ix = load_index()
    for e in entries:
        ix.setdefault(e["id"], {}).update(title=e.get("title"), channel=e.get("channel"), duration=e.get("duration"),
                                          query=a.query, found_at=time.strftime("%F"))
        print(e["id"], "|", e.get("channel"), "|", e.get("duration"), "s |", e.get("title"))
    save_index(ix)


def fetch_api(v):
    from youtube_transcript_api import YouTubeTranscriptApi
    sn = list(YouTubeTranscriptApi().fetch(v, languages=["ko", "en"]))
    return [{"t": round(s.start, 1), "text": s.text} for s in sn], "youtube-transcript-api"


def fetch_ytdlp(v):
    tmp = DATA / "tmp"; tmp.mkdir(exist_ok=True)
    subprocess.run(["yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs", "ko.*,en.*",
                    "--sub-format", "vtt", "-o", str(tmp / "%(id)s.%(ext)s"), f"https://www.youtube.com/watch?v={v}"],
                   capture_output=True, text=True, timeout=240)
    fs = sorted(tmp.glob(f"{v}*.vtt"))
    if not fs:
        raise RuntimeError("no subtitle file (blocked or none)")
    return parse_vtt(fs[0].read_text(encoding="utf-8")), "yt-dlp"


def parse_vtt(txt):
    seg, last = [], ""
    for blk in re.split(r"\n\n+", txt):
        lines = [l for l in blk.splitlines() if l.strip()]
        m = next((re.match(r"(\d+):(\d+):([\d.]+) -->", l) for l in lines if "-->" in l), None)
        if not m:
            continue
        body = re.sub(r"<[^>]+>", "", " ".join(l for l in lines if "-->" not in l and not l.startswith(("WEBVTT", "Kind", "Language")))).strip()
        if body and body != last:
            seg.append({"t": round(int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]), 1), "text": body}); last = body
    return seg


def save_tr(v, seg, src):
    ensure()
    ix = load_index()
    (DATA / "transcripts" / f"{v}.json").write_text(
        json.dumps({"id": v, "source": src, "meta": ix.get(v, {}), "segments": seg}, ensure_ascii=False), encoding="utf-8")
    ix.setdefault(v, {})["transcript"] = src; save_index(ix)
    print(v, "saved", len(seg), "segments via", src)


def cmd_fetch(a):
    ensure()
    for s in a.ids:
        v = vid(s)
        for fn in (fetch_api, fetch_ytdlp):
            try:
                seg, src = fn(v); save_tr(v, seg, src); break
            except Exception as e:
                last = f"{fn.__name__}: {type(e).__name__} {str(e)[:120]}"
        else:
            print(v, "FAILED ->", last, "(drop a transcript into", DATA / "inbox", "as", f"{v}.txt)")


def cmd_inbox(a):
    ensure()
    for f in sorted((DATA / "inbox").glob("*")):
        if f.suffix not in (".txt", ".vtt", ".srt"):
            continue
        v = vid(f.stem); t = f.read_text(encoding="utf-8", errors="ignore")
        seg = parse_vtt(t) if f.suffix == ".vtt" else [{"t": i, "text": l.strip()} for i, l in enumerate(t.splitlines()) if l.strip()]
        save_tr(v, seg, "inbox")


def cmd_digest(a):
    ensure()
    ids = [vid(x) for x in a.ids] or [p.stem for p in (DATA / "transcripts").glob("*.json")]
    for v in ids:
        d = json.loads((DATA / "transcripts" / f"{v}.json").read_text(encoding="utf-8"))
        text = " ".join(s["text"] for s in d["segments"]); sents = re.split(r"(?<=[.?!다요죠])\s+", text)
        out = [f"# {v} {d['meta'].get('title','')}  ({d['meta'].get('channel','')})", f"segments={len(d['segments'])}"]
        for k, pat in KEYS.items():
            hit = [s.strip() for s in sents if re.search(pat, s)][: a.max]
            if hit:
                out.append(f"\n## {k}"); out += [f"- {h[:220]}" for h in hit]
        (DATA / "digests" / f"{v}.md").write_text("\n".join(out), encoding="utf-8"); print("\n".join(out))


def cmd_list(a):
    for v, m in load_index().items():
        print(v, "|", m.get("channel"), "|", m.get("title"), "| transcript:", m.get("transcript", "-"))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="c", required=True)
    x = s.add_parser("search"); x.add_argument("query"); x.add_argument("--n", type=int, default=10); x.set_defaults(f=cmd_search)
    x = s.add_parser("fetch"); x.add_argument("ids", nargs="+"); x.set_defaults(f=cmd_fetch)
    x = s.add_parser("inbox"); x.set_defaults(f=cmd_inbox)
    x = s.add_parser("digest"); x.add_argument("ids", nargs="*"); x.add_argument("--max", type=int, default=12); x.set_defaults(f=cmd_digest)
    x = s.add_parser("list"); x.set_defaults(f=cmd_list)
    a = p.parse_args(); sys.exit(a.f(a) or 0)
