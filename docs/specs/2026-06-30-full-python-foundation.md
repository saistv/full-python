# Full Python Foundation

## Objective

Create a clean Python-first trading research system that can become the eventual live execution authority after deterministic replay, shadow trading, paper execution, and broker reconciliation gates are proven.

## Current Architectural Decision

TradingView/Pine is frozen as a legacy baseline and charting reference. Full Python becomes the canonical system for strategy logic, research, replay, shadow trading, risk controls, and future execution.

## First Build Scope

The first build is not a live trader. It is the foundation:

- append-only event ledger with stable event IDs
- JSONL persistence for replay and shadow audit trails
- deterministic replay contracts
- strategy output types such as signal decisions, rejections, order intents, risk vetoes, stop updates, and exits
- risk manager boundary
- execution adapter interface with shadow mode first

## Implemented Foundation

- `EventLedger` records ordered events and can round-trip them through JSONL.
- `MarketBar` represents canonical OHLCV input.
- `StrategyResult` separates strategy decisions from execution.
- `ReplayEngine` feeds bars to a strategy and logs bar, signal, rejection, order-intent, risk-veto, stop-update, and exit events in deterministic order.

## Next Foundation Step

Add a real data-loading boundary that converts NQ/MNQ historical files into canonical `MarketBar` records without leaking vendor-specific format details into strategy or replay code.

## Explicit Non-Goals

- No direct Tradovate live execution yet.
- No TradersPost adapter yet.
- No auto-optimization changing live parameters.
- No live add-ons, anti-martingale, or experimental regime switching.
- No wholesale port of the old 2,151-line Pine script.

## Promotion Path

1. Deterministic historical replay.
2. Live shadow trading with no orders.
3. Paper execution with full reconciliation.
4. Limited live execution with smallest practical size.
5. Broader live automation only after logs prove safe behavior.
