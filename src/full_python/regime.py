"""Session regime classifier — MEASUREMENT ONLY.

Tags each CME session with regime features that are fully decidable at
that session's 9:30 ET open (prior-session statistics + overnight data),
then attributes strategy trades to those tags: n, total/mean/median P&L,
win rate, and a Welch t-statistic of the bucket against all other trades.

Two hard rules, both learned the expensive way:

1. This module NEVER gates entries. Every regime filter tested inside the
   9:30-10:00 window degraded Adaptive Trend net P&L (the window IS the
   filter). Regime tags exist to (a) describe where AT's P&L comes from
   and (b) later feed the mean-reversion sleeve's permission layer, which
   is a different strategy.
2. Subset statistics are correlation, not live-filter validation. Buckets
   under 50 trades are labeled unproven in the output.

Feature vocabulary follows the MR research contract: ADX(14) < 20 as the
non-trending gate and a variance ratio on prior-session RTH returns for
true mean-reversion regime detection.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any, Iterable, Optional

from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.sessions import classify_timestamp
from full_python.models import MarketBar

ADX_NON_TRENDING_MAX = 20.0  # MR contract design principle #5
VR_MEAN_REVERTING_MAX = 0.9
VR_TRENDING_MIN = 1.1
GAP_FLAT_ATR = 0.10
MIN_PROVEN_TRADES = 50


# ----------------------------------------------------------------------
# Daily ADX(14), Wilder-smoothed, fed one completed session at a time
# ----------------------------------------------------------------------


class DailyAdx:
    def __init__(self, length: int = 14) -> None:
        self._length = length
        self._prev: Optional[tuple[float, float, float]] = None  # high, low, close
        self._tr_sum = self._plus_sum = self._minus_sum = 0.0
        self._seed_count = 0
        self._smoothed: Optional[tuple[float, float, float]] = None
        self._dx_values: list[float] = []
        self._adx: Optional[float] = None

    def update(self, high: float, low: float, close: float) -> Optional[float]:
        if self._prev is None:
            self._prev = (high, low, close)
            return None
        prev_high, prev_low, prev_close = self._prev
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0
        self._prev = (high, low, close)

        if self._smoothed is None:
            self._tr_sum += tr
            self._plus_sum += plus_dm
            self._minus_sum += minus_dm
            self._seed_count += 1
            if self._seed_count < self._length:
                return None
            self._smoothed = (self._tr_sum, self._plus_sum, self._minus_sum)
        else:
            tr_s, plus_s, minus_s = self._smoothed
            self._smoothed = (
                tr_s - tr_s / self._length + tr,
                plus_s - plus_s / self._length + plus_dm,
                minus_s - minus_s / self._length + minus_dm,
            )

        tr_s, plus_s, minus_s = self._smoothed
        if tr_s == 0:
            return self._adx
        plus_di = 100.0 * plus_s / tr_s
        minus_di = 100.0 * minus_s / tr_s
        di_sum = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0

        if self._adx is None:
            self._dx_values.append(dx)
            if len(self._dx_values) < self._length:
                return None
            self._adx = sum(self._dx_values) / self._length
        else:
            self._adx = (self._adx * (self._length - 1) + dx) / self._length
        return self._adx


def variance_ratio(closes: list[float], q: int = 10) -> Optional[float]:
    """VR(q) on log returns: < 1 mean-reverting, > 1 trending."""
    if len(closes) < q * 4:
        return None
    returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    n = len(returns)
    if n < q * 3:
        return None
    mean = sum(returns) / n
    var_1 = sum((r - mean) ** 2 for r in returns) / (n - 1)
    if var_1 == 0:
        return None
    q_sums = [sum(returns[i : i + q]) for i in range(0, n - q + 1)]
    var_q = sum((s - q * mean) ** 2 for s in q_sums) / max(len(q_sums) - 1, 1)
    return var_q / (q * var_1)


# ----------------------------------------------------------------------
# Session feature extraction (no lookahead past the 9:30 open)
# ----------------------------------------------------------------------


@dataclass
class SessionFeatures:
    session_date: str
    rth_open: Optional[float] = None
    prior_rth_close: Optional[float] = None
    gap_atr: Optional[float] = None
    overnight_range_atr: Optional[float] = None
    prior_realized_vol: Optional[float] = None  # stdev of prior RTH 1m log returns
    adx_14: Optional[float] = None  # daily, through the PRIOR session
    variance_ratio_q10: Optional[float] = None  # prior RTH session
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class _SessionAccumulator:
    session_date: str
    high: float = float("-inf")
    low: float = float("inf")
    close: float = 0.0
    overnight_high: float = float("-inf")
    overnight_low: float = float("inf")
    rth_open: Optional[float] = None
    rth_closes: list[float] = field(default_factory=list)
    rth_close: Optional[float] = None


def compute_session_features(bars: Iterable[MarketBar]) -> list[SessionFeatures]:
    adx = DailyAdx(14)
    atr_window: list[float] = []  # daily true ranges, simple 14 SMA
    prev_session_close: Optional[float] = None

    accumulators: list[_SessionAccumulator] = []
    current: Optional[_SessionAccumulator] = None
    for bar in bars:
        session = classify_timestamp(bar.timestamp_utc)
        session_iso = session.session_date.isoformat()
        if current is None or current.session_date != session_iso:
            current = _SessionAccumulator(session_date=session_iso)
            accumulators.append(current)
        current.high = max(current.high, bar.high)
        current.low = min(current.low, bar.low)
        current.close = bar.close
        if session.is_rth:
            if current.rth_open is None:
                current.rth_open = bar.open
            current.rth_closes.append(bar.close)
            current.rth_close = bar.close
        elif current.rth_open is None:
            current.overnight_high = max(current.overnight_high, bar.high)
            current.overnight_low = min(current.overnight_low, bar.low)

    features: list[SessionFeatures] = []
    prior_rth_closes: list[float] = []
    prior_rth_close: Optional[float] = None
    daily_atr: Optional[float] = None
    adx_value: Optional[float] = None

    for acc in accumulators:
        row = SessionFeatures(session_date=acc.session_date)
        row.rth_open = acc.rth_open
        row.prior_rth_close = prior_rth_close
        if daily_atr and acc.rth_open is not None and prior_rth_close is not None:
            row.gap_atr = (acc.rth_open - prior_rth_close) / daily_atr
        if daily_atr and acc.overnight_high > acc.overnight_low:
            row.overnight_range_atr = (acc.overnight_high - acc.overnight_low) / daily_atr
        if len(prior_rth_closes) >= 30:
            returns = [
                math.log(prior_rth_closes[i] / prior_rth_closes[i - 1])
                for i in range(1, len(prior_rth_closes))
            ]
            mean = sum(returns) / len(returns)
            row.prior_realized_vol = math.sqrt(
                sum((r - mean) ** 2 for r in returns) / len(returns)
            )
            row.variance_ratio_q10 = variance_ratio(prior_rth_closes)
        row.adx_14 = adx_value
        features.append(row)

        # Roll the session that just completed into the "prior" state.
        if acc.high > acc.low:
            true_range = (
                max(
                    acc.high - acc.low,
                    abs(acc.high - (prev_session_close or acc.low)),
                    abs(acc.low - (prev_session_close or acc.high)),
                )
                if prev_session_close is not None
                else acc.high - acc.low
            )
            atr_window.append(true_range)
            if len(atr_window) > 14:
                atr_window.pop(0)
            if len(atr_window) == 14:
                daily_atr = sum(atr_window) / 14.0
            adx_value = adx.update(acc.high, acc.low, acc.close)
            prev_session_close = acc.close
        prior_rth_closes = acc.rth_closes
        if acc.rth_close is not None:
            prior_rth_close = acc.rth_close

    _assign_tags(features)
    return features


def _tercile_bounds(values: list[float]) -> tuple[float, float]:
    ordered = sorted(values)
    return ordered[len(ordered) // 3], ordered[(2 * len(ordered)) // 3]


def _assign_tags(features: list[SessionFeatures]) -> None:
    vols = [f.prior_realized_vol for f in features if f.prior_realized_vol is not None]
    ranges = [f.overnight_range_atr for f in features if f.overnight_range_atr is not None]
    vol_bounds = _tercile_bounds(vols) if len(vols) >= 9 else None
    range_bounds = _tercile_bounds(ranges) if len(ranges) >= 9 else None

    for row in features:
        tags = row.tags
        if row.adx_14 is not None:
            tags["adx"] = (
                "non_trending" if row.adx_14 < ADX_NON_TRENDING_MAX else "trending"
            )
        if row.variance_ratio_q10 is not None:
            if row.variance_ratio_q10 < VR_MEAN_REVERTING_MAX:
                tags["variance_ratio"] = "mean_reverting"
            elif row.variance_ratio_q10 > VR_TRENDING_MIN:
                tags["variance_ratio"] = "trending"
            else:
                tags["variance_ratio"] = "neutral"
        if row.gap_atr is not None:
            if abs(row.gap_atr) < GAP_FLAT_ATR:
                tags["gap"] = "flat"
            else:
                tags["gap"] = "gap_up" if row.gap_atr > 0 else "gap_down"
        if vol_bounds and row.prior_realized_vol is not None:
            lo, hi = vol_bounds
            tags["prior_vol"] = (
                "low" if row.prior_realized_vol <= lo
                else "high" if row.prior_realized_vol > hi
                else "mid"
            )
        if range_bounds and row.overnight_range_atr is not None:
            lo, hi = range_bounds
            tags["overnight_range"] = (
                "low" if row.overnight_range_atr <= lo
                else "high" if row.overnight_range_atr > hi
                else "mid"
            )


# ----------------------------------------------------------------------
# Attribution: strategy trades joined to session tags
# ----------------------------------------------------------------------


def welch_t(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    var_a = sum((x - mean_a) ** 2 for x in a) / (len(a) - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (len(b) - 1)
    denom = math.sqrt(var_a / len(a) + var_b / len(b))
    if denom == 0:
        return None
    return (mean_a - mean_b) / denom


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def attribute_trades(
    features: list[SessionFeatures],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    by_session = {f.session_date: f for f in features}
    tagged_days: dict[str, dict[str, list[str]]] = {}
    for row in features:
        for key, value in row.tags.items():
            tagged_days.setdefault(key, {}).setdefault(value, []).append(row.session_date)

    all_pnls = [float(t["net_pnl"]) for t in trades]
    report: dict[str, Any] = {
        "total_trades": len(trades),
        "total_sessions": len(features),
        "overall_mean_pnl": round(sum(all_pnls) / len(all_pnls), 2) if all_pnls else 0.0,
        "axes": {},
    }
    for axis, values in tagged_days.items():
        buckets = {}
        for value, days in values.items():
            day_set = set(days)
            bucket = [float(t["net_pnl"]) for t in trades if t["session_date"] in day_set]
            rest = [float(t["net_pnl"]) for t in trades if t["session_date"] not in day_set]
            wins = sum(1 for p in bucket if p > 0)
            t_stat = welch_t(bucket, rest)
            buckets[value] = {
                "sessions": len(days),
                "trades": len(bucket),
                "net_pnl": round(sum(bucket), 2),
                "mean_pnl": round(sum(bucket) / len(bucket), 2) if bucket else None,
                "median_pnl": round(_median(bucket), 2) if bucket else None,
                "win_rate": round(wins / len(bucket), 4) if bucket else None,
                "t_stat_vs_rest": round(t_stat, 3) if t_stat is not None else None,
                "proven_sample": len(bucket) >= MIN_PROVEN_TRADES,
            }
        report["axes"][axis] = buckets
    return report


COLUMN_MAP = CsvBarColumnMap(
    timestamp="timestamp", symbol="symbol", open="open",
    high="high", low="low", close="close", volume="volume",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Tag sessions with open-time regime features and attribute trades. Measurement only."
    )
    parser.add_argument("--data", required=True, help="Canonical bar CSV")
    parser.add_argument("--trades", required=True, help="trades.csv from a run")
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()

    bars = load_csv_bars(Path(args.data), COLUMN_MAP)
    features = compute_session_features(bars)
    with Path(args.trades).open("r", encoding="utf-8", newline="") as handle:
        trades = list(csv.DictReader(handle))
    report = attribute_trades(features, trades)
    report["sessions"] = [
        {"session_date": f.session_date, "tags": f.tags,
         "adx_14": None if f.adx_14 is None else round(f.adx_14, 2),
         "variance_ratio_q10": None if f.variance_ratio_q10 is None else round(f.variance_ratio_q10, 3),
         "gap_atr": None if f.gap_atr is None else round(f.gap_atr, 3)}
        for f in features
    ]
    if args.output:
        Path(args.output).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    print(f"sessions {report['total_sessions']}  trades {report['total_trades']}  "
          f"mean/trade {report['overall_mean_pnl']:+,.0f}")
    for axis, buckets in report["axes"].items():
        print(f"\n{axis}:")
        for value, stats in sorted(buckets.items()):
            proven = "" if stats["proven_sample"] else "  [n<50 unproven]"
            print(
                f"  {value:>14}  days {stats['sessions']:>4}  trades {stats['trades']:>4}  "
                f"net {stats['net_pnl']:>+12,.0f}  mean {stats['mean_pnl'] if stats['mean_pnl'] is not None else '-':>8}  "
                f"median {stats['median_pnl'] if stats['median_pnl'] is not None else '-':>8}  "
                f"t {stats['t_stat_vs_rest'] if stats['t_stat_vs_rest'] is not None else '-':>6}{proven}"
            )


if __name__ == "__main__":
    main()
