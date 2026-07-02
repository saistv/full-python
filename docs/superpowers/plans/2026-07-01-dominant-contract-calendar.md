# Dominant Contract Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-pass Databento contract calendar that selects one explicit outright NQ contract per daily OHLCV file using a transparent dominant-row-count rule.

**Architecture:** Add a focused `full_python.data.contract_calendar` module that consumes inventory records and emits calendar entries. Add a CLI command that scans a folder, writes `contract_calendar.json`, and optionally writes Markdown. This is not a back-adjusted continuous contract builder; it is the auditable contract-selection layer needed before multi-month replay.

**Tech Stack:** Python 3.9, dataclasses, json, existing Databento inventory module, pytest.

---

### Task 1: Calendar Module

**Files:**
- Create: `src/full_python/data/contract_calendar.py`
- Test: `tests/test_contract_calendar.py`

- [ ] **Step 1: Write failing tests**

Create tests for:

```python
def test_build_dominant_contract_calendar_selects_highest_row_count_outright():
    file_inventory = DatabentoFileInventory(
        path="/data/glbx-mdp3-20250203.ohlcv-1m.csv.zst",
        file_size_bytes=123,
        symbols={
            "NQH5": SymbolInventory(100, "2025-02-03T00:00:00Z", "2025-02-03T23:59:00Z"),
            "NQM5": SymbolInventory(200, "2025-02-03T00:00:00Z", "2025-02-03T23:59:00Z"),
            "NQH5-NQM5": SymbolInventory(300, "2025-02-03T00:00:00Z", "2025-02-03T23:59:00Z"),
        },
    )
    calendar = build_dominant_contract_calendar([file_inventory])
    assert calendar.entries[0].selected_contract == "NQM5"
    assert calendar.entries[0].selection_rule == "dominant_outright_row_count"
```

Also test deterministic tie-breaking by symbol name.

- [ ] **Step 2: Run focused tests to verify failure**

Run:

```bash
python3 -m pytest tests/test_contract_calendar.py -q
```

Expected: fail because `full_python.data.contract_calendar` does not exist.

- [ ] **Step 3: Implement module**

Implement:

```python
@dataclass(frozen=True)
class ContractCandidate:
    symbol: str
    row_count: int
    start_timestamp_utc: str
    end_timestamp_utc: str

@dataclass(frozen=True)
class ContractCalendarEntry:
    file_path: str
    trading_date: str
    selected_contract: str | None
    selection_rule: str
    candidates: list[ContractCandidate]

@dataclass(frozen=True)
class ContractCalendar:
    symbol_root: str
    selection_rule: str
    entries: list[ContractCalendarEntry]
```

Selection rule:
- ignore symbols containing `-`
- select the candidate with the highest row count
- if tied, select lexicographically smallest symbol for deterministic output
- derive `trading_date` from filename pattern `glbx-mdp3-YYYYMMDD.ohlcv-1m.csv.zst`

- [ ] **Step 4: Run focused tests**

Run:

```bash
python3 -m pytest tests/test_contract_calendar.py -q
```

Expected: pass.

### Task 2: CLI Calendar Command

**Files:**
- Modify: `src/full_python/cli.py`
- Test: `tests/test_cli_contract_calendar.py`

- [ ] **Step 1: Write failing CLI test**

Test:

```python
completed = subprocess.run(
    [
        sys.executable,
        "-m",
        "full_python.cli",
        "build-contract-calendar",
        "--folder",
        str(tmp_path),
        "--output-dir",
        str(output_dir),
        "--markdown",
    ],
    check=True,
)
assert (output_dir / "contract_calendar.json").exists()
assert (output_dir / "contract_calendar.md").exists()
```

- [ ] **Step 2: Run focused test to verify failure**

Run:

```bash
python3 -m pytest tests/test_cli_contract_calendar.py -q
```

Expected: fail because the subcommand does not exist.

- [ ] **Step 3: Implement CLI command**

Add a `build-contract-calendar` command with:

```text
--folder
--output-dir
--symbol-root
--markdown
```

It should scan the folder with `inspect_databento_ohlcv_folder`, build the calendar, write `contract_calendar.json`, and optionally write `contract_calendar.md`.

- [ ] **Step 4: Run focused CLI tests**

Run:

```bash
python3 -m pytest tests/test_cli_contract_calendar.py -q
```

Expected: pass.

### Task 3: Real Smoke, Docs, Commit

**Files:**
- Modify: `README.md`
- Create: `docs/runs/2026-07-01-dominant-contract-calendar-smoke.md`

- [ ] **Step 1: Run real smoke**

Run:

```bash
PYTHONPATH=src python3 -m full_python.cli build-contract-calendar \
  --folder "/Users/sais/Library/CloudStorage/Dropbox/Downloads/Claude_Projects/Hybrid/NQ 5 years" \
  --output-dir /private/tmp/full_python_contract_calendar_20260701 \
  --markdown
```

Expected: JSON and Markdown files written.

- [ ] **Step 2: Document usage and findings**

Add README usage and a run note with file count, first entries, and the caveat that dominant-row-count is a first-pass selection rule, not final roll research.

- [ ] **Step 3: Verify**

Run:

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit and push**

Commit:

```bash
git add README.md docs/runs/2026-07-01-dominant-contract-calendar-smoke.md docs/superpowers/plans/2026-07-01-dominant-contract-calendar.md src/full_python/cli.py src/full_python/data/contract_calendar.py tests/test_cli_contract_calendar.py tests/test_contract_calendar.py
git commit -m "feat: add dominant contract calendar"
git push
```
