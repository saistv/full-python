# Startup inherited-state flatten (P1-8) — design and task plan

Date: 2026-07-19
Audit finding: **P1-8** (no restart/recovery). Operator policy decision
(2026-07-19): **FLATTEN** — an inherited position or working order set found
at startup is closed via the Slice E confirmed-flatten protocol, never
adopted for trading.

## Why the doubling risk is already closed

Since Slice D1, an order-capable broker refuses to open with inherited state:
`hydrate_account_state` raises and latches on a position or working orders.
What P1-8 still needs is the RECOVERY flow — turning that dead-end halt into
a confirmed, journaled flatten followed by a fresh stable-flat start.

## Design

- `hydrate_account_state` stays STRICT (stable-flat only) — that invariant is
  load-bearing for the D2 runtime. The composition root inspects the
  hydration snapshot FIRST: inherited state routes to `startup_flatten`.
- **`TradovateBroker.startup_flatten(snapshot, *, timestamp_utc)`**: requires
  `flatten_enabled`; verifies the same account/contract identity as
  hydration; registers each inherited working order under a new
  `ROLE_INHERITED`; adopts the snapshot position solely so the Slice E
  protocol can close it; then enters the staged flatten
  (`reason="inherited_state_flatten"`, `requested_on_bar=timestamp_utc`) via
  the extracted `_begin_flatten` core that `flatten(bar, ...)` also uses.
  Misuse with a stable-flat snapshot raises.
- Progression is EXACTLY Slice E: cancels must confirm before the
  liquidation; an inherited stop filling first closes the position with the
  liquidation never submitted; cancel failure, liquidation rejection, and
  unknown outcomes halt latched.
- **No strategy trade is fabricated**: an exit fill with no open ledger leg
  (only possible for an inherited position) skips `close_leg` and strategy
  feedback; realized P&L re-enters through the account's own records at the
  next hydration.
- **Resolution stays `RECOVERY_REQUIRED`**: `_resolve_pending_flatten`
  already restores `NORMAL` only when no recovery latch is set. After the
  startup flatten confirms flat, the ONLY way entries reopen is a fresh
  stable-flat `hydrate_account_state` against new sync+REST agreement — the
  existing D1/D2 machinery, no special reopen path.
- A non-closing inherited fill (exposure grows during the flatten) hits the
  existing wrong-side/quantity guards in `_on_exit_fill` and halts.
- **`live/order_runner.run_startup_flatten(broker, pump, ...)`** drives the
  protocol before LiveLoop starts: loops `pump.pump()` until
  `broker.flatten_in_progress` clears or a wall-clock deadline raises
  (halt for operator review). If LiveLoop somehow starts with the flatten
  unresolved, Slice E's per-bar deadline halts on the first bar anyway.

Startup sequence in the composition root:
`hydrate_with_state()` → inherited state? → `startup_flatten` +
`run_startup_flatten` → fresh `hydrate_with_state()` →
`hydrate_account_state` → entries may reopen.

## Tasks

1. Broker: `ROLE_INHERITED`, `_require_snapshot_identity` (shared with
   hydration), `_begin_flatten` extraction, `startup_flatten`,
   `flatten_in_progress` property, `_on_exit_fill` no-open-leg guard.
   Tests: full staged sequence ending RECOVERY_REQUIRED then reopened by
   fresh hydration; stop-fill race; flatten-disabled, identity-mismatch,
   misuse, cancel-failure raises; non-closing inherited fill halts; no
   strategy feedback anywhere in the flow.
2. Runner: `run_startup_flatten` loop with deadline. Tests: scripted
   pump-to-resolution; timeout raises.
3. Docs: decision record + HANDOFF §5/§6; both suites green; PR.

Non-goals: partial inherited quantities (Slice F), adopt-and-protect
(explicitly rejected by the operator), any live-flag change.
