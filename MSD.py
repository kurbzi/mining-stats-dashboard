#!/usr/bin/env python3
# ============================================================
# Mining Stats Dashboard — v1.0.1
# Author: kurbzi
# License: MIT
#
# ✅ Edit only the CONFIG SECTION below.
# ⚠️ DO NOT EDIT BELOW THAT LINE unless you know what you're doing.
#
# NOTE ABOUT BLOCK COUNTS:
# - This dashboard increments "Blocks" when a miner exposes a block counter in /api/system/info
#   (commonly "blockFound" / "blocksFound" etc).
# - Some NerdOS/AxeOS builds may require enabling "Block Found Alerts" (or similar) in the miner UI
#   to ensure the block counter/alerts are active and updated.
# - If your miner does NOT expose a block counter, blocks will remain 0 unless you add another source
#   (e.g. pool-side checks or webhooks) in a future version.
# ============================================================

# =========================
# CONFIG SECTION (EDIT THIS)
# =========================

# How often to poll miners (seconds)
REFRESH_SECONDS = 5

# How often to refresh coin prices/difficulty (seconds)
COIN_REFRESH_SECONDS = 30

# Data stale thresholds (seconds)
STALE_YELLOW_SECONDS = 20
STALE_RED_SECONDS = 60

# Temperature color thresholds
TEMP_ORANGE_AT = 66
TEMP_RED_AT = 75

# Miner page rotation (seconds): how long each group of miners stays on screen
MINER_PAGE_SECONDS = 10

# Miners shown per page
MINERS_PER_PAGE = 3

# ------------------------------------------------------------
# MINERS (EDIT THESE)
# ------------------------------------------------------------
# ✅ Replace the "label" with your miner name (what you want to see on the dashboard).
# ✅ Replace the "ip" with your miner IP address.
#
# OPTIONAL:
# ✅ "model" is only used for Miner of the Week scoring (to compare performance vs a baseline).
#    - If you don't care about MOTW scoring, you can remove the "model" line entirely.
#    - If you DO want scoring, set "model" to any name you like (e.g. "MyModel").
#      Just make sure the same model name exists in MODEL_BASELINES.
#
MINERS = {
    "Miner1": {
        "ip": "192.168.0.XXX",      # <<< PASTE IP ADDRESS HERE
        "label": "Miner 1 Name",    # <<< PASTE MINER NAME HERE
        "model": "MyModel",         # <<< OPTIONAL: used only for Miner of the Week scoring
    },
    "Miner2": {
        "ip": "192.168.0.XXX",      # <<< PASTE IP ADDRESS HERE
        "label": "Miner 2 Name",    # <<< PASTE MINER NAME HERE
        "model": "MyModel",         # <<< OPTIONAL
    },
    "Miner3": {
        "ip": "192.168.0.XXX",      # <<< PASTE IP ADDRESS HERE
        "label": "Miner 3 Name",    # <<< PASTE MINER NAME HERE
        "model": "AnotherModel",    # <<< OPTIONAL
    },
    "Miner4": {
        "ip": "192.168.0.XXX",      # <<< PASTE IP ADDRESS HERE
        "label": "Miner 4 Name",    # <<< PASTE MINER NAME HERE
        "model": "AnotherModel",    # <<< OPTIONAL
        # No model = no baseline scoring for MOTW (still eligible via blocks/diff/uptime components)
    },
}

# ------------------------------------------------------------
# BASELINES (EDIT IF YOU WANT)
# ------------------------------------------------------------
# Used for Miner of the Week scoring. You can rename these model keys to anything.
MODEL_BASELINES = {
    "MyModel": {"baseline_ths": 5.00, "baseline_shares_per_hour": 40.0},
    "AnotherModel": {"baseline_ths": 1.15, "baseline_shares_per_hour": 30.0},
}

# ------------------------------------------------------------
# DISCORD WEBHOOK (OPTIONAL)
# ------------------------------------------------------------
DISCORD_WEBHOOK_URL = "PASTE_WEBHOOK_HERE"

# ------------------------------------------------------------
# COINS (POPULAR DEFAULTS)
# ------------------------------------------------------------
COIN_ORDER = ["BTC", "BCH", "FB", "DGB"]

# Web server settings
HOST = "0.0.0.0"
PORT = 8788

# =========================
# DO NOT EDIT BELOW THIS LINE
# =========================

from flask import Flask, request, jsonify, Response
import time
import threading
import requests
from datetime import datetime
import os
import json
import re
from typing import Optional

app = Flask(__name__)

BASE_DIR = os.path.dirname(__file__)
BLOCKS_FILE = os.path.join(BASE_DIR, "blocks.json")
WEEKLY_BEST_FILE = os.path.join(BASE_DIR, "weekly_best.json")       # previous week's winner summary
WEEKLY_CURRENT_FILE = os.path.join(BASE_DIR, "weekly_current.json") # per-miner current-week best diffs
MOTW_FILE = os.path.join(BASE_DIR, "miner_of_week.json")

IP_TO_LABEL = {cfg["ip"]: cfg.get("label", name) for name, cfg in MINERS.items()}

miners_state = {}
last_block_popup = None  # {"miner": "...", "ts_unix": int, "is_test": bool}

_blocks_lock = threading.Lock()
block_counts = {}
last_block_ts = {}
last_any_block_ts = None

# Reset-safe tracking for miner-reported counters (e.g. miners that expose "blockFound")
reported_last = {}  # { "Miner Label": last_seen_reported_int }

# Weekly baseline block counts (to compute weekly blocks)
week_start_counts = {}
week_start_unix = None

_coin_lock = threading.Lock()
coin_state = {sym: {"price_gbp": None, "diff": None} for sym in COIN_ORDER}
coin_last_ok_unix = None
coin_last_err = None

_logo_lock = threading.Lock()
coin_logos = {sym: None for sym in COIN_ORDER}
logos_last_ok_unix = None
logos_last_err = None

_last_seen_lock = threading.Lock()
last_seen_ts = {}

_weekly_lock = threading.Lock()
weekly_best = {"prev_name": None, "prev_value": None, "prev_str": None}
weekly_current = {}  # { miner_name: numeric best diff for THIS week }

_motw_lock = threading.Lock()
motw = {"prev_name": None, "prev_score": None, "prev_str": None, "prev_week_iso": None}

# =========================
# HELPERS
# =========================

def now_iso():
    return datetime.utcnow().strftime("%d-%m-%Y %H:%M:%S")


def now_hms():
    return datetime.now().strftime("%H:%M:%S")


def fmt_hashrate_ths(ths_value):
    try:
        v = float(ths_value)
    except Exception:
        return "-"
    return f"{v:.2f} TH/s"


def fmt_temp_pair(asic_temp, vr_temp):
    try:
        a = int(round(float(asic_temp)))
        v = int(round(float(vr_temp)))
        return f"{a}° / {v}°"
    except Exception:
        return "- / -"


_SI_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGTP])\s*$", re.IGNORECASE)


def diff_to_number(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        try:
            return float(x)
        except Exception:
            return None

    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        pass

    m = _SI_RE.match(s)
    if not m:
        return None
    num = float(m.group(1))
    suf = m.group(2).upper()
    mult = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15}.get(suf, 1.0)
    return num * mult


def fmt_diff_si_adaptive(n):
    v = diff_to_number(n)
    if v is None:
        return "-"

    sign = "-" if v < 0 else ""
    a = abs(v)

    def with_unit(val, suffix):
        if val < 10:
            dp = 2
        elif val < 100:
            dp = 1
        else:
            dp = 0
        return f"{sign}{val:.{dp}f}{suffix}"

    if a >= 1e15:
        return with_unit(a / 1e15, "P")
    if a >= 1e12:
        return with_unit(a / 1e12, "T")
    if a >= 1e9:
        return with_unit(a / 1e9, "G")
    if a >= 1e6:
        return with_unit(a / 1e6, "M")
    if a >= 1e3:
        return with_unit(a / 1e3, "K")
    return f"{sign}{int(round(a))}"


def fmt_diff_si(n):
    if n is None:
        return "-"
    if isinstance(n, str):
        s = n.strip()
        if _SI_RE.match(s):
            return s
    try:
        v = float(n)
    except Exception:
        return str(n).strip() if n is not None else "-"

    if v >= 1_000_000_000_000_000:
        return f"{v/1_000_000_000_000_000:.2f}P"
    if v >= 1_000_000_000_000:
        return f"{v/1_000_000_000_000:.2f}T"
    if v >= 1_000_000_000:
        return f"{v/1_000_000_000:.2f}G"
    if v >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v/1_000:.2f}K"
    return f"{v:.2f}"


def fmt_int(n):
    try:
        v = int(float(n))
        return f"{v:,}"
    except Exception:
        return "-"


def fmt_int_short(n):
    """Short integer formatting: 34k, 1.2M, etc."""
    try:
        v = float(n)
    except Exception:
        return "-"
    sign = "-" if v < 0 else ""
    a = abs(v)

    if a >= 1e9:
        val = a / 1e9
        s = f"{val:.1f}G"
    elif a >= 1e6:
        val = a / 1e6
        s = f"{val:.1f}M"
    elif a >= 1e3:
        val = a / 1e3
        s = f"{val:.0f}k"
    else:
        return f"{int(round(v))}"

    if s.endswith(".0G") or s.endswith(".0M"):
        s = s.replace(".0", "")
    return sign + s


def fmt_gbp(x):
    try:
        v = float(x)
    except Exception:
        return "-"
    if v >= 1:
        return f"£{v:,.2f}"
    if v >= 0.01:
        return f"£{v:,.4f}"
    return f"£{v:.6f}"


def pick_first(js, keys, default=None):
    for k in keys:
        if isinstance(js, dict) and k in js and js.get(k) is not None:
            return js.get(k)
    return default


def _safe_read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _safe_write_json(path: str, obj):
    tmp = path + ".tmp"
    bak = path + ".bak"
    try:
        if os.path.exists(path):
            try:
                with open(path, "rb") as fsrc:
                    data = fsrc.read()
                with open(bak, "wb") as fdst:
                    fdst.write(data)
            except Exception:
                pass

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def _load_blocks():
    global block_counts, last_block_ts, last_any_block_ts, reported_last, week_start_counts, week_start_unix

    def parse(data):
        global block_counts, last_block_ts, last_any_block_ts, reported_last, week_start_counts, week_start_unix

        if not isinstance(data, dict):
            return False

        if "counts" in data and isinstance(data.get("counts"), dict):
            counts = data.get("counts", {})
            lts = data.get("last_ts", {}) if isinstance(data.get("last_ts"), dict) else {}
            last_any = data.get("last_any_ts", None)
            rep = data.get("reported_last", {}) if isinstance(data.get("reported_last"), dict) else {}
            wsc = data.get("week_start_counts", {}) if isinstance(data.get("week_start_counts"), dict) else {}
            wsu = data.get("week_start_unix", None)

            cleaned_counts = {}
            for k, v in counts.items():
                try:
                    cleaned_counts[str(k)] = int(v)
                except Exception:
                    cleaned_counts[str(k)] = 0

            cleaned_lts = {}
            for k, v in lts.items():
                try:
                    cleaned_lts[str(k)] = int(v)
                except Exception:
                    pass

            cleaned_rep = {}
            for k, v in rep.items():
                try:
                    cleaned_rep[str(k)] = int(v)
                except Exception:
                    pass

            cleaned_wsc = {}
            for k, v in wsc.items():
                try:
                    cleaned_wsc[str(k)] = int(v)
                except Exception:
                    cleaned_wsc[str(k)] = 0

            block_counts = cleaned_counts
            last_block_ts = cleaned_lts
            last_any_block_ts = int(last_any) if isinstance(last_any, (int, float)) else None
            reported_last = cleaned_rep

            week_start_unix_local = int(wsu) if isinstance(wsu, (int, float)) else None
            week_start_counts_local = cleaned_wsc if isinstance(wsc, dict) else {}

            if week_start_unix_local is None:
                week_start_unix_local = int(time.time())
                week_start_counts_local = dict(block_counts)

            week_start_unix = week_start_unix_local
            week_start_counts = week_start_counts_local
            return True

        # legacy: dict of counts
        cleaned = {}
        for k, v in data.items():
            try:
                cleaned[str(k)] = int(v)
            except Exception:
                cleaned[str(k)] = 0

        block_counts = cleaned
        last_block_ts = {}
        last_any_block_ts = None
        reported_last = {}
        week_start_unix = int(time.time())
        week_start_counts = dict(block_counts)
        return True

    data = _safe_read_json(BLOCKS_FILE)
    ok = parse(data)

    if not ok:
        data_bak = _safe_read_json(BLOCKS_FILE + ".bak")
        ok = parse(data_bak)

    if not ok:
        block_counts = {}
        last_block_ts = {}
        last_any_block_ts = None
        reported_last = {}
        week_start_unix = int(time.time())
        week_start_counts = {}


def _save_blocks():
    obj = {
        "counts": block_counts,
        "last_ts": last_block_ts,
        "last_any_ts": last_any_block_ts,
        "reported_last": reported_last,
        "week_start_counts": week_start_counts,
        "week_start_unix": week_start_unix,
    }
    _safe_write_json(BLOCKS_FILE, obj)


def _load_weekly_best():
    global weekly_best
    data = _safe_read_json(WEEKLY_BEST_FILE) or _safe_read_json(WEEKLY_BEST_FILE + ".bak")
    if not isinstance(data, dict):
        weekly_best = {"prev_name": None, "prev_value": None, "prev_str": None}
        return
    weekly_best = {
        "prev_name": data.get("prev_name"),
        "prev_value": data.get("prev_value"),
        "prev_str": data.get("prev_str"),
    }


def _save_weekly_best():
    _safe_write_json(WEEKLY_BEST_FILE, weekly_best)


def _load_weekly_current():
    """Load per-miner current-week best diffs."""
    global weekly_current
    data = _safe_read_json(WEEKLY_CURRENT_FILE) or _safe_read_json(WEEKLY_CURRENT_FILE + ".bak")
    if not isinstance(data, dict):
        weekly_current = {}
        return

    stored_week = data.get("week_start_unix")
    current = data.get("current") if isinstance(data.get("current"), dict) else {}

    try:
        stored_week_int = int(stored_week) if stored_week is not None else None
    except Exception:
        stored_week_int = None

    # If the week_start_unix changed, treat it as a new week and discard old values
    if stored_week_int is not None and isinstance(week_start_unix, (int, float)):
        if int(week_start_unix) != stored_week_int:
            weekly_current = {}
            return

    cleaned = {}
    for k, v in current.items():
        try:
            cleaned[str(k)] = float(v)
        except Exception:
            continue
    weekly_current = cleaned


def _save_weekly_current():
    obj = {
        "week_start_unix": week_start_unix,
        "current": weekly_current,
    }
    _safe_write_json(WEEKLY_CURRENT_FILE, obj)


def _load_motw():
    global motw
    data = _safe_read_json(MOTW_FILE) or _safe_read_json(MOTW_FILE + ".bak")
    if not isinstance(data, dict):
        motw = {"prev_name": None, "prev_score": None, "prev_str": None, "prev_week_iso": None}
        return
    motw = {
        "prev_name": data.get("prev_name"),
        "prev_score": data.get("prev_score"),
        "prev_str": data.get("prev_str"),
        "prev_week_iso": data.get("prev_week_iso"),
    }


def _save_motw():
    _safe_write_json(MOTW_FILE, motw)


def send_discord_block_found(miner_name: str, when_hms: str, is_test: bool = False,
                            share_diff=None, network_diff=None):
    if not DISCORD_WEBHOOK_URL or DISCORD_WEBHOOK_URL.strip() == "PASTE_WEBHOOK_HERE":
        return

    prefix = "🧪 **TEST WEBHOOK**" if is_test else "🧱 **BLOCK FOUND!**"
    lines = [prefix, f"👑 **{miner_name}**", f"🕒 {when_hms}"]

    diff_str = fmt_diff_si(share_diff) if share_diff is not None else None
    net_str = fmt_diff_si(network_diff) if network_diff is not None else None

    if diff_str and net_str:
        lines.append(f"🎯 Diff: {diff_str} (network: {net_str})")
    elif diff_str:
        lines.append(f"🎯 Diff: {diff_str}")
    elif net_str:
        lines.append(f"🎯 Network diff: {net_str}")

    payload = {"content": "\n".join(lines)}
    try:
        requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=4)
    except Exception:
        pass


# =========================
# MINER POLLING
# =========================

def poll_miner_api(ip: str):
    url = f"http://{ip}/api/system/info"
    try:
        r = requests.get(url, timeout=2)
        r.raise_for_status()
        js = r.json()

        gh = pick_first(js, ["hashRate", "hashRate_1m", "hashrate", "hashrate_1m"], None)
        ths = (float(gh) / 1000.0) if gh is not None else None

        session_best = pick_first(
            js,
            ["bestSessionDiff", "best_session_diff", "sessionBestDiff", "session_best_diff", "bestDiff", "best_diff"],
            None,
        )
        best_overall = pick_first(
            js,
            ["totalBestDiff", "total_best_diff", "bestDiffAllTime", "best_all_time", "overallBestDiff",
             "overall_best_diff", "bestDiff", "best_diff"],
            None,
        )
        if best_overall is None and session_best is not None:
            best_overall = session_best
        if session_best is None and best_overall is not None:
            session_best = best_overall

        shares_accepted = pick_first(js, ["sharesAccepted"], None)
        shares_rejected = pick_first(js, ["sharesRejected", "rejectedShares", "sharesRejectedTotal"], None)
        uptime_seconds = pick_first(js, ["uptimeSeconds"], None)

        # Some miners expose a block counter (often "blockFound"); we treat any increase as a block event,
        # and any decrease as a reset (baseline update only).
        blocks_found = pick_first(js, ["blockFound", "blocksFound", "blocks_found", "foundBlocks", "blocks"], None)

        fan_speed = pick_first(js, ["fanspeed", "fanSpeed", "fan_speed", "fanPercent", "fan_percent"], None)

        return {
            "online": True,
            "hashrate_ths": ths,
            "asic_temp": pick_first(js, ["temp", "asicTemp", "asic_temp"], None),
            "vr_temp": pick_first(js, ["vrTemp", "vr_temp", "vr"], None),
            "shares_accepted": shares_accepted,
            "shares_rejected": shares_rejected,
            "session_best": session_best,
            "best_overall": best_overall,
            "uptime_seconds": uptime_seconds,
            "blocks_found": blocks_found,
            "fan_speed": fan_speed,
        }
    except Exception:
        return {"online": False}


def miner_loop():
    global miners_state, last_any_block_ts, last_block_popup
    while True:
        new_state = {}
        now_unix = int(time.time())

        for name, cfg in MINERS.items():
            ip = cfg["ip"]
            label = cfg.get("label", name)
            model = cfg.get("model")
            data = poll_miner_api(ip)

            if data.get("online"):
                with _last_seen_lock:
                    last_seen_ts[label] = now_unix

            # Block logic: reset-safe tracking of reported "blocks_found"
            with _blocks_lock:
                blocks = int(block_counts.get(label, 0))

                reported = data.get("blocks_found")
                try:
                    reported_int = int(reported) if reported is not None else None
                except Exception:
                    reported_int = None

                if reported_int is not None:
                    prev_rep = reported_last.get(label)

                    if prev_rep is None:
                        reported_last[label] = reported_int
                        _save_blocks()
                    else:
                        if reported_int < prev_rep:
                            # miner reset: update baseline only
                            reported_last[label] = reported_int
                            _save_blocks()
                        elif reported_int > prev_rep:
                            delta = reported_int - prev_rep
                            reported_last[label] = reported_int

                            blocks += delta
                            block_counts[label] = blocks
                            last_block_ts[label] = now_unix
                            last_any_block_ts = now_unix
                            _save_blocks()

                            ts_hms = now_hms()
                            for _ in range(delta):
                                send_discord_block_found(label, ts_hms, is_test=False)

                            last_block_popup = {"miner": label, "ts_unix": now_unix, "is_test": False}

            with _last_seen_lock:
                last_seen = last_seen_ts.get(label)

            # Weekly best diff tracking (reset-safe & persisted)
            sb_raw = diff_to_number(data.get("session_best"))
            with _weekly_lock:
                current_val = weekly_current.get(label)
                if sb_raw is not None:
                    if current_val is None or sb_raw > current_val:
                        weekly_current[label] = sb_raw
                        _save_weekly_current()
                week_val = weekly_current.get(label, sb_raw)

            new_state[name] = {
                "name": label,
                "ip": ip,
                "model": model,
                "online": data.get("online", False),
                "hashrate_ths": data.get("hashrate_ths", None),
                "asic_temp": data.get("asic_temp", None),
                "vr_temp": data.get("vr_temp", None),
                "shares_accepted": data.get("shares_accepted", None),
                "shares_rejected": data.get("shares_rejected", None),
                "session_best": data.get("session_best", None),  # raw from miner
                "weekly_best": week_val,                         # our reset-safe weekly best
                "best_overall": data.get("best_overall", None),
                "uptime_seconds": data.get("uptime_seconds", None),
                "blocks": int(block_counts.get(label, 0)),
                "last_seen_unix": last_seen,
                "fan_speed": data.get("fan_speed", None),
            }

        miners_state = new_state
        time.sleep(max(1, int(REFRESH_SECONDS)))


# =========================
# MINER OF THE WEEK
# =========================

def _clamp(x, lo, hi):
    try:
        v = float(x)
    except Exception:
        return lo
    return max(lo, min(hi, v))


def _ratio_pct(actual, baseline):
    try:
        a = float(actual)
        b = float(baseline)
        if b <= 0:
            return None
        return (a / b) * 100.0
    except Exception:
        return None


def _shares_per_hour(shares, uptime_seconds):
    try:
        s = float(shares)
        up = float(uptime_seconds)
        if up <= 0:
            return None
        return s / (up / 3600.0)
    except Exception:
        return None


def compute_motw_for_last_week(snapshot_miners):
    if not snapshot_miners:
        return None, None, None

    with _blocks_lock:
        wsc = dict(week_start_counts)

    week_seconds = 7 * 24 * 3600

    rows = []
    max_blocks_week = 0
    max_weekly_diff = 0.0

    for m in snapshot_miners:
        name = m.get("name")
        model = m.get("model") or ""
        model_cfg = MODEL_BASELINES.get(model, {})

        blocks_total = int(m.get("blocks", 0) or 0)
        start_blocks = int(wsc.get(name, 0) or 0)
        blocks_week = max(0, blocks_total - start_blocks)

        weekly_best_raw = diff_to_number(m.get("session_best"))  # raw from miner for that past week
        weekly_best_raw = float(weekly_best_raw) if weekly_best_raw is not None else 0.0

        hr = m.get("hashrate_ths")
        hr = float(hr) if hr is not None else None

        shares = m.get("shares_accepted")
        try:
            shares = float(shares) if shares is not None else None
        except Exception:
            shares = None

        uptime = m.get("uptime_seconds")
        try:
            uptime = float(uptime) if uptime is not None else 0.0
        except Exception:
            uptime = 0.0

        hr_pct = _ratio_pct(hr, model_cfg.get("baseline_ths")) if hr is not None else None
        sph = _shares_per_hour(shares, uptime) if shares is not None and uptime else None
        sph_pct = _ratio_pct(sph, model_cfg.get("baseline_shares_per_hour")) if sph is not None else None

        uptime_frac = _clamp(uptime / float(week_seconds), 0.0, 1.0)

        max_blocks_week = max(max_blocks_week, blocks_week)
        max_weekly_diff = max(max_weekly_diff, weekly_best_raw)

        rows.append({
            "name": name,
            "blocks_week": blocks_week,
            "weekly_best": weekly_best_raw,
            "hr_pct": hr_pct,
            "sph_pct": sph_pct,
            "uptime_frac": uptime_frac,
        })

    # Weights
    W_BLOCKS = 0.40
    W_DIFF   = 0.25
    W_HR     = 0.15
    W_SPH    = 0.10
    W_UP     = 0.10

    best_name = None
    best_score = -1.0
    best_row = None

    for r in rows:
        blocks_score = (r["blocks_week"] / max_blocks_week) if max_blocks_week > 0 else 0.0
        diff_score = (r["weekly_best"] / max_weekly_diff) if max_weekly_diff > 0 else 0.0

        hr_score = 0.0
        if r["hr_pct"] is not None:
            hr_score = _clamp(r["hr_pct"] / 100.0, 0.0, 1.50) / 1.50

        sph_score = 0.0
        if r["sph_pct"] is not None:
            sph_score = _clamp(r["sph_pct"] / 100.0, 0.0, 1.50) / 1.50

        up_score = r["uptime_frac"]

        score = (
            W_BLOCKS * blocks_score +
            W_DIFF   * diff_score +
            W_HR     * hr_score +
            W_SPH    * sph_score +
            W_UP     * up_score
        )

        if score > best_score:
            best_score = score
            best_name = r["name"]
            best_row = r

    if best_name is None or best_row is None:
        return None, None, None

    score_int = int(round(_clamp(best_score, 0.0, 1.0) * 100.0))

    hrpct = best_row["hr_pct"]
    shpct = best_row["sph_pct"]
    hrpct_str = f"{int(round(hrpct))}%" if hrpct is not None else "—"
    shpct_str = f"{int(round(shpct))}%" if shpct is not None else "—"
    up_str = f"{int(round(best_row['uptime_frac'] * 100.0))}%"

    summary = (
        f"🏆 Miner of the Week — {best_name} — Score {score_int} — "
        f"Blocks {best_row['blocks_week']} | "
        f"Best {fmt_diff_si_adaptive(best_row['weekly_best'])} | "
        f"HR {hrpct_str} | Shares/hr {shpct_str} | Uptime {up_str}"
    )

    return best_name, score_int, summary


def weekly_rollover_loop():
    global weekly_best, motw, week_start_counts, week_start_unix, weekly_current

    last_run_week = None
    while True:
        now = datetime.now()
        iso_year, iso_week, _ = now.isocalendar()
        weekday = now.weekday()  # Monday=0, Sunday=6

        if weekday == 6 and now.hour == 23 and now.minute >= 59:
            if last_run_week != (iso_year, iso_week):
                snapshot = list(miners_state.values())

                # Weekly best diff (for previous week banner)
                best_val = None
                best_name = None
                for m in snapshot:
                    val = diff_to_number(m.get("session_best"))
                    if val is None:
                        continue
                    if best_val is None or val > best_val:
                        best_val = val
                        best_name = m.get("name")

                if best_val is not None and best_name:
                    with _weekly_lock:
                        weekly_best = {
                            "prev_name": best_name,
                            "prev_value": float(best_val),
                            "prev_str": fmt_diff_si_adaptive(best_val),
                        }
                        _save_weekly_best()

                # Miner of the Week
                winner, score_int, summary = compute_motw_for_last_week(snapshot)
                if winner and summary:
                    with _motw_lock:
                        motw = {
                            "prev_name": winner,
                            "prev_score": score_int,
                            "prev_str": summary,
                            "prev_week_iso": f"{iso_year}-W{iso_week:02d}",
                        }
                        _save_motw()

                # Reset weekly baseline counts + weekly best diffs
                with _blocks_lock:
                    week_start_unix = int(time.time())
                    week_start_counts = dict(block_counts)
                    _save_blocks()
                with _weekly_lock:
                    weekly_current = {}
                    _save_weekly_current()

                # Restart miners
                for _, cfg in MINERS.items():
                    ip = cfg["ip"]
                    try:
                        requests.post(f"http://{ip}/api/system/restart", timeout=2)
                    except Exception:
                        pass

                last_run_week = (iso_year, iso_week)

        time.sleep(30)


# =========================
# COINS
# =========================

def refresh_coin_logos():
    global logos_last_ok_unix, logos_last_err

    mapping = {
        "BTC": "bitcoin",
        "BCH": "bitcoin-cash",
        "FB":  "fractal-bitcoin",
        "DGB": "digibyte",
    }

    ids = ",".join(mapping.get(sym) for sym in COIN_ORDER if sym in mapping)

    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "gbp", "ids": ids, "sparkline": "false"},
            timeout=10,
        )
        r.raise_for_status()
        arr = r.json()

        id_to_image = {}
        if isinstance(arr, list):
            for item in arr:
                if isinstance(item, dict):
                    cid = item.get("id")
                    img = item.get("image")
                    if cid and img:
                        id_to_image[str(cid)] = str(img)

        with _logo_lock:
            for sym in COIN_ORDER:
                cid = mapping.get(sym)
                if cid and id_to_image.get(cid):
                    coin_logos[sym] = id_to_image[cid]
            logos_last_ok_unix = int(time.time())
            logos_last_err = None
    except Exception as e:
        with _logo_lock:
            logos_last_err = str(e)[:200]


def _wtm_coin_json(coin_id: int):
    r = requests.get(f"https://whattomine.com/coins/{coin_id}.json", timeout=8)
    r.raise_for_status()
    return r.json()


def fetch_coin_stats_gbp():
    out = {sym: {"price_gbp": None, "diff": None} for sym in COIN_ORDER}

    # Prices (CoinGecko)
    try:
        cg_ids = []
        if "BTC" in COIN_ORDER: cg_ids.append("bitcoin")
        if "BCH" in COIN_ORDER: cg_ids.append("bitcoin-cash")
        if "FB"  in COIN_ORDER: cg_ids.append("fractal-bitcoin")
        if "DGB" in COIN_ORDER: cg_ids.append("digibyte")

        cg = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ",".join(cg_ids), "vs_currencies": "gbp"},
            timeout=8,
        ).json()

        if "BTC" in COIN_ORDER: out["BTC"]["price_gbp"] = pick_first(cg.get("bitcoin", {}), ["gbp"], None)
        if "BCH" in COIN_ORDER: out["BCH"]["price_gbp"] = pick_first(cg.get("bitcoin-cash", {}), ["gbp"], None)
        if "FB"  in COIN_ORDER: out["FB"]["price_gbp"]  = pick_first(cg.get("fractal-bitcoin", {}), ["gbp"], None)
        if "DGB" in COIN_ORDER: out["DGB"]["price_gbp"] = pick_first(cg.get("digibyte", {}), ["gbp"], None)
    except Exception:
        pass

    # Difficulties (WhatToMine)
    try:
        if "BTC" in COIN_ORDER: out["BTC"]["diff"] = pick_first(_wtm_coin_json(1), ["difficulty"], None)
    except Exception:
        pass
    try:
        if "BCH" in COIN_ORDER: out["BCH"]["diff"] = pick_first(_wtm_coin_json(193), ["difficulty"], None)
    except Exception:
        pass
    try:
        if "FB" in COIN_ORDER: out["FB"]["diff"] = pick_first(_wtm_coin_json(431), ["difficulty"], None)
    except Exception:
        pass
    try:
        if "DGB" in COIN_ORDER: out["DGB"]["diff"] = pick_first(_wtm_coin_json(113), ["difficulty"], None)
    except Exception:
        pass

    return out


def coin_loop():
    global coin_state, coin_last_ok_unix, coin_last_err

    try:
        refresh_coin_logos()
    except Exception:
        pass

    last_logo_refresh = 0
    LOGO_REFRESH_SECONDS = 10 * 60

    while True:
        now_unix = int(time.time())
        with _logo_lock:
            missing_any = any(not coin_logos.get(sym) for sym in COIN_ORDER)

        if missing_any or (now_unix - last_logo_refresh >= LOGO_REFRESH_SECONDS):
            try:
                refresh_coin_logos()
            except Exception:
                pass
            last_logo_refresh = now_unix

        try:
            cs = fetch_coin_stats_gbp()
            with _coin_lock:
                coin_state = cs
                coin_last_ok_unix = int(time.time())
                coin_last_err = None
        except Exception as e:
            with _coin_lock:
                coin_last_err = str(e)[:200]

        time.sleep(max(5, int(COIN_REFRESH_SECONDS)))


# =========================
# API
# =========================

@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/data")
def data():
    with _coin_lock:
        coins_out = {}
        for sym in COIN_ORDER:
            c = coin_state.get(sym, {})
            coins_out[sym] = {
                "price_gbp": fmt_gbp(c.get("price_gbp")) if c.get("price_gbp") is not None else "-",
                "diff": fmt_diff_si(c.get("diff")) if c.get("diff") is not None else "-",
                "price_gbp_raw": c.get("price_gbp"),
                "diff_raw": c.get("diff"),
            }
        coin_ok_unix = coin_last_ok_unix
        coin_err = coin_last_err

    with _logo_lock:
        logos_out = dict(coin_logos)
        logos_ok_unix = logos_last_ok_unix
        logos_err = logos_last_err

    with _blocks_lock:
        global_last_ts = last_any_block_ts

    with _weekly_lock:
        prev_name = weekly_best.get("prev_name")
        prev_str = weekly_best.get("prev_str")

    with _motw_lock:
        motw_name = motw.get("prev_name")
        motw_str = motw.get("prev_str")

    out = {
        "miners": [],
        "refresh_seconds": REFRESH_SECONDS,
        "updated": now_iso(),
        "coins": coins_out,
        "coin_last_ok_unix": coin_ok_unix,
        "coin_last_err": coin_err,
        "coin_logos": logos_out,
        "coin_logos_last_ok_unix": logos_ok_unix,
        "coin_logos_last_err": logos_err,
        "last_any_block_ts": global_last_ts,
        "stale_yellow_seconds": STALE_YELLOW_SECONDS,
        "stale_red_seconds": STALE_RED_SECONDS,
        "last_block_popup": last_block_popup,
        "prev_week_best_name": prev_name,
        "prev_week_best_str": prev_str,
        "motw_name": motw_name,
        "motw_str": motw_str,
        "miner_page_seconds": MINER_PAGE_SECONDS,
        "miners_per_page": MINERS_PER_PAGE,
    }

    for _, m in miners_state.items():
        weekly_raw = diff_to_number(m.get("weekly_best"))
        if weekly_raw is None:
            weekly_raw = diff_to_number(m.get("session_best"))
        bo_raw_num = diff_to_number(m.get("best_overall"))

        out["miners"].append(
            {
                "name": m["name"],
                "ip": m["ip"],
                "model": m.get("model"),
                "online": m["online"],
                "uptime_seconds": m.get("uptime_seconds"),
                "hashrate": fmt_hashrate_ths(m.get("hashrate_ths")) if m.get("hashrate_ths") is not None else "-",
                "temp": fmt_temp_pair(m.get("asic_temp"), m.get("vr_temp")),
                "asic_temp_raw": m.get("asic_temp"),
                "vr_temp_raw": m.get("vr_temp"),
                "fan_speed": m.get("fan_speed"),
                "shares_accepted": fmt_int_short(m.get("shares_accepted")) if m.get("shares_accepted") is not None else "-",
                "shares_accepted_raw": m.get("shares_accepted"),
                "shares_rejected": fmt_int_short(m.get("shares_rejected")) if m.get("shares_rejected") is not None else "0",
                "shares_rejected_raw": m.get("shares_rejected"),
                "session_best": fmt_diff_si_adaptive(weekly_raw) if weekly_raw is not None else "-",
                "session_best_raw": weekly_raw,
                "best_overall": fmt_diff_si_adaptive(m.get("best_overall")) if m.get("best_overall") is not None else "-",
                "best_overall_raw": bo_raw_num,
                "blocks": int(m.get("blocks", 0)),
                "last_seen_unix": m.get("last_seen_unix"),
            }
        )

    return jsonify(out)


# =========================
# SINGLE-FILE UI
# =========================

@app.get("/")
def root():
    html_template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Mining Stats Dashboard</title>
<style>
:root {
  --bg: #0b1020;
  --panel: rgba(255,255,255,0.06);
  --text: rgba(255,255,255,0.90);
  --muted: rgba(255,255,255,0.55);
  --green: #27f5a7;
  --red: #ff3b3b;
  --yellow: #ffd84a;
  --orange: #ff9f2a;
  --gold: #ffcf33;
}

html, body {
  height: 100%;
  margin: 0;
  background: radial-gradient(1200px 600px at 20% 0%, #121a33, var(--bg));
  font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
  color: var(--text);
  overflow: hidden;
  font-variant-numeric: tabular-nums;
}

.wrap { padding: 10px 12px; }

.top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 6px;
}

.title {
  font-size: 28px;
  font-weight: 900;
  line-height: 1;
  letter-spacing: 0.01em;
}

.updated {
  margin-top: 3px;
  font-size: 12px;
  color: var(--muted);
}

.live {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 800;
  color: rgba(120,255,220,0.95);
  font-size: 15px;
  margin-top: 3px;
}

.dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: rgba(120,255,220,0.95);
  box-shadow: 0 0 10px rgba(120,255,220,0.45);
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%   { transform: scale(1);   opacity: 0.55; }
  50%  { transform: scale(1.55); opacity: 1;    }
  100% { transform: scale(1);   opacity: 0.55; }
}

.tickerWrap {
  margin-top: 6px;
  margin-bottom: 8px;
  overflow: hidden;
  border-radius: 10px;
  background: rgba(255,255,255,0.035);
  border: 1px solid rgba(255,255,255,0.055);
}

.tickerTrack {
  display: inline-flex;
  white-space: nowrap;
  gap: 22px;
  padding: 5px 10px;
  will-change: transform;
  animation: tickerMove 22s linear infinite;
}

@keyframes tickerMove {
  from { transform: translateX(0); }
  to   { transform: translateX(-50%); }
}

.tickerItem {
  font-weight: 900;
  letter-spacing: 0.01em;
  font-size: 13px;
  color: rgba(255,255,255,0.86);
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.tickerItem .mut {
  color: rgba(255,255,255,0.55);
  font-weight: 800;
  margin-left: 6px;
}

.tickerWeekly {
  margin-left: 70px;
  margin-right: 70px;
}

.coinLogo {
  width: 16px;
  height: 16px;
  border-radius: 999px;
  display: inline-block;
  object-fit: contain;
  background: rgba(255,255,255,0.10);
  box-shadow: 0 0 10px rgba(0,0,0,0.15);
  flex: 0 0 auto;
}

.ind {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  font-weight: 1000;
  font-size: 12px;
  line-height: 1;
}
.indUp { color: var(--green); }
.indDown { color: var(--red); }
.indFlat { color: rgba(255,255,255,0.85); }

.miners {
  display: grid;
  grid-template-columns: 2.2fr 1.25fr 1.1fr 1fr 1fr;
  grid-auto-rows: minmax(0, auto);
  gap: 7px;
  width: 100%;
  text-align: left;
}

.row { display: contents; }

.card {
  background: var(--panel);
  border-radius: 12px;
  padding: 9px 10px;
  box-sizing: border-box;
  min-height: 58px;
  text-align: left;
}

.label {
  font-size: 10px;
  font-weight: 900;
  letter-spacing: 0.16em;
  color: rgba(255,255,255,0.55);
  width: 100%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.minerLine {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 5px;
  min-width: 0;
  width: 100%;
}

.rankIcon {
  font-size: 16px;
  filter: drop-shadow(0 0 5px rgba(255,216,74,0.25));
  width: 1.4em;
  display: flex;
  align-items: center;
  justify-content: center;
}

.minerName {
  font-size: 19px;
  font-weight: 900;
  line-height: 1.05;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

/* Miner of the Week highlight */
.motwName {
  color: var(--gold);
  animation: goldGlow 1.35s ease-in-out infinite;
  text-shadow: 0 0 10px rgba(255,207,51,0.25);
}

.blocks {
  margin-left: 6px;
  font-size: 13px;
  font-weight: 900;
  color: rgba(255,255,255,0.72);
  flex: 0 0 auto;
}
.blocksLeader { color: var(--gold); }

.sub {
  margin-top: 3px;
  font-size: 12px;
  color: rgba(255,255,255,0.70);
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.statusDot { width: 8px; height: 8px; border-radius: 999px; }
.online { background: rgba(39,245,167,1); box-shadow: 0 0 8px rgba(39,245,167,0.45); }
.offline { background: rgba(255,255,255,0.18); }
.staleYellow { background: rgba(255,216,74,1); box-shadow: 0 0 8px rgba(255,216,74,0.35); }
.staleRed { background: rgba(255,59,59,1); box-shadow: 0 0 8px rgba(255,59,59,0.35); }

.valueBig {
  font-size: 19px;
  font-weight: 900;
  margin-top: 6px;
  line-height: 1.05;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: rgba(255,255,255,0.92);
  width: 100%;
}

.green { color: var(--green); }
.orange { color: var(--orange); }
.red { color: var(--red); }

@keyframes goldGlow {
  0% {
    text-shadow: 0 0 6px rgba(255,207,51,0.25),
                 0 0 14px rgba(255,207,51,0.12);
    filter: brightness(1.00);
  }
  50% {
    text-shadow: 0 0 10px rgba(255,207,51,0.55),
                 0 0 22px rgba(255,207,51,0.28);
    filter: brightness(1.10);
  }
  100% {
    text-shadow: 0 0 6px rgba(255,207,51,0.25),
                 0 0 14px rgba(255,207,51,0.12);
    filter: brightness(1.00);
  }
}

.bestTop {
  color: var(--gold);
  animation: goldGlow 1.35s ease-in-out infinite;
  text-shadow: 0 0 10px rgba(255,207,51,0.25);
}

.titleStats { color: var(--yellow); }

.slash { color: rgba(255,255,255,0.92) !important; display: inline-block; padding: 0 2px; }

.blockPopup {
  position: fixed;
  inset: 0;
  z-index: 999999;
  background: rgba(0,0,0,0.65);
  display: none;
  align-items: center;
  justify-content: center;
  padding: 16px;
  box-sizing: border-box;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
}

.blockPopupInner {
  background: radial-gradient(circle at top, #222a4a, #050814);
  border-radius: 22px;
  padding: 22px 18px 18px;
  max-width: 380px;
  width: 100%;
  text-align: center;
  box-shadow: 0 18px 40px rgba(0,0,0,0.65);
  border: 1px solid rgba(255,255,255,0.08);
}

.blockPopupEmoji { font-size: 40px; margin-bottom: 8px; }
.blockPopupTitle { font-size: 22px; font-weight: 900; margin-bottom: 4px; letter-spacing: 0.05em; text-transform: uppercase; }
.blockPopupMiner { font-size: 18px; font-weight: 800; color: var(--gold); margin-bottom: 10px; word-break: break-word; }
.blockPopupHint { font-size: 12px; color: var(--muted); }

.slideRow { animation: swapSlide 0.6s ease-out; }
@keyframes swapSlide { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: translateY(0); } }

.footerLine {
  margin-top: 6px;
  font-size: 12px;
  color: rgba(255,255,255,0.55);
  display: flex;
  justify-content: space-between;
  gap: 10px;
  user-select: none;
}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <div class="title">Mining <span class="titleStats">Stats</span> Dashboard</div>
      <div class="updated" id="updated">Dash Updated: - • Coins Updated: - • Time Since Last Block: -</div>
    </div>
    <div class="live"><span class="dot"></span> LIVE</div>
  </div>

  <div class="tickerWrap">
    <div class="tickerTrack" id="tickerTrack"></div>
  </div>

  <div class="miners" id="miners"></div>
  <div class="footerLine" id="footerLine"></div>
</div>

<div class="blockPopup" id="blockPopup"></div>

<script>
const REFRESH_MS = __REFRESH_SECONDS__ * 1000;
const TEMP_ORANGE_AT = __TEMP_ORANGE_AT__;
const TEMP_RED_AT = __TEMP_RED_AT__;
const STALE_YELLOW_SECONDS = __STALE_YELLOW__;
const STALE_RED_SECONDS = __STALE_RED__;

const COIN_ORDER = __COIN_ORDER__;
const MINER_PAGE_SECONDS = __MINER_PAGE_SECONDS__;
const MINERS_PER_PAGE = __MINERS_PER_PAGE__;

let pendingCoins = null;
let currentCoins = null;

let LIVE_LOGOS = {};
for (const s of COIN_ORDER) LIVE_LOGOS[s] = null;

const prevCoins = {};
for (const s of COIN_ORDER) prevCoins[s] = { price: null, diff: null };

const FALLBACK_LOGO = __FALLBACK_LOGO__;

const prevBlocks = {};

let BEST_WEEK_NAME = null;
let BEST_WEEK_STR = null;
let MOTW_NAME = null;
let MOTW_STR = null;
let TICKER_STATS = null;

// Paging state
let currentPage = 0;
let pageMiners = [];
let lastSortedFingerprint = null;

function showBlockPopup(minerName) {
  const el = document.getElementById('blockPopup');
  if (!el) return;
  const safeName = minerName || 'Unknown miner';
  el.innerHTML =
    '<div class="blockPopupInner">' +
      '<div class="blockPopupEmoji">🎉🥳</div>' +
      '<div class="blockPopupTitle">Block Found!</div>' +
      '<div class="blockPopupMiner">' + safeName + '</div>' +
      '<div class="blockPopupHint">Tap anywhere to dismiss</div>' +
    '</div>';
  el.style.display = 'flex';
}

function hideBlockPopup() {
  const el = document.getElementById('blockPopup');
  if (!el) return;
  el.style.display = 'none';
  el.innerHTML = '';
}

(function() {
  const el = document.getElementById('blockPopup');
  if (!el) return;
  el.addEventListener('click', hideBlockPopup, { passive: true });
  el.addEventListener('touchstart', hideBlockPopup, { passive: true });
})();

function indicator(newVal, oldVal) {
  const n = Number(newVal);
  const o = Number(oldVal);
  if (!Number.isFinite(n) || !Number.isFinite(o)) return { ch: '—', cls: 'indFlat' };
  if (n > o) return { ch: '▲', cls: 'indUp' };
  if (n < o) return { ch: '▼', cls: 'indDown' };
  return { ch: '—', cls: 'indFlat' };
}

function uptimeDaysHoursText(uptimeSeconds) {
  const s = Number(uptimeSeconds);
  if (!Number.isFinite(s) || s < 0) return '-';
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  return days + 'd ' + hours + 'h';
}

function sinceLastBlockText(lastTs) {
  const t = Number(lastTs);
  if (!Number.isFinite(t) || t <= 0) return 'Never';
  const now = Math.floor(Date.now() / 1000);
  const diff = Math.max(0, now - t);
  const days = Math.floor(diff / 86400);
  const hours = Math.floor((diff % 86400) / 3600);
  const mins = Math.floor((diff % 3600) / 60);
  return days + 'd ' + hours + 'h ' + mins + 'm';
}

function secondsAgoText(tsUnix) {
  const t = Number(tsUnix);
  if (!Number.isFinite(t) || t <= 0) return '-';
  const now = Math.floor(Date.now() / 1000);
  return Math.max(0, now - t) + 's ago';
}

function pickTempClass(m) {
  const a = parseFloat(m.asic_temp_raw);
  const v = parseFloat(m.vr_temp_raw);
  const vals = [];
  if (Number.isFinite(a)) vals.push(a);
  if (Number.isFinite(v)) vals.push(v);
  if (vals.length === 0) return 'green';
  const t = Math.max.apply(null, vals);
  if (t >= TEMP_RED_AT) return 'red';
  if (t >= TEMP_ORANGE_AT) return 'orange';
  return 'green';
}

function staleStatus(m) {
  if (!m.online) return { dotClass: 'offline', extraText: 'offline' };
  const last = Number(m.last_seen_unix);
  if (!Number.isFinite(last) || last <= 0) return { dotClass: 'staleRed', extraText: 'stale' };
  const now = Math.floor(Date.now() / 1000);
  const age = Math.max(0, now - last);
  if (age >= STALE_RED_SECONDS) return { dotClass: 'staleRed', extraText: 'stale ' + age + 's' };
  if (age >= STALE_YELLOW_SECONDS) return { dotClass: 'staleYellow', extraText: 'stale ' + age + 's' };
  return { dotClass: 'online', extraText: null };
}

function tempHTML(m) {
  const tempClass = pickTempClass(m);
  const aNum = Number(m.asic_temp_raw);
  const vNum = Number(m.vr_temp_raw);
  const left  = Number.isFinite(aNum) ? Math.round(aNum) + '°' : '-';
  const right = Number.isFinite(vNum) ? Math.round(vNum) + '°' : '-';

  let base = '<span class="' + tempClass + '">' + left + '</span>' +
             '<span class="slash">/</span>' +
             '<span class="' + tempClass + '">' + right + '</span>';

  const fNum = Number(m.fan_speed);
  if (Number.isFinite(fNum)) {
    base += '<span class="slash">/</span>' +
            '<span>' + Math.round(fNum) + '%</span>';
  }
  return base;
}

function sessionBestHTML(m, isTopWeekly, isTopBest) {
  const wVal = m.session_best || '-';
  const bVal = m.best_overall || '-';
  const wCls = isTopWeekly ? 'bestTop' : '';
  const bCls = isTopBest ? 'bestTop' : '';
  return '<span class="' + wCls + '">' + wVal + '</span>' +
         ' / ' +
         '<span class="' + bCls + '">' + bVal + '</span>';
}

function padBlocks(val) {
  const n = Number(val);
  const v = (Number.isFinite(n) && n >= 0) ? Math.floor(n) : 0;
  return String(v).padStart(3, '0');
}

function rankIconForIndex(idx) {
  if (idx === 0) return "🥇";
  if (idx === 1) return "🥈";
  if (idx === 2) return "🥉";
  return "👷";
}

function sharesRejectedText(m) {
  const rejStr = m.shares_rejected || '0';

  const accRaw = Number(m.shares_accepted_raw);
  const rejRaw = Number(m.shares_rejected_raw);
  let pctStr = '-';

  if (Number.isFinite(accRaw) && Number.isFinite(rejRaw)) {
    const total = accRaw + rejRaw;
    if (total > 0) {
      const pct = (rejRaw / total) * 100;
      if (pct < 10) {
        pctStr = pct.toFixed(2) + '%';
      } else {
        pctStr = pct.toFixed(1) + '%';
      }
    }
  }

  return rejStr + ' / ' + pctStr;
}

function minerRowHTML(m, globalIdx, topWeeklyName, topBestName, isSliding, blockLeaderName) {
  const isTopWeekly = (m.name === topWeeklyName);
  const isTopBest = (m.name === topBestName);

  const icon = rankIconForIndex(globalIdx);
  const isBlockLeader = (m.name === blockLeaderName);
  const blockIcon = isBlockLeader ? '🟨' : '🧱';
  const blocksStr = padBlocks(m.blocks);

  const st = staleStatus(m);
  const uptimeText = m.online ? uptimeDaysHoursText(m.uptime_seconds) : 'offline';
  const thirdBit = st.extraText ? st.extraText : uptimeText;

  const cardClass = isSliding ? 'card slideRow' : 'card';
  const motwClass = (MOTW_NAME && m.name === MOTW_NAME) ? ' motwName' : '';

  let html = '';
  html += '<div class="row">';
  html += '<div class="' + cardClass + '">';
  html += '<div class="label">MINER</div>';
  html += '<div class="minerLine">';
  html += '<div class="rankIcon">' + icon + '</div>';
  html += '<span class="blocks' + (isBlockLeader ? ' blocksLeader' : '') + '">';
  html += blockIcon + ' ' + blocksStr + '</span>';
  html += '<div class="minerName' + motwClass + '" title="' + m.name + '">';
  html += m.name;
  html += '</div></div>';
  html += '<div class="sub">';
  html += '<span>' + m.ip + '</span>';
  html += '<span class="statusDot ' + st.dotClass + '"></span>';
  html += '<span>' + thirdBit + '</span>';
  html += '</div>';
  html += '</div>';

  html += '<div class="' + cardClass + '"><div class="label">HASHRATE</div><div class="valueBig">' + (m.hashrate || '-') + '</div></div>';
  html += '<div class="' + cardClass + '"><div class="label">ASIC / VR / FAN</div><div class="valueBig">' + tempHTML(m) + '</div></div>';

  const sharesText = sharesRejectedText(m);
  html += '<div class="' + cardClass + '"><div class="label">SHARES REJECTED</div><div class="valueBig">' + sharesText + '</div></div>';

  html += '<div class="' + cardClass + '"><div class="label">WEEKLY / BEST</div><div class="valueBig">' + sessionBestHTML(m, isTopWeekly, isTopBest) + '</div></div>';
  html += '</div>';
  return html;
}

function coinLogoHTML(sym) {
  const primary = LIVE_LOGOS && LIVE_LOGOS[sym] ? LIVE_LOGOS[sym] : '';
  const fallbackList = (FALLBACK_LOGO[sym] || []);
  const urls = [];
  if (primary) urls.push(primary);
  for (let i = 0; i < fallbackList.length; i++) urls.push(fallbackList[i]);
  if (!urls.length) return '';

  const encoded = encodeURIComponent(JSON.stringify(urls));
  const first = urls[0];

  return '<img class="coinLogo" src="' + first + '" alt="' + sym + '" loading="lazy"' +
    ' data-urls="' + encoded + '" data-idx="0"' +
    ' onerror="' +
      "try{" +
        "const urls=JSON.parse(decodeURIComponent(this.dataset.urls||'[]'));" +
        "let i=parseInt(this.dataset.idx||'0',10);" +
        "i++;" +
        "if(i<urls.length){this.dataset.idx=String(i);this.src=urls[i];}" +
        "else{this.style.display='none';}" +
      "}catch(e){this.style.display='none';}" +
    '"' +
  '>';
}

function weeklyBestTickerHTML() {
  if (!BEST_WEEK_NAME || !BEST_WEEK_STR) return null;
  return '⛏️ Best Difficulty for Previous Week - ' + BEST_WEEK_STR + ' by ' + BEST_WEEK_NAME + ' ⛏️';
}

function motwTickerHTML() {
  if (!MOTW_STR) return null;
  return MOTW_STR;
}

function computeTickerStats(miners) {
  if (!miners || !miners.length) { TICKER_STATS = null; return; }

  let totalMiners = miners.length;
  let activeMiners = 0;
  let hashSum = 0, hashCount = 0;
  let tempSum = 0, tempCount = 0;
  let maxTemp = null;
  let totalBlocks = 0;
  let blocksSeen = false;

  for (const m of miners) {
    if (m.online) activeMiners++;

    const hrStr = (m.hashrate || '').split(' ')[0];
    const hv = parseFloat(hrStr);
    if (Number.isFinite(hv)) { hashSum += hv; hashCount++; }

    const t1 = Number(m.asic_temp_raw);
    const t2 = Number(m.vr_temp_raw);
    for (const t of [t1, t2]) {
      if (Number.isFinite(t)) {
        tempSum += t; tempCount++;
        if (maxTemp === null || t > maxTemp) maxTemp = t;
      }
    }

    const b = Number(m.blocks);
    if (Number.isFinite(b) && b >= 0) { totalBlocks += b; blocksSeen = true; }
  }

  TICKER_STATS = {
    activeMiners: activeMiners,
    totalMiners: totalMiners,
    totalHash: hashCount ? hashSum : null,
    avgTemp: tempCount ? (tempSum / tempCount) : null,
    maxTemp: maxTemp,
    totalBlocks: blocksSeen ? totalBlocks : null,
  };
}

function formatTotalHash(th) {
  const v = Number(th);
  if (!Number.isFinite(v)) return '-';
  if (v >= 100) return v.toFixed(0) + ' TH/s';
  if (v >= 10) return v.toFixed(1) + ' TH/s';
  return v.toFixed(2) + ' TH/s';
}

function statsTickerItems() {
  if (!TICKER_STATS) return [];
  const s = TICKER_STATS;
  const items = [];

  if (s.totalBlocks != null && Number.isFinite(Number(s.totalBlocks))) {
    items.push({ html: '🧱 Total Blocks Found - ' + s.totalBlocks + ' 🧱', cls: ' tickerWeekly' });
  }
  if (s.totalMiners > 0) {
    items.push({ html: '👷 Active Miners: ' + s.activeMiners + ' / ' + s.totalMiners + ' 👷', cls: ' tickerWeekly' });
  }
  if (s.totalHash != null && Number.isFinite(Number(s.totalHash))) {
    items.push({ html: '⚡ Total Hashrate: ' + formatTotalHash(s.totalHash) + ' ⚡', cls: ' tickerWeekly' });
  }
  if (s.avgTemp != null && Number.isFinite(Number(s.avgTemp))) {
    const avg = Math.round(Number(s.avgTemp));
    const max = (s.maxTemp != null && Number.isFinite(Number(s.maxTemp))) ? Math.round(Number(s.maxTemp)) : avg;
    items.push({ html: '🌡 Temperatures - Avg ' + avg + ' / Max ' + max + ' 🌡', cls: ' tickerWeekly' });
  }
  return items;
}

function buildTickerHTML(coins) {
  function item(sym) {
    var c = coins[sym] || {};
    var prev = prevCoins[sym];

    var pInd = indicator(c.price_gbp_raw, prev.price);
    var dInd = indicator(c.diff_raw, prev.diff);

    if (c.price_gbp_raw != null) prev.price = c.price_gbp_raw;
    if (c.diff_raw != null) prev.diff = c.diff_raw;

    var html = '';
    html += coinLogoHTML(sym);
    html += sym;
    html += '<span class="mut">Price:</span>';
    html += '<span class="ind ' + pInd.cls + '">' + pInd.ch + '</span>';
    html += '<span>' + (c.price_gbp || '-') + '</span>';
    html += '<span class="mut">Diff:</span>';
    html += '<span class="ind ' + dInd.cls + '">' + dInd.ch + '</span>';
    html += '<span>' + (c.diff || '-') + '</span>';
    return html;
  }

  var items = [];
  for (const sym of COIN_ORDER) items.push({ html: item(sym), cls: "" });

  var motwLine = motwTickerHTML();
  var bestWeekLine = weeklyBestTickerHTML();
  var statsItemsArr = statsTickerItems();

  if (motwLine) items.push({ html: motwLine, cls: " tickerWeekly" });
  if (bestWeekLine) items.push({ html: bestWeekLine, cls: " tickerWeekly" });

  items = items.concat(statsItemsArr);

  var doubled = items.concat(items);
  return doubled.map(function(obj) {
    return '<div class="tickerItem' + obj.cls + '">' + obj.html + '</div>';
  }).join('');
}

function applyCoinsIfReady() {
  if (!pendingCoins) return;
  currentCoins = pendingCoins;
  pendingCoins = null;
  document.getElementById('tickerTrack').innerHTML = buildTickerHTML(currentCoins);
}

async function fetchData() {
  const r = await fetch('/data', {cache:'no-store'});
  return await r.json();
}

document.getElementById('tickerTrack')
  .addEventListener('animationiteration', function() {
    applyCoinsIfReady();
  });

function buildSortedFingerprint(sortedMiners) {
  return sortedMiners.map(m => (m.name + ':' + (m.blocks||0) + ':' + (m.best_overall_raw||0))).join('|');
}

function renderPage(sortedMiners, isSliding) {
  const container = document.getElementById('miners');
  const footer = document.getElementById('footerLine');
  if (!container) return;

  const total = sortedMiners.length;
  if (!total) {
    container.innerHTML = '';
    if (footer) footer.textContent = '';
    return;
  }

  const pages = Math.max(1, Math.ceil(total / MINERS_PER_PAGE));
  currentPage = ((currentPage % pages) + pages) % pages;

  const start = currentPage * MINERS_PER_PAGE;
  const end = Math.min(total, start + MINERS_PER_PAGE);
  const slice = sortedMiners.slice(start, end);

  const topWeeklyName = (function() {
    let bestName = null;
    let bestVal = -Infinity;
    for (const m of sortedMiners) {
      const v = Number(m.session_best_raw);
      if (Number.isFinite(v) && v > bestVal) { bestVal = v; bestName = m.name; }
    }
    return bestName;
  })();

  const topBestName = (function() {
    let bestName = null;
    let bestVal = -Infinity;
    for (const m of sortedMiners) {
      const v = Number(m.best_overall_raw);
      if (Number.isFinite(v) && v > bestVal) { bestVal = v; bestName = m.name; }
    }
    return bestName;
  })();

  let maxBlocks = -Infinity;
  let maxBlocksName = null;
  for (const m of sortedMiners) {
    const b = Number(m.blocks || 0);
    if (Number.isFinite(b) && b > maxBlocks) { maxBlocks = b; maxBlocksName = m.name; }
  }

  let html = '';
  for (let i = 0; i < slice.length; i++) {
    const m = slice[i];
    const globalIdx = start + i;
    html += minerRowHTML(m, globalIdx, topWeeklyName, topBestName, isSliding, maxBlocksName);
  }
  container.innerHTML = html;

  if (footer) {
    footer.textContent =
      'Showing miners ' + (start + 1) + '–' + end + ' of ' + total +
      ' • Rotating every ' + MINER_PAGE_SECONDS + 's';
  }

  for (const m of sortedMiners) {
    const name = m.name;
    const currentBlocks = Number(m.blocks || 0);
    if (Object.prototype.hasOwnProperty.call(prevBlocks, name)) {
      const prevVal = Number(prevBlocks[name] || 0);
      if (currentBlocks > prevVal) showBlockPopup(name);
    }
    prevBlocks[name] = currentBlocks;
  }
}

async function tick() {
  try {
    const d = await fetchData();

    const sinceTxt = sinceLastBlockText(d.last_any_block_ts);
    const coinsAge = secondsAgoText(d.coin_last_ok_unix);

    if (d.coin_logos) LIVE_LOGOS = d.coin_logos || LIVE_LOGOS;

    MOTW_NAME = d.motw_name || null;
    MOTW_STR = d.motw_str || null;

    document.getElementById('updated').textContent =
      'Dash Updated: ' + (d.updated || '-') +
      ' • Coins Updated: ' + coinsAge +
      ' • Time Since Last Block: ' + sinceTxt;

    const miners = (d.miners || []).slice();

    const sorted = miners.sort(function(a, b) {
      const ab = Number(a.blocks || 0);
      const bb = Number(b.blocks || 0);
      if (Number.isFinite(ab) && Number.isFinite(bb) && bb !== ab) return bb - ab;

      const av = Number(a.best_overall_raw);
      const bv = Number(b.best_overall_raw);
      if (Number.isFinite(av) && Number.isFinite(bv) && bv !== av) return bv - av;

      return (a.name || '').localeCompare(b.name || '');
    });

    BEST_WEEK_NAME = d.prev_week_best_name || null;
    BEST_WEEK_STR  = d.prev_week_best_str || null;

    if (!BEST_WEEK_NAME || !BEST_WEEK_STR) {
      let bestWeekName = null, bestWeekStr = null, bestWeekRaw = -Infinity;
      for (const m of sorted) {
        const raw = Number(m.session_best_raw);
        if (Number.isFinite(raw) && raw > bestWeekRaw) {
          bestWeekRaw = raw; bestWeekName = m.name; bestWeekStr = m.session_best || '-';
        }
      }
      BEST_WEEK_NAME = bestWeekName;
      BEST_WEEK_STR = bestWeekStr;
    }

    computeTickerStats(sorted);

    if (d.coins) {
      if (!currentCoins) {
        currentCoins = d.coins;
        document.getElementById('tickerTrack').innerHTML = buildTickerHTML(currentCoins);
      } else {
        pendingCoins = d.coins;
      }
    }

    const fingerprint = buildSortedFingerprint(sorted);
    const changed = (fingerprint !== lastSortedFingerprint);
    lastSortedFingerprint = fingerprint;

    renderPage(sorted, changed);
    pageMiners = sorted;
  } catch (e) {}
}

function rotatePage() {
  if (!pageMiners || !pageMiners.length) return;
  currentPage++;
  renderPage(pageMiners, true);
}

tick();
setInterval(tick, REFRESH_MS);
setInterval(rotatePage, MINER_PAGE_SECONDS * 1000);
</script>
</body>
</html>"""

    fallback_logo = {
        "BTC": ["https://assets.coingecko.com/coins/images/1/large/bitcoin.png"],
        "BCH": ["https://assets.coingecko.com/coins/images/780/large/bitcoin-cash-circle.png"],
        "FB":  ["https://assets.coingecko.com/coins/images/37001/large/fractal-bitcoin.png"],
        "DGB": ["https://assets.coingecko.com/coins/images/63/large/digibyte.png"],
    }

    html = (
        html_template
        .replace("__REFRESH_SECONDS__", str(int(REFRESH_SECONDS)))
        .replace("__TEMP_ORANGE_AT__", str(int(TEMP_ORANGE_AT)))
        .replace("__TEMP_RED_AT__", str(int(TEMP_RED_AT)))
        .replace("__STALE_YELLOW__", str(int(STALE_YELLOW_SECONDS)))
        .replace("__STALE_RED__", str(int(STALE_RED_SECONDS)))
        .replace("__COIN_ORDER__", json.dumps(COIN_ORDER))
        .replace("__FALLBACK_LOGO__", json.dumps(fallback_logo))
        .replace("__MINER_PAGE_SECONDS__", str(int(MINER_PAGE_SECONDS)))
        .replace("__MINERS_PER_PAGE__", str(int(MINERS_PER_PAGE)))
    )
    return Response(html, mimetype="text/html; charset=utf-8")


# =========================
# START
# =========================

if __name__ == "__main__":
    _load_blocks()
    _load_weekly_best()
    _load_motw()
    _load_weekly_current()

    # Make sure weekly baseline is initialised safely
    with _blocks_lock:
        if week_start_unix is None:
            week_start_unix = int(time.time())
        if not isinstance(week_start_counts, dict) or not week_start_counts:
            week_start_counts = dict(block_counts)
        _save_blocks()

    threading.Thread(target=miner_loop, daemon=True).start()
    threading.Thread(target=coin_loop, daemon=True).start()
    threading.Thread(target=weekly_rollover_loop, daemon=True).start()

    # 👇 Keep this exactly like this (matches your service config)
    app.run(host="0.0.0.0", port=8788)
