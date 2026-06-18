#!/usr/bin/env python3
"""
web_dashboard.py — Browser-accessible trading dashboard.

Serves a live HTML page at http://<VM_IP>:8080
Auto-refreshes every 30 seconds via JavaScript.

HOW TO RUN:
  cd ~/autotrade
  OPENALGO_API_KEY=<key> /home/freed/openalgo/.venv/bin/python3 agents/web_dashboard.py

Then open in browser: http://34.45.46.60:8080
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests
from functools import wraps
from flask import Flask, jsonify, render_template_string, request, session, redirect

IST = timezone(timedelta(hours=5, minutes=30))
OPENALGO_BASE = "http://localhost:5000"
LOG_DIR = Path("/home/freed/autotrade/data/decision_logs")
PORT = 8080

_OPT_RE = re.compile(r"^([A-Z]+?)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-fallback-change-me")
_DASHBOARD_PW  = os.environ.get("DASHBOARD_PASSWORD", "trade2026")

_NAV_BAR = """
<nav style="background:#12131a;border-bottom:1px solid #313244;padding:0 20px;display:flex;align-items:center;gap:0;position:sticky;top:0;z-index:100">
  <span style="font-weight:800;color:#89b4fa;font-size:14px;letter-spacing:.5px;padding:12px 20px 12px 0;border-right:1px solid #313244;margin-right:4px">&#9889; AutoTrade</span>
  <a href="/" style="padding:12px 16px;font-size:12px;text-decoration:none;color:#cdd6f4;border-bottom:2px solid transparent" id="nav-trading">&#128200; Trading</a>
  <a href="/screener" style="padding:12px 16px;font-size:12px;text-decoration:none;color:#cdd6f4;border-bottom:2px solid transparent" id="nav-screener">&#128269; Screener</a>
  <a href="/strategies" style="padding:12px 16px;font-size:12px;text-decoration:none;color:#cdd6f4;border-bottom:2px solid transparent" id="nav-strategies">&#127919; Strategies</a>
  <span style="margin-left:auto;font-size:11px;color:#6c7086;padding:12px 0">
    <a href="/logout" style="color:#f38ba8;text-decoration:none;font-size:11px">Logout</a>
  </span>
</nav>
<script>
(function(){{
  const path = window.location.pathname;
  const map = {{'/':'nav-trading','/screener':'nav-screener','/strategies':'nav-strategies'}};
  const id = map[path] || (path.startsWith('/screener') ? 'nav-screener' : null);
  if (id) {{ const el = document.getElementById(id); if(el) el.style.cssText += ';color:#89b4fa;border-bottom-color:#89b4fa;font-weight:600'; }}
}})();
</script>
"""

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoTrade Login</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#11111b;color:#cdd6f4;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       display:flex;align-items:center;justify-content:center;min-height:100vh}}
  .box{{background:#181b24;border:1px solid #313244;border-radius:12px;padding:40px 36px;width:340px}}
  h1{{font-size:22px;font-weight:800;color:#89b4fa;margin-bottom:4px}}
  .sub{{font-size:12px;color:#6c7086;margin-bottom:28px}}
  label{{font-size:11px;color:#a6adc8;text-transform:uppercase;letter-spacing:.8px;display:block;margin-bottom:6px}}
  input[type=password]{{width:100%;background:#11111b;border:1px solid #313244;color:#cdd6f4;
    border-radius:6px;padding:10px 12px;font-size:14px;outline:none;margin-bottom:20px}}
  input[type=password]:focus{{border-color:#89b4fa}}
  button{{width:100%;background:#89b4fa;color:#11111b;border:none;border-radius:6px;
    padding:11px;font-size:14px;font-weight:700;cursor:pointer}}
  button:hover{{background:#b4d0f7}}
  .err{{background:#f38ba822;border:1px solid #f38ba8;color:#f38ba8;border-radius:6px;
    padding:8px 12px;font-size:12px;margin-bottom:16px}}
  .status{{font-size:11px;color:#6c7086;margin-top:16px;text-align:center}}
  .dot{{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px;vertical-align:middle}}
  .dot.ok{{background:#a6e3a1}}.dot.err{{background:#f38ba8}}
</style>
</head>
<body>
<div class="box">
  <h1>&#9889; AutoTrade</h1>
  <div class="sub">Trading Platform &middot; {date}</div>
  {error_html}
  <form method="POST">
    <label>Password</label>
    <input type="password" name="pw" autofocus placeholder="Enter password">
    <button type="submit">Login</button>
  </form>
  <div class="status">
    <span class="dot {oa_cls}"></span>OpenAlgo: {oa_status}
  </div>
</div>
</body>
</html>"""


def login_required(f):
    @wraps(f)
    def _wrap(*args, **kwargs):
        today = datetime.now(IST).strftime("%Y-%m-%d")
        if session.get("auth_date") != today:
            return redirect("/login")
        return f(*args, **kwargs)
    return _wrap

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Dashboard — {{ underlying }}</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d0f14; color: #cdd6f4; font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace; font-size: 13px; }
  a { color: inherit; text-decoration: none; }

  .header {
    background: #181b24;
    border-bottom: 1px solid #313244;
    padding: 10px 20px;
    display: flex;
    align-items: center;
    gap: 24px;
    flex-wrap: wrap;
  }
  .header .title { font-size: 16px; font-weight: bold; color: #89b4fa; letter-spacing: 1px; }
  .header .stat { display: flex; flex-direction: column; }
  .header .stat .label { font-size: 10px; color: #6c7086; text-transform: uppercase; }
  .header .stat .value { font-size: 14px; font-weight: bold; }
  .pnl-pos { color: #a6e3a1; }
  .pnl-neg { color: #f38ba8; }
  .spot-val { color: #fab387; }

  .progress-bar {
    height: 6px; background: #313244; border-radius: 3px; width: 180px; margin-top: 4px;
  }
  .progress-fill { height: 100%; border-radius: 3px; transition: width 0.5s; }
  .progress-fill.pos { background: #a6e3a1; }
  .progress-fill.neg { background: #f38ba8; }

  .refresh-info { margin-left: auto; font-size: 11px; color: #6c7086; }
  #countdown { color: #89b4fa; font-weight: bold; }

  .main { display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: auto auto; gap: 12px; padding: 12px; }
  @media (max-width: 1100px) { .main { grid-template-columns: 1fr; } }

  .card {
    background: #181b24;
    border: 1px solid #313244;
    border-radius: 8px;
    overflow: hidden;
  }
  .card-header {
    padding: 8px 14px;
    border-bottom: 1px solid #313244;
    font-size: 11px;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #89b4fa;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .card-header .badge {
    background: #313244;
    color: #cdd6f4;
    padding: 1px 6px;
    border-radius: 10px;
    font-size: 10px;
    font-weight: normal;
  }
  /* Collapsible cards */
  .card-header.collapsible { cursor: pointer; user-select: none; justify-content: space-between; }
  .card-header.collapsible:hover { background: rgba(137,180,250,0.04); }
  .chevron { font-size: 12px; color: #6c7086; transition: transform 0.2s; }
  .card.collapsed .chevron { transform: rotate(-90deg); }
  .card-body { }
  .card.collapsed .card-body { display: none; }
  /* Scrollable table containers */
  .card-body.scrollable { max-height: 300px; overflow-y: auto; }
  .card-body.scrollable::-webkit-scrollbar { width: 4px; }
  .card-body.scrollable::-webkit-scrollbar-track { background: #1e2030; }
  .card-body.scrollable::-webkit-scrollbar-thumb { background: #45475a; border-radius: 2px; }
  /* Session memory */
  .sm-section { padding: 10px 14px; border-bottom: 1px solid #1e2030; }
  .sm-section:last-child { border-bottom: none; }
  .sm-label { font-size: 10px; color: #89b4fa; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }

  table { width: 100%; border-collapse: collapse; }
  th {
    padding: 6px 10px;
    text-align: left;
    color: #6c7086;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid #1e2030;
    white-space: nowrap;
  }
  th.r, td.r { text-align: right; }
  td { padding: 6px 10px; border-bottom: 1px solid #1e2030; vertical-align: middle; white-space: nowrap; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1e2030; }

  .sym { color: #89dceb; font-size: 12px; }
  .opt-ce { color: #89b4fa; }
  .opt-pe { color: #cba6f7; }
  .empty { color: #45475a; font-style: italic; padding: 14px 10px; }

  /* Risk flags */
  .flag-crit { color: #f38ba8; font-weight: bold; animation: blink 1s step-end infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  .flag-danger { color: #fab387; font-weight: bold; }
  .flag-warn { color: #f9e2af; }
  .flag-ok { color: #a6e3a1; }
  .flag-doubled { color: #cba6f7; font-weight: bold; }

  /* Actions */
  .act-hold { color: #6c7086; }
  .act-exit { color: #f38ba8; font-weight: bold; }
  .act-partial { color: #fab387; }
  .act-shift { color: #89b4fa; }
  .act-add { color: #89dceb; }
  .act-hedge { color: #cba6f7; }

  .source-rules { color: #f9e2af; font-size: 10px; }
  .source-llm { color: #6c7086; font-size: 10px; }
  .exec-yes { color: #a6e3a1; }
  .exec-no { color: #45475a; }

  .reasoning { color: #9399b2; white-space: normal; word-break: break-word; min-width: 200px; }

  /* Decisions panel spans full width */
  .full-width { grid-column: 1 / -1; }
</style>
</head>
<body>

<nav style="background:#12131a;border-bottom:1px solid #313244;padding:0 20px;display:flex;align-items:center;gap:0;position:sticky;top:0;z-index:100">
  <span style="font-weight:800;color:#89b4fa;font-size:14px;letter-spacing:.5px;padding:12px 20px 12px 0;border-right:1px solid #313244;margin-right:4px">&#9889; AutoTrade</span>
  <a href="/" style="padding:12px 16px;font-size:12px;text-decoration:none;color:#cdd6f4;border-bottom:2px solid transparent" id="nav-trading">&#128200; Trading</a>
  <a href="/screener" style="padding:12px 16px;font-size:12px;text-decoration:none;color:#cdd6f4;border-bottom:2px solid transparent" id="nav-screener">&#128269; Screener</a>
  <a href="/strategies" style="padding:12px 16px;font-size:12px;text-decoration:none;color:#cdd6f4;border-bottom:2px solid transparent" id="nav-strategies">&#127919; Strategies</a>
  <span style="margin-left:auto;font-size:11px;color:#6c7086;padding:12px 0">
    <a href="/logout" style="color:#f38ba8;text-decoration:none;font-size:11px">Logout</a>
  </span>
</nav>
<script>
(function(){
  const path = window.location.pathname;
  const map = {'/':'nav-trading','/screener':'nav-screener','/strategies':'nav-strategies'};
  const id = map[path] || (path.startsWith('/screener') ? 'nav-screener' : null);
  if (id) { const el = document.getElementById(id); if(el) el.style.cssText += ';color:#89b4fa;border-bottom-color:#89b4fa;font-weight:600'; }
})();
</script>

<div class="header">
  <div class="title">&#9670; TRADING DASHBOARD</div>

  <div class="stat">
    <span class="label">Strategy</span>
    <span class="value" style="color:#f9e2af" id="h-strategy">—</span>
  </div>
  <div class="stat">
    <span class="label">Underlying</span>
    <span class="value spot-val" id="h-underlying">—</span>
  </div>
  <div class="stat">
    <span class="label">Spot</span>
    <span class="value spot-val" id="h-spot">—</span>
  </div>
  <div class="stat">
    <span class="label">Entry Spot</span>
    <span class="value" id="h-entry" style="font-size:13px">—</span>
  </div>
  <div class="stat">
    <span class="label" id="pnl-label">Net P&L</span>
    <span class="value" id="h-pnl">—</span>
    <div class="progress-bar"><div class="progress-fill" id="h-bar" style="width:0%"></div></div>
  </div>
  <div class="stat">
    <span class="label">Target / Floor</span>
    <span class="value" id="h-goal">—</span>
  </div>
  <div class="stat">
    <span class="label">VIX</span>
    <span class="value" id="h-vix">—</span>
  </div>
  <div class="stat">
    <span class="label">PCR</span>
    <span class="value" id="h-pcr">—</span>
  </div>
  <div class="stat">
    <span class="label">Last Update</span>
    <span class="value" id="h-ts" style="font-size:12px">—</span>
  </div>
  <div class="refresh-info">
    <a href="/strategies" style="color:#6c7086;text-decoration:none;font-size:11px;padding:4px 10px;border:1px solid #313244;border-radius:4px;margin-right:12px;">Strategies</a>Auto-refresh in <span id="countdown">30</span>s &nbsp;
    <button onclick="loadData()" style="background:#313244;border:none;color:#cdd6f4;padding:3px 10px;border-radius:4px;cursor:pointer;font-size:11px">Refresh now</button>
  </div>
</div>

<!-- Session navigation bar -->
<div id="session-nav" style="background:#181b24;padding:6px 20px;border-bottom:1px solid #313244;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
  <span style="color:#6c7086;font-size:11px;text-transform:uppercase;letter-spacing:1px">Sessions:</span>
  <div id="date-links" style="display:flex;gap:6px;flex-wrap:wrap;"></div>
</div>

<div class="main">

  <!-- Open Positions -->
  <div class="card" id="card-positions">
    <div class="card-header collapsible" onclick="toggleCard('card-positions')">
      <span>&#9679; Open Positions <span class="badge" id="pos-count">0</span></span>
      <span class="chevron">&#9660;</span>
    </div>
    <div class="card-body scrollable"><table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th class="r">Type</th>
          <th class="r">Qty</th>
          <th class="r">OTM%</th>
          <th class="r">Sell@</th>
          <th class="r">LTP</th>
          <th class="r">P&L</th>
          <th class="r">Ratio</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody id="pos-body">
        <tr><td class="empty" colspan="8">Loading…</td></tr>
      </tbody>
    </table></div>
  </div>

  <!-- Fills Today -->
  <div class="card" id="card-fills">
    <div class="card-header collapsible" onclick="toggleCard('card-fills')">
      <span>&#10003; Fills Today <span class="badge" id="fills-count">0</span> <span style="font-size:11px;color:#a6e3a1;font-weight:normal" id="session-pnl-label"></span></span>
      <span class="chevron">&#9660;</span>
    </div>
    <div class="card-body scrollable"><table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Symbol</th>
          <th>Action</th>
          <th class="r">Qty</th>
          <th class="r">Price</th>
          <th class="r">P&amp;L</th>
          <th class="r">Dev%</th>
        </tr>
      </thead>
      <tbody id="fills-body">
        <tr><td class="empty" colspan="7">No fills today</td></tr>
      </tbody>
    </table></div>
  </div>


  <!-- Agent Decisions — full width -->
  <div class="card full-width" id="card-decisions">
    <div class="card-header collapsible" onclick="toggleCard('card-decisions')">
      <span>&#9889; Agent Decisions <span class="badge" id="dec-count">0</span> <span style="font-size:10px;color:#6c7086;font-weight:normal;margin-left:4px">[R]=rule &nbsp; LLM=model</span></span>
      <span class="chevron">&#9660;</span>
    </div>
    <div class="card-body scrollable"><table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Action</th>
          <th>Src</th>
          <th class="r">Spot</th>
          <th class="r">P&L</th>
          <th>X</th>
          <th>Reasoning</th>
        </tr>
      </thead>
      <tbody id="dec-body">
        <tr><td class="empty" colspan="7">No decisions yet today</td></tr>
      </tbody>
    </table></div>
  </div>

  <!-- Payoff Chart -->
  <div class="card full-width" id="payoff-card">
    <div class="card-header collapsible" onclick="toggleCard('payoff-card')">
      <span>&#128200; Strategy Payoff <span id="payoff-meta" style="font-size:11px;color:#6c7086;font-weight:normal;margin-left:8px"></span></span>
      <span class="chevron">&#9660;</span>
    </div>
    <div class="card-body">
      <div style="position:relative;height:320px;padding:8px 4px 4px 4px">
        <canvas id="payoff-chart"></canvas>
      </div>
    </div>
  </div>

  <!-- Session Memory — LLM context block -->
  <div class="card full-width" id="card-session-memory">
    <div class="card-header collapsible" onclick="toggleCard('card-session-memory')">
      <span>&#129504; LLM Session Memory <span style="font-size:10px;color:#6c7086;font-weight:normal;margin-left:4px">what the LLM knows about today</span></span>
      <span class="chevron">&#9660;</span>
    </div>
    <div class="card-body">
      <div class="sm-section" id="sm-header-section">
        <div class="sm-label">Session Header</div>
        <div id="sm-header" style="font-size:11px;color:#6c7086">Loading...</div>
      </div>
      <div class="sm-section" id="sm-chapters-section" style="display:none">
        <div class="sm-label">Compressed History</div>
        <div id="sm-chapters" style="font-size:11px;color:#9399b2;line-height:1.8"></div>
      </div>
      <div class="sm-section">
        <div class="sm-label">Recent Decisions (full detail passed to LLM)</div>
        <div class="card-body scrollable" style="max-height:220px">
          <table>
            <thead><tr><th>Time</th><th>Action</th><th class="r">Spot</th><th class="r">P&amp;L</th><th>Reasoning</th></tr></thead>
            <tbody id="sm-recent-body"><tr><td class="empty" colspan="5">No decisions yet</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>


</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
const urlParams      = new URLSearchParams(window.location.search);
const viewDate       = urlParams.get('date') || '';
const viewUnderlying = urlParams.get('underlying') || '';
const viewStrategyId = urlParams.get('strategy_id') || '';
const _qParts = [];
if (viewDate)       _qParts.push('date='        + viewDate);
if (viewUnderlying) _qParts.push('underlying='  + viewUnderlying);
if (viewStrategyId) _qParts.push('strategy_id=' + viewStrategyId);
const API = '/api/data' + (_qParts.length ? '?' + _qParts.join('&') : '');
let timer = 30;

function pnlClass(v) { return v >= 0 ? 'pnl-pos' : 'pnl-neg'; }
function fmt(v) { return (v >= 0 ? '+' : '') + Math.round(v).toLocaleString('en-IN'); }

function flagHtml(otm, ratio) {
  let s = '';
  if (otm !== null) {
    if (otm < 0.005)       s += '<span class="flag-crit">!! CRIT</span>';
    else if (otm < 0.010)  s += '<span class="flag-danger">! DANGER</span>';
    else if (otm < 0.015)  s += '<span class="flag-warn">~ WARN</span>';
    else                   s += '<span class="flag-ok">OK</span>';
  }
  if (ratio !== null && ratio >= 2.0) s += ' <span class="flag-doubled">DOUBLED</span>';
  return s;
}

function actionHtml(action, source) {
  const map = {
    'HOLD': 'act-hold',
    'FULL_EXIT': 'act-exit',
    'PARTIAL_EXIT': 'act-partial',
    'SHIFT_STRIKE': 'act-shift',
    'ADD_POSITION': 'act-add',
    'HEDGE_DELTA': 'act-hedge',
  };
  const cls = map[action] || '';
  const prefix = source === 'rules' ? '<span class="source-rules">[R]</span> ' : '';
  return prefix + `<span class="${cls}">${action}</span>`;
}

function tsShort(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString('en-IN', {hour:'2-digit', minute:'2-digit', timeZone:'Asia/Kolkata'});
  } catch(e) { return ts.slice(-8, -3); }
}

// ── Payoff chart ──────────────────────────────────────────────────────────
let payoffChart = null;

const vLinePlugin = {
  id: 'vline',
  afterDraw(chart) {
    const lines = chart.config.options._vlines || [];
    lines.forEach(vl => {
      const ctx  = chart.ctx;
      const xAx  = chart.scales.x;
      const yAx  = chart.scales.y;
      const x    = xAx.getPixelForValue(vl.value);
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(x, yAx.top);
      ctx.lineTo(x, yAx.bottom);
      ctx.lineWidth   = vl.width || 1;
      ctx.strokeStyle = vl.color || '#888';
      ctx.setLineDash(vl.dash || []);
      ctx.stroke();
      if (vl.label) {
        ctx.fillStyle = vl.color || '#888';
        ctx.font      = '10px monospace';
        ctx.fillText(vl.label, x + 3, yAx.top + 12);
      }
      ctx.restore();
    });
  }
};
Chart.register(vLinePlugin);

function renderPayoff(d) {
  const canvas = document.getElementById('payoff-chart');
  if (!canvas || !d.spots || d.spots.length === 0) return;

  const cur    = d.current_spot;
  const maxPt  = d.max_profit_spot;
  const beDown = d.breakeven_down;
  const beUp   = d.breakeven_up;
  const pct    = d.pct_from_center;
  const pctStr = pct >= 0 ? '+' + pct.toFixed(2) + '%' : pct.toFixed(2) + '%';

  // Meta bar
  const meta = document.getElementById('payoff-meta');
  if (meta) {
    const ivStr = (d.legs_iv || []).map(l => l.sym.slice(-10) + ' IV:' + l.iv_pct + '%').join('  ');
    // Order: BE↓ (blue) | Center (green) | BE↑ (red) | Drift% (grey) | IV
    const beDownStr = beDown ? '<span style="color:#89b4fa">BE\u2193 ' + beDown.toLocaleString('en-IN') + '</span>  ' : '';
    const centerStr = '<span style="color:#a6e3a1">Center ' + (maxPt || '\u2014').toLocaleString('en-IN') + '</span>  ';
    const beUpStr   = beUp   ? '<span style="color:#f38ba8">BE\u2191 ' + beUp.toLocaleString('en-IN') + '</span>  ' : '';
    const driftStr  = '<span style="color:#a6adc8">Drift ' + pctStr + '</span>';
    const ivHtml    = ivStr ? '  <span style="color:#6c7086">| ' + ivStr + '</span>' : '';
    const spotStr   = cur   ? '<span style="color:#f9e2af">Spot ' + cur.toLocaleString('en-IN') + '</span>  ' : '';
    meta.innerHTML = beDownStr + centerStr + spotStr + beUpStr + driftStr + ivHtml;
  }

  // Convert flat arrays to {x, y} for linear x-axis (fixes index 0-80 display)
  const expiryData = d.spots.map((s, i) => ({ x: s, y: d.expiry_pnl[i] }));
  const todayData  = d.spots.map((s, i) => ({ x: s, y: d.today_pnl[i]  }));

  // Current-spot dot: find closest index in spots array
  const curIdx  = d.spots.reduce((b, s, i) => Math.abs(s - cur) < Math.abs(d.spots[b] - cur) ? i : b, 0);
  const curDotY = d.today_pnl[curIdx];

  const vlines = [
    { value: cur,   color: '#f38ba8', width: 2,   dash: [],     label: 'Now' },
    { value: maxPt, color: '#a6e3a1', width: 1.5, dash: [5, 4], label: 'Peak' },
  ];
  if (beDown) vlines.push({ value: beDown, color: '#fab387', width: 1.5, dash: [3, 4], label: 'BE\u2193' });
  if (beUp)   vlines.push({ value: beUp,   color: '#fab387', width: 1.5, dash: [3, 4], label: 'BE\u2191' });
  if (d.restore_level) vlines.push({ value: d.restore_level, color: '#f9e2af', width: 1.5, dash: [6, 3], label: '+leg' });

  const xRange = d.spots[d.spots.length - 1] - d.spots[0];

  const cfg = {
    type: 'line',
    data: {
      datasets: [
        {
          label: 'At Expiry',
          data: expiryData,
          borderColor: '#9399b2',
          backgroundColor: 'transparent',
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0,
          order: 2,
        },
        {
          label: 'Today (theoretical)',
          data: todayData,
          borderColor: '#89dceb',
          backgroundColor: 'rgba(137,220,235,0.12)',
          borderWidth: 2.5,
          borderDash: [8, 4],
          pointRadius: 0,
          fill: true,
          tension: 0.25,
          order: 1,
        },
        {
          label: 'Now',
          data: [{ x: cur, y: curDotY }],
          borderColor: '#f38ba8',
          backgroundColor: '#f38ba8',
          pointRadius: 7,
          pointHoverRadius: 9,
          pointStyle: 'circle',
          showLine: false,
          order: 0,
        },
      ],
    },
    options: {
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      _vlines: vlines,
      plugins: {
        legend: { labels: { color: '#cdd6f4', font: { size: 11 }, boxWidth: 16 } },
        tooltip: {
          callbacks: {
            title: ctx => 'Spot: ' + Number(ctx[0].parsed.x).toLocaleString('en-IN'),
            label: ctx => ctx.dataset.label + ': \u20b9' + Math.round(ctx.parsed.y).toLocaleString('en-IN'),
          },
        },
      },
      scales: {
        x: {
          type: 'linear',
          ticks: {
            color: '#6c7086', maxTicksLimit: 10, font: { size: 10 },
            callback: v => xRange > 20000 ? (v/1000).toFixed(0)+'k' : Number(v).toLocaleString('en-IN'),
          },
          grid: { color: '#313244' },
        },
        y: {
          ticks: {
            color: '#6c7086', font: { size: 10 },
            callback: v => '\u20b9' + Math.round(v).toLocaleString('en-IN'),
          },
          grid: {
            color: ctx => ctx.tick.value === 0 ? '#585b70' : '#313244',
            lineWidth: ctx => ctx.tick.value === 0 ? 1.5 : 1,
          },
        },
      },
    },
  };

  if (payoffChart) {
    payoffChart.data    = cfg.data;
    payoffChart.options = cfg.options;
    payoffChart.update('none');
  } else {
    payoffChart = new Chart(canvas, cfg);
  }
}

function toggleCard(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('collapsed');
}

function renderSessionMemory(d) {
  // Header
  const hdr = d.session_header || {};
  const hdrEl = document.getElementById('sm-header');
  if (hdrEl) {
    if (!hdr.started_at) {
      hdrEl.textContent = 'No active session today.';
    } else {
      const pos = (hdr.positions_at_entry || [])
        .map(p => `<span style="color:#a6e3a1">${(p.symbol||'').slice(-14)}</span> qty=${p.qty} avg=₹${p.avg_price}`)
        .join(' &nbsp;|&nbsp; ');
      hdrEl.innerHTML =
        `Started: <b>${hdr.started_at}</b> &nbsp;|&nbsp; ` +
        `Entry Spot: <b>${(hdr.entry_spot||0).toLocaleString('en-IN')}</b> &nbsp;|&nbsp; ` +
        `Target: <b>₹${(hdr.target_profit||0).toLocaleString()}</b> &nbsp;|&nbsp; ` +
        `Floor: <b>₹${(hdr.max_loss||0).toLocaleString()}</b> &nbsp;|&nbsp; ` +
        `Expiry: <b>${hdr.expiry||'—'}</b><br>` +
        `<span style="color:#6c7086">Positions at entry:</span> ${pos||'—'}`;
    }
  }
  // Chapters
  const chapters = d.session_chapters || [];
  const chapSection = document.getElementById('sm-chapters-section');
  const chapEl = document.getElementById('sm-chapters');
  if (chapSection) chapSection.style.display = chapters.length ? '' : 'none';
  if (chapEl && chapters.length) {
    chapEl.innerHTML = chapters.map((ch, i) =>
      `<div style="padding:2px 0;border-bottom:1px solid #1e2030">${i+1}. ${ch}</div>`
    ).join('');
  }
  // Recent decisions
  const recent = d.recent_decisions || [];
  const tbody = document.getElementById('sm-recent-body');
  if (tbody) {
    if (!recent.length) {
      tbody.innerHTML = '<tr><td class="empty" colspan="5">No decisions yet today</td></tr>';
    } else {
      tbody.innerHTML = recent.map(r => {
        const ac = r.action === 'HOLD' ? '#6c7086' : (r.action === 'FULL_EXIT' ? '#f38ba8' : '#a6e3a1');
        const pnlCol = (r.pnl||0) >= 0 ? '#a6e3a1' : '#f38ba8';
        return `<tr>
          <td style="color:#6c7086">${r.time||''}</td>
          <td style="color:${ac};font-weight:600">${r.action||''}</td>
          <td class="r">${(r.spot||0).toLocaleString('en-IN')}</td>
          <td class="r" style="color:${pnlCol}">₹${(r.pnl||0).toLocaleString('en-IN')}</td>
          <td style="color:#9399b2;font-size:10px;max-width:320px">${r.why||''}</td>
        </tr>`;
      }).join('');
    }
  }
}

function loadSessionMemory() {
  const smq = new URLSearchParams();
  if (viewUnderlying) smq.set('underlying', viewUnderlying);
  if (viewStrategyId) smq.set('strategy_id', viewStrategyId);
  const smqs = smq.toString() ? '?' + smq.toString() : '';
  fetch('/api/session-memory' + smqs)
    .then(r => r.json())
    .then(d => renderSessionMemory(d))
    .catch(e => console.warn('session-memory:', e));
}

function loadPayoff() {
  const pqp = new URLSearchParams();
  if (viewUnderlying) pqp.set('underlying', viewUnderlying);
  if (viewStrategyId) pqp.set('strategy_id', viewStrategyId);
  fetch('/api/payoff?' + pqp.toString())
    .then(r => r.json())
    .then(d => renderPayoff(d))
    .catch(e => console.warn('payoff fetch:', e));
}

function _updateLlmBtn(enabled) {
  const btn = document.getElementById('llm-toggle-btn');
  if (!btn) return;
  btn.textContent = 'LLM ' + (enabled ? '●' : '○');
  btn.style.color = enabled ? '#a6e3a1' : '#f38ba8';
  btn.title = 'LLM is ' + (enabled ? 'ON — click to disable' : 'OFF — click to enable');
}
async function toggleLlm() {
  const resp = await fetch('/api/llm_toggle', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({underlying})
  }).then(r => r.json()).catch(() => null);
  if (resp) _updateLlmBtn(resp.llm_enabled);
}

function loadData() {
  fetch(API)
    .then(r => r.json())
    .then(d => render(d))
    .catch(e => console.error('fetch error', e));
  loadPayoff();
  loadSessionMemory();
  timer = 30;
}

function render(d) {
  const now = new Date().toLocaleTimeString('en-IN', {hour:'2-digit', minute:'2-digit', second:'2-digit', timeZone:'Asia/Kolkata'});

  // Header
  document.getElementById('h-strategy').textContent  = (d.strategy || '—').replace('_',' ').toUpperCase();
  document.getElementById('h-underlying').textContent = d.underlying || '—';
  if (d.entry_spot) {
    document.getElementById("h-entry").textContent = Math.round(d.entry_spot).toLocaleString("en-IN");
  }
  document.getElementById('h-spot').textContent       = d.spot > 0 ? d.spot.toLocaleString('en-IN') : '—';
  document.getElementById('h-vix').textContent        = d.vix ? d.vix.toFixed(1) : '—';
  document.getElementById('h-pcr').textContent        = d.pcr ? `${d.pcr.toFixed(2)} (${d.pcr_trend || ''})` : '—';
  document.getElementById('h-ts').textContent         = d.replay ? (d.hist_time || now) : now;

  const pnl = d.net_pnl || 0;
  const realized = d.realized_pnl || 0;
  const isToday  = d.is_today !== false;
  // If no open positions, net_pnl already = realized_pnl from server
  // Show "Session P&L" label when viewing a closed/historical session
  const pnlLabel = (!isToday || (d.positions && d.positions.length === 0 && realized !== 0))
    ? 'Session P&L' : 'Net P&L';
  document.getElementById('pnl-label').textContent    = pnlLabel;
  document.getElementById('h-pnl').textContent        = `Rs.${fmt(pnl)}`;
  document.getElementById('h-pnl').className          = pnlClass(pnl);
  document.getElementById('h-goal').textContent       = `Rs.${(d.target||0).toLocaleString()} / Rs.${(d.max_loss||0).toLocaleString()}`;
  if (!d.replay) _updateLlmBtn(d.llm_enabled !== false);

  // Replay progress banner
  let replayBanner = document.getElementById('replay-progress-banner');
  if (d.replay) {
    if (!replayBanner) {
      replayBanner = document.createElement('div');
      replayBanner.id = 'replay-progress-banner';
      replayBanner.style.cssText = 'background:#1e2a3a;border-bottom:1px solid #313244;padding:6px 20px;font-size:12px;display:flex;gap:24px;align-items:center;';
      document.querySelector('.topbar') && document.querySelector('.topbar').insertAdjacentElement('afterend', replayBanner);
    }
    const pct = d.total_bars > 0 ? Math.round(d.bar_num / d.total_bars * 100) : 0;
    replayBanner.innerHTML = `
      <span style="color:#89dceb;font-weight:600">⏮ REPLAY</span>
      <span style="color:#cdd6f4">${d.hist_date || ''}</span>
      <span style="color:#89b4fa">${d.hist_time || ''}</span>
      <span style="color:#6c7086">Bar ${d.bar_num || 0} / ${d.total_bars || 75}</span>
      <div style="flex:1;height:4px;background:#313244;border-radius:2px;max-width:200px">
        <div style="width:${pct}%;height:100%;background:#89dceb;border-radius:2px"></div>
      </div>
      <span style="color:#6c7086">${pct}% done</span>
    `;
    replayBanner.style.display = 'flex';
  } else if (replayBanner) {
    replayBanner.style.display = 'none';
  }

  const pct = d.target > 0 ? Math.min(Math.max(pnl / d.target, 0), 1) * 100 : 0;
  const bar = document.getElementById('h-bar');
  bar.style.width = pct + '%';
  bar.className = 'progress-fill ' + (pnl >= 0 ? 'pos' : 'neg');

  // Date navigation
  const dateLinks = document.getElementById('date-links');
  if (dateLinks && d.available_dates && d.available_dates.length) {
    const today = new Date().toISOString().slice(0,10);
    dateLinks.innerHTML = d.available_dates.map(dt => {
      const label = dt === today ? 'Today' : dt;
      const active = dt === d.date ? 'background:#45475a;' : '';
      const uSuffix = viewUnderlying ? `&underlying=${viewUnderlying}` : '';
      return `<a href="/?date=${dt}${uSuffix}" style="color:#89b4fa;font-size:11px;padding:2px 8px;background:#1e1e2e;border:1px solid #313244;border-radius:3px;text-decoration:none;${active}">${label}</a>`;
    }).join('');
  }

  // Fills (used by both fills table and session P&L label)
  const fills = d.fills || [];
  // Positions (declared early — needed for sessionPnlLabel check below)
  const positions = d.positions || [];
  var sessionPnlLabel = document.getElementById('session-pnl-label');
  // Only show Session P&L when all positions closed (open positions have their own P&L panel)
  if (sessionPnlLabel) {
    if (positions.length === 0 && realized !== 0) {
      sessionPnlLabel.textContent = 'Session P&L: Rs.' + fmt(realized);
    } else {
      sessionPnlLabel.textContent = '';
    }
  }
  document.getElementById('pos-count').textContent = positions.length;
  const pb = document.getElementById('pos-body');
  if (positions.length === 0) {
    pb.innerHTML = '<tr><td class="empty" colspan="8">No open positions</td></tr>';
  } else {
    const sortedPos = [...positions].sort((a,b) => {
      const order = {'CE':0,'PE':1};
      return (order[a.opt_type]??2) - (order[b.opt_type]??2);
    });
    pb.innerHTML = sortedPos.map(p => {
      const otmPct = p.otm_pct !== null ? (p.otm_pct * 100).toFixed(2) + '%' : '—';
      const ratio  = p.ratio !== null ? p.ratio.toFixed(2) + 'x' : '—';
      const typeCls = p.opt_type === 'CE' ? 'opt-ce' : (p.opt_type === 'PE' ? 'opt-pe' : '');
      return `<tr>
        <td class="sym">${p.symbol}</td>
        <td class="r ${typeCls}">${p.opt_type || '—'}</td>
        <td class="r">${p.qty}</td>
        <td class="r">${otmPct}</td>
        <td class="r">Rs.${(p.avg || 0).toFixed(1)}</td>
        <td class="r">Rs.${(p.ltp || 0).toFixed(1)}</td>
        <td class="r ${pnlClass(p.pnl)}">Rs.${fmt(p.pnl)}</td>
        <td class="r">${ratio}</td>
        <td>${flagHtml(p.otm_pct, p.ratio)}</td>
      </tr>`;
    }).join('');
  }

  // Fills
  // fills already declared above
  document.getElementById('fills-count').textContent = fills.length;
  const fb = document.getElementById('fills-body');
  if (fills.length === 0) {
    fb.innerHTML = '<tr><td class="empty" colspan="7">No fills today</td></tr>';
  } else {
    fb.innerHTML = fills.map(f => {
      const isSell = f.action === 'SELL';
      const actCls = isSell ? 'pnl-neg' : 'flag-ok';
      let pnlHtml = '', devHtml = '';
      if (!isSell && f.avg_price) {
        // BUY = close of a short: P&L = (entry - exit) × qty
        const pnl = (f.avg_price - f.price) * f.qty;
        const dev = ((f.price / f.avg_price) - 1) * 100;
        const pnlCls = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        const devCls = dev <= 0 ? 'pnl-pos' : 'pnl-neg';
        pnlHtml = `<span class="${pnlCls}">Rs.${Math.round(pnl).toLocaleString('en-IN')}</span>`;
        devHtml = `<span class="${devCls}">${dev >= 0 ? '+' : ''}${dev.toFixed(1)}%</span>`;
      }
      return `<tr>
        <td>${f.time}</td>
        <td class="sym">${f.symbol}</td>
        <td class="${actCls}">${f.action}</td>
        <td class="r">${f.qty}</td>
        <td class="r">Rs.${(f.price || 0).toFixed(2)}</td>
        <td class="r">${pnlHtml}</td>
        <td class="r">${devHtml}</td>
      </tr>`;
    }).join('');
  }

  // Decisions
  const decisions = d.decisions || [];
  document.getElementById('dec-count').textContent = decisions.length;
  const db = document.getElementById('dec-body');
  if (decisions.length === 0) {
    db.innerHTML = '<tr><td class="empty" colspan="7">No decisions yet today</td></tr>';
  } else {
    db.innerHTML = decisions.map(dec => {
      const execHtml = dec.executed
        ? '<span class="exec-yes">&#10003;</span>'
        : '<span class="exec-no">&#183;</span>';
      return `<tr>
        <td>${dec.time}</td>
        <td>${actionHtml(dec.action, dec.source)}</td>
        <td>${dec.source === 'rules' ? '<span class="source-rules">rule</span>' : '<span class="source-llm">llm</span>'}</td>
        <td class="r ${pnlClass(dec.spot)}" style="color:#fab387">${dec.spot > 0 ? dec.spot.toLocaleString('en-IN') : '—'}</td>
        <td class="r ${pnlClass(dec.pnl)}">Rs.${fmt(dec.pnl)}</td>
        <td style="text-align:center">${execHtml}</td>
        <td class="reasoning">${dec.reasoning}</td>
      </tr>`;
    }).join('');
  }
}

// Countdown + auto-refresh
setInterval(() => {
  timer--;
  document.getElementById('countdown').textContent = timer;
  if (timer <= 0) loadData();
}, 1000);

loadData();
</script>
</body>
</html>
"""

API_KEY = os.environ.get("OPENALGO_API_KEY", "")


def _parse_sym(symbol: str) -> Optional[dict]:
    m = _OPT_RE.match(symbol.upper())
    if not m:
        return None
    return {"underlying": m.group(1), "strike": int(m.group(5)), "opt_type": m.group(6)}


def _otm_pct(strike: int, opt_type: str, spot: float) -> Optional[float]:
    if spot <= 0:
        return None
    v = (strike - spot) / spot if opt_type == "CE" else (spot - strike) / spot
    return max(v, 0.0)


def _oa_headers() -> dict:
    return {"x-api-key": API_KEY, "Content-Type": "application/json"}


def _get_positions() -> list[dict]:
    try:
        r = requests.post(f"{OPENALGO_BASE}/api/v1/positionbook",
                          json={"apikey": API_KEY}, headers=_oa_headers(), timeout=8)
        r.raise_for_status()
        d = r.json()
        raw = d.get("data", d) if isinstance(d, dict) else d
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _get_tradebook() -> list[dict]:
    try:
        r = requests.post(f"{OPENALGO_BASE}/api/v1/tradebook", json={"apikey": API_KEY}, headers=_oa_headers(), timeout=8)
        r.raise_for_status()
        d = r.json()
        raw = d.get("data", d) if isinstance(d, dict) else d
        if isinstance(raw, dict):
            raw = raw.get("orders", raw.get("trades", []))
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


_INDEX_SYMBOLS = {
    "NIFTY":     ("NIFTY",     "NSE_INDEX"),
    "BANKNIFTY": ("BANKNIFTY", "NSE_INDEX"),
    "SENSEX":    ("SENSEX",    "BSE_INDEX"),
}

_MCX_UNDERLYINGS = {"GOLDM", "GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"}


def _get_spot_mcx(underlying: str) -> float:
    import sqlite3 as _sq
    try:
        conn = _sq.connect("/home/freed/openalgo/db/openalgo.db")
        row  = conn.execute(
            "SELECT symbol FROM symtoken WHERE exchange='MCX' AND symbol LIKE ? "
            "AND instrumenttype='FUT' ORDER BY expiry LIMIT 1",
            (f"{underlying}%",)
        ).fetchone()
        conn.close()
        if not row:
            return 0.0
        fut_sym = row[0]
        r = requests.post(
            f"{OPENALGO_BASE}/api/v1/quotes",
            json={"apikey": API_KEY, "symbol": fut_sym, "exchange": "MCX"},
            headers=_oa_headers(), timeout=8,
        )
        d = r.json()
        inner = d.get("data", d) if isinstance(d, dict) else {}
        return float(inner.get("ltp", d.get("ltp", 0)))
    except Exception:
        return 0.0


def _get_spot(underlying: str) -> float:
    if underlying in _MCX_UNDERLYINGS:
        return _get_spot_mcx(underlying)
    sym, exch = _INDEX_SYMBOLS.get(underlying, (underlying, "NSE_INDEX"))
    try:
        r = requests.post(
            f"{OPENALGO_BASE}/api/v1/quotes",
            json={"apikey": API_KEY, "symbol": sym, "exchange": exch},
            headers=_oa_headers(),
            timeout=8,
        )
        r.raise_for_status()
        d = r.json()
        inner = d.get("data", d) if isinstance(d, dict) else {}
        return float(inner.get("ltp", d.get("ltp", 0)))
    except Exception:
        return 0.0


def _read_decisions(n: int = 30) -> list[dict]:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    path = LOG_DIR / f"{today}.jsonl"
    if not path.exists():
        return []
    lines = [l for l in path.read_text().strip().splitlines() if l.strip()]
    records = []
    for line in lines[-n:]:
        try:
            records.append(json.loads(line))
        except Exception:
            pass
    return list(reversed(records))


def _available_dates(underlying: str = "NIFTY") -> list[str]:
    sym   = underlying.upper()
    files = sorted(LOG_DIR.glob(f"????-??-??-{sym}.jsonl"), reverse=True)
    dates = [f.name[:10] for f in files]  # "2026-06-09" from "2026-06-09-NIFTY.jsonl"
    # Fall back to old un-tagged files if no per-strategy files found
    if not dates:
        files = sorted(LOG_DIR.glob("????-??-??.jsonl"), reverse=True)
        dates = [f.stem for f in files]
    return dates


def _read_decisions_for_date(date_str: str, n: int = 60, underlying: str = "NIFTY",
                              strategy_id: str = None) -> list[dict]:
    sym  = underlying.upper()
    fpath = LOG_DIR / f"{date_str}-{sym}.jsonl"
    if not fpath.exists():
        fpath = LOG_DIR / f"{date_str}.jsonl"
    if not fpath.exists():
        return []
    lines = [l for l in fpath.read_text().strip().splitlines() if l.strip()]
    records = []
    for line in lines[-(n * 4):]:
        try:
            r = __import__('json').loads(line)
            g = r.get("goal", {})
            if g.get("underlying", sym).upper() != sym:
                continue
            if strategy_id and g.get("strategy_id") != strategy_id:
                continue
            records.append(r)
        except Exception:
            pass
    return list(reversed(records[-n:]))


def _session_realized_pnl_from_fills(fills: list[dict]) -> float:
    """Compute realized P&L from fill records: sum(SELL revenue) - sum(BUY cost)."""
    realized = 0.0
    for f in fills:
        price = float(f.get("price") or 0)
        qty   = int(f.get("qty") or 0)
        if f.get("action") in ("SELL", "S"):
            realized += price * qty
        elif f.get("action") in ("BUY", "B"):
            realized -= price * qty
    return realized


def _session_realized_pnl(date_str: str) -> float:
    return 0.0  # now computed inline from fills in api_data


def _ts_hhmm(raw: str) -> str:
    try:
        return datetime.fromisoformat(raw).strftime("%H:%M")
    except Exception:
        s = str(raw)
        return s[-8:-3] if len(s) >= 8 else s


def _strategy_config(underlying: str, strategy_id: str = None) -> dict:
    strats = _load_strategies()
    if strategy_id:
        match = next((s for s in strats if s.get("id") == strategy_id), None)
    else:
        match = next((s for s in strats if s.get("underlying") == underlying), None)
    if match:
        stype = match.get("strategy_type", "options")
        return {
            "target":        float(match.get("target",   6000)),
            "max_loss":      float(match.get("max_loss", -8000)),
            "strategy":      match.get("strategy", stype),
            "strategy_type": stype,
            "strategy_id":   match.get("id", strategy_id or "default"),
            "underlying":    match.get("underlying", underlying).upper(),
            "direction":     match.get("direction"),
            "qty":           match.get("qty"),
            "target_price":  match.get("target_price"),
            "stop_loss":     match.get("stop_loss_price"),
            "trailing_pct":  match.get("trailing_stop_pct"),
        }
    return {
        "target":        float(os.environ.get("DASHBOARD_TARGET",   "6000")),
        "max_loss":      float(os.environ.get("DASHBOARD_MAX_LOSS", "-8000")),
        "strategy":      os.environ.get("DASHBOARD_STRATEGY", "short_strangle"),
        "strategy_type": "options",
        "strategy_id":   strategy_id or "default",
        "underlying":    underlying.upper(),
    }




@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("pw","").strip() == _DASHBOARD_PW:
            session["auth_date"] = datetime.now(IST).strftime("%Y-%m-%d")
            return redirect(request.args.get("next", "/"))
        error = "Incorrect password."
    # Check OpenAlgo connectivity
    oa_ok = False
    try:
        r = requests.post(
            OPENALGO_BASE + "/api/v1/funds",
            json={"apikey": os.environ.get("OPENALGO_API_KEY","")},
            timeout=3,
        )
        oa_ok = r.status_code == 200 and r.json().get("status") == "success"
    except Exception:
        pass
    oa_status = "Connected ✓" if oa_ok else "Not authenticated"
    oa_cls    = "ok" if oa_ok else "err"
    error_html = f'<div class="err">{error}</div>' if error else ""
    html = _LOGIN_HTML.format(
        date=datetime.now(IST).strftime("%d %b %Y"),
        error_html=error_html,
        oa_status=oa_status,
        oa_cls=oa_cls,
    )
    return html


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/screener")
@login_required
def screener_page():
    import glob as _glob
    scr_dir = Path("/home/freed/autotrade/data/screener")
    files = sorted(_glob.glob(str(scr_dir / "*.html")), reverse=True)
    if not files:
        return '<meta http-equiv="refresh" content="0;url=/screener/generate">' +                '<p>No screener yet. <a href="/screener/generate">Generate now</a></p>'
    latest = files[0]
    content = open(latest).read()
    date_label = Path(latest).stem
    # Inject nav bar into the screener body
    content = content.replace("<body>", "<body>\n" + _NAV_BAR, 1)
    return content


@app.route("/screener/generate")
@login_required
def screener_generate():
    try:
        import sys as _sys
        agents_dir = Path(__file__).parent
        if str(agents_dir) not in _sys.path:
            _sys.path.insert(0, str(agents_dir))
        import screener_generator as _sg
        out = _sg.main()
        return redirect("/screener")
    except Exception as e:
        return f"<pre>Error: {e}</pre>", 500



@app.route("/")
@login_required
def index():
    underlying = (request.args.get("underlying")
                  or os.environ.get("DASHBOARD_UNDERLYING", "NIFTY"))
    return render_template_string(HTML, underlying=underlying)


@app.route("/api/data")
@login_required
def api_data():
    strategy_id = request.args.get("strategy_id")
    underlying  = (request.args.get("underlying")
                   or os.environ.get("DASHBOARD_UNDERLYING", "NIFTY"))
    cfg         = _strategy_config(underlying, strategy_id)
    underlying  = cfg.get("underlying", underlying)
    strategy_id = cfg.get("strategy_id", strategy_id or "default")
    target      = cfg["target"]
    max_loss    = cfg["max_loss"]
    strategy    = cfg["strategy"]

    # Replay sessions — serve from historical live_state
    if strategy_id.startswith("bt_"):
        return _api_data_replay(underlying, strategy_id, cfg)

    date_str   = request.args.get("date", datetime.now(IST).strftime("%Y-%m-%d"))
    is_today   = (date_str == datetime.now(IST).strftime("%Y-%m-%d"))
    spot       = _get_spot(underlying) if is_today else 0.0
    raw_pos    = _get_positions()      if is_today else []
    raw_trades = _get_tradebook()
    decisions  = _read_decisions_for_date(date_str, 200, underlying, strategy_id)

    # Filter decisions to current session only — exclude entries from earlier runs today
    try:
        import session_memory as _sm_flt
        _sm_hdr = _sm_flt._load(underlying, strategy_id).get("header", {})
        _sess_iso = _sm_hdr.get("started_at_iso", "")
        if _sess_iso:
            decisions = [d for d in decisions if d.get("ts", "") >= _sess_iso]
    except Exception:
        pass

    # Latest context from most recent decision (for VIX/PCR)
    vix = pcr = pcr_trend = None
    if decisions:
        latest_ctx = decisions[0].get("context_summary", {})
        vix       = latest_ctx.get("vix")
        pcr       = latest_ctx.get("pcr")
        pcr_trend = latest_ctx.get("pcr_trend")

    # Open positions — filter by owned_symbols for multi-strategy isolation
    _u = underlying.upper()
    try:
        import sys as _sys_w
        _ag = str(Path(__file__).parent)
        if _ag not in _sys_w.path: _sys_w.path.insert(0, _ag)
        import session_memory as _sm_w
        _owned = set(_sm_w.get_owned_symbols(underlying, strategy_id))
    except Exception:
        _owned = set()
    if _owned:
        open_pos = [p for p in raw_pos
                    if p.get("quantity", 0) != 0
                    and (p.get("symbol","") or p.get("tradingsymbol","")).upper() in _owned]
    else:
        open_pos = [p for p in raw_pos
                    if p.get("quantity", 0) != 0
                    and p.get("symbol", "").upper().startswith(_u)]
    unrealized_pnl = sum(float(p.get("pnl", 0)) for p in open_pos)
    # realized_pnl computed from fills below (after fills_out is built)

    positions_out = []
    for p in open_pos:
        sym    = p.get("symbol", "")
        qty    = p.get("quantity", 0)
        avg    = float(p.get("average_price", 0))
        ltp    = float(p.get("ltp", 0))
        pnl    = float(p.get("pnl", p.get("unrealized_pnl", 0)))
        parsed = _parse_sym(sym)
        opt_type = parsed["opt_type"] if parsed else None
        otm      = _otm_pct(parsed["strike"], opt_type, spot) if (parsed and spot > 0) else None
        ratio    = ltp / avg if avg > 0 else None
        positions_out.append({
            "symbol":   sym,
            "opt_type": opt_type,
            "qty":      qty,
            "avg":      avg,
            "ltp":      ltp,
            "pnl":      pnl,
            "otm_pct":  otm,
            "ratio":    ratio,
        })

    # Fills for the requested date (from tradebook)
    # Fills for the requested date (from tradebook)
    fills_raw = []
    for t in raw_trades:
        ts_raw = str(t.get("timestamp") or t.get("order_timestamp") or t.get("trade_timestamp") or "")
        if not ts_raw or date_str not in ts_raw:
            continue
        # Skip fills belonging to a different strategy
        if not t.get("symbol", "").upper().startswith(_u):
            continue
        action = t.get("action", t.get("side", t.get("transactiontype", "")))
        price  = float(t.get("price") or t.get("average_price") or t.get("fill_price") or 0)
        qty    = int(t.get("quantity") or 0)
        fills_raw.append((ts_raw, action, t.get("symbol",""), qty, price))
    # Sort oldest first (timestamps are "YYYY-MM-DD HH:MM:SS" so lexicographic works)
    fills_raw.sort(key=lambda x: x[0])
    fills_out = [
        {"time": _ts_hhmm(ts), "symbol": sym, "action": act, "qty": qty, "price": price}
        for ts, act, sym, qty, price in fills_raw
    ]
    # P&L: two modes depending on whether positions are still open
    _our_fill_syms = {sym.upper() for _, _, sym, _, _ in fills_raw}
    _closed_pos = [p for p in raw_pos
                   if int(p.get("quantity", 0) or 0) == 0
                   and (p.get("symbol","") or p.get("tradingsymbol","")).upper() in _our_fill_syms]
    if not open_pos and _closed_pos:
        # All positions closed (e.g. broker auto-sqoff at 15:20): trust broker pnl field
        realized_pnl = sum(float(p.get("pnl", 0)) for p in _closed_pos)
        net_pnl = realized_pnl
    else:
        # Mix of open/closed: fill-based realized for closed legs only
        _open_syms = {(p.get("symbol","") or p.get("tradingsymbol","")).upper() for p in open_pos}
        from collections import defaultdict as _dd
        _sym_net = _dd(float)
        for _, a, sym, q, p in fills_raw:
            _sym_net[sym.upper()] += (p * q if a == "SELL" else -p * q)
        realized_pnl = sum(v for sym, v in _sym_net.items() if sym not in _open_syms)
        net_pnl = unrealized_pnl + realized_pnl

    # Decisions
    decisions_out = []
    for rec in decisions:
        ctx    = rec.get("context_summary", {})
        dec    = rec.get("decision", {})
        decisions_out.append({
            "time":      _ts_hhmm(rec.get("ts", "")),
            "action":    dec.get("action", "?"),
            "source":    rec.get("decision_source", "llm"),
            "spot":      ctx.get("underlying_price", 0),
            "pnl":       ctx.get("pnl", 0),
            "executed":  rec.get("executed", False),
            "reasoning": (dec.get("reasoning") or ""),
        })

    # Entry spot = underlying_price minus move_pts from first log entry
    entry_spot = 0.0
    if decisions:
        first_ctx = decisions[-1].get("context_summary", {})
        up = first_ctx.get("underlying_price", 0)
        mv = first_ctx.get("underlying_move_pts", 0)
        if up:
            entry_spot = round(up - mv)

    return jsonify({
        "underlying":    underlying,
        "strategy":      strategy,
        "strategy_type": cfg.get("strategy_type", "options"),
        "direction":     cfg.get("direction"),
        "qty":           cfg.get("qty"),
        "target_price":  cfg.get("target_price"),
        "stop_loss":     cfg.get("stop_loss"),
        "trailing_pct":  cfg.get("trailing_pct"),
        "spot":          spot,
        "entry_spot": entry_spot,
        "net_pnl":       net_pnl,
        "realized_pnl":  realized_pnl,
        "is_today":      is_today,
        "date":          date_str,
        "available_dates": _available_dates(underlying),
        "target":     target,
        "max_loss":   max_loss,
        "vix":        vix,
        "pcr":        pcr,
        "pcr_trend":  pcr_trend,
        "strategy_id": strategy_id,
        "expiry":      cfg.get("expiry"),
        "positions":  positions_out,
        "fills":      fills_out,
        "decisions":  decisions_out,
    })


# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY HUB
# ══════════════════════════════════════════════════════════════════════════════

import subprocess as _sp

STRATEGIES_FILE = Path("/home/freed/autotrade/data/strategies.json")
AGENTS_DIR      = Path("/home/freed/autotrade/agents")
VENV_PYTHON     = "/home/freed/autotrade/.venv/bin/python3.12"
AUTOTRADE_DIR   = "/home/freed/autotrade"


def _load_strategies() -> list[dict]:
    if not STRATEGIES_FILE.exists():
        return []
    try:
        return json.loads(STRATEGIES_FILE.read_text())
    except Exception:
        return []


def _save_strategies(strategies: list[dict]) -> None:
    STRATEGIES_FILE.write_text(json.dumps(strategies, indent=2))


def _is_running(screen_name: str) -> bool:
    result = _sp.run(["screen", "-ls"], capture_output=True, text=True)
    return screen_name in result.stdout


def _strategy_today_pnl(strategy_id: str) -> float:
    """
    Today P&L for a strategy card:
    - Open positions exist → sum live position P&L from OpenAlgo (unrealized)
    - All closed → sum fills: SELL revenue minus BUY cost (realized)
    """
    strats     = _load_strategies()
    s          = next((x for x in strats if x["id"] == strategy_id), {})
    underlying = s.get("underlying", "").upper()
    if not underlying:
        return 0.0

    try:
        # ── Live P&L from open positions ─────────────────────────────────
        raw_pos  = _get_positions()
        open_pos = [
            p for p in raw_pos
            if int(p.get("quantity") or 0) != 0
            and p.get("symbol", "").upper().startswith(underlying)
        ]
        if open_pos:
            return sum(float(p.get("pnl", 0)) for p in open_pos)

        # ── Realized P&L from today's closed fills ────────────────────────
        date_str = datetime.now(IST).strftime("%Y-%m-%d")
        realized = 0.0
        for t in _get_tradebook():
            ts_raw = str(t.get("timestamp") or "")
            if date_str not in ts_raw:
                continue
            if not t.get("symbol", "").upper().startswith(underlying):
                continue
            action = t.get("action", "")
            price  = float(t.get("price") or t.get("average_price") or 0)
            qty    = int(t.get("quantity") or 0)
            if action == "SELL":
                realized += price * qty
            elif action == "BUY":
                realized -= price * qty
        return realized
    except Exception:
        return 0.0



@app.route("/api/session-memory")
@login_required
def api_session_memory():
    """Return today's session memory for the given underlying + strategy_id."""
    import sys, os
    underlying  = (request.args.get("underlying") or "NIFTY").upper()
    strategy_id = request.args.get("strategy_id") or None
    # Auto-resolve strategy_id from strategies.json if not provided in URL
    cfg = _strategy_config(underlying, strategy_id)
    strategy_id = cfg.get("strategy_id", "default")
    agents_dir  = os.path.dirname(os.path.abspath(__file__))
    if agents_dir not in sys.path:
        sys.path.insert(0, agents_dir)
    try:
        import session_memory as _sm
        block = _sm.get_context_block(underlying, strategy_id)
        return jsonify(block if block else {
            "session_header": {}, "session_chapters": [], "recent_decisions": []
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/payoff")
@login_required
def api_payoff():
    """Compute expiry + today theoretical P&L curve for open positions."""
    try:
        from opengreeks.black_scholes import black_scholes as _bs, implied_volatility as _iv_fn
    except ImportError:
        return jsonify({"error": "opengreeks not installed"}), 500

    underlying  = (request.args.get("underlying") or "NIFTY").upper()
    strategy_id = request.args.get("strategy_id", "default")
    # Replay sessions: read from live_state; use historical time for TTE
    replay_now = None
    if strategy_id.startswith("bt_"):
        try:
            import sys as _sys_pb; _sys_pb.path.insert(0, str(Path(__file__).parent))
            import session_memory as _sm_pb
            live_pb = _sm_pb.get_live_state(underlying, strategy_id)
            spot = live_pb.get("spot", 0.0)
            hdr_pb = _sm_pb._load(underlying, strategy_id).get("header", {})
            _entry_spot_ui = float(hdr_pb.get("entry_spot", 0) or 0)
            expiry_raw = live_pb.get("expiry") or hdr_pb.get("expiry", "")
            # Build historical datetime from live_state for accurate TTE
            hist_date = live_pb.get("hist_date", "")
            hist_time = live_pb.get("hist_time", "09:15 IST").replace(" IST", "")
            if hist_date and hist_time:
                try:
                    replay_now = datetime.strptime(
                        f"{hist_date} {hist_time}", "%Y-%m-%d %H:%M"
                    ).replace(tzinfo=None)
                except Exception:
                    replay_now = None
        except Exception:
            live_pb = {}; spot = 0.0; expiry_raw = ""
        if spot <= 0 or not live_pb.get("positions"):
            return jsonify({"spots": [], "expiry_pnl": [], "today_pnl": [], "legs_iv": []})
        open_pos = []
        for lp in live_pb.get("positions", []):
            if lp.get("qty", 0) == 0:
                continue
            open_pos.append({
                "symbol":        lp["symbol"],
                "quantity":      lp["qty"],
                "average_price": lp["avg"],
                "ltp":           lp["ltp"],
                "pnl":           lp["pnl"],
            })
        if not open_pos:
            return jsonify({"spots": [], "expiry_pnl": [], "today_pnl": [], "legs_iv": []})
        # Realized offset: net_pnl already includes realized from closed legs
        _ce_exit_spot_ui = live_pb.get("ce_exit_spot") or 0.0
        _pe_exit_spot_ui = live_pb.get("pe_exit_spot") or 0.0
        _net_pnl_rb   = live_pb.get("net_pnl", 0.0)
        _unrealized_rb = sum(float(p.get("pnl", 0)) for p in live_pb.get("positions", []) if p.get("qty", 0) != 0)
        realized_offset = _net_pnl_rb - _unrealized_rb
    else:
        spot = _get_spot(underlying)
        if spot <= 0:
            return jsonify({"error": "no spot"}), 400
        expiry_raw = None
        raw_pos = _get_positions()
        try:
            import sys as _sys_p; _sys_p.path.insert(0, str(Path(__file__).parent))
            import session_memory as _sm_p
            _owned_p = set(_sm_p.get_owned_symbols(underlying, strategy_id))
        except Exception:
            _owned_p = set()
        try:
            _hdr_live = _sm_p._load(underlying, strategy_id).get("header", {})
            _entry_spot_ui = float(_hdr_live.get("entry_spot", 0) or 0)
            _live_state_ui = _sm_p.get_live_state(underlying, strategy_id)
            _ce_exit_spot_ui = _live_state_ui.get("ce_exit_spot") or 0.0
            _pe_exit_spot_ui = _live_state_ui.get("pe_exit_spot") or 0.0
        except Exception:
            _entry_spot_ui = 0.0
            _ce_exit_spot_ui = 0.0
            _pe_exit_spot_ui = 0.0
        if _owned_p:
            open_pos = [p for p in raw_pos
                        if int(p.get("quantity") or 0) != 0
                        and (p.get("symbol") or "").upper() in _owned_p]
        else:
            open_pos = [p for p in raw_pos
                        if int(p.get("quantity") or 0) != 0
                        and (p.get("symbol") or "").upper().startswith(underlying)]
        if not open_pos:
            return jsonify({"spots": [], "expiry_pnl": [], "today_pnl": [], "legs_iv": []})
        # Realized offset: sum P&L of closed legs (symbols with fills but no open qty)
        _fills_pb = _get_fills(underlying)
        _open_syms_pb = {(p.get("symbol") or "").upper() for p in open_pos if int(p.get("quantity") or 0) != 0}
        _sym_pnl_pb = {}
        for t in _fills_pb:
            sym = (t.get("symbol") or "").upper()
            qty = abs(int(t.get("quantity") or 0))
            price = float(t.get("price") or 0)
            act = (t.get("action") or "").upper()
            _sym_pnl_pb[sym] = _sym_pnl_pb.get(sym, 0.0) + (price * qty if act == "SELL" else -price * qty)
        realized_offset = sum(v for sym, v in _sym_pnl_pb.items() if sym not in _open_syms_pb)

    R   = 0.065
    now = replay_now if replay_now is not None else datetime.now(IST).replace(tzinfo=None)
    _OP = re.compile(r"^([A-Z]+?)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$")
    # Replay symbols: NIFTY25OCT2325650CE = YY-MMM-DD; Live: NIFTY23OCT24000CE = DD-MMM-YY
    _sym_fmt     = "%y%b%d" if strategy_id.startswith("bt_") else "%d%b%y"
    _sym_fmt_alt = "%d%b%y" if strategy_id.startswith("bt_") else "%y%b%d"

    realized_offset  = locals().get("realized_offset", 0.0)  # set by live/replay branch above
    _entry_spot_ui   = locals().get("_entry_spot_ui", 0.0)
    _ce_exit_spot_ui = locals().get("_ce_exit_spot_ui", 0.0)
    _pe_exit_spot_ui = locals().get("_pe_exit_spot_ui", 0.0)
    legs = []
    for p in open_pos:
        sym = (p.get("symbol") or "").upper()
        m   = _OP.match(sym)
        if not m:
            continue
        qty = int(p.get("quantity") or 0)
        avg = float(p.get("average_price") or 0)
        ltp = float(p.get("ltp") or 0)
        if avg <= 0 or ltp <= 0:
            continue
        K    = int(m.group(5))
        flag = "c" if m.group(6) == "CE" else "p"
        try:
            exp_dt = datetime.strptime(f"{m.group(2)}{m.group(3)}{m.group(4)}", _sym_fmt)
            if (now - exp_dt).days > 180:
                exp_dt = datetime.strptime(f"{m.group(2)}{m.group(3)}{m.group(4)}", _sym_fmt_alt)
        except (ValueError, TypeError):
            continue
        T = max((exp_dt - now).total_seconds() / (365.25 * 24 * 3600), 1e-6)
        iv = 0.20
        try:
            iv = float(_iv_fn(ltp, spot, K, T, R, flag))
            iv = max(0.01, min(iv, 5.0))
        except Exception:
            pass
        legs.append({"qty": qty, "avg": avg, "K": K, "flag": flag, "T": T, "iv": iv, "sym": sym})

    if not legs:
        return jsonify({"spots": [], "expiry_pnl": [], "today_pnl": [], "legs_iv": []})

    N  = 100
    all_ks  = [lg["K"] for lg in legs]
    lo = min(spot * 0.93, min(all_ks) * 0.97)
    hi = max(spot * 1.07, max(all_ks) * 1.03)
    spots_f = [lo + (hi - lo) * i / (N - 1) for i in range(N)]

    expiry_pnl, today_pnl = [], []
    for S in spots_f:
        ep = tp = 0.0
        for lg in legs:
            qty, avg, K, flag, T, iv = lg["qty"], lg["avg"], lg["K"], lg["flag"], lg["T"], lg["iv"]
            intr = max(S - K, 0.0) if flag == "c" else max(K - S, 0.0)
            try:
                theo = max(float(_bs(flag, S, K, T, R, iv)), 0.0)
            except Exception:
                theo = intr
            if qty < 0:
                ep += (avg - intr) * abs(qty)
                tp += (avg - theo) * abs(qty)
            else:
                ep += (intr - avg) * abs(qty)
                tp += (theo - avg) * abs(qty)
        expiry_pnl.append(round(ep + realized_offset, 2))
        today_pnl.append(round(tp + realized_offset, 2))

    max_idx         = today_pnl.index(max(today_pnl))
    max_profit_spot = round(spots_f[max_idx])
    pct_from_center = round((spot - max_profit_spot) / max_profit_spot * 100, 3) if max_profit_spot else 0

    _crossings = []
    for i in range(len(today_pnl) - 1):
        a, b = today_pnl[i], today_pnl[i + 1]
        if (a <= 0 <= b) or (a >= 0 >= b):
            be = spots_f[i] + (spots_f[i+1]-spots_f[i]) * (-a/(b-a)) if b != a else spots_f[i]
            _crossings.append(round(be))
    _crossings.sort()
    be_down = _crossings[0]  if len(_crossings) >= 1 else None
    be_up   = _crossings[-1] if len(_crossings) >= 2 else None
    if be_down == be_up:
        be_up = None

    # ── Restore level: where missing opposite leg will be added ─────────────
    _ks_ces = [lg["K"] for lg in legs if lg["flag"] == "c" and lg["qty"] < 0]
    _ks_pes = [lg["K"] for lg in legs if lg["flag"] == "p" and lg["qty"] < 0]
    _one_sided_ui = bool(legs) and (not _ks_ces or not _ks_pes)
    restore_level = None
    if _one_sided_ui:
        _step_ui = 50 if underlying == "NIFTY" else (100 if underlying in ("BANKNIFTY", "SENSEX") else 50)
        if not _ks_ces:
            _ref_ui = _ce_exit_spot_ui if _ce_exit_spot_ui else _entry_spot_ui
            if _ref_ui > 0:
                restore_level = round(_ref_ui - _step_ui)
        elif not _ks_pes:
            _ref_ui = _pe_exit_spot_ui if _pe_exit_spot_ui else _entry_spot_ui
            if _ref_ui > 0:
                restore_level = round(_ref_ui + _step_ui)

    return jsonify({
        "spots":           [round(s) for s in spots_f],
        "expiry_pnl":      expiry_pnl,
        "today_pnl":       today_pnl,
        "current_spot":    round(spot),
        "max_profit_spot": max_profit_spot,
        "pct_from_center": pct_from_center,
        "breakeven_down":  be_down,
        "breakeven_up":    be_up,
        "legs_iv": [{"sym": lg["sym"], "iv_pct": round(lg["iv"]*100, 1)} for lg in legs],
        "restore_level":   restore_level,
    })


@app.route("/strategies")
@login_required
def strategies_page():
    return render_template_string(STRATEGIES_HUB_HTML)


@app.route("/api/strategies")
def api_strategies():
    strategies = _load_strategies()
    result = []
    for s in strategies:
        running = _is_running(s["screen_name"])
        pnl = _strategy_today_pnl(s["id"])
        result.append({**s, "running": running, "today_pnl": pnl})
    return jsonify(result)


@app.route("/api/strategy/save", methods=["POST"])
def api_strategy_save():
    data   = request.json or {}
    sid    = data.get("id", "").strip().lower().replace(" ", "_")
    if not sid:
        return jsonify({"error": "id required"}), 400
    strategies = _load_strategies()
    existing   = next((i for i, s in enumerate(strategies) if s["id"] == sid), None)
    entry = {
        "id":               sid,
        "name":             data.get("name", sid),
        "underlying":       data.get("underlying", "NIFTY"),
        "strategy_type":    data.get("strategy_type", "options"),
        "strategy":         data.get("strategy", "short_strangle"),
        "expiry":           data.get("expiry", ""),
        "lots":             int(data.get("lots", 1)),
        "target":           float(data.get("target", 5000)),
        "max_loss":         float(data.get("max_loss", -8000)),
        "mode":             data.get("mode", "sandbox"),
        "screen_name":      f"agent_{sid}",
        # Equity/futures fields (optional)
        "direction":        data.get("direction") or None,
        "qty":              int(data["qty"]) if data.get("qty") else None,
        "target_price":     float(data["target_price"]) if data.get("target_price") else None,
        "stop_loss_price":  float(data["stop_loss_price"]) if data.get("stop_loss_price") else None,
        "trailing_stop_pct": float(data["trailing_stop_pct"]) if data.get("trailing_stop_pct") else None,
    }
    if existing is not None:
        strategies[existing] = entry
    else:
        strategies.append(entry)
    _save_strategies(strategies)
    return jsonify({"ok": True, "strategy": entry})


@app.route("/api/strategy/delete", methods=["POST"])
def api_strategy_delete():
    sid = (request.json or {}).get("id")
    strategies = _load_strategies()
    strategies = [s for s in strategies if s["id"] != sid]
    _save_strategies(strategies)
    return jsonify({"ok": True})


@app.route("/api/replays")
@login_required
def api_replays():
    """List all replay sessions from session_memory files (prefix bt_)."""
    sm_dir = Path("/home/freed/autotrade/data/session_memory")
    # Snapshot of running screens once — avoid repeated subprocess calls
    screens_out = _sp.run(["screen", "-ls"], capture_output=True, text=True).stdout
    replays = []
    for f in sorted(sm_dir.glob("*.json"), reverse=True):
        # Filename: YYYY-MM-DD-UNDERLYING-strategy_id.json
        stem = f.stem  # e.g. 2026-06-15-NIFTY-bt_nifty_20250603_0942
        parts = stem.split("-", 4)
        if len(parts) < 5:
            continue
        strategy_id = parts[4]
        if not strategy_id.startswith("bt_"):
            continue
        underlying = parts[3].upper()
        run_date = f"{parts[0]}-{parts[1]}-{parts[2]}"  # date when replay was run
        try:
            data = __import__('json').loads(f.read_text())
        except Exception:
            continue
        hdr = data.get("header", {})
        # Extract historical date from strategy_id: bt_nifty_YYYYMMDD_HHMM
        sid_parts = strategy_id.split("_")
        hist_date = hdr.get("date", "")
        if not hist_date and len(sid_parts) >= 3:
            raw = sid_parts[2]  # YYYYMMDD
            if len(raw) == 8:
                hist_date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        # Check whether the screen process is still alive using stored screen_name
        _screen_name = hdr.get("screen_name", "")
        if _screen_name:
            is_running = _screen_name in screens_out
        else:
            # Old sessions without stored screen_name cannot be verified — treat as not running
            is_running = False
        saved_status = hdr.get("status", "")
        decisions = data.get("recent_decisions", [])
        chapters  = data.get("chapters", [])
        has_data  = bool(decisions or chapters or data.get("live_state"))
        if is_running:
            status = "running"
        elif saved_status == "stopped":
            status = "stopped"
        elif saved_status == "loading" and not has_data:
            status = "stopped"  # screen died during catalog load
        elif saved_status == "loading" and has_data:
            status = "completed"  # finished without explicit status update
        else:
            status = "completed"
        replays.append({
            "strategy_id":   strategy_id,
            "underlying":    underlying,
            "hist_date":     hist_date,
            "run_date":      f"{parts[0]}-{parts[1]}-{parts[2]}",
            "run_time":      sid_parts[3] if len(sid_parts) >= 4 else "",
            "status":        status,
            "is_running":    is_running,
            "decisions":     len(decisions),
            "started_at":    hdr.get("started_at", ""),
            "expiry":        hdr.get("expiry", ""),
            "last_action":   decisions[0].get("action", "") if decisions else ("Loading..." if status == "loading" else ""),
            "final_pnl":     decisions[0].get("pnl", 0) if decisions else 0,
            "file":          f.name,
        })
    return jsonify(replays)


@app.route("/api/replay/start", methods=["POST"])
@login_required
def api_replay_start():
    import shlex, datetime as _dt
    d = request.json or {}
    date       = (d.get("date") or "").strip()
    underlying = (d.get("underlying") or "NIFTY").upper()
    speed      = max(1, int(d.get("speed", 30)))
    lots       = max(1, int(d.get("lots",  5)))
    otm        = max(1, int(d.get("otm", 6)))
    use_llm    = bool(d.get("use_llm", False))
    if not date:
        return jsonify({"error": "date is required"}), 400
    ts          = _dt.datetime.now().strftime("%H%M%S")
    screen_name = f"replay_{underlying.lower()}_{date.replace('-','')}_{ts}"
    # Auto-stop any existing replay sessions for the same date/underlying
    _date_key = date.replace('-', '')
    _screens_now = _sp.run(["screen", "-ls"], capture_output=True, text=True).stdout
    for _sf in sorted(Path("/home/freed/autotrade/data/session_memory").glob("*.json")):
        _sstem = _sf.stem
        if not any(p.startswith("bt_") for p in _sstem.split("-")):
            continue
        _sp2 = _sstem.split("-", 4)
        if len(_sp2) < 5 or not _sp2[4].startswith("bt_"):
            continue
        _old_sid = _sp2[4]
        _old_parts = _old_sid.split("_")
        if len(_old_parts) < 3:
            continue
        if _old_parts[1].lower() != underlying.lower() or _old_parts[2] != _date_key:
            continue
        # Same date/underlying — kill its screen and mark stopped
        _old_screen = f"replay_{'_'.join(_old_parts[1:])}"
        for _line in _screens_now.splitlines():
            if _old_screen in _line:
                _sn = _line.strip().split()[0]
                _sp.run(["screen", "-S", _sn, "-X", "quit"], capture_output=True)
        try:
            import sys as _sys_as; _sys_as.path.insert(0, str(Path(__file__).parent))
            import session_memory as _sm_as
            _dd = _sm_as._load(underlying, _old_sid)
            _dd.setdefault("header", {})["status"] = "stopped"
            _sm_as._save(underlying, _old_sid, _dd)
        except Exception:
            pass
    env_cmd  = f"cd {AUTOTRADE_DIR} && set -a && source .env && set +a"
    run_cmd  = (f"{VENV_PYTHON} -u {AGENTS_DIR}/backtest_replay.py"
                f" --date {shlex.quote(date)}"
                f" --speed {speed} --lots {lots} --otm {otm}"
                f" --screen-name {shlex.quote(screen_name)}"
                + ("" if use_llm else " --no-llm"))
    log_file = f"/tmp/{screen_name}.log"
    full_cmd = f"{env_cmd} && {run_cmd} 2>&1 | tee {log_file}"
    proc = _sp.run(["screen", "-dmS", screen_name, "bash", "-c", full_cmd],
                   capture_output=True, text=True)
    if proc.returncode != 0:
        return jsonify({"error": proc.stderr or "screen failed"}), 500
    return jsonify({"ok": True, "screen": screen_name, "log": log_file})



@app.route("/api/replay/stop", methods=["POST"])
@login_required
def api_replay_stop():
    d = request.json or {}
    sid = (d.get("strategy_id") or "").strip()
    if not sid:
        return jsonify({"error": "strategy_id required"}), 400
    parts = sid.replace("bt_", "").split("_")
    # Derive the exact screen name from strategy_id (bt_nifty_20260123_1300 -> replay_nifty_20260123_1300)
    exact_screen = "replay_" + sid[3:] if sid.startswith("bt_") else sid
    screens = _sp.run(["screen", "-ls"], capture_output=True, text=True).stdout
    killed = []
    for line in screens.splitlines():
        if exact_screen in line:
            name = line.strip().split()[0]
            _sp.run(["screen", "-S", name, "-X", "quit"])
            killed.append(name)
    # Also kill any raw Python process using the screen name (CLI-launched replays)
    _sp.run(["pkill", "-f", f"--screen-name {exact_screen}"], capture_output=True)
    try:
        import sys as _sys_rs; _sys_rs.path.insert(0, str(Path(__file__).parent))
        import session_memory as _sm_rs
        _under = parts[0].upper() if parts else "NIFTY"
        _d = _sm_rs._load(_under, sid)
        _d.setdefault("header", {})["status"] = "stopped"
        _sm_rs._save(_under, sid, _d)
    except Exception:
        pass
    return jsonify({"ok": True, "killed": killed, "strategy_id": sid})


def _api_data_replay(underlying: str, strategy_id: str, cfg: dict):
    """Return api_data-compatible response from session_memory live_state for replay sessions."""
    import sys as _sys_r, json as _json_r
    _ag = str(Path(__file__).parent)
    if _ag not in _sys_r.path: _sys_r.path.insert(0, _ag)
    try:
        import session_memory as _sm_r
        # _load() pins to today's date — misses sessions saved on a previous day.
        # Glob for *-UNDERLYING-strategy_id.json to find it regardless of run date.
        _sm_dir = Path("/home/freed/autotrade/data/session_memory")
        _matches = sorted(_sm_dir.glob(f"*-{underlying.upper()}-{strategy_id}.json"), reverse=True)
        if _matches:
            sm_raw = _json_r.loads(_matches[0].read_text())
            # Extract run date from filename (YYYY-MM-DD-UNDERLYING-strategy_id.json)
            _run_date = "-".join(_matches[0].stem.split("-")[:3])
        else:
            sm_raw = _sm_r._load(underlying, strategy_id)
            _run_date = datetime.now(IST).strftime("%Y-%m-%d")
        live = sm_raw.get("live_state", {})
    except Exception:
        live      = {}
        sm_raw    = {}
        _run_date = datetime.now(IST).strftime("%Y-%m-%d")

    spot      = live.get("spot", 0.0)
    net_pnl   = live.get("net_pnl", 0.0)
    hist_time = live.get("hist_time", "—")
    hist_date = live.get("hist_date", "—")
    bar_num   = live.get("bar_num", 0)
    total_bars= live.get("total_bars", 75)
    hdr       = sm_raw.get("header", {})
    date_str  = _run_date  # use run date for decision log lookup (not historical date)
    expiry    = live.get("expiry") or hdr.get("expiry", "")

    positions_out = []
    for p in live.get("positions", []):
        sym = p.get("symbol", "")
        parsed = _parse_sym(sym)
        opt_type = p.get("opt_type") or (parsed["opt_type"] if parsed else None)
        positions_out.append({
            "symbol":   sym,
            "opt_type": opt_type,
            "qty":      p.get("qty", 0),
            "avg":      p.get("avg", 0),
            "ltp":      p.get("ltp", 0),
            "pnl":      p.get("pnl", 0),
            "otm_pct":  p.get("otm_pct"),   # decimal 0-1, already broker-API format
            "ratio":    p.get("ratio"),
        })

    fills_out = [
        {"time": f.get("time",""), "symbol": f.get("symbol",""),
         "action": f.get("action",""), "qty": f.get("qty",0), "price": f.get("price",0),
         "avg_price": f.get("avg_price")}
        for f in live.get("fills", [])
    ]

    decisions = _read_decisions_for_date(date_str, 200, underlying, strategy_id)
    decisions_out = []
    for rec in decisions:
        ctx = rec.get("context_summary", {})
        dec = rec.get("decision", {})
        decisions_out.append({
            "time":      _ts_hhmm(rec.get("ts", "")),
            "action":    dec.get("action", "?"),
            "source":    rec.get("decision_source", "llm"),
            "spot":      ctx.get("underlying_price", 0),
            "pnl":       ctx.get("pnl", 0),
            "executed":  rec.get("executed", False),
            "reasoning": (dec.get("reasoning") or ""),
        })

    return jsonify({
        "spot":        spot,
        "entry_spot":  hdr.get("entry_spot", 0),
        "net_pnl":     net_pnl,
        "target":      cfg.get("target", 6000),
        "max_loss":    cfg.get("max_loss", -8000),
        "strategy":    cfg.get("strategy", "short_strangle"),
        "underlying":  underlying,
        "strategy_id": strategy_id,
        "positions":   positions_out,
        "fills":       fills_out,
        "decisions":   decisions_out,
        "vix":         None,
        "pcr":         None,
        "pcr_trend":   None,
        "expiry":      expiry,
        "replay":          True,
        "hist_time":       hist_time,
        "hist_date":       hist_date,
        "bar_num":         bar_num,
        "total_bars":      total_bars,
        "is_today":        False,
        "date":            hist_date,
        "realized_pnl":    0,
        "available_dates": _available_dates(underlying),
    })


@app.route("/api/strategy/start", methods=["POST"])
def api_strategy_start():
    sid = (request.json or {}).get("id")
    strategies = _load_strategies()
    s = next((x for x in strategies if x["id"] == sid), None)
    if not s:
        return jsonify({"error": "Strategy not found"}), 404
    if _is_running(s["screen_name"]):
        return jsonify({"error": "Already running"}), 400

    env_cmd  = f"cd {AUTOTRADE_DIR} && set -a && source .env && set +a"
    run_cmd  = f"{VENV_PYTHON} {AGENTS_DIR}/start_strategy.py --id {sid}"
    log_file = f"/tmp/{s['screen_name']}.log"
    full_cmd = f"{env_cmd} && {run_cmd} 2>&1 | tee {log_file}"

    proc = _sp.run(
        ["screen", "-dmS", s["screen_name"], "bash", "-c", full_cmd],
        capture_output=True, text=True
    )
    if proc.returncode != 0:
        return jsonify({"error": proc.stderr or "screen failed"}), 500
    return jsonify({"ok": True})


@app.route("/api/strategy/stop", methods=["POST"])
def api_strategy_stop():
    sid = (request.json or {}).get("id")
    strategies = _load_strategies()
    s = next((x for x in strategies if x["id"] == sid), None)
    if not s:
        return jsonify({"error": "Strategy not found"}), 404

    # Close only this strategy's positions (owned_symbols), fallback to prefix
    underlying = s.get("underlying", "").upper()
    _MCX_UL = {"GOLDM", "GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"}
    closed, errors = [], []
    if underlying:
        exchange = "MCX" if underlying in _MCX_UL else "NFO"
        product  = "NRML" if underlying in _MCX_UL else "MIS"
        # Resolve owned_symbols for targeted closure
        try:
            import sys as _sys_stop
            _ag_stop = str(Path(__file__).parent)
            if _ag_stop not in _sys_stop.path: _sys_stop.path.insert(0, _ag_stop)
            import session_memory as _sm_stop
            _owned_stop = set(_sm_stop.get_owned_symbols(underlying, sid))
        except Exception:
            _owned_stop = set()
        try:
            for p in _get_positions():
                qty = int(p.get("quantity") or p.get("netqty") or 0)
                sym = (p.get("symbol") or p.get("tradingsymbol", ""))
                if qty == 0:
                    continue
                # Use owned_symbols if available, else prefix match
                if _owned_stop:
                    if sym.upper() not in _owned_stop:
                        continue
                else:
                    if not sym.upper().startswith(underlying):
                        continue
                action = "BUY" if qty < 0 else "SELL"
                payload = {
                    "apikey": API_KEY,
                    "strategy": "StopStrategy",
                    "symbol": sym,
                    "action": action,
                    "exchange": p.get("exchange", exchange),
                    "pricetype": "MARKET",
                    "product": p.get("product", product),
                    "quantity": str(abs(qty)),
                }
                r = requests.post(f"{OPENALGO_BASE}/api/v1/placeorder",
                                  json=payload, headers=_oa_headers(), timeout=10)
                r.raise_for_status()
                closed.append(sym)
        except Exception as e:
            errors.append(str(e))

    # Kill the strategy screen session
    _sp.run(["screen", "-S", s["screen_name"], "-X", "quit"],
            capture_output=True)
    return jsonify({"ok": True, "closed": closed, "errors": errors})


@app.route("/api/strategy/toggle-mode", methods=["POST"])
def api_toggle_mode():
    data = request.json or {}
    sid  = data.get("id")
    mode = data.get("mode")
    if mode not in ("sandbox", "live"):
        return jsonify({"error": "mode must be sandbox or live"}), 400
    strategies = _load_strategies()
    for s in strategies:
        if s["id"] == sid:
            s["mode"] = mode
    _save_strategies(strategies)
    return jsonify({"ok": True})


# ── Strategy Hub HTML ──────────────────────────────────────────────────────

STRATEGIES_HUB_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Strategy Hub</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #11111b; color: #cdd6f4; font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 13px; }

  /* ── Top bar ── */
  .topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 24px; background: #181825; border-bottom: 1px solid #313244;
    position: sticky; top: 0; z-index: 100;
  }
  .topbar-left  { display: flex; align-items: center; gap: 16px; }
  .logo         { color: #89b4fa; font-weight: 700; font-size: 15px; letter-spacing: 1px; }
  .logo span    { color: #cba6f7; }
  .nav-link     { color: #6c7086; font-size: 11px; text-decoration: none; padding: 4px 10px;
                  border: 1px solid #313244; border-radius: 4px; }
  .nav-link:hover { color: #cdd6f4; border-color: #585b70; }
  .btn-add      { background: #89b4fa; color: #11111b; border: none; padding: 7px 16px;
                  border-radius: 5px; font-size: 12px; font-weight: 700; cursor: pointer;
                  font-family: inherit; letter-spacing: 0.5px; }
  .btn-add:hover { background: #b4d0fa; }

  /* ── Main ── */
  .main { padding: 28px 24px; max-width: 1200px; margin: 0 auto; }
  .section-title { color: #6c7086; font-size: 10px; text-transform: uppercase;
                   letter-spacing: 2px; margin-bottom: 20px; }

  /* ── Strategy grid ── */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }

  /* ── Card ── */
  .card {
    background: #1e1e2e; border: 1px solid #313244; border-radius: 10px;
    padding: 20px; display: flex; flex-direction: column; gap: 14px;
    transition: border-color .15s;
  }
  .card:hover { border-color: #585b70; }
  .card.running { border-color: #a6e3a1; }
  .card.add-card {
    border: 2px dashed #313244; cursor: pointer; align-items: center;
    justify-content: center; min-height: 200px; color: #585b70;
    flex-direction: row; gap: 8px; font-size: 13px;
  }
  .card.add-card:hover { border-color: #89b4fa; color: #89b4fa; }
  .card.add-card .plus { font-size: 24px; line-height: 1; }

  /* ── Card header ── */
  .card-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
  .card-name { font-size: 14px; font-weight: 700; color: #cdd6f4; line-height: 1.3; }
  .badge-type { font-size: 10px; padding: 2px 7px; border-radius: 10px;
                background: #313244; color: #89b4fa; white-space: nowrap; }

  /* ── Status row ── */
  .status-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .dot.running  { background: #a6e3a1; box-shadow: 0 0 6px #a6e3a1; }
  .dot.stopped  { background: #6c7086; }
  .status-text  { font-size: 11px; font-weight: 700; letter-spacing: 1px; }
  .status-text.running { color: #a6e3a1; }
  .status-text.stopped { color: #6c7086; }

  /* ── Mode toggle ── */
  .mode-toggle { display: flex; border: 1px solid #313244; border-radius: 4px; overflow: hidden; }
  .mode-btn { padding: 3px 10px; font-size: 10px; font-family: inherit; font-weight: 700;
              letter-spacing: 0.5px; cursor: pointer; border: none; background: transparent;
              color: #6c7086; transition: all .12s; }
  .mode-btn.active.sandbox { background: #313244; color: #f9e2af; }
  .mode-btn.active.live    { background: #45475a; color: #a6e3a1; }
  .mode-btn:hover:not(.active) { color: #cdd6f4; }

  /* ── P&L ── */
  .pnl-row { display: flex; align-items: baseline; gap: 8px; }
  .pnl-label { font-size: 10px; color: #6c7086; text-transform: uppercase; letter-spacing: 1px; }
  .pnl-value { font-size: 20px; font-weight: 700; }
  .pnl-pos { color: #a6e3a1; }
  .pnl-neg { color: #f38ba8; }
  .pnl-zero { color: #6c7086; }

  /* ── Meta grid ── */
  .meta { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; }
  .meta-item { display: flex; flex-direction: column; gap: 1px; }
  .meta-key  { font-size: 9px; color: #6c7086; text-transform: uppercase; letter-spacing: 1px; }
  .meta-val  { font-size: 12px; color: #cdd6f4; }
  .expiry-val { display: flex; align-items: center; gap: 6px; }
  .edit-expiry { font-size: 9px; color: #89b4fa; cursor: pointer; background: none;
                 border: none; font-family: inherit; padding: 0; }
  .edit-expiry:hover { text-decoration: underline; }

  /* ── Card footer ── */
  .card-footer { display: flex; gap: 8px; margin-top: 4px; }
  .btn { padding: 7px 14px; border-radius: 5px; font-size: 11px; font-weight: 700;
         font-family: inherit; cursor: pointer; border: none; letter-spacing: 0.5px;
         transition: opacity .12s; }
  .btn:hover { opacity: .85; }
  .btn-start   { background: #a6e3a1; color: #11111b; flex: 1; }
  .btn-stop    { background: #f38ba8; color: #11111b; flex: 1; }
  .btn-monitor { background: #313244; color: #89b4fa; }
  .btn-edit    { background: #313244; color: #cdd6f4; }
  .btn:disabled { opacity: .4; cursor: not-allowed; }

  /* ── Modal ── */
  .overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.65);
             z-index: 200; align-items: center; justify-content: center; }
  .overlay.open { display: flex; }
  .modal { background: #1e1e2e; border: 1px solid #45475a; border-radius: 12px;
           padding: 28px; width: 480px; max-width: 95vw; }
  .modal-title { font-size: 14px; font-weight: 700; color: #cdd6f4; margin-bottom: 20px; }
  .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .form-full { grid-column: 1 / -1; }
  .field { display: flex; flex-direction: column; gap: 5px; }
  .field label { font-size: 10px; color: #6c7086; text-transform: uppercase; letter-spacing: 1px; }
  .field input, .field select {
    background: #181825; border: 1px solid #313244; color: #cdd6f4;
    padding: 8px 10px; border-radius: 5px; font-size: 13px; font-family: inherit;
    outline: none; transition: border-color .12s;
  }
  .field input:focus, .field select:focus { border-color: #89b4fa; }
  .mode-field { display: flex; gap: 0; border: 1px solid #313244; border-radius: 5px; overflow: hidden; }
  .mode-radio { display: none; }
  .mode-label { flex: 1; text-align: center; padding: 8px; font-size: 12px; font-weight: 700;
                cursor: pointer; color: #6c7086; transition: all .12s; }
  .mode-radio:checked + .mode-label { background: #313244; color: #cdd6f4; }
  .modal-footer { display: flex; gap: 10px; justify-content: flex-end; margin-top: 22px; }
  .btn-cancel { background: #313244; color: #cdd6f4; padding: 8px 18px; border-radius: 5px;
                border: none; font-family: inherit; font-size: 12px; cursor: pointer; }
  .btn-save   { background: #89b4fa; color: #11111b; padding: 8px 18px; border-radius: 5px;
                border: none; font-family: inherit; font-size: 12px; font-weight: 700; cursor: pointer; }

  /* ── Toast ── */
  .toast { position: fixed; bottom: 24px; right: 24px; background: #313244; color: #cdd6f4;
           padding: 10px 18px; border-radius: 7px; font-size: 12px; z-index: 999;
           opacity: 0; transition: opacity .2s; pointer-events: none; }
  .toast.show { opacity: 1; }
  .toast.ok  { border-left: 3px solid #a6e3a1; }
  .toast.err { border-left: 3px solid #f38ba8; }
</style>
</head>
<body>

<nav style="background:#12131a;border-bottom:1px solid #313244;padding:0 20px;display:flex;align-items:center;gap:0;position:sticky;top:0;z-index:100">
  <span style="font-weight:800;color:#89b4fa;font-size:14px;letter-spacing:.5px;padding:12px 20px 12px 0;border-right:1px solid #313244;margin-right:4px">&#9889; AutoTrade</span>
  <a href="/" style="padding:12px 16px;font-size:12px;text-decoration:none;color:#cdd6f4;border-bottom:2px solid transparent" id="nav-trading">&#128200; Trading</a>
  <a href="/screener" style="padding:12px 16px;font-size:12px;text-decoration:none;color:#cdd6f4;border-bottom:2px solid transparent" id="nav-screener">&#128269; Screener</a>
  <a href="/strategies" style="padding:12px 16px;font-size:12px;text-decoration:none;color:#cdd6f4;border-bottom:2px solid transparent" id="nav-strategies">&#127919; Strategies</a>
  <span style="margin-left:auto;font-size:11px;color:#6c7086;padding:12px 0">
    <a href="/logout" style="color:#f38ba8;text-decoration:none;font-size:11px">Logout</a>
  </span>
</nav>
<script>
(function(){
  const path = window.location.pathname;
  const map = {'/':'nav-trading','/screener':'nav-screener','/strategies':'nav-strategies'};
  const id = map[path] || (path.startsWith('/screener') ? 'nav-screener' : null);
  if (id) { const el = document.getElementById(id); if(el) el.style.cssText += ';color:#89b4fa;border-bottom-color:#89b4fa;font-weight:600'; }
})();
</script>

<div class="topbar">
  <div class="topbar-left">
    <div class="logo">◆ STRATEGY <span>HUB</span></div>
    <a href="/" class="nav-link">Monitor →</a>
  </div>
  <button class="btn-add" onclick="openModal()">+ Add Strategy</button>
</div>

<div class="main">
  <div class="section-title">Active Strategies</div>
  <div class="grid" id="strategy-grid">
    <div style="color:#6c7086;padding:20px">Loading strategies…</div>
  </div>
</div>

<div class="main" style="margin-top:24px">
  <div class="section-title" style="color:#89dceb">Replay Sessions</div>
  <div style="background:#1e1e2e;border:1px solid #313244;border-radius:8px;padding:16px;margin-bottom:16px">
    <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end">
      <div style="flex:0 0 auto">
        <label style="font-size:11px;color:#6c7086;display:block;margin-bottom:4px">Historical Date</label>
        <input type="date" id="rp-date" style="background:#11111b;border:1px solid #313244;color:#cdd6f4;padding:6px 10px;border-radius:6px;font-size:13px">
      </div>
      <div style="flex:0 0 auto">
        <label style="font-size:11px;color:#6c7086;display:block;margin-bottom:4px">Underlying</label>
        <select id="rp-underlying" style="background:#11111b;border:1px solid #313244;color:#cdd6f4;padding:6px 10px;border-radius:6px;font-size:13px">
          <option value="NIFTY">NIFTY</option>
          <option value="BANKNIFTY">BANKNIFTY</option>
        </select>
      </div>
      <div style="flex:0 0 auto">
        <label style="font-size:11px;color:#6c7086;display:block;margin-bottom:4px">Speed (sec/bar)</label>
        <select id="rp-speed" style="background:#11111b;border:1px solid #313244;color:#cdd6f4;padding:6px 10px;border-radius:6px;font-size:13px">
          <option value="60">60s — real feel (~75min)</option>
          <option value="30" selected>30s — fast (~37min)</option>
          <option value="10">10s — turbo (~12min)</option>
          <option value="1">1s — smoke test</option>
        </select>
      </div>
      <div style="flex:0 0 auto">
        <label style="font-size:11px;color:#6c7086;display:block;margin-bottom:4px">Lots</label>
        <input type="number" id="rp-lots" value="5" min="1" max="50" style="background:#11111b;border:1px solid #313244;color:#cdd6f4;padding:6px 10px;border-radius:6px;font-size:13px;width:70px">
      </div>
      <div style="flex:0 0 auto">
        <label style="font-size:11px;color:#6c7086;display:block;margin-bottom:4px">OTM Steps (×50pts)</label>
        <input type="number" id="rp-otm" value="6" min="1" max="30" style="background:#11111b;border:1px solid #313244;color:#cdd6f4;padding:6px 10px;border-radius:6px;font-size:13px;width:70px">
      </div>
      <div style="flex:0 0 auto;display:flex;align-items:center;gap:8px;padding-top:18px">
        <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:#cdd6f4;cursor:pointer;user-select:none">
          <input type="checkbox" id="rp-use-llm" style="width:15px;height:15px;accent-color:#cba6f7">
          <span>Use LLM</span>
        </label>
      </div>
      <div style="flex:0 0 auto">
        <button class="btn btn-start" id="rp-launch-btn" onclick="launchReplay()" style="padding:8px 20px;font-size:13px">▶ Start Replay</button>
      </div>
    </div>
    <div id="rp-status" style="font-size:12px;color:#6c7086;margin-top:8px"></div>
  </div>
  <div class="grid" id="replay-list">
    <div style="color:#6c7086;padding:20px">Loading...</div>
  </div>
</div>

<!-- Add / Edit Modal -->
<div class="overlay" id="modal">
  <div class="modal" style="width:520px">
    <div class="modal-title" id="modal-title">Add Strategy</div>
    <input type="hidden" id="f-id">
    <div class="form-grid">
      <div class="field form-full">
        <label>Strategy Name</label>
        <input id="f-name" type="text" placeholder="e.g. NIFTY Short Strangle">
      </div>
      <div class="field form-full">
        <label>Strategy Type</label>
        <div class="mode-field">
          <input class="mode-radio" type="radio" name="stype" id="st-options" value="options" checked>
          <label class="mode-label" for="st-options">Options</label>
          <input class="mode-radio" type="radio" name="stype" id="st-equity" value="equity">
          <label class="mode-label" for="st-equity">Equity</label>
          <input class="mode-radio" type="radio" name="stype" id="st-futures" value="futures">
          <label class="mode-label" for="st-futures">Futures</label>
        </div>
      </div>

      <!-- OPTIONS fields -->
      <div class="field opt-field">
        <label>Underlying</label>
        <select id="f-underlying">
          <option value="NIFTY">NIFTY</option>
          <option value="BANKNIFTY">BANKNIFTY</option>
          <option value="GOLDM">GOLDM (MCX)</option>
        </select>
      </div>
      <div class="field opt-field">
        <label>Strategy</label>
        <select id="f-strategy">
          <option value="short_strangle">Short Strangle</option>
          <option value="short_straddle">Short Straddle</option>
          <option value="iron_condor">Iron Condor</option>
        </select>
      </div>
      <div class="field opt-field">
        <label>Expiry Date</label>
        <input id="f-expiry" type="date">
      </div>
      <div class="field opt-field">
        <label>Lots</label>
        <input id="f-lots" type="number" value="5" min="1">
      </div>

      <!-- EQUITY fields -->
      <div class="field eq-field" style="display:none">
        <label>Stock Symbol (NSE)</label>
        <input id="f-eq-underlying" type="text" placeholder="RELIANCE">
      </div>
      <div class="field eq-field" style="display:none">
        <label>Direction</label>
        <select id="f-eq-direction">
          <option value="LONG">LONG (Buy)</option>
          <option value="SHORT">SHORT (Sell)</option>
        </select>
      </div>
      <div class="field eq-field" style="display:none">
        <label>Quantity (shares)</label>
        <input id="f-eq-qty" type="number" value="50" min="1">
      </div>
      <div class="field eq-field" style="display:none">
        <label>Target Price (Rs.)</label>
        <input id="f-eq-target-price" type="number" step="1">
      </div>
      <div class="field eq-field" style="display:none">
        <label>Stop Loss Price (Rs.)</label>
        <input id="f-eq-stop" type="number" step="1">
      </div>
      <div class="field eq-field" style="display:none">
        <label>Trailing Stop %</label>
        <input id="f-eq-trail" type="number" value="2" step="0.5" min="0.5">
      </div>

      <!-- FUTURES fields -->
      <div class="field fut-field" style="display:none">
        <label>Underlying</label>
        <select id="f-fut-underlying">
          <option value="NIFTY">NIFTY FUT</option>
          <option value="BANKNIFTY">BANKNIFTY FUT</option>
          <option value="GOLDM">GOLDM FUT (MCX)</option>
          <option value="CRUDEOIL">CRUDEOIL FUT (MCX)</option>
        </select>
      </div>
      <div class="field fut-field" style="display:none">
        <label>Direction</label>
        <select id="f-fut-direction">
          <option value="LONG">LONG</option>
          <option value="SHORT">SHORT</option>
        </select>
      </div>
      <div class="field fut-field" style="display:none">
        <label>Lots</label>
        <input id="f-fut-lots" type="number" value="1" min="1">
      </div>
      <div class="field fut-field" style="display:none">
        <label>Stop Loss Price (Rs.)</label>
        <input id="f-fut-stop" type="number" step="1">
      </div>
      <div class="field fut-field" style="display:none">
        <label>Trailing Stop %</label>
        <input id="f-fut-trail" type="number" value="1" step="0.25" min="0.25">
      </div>

      <!-- Common: Target P&L + Max Loss -->
      <div class="field">
        <label>Target P&amp;L (Rs.)</label>
        <input id="f-target" type="number" value="5000" step="500">
      </div>
      <div class="field">
        <label>Max Loss (Rs.)</label>
        <input id="f-maxloss" type="number" value="-8000" step="500">
      </div>
      <div class="field form-full">
        <label>Mode</label>
        <div class="mode-field">
          <input class="mode-radio" type="radio" name="mode" id="m-sandbox" value="sandbox" checked>
          <label class="mode-label" for="m-sandbox">SANDBOX</label>
          <input class="mode-radio" type="radio" name="mode" id="m-live" value="live">
          <label class="mode-label" for="m-live">LIVE</label>
        </div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn-cancel" onclick="closeModal()">Cancel</button>
      <button class="btn-save" onclick="saveStrategy()">Save</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
let strategies = [];
let editingId  = null;

function fmt(n) {
  const v = Math.round(n).toLocaleString('en-IN');
  return (n >= 0 ? '+' : '') + 'Rs.' + v;
}

function pnlClass(n) {
  if (n > 0) return 'pnl-pos';
  if (n < 0) return 'pnl-neg';
  return 'pnl-zero';
}

function toast(msg, type='ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast ${type} show`;
  setTimeout(() => el.classList.remove('show'), 3000);
}

function renderStrategies(data) {
  strategies = data;
  const grid = document.getElementById('strategy-grid');
  let html = data.map(s => {
    const running     = s.running;
    const pnl         = s.today_pnl || 0;
    const cardClass   = running ? 'card running' : 'card';
    const dotClass    = running ? 'dot running' : 'dot stopped';
    const statusText  = running ? 'RUNNING' : 'STOPPED';
    const statusClass = running ? 'status-text running' : 'status-text stopped';
    const sandboxActive = s.mode === 'sandbox' ? 'active sandbox' : '';
    const liveActive    = s.mode === 'live' ? 'active live' : '';
    const pnlHtml = pnl !== 0
      ? `<span class="pnl-value ${pnlClass(pnl)}">${fmt(pnl)}</span>`
      : `<span class="pnl-value pnl-zero">Rs.0</span>`;
    const startStopBtn = running
      ? `<button class="btn btn-stop" onclick="stopStrategy('${s.id}')">■ Stop</button>`
      : `<button class="btn btn-start" onclick="startStrategy('${s.id}')">▶ Start</button>`;
    const expiryFormatted = s.expiry
      ? new Date(s.expiry + 'T00:00:00').toLocaleDateString('en-IN', {day:'2-digit', month:'short', year:'2-digit'})
      : '—';

    return `
    <div class="${cardClass}" id="card-${s.id}">
      <div class="card-header">
        <div class="card-name">${s.name}</div>
        <span class="badge-type">${(s.strategy||'').replace('_',' ').toUpperCase()}</span>
      </div>

      <div class="status-row">
        <span class="${dotClass}"></span>
        <span class="${statusClass}">${statusText}</span>
        <div class="mode-toggle">
          <button class="mode-btn ${sandboxActive}" onclick="toggleMode('${s.id}', 'sandbox')">SANDBOX</button>
          <button class="mode-btn ${liveActive}"    onclick="toggleMode('${s.id}', 'live')">LIVE</button>
        </div>
      </div>

      <div class="pnl-row">
        <span class="pnl-label">Today P&L</span>
        ${pnlHtml}
      </div>

      <div class="meta">
        <div class="meta-item">
          <span class="meta-key">Underlying</span>
          <span class="meta-val">${s.underlying}</span>
        </div>
        <div class="meta-item">
          <span class="meta-key">Lots</span>
          <span class="meta-val">${s.lots}</span>
        </div>
        <div class="meta-item">
          <span class="meta-key">Expiry</span>
          <span class="meta-val expiry-val">
            ${expiryFormatted}
            <button class="edit-expiry" onclick="editExpiry('${s.id}', '${s.expiry}')">edit</button>
          </span>
        </div>
        <div class="meta-item">
          <span class="meta-key">Target / Floor</span>
          <span class="meta-val">Rs.${(s.target||0).toLocaleString()} / Rs.${(s.max_loss||0).toLocaleString()}</span>
        </div>
      </div>

      <div class="card-footer">
        ${startStopBtn}
        <a href="/?underlying=${s.underlying}&strategy_id=${s.id}" class="btn btn-monitor">Monitor →</a>
        <button class="btn btn-edit" onclick="editStrategy('${s.id}')">Edit</button>
      </div>
    </div>`;
  }).join('');

  html += `
  <div class="card add-card" onclick="openModal()">
    <span class="plus">+</span>
    <span>Add Strategy</span>
  </div>`;

  grid.innerHTML = html;
}

async function launchReplay() {
  const date    = document.getElementById('rp-date').value;
  if (!date) { alert('Please select a date'); return; }
  const underlying = document.getElementById('rp-underlying').value;
  const speed      = document.getElementById('rp-speed').value;
  const lots       = document.getElementById('rp-lots').value;
  const otm        = document.getElementById('rp-otm').value;
  const useLlm     = document.getElementById('rp-use-llm').checked;
  const btn        = document.getElementById('rp-launch-btn');
  const status     = document.getElementById('rp-status');
  btn.disabled = true; btn.textContent = 'Starting…';
  status.style.color = '#89b4fa';
  status.textContent = 'Launching replay in background screen session…';
  try {
    const r = await fetch('/api/replay/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({date, underlying, speed: +speed, lots: +lots, otm: +otm, use_llm: useLlm})
    });
    const d = await r.json();
    if (d.ok) {
      status.style.color = '#a6e3a1';
      status.textContent = 'Replay started — check Replay Sessions list below';
      setTimeout(() => { loadStrategies(); }, 5000); setTimeout(() => { loadStrategies(); }, 20000);
    } else {
      status.style.color = '#f38ba8';
      status.textContent = 'Error: ' + (d.error || 'unknown');
    }
  } catch(e) {
    status.style.color = '#f38ba8';
    status.textContent = 'Request failed: ' + e.message;
  } finally {
    btn.disabled = false; btn.textContent = '▶ Start Replay';
  }
}

async function loadStrategies() {
  const r = await fetch('/api/strategies');
  const data = await r.json();
  renderStrategies(data);
  // Load replay sessions alongside strategies
  try {
    const rr = await fetch('/api/replays');
    const replays = await rr.json();
    renderReplays(replays);
  } catch(e) { console.warn('replays fetch:', e); }
}

function renderReplays(replays) {
  const el = document.getElementById('replay-list');
  if (!el) return;
  if (!replays.length) {
    el.innerHTML = '<p style="color:#6c7086;font-size:12px;grid-column:1/-1">No replay sessions yet. Run: <code style="color:#cba6f7">backtest_replay.py --date YYYY-MM-DD --speed 30</code></p>';
    return;
  }
  el.innerHTML = replays.map(r => {
    const pnlColor = (r.final_pnl||0) >= 0 ? '#a6e3a1' : '#f38ba8';
    const runTime  = r.run_time ? r.run_time.slice(0,2)+':'+r.run_time.slice(2)+' IST' : '';
    let borderColor, badge;
    if (r.status === 'running') {
      borderColor = '#a6e3a1';
      badge = `<span class="badge-type" style="background:#0d2e18;color:#a6e3a1;animation:pulse 1.5s infinite">⟳ RUNNING</span>`;
    } else if (r.status === 'loading') {
      borderColor = '#f9e2af';
      badge = `<span class="badge-type" style="background:#3a2e00;color:#f9e2af;animation:pulse 1.5s infinite">⏳ LOADING</span>`;
    } else if (r.status === 'stopped') {
      borderColor = '#6c7086';
      badge = `<span class="badge-type" style="background:#2a2a3a;color:#6c7086">■ STOPPED</span>`;
    } else {
      borderColor = '#89dceb';
      badge = `<span class="badge-type" style="background:#1e3a5f;color:#89dceb">✓ DONE</span>`;
    }
    const canStop = r.status === 'running' || r.status === 'loading';
    const stopBtn = canStop
      ? `<button onclick="stopReplay('${r.strategy_id}','${r.underlying}')" style="margin-left:8px;background:#3a1a1a;color:#f38ba8;border:1px solid #f38ba8;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:11px">■ Stop</button>`
      : '';
    const watchLink = `<a href="/?underlying=${r.underlying}&strategy_id=${r.strategy_id}" class="btn btn-monitor">Watch →</a>`;
    const footer = r.status === 'loading'
      ? `${watchLink}  <span style="font-size:11px;color:#f9e2af;margin-left:8px">⏳ Loading catalog…</span>${stopBtn}`
      : `${watchLink}
        ${stopBtn}
        <code style="font-size:10px;color:#6c7086;margin-left:8px">${r.strategy_id}</code>`;
    return `<div class="card" style="border-left:3px solid ${borderColor};cursor:default">
      <div class="card-header">
        <div class="card-name">${r.underlying} · ${r.hist_date||'?'}</div>
        ${badge}
      </div>
      <div class="meta" style="margin:8px 0">
        <div class="meta-item"><span class="meta-key">Run</span><span class="meta-val">${r.run_date} ${runTime}</span></div>
        <div class="meta-item"><span class="meta-key">Expiry</span><span class="meta-val">${r.expiry||'—'}</span></div>
        <div class="meta-item"><span class="meta-key">Decisions</span><span class="meta-val">${r.status === 'loading' ? '…' : r.decisions}</span></div>
        <div class="meta-item"><span class="meta-key">Final P&L</span><span class="meta-val" style="color:${pnlColor}">${r.status === 'loading' ? '—' : '₹'+(r.final_pnl||0).toLocaleString('en-IN')}</span></div>
      </div>
      <div class="card-footer">
        ${footer}
      </div>
    </div>`;
  }).join('');
}

async function stopReplay(strategyId, underlying) {
  if (!confirm('Stop replay ' + strategyId + '?')) return;
  const r = await fetch('/api/replay/stop', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({strategy_id: strategyId})
  });
  const d = await r.json();
  if (d.ok) { toast('Replay stopped'); loadReplays(); }
  else toast('Error: ' + (d.error || 'unknown'), 'err');
}

async function startStrategy(id) {
  const btn = document.querySelector(`#card-${id} .btn-start`);
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }
  const r = await fetch('/api/strategy/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id})
  });
  const d = await r.json();
  if (d.ok) {
    toast('Strategy started ✓');
    setTimeout(loadStrategies, 2000);
  } else {
    toast(d.error || 'Start failed', 'err');
    if (btn) { btn.disabled = false; btn.textContent = '▶ Start'; }
  }
}

async function stopStrategy(id) {
  if (!confirm('Stop this strategy? Open positions will remain — you manage exit manually.')) return;
  const r = await fetch('/api/strategy/stop', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id})
  });
  const d = await r.json();
  if (d.ok) {
    const n = (d.closed || []).length;
    toast(n > 0 ? `Stopped — closed ${n} position(s)` : 'Strategy stopped (no open positions)', 'ok');
  } else {
    toast(d.error || 'Stop failed', 'err');
  }
  setTimeout(loadStrategies, 1000);
}

async function toggleMode(id, mode) {
  const s = strategies.find(x => x.id === id);
  if (s && s.running) {
    toast('Stop the strategy before changing mode', 'err');
    return;
  }
  const r = await fetch('/api/strategy/toggle-mode', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id, mode})
  });
  const d = await r.json();
  if (d.ok) loadStrategies();
}

function editExpiry(id, currentExpiry) {
  const val = prompt('Enter new expiry (YYYY-MM-DD):', currentExpiry);
  if (!val || val === currentExpiry) return;
  const s = strategies.find(x => x.id === id);
  if (!s) return;
  fetch('/api/strategy/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({...s, expiry: val})
  }).then(r => r.json()).then(d => {
    if (d.ok) { toast('Expiry updated'); loadStrategies(); }
    else toast(d.error || 'Save failed', 'err');
  });
}

function switchStrategyType(stype) {
  ['opt-field','eq-field','fut-field'].forEach(cls => {
    document.querySelectorAll('.' + cls).forEach(el => el.style.display = 'none');
  });
  const show = stype === 'equity' ? 'eq-field' : stype === 'futures' ? 'fut-field' : 'opt-field';
  document.querySelectorAll('.' + show).forEach(el => el.style.display = '');
}
document.querySelectorAll('input[name="stype"]').forEach(r => {
  r.addEventListener('change', () => switchStrategyType(r.value));
});

function openModal(s) {
  editingId = s ? s.id : null;
  document.getElementById('modal-title').textContent = s ? 'Edit Strategy' : 'Add Strategy';
  document.getElementById('f-id').value    = s ? s.id    : '';
  document.getElementById('f-name').value  = s ? s.name  : '';
  document.getElementById('f-target').value   = s ? s.target   : 5000;
  document.getElementById('f-maxloss').value  = s ? s.max_loss : -8000;
  const mode  = s ? s.mode  : 'sandbox';
  const stype = s ? (s.strategy_type || 'options') : 'options';
  document.getElementById('m-sandbox').checked  = mode  === 'sandbox';
  document.getElementById('m-live').checked     = mode  === 'live';
  document.getElementById('st-options').checked = stype === 'options';
  document.getElementById('st-equity').checked  = stype === 'equity';
  document.getElementById('st-futures').checked = stype === 'futures';
  switchStrategyType(stype);
  if (stype === 'options') {
    document.getElementById('f-underlying').value = s ? s.underlying : 'NIFTY';
    document.getElementById('f-strategy').value   = s ? s.strategy   : 'short_strangle';
    document.getElementById('f-expiry').value     = s ? s.expiry     : '';
    document.getElementById('f-lots').value       = s ? s.lots       : 5;
  } else if (stype === 'equity') {
    document.getElementById('f-eq-underlying').value    = s ? s.underlying    : '';
    document.getElementById('f-eq-direction').value     = s ? (s.direction||'LONG') : 'LONG';
    document.getElementById('f-eq-qty').value           = s ? (s.qty||50)     : 50;
    document.getElementById('f-eq-target-price').value  = s ? (s.target_price||'') : '';
    document.getElementById('f-eq-stop').value          = s ? (s.stop_loss_price||'') : '';
    document.getElementById('f-eq-trail').value         = s ? (s.trailing_stop_pct ? s.trailing_stop_pct*100 : 2) : 2;
  } else {
    document.getElementById('f-fut-underlying').value = s ? s.underlying : 'NIFTY';
    document.getElementById('f-fut-direction').value  = s ? (s.direction||'LONG') : 'LONG';
    document.getElementById('f-fut-lots').value       = s ? (s.lots||1)   : 1;
    document.getElementById('f-fut-stop').value       = s ? (s.stop_loss_price||'') : '';
    document.getElementById('f-fut-trail').value      = s ? (s.trailing_stop_pct ? s.trailing_stop_pct*100 : 1) : 1;
  }
  document.getElementById('modal').classList.add('open');
}

function closeModal() {
  document.getElementById('modal').classList.remove('open');
  editingId = null;
}

function editStrategy(id) {
  const s = strategies.find(x => x.id === id);
  if (s) openModal(s);
}

async function saveStrategy() {
  const id    = editingId || document.getElementById('f-name').value.toLowerCase().replace(/\s+/g,'_');
  const stype = document.querySelector('input[name="stype"]:checked').value;
  let underlying, strategy, expiry, lots, direction, qty, target_price, stop_loss_price, trailing_stop_pct;
  if (stype === 'options') {
    underlying = document.getElementById('f-underlying').value;
    strategy   = document.getElementById('f-strategy').value;
    expiry     = document.getElementById('f-expiry').value;
    lots       = parseInt(document.getElementById('f-lots').value);
  } else if (stype === 'equity') {
    underlying  = document.getElementById('f-eq-underlying').value.toUpperCase().trim();
    strategy    = 'equity_trend';
    direction   = document.getElementById('f-eq-direction').value;
    qty         = parseInt(document.getElementById('f-eq-qty').value);
    target_price = parseFloat(document.getElementById('f-eq-target-price').value) || null;
    stop_loss_price = parseFloat(document.getElementById('f-eq-stop').value) || null;
    trailing_stop_pct = (parseFloat(document.getElementById('f-eq-trail').value) || 2) / 100;
  } else {
    underlying  = document.getElementById('f-fut-underlying').value;
    strategy    = 'futures_directional';
    direction   = document.getElementById('f-fut-direction').value;
    lots        = parseInt(document.getElementById('f-fut-lots').value);
    stop_loss_price = parseFloat(document.getElementById('f-fut-stop').value) || null;
    trailing_stop_pct = (parseFloat(document.getElementById('f-fut-trail').value) || 1) / 100;
  }
  const payload = {
    id,
    name:       document.getElementById('f-name').value,
    strategy_type: stype,
    underlying,
    strategy:   strategy || 'short_strangle',
    expiry:     expiry || '',
    lots:       lots || 1,
    target:     parseFloat(document.getElementById('f-target').value),
    max_loss:   parseFloat(document.getElementById('f-maxloss').value),
    mode:       document.querySelector('input[name="mode"]:checked').value,
    direction, qty, target_price, stop_loss_price, trailing_stop_pct,
  };
  const r = await fetch('/api/strategy/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const d = await r.json();
  if (d.ok) {
    closeModal();
    toast('Strategy saved ✓');
    loadStrategies();
  } else {
    toast(d.error || 'Save failed', 'err');
  }
}

// Auto-refresh every 15s
loadStrategies();
setInterval(loadStrategies, 15000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: set OPENALGO_API_KEY env var")
        sys.exit(1)
    underlying = os.environ.get("DASHBOARD_UNDERLYING", "NIFTY")
    target     = os.environ.get("DASHBOARD_TARGET", "6000")
    strategy   = os.environ.get("DASHBOARD_STRATEGY", "short_strangle")
    print(f"Dashboard starting on http://0.0.0.0:{PORT}")
    print(f"  Underlying: {underlying}  Target: Rs.{target}  Strategy: {strategy}")
    print(f"  Open in browser: http://34.45.46.60:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)


