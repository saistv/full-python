from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from full_python.data.sessions import classify_timestamp, parse_timestamp_utc
from full_python.models import MarketBar

MAX_RECORDED_ISSUES = 100

STRUCTURAL_ISSUE_KINDS = (
    "unparseable_timestamp",
    "non_monotonic_timestamp",
    "duplicate_timestamp",
    "invalid_ohlc",
    "non_positive_price",
    "negative_volume",
    "symbol_mix",
)


@dataclass(frozen=True)
class DataQualityIssue:
    index: int
    timestamp_utc: str
    kind: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DataQualityReport:
    bar_count: int
    symbols: tuple[str, ...]
    issue_counts: dict[str, int]
    intra_session_gap_count: int
    max_intra_session_gap_minutes: float
    recorded_issues: tuple[DataQualityIssue, ...] = field(default_factory=tuple)

    @property
    def structural_issue_count(self) -> int:
        return sum(
            count
            for kind, count in self.issue_counts.items()
            if kind in STRUCTURAL_ISSUE_KINDS
        )

    @property
    def is_structurally_clean(self) -> bool:
        return self.structural_issue_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "bar_count": self.bar_count,
            "symbols": list(self.symbols),
            "issue_counts": dict(self.issue_counts),
            "structural_issue_count": self.structural_issue_count,
            "is_structurally_clean": self.is_structurally_clean,
            "intra_session_gap_count": self.intra_session_gap_count,
            "max_intra_session_gap_minutes": self.max_intra_session_gap_minutes,
            "recorded_issues": [issue.to_dict() for issue in self.recorded_issues],
        }


def validate_bars(
    bars: list[MarketBar],
    *,
    expected_interval_minutes: float = 1.0,
) -> DataQualityReport:
    """Validate canonical bars before any simulation touches them.

    Structural issues (ordering, duplicates, malformed OHLC, mixed symbols)
    make a dataset unfit for simulation. Intra-session gaps are informational:
    they are counted and surfaced, never silently ignored, but they do not
    block a run because real data has halts and low-volume minutes.
    Session boundaries (overnight, weekend) are not gaps.
    """
    issues: list[DataQualityIssue] = []
    counts: dict[str, int] = {}
    symbols: list[str] = []
    gap_count = 0
    max_gap_minutes = 0.0

    def record(index: int, timestamp: str, kind: str, detail: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1
        if len(issues) < MAX_RECORDED_ISSUES:
            issues.append(
                DataQualityIssue(
                    index=index, timestamp_utc=timestamp, kind=kind, detail=detail
                )
            )

    previous_dt = None
    previous_session = None
    for index, bar in enumerate(bars):
        if bar.symbol not in symbols:
            symbols.append(bar.symbol)

        try:
            current_dt = parse_timestamp_utc(bar.timestamp_utc)
            current_session = classify_timestamp(bar.timestamp_utc).session_date
        except ValueError as error:
            record(index, bar.timestamp_utc, "unparseable_timestamp", str(error))
            previous_dt = None
            previous_session = None
            continue

        if previous_dt is not None:
            if current_dt < previous_dt:
                record(
                    index,
                    bar.timestamp_utc,
                    "non_monotonic_timestamp",
                    f"goes backward from {previous_dt.isoformat()}",
                )
            elif current_dt == previous_dt:
                record(index, bar.timestamp_utc, "duplicate_timestamp", "same timestamp as prior bar")
            elif previous_session == current_session:
                delta_minutes = (current_dt - previous_dt).total_seconds() / 60.0
                if delta_minutes > expected_interval_minutes:
                    gap_count += 1
                    max_gap_minutes = max(max_gap_minutes, delta_minutes)

        prices = (bar.open, bar.high, bar.low, bar.close)
        if any(price <= 0 for price in prices):
            record(index, bar.timestamp_utc, "non_positive_price", f"ohlc={prices}")
        elif not (
            bar.high >= bar.low
            and bar.high >= bar.open
            and bar.high >= bar.close
            and bar.low <= bar.open
            and bar.low <= bar.close
        ):
            record(index, bar.timestamp_utc, "invalid_ohlc", f"ohlc={prices}")
        if bar.volume < 0:
            record(index, bar.timestamp_utc, "negative_volume", f"volume={bar.volume}")

        previous_dt = current_dt
        previous_session = current_session

    if len(symbols) > 1:
        record(0, bars[0].timestamp_utc if bars else "", "symbol_mix", f"symbols={symbols}")

    return DataQualityReport(
        bar_count=len(bars),
        symbols=tuple(symbols),
        issue_counts=counts,
        intra_session_gap_count=gap_count,
        max_intra_session_gap_minutes=max_gap_minutes,
        recorded_issues=tuple(issues),
    )
