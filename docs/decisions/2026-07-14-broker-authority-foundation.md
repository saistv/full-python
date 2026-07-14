# Broker Authority Foundation

**Decision:** close principal-audit P0-02 in offline code by making broker
lifecycle feedback authoritative and preventing entry submission outside a
stable-flat execution state. Keep every order-capable environment prohibited;
the remaining broker P0/P1 findings are unchanged.

Parent design:
`docs/superpowers/specs/2026-07-14-broker-safe-execution-design.md`.

## Failure Closed

The Tradovate adapter previously submitted a second market entry while an entry
was working or a position was already open. Its fills changed broker state but
never reached `AdaptiveTrendStrategy.on_fill()`, so the strategy could continue
believing it was flat. Trade closures likewise never reached
`on_trade_closed()`, leaving cooldown and anti-martingale state stale.

## Implemented Authority Model

- `PositionEngine` and `TradovateBroker` now produce a common domain feedback
  stream containing entry `Fill` and closed `Trade` records.
- `SimulationEngine` and `LiveLoop` use one dispatcher and deliver feedback
  after broker/state-machine agreement, before the next strategy decision.
- `PaperBroker` delegates the same PositionEngine feedback stream, preserving
  simulator identity without a parallel callback implementation.
- Tradovate entry feedback uses the submitted intent's symbol and reason and is
  queued only after the adapter has an acknowledged protective-stop order ID.
- Tradovate close feedback is the exact trade returned by the fill-pairing
  ledger.
- `ENTRY_PENDING_FILL` distinguishes a working entry from stable state.
- Entry intents are rejected before REST when any entry/order/position/exit or
  recovery state makes the broker non-flat or uncertain.
- Confirmed entry rejection or cancellation returns to normal only when no
  recovery condition is latched. Unknown submission outcomes remain in
  `RECOVERY_REQUIRED` and cannot retry.

## Acceptance Evidence

- Repeated signal while entry is working: one REST market entry.
- Repeated signal after fill/protection: one REST market entry.
- Signal during stop-cancel/exit transition: no entry.
- Signal after unknown submission: no retry.
- Confirmed rejection/cancellation: later signal is permitted.
- Entry fill and fill-derived closed trade feedback drain exactly once.
- LiveLoop delivers entry feedback before the strategy's next `on_bar` call.
- Focused broker/live/simulation suite: 87 passed, 1 data-gated skip.
- Full offline suite: 426 passed, 4 data-gated skips.
- Full suite with `FULL_PYTHON_BASELINE_DATA`: 430 passed.

The baseline-backed suite proves the callback refactor did not change the
frozen simulator/PaperBroker trade sequence. It does not prove Tradovate broker
parity.

## Remaining Blockers

P0-03, P0-04, P0-05, P1-01, and P1-02 remain open: session/shutdown flatten,
contract-ID liquidation and confirmation, durable idempotent intent recovery,
account user-sync/startup hydration, and exact account/contract reconciliation.
Partial quantities and the full adversarial failure matrix also remain open.

The project classification remains **RESEARCH-ONLY**. Demo orders, paper,
funded MNQ, and unattended production remain prohibited.
