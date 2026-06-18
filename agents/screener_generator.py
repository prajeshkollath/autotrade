#!/usr/bin/env python3
"""
screener_generator.py — Daily NSE screener: IBD RS + Weinstein Stage + Sector Rotation + Scans.

Data source: daily_ohlcv table in PostgreSQL (populated by agents/dhan_ohlcv_sync.py).
Sector indices still use yfinance (not stored in DB).

HOW TO RUN:
  cd ~/autotrade
  .venv/bin/python3.12 agents/screener_generator.py

Scheduled via systemd timer daily at 18:00 IST (after market close).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import yfinance as yf

sys.path.insert(0, "/home/freed/autotrade")
from shared.db import get_ohlcv_symbols, get_ohlcv_df

IST     = timezone(timedelta(hours=5, minutes=30))
OUT_DIR = Path("/home/freed/autotrade/data/screener")

# Fallback static list if DB is empty
NIFTY50_FALLBACK = [
    "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK",
    "BAJAJ-AUTO","BAJFINANCE","BAJAJFINSV","BEL","BPCL",
    "BHARTIARTL","BRITANNIA","CIPLA","COALINDIA","DRREDDY",
    "EICHERMOT","GRASIM","HCLTECH","HDFCBANK","HDFCLIFE",
    "HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK","INDUSINDBK",
    "INFY","ITC","JSWSTEEL","KOTAKBANK","LT",
    "LTIMINDTECH","M&M","MARUTI","NESTLEIND","NTPC",
    "ONGC","POWERGRID","RELIANCE","SBILIFE","SHRIRAMFIN",
    "SBIN","SUNPHARMA","TCS","TATACONSUM","TATAMOTORS",
    "TATASTEEL","TECHM","TITAN","TRENT","ULTRACEMCO","WIPRO",
]

SECTORS = {
    "ADANIENT":"INFRA","ADANIPORTS":"INFRA","APOLLOHOSP":"PHARMA","ASIANPAINT":"CONSUMER",
    "AXISBANK":"BANKING","BAJAJ-AUTO":"AUTO","BAJFINANCE":"FINANCE","BAJAJFINSV":"FINANCE",
    "BEL":"DEFENCE","BPCL":"ENERGY","BHARTIARTL":"TELECOM","BRITANNIA":"FMCG",
    "CIPLA":"PHARMA","COALINDIA":"ENERGY","DRREDDY":"PHARMA","EICHERMOT":"AUTO",
    "GRASIM":"CEMENT","HCLTECH":"IT","HDFCBANK":"BANKING","HDFCLIFE":"FINANCE",
    "HEROMOTOCO":"AUTO","HINDALCO":"METALS","HINDUNILVR":"FMCG","ICICIBANK":"BANKING",
    "INDUSINDBK":"BANKING","INFY":"IT","ITC":"FMCG","JSWSTEEL":"METALS",
    "KOTAKBANK":"BANKING","LT":"INFRA","LTIMINDTECH":"IT","M&M":"AUTO",
    "MARUTI":"AUTO","NESTLEIND":"FMCG","NTPC":"ENERGY","ONGC":"ENERGY",
    "POWERGRID":"ENERGY","RELIANCE":"ENERGY","SBILIFE":"FINANCE","SHRIRAMFIN":"FINANCE",
    "SBIN":"BANKING","SUNPHARMA":"PHARMA","TCS":"IT","TATACONSUM":"CONSUMER",
    "TATAMOTORS":"AUTO","TATASTEEL":"METALS","TECHM":"IT","TITAN":"CONSUMER",
    "TRENT":"CONSUMER","ULTRACEMCO":"CEMENT","WIPRO":"IT",
}

SECTOR_INDICES = {
    "IT":        "^CNXIT",
    "Pharma":    "^CNXPHARMA",
    "Auto":      "^CNXAUTO",
    "FMCG":      "^CNXFMCG",
    "Metals":    "^CNXMETAL",
    "Realty":    "^CNXREALTY",
    "Infra":     "^CNXINFRA",
    "Energy":    "^CNXENERGY",
    "PSU Bank":  "^CNXPSUBANK",
}


# ─── Data loaders ─────────────────────────────────────────────────────────────

def load_universe() -> list[str]:
    """
    Return the screener universe: all symbols in daily_ohlcv (F&O stocks).
    Falls back to NIFTY50_FALLBACK if the DB is empty.
    """
    syms = get_ohlcv_symbols()
    if syms:
        print(f"  Universe from DB: {len(syms)} symbols")
        return syms
    print(f"  DB empty — falling back to NIFTY50 static list ({len(NIFTY50_FALLBACK)} stocks)")
    return NIFTY50_FALLBACK


def _db_to_series(symbol: str, col: str, days: int) -> pd.Series:
    """Read one column from daily_ohlcv as a date-indexed Series."""
    rows = get_ohlcv_df(symbol, days=days)
    if not rows:
        return pd.Series(dtype=float)
    idx  = pd.to_datetime([r["trade_date"] for r in rows])
    vals = [float(r[col]) if r[col] is not None else float("nan") for r in rows]
    return pd.Series(vals, index=idx)


def load_db_weekly(universe: list[str], weeks: int = 54) -> pd.DataFrame:
    """
    Load daily close from DB and resample to weekly (Friday close).
    Returns wide DataFrame: columns = symbols + '^NSEI', index = weekly dates.
    """
    days = weeks * 7 + 14
    print(f"  Loading weekly data from DB ({len(universe)} symbols, ~{weeks}w)...", end="", flush=True)

    frames = {}
    for sym in universe:
        s = _db_to_series(sym, "close", days).resample("W-FRI").last()
        if len(s) >= 10:
            frames[sym] = s

    # Benchmark: still fetch from yfinance (only 1 ticker)
    try:
        bench = yf.download("^NSEI", period=f"{weeks+4}wk", interval="1wk",
                             auto_adjust=True, progress=False)["Close"]
        if isinstance(bench, pd.DataFrame):
            bench = bench.iloc[:, 0]
        frames["^NSEI"] = bench
    except Exception:
        pass

    df = pd.DataFrame(frames)
    print(f" done ({len(frames)} columns)")
    return df


def load_db_daily(universe: list[str], days: int = 180) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load daily OHLCV from DB for scan computations.
    Returns (closes_df, volumes_df).
    """
    print(f"  Loading daily data from DB ({len(universe)} symbols, {days}d)...", end="", flush=True)

    closes_map  = {}
    volumes_map = {}
    for sym in universe:
        rows = get_ohlcv_df(sym, days=days)
        if not rows:
            continue
        idx  = pd.to_datetime([r["trade_date"] for r in rows])
        closes_map[sym]  = pd.Series([float(r["close"])  if r["close"]  is not None else float("nan") for r in rows], index=idx)
        volumes_map[sym] = pd.Series([float(r["volume"]) if r["volume"] is not None else 0.0          for r in rows], index=idx)

    closes  = pd.DataFrame(closes_map)
    volumes = pd.DataFrame(volumes_map)
    print(f" done ({len(closes_map)} symbols)")
    return closes, volumes


def fetch_sector_index_data() -> pd.DataFrame:
    """1-year weekly data for NSE sector indices (still from yfinance — not in DB)."""
    tickers = list(SECTOR_INDICES.values()) + ["^NSEI"]
    print(f"  Fetching sector index data ({len(tickers)} indices from yfinance)...")
    raw = yf.download(tickers, period="52wk", interval="1wk",
                      auto_adjust=True, progress=False)["Close"]
    return raw


# ─── Computations ─────────────────────────────────────────────────────────────

def compute_rs(closes: pd.DataFrame, universe: list[str]) -> dict[str, float]:
    """IBD-style RS: weighted quarterly performance vs NIFTY50, normalised 1–99."""
    nifty = closes["^NSEI"].dropna() if "^NSEI" in closes.columns else pd.Series(dtype=float)
    scores = {}
    def qret(ser, n):
        return (ser.iloc[-1] / ser.iloc[-(n+1)] - 1) if len(ser) > n else 0
    for sym in universe:
        if sym not in closes.columns:
            continue
        s = closes[sym].dropna()
        if len(s) < 13:
            continue
        stock = 0.4*qret(s,13) + 0.2*qret(s,26) + 0.2*qret(s,39) + 0.2*qret(s,52)
        bench = (0.4*qret(nifty,13) + 0.2*qret(nifty,26) + 0.2*qret(nifty,39) + 0.2*qret(nifty,52)
                 if len(nifty) >= 13 else 0)
        scores[sym] = stock - bench
    vals = list(scores.values())
    if not vals:
        return {}
    lo, hi = min(vals), max(vals)
    rng = hi - lo or 1
    return {k: round(1 + 98*(v - lo)/rng) for k, v in scores.items()}


def compute_stage(closes: pd.DataFrame, universe: list[str]) -> dict[str, dict]:
    """Weinstein stage: price vs 30-week MA and MA slope."""
    results = {}
    for sym in universe:
        if sym not in closes.columns:
            continue
        s = closes[sym].dropna()
        if len(s) < 30:
            continue
        ma30     = s.rolling(30).mean()
        price    = float(s.iloc[-1])
        ma_now   = float(ma30.iloc[-1])
        ma_4w    = float(ma30.iloc[-5]) if len(ma30) >= 5 else ma_now
        vs_ma    = (price - ma_now) / ma_now
        slope    = (ma_now - ma_4w) / ma_4w
        hi52w    = float(s.iloc[-52:].max()) if len(s) >= 52 else float(s.max())
        chg_1m   = (price / float(s.iloc[-5])  - 1) if len(s) >= 5  else 0
        chg_3m   = (price / float(s.iloc[-13]) - 1) if len(s) >= 13 else 0
        chg_6m   = (price / float(s.iloc[-26]) - 1) if len(s) >= 26 else 0

        if price > ma_now and slope > 0.002:
            stage, label = 2, "Stage 2 ✓"
        elif price > ma_now * 0.98 and abs(slope) <= 0.002:
            stage, label = 1, "Stage 1 — Base"
        elif price < ma_now and slope > -0.002:
            stage, label = 3, "Stage 3 — Top"
        else:
            stage, label = 4, "Stage 4 ↓"

        results[sym] = {
            "price": round(price, 2),
            "stage": stage, "label": label,
            "vs_ma_pct": round(vs_ma*100, 1),
            "chg_1m":  round(chg_1m*100, 1),
            "chg_3m":  round(chg_3m*100, 1),
            "chg_6m":  round(chg_6m*100, 1),
            "hi52w":   round(hi52w, 2),
            "pct_from_hi": round((price/hi52w - 1)*100, 1),
        }
    return results


def compute_sector_rotation(sector_data: pd.DataFrame) -> list[dict]:
    """Compute sector index performance vs NIFTY50 over 1W, 1M, 3M, 6M."""
    if "^NSEI" not in sector_data.columns:
        return []

    nifty = sector_data["^NSEI"].dropna()
    results = []

    def pct(ser, n):
        s = ser.dropna()
        return round((s.iloc[-1] / s.iloc[-min(n+1, len(s))] - 1) * 100, 2) if len(s) > 1 else 0

    def rel(ser_pct, nifty_pct):
        return round(ser_pct - nifty_pct, 2)

    nifty_1w = pct(nifty, 1)
    nifty_1m = pct(nifty, 4)
    nifty_3m = pct(nifty, 13)
    nifty_6m = pct(nifty, 26)

    for name, ticker in SECTOR_INDICES.items():
        if ticker not in sector_data.columns:
            continue
        ser = sector_data[ticker].dropna()
        if len(ser) < 4:
            continue
        p1w = pct(ser, 1);  p1m = pct(ser, 4)
        p3m = pct(ser, 13); p6m = pct(ser, 26)
        r3m = rel(p3m, nifty_3m)  # relative strength 3M
        results.append({
            "name": name, "ticker": ticker,
            "p1w": p1w, "p1m": p1m, "p3m": p3m, "p6m": p6m,
            "rel_1w": rel(p1w, nifty_1w),
            "rel_1m": rel(p1m, nifty_1m),
            "rel_3m": r3m,
            "rel_6m": rel(p6m, nifty_6m),
            "score": round(0.15*rel(p1w,nifty_1w) + 0.35*rel(p1m,nifty_1m) + 0.5*r3m, 2),
        })
    results.sort(key=lambda x: -x["score"])

    # NIFTY benchmark row at end
    results.append({
        "name": "NIFTY 50", "ticker": "^NSEI",
        "p1w": nifty_1w, "p1m": nifty_1m, "p3m": nifty_3m, "p6m": nifty_6m,
        "rel_1w": 0, "rel_1m": 0, "rel_3m": 0, "rel_6m": 0, "score": 0,
    })
    return results


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_scans(closes: pd.DataFrame, volumes: pd.DataFrame,
                  weekly_stages: dict, rs_ratings: dict,
                  universe: list[str]) -> dict[str, list[dict]]:
    """Run 6 technical scans on daily data."""
    scans: dict[str, list[dict]] = {
        "rs_leaders":      [],
        "ema_crossover":   [],
        "near_52w_high":   [],
        "golden_cross":    [],
        "volume_surge":    [],
        "oversold_bounce": [],
    }

    for sym in universe:
        if sym not in closes.columns:
            continue
        s = closes[sym].dropna()
        if len(s) < 50:
            continue

        price  = float(s.iloc[-1])
        rs     = rs_ratings.get(sym, 0)
        stage  = weekly_stages.get(sym, {}).get("stage", 0)
        chg_1d = round((s.iloc[-1]/s.iloc[-2] - 1)*100, 2) if len(s) >= 2 else 0

        # 1. RS Leaders: IBD RS > 80 + Stage 2
        if rs >= 80 and stage == 2:
            scans["rs_leaders"].append({
                "sym": sym, "price": price, "rs": rs,
                "chg": chg_1d, "detail": f"RS {rs} · Stage 2"
            })

        # 2. EMA 20/50 Crossover (bullish — 20 EMA crossed above 50 EMA within 5 days)
        if len(s) >= 60:
            ema20 = s.ewm(span=20, adjust=False).mean()
            ema50 = s.ewm(span=50, adjust=False).mean()
            now_cross  = ema20.iloc[-1] > ema50.iloc[-1]
            prev_cross = ema20.iloc[-6] <= ema50.iloc[-6]
            if now_cross and prev_cross and price > ema20.iloc[-1]:
                scans["ema_crossover"].append({
                    "sym": sym, "price": price, "rs": rs,
                    "chg": chg_1d,
                    "detail": f"EMA20 {ema20.iloc[-1]:.0f} > EMA50 {ema50.iloc[-1]:.0f}"
                })

        # 3. Near 52W High (within 5%)
        hi52 = float(s.iloc[-252:].max()) if len(s) >= 252 else float(s.max())
        pct_from_hi = (price/hi52 - 1)*100
        if pct_from_hi >= -5:
            scans["near_52w_high"].append({
                "sym": sym, "price": price, "rs": rs,
                "chg": chg_1d,
                "detail": f"{pct_from_hi:+.1f}% from 52W high ₹{hi52:.0f}"
            })

        # 4. Golden Cross: 50 DMA crossed above 200 DMA within last 30 days
        if len(s) >= 210:
            ma50  = s.rolling(50).mean()
            ma200 = s.rolling(200).mean()
            current_above = ma50.iloc[-1] > ma200.iloc[-1]
            was_below = (ma50.iloc[-31:-1] <= ma200.iloc[-31:-1]).any()
            if current_above and was_below:
                gap = round((ma50.iloc[-1]/ma200.iloc[-1] - 1)*100, 2)
                scans["golden_cross"].append({
                    "sym": sym, "price": price, "rs": rs,
                    "chg": chg_1d,
                    "detail": f"MA50 {gap:+.1f}% above MA200"
                })

        # 5. Volume Surge: today's volume > 2x 20-day avg
        if volumes is not None and sym in volumes.columns:
            v = volumes[sym].dropna()
            if len(v) >= 21:
                avg_vol = float(v.iloc[-21:-1].mean())
                today_vol = float(v.iloc[-1])
                ratio = today_vol / avg_vol if avg_vol > 0 else 0
                if ratio >= 2.0:
                    scans["volume_surge"].append({
                        "sym": sym, "price": price, "rs": rs,
                        "chg": chg_1d,
                        "detail": f"Vol {ratio:.1f}x avg · {chg_1d:+.1f}% today"
                    })

        # 6. Oversold Bounce: RSI(14) was < 35 in last 5 days, now recovering + above 200 DMA
        if len(s) >= 220:
            rsi = _rsi(s)
            ma200 = s.rolling(200).mean()
            rsi_recent_low = float(rsi.iloc[-5:].min())
            rsi_now        = float(rsi.iloc[-1])
            above_ma200    = price > float(ma200.iloc[-1])
            if rsi_recent_low < 35 and rsi_now > rsi_recent_low and above_ma200:
                scans["oversold_bounce"].append({
                    "sym": sym, "price": price, "rs": rs,
                    "chg": chg_1d,
                    "detail": f"RSI {rsi_now:.0f} (was {rsi_recent_low:.0f}) · above 200D"
                })

    # Sort each scan by RS rating desc
    for key in scans:
        scans[key].sort(key=lambda x: -x["rs"])
    return scans


# ─── HTML builder ────────────────────────────────────────────────────────────

def _chg_col(v: float) -> str:
    return "#3fb950" if v >= 0 else "#f85149"

def _rel_col(v: float) -> str:
    if v >= 2:   return "#2ea043"
    if v >= 0.5: return "#54a020"
    if v >= -0.5:return "#8b949e"
    if v >= -2:  return "#c93c37"
    return "#da3633"

def _heat_bg(v: float) -> str:
    if v >= 3:    return "#1a4a2a"
    if v >= 1:    return "#1a3a1a"
    if v >= 0:    return "#1c2128"
    if v >= -1:   return "#2a1c1c"
    return "#3a1c1c"

def build_html(rs: dict, stages: dict, sector_rot: list, scans: dict, date_str: str,
               universe: list[str]) -> str:
    # ── Tab 1: Stock table ───────────────────────────────────────────
    rows = []
    for sym in universe:
        if sym not in rs or sym not in stages:
            continue
        rows.append({"sym": sym, "sector": SECTORS.get(sym, "OTHER"), "rs": rs[sym], **stages[sym]})
    rows.sort(key=lambda x: -x["rs"])

    sector_data: dict[str, dict] = {}
    for row in rows:
        sec = row["sector"]
        sd  = sector_data.setdefault(sec, {"rs_sum": 0, "count": 0, "stage2": 0})
        sd["rs_sum"] += row["rs"]; sd["count"] += 1
        if row["stage"] == 2: sd["stage2"] += 1
    sectors_sorted = sorted(sector_data.items(), key=lambda x: -x[1]["rs_sum"]/x[1]["count"])
    stage2_count = sum(1 for r in rows if r["stage"] == 2)

    def rs_bg(r):
        if r >= 80: return "#1a4a2a"
        if r >= 60: return "#1a3a1a"
        if r >= 40: return "#2a2a1a"
        return "#2a1a1a"

    def stage_cls(s):
        return {1:"s1", 2:"s2", 3:"s3", 4:"s4"}.get(s, "s0")

    def sec_colour(avg):
        if avg >= 75: return "#1a7a3a"
        if avg >= 60: return "#3a6a1a"
        if avg >= 45: return "#7a6a00"
        if avg >= 30: return "#7a4500"
        return "#7a1a1a"

    heatmap = "".join(
        f'<div class="stile" style="background:{sec_colour(round(sd["rs_sum"]/sd["count"]))}">'
        f'<div class="sn">{sec}</div>'
        f'<div class="sr">RS {round(sd["rs_sum"]/sd["count"])}</div>'
        f'<div class="sd">{sd["stage2"]} Stage 2 · {sd["count"]}</div></div>'
        for sec, sd in sectors_sorted
    )

    stock_rows = ""
    for row in rows:
        badge = '<span class="badge">S2</span>' if row["stage"] == 2 else ""
        stock_rows += (
            f'<tr><td><b>{row["sym"]}</b>{badge}</td>'
            f'<td><span class="stag">{row["sector"]}</span></td>'
            f'<td style="background:{rs_bg(row["rs"])};color:#e6edf3;font-weight:700">{row["rs"]}</td>'
            f'<td>₹{row["price"]:,.2f}</td>'
            f'<td style="color:{_chg_col(row["chg_1m"])}">{row["chg_1m"]:+.1f}%</td>'
            f'<td style="color:{_chg_col(row["chg_3m"])}">{row["chg_3m"]:+.1f}%</td>'
            f'<td style="color:{_chg_col(row["chg_6m"])}">{row["chg_6m"]:+.1f}%</td>'
            f'<td>{row["vs_ma_pct"]:+.1f}%</td>'
            f'<td style="color:{_chg_col(row["pct_from_hi"])}">{row["pct_from_hi"]:+.1f}%</td>'
            f'<td><span class="sl {stage_cls(row["stage"])}">{row["label"]}</span></td></tr>\n'
        )

    # ── Tab 2: Sector rotation ───────────────────────────────────────
    sec_rows = ""
    for r in sector_rot:
        is_bench = r["ticker"] == "^NSEI"
        style = ' style="border-top:1px solid #444;font-style:italic"' if is_bench else ""
        sec_rows += (
            f'<tr{style}><td><b>{r["name"]}</b></td>'
            f'<td style="color:{_chg_col(r["p1w"])}">{r["p1w"]:+.2f}%</td>'
            f'<td style="color:{_rel_col(r["rel_1w"])}">{r["rel_1w"]:+.2f}%</td>'
            f'<td style="color:{_chg_col(r["p1m"])}">{r["p1m"]:+.2f}%</td>'
            f'<td style="color:{_rel_col(r["rel_1m"])}">{r["rel_1m"]:+.2f}%</td>'
            f'<td style="color:{_chg_col(r["p3m"])}">{r["p3m"]:+.2f}%</td>'
            f'<td style="color:{_rel_col(r["rel_3m"])}">{r["rel_3m"]:+.2f}%</td>'
            f'<td style="color:{_chg_col(r["p6m"])}">{r["p6m"]:+.2f}%</td>'
            f'<td style="color:{_rel_col(r["rel_6m"])}">{r["rel_6m"]:+.2f}%</td>'
            f'<td style="color:{_rel_col(r["score"])};font-weight:700">{r["score"]:+.2f}</td></tr>\n'
        )

    # Sector rotation heatmap (3M relative)
    non_bench = [r for r in sector_rot if r["ticker"] != "^NSEI"]
    sec_heat = "".join(
        f'<div class="sectile" style="background:{_heat_bg(r["rel_3m"])}">'
        f'<div class="stn">{r["name"]}</div>'
        f'<div class="str2" style="color:{_rel_col(r["rel_3m"])}">{r["rel_3m"]:+.2f}%</div>'
        f'<div class="std">vs NIFTY 3M</div></div>'
        for r in non_bench
    )

    # ── Tab 3: Scans ─────────────────────────────────────────────────
    SCAN_META = {
        "rs_leaders":      ("RS Leaders",       "IBD RS &gt; 80 + Weinstein Stage 2. Institutional momentum.",       "#2ea043"),
        "ema_crossover":   ("EMA Crossover",     "20 EMA crossed above 50 EMA in last 5 sessions. Trend turn.",      "#89b4fa"),
        "near_52w_high":   ("52W High Zone",     "Price within 5% of 52-week high. Breakout candidates.",            "#f9e2af"),
        "golden_cross":    ("Golden Cross",      "50 DMA crossed above 200 DMA within last 30 days. Major signal.", "#cba6f7"),
        "volume_surge":    ("Volume Surge",      "Today's volume &ge; 2× 20-day average. Institutional activity.",   "#fab387"),
        "oversold_bounce": ("Oversold Bounce",   "RSI touched &lt;35 in last 5 days + recovering + above 200 DMA.", "#a6e3a1"),
    }

    scan_cards = ""
    for key, (title, desc, color) in SCAN_META.items():
        items = scans.get(key, [])
        cnt   = len(items)
        chip_html = "".join(
            f'<div class="chip">'
            f'<span style="color:#e6edf3;font-weight:700">{it["sym"]}</span>'
            f'<span style="color:#8b949e;font-size:.8em;margin-left:6px">₹{it["price"]:,.0f}</span>'
            f'<span style="color:{_chg_col(it["chg"])};font-size:.8em;margin-left:4px">{it["chg"]:+.1f}%</span>'
            f'<div style="font-size:.75em;color:#8b949e;margin-top:2px">{it["detail"]}</div>'
            f'</div>'
            for it in items[:15]
        ) if items else '<div style="color:#484f58;font-size:.85em;padding:8px 0">No matches today</div>'
        scan_cards += (
            f'<div class="scan-card">'
            f'<div class="scan-head"><span style="color:{color};font-weight:700">{title}</span>'
            f'<span class="scan-cnt">{cnt}</span></div>'
            f'<div class="scan-desc">{desc}</div>'
            f'<div class="chips">{chip_html}</div>'
            f'</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WealthLab Screener — {date_str}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;padding:0}}
  .header{{background:#161b22;border-bottom:1px solid #30363d;padding:14px 24px;display:flex;align-items:center;justify-content:space-between}}
  .hdr-title{{font-size:1.2em;font-weight:700;color:#89b4fa}}
  .hdr-sub{{font-size:.8em;color:#8b949e;margin-top:2px}}
  /* Tabs */
  .tabs{{display:flex;background:#161b22;border-bottom:1px solid #30363d;padding:0 24px}}
  .tab{{padding:10px 20px;font-size:.88em;cursor:pointer;color:#8b949e;border-bottom:2px solid transparent;transition:.15s}}
  .tab.active{{color:#89b4fa;border-bottom-color:#89b4fa;font-weight:600}}
  .tab-body{{display:none;padding:20px 24px}}
  .tab-body.active{{display:block}}
  /* Stats row */
  .stats{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}}
  .sbox{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 16px}}
  .sv{{font-size:1.6em;font-weight:700}}.sl2{{color:#3fb950}}.snl{{font-size:.75em;color:#8b949e}}
  /* Heatmap */
  .hm{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}}
  .stile{{border-radius:8px;padding:10px 12px;min-width:120px;cursor:default}}
  .sn{{font-weight:700;font-size:.88em}}.sr{{font-size:1.1em;font-weight:700}}.sd{{font-size:.72em;opacity:.8;margin-top:1px}}
  /* Table */
  table{{width:100%;border-collapse:collapse;font-size:.85em}}
  th{{background:#161b22;padding:8px 10px;text-align:left;border-bottom:2px solid #30363d;cursor:pointer;user-select:none;white-space:nowrap}}
  th:hover{{background:#1c2128}}
  td{{padding:6px 10px;border-bottom:1px solid #21262d;white-space:nowrap}}
  tr:hover td{{background:#161b22}}
  .badge{{background:#d29922;color:#0d1117;border-radius:3px;padding:1px 5px;font-size:.7em;margin-left:4px}}
  .sl{{font-size:.78em;padding:2px 7px;border-radius:10px;white-space:nowrap}}
  .s2{{background:#1a4a2a22;color:#3fb950;border:1px solid #238636}}
  .s1{{background:#78614a22;color:#e3b341;border:1px solid #9e6a03}}
  .s3,.s4{{background:#4a1a1a22;color:#f85149;border:1px solid #8b3210}}
  .s0{{color:#8b949e}}
  .stag{{background:#21262d;border-radius:10px;padding:2px 8px;font-size:.75em;color:#8b949e}}
  /* Sector rotation */
  .sec-heat{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}}
  .sectile{{border-radius:8px;padding:10px 14px;min-width:130px}}
  .stn{{font-size:.85em;font-weight:700;color:#e6edf3}}
  .str2{{font-size:1.3em;font-weight:700}}.std{{font-size:.72em;color:#8b949e;margin-top:1px}}
  .stitle{{font-size:.8em;color:#8b949e;text-transform:uppercase;letter-spacing:.08em;margin:16px 0 8px}}
  /* Scan cards */
  .scan-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}}
  .scan-card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px}}
  .scan-head{{display:flex;align-items:center;gap:10px;margin-bottom:6px}}
  .scan-cnt{{background:#21262d;color:#89b4fa;border-radius:10px;padding:1px 8px;font-size:.78em;font-weight:700}}
  .scan-desc{{font-size:.78em;color:#8b949e;margin-bottom:12px}}
  .chips{{display:flex;flex-direction:column;gap:6px}}
  .chip{{background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:6px 10px}}
  /* note: sectors table same as stock table */
</style>
</head>
<body>
<div class="header">
  <div><div class="hdr-title">⚡ WealthLab Screener</div>
    <div class="hdr-sub">NSE · NIFTY 50 universe · {date_str}</div></div>
  <div style="display:flex;gap:8px">
    <div class="sbox"><div class="sv sl2">{stage2_count}</div><div class="snl">Stage 2</div></div>
    <div class="sbox"><div class="sv">{len(rows)}</div><div class="snl">Stocks</div></div>
    <div class="sbox"><div class="sv">{len([r for r in sector_rot if r["ticker"]!="^NSEI"])}</div><div class="snl">Sectors tracked</div></div>
  </div>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab('stocks',this)">📊 Stocks</div>
  <div class="tab" onclick="showTab('sectors',this)">🔄 Sector Rotation</div>
  <div class="tab" onclick="showTab('scans',this)">🔍 Scans</div>
</div>

<!-- Tab 1: Stocks -->
<div class="tab-body active" id="tab-stocks">
  <div class="stitle">Sector Heatmap (avg RS)</div>
  <div class="hm">{heatmap}</div>
  <div class="stitle">RS Rankings — click headers to sort</div>
  <table id="tbl">
    <thead><tr>
      <th onclick="sort(0)">Symbol</th><th onclick="sort(1)">Sector</th>
      <th onclick="sort(2)">RS ↓</th><th onclick="sort(3)">Price</th>
      <th onclick="sort(4)">1M%</th><th onclick="sort(5)">3M%</th>
      <th onclick="sort(6)">6M%</th><th onclick="sort(7)">vs 30W MA</th>
      <th onclick="sort(8)">vs 52W Hi</th><th onclick="sort(9)">Stage</th>
    </tr></thead>
    <tbody>{stock_rows}</tbody>
  </table>
</div>

<!-- Tab 2: Sector Rotation -->
<div class="tab-body" id="tab-sectors">
  <div class="stitle">3M Relative Performance vs NIFTY50</div>
  <div class="sec-heat">{sec_heat}</div>
  <div class="stitle">Sector Performance Table — Absolute % and vs NIFTY50</div>
  <table id="sectbl">
    <thead><tr>
      <th onclick="sort2(0)">Sector</th>
      <th onclick="sort2(1)">1W Abs</th><th onclick="sort2(2)">1W Rel</th>
      <th onclick="sort2(3)">1M Abs</th><th onclick="sort2(4)">1M Rel</th>
      <th onclick="sort2(5)">3M Abs</th><th onclick="sort2(6)">3M Rel ↓</th>
      <th onclick="sort2(7)">6M Abs</th><th onclick="sort2(8)">6M Rel</th>
      <th onclick="sort2(9)">Score</th>
    </tr></thead>
    <tbody>{sec_rows}</tbody>
  </table>
  <div style="margin-top:12px;font-size:.75em;color:#484f58">
    Rel = sector performance minus NIFTY50 performance over same period.
    Score = 0.15×(1W rel) + 0.35×(1M rel) + 0.5×(3M rel).
    Green = outperforming NIFTY · Red = underperforming.
  </div>
</div>

<!-- Tab 3: Scans -->
<div class="tab-body" id="tab-scans">
  <div class="scan-grid">{scan_cards}</div>
</div>

<script>
function showTab(id, el) {{
  document.querySelectorAll('.tab-body').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
  el.classList.add('active');
}}
let _d={{}};
function sortGen(tbid, col) {{
  const tb = document.querySelector('#' + tbid + ' tbody');
  const rows = Array.from(tb.querySelectorAll('tr'));
  _d[tbid+col] = !_d[tbid+col];
  rows.sort((a,b) => {{
    const va = a.cells[col]?.innerText.replace(/[₹%+, ↓★S2]/g,'').trim()||'';
    const vb = b.cells[col]?.innerText.replace(/[₹%+, ↓★S2]/g,'').trim()||'';
    const na=parseFloat(va), nb=parseFloat(vb);
    const cmp = isNaN(na) ? va.localeCompare(vb) : na-nb;
    return _d[tbid+col] ? cmp : -cmp;
  }});
  rows.forEach(r => tb.appendChild(r));
}}
function sort(col)  {{ sortGen('tbl', col); }}
function sort2(col) {{ sortGen('sectbl', col); }}
</script>
</body>
</html>"""


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today    = datetime.now(IST).strftime("%Y-%m-%d")
    out_path = OUT_DIR / f"{today}.html"

    print(f"\n=== WealthLab Screener  {today} ===")

    # Universe from DB (F&O stocks) or fallback
    universe = load_universe()

    # Load data
    weekly          = load_db_weekly(universe)
    sec_idx         = fetch_sector_index_data()
    daily_c, daily_v = load_db_daily(universe)

    # Compute
    rs       = compute_rs(weekly, universe)
    stages   = compute_stage(weekly, universe)
    sec_rot  = compute_sector_rotation(sec_idx)
    scans    = compute_scans(daily_c, daily_v, stages, rs, universe)

    # Build and save
    html = build_html(rs, stages, sec_rot, scans, today, universe)
    out_path.write_text(html)

    s2 = sum(1 for s in stages.values() if s["stage"] == 2)
    scan_summary = ", ".join(f"{k}:{len(v)}" for k, v in scans.items())
    print(f"Saved: {out_path}  ({len(rs)} stocks, {s2} Stage 2)")
    print(f"Scans: {scan_summary}")
    return str(out_path)


if __name__ == "__main__":
    main()
