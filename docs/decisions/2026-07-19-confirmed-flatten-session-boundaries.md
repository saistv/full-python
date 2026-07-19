# Confirmed flatten and session boundaries (Slice E) — IMPLEMENTED OFFLINE

Date: 2026-07-19
Design: `docs/superpowers/specs/2026-07-14-broker-safe-execution-design.md` § Slice E
Plan: `docs/superpowers/plans/2026-07-19-confirmed-flatten-session-boundaries.md`
Audit: `docs/audits/2026-07-13-adversarial-audit.md`

## What changed

`TradovateBroker.flatten()` was a one-shot: best-effort cancels, then an
immediate liquidation, with `RECOVERY_REQUIRED` latched even for the routine
daily-loss flatten. It is now a staged, event-confirmed protocol:

1. **Request** — every working order gets a journaled cancel. A cancel
   submission failure halts immediately with the existing protection still
   standing (never liquidate blind). State: `FLATTEN_PENDING_CANCEL`.
2. **Confirm cancels** — `_ingest_cancel` removes confirmed ids from
   `PendingFlatten.awaiting_cancel_ids`. Only when the set empties is the
   liquidation submitted (`FLATTEN_PENDING_FILL`). If the protective stop
   FILLS before its cancel lands — the audit's P0-2 race — the position is
   closed by the stop and the liquidation is **never submitted**: two live
   closing orders can no longer coexist, so a DLL flatten can no longer
   reverse the position.
3. **Confirm flat** — resolution requires no position AND no working orders
   (P0-04); a residual working order latches recovery and halts. A routine
   confirmed flatten ends `NORMAL` — the P1-5 dead latch is removed — while
   `daily_limit_hit` still blocks entries for the session
   (`_entry_is_stable_flat` checks it independently). Emergency flatten keeps
   its latch semantics unchanged.
4. **Deadline** — an unresolved flatten on any later bar halts
   (`process_bar_open` raises; LiveLoop writes the durable `execution_halt`
   ledger entry — that is the external alert). One bar is the deadline
   because every cancel/fill confirmation for a marketable order arrives
   within the same one-minute bar on this feed.

**Session-close backstop (P0-03):** `process_bar_open` now triggers the same
staged flatten at `session.rth_close_minutes_et - 1` whenever a position or
working order still exists — the exchange calendar (pinned to
`tests/fixtures/cme_equity_rth_close.json` since PR #25) is the authority, so
early-close sessions flatten at close−1 instead of holding into a closed
market waiting for a 15:59 that never comes. This is strategy-independent
belt-and-suspenders under the strategy's own backstop exit; with
`flatten_enabled=False` reaching the boundary with a position halts instead.

Entry fills whose cancel was requested by a pending flatten now trigger the
existing "filled after flatten cancellation" emergency path (the guard was
previously predicated only on the recovery latch that routine flattens no
longer set). A flatten-liquidation rejection latches and halts without a
second emergency liquidation attempt.

## Evidence

- 11 new tests in `tests/test_tradovate_broker.py` (§ "Slice E"): staged
  DLL sequence ending NORMAL with entries still blocked, the P0-2 stop-fill
  race with zero liquidations, cancel-failure halt with protection standing,
  liquidation rejection without re-attempt, residual-working-order refusal,
  one-bar deadline halt, early-close and 15:59 backstop triggers, no-fire
  before close−1 or when flat.
- 7 existing flatten tests updated from the one-shot to the staged protocol
  (same safety intent, staged mechanics).
- Offline suite: **736 passed, 5 skipped**. With
  `FULL_PYTHON_BASELINE_DATA` (9-month anchor): **740 passed, 1 skipped** —
  simulation/PaperBroker/LiveLoop identity untouched (this slice changes
  only the Tradovate adapter).

## Status of audit findings

- **P0-2 CLOSED in offline code** (flatten awaits cancel confirmation).
- **P0-04 partially closed**: flat + no-working-orders is now confirmed at
  resolution and deadlined; the REST leg of post-liquidation confirmation
  rides the D2 runtime's event-and-interval reconciliation, and the attended
  DEMO liquidation drill remains open.
- **P1-5 CLOSED** (by subtraction: routine flattens no longer latch; the
  states that do latch are consumed by hydration or are terminal halts).
- **P0-03 CLOSED in offline code** (calendar-driven close−1 backstop,
  early closes included).

## Still open (unchanged by this slice)

P1-6 (no production account-event pump / composition root), P1-7 (no
RiskManager veto in the broker), P1-8 (restart/inherited-position recovery),
Slice F (partial quantities + full adversarial failure matrix against a
protocol-faithful fake server), shutdown flatten wiring (deliberately left
operator-owned until the composition root exists to call it), the real DEMO
split-sync envelope (P1-01), and every attended Gate 5+ drill. Nothing may
trade live; `order_enabled`/`flatten_enabled` remain default-False.
