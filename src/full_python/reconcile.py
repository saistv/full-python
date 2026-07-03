"""Trade-by-trade reconciliation against TradingView trade-list exports.

This is how the Python engine earns backtest authority: every simulated
trade must match the TradingView export within a stated tolerance, and
every mismatch must be explained (fill timing, intrabar ambiguity, roll
boundary) or fixed. Aggregate agreement is not accepted as evidence —
the legacy Python backtester agreed in aggregate while being +23% wrong.

TV export format (List of trades): one CSV row per entry/exit leg with
columns like ``Trade #, Type, Date and time, Signal, Price USD, ...``.
Timestamps are naive strings in the chart's timezone (ET by default).
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from full_python.data.sessions import parse_timestamp_utc


@dataclass(frozen=True)
class TvTrade:
    trade_number: str
    side: str
    entry_time: datetime
    entry_price: float
    entry_signal: str
    exit_time: Optional[datetime]
    exit_price: Optional[float]
    exit_signal: str
    quantity: float


@dataclass(frozen=True)
class SimTrade:
    side: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: str
    quantity: float


@dataclass(frozen=True)
class MatchedPair:
    tv_trade_number: str
    side: str
    entry_time_delta_minutes: float
    entry_price_delta: float
    exit_price_delta: Optional[float]
    tv_entry_signal: str
    sim_exit_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconciliationReport:
    tv_trade_count: int
    sim_trade_count: int
    matched_count: int
    missing_in_sim: list[dict[str, Any]] = field(default_factory=list)
    extra_in_sim: list[dict[str, Any]] = field(default_factory=list)
    matches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def match_rate(self) -> float:
        if self.tv_trade_count == 0:
            return 0.0
        return self.matched_count / self.tv_trade_count

    def summary(self) -> dict[str, Any]:
        entry_deltas = [abs(m["entry_price_delta"]) for m in self.matches]
        return {
            "tv_trade_count": self.tv_trade_count,
            "sim_trade_count": self.sim_trade_count,
            "matched_count": self.matched_count,
            "match_rate": self.match_rate,
            "missing_in_sim": len(self.missing_in_sim),
            "extra_in_sim": len(self.extra_in_sim),
            "mean_abs_entry_price_delta": (
                sum(entry_deltas) / len(entry_deltas) if entry_deltas else None
            ),
            "max_abs_entry_price_delta": max(entry_deltas) if entry_deltas else None,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "matches": self.matches,
            "missing_in_sim": self.missing_in_sim,
            "extra_in_sim": self.extra_in_sim,
        }


def _find_column(fieldnames: list[str], *candidates: str) -> str:
    lowered = {name.lower().strip("﻿ "): name for name in fieldnames}
    for candidate in candidates:
        for key, original in lowered.items():
            if candidate in key:
                return original
    raise ValueError(f"No column matching {candidates} in {fieldnames}")


def load_tv_trades(path: str | Path, timezone_name: str = "America/New_York") -> list[TvTrade]:
    tz = ZoneInfo(timezone_name)
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        col_number = _find_column(fieldnames, "trade #", "trade")
        col_type = _find_column(fieldnames, "type")
        col_time = _find_column(fieldnames, "date")
        col_signal = _find_column(fieldnames, "signal")
        col_price = _find_column(fieldnames, "price")
        col_qty = _find_column(fieldnames, "qty", "size")

        legs: dict[str, dict[str, Any]] = {}
        for row in reader:
            number = str(row[col_number]).strip()
            if not number:
                continue
            leg_type = str(row[col_type]).strip().lower()
            record = legs.setdefault(number, {})
            side = "long" if "long" in leg_type else "short" if "short" in leg_type else ""
            parsed_time = datetime.strptime(str(row[col_time]).strip(), "%Y-%m-%d %H:%M").replace(
                tzinfo=tz
            )
            price = float(str(row[col_price]).replace(",", ""))
            if "entry" in leg_type:
                record["side"] = side
                record["entry_time"] = parsed_time
                record["entry_price"] = price
                record["entry_signal"] = str(row[col_signal]).strip()
                record["quantity"] = float(str(row[col_qty]).replace(",", "") or 1)
            elif "exit" in leg_type:
                record.setdefault("side", side)
                record["exit_time"] = parsed_time
                record["exit_price"] = price
                record["exit_signal"] = str(row[col_signal]).strip()

    trades = []
    for number, record in legs.items():
        if "entry_time" not in record:
            continue
        trades.append(
            TvTrade(
                trade_number=number,
                side=record.get("side", ""),
                entry_time=record["entry_time"],
                entry_price=record["entry_price"],
                entry_signal=record.get("entry_signal", ""),
                exit_time=record.get("exit_time"),
                exit_price=record.get("exit_price"),
                exit_signal=record.get("exit_signal", ""),
                quantity=record.get("quantity", 1.0),
            )
        )
    trades.sort(key=lambda trade: trade.entry_time)
    return trades


def load_sim_trades(path: str | Path) -> list[SimTrade]:
    trades = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            trades.append(
                SimTrade(
                    side=row["side"],
                    entry_time=parse_timestamp_utc(row["entry_timestamp_utc"]),
                    entry_price=float(row["entry_price"]),
                    exit_time=parse_timestamp_utc(row["exit_timestamp_utc"]),
                    exit_price=float(row["exit_price"]),
                    exit_reason=row["exit_reason"],
                    quantity=float(row["quantity"]),
                )
            )
    trades.sort(key=lambda trade: trade.entry_time)
    return trades


def reconcile(
    tv_trades: list[TvTrade],
    sim_trades: list[SimTrade],
    *,
    tolerance_minutes: float = 3.0,
) -> ReconciliationReport:
    unmatched_sim = list(sim_trades)
    matches: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    for tv_trade in tv_trades:
        best: Optional[SimTrade] = None
        best_delta: Optional[float] = None
        for sim_trade in unmatched_sim:
            if sim_trade.side != tv_trade.side:
                continue
            delta_minutes = abs(
                (sim_trade.entry_time - tv_trade.entry_time).total_seconds() / 60.0
            )
            if delta_minutes > tolerance_minutes:
                continue
            if best_delta is None or delta_minutes < best_delta:
                best = sim_trade
                best_delta = delta_minutes
        if best is None:
            missing.append(
                {
                    "tv_trade_number": tv_trade.trade_number,
                    "side": tv_trade.side,
                    "entry_time": tv_trade.entry_time.isoformat(),
                    "entry_price": tv_trade.entry_price,
                    "entry_signal": tv_trade.entry_signal,
                }
            )
            continue
        unmatched_sim.remove(best)
        exit_delta = (
            best.exit_price - tv_trade.exit_price if tv_trade.exit_price is not None else None
        )
        matches.append(
            MatchedPair(
                tv_trade_number=tv_trade.trade_number,
                side=tv_trade.side,
                entry_time_delta_minutes=best_delta if best_delta is not None else 0.0,
                entry_price_delta=best.entry_price - tv_trade.entry_price,
                exit_price_delta=exit_delta,
                tv_entry_signal=tv_trade.entry_signal,
                sim_exit_reason=best.exit_reason,
            ).to_dict()
        )

    extra = [
        {
            "side": sim_trade.side,
            "entry_time": sim_trade.entry_time.isoformat(),
            "entry_price": sim_trade.entry_price,
            "exit_reason": sim_trade.exit_reason,
        }
        for sim_trade in unmatched_sim
    ]

    return ReconciliationReport(
        tv_trade_count=len(tv_trades),
        sim_trade_count=len(sim_trades),
        matched_count=len(matches),
        missing_in_sim=missing,
        extra_in_sim=extra,
        matches=matches,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile simulated trades against a TradingView trade-list export."
    )
    parser.add_argument("--tv", required=True, help="TradingView 'List of trades' CSV export")
    parser.add_argument("--trades", required=True, help="trades.csv from a simulation run")
    parser.add_argument("--tolerance-minutes", type=float, default=3.0)
    parser.add_argument("--tv-timezone", default="America/New_York")
    parser.add_argument(
        "--entries-before",
        help="Ignore TV trades entered on/after this date (YYYY-MM-DD, TV timezone) — for trimming to data coverage",
    )
    parser.add_argument("--output", help="Optional path for the full JSON report")
    args = parser.parse_args()

    tv_trades = load_tv_trades(args.tv, args.tv_timezone)
    if args.entries_before:
        cutoff = datetime.strptime(args.entries_before, "%Y-%m-%d").replace(
            tzinfo=ZoneInfo(args.tv_timezone)
        )
        tv_trades = [trade for trade in tv_trades if trade.entry_time < cutoff]

    report = reconcile(
        tv_trades,
        load_sim_trades(args.trades),
        tolerance_minutes=args.tolerance_minutes,
    )
    if args.output:
        Path(args.output).write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report.summary(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
