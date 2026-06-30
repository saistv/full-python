# Databento Contract Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an auditable contract inventory tool that scans Databento OHLCV `.csv.zst` files and shows which NQ contracts are present before replay or optimization.

**Architecture:** Add a focused `full_python.data.inventory` module that streams compressed CSV files, summarizes rows by symbol, and returns serializable records. Expose it through the existing CLI as an `inventory-databento` subcommand that writes JSON and optional Markdown, leaving replay behavior unchanged.

**Tech Stack:** Python 3.9, standard `csv`/`json`/`dataclasses`, existing `zstandard` dependency, pytest.

---

### Task 1: Contract Inventory Module

**Files:**
- Create: `src/full_python/data/inventory.py`
- Test: `tests/test_databento_inventory.py`

- [ ] **Step 1: Write failing tests**

Create tests that write tiny `.csv.zst` files and verify:

```python
def test_inventory_counts_symbols_and_timestamps(tmp_path: Path) -> None:
    inventory = inspect_databento_ohlcv_file(data_path)
    assert inventory.path == str(data_path)
    assert inventory.symbols["NQH5"].row_count == 2
    assert inventory.symbols["NQH5"].start_timestamp_utc == "2025-02-03T00:00:00Z"
    assert inventory.symbols["NQH5"].end_timestamp_utc == "2025-02-03T00:01:00Z"


def test_inventory_folder_sorts_files(tmp_path: Path) -> None:
    inventories = inspect_databento_ohlcv_folder(tmp_path)
    assert [Path(item.path).name for item in inventories] == [
        "a.ohlcv-1m.csv.zst",
        "b.ohlcv-1m.csv.zst",
    ]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m pytest tests/test_databento_inventory.py -q
```

Expected: fail because `full_python.data.inventory` does not exist.

- [ ] **Step 3: Implement module**

Add immutable dataclasses:

```python
@dataclass(frozen=True)
class SymbolInventory:
    row_count: int
    start_timestamp_utc: str
    end_timestamp_utc: str


@dataclass(frozen=True)
class DatabentoFileInventory:
    path: str
    file_size_bytes: int
    symbols: dict[str, SymbolInventory]
```

Implement:

```python
def inspect_databento_ohlcv_file(path: str | Path, symbol_root: str = "NQ") -> DatabentoFileInventory
def inspect_databento_ohlcv_folder(folder: str | Path, symbol_root: str = "NQ") -> list[DatabentoFileInventory]
```

Normalize timestamps using the same Databento timestamp rule as `data.databento`.

- [ ] **Step 4: Run focused tests**

Run:

```bash
python3 -m pytest tests/test_databento_inventory.py -q
```

Expected: pass.

### Task 2: CLI Inventory Command

**Files:**
- Modify: `src/full_python/cli.py`
- Test: `tests/test_cli_inventory.py`

- [ ] **Step 1: Write failing CLI tests**

Test that the new command writes `contract_inventory.json`:

```python
completed = subprocess.run(
    [
        sys.executable,
        "-m",
        "full_python.cli",
        "inventory-databento",
        "--folder",
        str(tmp_path),
        "--output-dir",
        str(output_dir),
    ],
    check=True,
)
assert (output_dir / "contract_inventory.json").exists()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m pytest tests/test_cli_inventory.py -q
```

Expected: fail because the subcommand does not exist.

- [ ] **Step 3: Implement CLI subcommand**

Add `inventory-databento` subparser with:

```text
--folder
--output-dir
--symbol-root
--markdown
```

Write `contract_inventory.json`, and when `--markdown` is present, write `contract_inventory.md`.

- [ ] **Step 4: Run focused CLI tests**

Run:

```bash
python3 -m pytest tests/test_cli_inventory.py -q
```

Expected: pass.

### Task 3: Real Data Smoke And Docs

**Files:**
- Modify: `README.md`
- Create: `docs/runs/2026-06-30-contract-inventory-smoke.md`

- [ ] **Step 1: Run real folder smoke**

Run:

```bash
PYTHONPATH=src python3 -m full_python.cli inventory-databento \
  --folder "/Users/sais/Library/CloudStorage/Dropbox/Downloads/Claude_Projects/Hybrid/NQ 5 years" \
  --output-dir /private/tmp/full_python_contract_inventory_20260630 \
  --markdown
```

Expected: writes JSON and Markdown inventory files.

- [ ] **Step 2: Document command and findings**

Update README with the command and record a run note showing the first few files and the reason this blocks accidental mixed-contract replay.

- [ ] **Step 3: Full verification**

Run:

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

Commit with:

```bash
git add README.md docs/runs/2026-06-30-contract-inventory-smoke.md docs/superpowers/plans/2026-06-30-databento-contract-inventory.md src/full_python/cli.py src/full_python/data/inventory.py tests/test_cli_inventory.py tests/test_databento_inventory.py
git commit -m "feat: add Databento contract inventory"
```
