# Baseline Replay Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first reproducible Full Python baseline report: canonical data loading, data manifest, strategy config/hash, baseline momentum-breakout replay, rejected-signal logging, and MNQ-first survivability metrics.

**Architecture:** Keep the system modular. `data` converts vendor files into canonical `MarketBar` records, `strategy` emits `StrategyResult` objects, `replay` logs every event through `EventLedger`, and `reporting` summarizes a completed run. No execution adapter, broker API, TradingView dependency, or broad optimization is part of this milestone.

**Tech Stack:** Python 3.9, dataclasses, stdlib `csv`/`json`/`hashlib`/`pathlib`, existing `pytest`, optional `pandas` dependency already present but not required for the first pass.

---

## File Structure

- Create `src/full_python/data/__init__.py`: data package marker.
- Create `src/full_python/data/manifest.py`: immutable data manifest and manifest hash.
- Create `src/full_python/data/loaders.py`: CSV-to-`MarketBar` loader with explicit column mapping.
- Create `src/full_python/strategy/__init__.py`: strategy package marker.
- Create `src/full_python/strategy/config.py`: baseline strategy config and stable parameter hash.
- Create `src/full_python/strategy/baseline.py`: simple auditable momentum-breakout baseline.
- Create `src/full_python/reporting/__init__.py`: reporting package marker.
- Create `src/full_python/reporting/survivability.py`: metrics and report model.
- Create `src/full_python/cli.py`: first command-line entry point for a baseline run.
- Modify `README.md`: document how to run the baseline command.
- Test `tests/test_data_loading.py`: manifest and CSV loader behavior.
- Test `tests/test_strategy_config.py`: config hashing and MNQ-first defaults.
- Test `tests/test_baseline_strategy.py`: accepted and rejected signal behavior.
- Test `tests/test_survivability_report.py`: drawdown, loss streak, top-trade dependency.
- Test `tests/test_cli_baseline.py`: end-to-end CLI writes event log and report.

---

### Task 1: Data Manifest And CSV Loader

**Files:**
- Create: `src/full_python/data/__init__.py`
- Create: `src/full_python/data/manifest.py`
- Create: `src/full_python/data/loaders.py`
- Test: `tests/test_data_loading.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_data_loading.py`:

```python
from pathlib import Path

from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.manifest import DataManifest


def test_data_manifest_has_stable_hash() -> None:
    manifest = DataManifest(
        dataset_name="tiny-nq",
        source="fixture",
        symbol="NQ",
        contract="NQU2026",
        timezone="UTC",
        session="RTH",
        start_timestamp_utc="2026-06-30T13:30:00Z",
        end_timestamp_utc="2026-06-30T13:31:00Z",
        path="tests/fixtures/tiny_nq.csv",
    )

    assert manifest.stable_hash() == manifest.stable_hash()
    assert len(manifest.stable_hash()) == 64
    assert manifest.to_dict()["contract"] == "NQU2026"


def test_load_csv_bars_converts_rows_to_market_bars(tmp_path: Path) -> None:
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(
        "ts,symbol,o,h,l,c,v\n"
        "2026-06-30T13:30:00Z,NQU2026,100,101,99,100.5,10\n"
        "2026-06-30T13:31:00Z,NQU2026,100.5,102,100.25,101.75,12\n",
        encoding="utf-8",
    )
    column_map = CsvBarColumnMap(
        timestamp="ts",
        symbol="symbol",
        open="o",
        high="h",
        low="l",
        close="c",
        volume="v",
    )

    bars = load_csv_bars(csv_path, column_map)

    assert len(bars) == 2
    assert bars[0].timestamp_utc == "2026-06-30T13:30:00Z"
    assert bars[0].symbol == "NQU2026"
    assert bars[1].close == 101.75
    assert bars[1].volume == 12.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_data_loading.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'full_python.data'`.

- [ ] **Step 3: Implement data manifest**

Create `src/full_python/data/__init__.py`:

```python
"""Data loading boundaries for Full Python."""
```

Create `src/full_python/data/manifest.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class DataManifest:
    dataset_name: str
    source: str
    symbol: str
    contract: str
    timezone: str
    session: str
    start_timestamp_utc: str
    end_timestamp_utc: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def stable_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Implement CSV loader**

Create `src/full_python/data/loaders.py`:

```python
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from full_python.models import MarketBar


@dataclass(frozen=True)
class CsvBarColumnMap:
    timestamp: str
    symbol: str
    open: str
    high: str
    low: str
    close: str
    volume: str


def load_csv_bars(path: str | Path, column_map: CsvBarColumnMap) -> list[MarketBar]:
    input_path = Path(path)
    bars: list[MarketBar] = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            bars.append(
                MarketBar(
                    timestamp_utc=row[column_map.timestamp],
                    symbol=row[column_map.symbol],
                    open=float(row[column_map.open]),
                    high=float(row[column_map.high]),
                    low=float(row[column_map.low]),
                    close=float(row[column_map.close]),
                    volume=float(row[column_map.volume]),
                )
            )
    return bars
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_data_loading.py -q
```

Expected: PASS, `2 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/full_python/data tests/test_data_loading.py
git commit -m "feat: add canonical data manifest and CSV loader"
```

---

### Task 2: Strategy Config And Stable Parameter Hash

**Files:**
- Create: `src/full_python/strategy/__init__.py`
- Create: `src/full_python/strategy/config.py`
- Test: `tests/test_strategy_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_config.py`:

```python
from full_python.strategy.config import BaselineMomentumConfig


def test_baseline_config_defaults_are_mnq_first_and_rth_promotable() -> None:
    config = BaselineMomentumConfig()

    assert config.instrument_for_risk == "MNQ"
    assert config.promote_session == "RTH"
    assert config.max_drawdown_dollars == 5000.0
    assert config.contract_multiplier == 2.0


def test_baseline_config_hash_changes_when_parameters_change() -> None:
    base = BaselineMomentumConfig()
    changed = BaselineMomentumConfig(breakout_lookback_bars=3)

    assert len(base.parameter_hash()) == 64
    assert base.parameter_hash() != changed.parameter_hash()
    assert base.to_dict()["breakout_lookback_bars"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_strategy_config.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'full_python.strategy'`.

- [ ] **Step 3: Implement config**

Create `src/full_python/strategy/__init__.py`:

```python
"""Strategy implementations and configuration."""
```

Create `src/full_python/strategy/config.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class BaselineMomentumConfig:
    name: str = "baseline_momentum_breakout"
    instrument_for_signal: str = "NQ"
    instrument_for_risk: str = "MNQ"
    promote_session: str = "RTH"
    max_drawdown_dollars: float = 5000.0
    contract_multiplier: float = 2.0
    commission_per_contract: float = 1.0
    slippage_points: float = 1.0
    breakout_lookback_bars: int = 2
    stop_points: float = 30.0
    min_body_points: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def parameter_hash(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_strategy_config.py -q
```

Expected: PASS, `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/full_python/strategy tests/test_strategy_config.py
git commit -m "feat: add baseline strategy config"
```

---

### Task 3: Baseline Momentum Strategy With Rejected Signals

**Files:**
- Create: `src/full_python/strategy/baseline.py`
- Test: `tests/test_baseline_strategy.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_baseline_strategy.py`:

```python
from full_python.models import MarketBar
from full_python.strategy.baseline import BaselineMomentumStrategy
from full_python.strategy.config import BaselineMomentumConfig


def test_baseline_strategy_rejects_until_enough_history_exists() -> None:
    strategy = BaselineMomentumStrategy(BaselineMomentumConfig())
    bar = MarketBar(
        timestamp_utc="2026-06-30T13:30:00Z",
        symbol="NQU2026",
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
    )

    result = strategy.on_bar(bar)

    assert result.signal is not None
    assert result.signal.decision == "rejected"
    assert result.signal.reason == "insufficient_history"
    assert result.order_intents == ()


def test_baseline_strategy_accepts_breakout_after_history() -> None:
    strategy = BaselineMomentumStrategy(BaselineMomentumConfig(breakout_lookback_bars=2))
    bars = [
        MarketBar("2026-06-30T13:30:00Z", "NQU2026", 100, 101, 99, 100, 10),
        MarketBar("2026-06-30T13:31:00Z", "NQU2026", 100, 102, 99, 101, 10),
        MarketBar("2026-06-30T13:32:00Z", "NQU2026", 101, 103, 100, 102.5, 10),
    ]

    first = strategy.on_bar(bars[0])
    second = strategy.on_bar(bars[1])
    third = strategy.on_bar(bars[2])

    assert first.signal.reason == "insufficient_history"
    assert second.signal.reason == "insufficient_history"
    assert third.signal.decision == "accepted"
    assert third.signal.side == "long"
    assert third.order_intents[0].side == "buy"
    assert third.order_intents[0].metadata["stop_price"] == 72.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_baseline_strategy.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'full_python.strategy.baseline'`.

- [ ] **Step 3: Implement baseline strategy**

Create `src/full_python/strategy/baseline.py`:

```python
from __future__ import annotations

from full_python.models import MarketBar, OrderIntent, SignalDecision, StrategyResult
from full_python.strategy.config import BaselineMomentumConfig


class BaselineMomentumStrategy:
    def __init__(self, config: BaselineMomentumConfig) -> None:
        self.config = config
        self._history: list[MarketBar] = []

    def on_bar(self, bar: MarketBar) -> StrategyResult:
        if len(self._history) < self.config.breakout_lookback_bars:
            self._history.append(bar)
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="long",
                    reason="insufficient_history",
                    metadata={"history_bars": len(self._history)},
                )
            )

        prior_high = max(prior.high for prior in self._history[-self.config.breakout_lookback_bars :])
        body_points = abs(bar.close - bar.open)
        is_breakout = bar.close > prior_high
        body_pass = body_points >= self.config.min_body_points
        self._history.append(bar)

        if not is_breakout:
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="long",
                    reason="no_breakout",
                    metadata={"prior_high": prior_high, "close": bar.close},
                )
            )

        if not body_pass:
            return StrategyResult(
                signal=SignalDecision.rejected(
                    timestamp_utc=bar.timestamp_utc,
                    symbol=bar.symbol,
                    side="long",
                    reason="body_too_small",
                    metadata={"body_points": body_points, "min_body_points": self.config.min_body_points},
                )
            )

        stop_price = bar.close - self.config.stop_points
        signal = SignalDecision.accepted(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side="long",
            reason="breakout",
            metadata={"prior_high": prior_high, "stop_price": stop_price},
        )
        order_intent = OrderIntent.market_entry(
            timestamp_utc=bar.timestamp_utc,
            symbol=bar.symbol,
            side="buy",
            quantity=1,
            reason="breakout",
            metadata={"stop_price": stop_price},
        )
        return StrategyResult(signal=signal, order_intents=(order_intent,))
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_baseline_strategy.py -q
```

Expected: PASS, `2 passed`.

- [ ] **Step 5: Run replay tests to verify event ordering still passes**

Run:

```bash
python3 -m pytest tests/test_replay_engine.py tests/test_baseline_strategy.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/full_python/strategy/baseline.py tests/test_baseline_strategy.py
git commit -m "feat: add baseline momentum strategy"
```

---

### Task 4: Survivability Metrics And Report Model

**Files:**
- Create: `src/full_python/reporting/__init__.py`
- Create: `src/full_python/reporting/survivability.py`
- Test: `tests/test_survivability_report.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_survivability_report.py`:

```python
from full_python.reporting.survivability import TradeResult, build_survivability_report


def test_survivability_report_calculates_drawdown_and_loss_streak() -> None:
    trades = [
        TradeResult("2026-06-30T13:31:00Z", "long", 100.0),
        TradeResult("2026-06-30T13:35:00Z", "long", -50.0),
        TradeResult("2026-06-30T13:40:00Z", "long", -75.0),
        TradeResult("2026-06-30T13:45:00Z", "long", 25.0),
    ]

    report = build_survivability_report(trades)

    assert report.net_pnl == 0.0
    assert report.max_drawdown == -125.0
    assert report.max_loss_streak == 2
    assert report.trade_count == 4


def test_survivability_report_tracks_top_trade_dependency() -> None:
    trades = [
        TradeResult("2026-06-30T13:31:00Z", "long", 500.0),
        TradeResult("2026-06-30T13:35:00Z", "long", -100.0),
        TradeResult("2026-06-30T13:40:00Z", "short", 50.0),
    ]

    report = build_survivability_report(trades)

    assert report.net_pnl == 450.0
    assert report.pnl_without_best_trade == -50.0
    assert report.long_pnl == 400.0
    assert report.short_pnl == 50.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_survivability_report.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'full_python.reporting'`.

- [ ] **Step 3: Implement survivability report**

Create `src/full_python/reporting/__init__.py`:

```python
"""Research reporting and survivability metrics."""
```

Create `src/full_python/reporting/survivability.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TradeResult:
    exit_timestamp_utc: str
    side: str
    pnl: float


@dataclass(frozen=True)
class SurvivabilityReport:
    trade_count: int
    net_pnl: float
    max_drawdown: float
    max_loss_streak: int
    pnl_without_best_trade: float
    long_pnl: float
    short_pnl: float

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def build_survivability_report(trades: list[TradeResult]) -> SurvivabilityReport:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    current_loss_streak = 0
    max_loss_streak = 0
    long_pnl = 0.0
    short_pnl = 0.0

    for trade in trades:
        equity += trade.pnl
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if trade.pnl < 0:
            current_loss_streak += 1
            max_loss_streak = max(max_loss_streak, current_loss_streak)
        else:
            current_loss_streak = 0
        if trade.side == "long":
            long_pnl += trade.pnl
        elif trade.side == "short":
            short_pnl += trade.pnl

    best_trade = max((trade.pnl for trade in trades), default=0.0)
    net_pnl = sum(trade.pnl for trade in trades)
    return SurvivabilityReport(
        trade_count=len(trades),
        net_pnl=net_pnl,
        max_drawdown=max_drawdown,
        max_loss_streak=max_loss_streak,
        pnl_without_best_trade=net_pnl - best_trade,
        long_pnl=long_pnl,
        short_pnl=short_pnl,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_survivability_report.py -q
```

Expected: PASS, `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/full_python/reporting tests/test_survivability_report.py
git commit -m "feat: add survivability report metrics"
```

---

### Task 5: End-To-End Baseline CLI

**Files:**
- Create: `src/full_python/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli_baseline.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_baseline.py`:

```python
import json
from pathlib import Path

from full_python.cli import run_baseline


def test_run_baseline_writes_event_log_and_report(tmp_path: Path) -> None:
    data_path = tmp_path / "bars.csv"
    data_path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-06-30T13:30:00Z,NQU2026,100,101,99,100,10\n"
        "2026-06-30T13:31:00Z,NQU2026,100,102,99,101,10\n"
        "2026-06-30T13:32:00Z,NQU2026,101,103,100,102.5,10\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "run"

    report_path = run_baseline(data_path=data_path, output_dir=output_dir)

    events_path = output_dir / "events.jsonl"
    assert events_path.exists()
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["strategy"]["name"] == "baseline_momentum_breakout"
    assert report["data"]["path"] == str(data_path)
    assert report["survivability"]["trade_count"] == 0
    assert len(report["strategy"]["parameter_hash"]) == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_cli_baseline.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'full_python.cli'`.

- [ ] **Step 3: Implement CLI run function**

Create `src/full_python/cli.py`:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from full_python.data.loaders import CsvBarColumnMap, load_csv_bars
from full_python.data.manifest import DataManifest
from full_python.replay import ReplayEngine
from full_python.reporting.survivability import build_survivability_report
from full_python.strategy.baseline import BaselineMomentumStrategy
from full_python.strategy.config import BaselineMomentumConfig


def run_baseline(*, data_path: str | Path, output_dir: str | Path) -> Path:
    input_path = Path(data_path)
    run_dir = Path(output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    column_map = CsvBarColumnMap(
        timestamp="timestamp",
        symbol="symbol",
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
    )
    bars = load_csv_bars(input_path, column_map)
    if not bars:
        raise ValueError(f"No bars loaded from {input_path}")

    manifest = DataManifest(
        dataset_name=input_path.stem,
        source="csv",
        symbol="NQ",
        contract=bars[0].symbol,
        timezone="UTC",
        session="UNKNOWN",
        start_timestamp_utc=bars[0].timestamp_utc,
        end_timestamp_utc=bars[-1].timestamp_utc,
        path=str(input_path),
    )
    config = BaselineMomentumConfig()
    strategy = BaselineMomentumStrategy(config)
    ledger = ReplayEngine().run(bars, strategy)
    events_path = run_dir / "events.jsonl"
    ledger.write_jsonl(events_path)

    survivability = build_survivability_report([])
    report = {
        "data": {
            **manifest.to_dict(),
            "manifest_hash": manifest.stable_hash(),
        },
        "strategy": {
            **config.to_dict(),
            "parameter_hash": config.parameter_hash(),
        },
        "events_path": str(events_path),
        "survivability": survivability.to_dict(),
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Full Python baseline replay.")
    parser.add_argument("--data", required=True, help="CSV file with timestamp,symbol,open,high,low,close,volume columns")
    parser.add_argument("--output-dir", required=True, help="Directory for report.json and events.jsonl")
    args = parser.parse_args()
    report_path = run_baseline(data_path=args.data, output_dir=args.output_dir)
    print(report_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Update README usage**

Append to `README.md`:

````markdown
## Baseline Replay Command

The first baseline command expects a CSV with:

```text
timestamp,symbol,open,high,low,close,volume
```

Run:

```bash
python3 -m full_python.cli --data path/to/bars.csv --output-dir runs/baseline-smoke
```

The command writes:

- `events.jsonl`
- `report.json`
````

- [ ] **Step 5: Run CLI test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_cli_baseline.py -q
```

Expected: PASS, `1 passed`.

- [ ] **Step 6: Run full suite**

Run:

```bash
python3 -m pytest -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/full_python/cli.py README.md tests/test_cli_baseline.py
git commit -m "feat: add baseline replay CLI"
```

---

### Task 6: Final Verification And Milestone Decision Note

**Files:**
- Create: `docs/decisions/2026-06-30-first-baseline-milestone.md`

- [ ] **Step 1: Create decision note**

Create `docs/decisions/2026-06-30-first-baseline-milestone.md`:

```markdown
# First Baseline Milestone

## Decision

The first Full Python milestone is a reproducible baseline replay report, not optimization and not live execution.

## Evidence

- Canonical CSV data boundary exists.
- Data manifest hash exists.
- Strategy config hash exists.
- Baseline strategy emits accepted and rejected decisions.
- Replay logs bars, signals, rejections, and order intents.
- Event log persists to JSONL.
- Baseline CLI writes `events.jsonl` and `report.json`.

## Constraints

- Risk validation remains MNQ-first.
- RTH candidates are the first promotion target.
- Pine remains reference material only.
- No broker adapter is included in this milestone.

## Next Review Trigger

After the first real NQ/MNQ historical file successfully produces a baseline report, review whether to port ATF, support/resistance, prove-it, and squeeze primitives into the baseline strategy.
```

- [ ] **Step 2: Run all tests**

Run:

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Check git status**

Run:

```bash
git status --short
```

Expected: only the new decision note is uncommitted.

- [ ] **Step 4: Commit decision note**

```bash
git add docs/decisions/2026-06-30-first-baseline-milestone.md
git commit -m "docs: record first baseline milestone"
```

---

## Self-Review Checklist

- Spec coverage: this plan covers canonical data loading, data manifest, baseline strategy skeleton, rejected-signal logging, and survivability report.
- Scope discipline: this plan does not include Tradovate, TradersPost, live execution, broad optimization, or mean reversion.
- Type consistency: `MarketBar`, `StrategyResult`, `OrderIntent`, `EventLedger`, `BaselineMomentumConfig`, and `DataManifest` are consistently named across tasks.
- Test discipline: every code task starts with a failing test, verifies the failure, implements minimal code, and reruns tests.
