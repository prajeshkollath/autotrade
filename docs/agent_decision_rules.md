# NIFTY Short Strangle — Agent Decision Rules
**Strategy:** Short Strangle (Sell OTM CE + OTM PE)
**Engine:** `behavioral_checks()` in `agents/position_manager.py`
**Last reviewed:** 2026-06-17

---

## How It Works

Every 5-minute bar, `behavioral_checks()` is called before the LLM. If a rule fires, it returns a `Decision` immediately — LLM is bypassed. Rules are evaluated in strict priority order. The first rule that fires wins; nothing below it is evaluated.

After any **PARTIAL_EXIT**, a **second pass** runs in the same bar with a restricted rule set.

---

## Rule Priority (highest → lowest)

### 1. R1 — Per-leg Max Loss
**Condition:** `position.pnl < −Rs.6,000`
**Action:** `PARTIAL_EXIT` — close that leg immediately
**Notes:**
- Fixed absolute threshold, not context-aware
- If two legs breach in the same bar, only the first is closed (next bar catches the second)
- **Known issue:** Rs.6,000 is too permissive in a low-profit session and too tight in a high-profit session. Should be premium-relative.
- **Planned fix:** `max_loss_per_leg = max(Rs.6,000, entry_premium × lot × 0.8)`

---

### 2. R2 — Premium Doubled (1.5×)
**Condition:** `premium_ratio > 1.5×`
**Action:** `PARTIAL_EXIT`
**Notes:**
- Premium ratio = current LTP / entry price
- For most NIFTY options (entry Rs.60–100), R1 fires before R2
- R2 only matters for high-premium entries (>Rs.80 where Rs.6,000 limit isn't hit first)
- Name says "doubled" but threshold is 1.5× — misleading
- **Planned fix:** Raise to 2.0× (true doubling), keep R1 as the cash-loss guard

---

### 3. R3 — Critical OTM (<0.5%)
**Condition:** `position.otm_pct < 0.5%`
**Action:** `PARTIAL_EXIT` — close before going ITM
**Notes:**
- 0.5% for NIFTY at 25,000 = ~125 points = 2–3 strike steps
- R3 floor is enforced in REBALANCE and TRAIL_PROTECT (new adds must be ≥ R3 floor OTM)
- **Known issue:** Near expiry (DTE 0–1), an option at 0.3% OTM will expire worthless — closing is unnecessary
- **Planned fix:** Skip R3 when `DTE ≤ 1 AND otm_pct > 0`
- **Known issue:** SUSTAINED_BE_BREACH does NOT enforce the R3 floor (could add within 0.5% OTM, triggering immediate R3 close)

---

### 4. REBALANCE — One-sided position recovery
**Condition:** ALL open legs are CE or ALL are PE (fully one-sided)

**Sub-rule A: REBALANCE_RESTORE**
- CE missing AND `spot < ce_exit_spot − 50` → ADD CE
- PE missing AND `spot > pe_exit_spot + 50` → ADD PE
- Uses `entry_spot` as fallback if `ce_exit_spot`/`pe_exit_spot` unknown
- Logic: wait for spot to prove the directional move has reversed before restoring

**Sub-rule B: REBALANCE_SAFE** (fires only when RESTORE condition not met AND not already done this incident)
- Trending up → ADD PE (safe side, away from spot)
- Trending down → ADD CE
- `rebalance_safe_done` flag prevents it from firing every bar
- Resets when REBALANCE_RESTORE fires

**Notes:**
- OTM for adds: `max(rebalance_otm_steps, R3_floor_steps) × strike_step / spot`
- `rebalance_otm_steps` starts at 5 (1.0% OTM for NIFTY), decrements by 1 after each protective add
- **Known issue:** If spot never reverses, RESTORE never fires and position stays one-sided. REBALANCE_SAFE fired once but RESTORE is waiting for a reversal that never comes.
- **Tension with POST-EXIT REBALANCE:** The second-pass post-exit rebalance now adds the opposite side immediately after any PARTIAL_EXIT. REBALANCE_SAFE (which adds the SAME side) is blocked in the second pass. These two must not conflict.

---

### 5. SUSTAINED_BE_BREACH
**Condition:**
- `bars_beyond_be ≥ 3` (spot outside a BE for 15+ min consecutive)
- `bars_since_last_add ≥ 3` (cooldown)
- `current_pnl > max_loss`
- `len(positions) ≥ 2`

**Action:** ADD opposite side, 1 step tighter than closest existing leg on that side

**Direction:**
- Spot below lower BE → ADD CE
- Spot above upper BE → ADD PE

**Notes:**
- `bars_beyond_be` resets to 0 when inside BEs or after firing
- `bars_since_last_add` is tracked via `ctx.bars_since_last_add`
- **Critical bug (pending fix):** No R3 floor check. `max(1, min_steps − 1)` can produce 1 step OTM = 0.2%, which R3 immediately closes. Should use `max(R3_floor, min_steps − 1)`.
- **Planned fix:** Add `_r3_floor_sb = int(0.005 × spot / step) + 2; _new_steps_sb = max(_r3_floor_sb, min_steps − 1)`
- **Planned improvement:** Only fire when `current_pnl > max_loss × 0.5` (don't add during deep losses)

---

### 6. TRAIL_PROTECT — Trailing profit lock
**Condition:**
- `peak_pnl ≥ Rs.3,000`
- `current_pnl < peak_pnl × 50%`
- `current_pnl ≥ 0` (loss mode handles negatives)
- `rebalance_otm_steps ≥ R3_floor` (else prints "cannot add" and skips)

**Action:** ADD 1 lot on safe side of drift
- Spot up from entry → ADD PE
- Spot down from entry → ADD CE
- If drift < 1 strike step (noise), uses recent bar direction instead

**State changes after firing:**
- `peak_pnl` resets to `max(current_pnl, 0)` (prevents re-firing against old peak)
- `rebalance_otm_steps` decrements by 1

**After PARTIAL_EXIT:**
- `peak_pnl = 0` immediately (PARTIAL_EXIT invalidates the old peak)
- TRAIL_PROTECT is blocked in the second pass (peak_pnl=0.0 is passed)

**Notes:**
- **Known issue:** 50% threshold fires on noise at low peaks (peak=Rs.3,200, fires at Rs.1,600 — a Rs.1,600 swing is normal). Threshold is too sensitive.
- **Planned fix:** Change to fixed drop: `current_pnl < peak_pnl − Rs.2,000` OR only allow when `peak_pnl > Rs.6,000`
- **Planned improvement:** Max 2 TRAIL_PROTECT adds per session to prevent overexposure

---

### 7. LOSS MODE — Recovery when session is in loss
**Activation:** `current_pnl < max(max_loss × 5%, −Rs.500)`

**Sub-rule A: LOSS_RECOVERY_ENTER**
- `last_loss_add_type is None` (first add in this loss episode)
- Spot outside BE → ADD (CE if below lower BE, PE if above upper BE)
- Trigger: `LOSS_RECOVERY_ENTER`

**Sub-rule B: LOSS_RECOVERY_CROSS**
- Already added before (`last_loss_add_type` is set)
- `prev_spot` was INSIDE BEs last bar, spot is now OUTSIDE
- → ADD same direction as the breach
- Trigger: `LOSS_RECOVERY_CROSS`
- Prevents firing every bar when stuck outside BE

**Sub-rule C: LOSS_RECOVERY_REBUILD**
- No BEs exist (realized losses too large, position is one-sided)
- `last_loss_add_type != missing_side` (haven't added this specific side yet)
- PE-only → ADD CE; CE-only → ADD PE
- Trigger: `LOSS_RECOVERY_REBUILD`

**If in loss but INSIDE BEs:**
- `return None` — holds; theta is working; BE_RECENTER is blocked below

**State changes:**
- `last_loss_add_type` set to added instrument
- `loss_otm_steps` decrements by 1 (starts at 6 = 1.2% OTM for NIFTY)

**Critical known issue:** `last_loss_add_type` NEVER resets when P&L goes positive. Scenario:
1. Enter loss → LOSS_RECOVERY_ENTER fires (sets `last_loss_add_type = "CE"`)
2. P&L recovers to positive (`prev_in_loss` becomes False)
3. P&L drops to loss again
4. ENTER won't fire (`last_loss_add_type` is set), CROSS won't fire (`prev_in_loss = False`)
5. **Dead zone — no recovery action fires**

**Planned fix:** In `backtest_replay.py`, reset `last_loss_add_type = None` when `not prev_in_loss AND current_pnl < threshold` (start of new loss episode detected).

---

### 8. BE_RECENTER — Profit-mode recentering
**Only fires if NOT in loss (loss mode's `return None` gates this)**

**Gates (all must pass):**
- Gate A: Bar time ≥ 09:30 IST (opening freeze)
- Gate B: `bars_since_last_add ≥ 3` (cooldown after any add OR PARTIAL_EXIT)
- Gate C: `|spot − entry_center| > 50pt` (prevents noise near original entry)
- Gate D: Existing legs on the add side must exist (don't add PE if no PE legs)
- Gate E: `current_pnl > max_loss × 50%`
- Gate F: `DTE > 2`

**Trigger:**
- Spot within **15%** of BE range from upper BE → ADD PE
- Spot within **15%** of BE range from lower BE → ADD CE

**Lot sizing (drift-adaptive):**
| Drift from center | Lots | OTM | Est. center shift |
|---|---|---|---|
| < 150pt | 2L | 1.5% | ~64pt |
| 150–250pt | 3L | 1.5% | ~84pt |
| > 250pt | 3L | 1.0% | ~112pt |

**Notes:**
- **Known issue:** 15% of range is relative. Narrow range (300pt) = fires at 45pt from BE. Wide range (800pt) = fires at 120pt. Behavior is inconsistent across sessions.
- **Known issue:** Adds 2–3 lots per fire. Combined with other rules, position count grows fast.
- **Known issue:** Gate C uses `entry_center` from 09:15 which is stale by 13:00.
- **Blocked in second pass:** Cannot fire after PARTIAL_EXIT (same bar or next 3 bars via cooldown).
- **Planned fix:** Add `current_pnl > Rs.2,000` minimum to prevent recentering on thin profits. Add total position count limit.

---

## Second-Pass Rules (after PARTIAL_EXIT in same bar)

When PARTIAL_EXIT fires, the following happens BEFORE moving to the next bar:

```
PARTIAL_EXIT executes
│
├── peak_pnl = 0.0          (invalidates TRAIL_PROTECT peak)
├── last_add_bar_idx = bar  (starts 3-bar BE_RECENTER cooldown)
│
├── Second pass behavioral_checks (peak_pnl=0.0 passed)
│     BLOCKED:  TRAIL_PROTECT, BE_RECENTER, REBALANCE_SAFE
│     ALLOWED:  REBALANCE_RESTORE, LOSS_RECOVERY_ENTER/CROSS/REBUILD
│     _d2_added = True if something fires
│
└── POST-EXIT REBALANCE (only if _d2_added = False)
      PE was closed → ADD CE at rebalance_otm_steps OTM
      CE was closed → ADD PE at rebalance_otm_steps OTM
```

**Purpose of POST-EXIT REBALANCE:** Immediately add the opposite side of what was just force-closed. If a PE hit loss limit (spot moved down), add CE (spot is now further from CE strikes = safer entry).

---

## State Variables

| Variable | Initial | Updated by |
|---|---|---|
| `peak_pnl` | 0 | Grows with pnl each bar; reset to 0 on PARTIAL_EXIT; reset to `current_pnl` on TRAIL_PROTECT add |
| `rebalance_otm_steps` | 5 (1.0% OTM) | −1 after TRAIL_PROTECT, BE_RECENTER, or POST-EXIT REBALANCE add |
| `loss_otm_steps` | 6 (1.2% OTM) | −1 after each LOSS_RECOVERY add |
| `bars_beyond_be` | 0 | +1 each bar spot is outside BE; reset to 0 when inside BEs or after SUSTAINED_BE_BREACH fires |
| `last_loss_add_type` | None | Set to "CE"/"PE" on LOSS_RECOVERY_ENTER; **never reset (bug — see above)** |
| `ce_exit_spot` | None | Set to spot when CE is PARTIAL_EXITed; cleared when CE is re-added |
| `pe_exit_spot` | None | Set to spot when PE is PARTIAL_EXITed; cleared when PE is re-added |
| `rebalance_safe_done` | False | True after REBALANCE_SAFE fires; False after REBALANCE_RESTORE fires |
| `last_add_bar_idx` | 0 | Updated on any ADD_POSITION or PARTIAL_EXIT (for BE_RECENTER cooldown) |

---

## Known Issues / Backlog

| Priority | Rule | Issue | Planned Fix |
|---|---|---|---|
| P0 | LOSS MODE | `last_loss_add_type` never resets → dead zone in repeat loss episodes | Reset on new loss episode detection |
| P0 | SUSTAINED_BE_BREACH | No R3 floor → can add within 0.5% OTM → instant R3 close loop | Add `max(R3_floor, steps−1)` |
| P1 | All rules | No total position count limit → unlimited legs | Hard cap at 8 total open legs |
| P1 | R1 | Fixed Rs.6,000 not context-aware | Make premium-relative with Rs.6,000 floor |
| P1 | TRAIL_PROTECT | 50% threshold too sensitive at low peaks | Switch to fixed Rs.2,000 drop or add min-peak gate |
| P2 | R2 | 1.5× vs "doubled" naming inconsistency | Raise to 2.0× |
| P2 | R3 | Fires near expiry unnecessarily | Skip when DTE ≤ 1 |
| P2 | LOSS MODE | LOSS_RECOVERY_REBUILD gap for 2CE+0PE (imbalanced, not fully one-sided) | Extend to count-imbalanced cases |
| P2 | BE_RECENTER | 15% threshold inconsistent across range widths | Switch to fixed point threshold (e.g., 80pt from BE) |
| P2 | BE_RECENTER | Gate C uses stale entry center by afternoon | Decay entry center weight or remove by 11:00 |
| P3 | REBALANCE_SAFE | Tension with POST-EXIT REBALANCE | Review if REBALANCE_SAFE is still needed |
| P3 | TRAIL_PROTECT | Could fire 3–4× per session as peak resets | Max 2 fires per session |
