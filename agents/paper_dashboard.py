#!/usr/bin/env python3
"""
paper_dashboard.py — Live trading dashboard for paper/sandbox session.

Shows three panels:
  LEFT TOP  — Open positions with OTM%, sell price, LTP, P&L, risk flags
  LEFT BOT  — Filled trades today (from OpenAlgo tradebook)
  RIGHT     — LLM agent decisions timeline (last 22, newest first)

Auto-refreshes every --refresh seconds (default: 60).

HOW TO RUN:
  cd ~/autotrade
  OPENALGO_API_KEY=<key> .venv/bin/python agents/paper_dashboard.py \
    --underlying NIFTY --target 6000 --max-loss -8000

PAPER TRADING PREREQUISITE:
  Enable "Analyze Mode" in OpenAlgo -> Settings -> Analyzer Settings.
  When enabled, /api/v1/placeorder routes to sandbox DB instead of live broker.
  Positions and trades then show up in /api/v1/positions and /api/v1/tradebook.

LIVE TRADING:
  Run without enabling Analyze Mode — shows real broker positions.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

IST = timezone(timedelta(hours=5, minutes=30))
OPENALGO_BASE = "http://localhost:5000"
LOG_DIR = Path("/home/freed/autotrade/data/decision_logs")

# Option symbol regex: NIFTY09DEC2526000CE / BANKNIFTY23JUN2624700PE
_OPT_RE = re.compile(r"^([A-Z]+?)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$")


def _parse_sym(symbol: str) -> Optional[dict]:
    m = _OPT_RE.match(symbol.upper())
    if not m:
        return None
    return {"underlying": m.group(1), "strike": int(m.group(5)), "opt_type": m.group(6)}


def _otm_pct(strike: int, opt_type: str, spot: float) -> float:
    if spot <= 0:
        return 0.0
    return max((strike - spot) / spot if opt_type == "CE" else (spot - strike) / spot, 0.0)


def _risk_flag(otm: float, ratio: float) -> Text:
    t = Text()
    if otm < 0.005:
        t.append("!! CRIT", style="bold red")
    elif otm < 0.010:
        t.append("! DANGER", style="red")
    elif otm < 0.015:
        t.append("~ WARN", style="yellow")
    else:
        t.append("OK", style="green")
    if ratio >= 2.0:
        t.append(" DOUBLED", style="bold magenta")
    return t


def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def _get_positions(api_key: str) -> list[dict]:
    try:
        r = requests.get(f"{OPENALGO_BASE}/api/v1/positions", headers=_headers(api_key), timeout=8)
        r.raise_for_status()
        d = r.json()
        return d.get("data", d) if isinstance(d, dict) else (d or [])
    except Exception:
        return []


def _get_tradebook(api_key: str) -> list[dict]:
    try:
        r = requests.get(f"{OPENALGO_BASE}/api/v1/tradebook", headers=_headers(api_key), timeout=8)
        r.raise_for_status()
        d = r.json()
        raw = d.get("data", d) if isinstance(d, dict) else (d or [])
        if isinstance(raw, dict):
            raw = raw.get("orders", raw.get("trades", []))
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _get_spot(api_key: str, underlying: str) -> float:
    sym_map = {"BANKNIFTY": "NIFTY BANK", "NIFTY": "NIFTY 50", "SENSEX": "SENSEX"}
    sym = sym_map.get(underlying, underlying)
    try:
        r = requests.post(
            f"{OPENALGO_BASE}/api/v1/quotes",
            json={"apikey": api_key, "symbol": sym, "exchange": "NSE"},
            headers=_headers(api_key),
            timeout=8,
        )
        r.raise_for_status()
        d = r.json()
        inner = d.get("data", d) if isinstance(d, dict) else {}
        return float(inner.get("ltp", d.get("ltp", 0)))
    except Exception:
        return 0.0


def _read_decisions(n: int = 25) -> list[dict]:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    path = LOG_DIR / f"{today}.jsonl"
    if not path.exists():
        return []
    lines = [line for line in path.read_text().strip().splitlines() if line.strip()]
    records = []
    for line in lines[-n:]:
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return list(reversed(records))  # newest first


def _ts_str(raw: str) -> str:
    try:
        return datetime.fromisoformat(raw).strftime("%H:%M")
    except Exception:
        s = str(raw)
        return s[-8:-3] if len(s) >= 8 else s


def _build_layout(api_key: str, underlying: str, target: float, max_loss: float) -> Layout:
    positions = _get_positions(api_key)
    trades    = _get_tradebook(api_key)
    spot      = _get_spot(api_key, underlying)
    decisions = _read_decisions(25)
    now_str   = datetime.now(IST).strftime("%Y-%m-%d  %H:%M IST")

    open_pos = [p for p in positions if p.get("quantity", 0) != 0]
    net_pnl  = sum(float(p.get("pnl", 0)) for p in open_pos)

    # Header
    pnl_col = "bold green" if net_pnl >= 0 else "bold red"
    header = Text()
    header.append(f"  {underlying}  |  {now_str}  |  Spot: {spot:,.0f}  |  ", style="white")
    header.append(f"Net P&L: Rs.{net_pnl:+,.0f}", style=pnl_col)
    header.append(f"  /  Target Rs.{target:,.0f}  Floor Rs.{max_loss:,.0f}", style="dim white")
    pct = net_pnl / target if target else 0
    bar_filled = max(0, min(int(pct * 20), 20))
    bar = "[" + "#" * bar_filled + "." * (20 - bar_filled) + f"]  {pct*100:.0f}%"
    header.append(f"   {bar}", style="green" if pct >= 0 else "red")

    # Open Positions table
    pos_tbl = Table(box=box.SIMPLE_HEAVY, expand=True, show_header=True, header_style="bold")
    pos_tbl.add_column("Symbol",  style="cyan", no_wrap=True)
    pos_tbl.add_column("T",  width=3)
    pos_tbl.add_column("Qty",     width=6,  justify="right")
    pos_tbl.add_column("OTM%",    width=7,  justify="right")
    pos_tbl.add_column("Sell@",   width=8,  justify="right")
    pos_tbl.add_column("LTP",     width=8,  justify="right")
    pos_tbl.add_column("P&L",     width=10, justify="right")
    pos_tbl.add_column("Ratio",   width=6,  justify="right")
    pos_tbl.add_column("Status",  width=14)

    for p in open_pos:
        sym   = p.get("symbol", "")
        qty   = p.get("quantity", 0)
        avg   = float(p.get("average_price", 0))
        ltp   = float(p.get("ltp", 0))
        pnl_v = float(p.get("pnl", p.get("unrealized_pnl", 0)))

        parsed   = _parse_sym(sym)
        opt_type = parsed["opt_type"] if parsed else "?"
        otm      = _otm_pct(parsed["strike"], opt_type, spot) if (parsed and spot > 0) else 0.0
        ratio    = ltp / avg if avg > 0 else 0.0

        pos_tbl.add_row(
            sym, opt_type, str(qty),
            f"{otm*100:.2f}%",
            f"Rs.{avg:.1f}",
            f"Rs.{ltp:.1f}",
            Text(f"Rs.{pnl_v:+,.0f}", style="green" if pnl_v >= 0 else "red"),
            f"{ratio:.2f}x",
            _risk_flag(otm, ratio),
        )

    if not open_pos:
        pos_tbl.add_row("[dim]No open positions[/dim]", "", "", "", "", "", "", "", "")

    # Trades today
    trd_tbl = Table(box=box.SIMPLE, expand=True, show_header=True)
    trd_tbl.add_column("Time",   width=7)
    trd_tbl.add_column("Symbol", style="cyan", ratio=1)
    trd_tbl.add_column("Act",    width=5)
    trd_tbl.add_column("Qty",    width=6,  justify="right")
    trd_tbl.add_column("Price",  width=9,  justify="right")

    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    today_trades = [
        t for t in trades
        if today_str in str(
            t.get("timestamp") or t.get("order_timestamp") or t.get("trade_timestamp", "")
        )
    ]
    for t in today_trades[-8:]:
        ts_raw = t.get("timestamp") or t.get("order_timestamp") or t.get("trade_timestamp", "")
        action = t.get("action", t.get("side", t.get("transactiontype", "")))
        price  = float(t.get("price", t.get("average_price", t.get("fill_price", 0))))
        trd_tbl.add_row(
            _ts_str(str(ts_raw)),
            t.get("symbol", ""),
            Text(action, style="green" if action in ("BUY", "B") else "red"),
            str(t.get("quantity", "")),
            f"Rs.{price:.2f}",
        )
    if not today_trades:
        trd_tbl.add_row("[dim]No fills today[/dim]", "", "", "", "")

    # LLM Decisions timeline
    _AC = {
        "HOLD":         "dim white",
        "PARTIAL_EXIT": "yellow",
        "FULL_EXIT":    "bold red",
        "SHIFT_STRIKE": "blue",
        "ADD_POSITION": "cyan",
        "HEDGE_DELTA":  "magenta",
    }
    dec_tbl = Table(box=box.SIMPLE, expand=True, show_header=True)
    dec_tbl.add_column("Time",      width=6)
    dec_tbl.add_column("Action",    width=14)
    dec_tbl.add_column("Spot",      width=7,  justify="right")
    dec_tbl.add_column("P&L",       width=9,  justify="right")
    dec_tbl.add_column("X",         width=2)
    dec_tbl.add_column("Reasoning", ratio=1)

    for rec in decisions[:22]:
        ctx    = rec.get("context_summary", {})
        dec    = rec.get("decision", {})
        action = dec.get("action", "?")
        spot_v = ctx.get("underlying_price", 0)
        pnl_v  = ctx.get("pnl", 0)
        exec_v = "v" if rec.get("executed") else "."
        reason = (dec.get("reasoning") or "")[:72]
        source = rec.get("decision_source", "")

        if source == "rules":
            action_label = Text(f"[R]{action}", style=_AC.get(action, "white"))
        else:
            action_label = Text(action, style=_AC.get(action, "white"))

        dec_tbl.add_row(
            _ts_str(rec.get("ts", "")),
            action_label,
            f"{spot_v:,.0f}" if spot_v else "-",
            Text(f"Rs.{pnl_v:+,.0f}", style="green" if pnl_v >= 0 else "red"),
            exec_v,
            reason,
        )
    if not decisions:
        dec_tbl.add_row("[dim]No decisions yet[/dim]", "", "", "", "", "")

    # Assemble layout
    layout = Layout()
    layout.split_column(
        Layout(Panel(header, border_style="blue"), size=3),
        Layout(name="body"),
    )
    layout["body"].split_row(
        Layout(name="left", ratio=3),
        Layout(name="right", ratio=2),
    )
    layout["left"].split_column(
        Layout(Panel(pos_tbl, title=f"[bold]Open Positions[/]  spot={spot:,.0f}", border_style="green"), ratio=3),
        Layout(Panel(trd_tbl, title="[bold]Fills Today[/]", border_style="dim"), ratio=2),
    )
    layout["right"].update(
        Panel(dec_tbl, title=f"[bold]Agent Decisions  ({len(decisions)} today)[/]", border_style="cyan")
    )
    return layout


def main():
    ap = argparse.ArgumentParser(description="Live paper trade dashboard")
    ap.add_argument("--underlying", default="NIFTY",   help="NIFTY or BANKNIFTY")
    ap.add_argument("--target",     type=float, default=6000.0,  help="Session profit target INR")
    ap.add_argument("--max-loss",   type=float, default=-8000.0, help="Session floor INR (negative)")
    ap.add_argument("--refresh",    type=int,   default=60,      help="Refresh interval seconds")
    args = ap.parse_args()

    api_key = os.environ.get("OPENALGO_API_KEY") or os.environ.get("OPENALGO_KEY", "")
    if not api_key:
        print("ERROR: set OPENALGO_API_KEY env var")
        sys.exit(1)

    console = Console()
    console.print(
        f"[bold blue]Paper Dashboard[/] -- {args.underlying}  "
        f"target Rs.{args.target:,.0f}  floor Rs.{args.max_loss:,.0f}  "
        f"refresh {args.refresh}s   Ctrl+C to quit"
    )

    with Live(console=console, refresh_per_second=0.5, screen=True) as live:
        while True:
            try:
                live.update(_build_layout(api_key, args.underlying, args.target, args.max_loss))
            except KeyboardInterrupt:
                break
            except Exception as exc:
                live.update(Panel(f"[red]Error: {exc}[/]  -- retrying in {args.refresh}s"))
            try:
                time.sleep(args.refresh)
            except KeyboardInterrupt:
                break


if __name__ == "__main__":
    main()
