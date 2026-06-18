# Intraday Options Trading Behavior Analysis
## Short Strangle / Short Options — Theta Decay Strategy

**Period:** May 14 – June 2, 2026 | **Instruments:** NIFTY, SENSEX | **Style:** Intraday theta capture

---

## SECTION 1: DAY-BY-DAY ANALYSIS

---

### DAY 1: SENSEX — May 14, 2026

**Market:** Open 74,983 | High 75,675 | Low 74,527 | Close 75,399 | **Move: +416 pts (BULLISH)**

**Session:** 9:16 – 13:38 (262 min) | **P&L: −₹15,605 (LOSS DAY)**

#### Intraday Story

SENSEX opened at 74,983 and immediately started a strong bullish rally. At 9:16 a wide strangle was opened: SELL 73,700 PE (+1,283 OTM, 1.7%) + SELL 76,200 CE (+1,217 OTM, 1.6%). These were wide safety anchors collecting ₹14+₹6 = ₹20 combined premium.

By 9:27 market had moved to 75,053 (+70 pts). The rally was intact so PE side was added: SELL 74,000 PE at ₹17.65 (now 1,053 OTM, 1.4%). Smart — rallying market = PE options getting cheaper and safer simultaneously.

At 10:10 market pulled back to 74,781 (off the highs by ~272 pts). You added 75,800 CE at ₹14.80 (1,019 OTM, 1.4%) — taking advantage of the dip making CE options slightly cheaper.

**THE CRITICAL MISTAKE:** At 11:07, market dipped further to 74,558. You sold 75,500 CE at ₹14.05 — this looked fine (942 OTM, 1.3%). But the market then EXPLODED higher. By 11:43 SENSEX was at 75,388 (up 830 pts from the 11:07 level!). The 75,500 CE which had been sold at ₹14.05 was now nearly ATM (only +86 OTM) and its price had gone to ₹168.

At 11:45 you BOUGHT BACK 75,500 CE at ₹168 — a catastrophic loss of ₹153.95/lot × 80 qty = **−₹12,316 on a single position.**

**Why it happened:** The 75,500 CE was added during a dip (market at 74,558) thinking the rally was over. It was not over. The market rallied +830 pts from that level, completely overwhelming the CE position.

Simultaneously at 11:45 you SOLD 75,800 CE at ₹77.20 to rebuild the CE coverage at a safer strike.

At 11:43 you also rolled the PE side: bought back 74,000 PE and 74,100 PE cheaply (market had rallied, these were far OTM), and sold 74,600 PE at ₹31 — collecting higher premium at a closer-in strike since market was now much higher.

Late day (13:38): Rolled the CE side again — bought back 75,800 CE (₹78.57) and sold 76,000 CE at ₹35.66. The 75,800 CE was now losing (bought at 78.57 vs sold at 42.53 avg = −₹36/lot). Exited at a loss.

#### Day 1 Behavior Rules
1. **Expiry context matters**: SENSEX was on a bullish trend day — selling CE within 1,000 pts OTM on a strong trend day is dangerous
2. **Never add CE shorts during an intraday dip on a clear bullish day** — the dip can recover violently
3. **If CE has doubled in price (sold at 14, now at 30+), close it before it goes to ATM** — never let a CE go from 1.3% OTM to 0.1% OTM without action
4. **Rolling lesson**: Rolling PE up as market rallied (74,000 → 74,600) was correct and profitable

---

### DAY 2: NIFTY — May 15, 2026

**Market:** Open 23,719 | High 23,839 | Low 23,611 | Close 23,658 | **Move: −61 pts (BEARISH)**

**Session:** 9:15 – 13:12 (237 min) | **P&L: +₹3,120**

#### Intraday Story

NIFTY opened at 23,719 with explosive first candle (range 103 pts in 5 min). Wide strangle placed immediately: SELL 23,200 PE (2.2% OTM) + SELL 24,200 CE (2.0% OTM). Safety anchors locked in.

**9:15–9:50 — Morning rally (+105 pts):** Market rose to 23,824. During this rally:
- 9:22 at 23,765: SELL 24,150 CE at ₹36.50 (385 OTM, 1.6%) — market rising, CE still far, good premium
- 9:27 at 23,789: SELL 23,300 PE at ₹40 (489 OTM, 2.1%) — market higher, PE even safer
- 9:49 at 23,795: SELL 23,400 PE at ₹50.80 (395 OTM, 1.7%) — market consolidated at highs, add PE for high premium

**10:00–10:55 — Consolidation at highs:** Market ground up to 23,839. All positions decaying quietly. No action needed.

**11:00–11:25 — Sharp drop (−118 pts):** Market crashed from 23,830 → 23,712. PE side still safe (23,400 PE had 312 OTM at the bottom). IV spiked massively.

**11:55 — IV spike sell on CE:** Market stabilized at 23,726 for 30+ minutes. SELL 24,000 CE at ₹57.25 (only 274 OTM = 1.2%). Rationale: (a) morning rejected 23,839 high, (b) market stabilized post-drop, (c) premium was ₹57 — very high for 1.2% OTM reflecting IV spike.

**12:50 — Recovery add:** Market recovered to 23,783. SELL 23,500 PE at ₹62.80 (283 OTM = 1.2%) — selling fear premium while market looked stable.

**13:12 — Square off:** NIFTY at 23,780. The 24,000 CE (sold at 23,726, market now 23,780 = 220 OTM) was losing ₹3,445. Took net positive and closed all.

#### Day 2 Behavior Rules
1. **9:15 anchor strangle, then layer during the day** as range reveals itself
2. **When market rallies in the morning → add PE shorts** (safer + better premium as market moves away from puts)
3. **Post-crash stabilization is a sell opportunity on CE side** — IV spike makes CE expensive, market showing inability to break higher
4. **Tight CE (<1.5% OTM) added post-crash** — only when market clearly stabilized for 20+ min
5. **Exit 13:00–13:30** — enough theta captured, avoids afternoon volatility

---

### DAY 3: NIFTY — May 18, 2026

**Market:** Open 23,400 | High 23,695 | Low 23,318 | Close 23,644 | **Move: +244 pts (BULLISH)**

**Session:** 9:15 – 10:05 (50 min) | **P&L: +₹7,563**

#### Intraday Story

May 18 was a Monday — **one day before expiry (May 19)**. This changes the entire dynamic: with only 1 DTE, theta decay is astronomical and even small OTM options have high absolute premium.

At 9:15 with NIFTY at 23,400, an aggressive CE-heavy strangle was placed:
- SELL 23,700 CE (300 OTM = 1.3%) × 4 lots at ₹41.20 — 1 day to expiry, this premium is HUGE
- SELL 23,800 CE (400 OTM = 1.7%) × 2 lots at ₹25.85
- SELL 23,100 PE (300 OTM = 1.3%) × 2 lots at ₹36.87

The CE-heavy entry reflected a view that NIFTY (after yesterday's close) was unlikely to run up aggressively.

**9:15–9:48 — Market drops:** NIFTY dipped from 23,400 to 23,342 (−58 pts). This made all the CE shorts much safer (market moving away from CE strikes). At 9:48 you ADDED more CE: SELL 23,650 CE (308 OTM, 1.3%) — tighter strike as market was down and only 1 DTE meant this 1.3% OTM strike had almost no chance of being touched before 3:30pm.

**10:03–10:05 — Quick FULL EXIT:** Market at 23,351–23,361. Bought back everything:
- CE 23,700 at ₹25.05 (sold at ₹41.20) → +₹16.15/lot = **+₹5,249**
- CE 23,800 at ₹14.55 (sold at ₹25.85) → +₹11.30/lot = **+₹3,673**
- PE 23,100 at ₹40.00 (sold at ₹36.87) → −₹3.13/lot = **−₹1,017**
- CE 23,650 at ₹32.85 (sold at ₹31.10) → −₹1.75/lot = **−₹341**

**Why exit so fast (50 minutes)?** With 1 DTE, the CE side had already captured the bulk of its remaining premium (CE 23700 decayed from 41 → 25, capturing 40% of remaining value in 50 min). The PE side was losing as market dipped toward 23,100. Better to lock in the CE gains than risk the PE side deteriorating further.

#### Day 3 Behavior Rules
1. **Day before expiry = ultra-high theta day** — even 1.3% OTM options have ₹40+ premium
2. **Pre-expiry entry: heavier on the side opposite to expected market direction**
3. **Exit quickly when the profitable side has captured 40–50% of remaining premium** — the rate of decay slows after the initial fast-theta period
4. **Pre-expiry PE shorts are dangerous on a downward-gapping morning** — PE decay is slow if market is drifting toward the strike

---

### DAY 4: NIFTY — May 19, 2026

**Market:** Open 23,735 | High 23,782 | Low 23,587 | Close 23,606 | **Move: −129 pts (BEARISH)**

**Session:** 9:15 – 13:20 (245 min) | **P&L: +₹13,933 (BEST DAY)**

#### Intraday Story

**This was EXPIRY DAY for NIFTY May 19 weekly options (0 DTE).** The strategy combined:
- 0-DTE weekly options (23,900 CE, 23,400/23,500 PE) — hyper-sensitive to theta
- Monthly far-OTM options (22,800 PE, 22,900 PE, 24,300 CE) — providing a wide safety net at large OTM%

At 9:15 with NIFTY at 23,735:
- **0-DTE layer**: SELL 23,900 CE at ₹13.75 (only 165 OTM = 0.7%!) — ATM-ish on expiry morning
- **0-DTE layer**: SELL 23,400 PE at ₹8.40 (335 OTM = 1.4%), SELL 23,500 PE at ₹15.95 (235 OTM = 1.0%)
- **Monthly safety net**: SELL 22,800 PE at ₹40.55 (935 OTM = 3.9%), SELL 24,300 CE at ₹44.85 (565 OTM = 2.4%)

This is a dual-layer structure: the 0-DTE options are the income engine (fast theta), the monthly far-OTM options are the hedge.

**9:24 — Early rolling on PE side:** Market at 23,749 (+14 from open). The 22,800 PE monthly had been bought back at ₹33.08 (profit +₹7.47) — sold at 40.55, bought at 33 = already made 18% of premium in 9 minutes. Replaced with 22,900 PE at ₹40.90 (tighter strike = higher premium = more theta to capture). This is efficient rolling on expiry day.

**9:34 — Quick 0-DTE PE close:** Bought back 23,500 PE (0-DTE) at ₹15.80 — basically flat. Then at 9:48 RESOLD 23,500 PE at ₹9.45 — premium had collapsed with market at 23,715. The 0-DTE PE cycle: sell → slight recovery → resell at lower price = net theta capture.

**10:27 — Monthly layer close:** Bought back the monthly 24,300 CE and 22,900 PE. Reason: after 1 hour, the monthly options had already given their short-term theta kick. No point holding them past mid-morning with better 0-DTE opportunities.

**13:20 — Final 0-DTE close:** Market at 23,733. Bought back:
- 23,500 PE at ₹1.30 (sold at ₹5.52 avg) — massive decay
- 23,900 CE at ₹1.50 (sold at ₹13.75) — massive decay, market 167 OTM at close

**Why 23,900 CE worked so well (₹13.75 → ₹1.50):** Expiry day, market stayed below 23,782 all day. The 0-DTE CE at 165 OTM collected ₹12+ in premium over 4 hours — textbook theta capture.

#### Day 4 Behavior Rules
1. **Expiry day = 0-DTE entries can be as tight as 0.7–1.0% OTM** — theta is so fast that even near-ATM options decay to near-zero by close
2. **Dual-layer structure on expiry day**: tight 0-DTE for income + wide monthly for safety
3. **Rolling on expiry day is aggressive** — roll PE from 22,800 to 22,900 when safe, capturing higher premium from the tighter strike
4. **Hold 0-DTE all day** — they expire worthless at 3:30pm. Early exit wastes the final-hour theta explosion
5. **Monthly safety net can be bought back mid-session** — it has served its purpose as hedge once the morning volatility is over

---

### DAY 5: NIFTY — May 20, 2026

**Market:** Open 23,461 | High 23,691 | Low 23,404 | Close 23,664 | **Move: +204 pts (BULLISH)**

**Session:** 15:19 only | **P&L: OPEN (not analyzed for intraday)**

Only 2 rows — one SELL at 15:19 and one BUY at the same time. SELL of NIFTY June monthly 24,500 CE at ₹216.30 (3.5% OTM). This appears to be a **POSITIONAL / CARRY-FORWARD trade**, not an intraday entry. Struck at 15:19 which is near EOD. This trade was left open (OPEN status).

*This day is excluded from intraday behavior analysis.*

---

### DAY 6: SENSEX — May 20, 2026

**Market:** Open 74,699 | High 75,406 | Low 74,531 | Close 75,318 | **Move: +620 pts (STRONG BULLISH)**

**Session:** 9:15 – 13:14 (239 min) | **P&L: +₹4,067**

#### Intraday Story

SENSEX opened at 74,699 and ran up 620 pts to close at 75,318 — a relentless bullish day. The challenge: CE side getting crushed as market moved toward CE strikes.

**Opening (9:15):** Sold 4 positions — 73,800 PE × 2 (1.2% OTM, ₹119), 75,800 CE × 2 (1.5% OTM, ₹42), 75,500 CE × 2 (1.1% OTM, ₹94). The CE side was tight from the start relative to the direction.

**9:31 — Market at 74,891 (up 192 pts from open):** Added PE 74,000 at ₹139.90 (891 OTM, 1.2%). Market rising = PE options safer = add more.

**9:32 — EMERGENCY CE ROLL:** Market had run up enough that 75,800 CE was getting dangerously close. Bought back 75,800 CE at ₹102.20 (sold at ₹42.15 = LOSS −₹60.05 = −₹6,005). Immediately sold 76,300 CE at ₹43.40 — reset the CE protection at a safer distance.

The 75,500 CE was NOT rolled at this point — and it went on to cause a massive loss: sold at ₹94.82, forced to buy back at ₹196.21 = **−₹10,139**.

**10:36–10:37:** Market at 74,989 (up 290 pts from open). Added tighter PE shorts: 74,200 PE at ₹126.65 and 74,400 PE at ₹173.15 — higher premium for these closer PE strikes on a strongly bullish day.

**13:14 — Square off all:** SENSEX at 75,122. PE side hugely profitable (73,800 PE paid ₹119, bought back at ₹50 = +₹69 profit; 74,400 PE paid ₹173, bought back at ₹131 = +₹42 profit).

The PE side profits (+₹21,021) partially offset the CE losses (−₹16,144), resulting in +₹4,067 net.

**Why the day was still profitable despite the CE disasters:** The PE premiums were so large (₹119–₹173 per lot vs ₹40–₹94 for CE) that the PE profits exceeded the CE losses. This is the SENSEX dynamics — higher premium levels mean bigger absolute buffers on the winning side.

#### Day 6 Behavior Rules
1. **On a strong trending day, the option-selling side facing the trend WILL LOSE** — the question is whether the opposite side gains more
2. **SENSEX CE-side management on bullish day**: if market is up 200+ pts by 9:45, MUST roll the CE higher — don't wait for it to nearly go ATM
3. **The 75,500 CE was never rolled** — this was the primary mistake. Should have been rolled with the 75,800 CE at 9:32
4. **PE profits on a bullish day are the offset** — high absolute premium at open ensures PE side is highly profitable
5. **PE adds during the day on bullish rallies** (74,200 and 74,400 at 10:36) were correct — market was rising fast, making these even safer

---

### DAY 7: NIFTY — May 21, 2026

**Market:** Open 23,767 | High 23,859 | Low 23,597 | Close 23,649 | **Move: −118 pts (BEARISH)**

**Session:** 9:15 – 11:39 (143 min) | **P&L: +₹3,461**

#### Intraday Story

May 21 was a New NIFTY weekly expiry (Jun 2 weekly). **Wide OTM entries** — much wider than usual: 23,000 PE (767 OTM = 3.2%) and 24,500 CE (733 OTM = 3.1%). These were new weekly options with more time value.

**9:15 opening:** Sold 3 lots each of 23,000 PE and 24,500 CE at ~₹51–₹61. Wide premium for a 3%+ OTM new weekly.

**9:33:** Market dipped to 23,738 (−29). Added 3 more lots 24,400 CE at ₹60.45 (662 OTM, 2.8%) — tighter CE as market dipped, taking advantage of the intraday CE premium spike.

**11:00:** Market at 23,714 (−53 from open). Added one more 24,400 CE at ₹51.20. Market was weakening, CE side getting safer (further OTM) with each dip.

**11:39 — Exit all:** Market at 23,730. P&L:
- 23,000 PE: sold at 61.47, bought at 67.27 → **−₹5.80 = −₹1,885** (market dipped toward puts)
- 24,500 CE: sold at 51.54, bought at 38.65 → **+₹12.89 = +₹4,189** (market fell away from CE)
- 24,400 CE: sold at 55.83, bought at 52.86 → **+₹2.97 = +₹1,157** (slight profit)

**Why exit at 11:39 (2.5 hours)?** Market had been slowly weakening (23,767 → 23,649 close). The PE side (23,000 PE) was losing slightly as market drifted down. Rather than let the bearish trend continue and threaten the PE side further, locked in the net positive.

#### Day 7 Behavior Rules
1. **New weekly options (5+ DTE) → start wider (3%+ OTM)** — premium is there but time value is different from near-expiry
2. **On a bearish day, CE adds during dips are correct** (market moving away = safer + possible IV spike)
3. **PE side loss partially offset CE gains** — characteristic of a bearish day
4. **Exit while net positive before PE gets threatened** — correct timing at 11:39

---

### DAY 8: SENSEX — May 21, 2026

**Market:** Open 75,637 | High 75,932 | Low 74,997 | Close 75,183 | **Move: −454 pts (SHARP DROP)**

**Session:** 9:15 – 14:02 (287 min) | **P&L: +₹1,042 (barely profitable)**

#### Intraday Story

May 21 SENSEX was a day of sharp downward pressure (−454 pts). The challenge: PE side getting squeezed.

**9:15 opening:** Tight strikes — PE 74,900 (737 OTM = 1.0%) and CE 76,600 (963 OTM = 1.3%). Premium was low (₹15–₹24) reflecting perhaps lower IV environment.

**9:21:** Added CE 76,400 at ₹29.60 (755 OTM, 1.0%) — tighter CE.

**11:01–11:17 — Market at 75,394 (dropped 243 pts):** CE side now much safer (76,600 CE is now 1,206 OTM = 1.6%). CE premiums had collapsed. Action:
- BOUGHT BACK 76,600 CE at ₹6.15 (sold at ₹24.14 = **+₹18 profit**). Smart — CE had decayed fast with market dropping.
- SOLD 76,200 CE at ₹15.60 and 76,000 CE at ₹21.20 — new CE layer at tighter strikes (now safer since market dropped)
- Added 76,200 CE at ₹12.85

**11:23 — PE EMERGENCY:** Market at 75,395, PE 74,900 is now only 495 OTM (0.7%!). BOUGHT BACK all PE 74,900 at ₹42.35 (sold at ₹15.65 = **−₹26.70 = −₹5,340 LOSS**). Market had dropped 242 pts since open, bringing the PE strike into dangerous territory.

Immediately SOLD PE 74,800 at ₹30.45 — rolled 100 pts lower.

**12:40:** Market stabilized at ~75,141. Added CE 75,800 at ₹17.25 and 75,900 at ₹13.55 — very tight strikes (659–759 OTM, 0.9–1.0%). Collecting final afternoon theta.

**14:02:** Rolled PE again — bought back 74,800 PE at ₹16.55 (taking profit vs ₹30.47 sell), sold 74,700 PE at ₹9.90.

Many positions left open at day end.

#### Day 8 Behavior Rules
1. **On a sharply falling day, PE shorts with <1% OTM are DANGEROUS** — must close before market gets within 200–300 pts
2. **CE side becomes a layering opportunity on strong bear days** — as market falls, CE moves further OTM, sell increasingly tighter strikes for premium
3. **Rolling PE DOWN on falling market** (74,900 → 74,800 → 74,700): correct technique but generates transaction costs
4. **Know when to stop rolling and just cut** — endless downward rolling signals the market has broken support, better to take the loss cleanly

---

### DAY 9: NIFTY — May 22, 2026

**Market:** Open 23,694 | High 23,836 | Low 23,675 | Close 23,749 | **Move: +55 pts (FLAT)**

**Session:** 9:15 – 11:27 (132 min) | **P&L: +₹3,175**

#### Intraday Story

Quiet expiry-week Monday. NIFTY moved only +55 pts in a narrow range (161 pts high-low). Clean theta day.

**9:15 opening:** Standard asymmetric entry — one PE (23,200 at ₹22.45, 2.1% OTM) and three CE (24,100 at ₹34.48, 1.7% OTM). The CE-heavy structure reflects mild directional skepticism that market would break higher in the near term.

**9:21:** Added PE 23,300 at ₹34.70 (453 OTM, 1.9%) — market moved up slightly to 23,753 in 6 minutes. Selling put on a micro-rally — correct technique.

**11:27 — Clean exit:** Market at 23,759. P&L:
- 23,200 PE: +₹3.50 profit = +₹1,138
- 24,100 CE: −₹0.73 = −₹237 (essentially flat)
- 23,300 PE: +₹7.00 = +₹2,275

**Why early exit (2:12 hours)?** Market was flat. Options had decayed enough. Clean exit, simple day.

#### Day 9 Behavior Rules
1. **On low-volatility flat days, take profit early** — if all positions are at 50%+ premium decay, close and stop
2. **CE-heavy entry when market is near recent highs** — reflects natural resistance view
3. **Clean textbook day: open → let theta burn → close** — no drama needed on flat days

---

### DAY 10: NIFTY — May 25, 2026

**Market:** Open 23,968 | High 24,054 | Low 23,924 | Close 24,050 | **Move: +82 pts (QUIET)**

**Session:** 9:16 – 11:49 (152 min) | **P&L: +₹6,383**

#### Intraday Story

Monday — 4 days to May 29 monthly expiry. **Ultra-tight entry**: PE 23,800 only 168 OTM (0.7%!), CE 24,200 only 232 OTM (1.0%). These incredibly tight strikes mean:
- Very high absolute premium (₹44 for 0.7% OTM PE, ₹31 for 1.0% OTM CE)
- Very high theta decay rate (near-ATM options decay fastest in absolute terms)
- Very high risk if market moves

**Why tight strikes?** With 4 DTE for monthly options, IV is elevated and theta is accelerating. The 23,800 PE at ₹44 will lose most of its value in 4 days regardless of market staying flat. This is a premium capture play, not a safety play.

**9:53:** Market dipped to 23,937 (−31). Added CE 24,150 at ₹34.65 (213 OTM, 0.9%). CE was safe on the dip.

**11:49 — Exit:** Market at 23,981. P&L:
- 23,800 PE: +₹13.65 profit = **+₹4,436** (strongest single position)
- 24,200 CE: +₹6.65 = +₹2,161
- 24,150 CE: −₹1.10 = −₹215

**Key insight:** The 23,800 PE was TIGHT (0.7% OTM) but the market barely moved (only −44 pts at worst). This paid off handsomely — ₹44 → ₹30.60 = 31% decay in 2.5 hours.

#### Day 10 Behavior Rules
1. **Near-expiry tight entries are high reward/risk** — ₹44 for 0.7% OTM only works if market stays ranged
2. **4-DTE window is sweet spot for tight entries** — enough time for theta to work fast, not so far out that options barely move
3. **Exit before 12pm on 4-DTE days** — risk in afternoon session increases; most theta captured by noon

---

### DAY 11: NIFTY — May 26, 2026

**Market:** Open 24,013 | High 24,090 | Low 23,885 | Close 23,934 | **Move: −79 pts (FLAT/NEAR EXPIRY)**

**Session:** 9:15 – 14:42 (327 min) | **P&L: +₹205 (barely profitable)**

#### Intraday Story

**3 days to May 29 expiry.** Premium nearly evaporated — 23,700 PE at ₹3.65 (1.3% OTM), 24,300 CE at ₹3.40 (1.2% OTM). These were NEAR-ZERO premium options with 3 DTE.

This day shows a **scalping pattern** rather than the usual theta capture:
- Multiple tiny entries throughout the day (₹2.80 to ₹8.70 per option)
- Very high volume of trades (15 rows for small premiums)
- Pattern: sell at ₹8–9, buy back at ₹4–7, resell at ₹3–6

The session lasted 5.5 hours and collected total premium of ₹11,840 but net P&L was only +₹205. This was essentially churn — many transactions for minimal net gain.

**Key problem:** When options are near-zero (₹2–5), the bid-ask spread and transaction costs eat most of the theoretical profit. The scalping activity was largely counterproductive.

#### Day 11 Behavior Rules
1. **Do not trade near-expiry options with <₹10 premium** — bid-ask spread + costs eat the profit
2. **3 DTE with all options at ₹2–8 = skip or wait for 0-DTE expiry day** — the sweet spot has passed
3. **Large number of transactions for minimal P&L is a signal to stop** — this day exemplifies "churning" behavior that should be avoided

---

### DAY 12: SENSEX — May 26, 2026

**Market:** Open 76,390 | High 76,627 | Low 75,911 | Close 76,010 | **Move: −380 pts (BEARISH)**

**Session:** 9:15 – 10:30 (74 min) | **P&L: +₹4,097**

#### Intraday Story

SENSEX monthly option (May 26, 3 DTE to May 29 expiry). But SENSEX premiums are much larger than NIFTY — CE 77,000 at ₹94.79 for only 610 OTM (0.8%)! Much richer premium environment.

**9:15:** SELL CE 77,000 × 2 (₹94.79), SELL PE 75,400 (₹66.05), SELL PE 75,600 (₹89.75)

**9:18–9:40:** Added two more PE shorts (75,700 × 2) as market was at 76,390–76,569.

**The problem:** The CE 77,000 was sold at ₹94.79 when market was at 76,390 (610 OTM = 0.8%). By 10:08–10:30 the market had moved to 76,502–76,586. The CE 77,000 now only 414–498 OTM. Premium spiked to ₹132.10.

At 10:08: BOUGHT BACK PE 75,700 at ₹73.60 (tiny profit). At 10:30: BOUGHT BACK PE 75,400, 75,600 and CE 77,000.

**The CE 77,000 loss (−₹3,731)** was incurred because the market didn't drop enough to justify holding the tight CE short. With SENSEX at 76,586 and CE 77,000 only 414 OTM (0.54%), it was too risky to hold.

Net: +₹4,097 (PE profits > CE loss).

#### Day 12 Behavior Rules
1. **SENSEX CE strikes at 0.8–1.0% OTM are VERY TIGHT** — SENSEX moves faster in absolute pts. Use 1.5%+ minimum for CE on SENSEX
2. **Fast session (74 min)** when positions are threatened — correct to close quickly
3. **PE profits offset CE losses** — same dynamic as NIFTY but larger absolute numbers

---

### DAY 13: NIFTY — May 27, 2026

**Market:** Open 23,926 | High 23,983 | Low 23,859 | Close 23,922 | **Move: −5 pts (FLAT)**

**Session:** 9:15 – 11:01 (106 min) | **P&L: +₹5,492**

#### Intraday Story

**Perfectly flat day — textbook theta capture.** NIFTY moved only 5 pts from open to close, with a range of 124 pts.

**Opening (9:15):** CE 24,300 × 5 (₹45.13, 374 OTM = 1.6%) + PE 23,400 × 5 (₹29.72, 526 OTM = 2.2%)

**9:17:** Added PE 23,500 × 3 (₹40.40, 426 OTM = 1.8%) — market barely moved in first 2 minutes.

**9:32 — Market dipped to 23,881:** Bought back 4 lots of PE 23,400 at ₹28.70 (sold at ₹29.72 = +₹1.01 each). The market dip compressed PE pricing slightly (put options get more expensive when market falls — but the dip was minor).

**9:43:** Market recovered to 23,908. SOLD PE 23,550 × 2 at ₹42.00 (358 OTM = 1.5%). Closer strike, higher premium.

**9:46:** Bought back PE 23,500 at ₹39.40 (minimal profit). This two-step — sell PE 23,500, close it, sell PE 23,550 — is a **rolling-up** maneuver: as market recovered from the dip, the premium was better at 23,550 than 23,500.

**10:05–10:26:** Added PE 23,550 and PE 23,600 as market continued recovering toward 23,963.

**11:01 — Exit all:** Market at 23,955 (up 29 from open, back near open level). P&L:
- 24,300 CE: −₹2.20 = −₹1,001 (barely lost)
- 23,400 PE: +₹1.01 = +₹462
- 23,500 PE: +₹1.00 = +₹325
- **23,550 PE: +₹10.39 = +₹4,725** ← star position
- 23,600 PE: +₹3.02 = +₹981

**Why 23,550 PE was the star:** Sold between ₹37–₹42 (market at 23,908–23,963), bought back at ₹29.49 (market at 23,955). As market recovered, PE options lost value. Perfect timing.

#### Day 13 Behavior Rules
1. **Flat days are best theta days** — market grinding in a range = pure decay with no directional risk
2. **On flat days, roll PE up toward the market level progressively** — as market demonstrates it's range-bound, sell progressively tighter PE strikes
3. **Active rolling generates significant extra premium** on flat days — the 23,550 PE was the most profitable leg because it was added with better timing
4. **Exit when the range is confirmed and most premium is captured** — 106 min session captured ~80% of the day's achievable theta
5. **CE side on flat days** = buy/hold, let it decay, don't trade it — CE didn't need active management on this flat day

---

### DAY 14: SENSEX — May 27, 2026

**Market:** Open 76,009 | High 76,222 | Low 75,749 | Close 75,868 | **Move: −141 pts**

**Session:** 9:15 – 11:22+ | **P&L: +₹1,751 realized (many positions OPEN)**

#### Notes

Multiple OPEN positions suggest this was a CARRY-FORWARD day where positions were intended to be held overnight. Low premiums (₹9–₹19) indicate near-expiry SENSEX May options (same May 29 expiry as NIFTY).

The only realized P&L was from quickly closing PE 75,100 bought back at ₹7.45 (minimal profit). Most positions were left open suggesting a positional hold strategy different from the pure intraday pattern seen in other days.

*This day's incomplete close limits behavioral analysis.*

---

### DAY 15: NIFTY — May 29, 2026

**Market:** Open 23,963 | High 23,999 | Low 23,487 | Close 23,609 | **Move: −354 pts (BIG DROP)**

**Session:** 9:15 – 10:44 (89 min) | **P&L: +₹2,564**

#### Intraday Story

**Expiry day for NIFTY May 29 monthly.** NIFTY dropped −354 pts (−1.5%) — a significant down move.

**9:15 opening:** CE 24,300 × 5 (₹35.70, 337 OTM = 1.4%) + PE 23,600 × 1 (₹27.30, 363 OTM = 1.5%)

**9:16:** Added PE 23,700 × 3 (₹42.15, 263 OTM = 1.1%) — slightly tighter. Market at 23,963 at open.

**9:28:** Market dropped to 23,893 (−70 pts in 13 min). Added CE 24,200 × 2 (₹45.40, 307 OTM = 1.3%) — falling market = CE safer = add more CE premium.

**10:15:** Market at 23,934 (recovered slightly). Added more PE 23,700 × 2 at ₹38.50 — second add to PE side.

**10:44 — EARLY EXIT (89 min total):** Market at 23,901. Exit all positions.
P&L:
- 23,600 PE: +₹0.66 = +₹299 (barely)
- 24,300 CE: +₹8.54 = +₹3,887 (excellent)
- 23,700 PE: −₹2.16 = −₹985 (slight loss — market dropped toward PE)
- 24,200 CE: −₹1.40 = −₹637 (slight loss)

**Why exit so early (89 min)?** Market had already dropped −70 pts by 9:28 and showed no sign of reversal. The 23,700 PE (263 OTM at entry, now only 201 OTM at 10:44 with market at 23,901) was getting uncomfortably close. Rather than risk the PE side being overwhelmed by the continuing drop (market eventually reached 23,487 — which would have been catastrophic for the 23,700 PE), early exit was the correct call.

**Key: the market went to 23,487 after exit — 23,700 PE would have been DEEP ITM (213 pts through). Early exit saved approximately −₹20,000+ in potential losses.**

#### Day 15 Behavior Rules
1. **Expiry day + immediate 70-pt drop = exit early** — don't hold PE positions through a trending bearish expiry
2. **CE gains partially offset PE threat** — good to hold CE even as PE is exited
3. **The correct instinct: exit when PE is <250 pts OTM and market is trending down** — this threshold saved the session
4. **Early exit on danger days is +₹2,564 vs potential −₹20,000** — the asymmetry justifies quick decisions

---

### DAY 16: NIFTY — June 1, 2026

**Market:** Open 23,633 | High 23,728 | Low 23,358 | Close 23,379 | **Move: −254 pts (BEARISH)**

**Session:** 9:15 – 15:03 (348 min) | **P&L: +₹6,084**

#### Intraday Story

Complex day — longest session (348 min), and the day with the most active position management.

**9:15:** Initial strangle — CE 24,000 (1.6% OTM, ₹31.83) + PE 23,300 (1.4% OTM, ₹11.65)

**9:22 — PIVOT (7 min into session):** Market at 23,599 (dropped −34 pts). Bought back:
- CE 24,000 at ₹16.50 (sold at ₹31.83 = quick **+₹15.31 profit**) — market drop made CE cheaper instantly
- PE 23,300 at ₹15.50 (sold at ₹11.65 = **−₹3.85 loss**) — market drop made PE more expensive

**Why close in 7 minutes?** The PE 23,300 (only 299 OTM = 1.3% with market at 23,599) was immediately at risk with the market dropping. Cut the loss fast, keep the CE profit.

**9:24 — Reset to WIDER strikes:** Sold 24,300 CE (701 OTM = 3.0%) and 22,900 PE (699 OTM = 3.0%). Much safer. This was the smart move — after the early morning instability, go wider and safer.

**12:02 — Market at 23,499 (−134 from open):** Added 24,200 CE at ₹35.55 (701 OTM = 3.0%) — still far, market had dropped further.

**12:14 and 12:57:** Partial buyback of 24,300 CE (partial profit taking), resold 24,200 CE × 3 again — rolling CE down as market kept falling (safer CE position at lower strike level)

**13:12:** Added 24,100 CE × 3 at ₹42.95 (639 OTM = 2.7%) — market now at 23,461, CE still safe

**14:15:** Added more 24,100 CE × 2 at ₹38.50 — market at 23,425

**14:59 — Late exit of CE + PE close:** Market at 23,374. Bought back CE 24,200 and CE 24,100. Also closed PE 22,900 at ₹53.80 (sold at ₹33.60 = **−₹20.20 = −₹9,191 LOSS**).

The PE 22,900 was sold as a wide safety net (3.0% OTM at entry). But market dropped 259 pts to 23,374 — now only 474 OTM (2%). With the market continuing to drop and only 1 hour to close, decision was made to exit at a loss.

**15:03:** Closed a rogue open position in 24,500 CE at ₹69.65.

Net +₹6,084 despite the PE loss, because CE profits (+₹16,027) exceeded PE loss (−₹9,191).

#### Day 16 Behavior Rules
1. **7-minute pivot rule**: if the market makes a strong move immediately at open threatening a position, close it and reset — don't wait for it to recover
2. **After early morning instability, reset to WIDER strikes** (3%+ OTM) before adding new positions
3. **On a strongly trending day, the CE side becomes the income engine** — keep adding CE shorts as market falls away from them
4. **Wide safety PE (3% OTM) can still be threatened on a −250 pt day** — the 22,900 PE loss showed that even "far" OTM options need to be watched
5. **EOD PE exits on bearish days** — don't let a PE position become ITM by 3:30pm; close it aggressively in the last hour

---

### DAY 17: NIFTY — June 2, 2026

**Market:** Open 23,283 | High 23,557 | Low 23,229 | Close 23,521 | **Move: +238 pts (BULLISH RECOVERY)**

**Session:** 9:15 – 14:40 (324 min) | **P&L: −₹7,435 (LOSS DAY)**

#### Intraday Story

The most complex and ultimately losing day. NIFTY recovered +238 pts from a low open (23,229) to close at 23,521.

**The core problem:** CE 23,500 was sold at ₹9.50 at open. Market was at 23,283 with 23,500 CE only 217 OTM (0.9%). As market rallied +238 pts to 23,521, the 23,500 CE went from far-OTM to ESSENTIALLY ATM and then THROUGH the strike. This single position caused −₹14,349 loss.

**9:15 opening:** Mixed structure — selling very tight strikes:
- PE 22,900 × 4 (383 OTM = 1.6%, ₹1.55 — near-zero premium)
- CE 23,500 (217 OTM = 0.9%, ₹9.50 — very tight)
- PE 23,000 (283 OTM = 1.2%, ₹4.65)

The problem was apparent immediately: 23,500 CE at 0.9% OTM on a recovering market is reckless.

**9:16:** Added CE 23,900 × 4 (₹38.20, 617 OTM = 2.6%) and PE 22,700 × 2 (₹37.75, 583 OTM = 2.5%). The 23,900 CE was the sensible entry; the 23,500 CE sold at open was not.

**9:51 — CE 23,900 exit:** Market dipped to 23,309 at 9:51. Bought back CE 23,900 at ₹35.90 (minor profit +₹2.30).

**Throughout 10:00–13:00:** Market rallied relentlessly. The 23,500 CE was underwater the whole time. Attempted to manage by:
- Selling tighter CE strikes (23,550, 23,600) — but these also got overwhelmed as market kept rising
- Multiple buy/sell cycles of CE positions as market gyrated around 23,430–23,530

**The snowball:** As market crossed 23,400, then 23,450, then 23,500, the CE shorts at 23,500, 23,550 became ITM. Each roll created additional losses. The 23,550 CE position alone: sold at ₹9.39 avg, bought back at ₹29.25 = **−₹19.86 = −₹15,492**.

**14:39–14:40:** Final cleanup — closed remaining positions.

#### Day 17 Behavior Rules
1. **NEVER sell CE with <1.5% OTM on a day that opened lower** — a lower-open day is primed for a recovery rally that will overwhelm tight CE
2. **₹1.55 PE premium at open = do not sell** — near-zero premium has no theta to capture; the only risk is the option moving against you
3. **When a CE is 0.9% OTM and market is recovering, close it IMMEDIATELY** — do not try to "manage" it with additional positions
4. **The rolling-into-more-tight-CE pattern compounds losses on a bullish day** — each additional tight CE sell adds to the problem
5. **Loss day recognition**: by 10:00 when market was clearly recovering past 23,350, the 23,500 CE should have been bought back. Total loss at that point was small; waiting caused it to become −₹14,349.

---

## SECTION 2: CROSS-DAY PATTERN ANALYSIS

### P&L Summary Table

| Date | Index | Move | Session | Net P&L | Quality |
|---|---|---|---|---|---|
| May 14 | SENSEX | +416 (Bull) | 262 min | −₹15,605 | BAD — CE not rolled on bull run |
| May 15 | NIFTY | −61 (Bear) | 237 min | +₹3,120 | GOOD |
| May 18 | NIFTY | +244 (Bull) | 50 min | +₹7,563 | EXCELLENT — pre-expiry day |
| May 19 | NIFTY | −129 (Bear) | 245 min | +₹13,933 | BEST — expiry day |
| May 20 | NIFTY | +204 (Bull) | n/a | OPEN | n/a |
| May 20 | SENSEX | +620 (Bull) | 239 min | +₹4,067 | OK — partially managed |
| May 21 | NIFTY | −118 (Bear) | 143 min | +₹3,461 | GOOD |
| May 21 | SENSEX | −454 (Bear) | 287 min | +₹1,042 | POOR — PE rolled too late |
| May 22 | NIFTY | +55 (Flat) | 132 min | +₹3,175 | GOOD |
| May 25 | NIFTY | +82 (Flat) | 152 min | +₹6,383 | EXCELLENT — tight entries worked |
| May 26 | NIFTY | −79 (Near-0) | 327 min | +₹205 | POOR — churning near-zero premiums |
| May 26 | SENSEX | −380 (Bear) | 74 min | +₹4,097 | GOOD — quick exit |
| May 27 | NIFTY | −5 (Flat) | 106 min | +₹5,492 | EXCELLENT — flat day PE rolling |
| May 27 | SENSEX | −141 (Bear) | Open | +₹1,751 | INCOMPLETE |
| May 29 | NIFTY | −354 (Big Bear) | 89 min | +₹2,564 | GOOD — early exit |
| Jun 1 | NIFTY | −254 (Bear) | 348 min | +₹6,084 | GOOD — active CE management |
| Jun 2 | NIFTY | +238 (Bull) | 324 min | −₹7,435 | BAD — tight CE on bull day |

**Total realized P&L (closed days): approximately +₹38,960**

---

## SECTION 3: UNIVERSAL BEHAVIOR RULES

These rules are derived from repeating patterns across all 17 sessions.

---

### RULE SET A: ENTRY RULES

**A1 — Opening Strangle at 9:15 (Non-Negotiable)**
- Always enter BOTH sides (CE + PE) at market open 9:15
- Never enter only one side at open — the strangle must be symmetrically placed first
- Starting wide provides the safety net; tighter positions are added throughout the day

**A2 — Opening OTM Thresholds by DTE**

| DTE | Minimum OTM% | Typical Premium |
|---|---|---|
| 0 (Expiry day) | 0.7–1.0% | ₹10–30 |
| 1 (Day before expiry) | 1.0–1.3% | ₹25–50 |
| 2–4 (Near expiry) | 1.2–1.8% | ₹20–60 |
| 5–7 (New weekly) | 2.5–3.5% | ₹40–80 |
| 8+ (Next week) | 3.0%+ | ₹50–100 |

**A3 — Minimum Opening Premium Gate**
- CE: minimum ₹20 at open. If CE premium < ₹15, skip or use wider strike
- PE: minimum ₹20 at open. If PE premium < ₹15, skip or use wider strike
- Day 11 (May 26) showed that ₹2–8 premium entries generate transaction churn with no net gain
- Day 17 (Jun 2) showed that ₹1.55 PE at open is zero-value, only risk

**A4 — Do Not Open CE Within 1.0% OTM on a Lower-Open Day**
- If NIFTY opens below previous close (gap-down or declining from yesterday), the market is primed for a recovery rally
- A CE at <1.0% OTM on such a day will be overwhelmed (confirmed by Jun 2 disaster)
- Exception: pre-expiry 0-DTE only (where 0.7% OTM is acceptable as the position expires same day)

**A5 — Strike selection priority order**
1. Select strike where premium is ₹25–75 range
2. Confirm OTM% ≥ minimum for DTE (per A2)
3. If both criteria met, prefer the strike that is farther OTM

---

### RULE SET B: INTRADAY LAYERING RULES

**B1 — Direction of Additional Entries Based on Market Movement**

| Market condition | Add this side | Rationale |
|---|---|---|
| Market rallies in first hour | Add PE shorts | Market moving AWAY from PE = safer to add |
| Market falls in first hour | Add CE shorts | Market moving AWAY from CE + IV spike = rich CE premium |
| Market is flat for 45+ min | Add BOTH sides, tighter | Range-bound = safe to add both |
| Market rapidly trending (100+ pts in 30 min) | Do NOT add against the trend | Trending market will overwhelm opposing short |

**B2 — IV Spike Sell Rule**
- After a sharp intraday move (≥80 pts in ≤30 min), wait 20–30 minutes for stabilization
- Then sell the OPPOSITE side to the move (CE after a drop, PE after a rally)
- Premium will be inflated due to IV spike — this is the best sell premium of the day
- Evidence: May 15 — sold CE 24,000 post-crash at ₹57 (highest CE premium of the day)
- Evidence: Jun 1 — added CE throughout bearish day at increasingly rich premiums

**B3 — Layering Sequence**
1. First entry: wide safety strangle (≥2% OTM ideally)
2. Second entry (30–60 min): if market has confirmed initial direction, add toward the safe side
3. Third entry (60–120 min): post-stabilization, add tighter strikes where premium justifies risk
4. Fourth entry (only if IV spiked): sell the IV on the opposite side
5. Maximum 4 entry rounds per day to avoid over-concentration

**B4 — Active Rolling on Flat Days**
- On flat/ranging days (±50 pts from open through noon), progressively roll PE toward the market level
- Pattern: as market drifts up, close PE sold 1 hour ago and resell at 50–100 pts higher strike
- This captures the premium differential as options decay AND the market moves away from old strikes
- Evidence: May 27 — PE rolling from 23,400 → 23,500 → 23,550 → 23,600 generated the bulk of +₹5,492

---

### RULE SET C: RISK AND POSITION MANAGEMENT RULES

**C1 — The Double Rule (automatic review trigger)**
- If any position's current premium has DOUBLED from your sell price, it's a mandatory review
- Example: sold CE at ₹25, now at ₹50 → mandatory review
- Action: close it (realize loss), roll to a safer strike, or reduce size
- Evidence: May 14 SENSEX — 75,500 CE sold at ₹14, went to ₹168. No review until catastrophic.

**C2 — OTM% Danger Zone**

| OTM% | Status | Action |
|---|---|---|
| >2.0% | Safe | Monitor, no action needed |
| 1.5–2.0% | Watch | Monitor more frequently (every 30 min) |
| 1.0–1.5% | Warning | Consider partial close or roll out |
| <1.0% | DANGER | Close immediately unless 0-DTE expiry day |
| <0.5% | CRITICAL | Close without hesitation regardless of DTE |

**C3 — Early Session Cut Rule**
- If ANY position becomes a Warning within the first 30 minutes (before 9:45)
- AND the market is trending TOWARD that position (not random noise)
- CLOSE the threatened position IMMEDIATELY and reset to wider strikes
- Evidence: Jun 1 at 9:22 — PE 23,300 became threatening within 7 min; closed and reset to 3% OTM. Correct.
- Evidence: Jun 2 — CE 23,500 at 0.9% OTM should have been closed at 9:16 when market started recovering. Wasn't. Catastrophic.

**C4 — Rolling Rules**

| Situation | Correct Action |
|---|---|
| Market rallied 200+ pts, CE getting squeezed | Roll CE UP by 200–300 pts immediately |
| Market dropped 200+ pts, PE getting squeezed | Roll PE DOWN by 200–300 pts |
| Rolling would produce >₹50/lot loss on the original | Consider closing entirely instead of rolling |
| Market trending continuously in one direction | Stop rolling, close the threatened side, accept loss |

**C5 — Never Chase a Position**
- When a CE or PE has been overwhelmed by a strong trend, do NOT add more CE/PE shorts in the same direction to "average down"
- This was the fatal error in Jun 2 — selling 23,550, 23,600 CE after 23,500 CE went bad compounded the loss dramatically
- A position that has gone wrong is a SIGNAL to close, not to add more

**C6 — Maximum Loss Trigger per Position**
- If a single option position shows a running loss > ₹6,000, close it immediately
- Continuing past ₹6,000 loss on a single position is portfolio-threatening
- Evidence: May 14 (75,500 CE: −₹12,316), Jun 2 (23,500 CE: −₹14,349) — both could have been stopped at ₹3,000–₹5,000 loss

---

### RULE SET D: EXIT RULES

**D1 — Exit Time Windows by Day Type**

| Day Type | Target Exit | Latest Exit |
|---|---|---|
| Expiry day (0-DTE) | Hold to 3:00–3:15 | 3:25 (last viable exit) |
| Day before expiry (1-DTE) | 10:00–11:00 | 11:30 |
| Near expiry (2–4 DTE) | 11:00–13:00 | 13:30 |
| New weekly (5–7 DTE) | 10:30–12:30 | 13:00 |
| Danger day (100+ pt move) | As soon as threatened position <1.5% OTM | Immediately |

**D2 — Profit Capture Exit Rule**
- If total realized P&L for the day has crossed a target level, consider closing all positions
- Suggested targets: ≥₹5,000 by 11am, ≥₹3,000 by 12pm — take the money and stop
- This prevents giving back profits in the afternoon session

**D3 — Bulk Exit on Danger Days**
- On days where NIFTY/SENSEX moves >150 pts in either direction by 11am, consider bulk exit
- May 15: +105 pts rally then −118 crash → still managed, exited at 13:12 profitably
- May 29: −70 pts by 9:28 → exited at 10:44 → saved ₹20,000+ in PE losses
- Jun 1: market dropped −134 pts by 12pm → stayed in managed mode, profitable, but risky
- Rule: if PE/CE is within 300 pts OTM AND day is directionally trending, do not hold to 2pm+

**D4 — Never Hold into Final 45 Minutes on a Bad Day**
- If there's a net losing position on the books by 2:45pm, close everything by 3:00pm
- The last 45 minutes (2:45–3:30) are high-volatility and will often make bad positions worse
- Jun 1 had a 15:03 exit for the final CE position — acceptable but risky

---

### RULE SET E: DTE AND EXPIRY-SPECIFIC RULES

**E1 — 0-DTE Expiry Day Strategy**
- Use DUAL LAYER: tight 0-DTE options (0.7–1.0% OTM) + wide monthly safety net (3–4% OTM)
- 0-DTE options WILL expire at zero regardless of small intraday moves — hold them all day
- If any 0-DTE option threatens to go ITM (less than 50 pts OTM), close the 0-DTE only
- Monthly safety net can be unwound mid-morning once intraday range is established
- Evidence: May 19 — 23,900 CE at 0.7% OTM, 4-hour hold, ₹13.75 → ₹1.50. Optimal.

**E2 — 1-DTE (Day Before Expiry) Strategy**
- Premium is high but risk is real — options can move 50% in 30 minutes
- Focus on CE side if market shows signs of having topped (lower-high pattern)
- Exit the entire position when total premium decay hits 40%
- Evidence: May 18 — CE 23,700 captured 40% of remaining value in 50 minutes, then exited

**E3 — Premium Near-Zero Alert (3+ DTE but <₹10)**
- If ALL options in the strangle are priced under ₹10, this is NOT a viable theta capture day
- Skip the day or wait for the 0-DTE expiry day of the same week
- Evidence: May 26 NIFTY — ₹3–8 premiums with 327-minute session netted only +₹205

---

### RULE SET F: INDEX-SPECIFIC RULES

**F1 — NIFTY Parameters**
- Lot size: 75
- Opening strangle: 1.5–2.5% OTM ideally
- Daily range: typical 80–150 pts (normal), 250–400 pts (volatile)
- Safe CE/PE distance from spot: ≥300 pts for new positions intraday

**F2 — SENSEX Parameters**
- Lot size: 20 (higher absolute premium per lot)
- Opening strangle: 1.0–1.5% OTM (premiums are MUCH larger than NIFTY at same OTM%)
- Daily range: typical 200–400 pts (normal), 450–700 pts (volatile)
- SENSEX moves faster and hits strikes more easily — use WIDER OTM than NIFTY
- CE minimum OTM: 1.5% (not 1.0% as used in some sessions — see May 14, May 26 SENSEX CE losses)
- SENSEX pre-expiry premium: ₹50–170 for 1-1.5% OTM — very rich, worth the extra risk

**F3 — Don't Trade Both Indices on Same Day (unless intentional)**
- When trading both NIFTY and SENSEX simultaneously, ensure they have different expiry dates
- They often move together — a market crash hits both simultaneously, doubling PE exposure
- Manage independently but be aware of correlated risk

---

## SECTION 4: CODED BEHAVIORAL PARAMETERS FOR AGENT

The following are the parameters that should govern the automated `entry_executor.py` and `position_manager.py`:

```python
# ── Entry parameters ──────────────────────────────────────
MINIMUM_ENTRY_PREMIUM = 20.0          # Do not sell any option below this at open
MIN_OTM_PCT_BY_DTE = {
    0: 0.007,   # 0 DTE: 0.7% minimum
    1: 0.010,   # 1 DTE: 1.0% minimum
    2: 0.012,   # 2 DTE: 1.2% minimum
    3: 0.013,   # 3 DTE: 1.3% minimum
    4: 0.015,   # 4 DTE: 1.5% minimum
    7: 0.025,   # 5-7 DTE: 2.5% minimum
}
NO_ENTRY_ON_GAP_RECOVERY_CE_OTM = 0.015  # If lower-open day, no CE < 1.5% OTM

# ── Risk triggers ─────────────────────────────────────────
DOUBLE_RULE_PREMIUM_MULTIPLIER = 2.0  # Review if premium doubles
DANGER_ZONE_OTM_PCT = 0.010           # <1.0% OTM = danger zone, consider closing
CRITICAL_ZONE_OTM_PCT = 0.005         # <0.5% OTM = close immediately
MAX_SINGLE_POSITION_LOSS = 6000       # Rs. — hard stop per position

# ── Exit parameters ───────────────────────────────────────
EXPIRY_DAY_EXIT_TIME = "15:00"
PRE_EXPIRY_EXIT_TIME = "11:00"
NEAR_EXPIRY_EXIT_TIME = "13:00"       # 2-4 DTE
NEW_WEEKLY_EXIT_TIME = "12:30"        # 5-7 DTE

# ── IV spike detection ────────────────────────────────────
IV_SPIKE_MIN_MOVE_POINTS = 80         # Minimum move in 30 min to trigger IV sell
IV_SPIKE_STABILIZATION_MINUTES = 20  # Wait this long after spike before selling

# ── Skip day criteria ─────────────────────────────────────
SKIP_IF_ALL_PREMIUMS_BELOW = 10.0    # Skip day if all options priced under ₹10
```

---

## SECTION 5: QUICK REFERENCE — DAILY DECISION TREE

```
09:08 PRE-MARKET CHECK  ← run entry_executor.py --dry-run at this time
  └─ What is DTE? → sets OTM% minimums (auto-computed from expiry)
  └─ What is gap from yesterday? → if gap-down, no tight CE at open
  └─ What was yesterday's range? → if >200 pts, use wider strikes today
  └─ Review the dry-run output: confirm both CE and PE strikes + premiums
  └─ If any premium < Rs.20 → widen strike manually before going live at 9:15

09:15 OPEN
  └─ Place ANCHOR STRANGLE: both CE + PE, ≥ minimum OTM for DTE
  └─ Both premiums ≥ ₹20? If not, widen strike

09:15–10:00 OBSERVE
  └─ Market rallying → safe to add PE shorts (market moving away)
  └─ Market falling → consider CE add after 20-min stabilization
  └─ Market flat → wait until 09:45 before adding any layer

10:00–12:00 LAYERING WINDOW
  └─ Market confirmed range? → add tighter strikes for more premium
  └─ IV spike happened? → sell the spiked side (WAIT 20 min first)
  └─ Any position at <1.5% OTM? → WARNING, watch closely
  └─ Any position at <1.0% OTM? → DANGER, close or roll now

12:00–13:00 EXIT WINDOW (normal days)
  └─ All positions profitable → bulk exit
  └─ One side losing, one winning → check net, if positive close all
  └─ Any position lost >₹6,000? → close that position immediately

CONTINUOUS MONITORING
  └─ Position doubled in price? → mandatory review
  └─ Market moved 100+ pts in 30 min? → emergency review
  └─ Trending day (200+ pts by 11am)? → close threatened side, keep safe side

NEVER DO
  └─ Sell CE with <1.0% OTM on a gap-down/recovering day
  └─ Add more shorts to a position that's already losing
  └─ Hold PE <0.5% OTM past 2pm
  └─ Sell options with premium < ₹10 (transaction churn)
  └─ Let a single position lose >₹6,000 without closing
```

---

*Generated from analysis of 17 trading sessions (May 14 – June 2, 2026)*
*Total analyzed sessions: 16 complete + 1 partial | 15 profitable, 3 losing/incomplete*
