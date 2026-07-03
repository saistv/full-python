"""Databento GLBX ohlcv-1m -> canonical continuous-front-month CSV.

Converts daily ``glbx-mdp3-YYYYMMDD.ohlcv-1m.csv.zst`` files (raw contract
symbols like NQZ5, spreads like NQZ5-NQH6) into the canonical bar CSV the
CLI consumes, keeping only the front-month contract under the validated
TradingView-NQ1! roll rule: quarterly contracts expire the third Friday of
Mar/Jun/Sep/Dec, and the front switches to the next contract when the CME
session date reaches expiry minus 3 calendar days (Tuesday of expiration
week). That rule was fitted for TV NQ1! parity in the legacy research repo
("tv-nq1-v1.2", roll_days_before=2 with the 1.5x calendar conversion) and
is reproduced here exactly.

Requires the optional ``zstandard`` dependency.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import io
from pathlib import Path
from typing import Iterable, Optional

from full_python.data.sessions import EASTERN

try:
    import zstandard
except ImportError:  # pragma: no cover - exercised only without the extra
    zstandard = None

QUARTERLY_MONTH_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}
ROLL_CALENDAR_DAYS_BEFORE_EXPIRY = 3  # legacy fit: int(roll_days_before=2 * 1.5)


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
    return first_friday + timedelta(days=14)


def front_contract_for_session(session_date: date, root: str = "NQ") -> str:
    """Front-month contract code (Databento style, single-digit year)."""
    year = session_date.year
    candidates = []
    for candidate_year in (year, year + 1):
        for month in (3, 6, 9, 12):
            expiry = third_friday(candidate_year, month)
            roll = expiry - timedelta(days=ROLL_CALENDAR_DAYS_BEFORE_EXPIRY)
            if session_date < roll:
                candidates.append((expiry, candidate_year, month))
    expiry, contract_year, contract_month = min(candidates)
    return f"{root}{QUARTERLY_MONTH_CODES[contract_month]}{contract_year % 10}"


class _SessionDateCache:
    """UTC 'YYYY-MM-DDTHH' prefix -> CME session date (18:00 ET boundary).

    Hour-level caching is safe: the session boundary sits on an exact hour
    and the UTC->ET offset cannot change mid-hour.
    """

    def __init__(self) -> None:
        self._cache: dict[str, date] = {}

    def get(self, timestamp: str) -> date:
        prefix = timestamp[:13]
        cached = self._cache.get(prefix)
        if cached is not None:
            return cached
        moment = datetime(
            int(prefix[0:4]), int(prefix[5:7]), int(prefix[8:10]), int(prefix[11:13]),
            tzinfo=timezone.utc,
        ).astimezone(EASTERN)
        session = moment.date()
        if moment.hour >= 18:
            session += timedelta(days=1)
        self._cache[prefix] = session
        return session


def convert_glbx_files(
    input_files: Iterable[Path],
    output_path: Path,
    *,
    root: str = "NQ",
    output_symbol: str = "NQ1!",
    start_utc: Optional[str] = None,
    end_utc: Optional[str] = None,
) -> dict:
    """Stream GLBX daily files into one canonical front-month CSV.

    Returns a summary dict with row counts and the observed roll map.
    """
    if zstandard is None:
        raise RuntimeError(
            "zstandard is required for Databento conversion: pip install zstandard"
        )

    sessions = _SessionDateCache()
    rows: list[tuple[str, float, float, float, float, float]] = []
    contracts_used: dict[str, str] = {}
    skipped_symbols = 0

    for path in sorted(input_files):
        with Path(path).open("rb") as raw:
            reader = io.TextIOWrapper(
                zstandard.ZstdDecompressor().stream_reader(raw), encoding="utf-8"
            )
            csv_reader = csv.DictReader(reader)
            for row in csv_reader:
                symbol = row["symbol"]
                if "-" in symbol or not symbol.startswith(root):
                    continue
                timestamp = row["ts_event"]
                minute_timestamp = timestamp[:16] + ":00Z"  # ns -> minute precision
                if start_utc is not None and minute_timestamp < start_utc:
                    continue
                if end_utc is not None and minute_timestamp >= end_utc:
                    continue
                session = sessions.get(timestamp)
                front = front_contract_for_session(session, root)
                if symbol != front:
                    skipped_symbols += 1
                    continue
                contracts_used.setdefault(session.isoformat(), front)
                rows.append(
                    (
                        minute_timestamp,
                        float(row["open"]),
                        float(row["high"]),
                        float(row["low"]),
                        float(row["close"]),
                        float(row["volume"]),
                    )
                )

    rows.sort(key=lambda item: item[0])
    deduped: list[tuple[str, float, float, float, float, float]] = []
    for row in rows:
        if deduped and deduped[-1][0] == row[0]:
            continue
        deduped.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "symbol", "open", "high", "low", "close", "volume"])
        for timestamp, open_, high, low, close, volume in deduped:
            writer.writerow([timestamp, output_symbol, open_, high, low, close, volume])

    roll_map: dict[str, str] = {}
    previous = None
    for session_iso in sorted(contracts_used):
        contract = contracts_used[session_iso]
        if contract != previous:
            roll_map[session_iso] = contract
            previous = contract

    return {
        "rows_written": len(deduped),
        "duplicates_dropped": len(rows) - len(deduped),
        "other_contract_rows_skipped": skipped_symbols,
        "first_timestamp": deduped[0][0] if deduped else None,
        "last_timestamp": deduped[-1][0] if deduped else None,
        "roll_map": roll_map,
        "output_path": str(output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Databento GLBX ohlcv-1m .csv.zst files to a canonical front-month CSV."
    )
    parser.add_argument("--input-dir", required=True, help="Directory of glbx-mdp3-*.ohlcv-1m.csv.zst files")
    parser.add_argument("--extra-file", action="append", default=[], help="Additional .csv.zst files (e.g. gap-fill batches)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", default="NQ")
    parser.add_argument("--start-utc", help="Inclusive ISO minute, e.g. 2025-10-01T00:00:00Z")
    parser.add_argument("--end-utc", help="Exclusive ISO minute")
    args = parser.parse_args()

    files = sorted(Path(args.input_dir).glob("glbx-mdp3-*.ohlcv-1m.csv.zst"))
    if args.start_utc or args.end_utc:
        start_name = args.start_utc[:10].replace("-", "") if args.start_utc else "00000000"
        end_name = args.end_utc[:10].replace("-", "") if args.end_utc else "99999999"
        files = [
            path
            for path in files
            if start_name <= path.name.split("-")[2].split(".")[0] <= end_name
        ]
    files.extend(Path(item) for item in args.extra_file)

    summary = convert_glbx_files(
        files,
        Path(args.output),
        root=args.root,
        start_utc=args.start_utc,
        end_utc=args.end_utc,
    )
    import json

    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
