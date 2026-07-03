"""Verify the 3-year continuous against a TV NQ1! trade export.

For every TV entry leg: fill price should equal our bar's OPEN at the fill
minute, offset by a constant slippage (sign by side). Any roll-basis
mismatch shows up as a several-hundred-point outlier clustered in an
expiration week.
"""
import csv
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

bars_path = sys.argv[1]
tv_path = sys.argv[2]

opens = {}
with open(bars_path) as handle:
    for row in csv.DictReader(handle):
        opens[row["timestamp"]] = float(row["open"])

deltas = []
missing = 0
with open(tv_path, encoding="utf-8-sig") as handle:
    for row in csv.DictReader(handle):
        if "Entry" not in row["Type"]:
            continue
        side = 1 if "long" in row["Type"].lower() else -1
        et_naive = datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
        utc = et_naive.replace(tzinfo=ET).astimezone(UTC)
        key = utc.strftime("%Y-%m-%dT%H:%M:00Z")
        if key not in opens:
            missing += 1
            continue
        fill = float(row["Price USD"].replace(",", ""))
        # slippage-normalized delta: positive constant if basis matches
        deltas.append((row["Date and time"], (fill - opens[key]) * side))

values = [d for _, d in deltas]
values_sorted = sorted(values)
median = values_sorted[len(values_sorted) // 2]
print(f"entries checked: {len(deltas)}  |  bars missing: {missing}")
print(f"delta (fill-open, side-adjusted): median {median:+.2f}  "
      f"min {values_sorted[0]:+.2f}  max {values_sorted[-1]:+.2f}")

by_quarter = defaultdict(list)
for stamp, delta in deltas:
    by_quarter[stamp[:7]].append(delta)
print("\nmonth   n    median   worst|delta-median|")
bad = []
for month in sorted(by_quarter):
    ds = sorted(by_quarter[month])
    med = ds[len(ds) // 2]
    worst = max(abs(d - median) for d in ds)
    flag = "  <-- CHECK" if worst > 5 else ""
    print(f"{month}  {len(ds):4d}  {med:+7.2f}  {worst:10.2f}{flag}")
    if worst > 5:
        bad.extend((s, d) for s, d in deltas if s.startswith(month) and abs(d - median) > 5)

if bad:
    print("\noutliers:")
    for stamp, delta in bad[:20]:
        print(f"  {stamp}  {delta:+10.2f}")
