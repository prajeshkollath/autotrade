"""
rs_screener.py — WealthLab-style RS + Stage 2 screener for NSE stocks.

Computes:
  1. IBD Relative Strength Rating — percentile rank in the NIFTY 500 universe
     Formula: 40% × Q4 return + 20% × Q3 + 20% × Q2 + 20% × Q1
              where Q4 = last 63 days (most recent quarter, weighted highest)
  2. Weinstein Stage 2 detection — markup phase candidates
     Criteria: price > 30-week MA AND 30-week MA slope positive AND RS ≥ 70

Data source: yfinance (.NS suffix for NSE stocks — free, no API key)
Install: .venv/bin/pip install yfinance

HOW TO RUN:
  cd ~/autotrade

  # Full NIFTY 50 universe (default)
  .venv/bin/python agents/rs_screener.py

  # Show only Stage 2 candidates
  .venv/bin/python agents/rs_screener.py --stage2-only

  # Top N by RS rating
  .venv/bin/python agents/rs_screener.py --top 20

  # Filter by sector
  .venv/bin/python agents/rs_screener.py --sector IT

  # Inject Stage 2 stocks into morning_brief for TradingAgents to analyse
  .venv/bin/python agents/rs_screener.py --inject-brief

Outputs:
  data/screener/YYYY-MM-DD.json  — machine-readable results
  data/screener/YYYY-MM-DD.html  — self-contained browser dashboard

FRAMEWORK EQUIVALENT:
  IBD RS Rating = how strongly a stock moves relative to the whole market.
  Stage 2 (Weinstein) = the only stage worth buying: price in sustained uptrend
  above a rising 30-week MA, with RS confirmation.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))
SCREENER_DIR = Path("/home/freed/autotrade/data/screener")
BRIEFS_DIR   = Path("/home/freed/autotrade/data/morning_briefs")

# ---------------------------------------------------------------------------
# NIFTY 50 universe — symbol (yfinance .NS) → sector
# Update this list as constituents change
# ---------------------------------------------------------------------------
UNIVERSE: dict[str, str] = {
    "RELIANCE.NS":    "ENERGY",
    "TCS.NS":         "IT",
    "HDFCBANK.NS":    "BANKING",
    "INFY.NS":        "IT",
    "HINDUNILVR.NS":  "FMCG",
    "ICICIBANK.NS":   "BANKING",
    "KOTAKBANK.NS":   "BANKING",
    "SBIN.NS":        "BANKING",
    "BHARTIARTL.NS":  "TELECOM",
    "ITC.NS":         "FMCG",
    "BAJFINANCE.NS":  "FINANCE",
    "LT.NS":          "INFRA",
    "ASIANPAINT.NS":  "CONSUMER",
    "AXISBANK.NS":    "BANKING",
    "MARUTI.NS":      "AUTO",
    "TITAN.NS":       "CONSUMER",
    "SUNPHARMA.NS":   "PHARMA",
    "NESTLEIND.NS":   "FMCG",
    "WIPRO.NS":       "IT",
    "HCLTECH.NS":     "IT",
    "ULTRACEMCO.NS":  "CEMENT",
    "POWERGRID.NS":   "ENERGY",
    "NTPC.NS":        "ENERGY",
    "ONGC.NS":        "ENERGY",
    "TATAMOTORS.NS":  "AUTO",    # may show as delisted transiently — skip gracefully
    "TATASTEEL.NS":   "METALS",
    "COALINDIA.NS":   "ENERGY",
    "ADANIENT.NS":    "INFRA",
    "ADANIPORTS.NS":  "INFRA",
    "BAJAJFINSV.NS":  "FINANCE",
    "BAJAJ-AUTO.NS":  "AUTO",
    "TECHM.NS":       "IT",
    "M&M.NS":         "AUTO",
    "DIVISLAB.NS":    "PHARMA",
    "CIPLA.NS":       "PHARMA",
    "DRREDDY.NS":     "PHARMA",
    "EICHERMOT.NS":   "AUTO",
    "BPCL.NS":        "ENERGY",
    "HEROMOTOCO.NS":  "AUTO",
    "BRITANNIA.NS":   "FMCG",
    "APOLLOHOSP.NS":  "PHARMA",
    "JSWSTEEL.NS":    "METALS",
    "HINDALCO.NS":    "METALS",
    "TATACONSUM.NS":  "FMCG",
    "SBILIFE.NS":     "FINANCE",
    "HDFCLIFE.NS":    "FINANCE",
    "INDUSINDBK.NS":  "BANKING",
    "UPL.NS":         "AGRI",
    "GRASIM.NS":      "CEMENT",
    "SHREECEM.NS":    "CEMENT",
}

# Benchmark for RS comparison (Nifty 50 index)
BENCHMARK = "^NSEI"

# IBD RS quarter weights — most recent quarter gets highest weight
RS_WEIGHTS = [0.40, 0.20, 0.20, 0.20]   # Q4, Q3, Q2, Q1

# Each quarter = ~63 trading days
QUARTER_DAYS = 63

# Weinstein 30-week MA = 150 trading days
MA_PERIOD = 150

# Stage 2 minimum RS percentile
STAGE2_MIN_RS = 70


# ---------------------------------------------------------------------------
# Data download
# ---------------------------------------------------------------------------

def download_ohlcv(symbols: list[str], period: str = "14mo") -> dict:
    """
    Downloads daily OHLCV from Yahoo Finance for all symbols in one batch call.
    Returns dict: symbol → DataFrame with columns [Open, High, Low, Close, Volume].
    Symbols not found or with insufficient data are excluded with a warning.
    """
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance not installed — run: .venv/bin/pip install yfinance")

    print(f"  Downloading {len(symbols)} symbols from Yahoo Finance...", end="", flush=True)
    # Download all at once — yfinance batches automatically, much faster than loop
    raw = yf.download(
        symbols,
        period=period,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
        threads=True,
    )
    print(" done")

    result = {}
    needed = QUARTER_DAYS * 4 + 10   # 12+ months needed for full RS calc

    if len(symbols) == 1:
        # Single symbol — yfinance returns flat DataFrame, not grouped
        sym = symbols[0]
        if len(raw) >= needed:
            result[sym] = raw
        else:
            print(f"    !!  {sym}: only {len(raw)} days, skipping")
        return result

    for sym in symbols:
        try:
            df = raw[sym].dropna(subset=["Close"])
            if len(df) >= needed:
                result[sym] = df
            else:
                print(f"    !!  {sym}: only {len(df)} days, skipping")
        except Exception:
            print(f"    !!  {sym}: download failed, skipping")

    return result


# ---------------------------------------------------------------------------
# IBD RS Score calculation
# ---------------------------------------------------------------------------

def _quarter_return(close: "pd.Series", end_idx: int, length: int) -> float:
    """
    Return % gain over `length` days ending at `end_idx`.
    Uses index positions, not dates.
    """
    start_idx = end_idx - length
    if start_idx < 0:
        return 0.0
    p_end   = float(close.iloc[end_idx])
    p_start = float(close.iloc[start_idx])
    if p_start == 0:
        return 0.0
    return (p_end - p_start) / p_start


def compute_rs_score(close: "pd.Series") -> float:
    """
    IBD RS Score for a single stock's close price series.
    Returns raw score (not yet ranked — ranking is done across the universe).
    Higher = stronger price performance relative to its own history.
    """
    end = len(close) - 1
    q4 = _quarter_return(close, end, QUARTER_DAYS)
    q3 = _quarter_return(close, end - QUARTER_DAYS, QUARTER_DAYS)
    q2 = _quarter_return(close, end - 2 * QUARTER_DAYS, QUARTER_DAYS)
    q1 = _quarter_return(close, end - 3 * QUARTER_DAYS, QUARTER_DAYS)

    score = (RS_WEIGHTS[0] * q4 +
             RS_WEIGHTS[1] * q3 +
             RS_WEIGHTS[2] * q2 +
             RS_WEIGHTS[3] * q1)
    return score


def rank_universe(scores: dict[str, float]) -> dict[str, int]:
    """
    Converts raw RS scores to IBD-style 1-99 percentile ranks.
    1 = worst performer, 99 = best.
    """
    if not scores:
        return {}
    sorted_syms = sorted(scores.keys(), key=lambda s: scores[s])
    n = len(sorted_syms)
    ranks = {}
    for i, sym in enumerate(sorted_syms):
        ranks[sym] = max(1, min(99, round((i / (n - 1)) * 98 + 1))) if n > 1 else 50
    return ranks


# ---------------------------------------------------------------------------
# Weinstein Stage detection
# ---------------------------------------------------------------------------

def detect_stage(close: "pd.Series", volume: "pd.Series") -> tuple[int, str]:
    """
    Classifies stock into Weinstein Stage 1-4 based on 30-week MA (150 days).
    Returns (stage_number, stage_label).

    Stage 1 — Basing    : price near flat MA, waiting for breakout
    Stage 2 — Markup    : price above rising MA (BUY zone)
    Stage 3 — Top       : price topping, MA flattening
    Stage 4 — Decline   : price below declining MA (AVOID)
    """
    if len(close) < MA_PERIOD + 20:
        return (0, "Insufficient data")

    ma_series = close.rolling(MA_PERIOD).mean()
    ma_now    = float(ma_series.iloc[-1])
    ma_prev   = float(ma_series.iloc[-20])     # 4 weeks ago (20 trading days)
    price_now = float(close.iloc[-1])

    ma_slope_pct = (ma_now - ma_prev) / ma_prev if ma_prev else 0.0
    above_ma = price_now > ma_now

    if above_ma and ma_slope_pct > 0.005:       # price above rising MA
        return (2, "Stage 2 — Markup ✓")
    elif above_ma and abs(ma_slope_pct) <= 0.005:
        return (1, "Stage 1 — Basing")
    elif not above_ma and ma_slope_pct < -0.005:
        return (4, "Stage 4 — Decline")
    else:
        return (3, "Stage 3 — Top/Distribution")


def _pct_above_ma(close: "pd.Series") -> float:
    """How far is current price above/below the 30-week MA, as a %."""
    if len(close) < MA_PERIOD:
        return 0.0
    ma = float(close.rolling(MA_PERIOD).mean().iloc[-1])
    return ((float(close.iloc[-1]) - ma) / ma * 100) if ma else 0.0


# ---------------------------------------------------------------------------
# Build results
# ---------------------------------------------------------------------------

def build_results(
    data: dict,
    rs_ranks: dict[str, int],
    rs_scores: dict[str, float],
) -> list[dict]:
    """
    Assembles per-symbol result dicts for output.
    """
    results = []
    for sym, df in data.items():
        close  = df["Close"]
        volume = df["Volume"]

        stage_num, stage_label = detect_stage(close, volume)
        ticker = sym.replace(".NS", "")
        price_now = float(close.iloc[-1])
        price_1m  = float(close.iloc[-22]) if len(close) > 22 else price_now
        price_3m  = float(close.iloc[-63]) if len(close) > 63 else price_now

        results.append({
            "symbol":    ticker,
            "yf_symbol": sym,
            "sector":    UNIVERSE.get(sym, "OTHER"),
            "price":     round(price_now, 2),
            "chg_1m":    round((price_now - price_1m) / price_1m * 100, 1) if price_1m else 0,
            "chg_3m":    round((price_now - price_3m) / price_3m * 100, 1) if price_3m else 0,
            "rs_score":  round(rs_scores.get(sym, 0.0), 4),
            "rs_rating": rs_ranks.get(sym, 0),
            "stage":     stage_num,
            "stage_label": stage_label,
            "pct_above_ma": round(_pct_above_ma(close), 1),
            "is_stage2": stage_num == 2 and rs_ranks.get(sym, 0) >= STAGE2_MIN_RS,
        })

    # Sort by RS rating descending
    results.sort(key=lambda r: r["rs_rating"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Sector heatmap data
# ---------------------------------------------------------------------------

def sector_summary(results: list[dict]) -> list[dict]:
    """Aggregates RS and Stage 2 counts per sector for the heatmap."""
    sectors: dict[str, list] = {}
    for r in results:
        sec = r["sector"]
        sectors.setdefault(sec, []).append(r)

    summary = []
    for sec, stocks in sorted(sectors.items()):
        avg_rs   = round(sum(s["rs_rating"] for s in stocks) / len(stocks), 1)
        stage2_n = sum(1 for s in stocks if s["is_stage2"])
        summary.append({
            "sector":   sec,
            "count":    len(stocks),
            "avg_rs":   avg_rs,
            "stage2_n": stage2_n,
            "top_stock": stocks[0]["symbol"] if stocks else "",
        })

    summary.sort(key=lambda s: s["avg_rs"], reverse=True)
    return summary


# ---------------------------------------------------------------------------
# HTML dashboard generator
# ---------------------------------------------------------------------------

def _rs_color(rs: int) -> str:
    if rs >= 80:
        return "#1a7a3a"   # dark green
    elif rs >= 60:
        return "#7a6a00"   # olive
    elif rs >= 40:
        return "#555"
    else:
        return "#7a1a1a"   # dark red


def _sector_color(avg_rs: float) -> str:
    if avg_rs >= 75:
        return "#1a7a3a"
    elif avg_rs >= 55:
        return "#4a7a1a"
    elif avg_rs >= 40:
        return "#7a6a00"
    elif avg_rs >= 25:
        return "#7a4500"
    else:
        return "#7a1a1a"


def generate_html(results: list[dict], sectors: list[dict], run_date: str) -> str:
    """Generates a self-contained single-file HTML dashboard."""

    # Sector heatmap HTML
    sector_html = ""
    for s in sectors:
        bg = _sector_color(s["avg_rs"])
        sector_html += (
            f'<div class="sector-tile" style="background:{bg}">'
            f'<div class="sec-name">{s["sector"]}</div>'
            f'<div class="sec-rs">RS {s["avg_rs"]}</div>'
            f'<div class="sec-detail">{s["stage2_n"]} Stage 2 · {s["count"]} stocks</div>'
            f'</div>'
        )

    # Table rows
    rows_html = ""
    for r in results:
        rs_col = _rs_color(r["rs_rating"])
        stage2_badge = ' <span class="badge">★ S2</span>' if r["is_stage2"] else ""
        chg1m_col = "#1a7a3a" if r["chg_1m"] >= 0 else "#7a1a1a"
        chg3m_col = "#1a7a3a" if r["chg_3m"] >= 0 else "#7a1a1a"
        rows_html += f"""
        <tr class="{'stage2-row' if r['is_stage2'] else ''}">
          <td><strong>{r['symbol']}</strong>{stage2_badge}</td>
          <td><span class="sector-tag">{r['sector']}</span></td>
          <td style="color:{rs_col};font-weight:bold;font-size:1.1em">{r['rs_rating']}</td>
          <td>₹{r['price']:,.2f}</td>
          <td style="color:{chg1m_col}">{r['chg_1m']:+.1f}%</td>
          <td style="color:{chg3m_col}">{r['chg_3m']:+.1f}%</td>
          <td>{r['pct_above_ma']:+.1f}%</td>
          <td><span class="stage-label stage-{r['stage']}">{r['stage_label']}</span></td>
        </tr>"""

    stage2_count = sum(1 for r in results if r["is_stage2"])
    top_sector = sectors[0]["sector"] if sectors else "—"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>WealthLab Screener — {run_date}</title>
<style>
  body {{ font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background:#0d1117; color:#e6edf3; margin:0; padding:20px; }}
  h1   {{ font-size:1.4em; margin-bottom:4px; }}
  .subtitle {{ color:#8b949e; font-size:.85em; margin-bottom:20px; }}
  .stats-row {{ display:flex; gap:16px; margin-bottom:20px; flex-wrap:wrap; }}
  .stat-box  {{ background:#161b22; border:1px solid #30363d; border-radius:8px;
                padding:12px 18px; min-width:120px; }}
  .stat-val  {{ font-size:1.8em; font-weight:700; }}
  .stat-lbl  {{ font-size:.78em; color:#8b949e; }}
  .heatmap   {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:24px; }}
  .sector-tile {{ border-radius:8px; padding:12px 14px; min-width:130px;
                  color:#fff; cursor:default; }}
  .sec-name  {{ font-weight:700; font-size:.95em; }}
  .sec-rs    {{ font-size:1.2em; font-weight:700; }}
  .sec-detail {{ font-size:.75em; opacity:.8; margin-top:2px; }}
  table      {{ width:100%; border-collapse:collapse; font-size:.88em; }}
  th         {{ background:#161b22; padding:8px 10px; text-align:left;
                border-bottom:2px solid #30363d; cursor:pointer; user-select:none; }}
  th:hover   {{ background:#1c2128; }}
  td         {{ padding:7px 10px; border-bottom:1px solid #21262d; }}
  tr:hover   {{ background:#161b22; }}
  .stage2-row {{ background:#0d2010; }}
  .stage2-row:hover {{ background:#122918; }}
  .badge     {{ background:#d29922; color:#0d1117; border-radius:3px;
                padding:1px 5px; font-size:.75em; margin-left:5px; }}
  .stage-label {{ font-size:.8em; padding:2px 7px; border-radius:10px; white-space:nowrap; }}
  .stage-2   {{ background:#1a7a3a22; color:#3fb950; border:1px solid #238636; }}
  .stage-1   {{ background:#78614a22; color:#e3b341; border:1px solid #9e6a03; }}
  .stage-3   {{ background:#7a3a1a22; color:#f85149; border:1px solid #8b3210; }}
  .stage-4   {{ background:#7a1a1a22; color:#f85149; border:1px solid #6e1a1a; }}
  .stage-0   {{ color:#8b949e; }}
  .sector-tag {{ background:#21262d; border-radius:10px; padding:2px 8px;
                 font-size:.78em; color:#8b949e; }}
  .section-title {{ font-size:1em; font-weight:600; color:#8b949e;
                    margin:20px 0 8px; text-transform:uppercase; letter-spacing:.05em; }}
</style>
</head>
<body>
<h1>WealthLab Screener</h1>
<div class="subtitle">NSE / NIFTY 50 universe · {run_date} · IBD RS Rating + Weinstein Stage Analysis</div>

<div class="stats-row">
  <div class="stat-box"><div class="stat-val">{len(results)}</div><div class="stat-lbl">Stocks analysed</div></div>
  <div class="stat-box"><div class="stat-val" style="color:#3fb950">{stage2_count}</div><div class="stat-lbl">Stage 2 candidates</div></div>
  <div class="stat-box"><div class="stat-val">{sectors[0]['avg_rs'] if sectors else '—'}</div><div class="stat-lbl">Top sector RS ({top_sector})</div></div>
  <div class="stat-box"><div class="stat-val">{results[0]['rs_rating'] if results else '—'}</div><div class="stat-lbl">Highest RS ({results[0]['symbol'] if results else '—'})</div></div>
</div>

<div class="section-title">Sector Heatmap (avg RS)</div>
<div class="heatmap">{sector_html}</div>

<div class="section-title">RS Rankings (click column headers to sort)</div>
<table id="tbl">
  <thead>
    <tr>
      <th onclick="sortTable(0)">Symbol</th>
      <th onclick="sortTable(1)">Sector</th>
      <th onclick="sortTable(2)">RS Rating ↓</th>
      <th onclick="sortTable(3)">Price</th>
      <th onclick="sortTable(4)">1M Chg</th>
      <th onclick="sortTable(5)">3M Chg</th>
      <th onclick="sortTable(6)">vs 30W MA</th>
      <th onclick="sortTable(7)">Stage</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>

<script>
function sortTable(col) {{
  const t = document.getElementById("tbl");
  const rows = Array.from(t.tBodies[0].rows);
  const dir  = t.dataset.lastCol == col && t.dataset.dir == "1" ? -1 : 1;
  t.dataset.lastCol = col; t.dataset.dir = dir;
  rows.sort((a, b) => {{
    let av = a.cells[col].innerText.replace(/[₹%+,★ S2]/g,'').trim();
    let bv = b.cells[col].innerText.replace(/[₹%+,★ S2]/g,'').trim();
    return (isNaN(av) ? av.localeCompare(bv) : (parseFloat(av) - parseFloat(bv))) * dir;
  }});
  rows.forEach(r => t.tBodies[0].appendChild(r));
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Morning brief injection
# ---------------------------------------------------------------------------

def inject_stage2_into_brief(stage2_stocks: list[dict]) -> None:
    """
    Appends Stage 2 breakout candidates to today's morning brief JSON
    so TradingAgents can include them in equity analysis.
    """
    today = datetime.now(IST).strftime("%Y-%m-%d")
    brief_path = BRIEFS_DIR / f"{today}.json"

    if not brief_path.exists():
        print(f"  No morning brief found at {brief_path} — skipping inject")
        return

    brief = json.loads(brief_path.read_text())
    brief["stage2_candidates"] = [
        {"symbol": s["symbol"], "rs_rating": s["rs_rating"], "stage": s["stage_label"]}
        for s in stage2_stocks
    ]
    brief_path.write_text(json.dumps(brief, indent=2))
    print(f"  Injected {len(stage2_stocks)} Stage 2 candidates into {brief_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_screener(
    sector_filter: Optional[str] = None,
    top_n: Optional[int] = None,
    stage2_only: bool = False,
    inject_brief: bool = False,
) -> list[dict]:

    print("\n=== WealthLab RS Screener ===")
    today = datetime.now(IST).strftime("%Y-%m-%d")

    # Filter universe by sector if requested
    symbols = [
        sym for sym, sec in UNIVERSE.items()
        if sector_filter is None or sec.upper() == sector_filter.upper()
    ]
    if not symbols:
        sys.exit(f"No symbols found for sector: {sector_filter}")

    # Download OHLCV (14 months = 4 full quarters + MA buffer)
    data = download_ohlcv(symbols, period="14mo")
    if not data:
        sys.exit("No data downloaded — check yfinance connectivity")

    # Compute RS scores
    print("  Computing RS scores...", end="", flush=True)
    scores = {sym: compute_rs_score(df["Close"]) for sym, df in data.items()}
    rs_ranks = rank_universe(scores)
    print(" done")

    # Build full results
    print("  Detecting Weinstein stages...", end="", flush=True)
    results = build_results(data, rs_ranks, scores)
    print(" done")

    # Apply filters
    if stage2_only:
        results = [r for r in results if r["is_stage2"]]
    if top_n:
        results = results[:top_n]

    sectors = sector_summary(results if not stage2_only else build_results(data, rs_ranks, scores))

    # Print summary table
    print(f"\n  {'SYMBOL':<14} {'SECTOR':<12} {'RS':>4}  {'PRICE':>8}  {'1M':>6}  {'STAGE'}")
    print("  " + "-" * 65)
    for r in results[:30]:
        s2 = " ★" if r["is_stage2"] else ""
        print(
            f"  {r['symbol']:<14} {r['sector']:<12} {r['rs_rating']:>4}  "
            f"₹{r['price']:>7,.0f}  {r['chg_1m']:>+5.1f}%  {r['stage_label']}{s2}"
        )

    stage2_list = [r for r in results if r["is_stage2"]]
    print(f"\n  Total: {len(results)} stocks  |  Stage 2 candidates: {len(stage2_list)}")
    if sector_filter is None:
        print("\n  Top sectors by avg RS:")
        for s in sectors[:5]:
            print(f"    {s['sector']:<12}  avg RS {s['avg_rs']}  ({s['stage2_n']} Stage 2)")

    # Save outputs
    SCREENER_DIR.mkdir(parents=True, exist_ok=True)
    json_path = SCREENER_DIR / f"{today}.json"
    html_path = SCREENER_DIR / f"{today}.html"

    output = {
        "date":      today,
        "universe":  len(symbols),
        "analysed":  len(data),
        "results":   results,
        "sectors":   sectors,
        "stage2":    stage2_list,
    }
    json_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    generate_html_content = generate_html(results, sectors, today)
    html_path.write_text(generate_html_content, encoding="utf-8")

    print(f"\n  Saved → {json_path}")
    print(f"  Saved → {html_path}")

    if inject_brief and stage2_list:
        inject_stage2_into_brief(stage2_list)

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WealthLab RS + Stage 2 screener")
    parser.add_argument("--sector",      help="Filter by sector (e.g. IT, BANKING, PHARMA)")
    parser.add_argument("--top",         type=int, help="Show top N stocks by RS rating")
    parser.add_argument("--stage2-only", action="store_true", help="Show only Stage 2 candidates")
    parser.add_argument("--inject-brief",action="store_true", help="Add Stage 2 stocks to today's morning brief")
    args = parser.parse_args()

    run_screener(
        sector_filter=args.sector,
        top_n=args.top,
        stage2_only=args.stage2_only,
        inject_brief=args.inject_brief,
    )
