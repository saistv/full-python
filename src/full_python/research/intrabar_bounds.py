"""Conservative MFE bounds for path-ambiguous one-minute stop bars."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import statistics
from typing import Iterable, Sequence

from full_python.models import MarketBar, Trade


DEFAULT_MFE_THRESHOLDS = (5.0, 10.0, 15.0, 20.0, 30.0, 40.0)


@dataclass(frozen=True)
class IntrabarBoundsReport:
    trade_count: int
    stop_trade_count: int
    stop_trade_net_pnl: float
    entry_minute_stop_count: int
    entry_minute_stop_net_pnl: float
    ambiguous_exit_count: int
    ambiguous_exit_net_pnl: float
    confirmed_mfe_total: float
    mfe_upper_bound_total: float
    mfe_uncertainty_total: float
    mfe_uncertainty_median: float
    mfe_uncertainty_max: float
    unresolved_threshold_counts: dict[str, int]
    pnl_path_uncertain_trade_count: int
    ambiguous_intervals: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _threshold_key(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def build_intrabar_bounds_report(
    trades: Sequence[Trade],
    bars: Iterable[MarketBar],
    *,
    thresholds: Sequence[float] = DEFAULT_MFE_THRESHOLDS,
) -> IntrabarBoundsReport:
    """Bound true pre-stop MFE without assuming OHLC traversal order.

    Adaptive Trend has no profit target, so bar-path uncertainty does not alter
    the stop-first P&L replay. It only prevents exact MFE claims.
    """
    stops = [trade for trade in trades if trade.exit_reason == "stop"]
    entry_minute_stops = [
        trade for trade in stops
        if trade.entry_timestamp_utc == trade.exit_timestamp_utc
    ]
    ambiguous = [trade for trade in stops if trade.ambiguous_exit]
    needed_keys = {(trade.symbol, trade.exit_timestamp_utc) for trade in ambiguous}
    bar_by_key: dict[tuple[str, str], MarketBar] = {}
    for bar in bars:
        key = (bar.symbol, bar.timestamp_utc)
        if key not in needed_keys:
            continue
        if key in bar_by_key:
            raise ValueError(f"duplicate market bar: {bar.symbol} {bar.timestamp_utc}")
        bar_by_key[key] = bar

    threshold_counts = {_threshold_key(float(value)): 0 for value in thresholds}
    intervals: list[dict[str, object]] = []
    widths: list[float] = []
    confirmed_total = 0.0
    upper_total = 0.0

    for trade in ambiguous:
        key = (trade.symbol, trade.exit_timestamp_utc)
        bar = bar_by_key.get(key)
        if bar is None:
            raise ValueError(
                "missing exit bar for ambiguous trade: "
                f"{trade.symbol} {trade.exit_timestamp_utc}"
            )
        favorable_upper = (
            bar.high - trade.entry_price
            if trade.side == "long"
            else trade.entry_price - bar.low
        )
        lower = max(0.0, trade.mfe_points)
        upper = max(lower, favorable_upper)
        width = upper - lower
        confirmed_total += lower
        upper_total += upper
        widths.append(width)
        for value in thresholds:
            threshold = float(value)
            if lower < threshold <= upper:
                threshold_counts[_threshold_key(threshold)] += 1
        intervals.append({
            "symbol": trade.symbol,
            "side": trade.side,
            "entry_timestamp_utc": trade.entry_timestamp_utc,
            "exit_timestamp_utc": trade.exit_timestamp_utc,
            "entry_minute_stop": trade.entry_timestamp_utc == trade.exit_timestamp_utc,
            "confirmed_mfe_lower": lower,
            "ohlc_mfe_upper": upper,
            "uncertainty_width": width,
            "net_pnl": trade.net_pnl,
        })

    return IntrabarBoundsReport(
        trade_count=len(trades),
        stop_trade_count=len(stops),
        stop_trade_net_pnl=sum(trade.net_pnl for trade in stops),
        entry_minute_stop_count=len(entry_minute_stops),
        entry_minute_stop_net_pnl=sum(trade.net_pnl for trade in entry_minute_stops),
        ambiguous_exit_count=len(ambiguous),
        ambiguous_exit_net_pnl=sum(trade.net_pnl for trade in ambiguous),
        confirmed_mfe_total=confirmed_total,
        mfe_upper_bound_total=upper_total,
        mfe_uncertainty_total=sum(widths),
        mfe_uncertainty_median=statistics.median(widths) if widths else 0.0,
        mfe_uncertainty_max=max(widths, default=0.0),
        unresolved_threshold_counts=threshold_counts,
        pnl_path_uncertain_trade_count=0,
        ambiguous_intervals=tuple(intervals),
    )
