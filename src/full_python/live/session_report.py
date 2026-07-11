"""Post-session shadow report for observe-mode sessions.

Replays the recorded bars through the identical no-fill strategy stack
and diffs the signal streams. In observe mode the live strategy never
receives fills (orders are disabled), so the like-for-like comparison
is a fill-free replay: same bars, same production config,
on_bar_context(0.0, False) every bar -- exactly what the live wrapper
saw. Full fill-level parity belongs to the later order-test/paper
gates.

Also renders an informational "what the sim would have traded" section
using the frozen baseline simulation config; it plays no part in the
PARITY verdict.
"""
from __future__ import annotations

import html as html_module
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from full_python.events import EventLedger, EventType
from full_python.models import MarketBar
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import production_am_config

logger = logging.getLogger("full_python.live")


def bars_from_ledger(ledger: EventLedger) -> list:
    bars = []
    for record in ledger.records:
        if record.event_type is not EventType.BAR:
            continue
        payload = record.payload
        bars.append(MarketBar(
            timestamp_utc=record.timestamp_utc,
            symbol=str(payload["symbol"]),
            open=float(payload["open"]),
            high=float(payload["high"]),
            low=float(payload["low"]),
            close=float(payload["close"]),
            volume=float(payload["volume"]),
        ))
    return bars


def recorded_signals(ledger: EventLedger) -> list:
    signals = []
    for record in ledger.records:
        if record.event_type is EventType.ORDER_INTENT:
            signals.append({
                "minute": record.timestamp_utc, "kind": "entry",
                "side": record.payload.get("side"),
                "quantity": record.payload.get("quantity"),
                "stop_price": record.payload.get("stop_price"),
            })
        elif record.event_type is EventType.EXIT:
            signals.append({
                "minute": record.timestamp_utc, "kind": "exit",
                "reason": record.payload.get("reason"),
            })
    return signals


def replay_signals(
    bars: list, strategy_factory: Optional[Callable[[], object]] = None
) -> list:
    factory = strategy_factory or (lambda: AdaptiveTrendStrategy(production_am_config()))
    strategy = factory()
    signals = []
    for bar in bars:
        strategy.on_bar_context(session_pnl=0.0, daily_limit_hit=False)
        result = strategy.on_bar(bar)
        for intent in result.order_intents:
            signals.append({
                "minute": bar.timestamp_utc, "kind": "entry",
                "side": intent.side, "quantity": intent.quantity,
                "stop_price": intent.metadata.get("stop_price"),
            })
        for exit_decision in result.exits:
            signals.append({
                "minute": bar.timestamp_utc, "kind": "exit",
                "reason": exit_decision.reason,
            })
    return signals


def diff_signals(live: list, replay: list) -> list:
    divergences = []
    for index in range(max(len(live), len(replay))):
        lhs = live[index] if index < len(live) else None
        rhs = replay[index] if index < len(replay) else None
        if lhs != rhs:
            minute = (lhs or rhs or {}).get("minute", "?")
            divergences.append(
                f"signal #{index + 1} at {minute}: live={lhs!r} replay={rhs!r}"
            )
    return divergences


def _halts(ledger: EventLedger) -> list:
    return [
        record for record in ledger.records
        if record.event_type is EventType.STATE_TRANSITION
    ]


def _sim_trades(bars: list) -> list:
    from scripts.freeze_baseline_anchor import FROZEN_SIMULATION_OVERRIDES
    from full_python.simulation import SimulationConfig, SimulationEngine

    config = SimulationConfig(**FROZEN_SIMULATION_OVERRIDES)
    result = SimulationEngine(config).run(
        bars, AdaptiveTrendStrategy(production_am_config())
    )
    return list(result.trades)


def _esc(value: Any) -> str:
    return html_module.escape(str(value))


def _write_html(
    path: Path, *, bars, live, replay, divergences, halts, sim_trades, sim_error=None
) -> None:
    verdict = "PARITY" if not divergences else "DIVERGENCE"
    color = "#0a7d33" if not divergences else "#b00020"
    rows = "".join(
        f"<tr><td>{_esc(s['minute'])}</td><td>{_esc(s['kind'])}</td>"
        f"<td>{_esc(s.get('side', s.get('reason', '')))}</td>"
        f"<td>{_esc(s.get('quantity', ''))}</td>"
        f"<td>{_esc(s.get('stop_price', ''))}</td></tr>"
        for s in live
    ) or "<tr><td colspan='5'>no signals recorded</td></tr>"
    diff_rows = "".join(f"<li>{_esc(line)}</li>" for line in divergences)
    halt_rows = "".join(
        f"<li>{_esc(r.timestamp_utc)} — {_esc(r.payload.get('reason'))}: "
        f"{_esc(r.payload.get('error', ''))}</li>"
        for r in halts
    ) or "<li>none</li>"
    if sim_error is not None:
        sim_section = f"<p>sim section unavailable: {_esc(sim_error)}</p>"
    else:
        trade_rows = "".join(
            f"<tr><td>{_esc(t.entry_timestamp_utc)}</td><td>{_esc(t.side)}</td>"
            f"<td>{_esc(t.quantity)}</td><td>{_esc(t.exit_reason)}</td>"
            f"<td>{t.net_pnl:+.2f}</td></tr>"
            for t in sim_trades
        ) or "<tr><td colspan='5'>none</td></tr>"
        net = sum(t.net_pnl for t in sim_trades)
        sim_section = (
            f"<p>Net: {net:+.2f}. Not part of the verdict.</p>"
            f"<table><tr><th>entry (UTC)</th><th>side</th><th>qty</th>"
            f"<th>exit</th><th>net P&amp;L</th></tr>{trade_rows}</table>"
        )
    body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Observe session report</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; }} td, th {{ border: 1px solid #ccc; padding: 4px 10px; }}
.verdict {{ color: white; background: {color}; display: inline-block;
            padding: 6px 16px; font-weight: 700; border-radius: 4px; }}
</style></head><body>
<h1>Observe session shadow report</h1>
<p><span class="verdict">{verdict}</span></p>
<p>{len(bars)} bars, {len(live)} live signals, {len(replay)} replay signals.</p>
<h2>Divergences</h2><ul>{diff_rows or "<li>none</li>"}</ul>
<h2>Halts</h2><ul>{halt_rows}</ul>
<h2>Live signals</h2>
<table><tr><th>minute (UTC)</th><th>kind</th><th>side/reason</th><th>qty</th><th>stop</th></tr>{rows}</table>
<h2>Informational: sim trades on these bars (frozen baseline config)</h2>
{sim_section}
</body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def run_report(events_path, html_path) -> int:
    ledger = EventLedger.read_jsonl(events_path)
    bars = bars_from_ledger(ledger)
    live = recorded_signals(ledger)
    replay = replay_signals(bars)
    divergences = diff_signals(live, replay)
    halts = _halts(ledger)
    try:
        sim_trades = _sim_trades(bars)
        sim_error = None
    except Exception as exc:  # informational section only -- must not sink the verdict
        logger.warning("sim section unavailable: %s", exc)
        sim_trades = []
        sim_error = str(exc)
    _write_html(Path(html_path), bars=bars, live=live, replay=replay,
                divergences=divergences, halts=halts, sim_trades=sim_trades,
                sim_error=sim_error)
    for line in divergences:
        logger.error("DIVERGENCE %s", line)
    for record in halts:
        logger.warning("HALT %s %s", record.timestamp_utc, record.payload)
    logger.info("verdict: %s (%d bars, %d live signals) -> %s",
                "PARITY" if not divergences else "DIVERGENCE",
                len(bars), len(live), html_path)
    return 1 if divergences else 0
