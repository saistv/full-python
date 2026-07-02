# Selected Contract Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a replay-ready CSV stream that loads only the selected outright contract for each Databento daily file according to the dominant contract calendar.

**Architecture:** Add a focused `full_python.data.selected_stream` module that takes a `ContractCalendar`, loads each selected Databento contract, and writes a canonical bar CSV with provenance columns. Add a CLI command that scans the folder, builds the dominant calendar, writes `selected_bars.csv`, and writes a JSON manifest describing source files, rule, row count, and skipped entries.

**Tech Stack:** Python 3.9, csv/json/dataclasses, existing Databento loader, existing calendar/inventory modules, pytest.

---

### Task 1: Selected Stream Module

**Files:**
- Create: `src/full_python/data/selected_stream.py`
- Test: `tests/test_selected_stream.py`

- [ ] **Step 1: Write failing tests**

Create tests that build two tiny Databento files and a calendar, then verify `build_selected_contract_stream` returns rows from only the selected contracts and includes provenance:

```python
stream = build_selected_contract_stream(calendar)
assert [row.symbol for row in stream.rows] == ["NQH5", "NQM5"]
assert stream.rows[0].source_file.endswith("glbx-mdp3-20250203.ohlcv-1m.csv.zst")
assert stream.rows[0].selection_rule == "dominant_outright_row_count"
```

- [ ] **Step 2: Run focused tests to verify failure**

Run:

```bash
python3 -m pytest tests/test_selected_stream.py -q
```

Expected: fail because `full_python.data.selected_stream` does not exist.

- [ ] **Step 3: Implement selected stream dataclasses and builder**

Implement:

```python
@dataclass(frozen=True)
class SelectedContractBar:
    timestamp_utc: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_file: str
    trading_date: str
    selected_contract: str
    selection_rule: str

@dataclass(frozen=True)
class SelectedContractStream:
    rows: list[SelectedContractBar]
    skipped_entries: list[dict[str, str]]
```

Use `load_databento_ohlcv_bars(..., contract_symbol=entry.selected_contract)` per calendar entry. Skip entries whose selected contract is `None` and record the skipped path/date.

- [ ] **Step 4: Add CSV/manifest writers**

Implement:

```python
write_selected_contract_stream_csv(stream, path)
write_selected_contract_stream_manifest(stream, path, calendar)
```

CSV columns:

```text
timestamp,symbol,open,high,low,close,volume,source_file,trading_date,selected_contract,selection_rule
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
python3 -m pytest tests/test_selected_stream.py -q
```

Expected: pass.

### Task 2: CLI Command

**Files:**
- Modify: `src/full_python/cli.py`
- Test: `tests/test_cli_selected_stream.py`

- [ ] **Step 1: Write failing CLI test**

Test:

```python
python3 -m full_python.cli build-selected-stream --folder tmp_path --output-dir output_dir
```

Expected files:

```text
selected_bars.csv
selected_bars_manifest.json
```

- [ ] **Step 2: Run focused test to verify failure**

Run:

```bash
python3 -m pytest tests/test_cli_selected_stream.py -q
```

Expected: fail because the subcommand does not exist.

- [ ] **Step 3: Implement CLI command**

Add:

```text
build-selected-stream --folder --output-dir --symbol-root
```

The command should inventory the folder, build the dominant calendar, build the selected stream, write CSV and manifest, and print the CSV path.

- [ ] **Step 4: Run focused CLI tests**

Run:

```bash
python3 -m pytest tests/test_cli_selected_stream.py -q
```

Expected: pass.

### Task 3: Real Smoke, Docs, Commit

**Files:**
- Modify: `README.md`
- Create: `docs/runs/2026-07-01-selected-contract-stream-smoke.md`

- [ ] **Step 1: Run real smoke**

Run:

```bash
PYTHONPATH=src python3 -m full_python.cli build-selected-stream \
  --folder "/Users/sais/Library/CloudStorage/Dropbox/Downloads/Claude_Projects/Hybrid/NQ 5 years" \
  --output-dir /private/tmp/full_python_selected_stream_20260701
```

Expected: `selected_bars.csv` and `selected_bars_manifest.json` written.

- [ ] **Step 2: Document usage and findings**

Add README usage and a run note with row count, first/last timestamps, selected contracts count, and skipped entries.

- [ ] **Step 3: Verify, commit, push**

Run:

```bash
python3 -m pytest -q
git add README.md docs/runs/2026-07-01-selected-contract-stream-smoke.md docs/superpowers/plans/2026-07-01-selected-contract-stream.md src/full_python/cli.py src/full_python/data/selected_stream.py tests/test_cli_selected_stream.py tests/test_selected_stream.py
git commit -m "feat: build selected contract bar stream"
git push
```
