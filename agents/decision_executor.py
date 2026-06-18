"""
decision_executor.py — Maps agent Decision JSON → OpenAlgo REST orders.

Handles ALL actions automatically:
  HOLD          → no-op
  SHIFT_STRIKE  → close threatening leg + open new safer strike (auto-roll)
  PARTIAL_EXIT  → buy back specific instrument at MARKET
  FULL_EXIT     → close ALL open positions at MARKET
  ADD_POSITION  → sell new option leg (layering / IV spike)
  HEDGE_DELTA   → buy/sell NIFTY-FUT / BANKNIFTY-FUT
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).parent))
from goal_schema import Decision, ContextSnapshot
try:
    import session_memory as _sm_track
except ImportError:
    _sm_track = None

OPENALGO_BASE = "http://localhost:5000"

_MCX_UNDERLYINGS = {"GOLDM", "GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"}
_LOT_SIZE = {"BANKNIFTY": 15, "NIFTY": 65, "GOLDM": 1, "GOLD": 1, "SILVER": 30, "CRUDEOIL": 100, "NATURALGAS": 1250}

def _exchange_for(underlying: str) -> str:
    return "MCX" if underlying.upper() in _MCX_UNDERLYINGS else "NFO"

def _product_for(underlying: str) -> str:
    return "NRML" if underlying.upper() in _MCX_UNDERLYINGS else "MIS"

_OPT_RE = re.compile(r"^([A-Z]+?)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$")


def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Content-Type": "application/json"}


def _place_order(
    api_key: str,
    symbol: str,
    action: str,
    quantity: int,
    price_type: str = "MARKET",
    price: float = 0.0,
    exchange: str = "NFO",
    product: str = "MIS",
    strategy_id: str = "pm_default",
) -> dict:
    payload = {
        "apikey": api_key,
        "strategy": strategy_id,
        "symbol": symbol,
        "action": action,
        "exchange": exchange,
        "pricetype": price_type,
        "product": product,
        "quantity": str(quantity),
    }
    if price_type == "LIMIT" and price:
        payload["price"] = str(price)
    resp = requests.post(
        f"{OPENALGO_BASE}/api/v1/placeorder",
        json=payload,
        headers=_headers(api_key),
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_option_symbol(symbol: str) -> Optional[dict]:
    """Parse NIFTY16JUN2622600PE → {underlying, expiry, strike, opt_type}"""
    m = _OPT_RE.match(symbol.upper())
    if not m:
        return None
    expiry_dt = datetime.strptime(f"{m.group(2)}-{m.group(3)}-{m.group(4)}", "%d-%b-%y")
    return {
        "underlying": m.group(1),
        "expiry": expiry_dt.strftime("%Y-%m-%d"),
        "strike": int(m.group(5)),
        "opt_type": m.group(6),
    }


def _close_all_positions(api_key: str, owned_symbols: set = None) -> list[dict]:
    """Close open positions at MARKET. If owned_symbols given, only close those."""
    resp = requests.post(
        f"{OPENALGO_BASE}/api/v1/positionbook",
        json={"apikey": api_key},
        headers=_headers(api_key),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    positions = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(positions, list):
        positions = []

    results = []
    for p in positions:
        sym = p.get("symbol") or p.get("tradingsymbol", "")
        qty = int(p.get("quantity") or p.get("netqty") or 0)
        if qty == 0:
            continue
        if owned_symbols and sym.upper() not in owned_symbols:
            continue  # skip positions not owned by this strategy
        action = "SELL" if qty > 0 else "BUY"
        exchange = p.get("exchange", "NFO")
        product = p.get("product", "MIS")
        try:
            result = _place_order(api_key, sym, action, abs(qty), exchange=exchange, product=product)
            results.append({"symbol": sym, "result": result})
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})
    return results


def _execute_shift_strike(
    decision: Decision,
    context: ContextSnapshot,
    api_key: str,
    underlying: str,
) -> tuple[bool, str]:
    """
    Auto-roll: close the threatening leg, scan chain for safer strike, sell it.

    LLM must set decision.instrument = exact option symbol of the leg to roll.
    """
    if not decision.instrument:
        return False, "SHIFT_STRIKE: instrument not set by LLM — cannot roll"

    parsed = _parse_option_symbol(decision.instrument)
    if not parsed:
        return False, f"SHIFT_STRIKE: cannot parse symbol '{decision.instrument}'"

    opt_type = parsed["opt_type"]
    expiry = parsed["expiry"]
    current_strike = parsed["strike"]
    spot = context.underlying_price

    # Qty: from decision, else from context positions
    qty = abs(decision.quantity) if decision.quantity else 0
    if not qty:
        for pos in context.positions:
            if pos.symbol == decision.instrument:
                qty = abs(pos.qty)
                break
    if not qty:
        return False, f"SHIFT_STRIKE: cannot determine qty for {decision.instrument}"

    results = []

    # Step 1 — close current leg
    try:
        r1 = _place_order(api_key, decision.instrument, "BUY", qty, exchange=_exchange_for(underlying), product=_product_for(underlying))
        results.append(f"closed {decision.instrument} order={r1.get('orderid', r1)}")
    except Exception as e:
        return False, f"SHIFT_STRIKE: close failed: {e}"

    # Step 2 — find safer strike via premium scan
    try:
        from entry_executor import _scan_strike_by_premium, _build_option_symbol, _calc_dte
        dte = _calc_dte(expiry)
        new_strike = _scan_strike_by_premium(api_key, underlying, expiry, spot, opt_type, dte)

        # Enforce new strike is further OTM than the one we just closed
        from entry_executor import STRIKE_STEP as _STRIKE_STEP
        step = _STRIKE_STEP.get(underlying.upper(), 50)
        if opt_type == "CE" and new_strike <= current_strike:
            new_strike = current_strike + 4 * step   # push 200 pts further OTM for NIFTY
        elif opt_type == "PE" and new_strike >= current_strike:
            new_strike = current_strike - 4 * step

        new_symbol = _build_option_symbol(underlying, expiry, new_strike, opt_type)
    except Exception as e:
        return False, f"SHIFT_STRIKE: closed old leg but strike scan failed: {e} — CHECK POSITIONS"

    # Step 3 — sell new leg
    try:
        r2 = _place_order(api_key, new_symbol, "SELL", qty, exchange=_exchange_for(underlying), product=_product_for(underlying))
        results.append(f"opened {new_symbol} order={r2.get('orderid', r2)}")
    except Exception as e:
        return False, f"SHIFT_STRIKE: closed old leg but NEW leg SELL failed: {e} — CHECK POSITIONS"

    return True, " | ".join(results)


def execute_equity(
    decision,
    underlying: str,
    api_key: str,
    dry_run: bool = False,
    strategy_id: str = "pm_default",
) -> tuple[bool, str]:
    """Execute equity/futures decisions: ADD_POSITION, PARTIAL_EXIT, FULL_EXIT, MODIFY_STOP."""
    action = decision.action
    if action == "HOLD":
        return True, "HOLD"
    if action == "MODIFY_STOP":
        # No order — just log the new stop. Caller updates goal.stop_loss_price.
        return True, f"MODIFY_STOP new_stop={decision.new_stop_price}"

    if action not in ("ADD_POSITION", "PARTIAL_EXIT", "FULL_EXIT"):
        return False, f"Unrecognised action for equity/futures: {action}"

    symbol = (decision.instrument or underlying).upper()
    qty = decision.quantity or 1
    direction = decision.direction or "BUY"
    is_mcx = underlying.upper() in _MCX_UNDERLYINGS
    exchange = "MCX" if is_mcx else "NSE"
    # Equity = CNC for positional, MIS intraday. Futures = NRML for MCX, MIS for NSE intraday.
    # Simplified: use NRML for MCX futures, CNC for NSE equity (no MIS to avoid forced sq-off).
    product = "NRML" if is_mcx else ("NRML" if "FUT" in symbol.upper() else "CNC")

    if dry_run:
        print(f"[DRY RUN] equity/futures: {action} {direction} {qty} {symbol} {exchange} {product}")
        return True, f"DRY {action}"

    try:
        r = _place_order(
            api_key=api_key,
            symbol=symbol,
            action=direction,
            quantity=qty,
            price_type=decision.price_type or "MARKET",
            price=decision.price or 0.0,
            exchange=exchange,
            product=product,
        )
        ok = r.get("status") == "success"
        if ok and action == "ADD_POSITION" and _sm_track:
            _sm_track.add_owned_symbol(underlying, symbol, strategy_id)
        if ok and action == "FULL_EXIT" and _sm_track:
            _sm_track.clear_owned_symbols(underlying, strategy_id)
        return ok, f"{action} {symbol} x{qty} → {r.get('status','?')} {r.get('orderid','')}"
    except Exception as e:
        return False, f"Order error: {e}"


def execute(decision: Decision, context: ContextSnapshot, api_key: str, underlying: str, expiry: str = "", strategy_id: str = "pm_default") -> tuple[bool, str]:
    """
    Executes a decision. Returns (executed: bool, detail: str).
    """
    action = decision.action

    if action == "HOLD":
        return False, "HOLD — no order"

    if action == "SHIFT_STRIKE":
        return _execute_shift_strike(decision, context, api_key, underlying)

    if action == "FULL_EXIT":
        # Resolve owned_symbols so we only close this strategy's legs
        owned: set = set()
        if strategy_id:
            try:
                import sys as _sys_ex
                import os as _os_ex
                _ag = _os_ex.path.dirname(_os_ex.path.abspath(__file__))
                if _ag not in _sys_ex.path: _sys_ex.path.insert(0, _ag)
                import session_memory as _sm_ex
                sid = strategy_id.replace("pm_", "").strip() or strategy_id
                owned = set(_sm_ex.get_owned_symbols(underlying, sid))
            except Exception:
                pass
        results = _close_all_positions(api_key, owned or None)
        return True, f"FULL_EXIT: {json.dumps(results)}"

    if action == "ADD_POSITION":
        opt_type = (decision.instrument or "").upper()
        if opt_type not in ("CE", "PE"):
            return False, f"ADD_POSITION: instrument must be CE or PE, got {decision.instrument}"
        if not expiry:
            return False, "ADD_POSITION: expiry not provided"
        try:
            from entry_executor import _scan_strike_by_premium, _build_option_symbol, _calc_dte, _round_to_step, STRIKE_STEP
            spot = context.underlying_price
            dte = _calc_dte(expiry)
            _tgt_otm = getattr(decision, "target_otm_pct", None)
            if _tgt_otm and _tgt_otm > 0:
                # BE_RECENTER: bypass DTE minimum, place at specified OTM distance
                _step = STRIKE_STEP.get(underlying.upper(), 50)
                _otm_pts = int(spot * _tgt_otm)
                if opt_type == "PE":
                    new_strike = int((spot - _otm_pts) / _step) * _step
                else:
                    new_strike = (int(spot / _step) + 1) * _step + _round_to_step(_otm_pts, _step)
            else:
                new_strike = _scan_strike_by_premium(api_key, underlying, expiry, spot, opt_type, dte)
            new_symbol = _build_option_symbol(underlying, expiry, new_strike, opt_type)
            exchange = _exchange_for(underlying)
            product  = _product_for(underlying)
            lot_qty  = _LOT_SIZE.get(underlying.upper(), 65)
            qty      = decision.quantity or lot_qty
            result   = _place_order(api_key, new_symbol, "SELL", qty, exchange=exchange,
                                    product=product, strategy_id=strategy_id)
            if _sm_track:
                _sm_track.add_owned_symbol(underlying, new_symbol, strategy_id)
            return True, f"ADD_POSITION {new_symbol} SELL {qty}: {result.get('orderid', str(result))}"
        except Exception as e:
            return False, f"ADD_POSITION failed: {e}"

    if action in ("PARTIAL_EXIT", "HEDGE_DELTA", "ADD_HEDGE"):
        if not decision.instrument or not decision.direction or not decision.quantity:
            return False, f"{action}: missing instrument/direction/quantity"
        try:
            result = _place_order(
                api_key=api_key,
                symbol=decision.instrument,
                action=decision.direction,
                quantity=decision.quantity,
                price_type=decision.price_type,
                price=decision.price or 0.0,
                exchange=_exchange_for(underlying),
                product=_product_for(underlying),
            )
            order_id = result.get("orderid", str(result))
            return True, f"Order placed: {order_id}"
        except Exception as e:
            return False, f"Order failed: {e}"

    return False, f"Unknown action: {action}"
