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

from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.sessions import classify_timestamp
from full_python.events import EventLedger, EventType
from full_python.models import MarketBar
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import production_am_config

logger = logging.getLogger("full_python.live")

REFERENCE_COLUMN_MAP = CsvBarColumnMap(
    timestamp="timestamp", symbol="symbol", open="open", high="high",
    low="low", close="close", volume="volume",
)


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


def diff_bars(recorded: list[MarketBar], reference: list[MarketBar]) -> list[str]:
    """Compare captured bars with an independent source over captured coverage."""
    if not recorded:
        return ["no recorded bars"]
    start = recorded[0].timestamp_utc
    end = recorded[-1].timestamp_utc
    recorded_by = {bar.timestamp_utc: bar for bar in recorded}
    reference_by = {
        bar.timestamp_utc: bar
        for bar in reference
        if start <= bar.timestamp_utc <= end
    }
    differences: list[str] = []
    for timestamp in sorted(set(recorded_by) | set(reference_by)):
        lhs = recorded_by.get(timestamp)
        rhs = reference_by.get(timestamp)
        if lhs is None:
            differences.append(f"bar {timestamp}: missing from capture")
            continue
        if rhs is None:
            differences.append(f"bar {timestamp}: missing from independent reference")
            continue
        lhs_values = (lhs.open, lhs.high, lhs.low, lhs.close, lhs.volume)
        rhs_values = (rhs.open, rhs.high, rhs.low, rhs.close, rhs.volume)
        if lhs.symbol != rhs.symbol or lhs_values != rhs_values:
            differences.append(
                f"bar {timestamp}: captured={(lhs.symbol, *lhs_values)!r} "
                f"reference={(rhs.symbol, *rhs_values)!r}"
            )
    return differences


def check_bar_coverage(
    recorded: list,
    reference: list,
    *,
    entry_start_minutes_et: int,
    entry_end_minutes_et: int,
    warmup_bars: int,
) -> list:
    """Assert the capture holds ENOUGH bars, not merely correct ones.

    ``diff_bars`` compares only inside ``[captured[0], captured[-1]]``, so a
    capture that lost its leading warmup history -- or that simply stopped
    mid-window -- looks clean to it. That is exactly what the feed's history-drop
    bug produced: no warmup, no signals, and a replay of the same truncated
    capture that also produced no signals, so the session still read PARITY.

    A session is only meaningful if the strategy could actually warm up and could
    see the whole entry window. Both are checked here, per captured session.
    """
    if not recorded:
        return ["no recorded bars"]

    problems: list = []
    captured_by_session: dict = {}
    for bar in recorded:
        session = classify_timestamp(bar.timestamp_utc)
        captured_by_session.setdefault(session.session_date, set()).add(bar.timestamp_utc)

    for session_date, captured in sorted(captured_by_session.items()):
        expected_window = sorted(
            bar.timestamp_utc
            for bar in reference
            if classify_timestamp(bar.timestamp_utc).session_date == session_date
            and entry_start_minutes_et
            <= classify_timestamp(bar.timestamp_utc).minutes_from_midnight_et
            < entry_end_minutes_et
        )
        if not expected_window:
            continue  # the reference has no entry window here (e.g. a closure)

        missing = [ts for ts in expected_window if ts not in captured]
        if missing:
            problems.append(
                f"{session_date}: {len(missing)} of {len(expected_window)} entry window "
                f"minutes missing from the capture (first: {missing[0]})"
            )

        window_open = expected_window[0]
        warmup_available = sum(1 for ts in captured if ts < window_open)
        if warmup_available < warmup_bars:
            problems.append(
                f"{session_date}: only {warmup_available} bars captured before the entry "
                f"window opened; the strategy needs {warmup_bars} warmup bars, so it could "
                "not have traded this session"
            )
    return problems


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
    path: Path, *, bars, live, replay, divergences, bar_differences,
    bar_check_status, halts, sim_trades, sim_error=None
) -> None:
    if not bars:
        # Zero recorded BAR events (e.g. a startup crash) is not evidence of
        # parity -- it's an empty session. Render a distinct amber/gray
        # banner so this can never be mistaken for the green PARITY verdict.
        verdict = "NO-DATA"
        color = "#8a6d00"
    elif divergences or bar_differences:
        verdict = "DIVERGENCE"
        color = "#b00020"
    elif bar_check_status != "VERIFIED":
        verdict = "BAR-UNVERIFIED"
        color = "#8a6d00"
    else:
        verdict = "PARITY"
        color = "#0a7d33"
    rows = "".join(
        f"<tr><td>{_esc(s['minute'])}</td><td>{_esc(s['kind'])}</td>"
        f"<td>{_esc(s.get('side', s.get('reason', '')))}</td>"
        f"<td>{_esc(s.get('quantity', ''))}</td>"
        f"<td>{_esc(s.get('stop_price', ''))}</td></tr>"
        for s in live
    ) or "<tr><td colspan='5'>no signals recorded</td></tr>"
    diff_rows = "".join(f"<li>{_esc(line)}</li>" for line in divergences)
    bar_diff_rows = "".join(f"<li>{_esc(line)}</li>" for line in bar_differences)
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
<h2>Independent bar check: {_esc(bar_check_status)}</h2>
<ul>{bar_diff_rows or "<li>none</li>"}</ul>
<h2>Divergences</h2><ul>{diff_rows or "<li>none</li>"}</ul>
<h2>Halts</h2><ul>{halt_rows}</ul>
<h2>Live signals</h2>
<table><tr><th>minute (UTC)</th><th>kind</th><th>side/reason</th><th>qty</th><th>stop</th></tr>{rows}</table>
<h2>Informational: sim trades on these bars (frozen baseline config)</h2>
{sim_section}
</body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def run_report(events_path, html_path, *, reference_bars_path=None) -> int:
    ledger = EventLedger.read_jsonl(events_path)
    bars = bars_from_ledger(ledger)
    live = recorded_signals(ledger)
    replay = replay_signals(bars)
    divergences = diff_signals(live, replay)
    if reference_bars_path is None:
        bar_check_status = "NOT RUN"
        bar_differences: list[str] = []
    else:
        config = production_am_config()
        reference = load_csv_bars(Path(reference_bars_path), REFERENCE_COLUMN_MAP)
        bar_differences = diff_bars(bars, reference) + check_bar_coverage(
            bars,
            reference,
            entry_start_minutes_et=config.entry_start_minutes_et,
            entry_end_minutes_et=config.entry_end_minutes_et,
            warmup_bars=config.warmup_bars,
        )
        bar_check_status = "VERIFIED" if not bar_differences else "FAILED"
    halts = _halts(ledger)
    try:
        sim_trades = _sim_trades(bars)
        sim_error = None
    except Exception as exc:  # informational section only -- must not sink the verdict
        logger.warning("sim section unavailable: %s", exc)
        sim_trades = []
        sim_error = str(exc)
    _write_html(Path(html_path), bars=bars, live=live, replay=replay,
                divergences=divergences, bar_differences=bar_differences,
                bar_check_status=bar_check_status, halts=halts,
                sim_trades=sim_trades, sim_error=sim_error)
    for line in divergences:
        logger.error("DIVERGENCE %s", line)
    for record in halts:
        logger.warning("HALT %s %s", record.timestamp_utc, record.payload)
    for line in bar_differences:
        logger.error("BAR DIVERGENCE %s", line)
    if not bars:
        verdict = "NO-DATA"
    elif divergences or bar_differences:
        verdict = "DIVERGENCE"
    elif bar_check_status != "VERIFIED":
        verdict = "BAR-UNVERIFIED"
    else:
        verdict = "PARITY"
    logger.info("verdict: %s (%d bars, %d live signals) -> %s",
                verdict, len(bars), len(live), html_path)
    return 0 if verdict == "PARITY" else 1
