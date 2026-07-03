# First TradingView Reconciliation — Adaptive Trend, Real NQ Data

## Setup

- Data: Databento GLBX ohlcv-1m raw contracts → canonical NQ1!-style front-month
  continuous via `full_python.data.databento` (roll = expiry − 3 calendar days,
  the legacy "tv-nq1-v1.2" fit). Window 2025-10-01 → 2026-06-26, 260,681 bars,
  structurally clean, rolls NQZ5→NQH6 (Dec 16), →NQM6 (Mar 17), →NQU6 (Jun 16).
- Rule-14 spot check passed before any aggregate: TV trade #1 fill 26,084.50 =
  our 2025-10-28 13:32Z bar open 26,083.75 + 3 ticks slippage, exactly.
- Sim: `--strategy adaptive_trend` (flat 1-contract), point value 20,
  slippage 0.75/0.75 to mirror the TV run, commission $10 RT.
- TV export: `AT-RSRCH_CME_MINI_NQ1!_2026-07-03_9e40f.csv` — the research fork
  on NQ1!, **with `am=1-4` and `dll=$1000` enabled** (read from the embedded CFG
  comment), first trade 2025-10-28 (TV 1m history limit), 106 trades ≤ Jun 26.

## Result

**105 of 106 TV trades matched (99.1%), every match on the exact same minute;
104 of 105 with an entry price delta of exactly $0.00.** 96/105 exit prices
exact. One TV trade missing in sim; 24 sim extras.

## Mismatch classification (every one explained except one bar)

| Class | Count | Explanation |
|---|---|---|
| TV history coverage | 9 extras (Oct 1–22) | Sim window starts before TV's 1m chart history; out of scope |
| DLL projected-risk guard | 13 extras | On each day TV took one ~$650 stop, leaving < one full-stop of $1K daily budget — the guard blocked TV's next entry; the flat sim took it. Known config difference (sim is deliberately flat until M2b) |
| June roll basis | 1 matched trade (TV#102, ±305.5 on entry and exit) | TV rolled NQ1! to NQU6 on Mon Jun 15; the fitted rule rolls Tue Jun 16. Dec and Mar rolls matched exactly. Signal fired the same minute on both price bases |
| Flatten fill timing | 7 exits, ≤1.0pt | Sim fills the 15:59 backstop at that bar's close; TV fills at the next bar's open |
| Half-day close | 1 exit (TV#13, 8pt) | Session ended without a 15:59 bar; sim closed on the session's last bar |
| **May 13 — unexplained** | 1 missing + 2 knock-on extras | TV shorted 09:32 (stop-capped entry); sim did not fire that bar (data present, no gap), then fired 09:37/09:48 while TV was positioned/blocked. One gate disagreed on one bar in 8 months — bar-level debug open |

## Conclusions

1. The port is behaviorally correct: signal timing is minute-exact and entry
   pricing dollar-exact across 8 months, both directions, three contract rolls.
2. The remaining work is mechanical: (a) flat TV re-export (AM off, DLL off) to
   collapse the DLL class; (b) bar-level debug of 2026-05-13 09:32; (c) TV roll
   dates need an observed-override table rather than a pure rule (June rolled
   Monday); (d) optional: flatten fill at next bar open for exit-price parity.
3. Only after (a)–(b) drive the in-scope match to explained-100% does M2b (AM
   sizing + DLL guard port) start, reconciled separately.

---

# Same-day update: flat re-export gate — 120/120 EXACT

The owner provided the flat export (`am=off|dll=off` confirmed via the embedded
CFG comment; all legs qty=1). Result vs the same continuous dataset:

- The 13 DLL-class extras all matched flat TV trades — the projected-risk-guard
  explanation was confirmed empirically, not assumed.
- The May 13 divergence was root-caused **from the event ledger alone**: the
  9:31 rejection said `sr_not_confirmed`, and the bar data showed tied lows at
  09:22/09:23 (29,283.25). Pine's pivot tie semantics were then determined
  experimentally by running all three plausible rules over the full dataset:

  | Tie rule | Result |
  |---|---|
  | strict both sides (legacy port) | misses 2026-05-13 |
  | non-strict right (earlier tied bar wins) | fixes May 13, breaks 2026-01-19 |
  | **non-strict left (LATER tied bar wins)** | **120/120 exact** |

  `PivotHigh`/`PivotLow` now implement non-strict-left/strict-right, verified
  against TradingView on two independent real tie dates.

**Final: 120/120 trades matched (100%), all entries minute-exact, 119/120 at
$0.00 entry price delta.** The single nonzero delta is the June roll-basis
trade (TV rolled NQ1! Monday Jun 15 vs the fitted Tuesday rule) — a data
calendar artifact, not strategy logic. The 9 sim extras before 2025-10-28 are
outside TV's 1-minute history and out of scope.

The M2 authority gate is passed for the flat signal core. M2b (anti-martingale
sizing + DLL guard) may proceed, reconciled separately against the AM/DLL
export from the same day, whose blocking behavior is now a known test target.
