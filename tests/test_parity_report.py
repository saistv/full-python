from pathlib import Path

from full_python.parity_report import build_parity_delta_report
from full_python.reconcile import load_sim_trades, load_tv_trades, reconcile

TV_EXPORT = (
    "﻿Trade #,Type,Date and time,Signal,Price USD,Size (qty),Size (value),Net P&L USD\n"
    "1,Entry long,2026-06-30 09:33,Long,20105.25,1,402105,0\n"
    "1,Exit long,2026-06-30 09:41,Stop Loss,20074.25,1,401485,-625\n"
    "2,Entry short,2026-06-30 09:52,Short,20080.50,1,401610,0\n"
    "2,Exit short,2026-06-30 10:31,ATF Flip,20010.00,1,400200,1400\n"
)
SIM_TRADES = (
    "symbol,side,quantity,entry_timestamp_utc,entry_price,exit_timestamp_utc,exit_price,"
    "exit_reason,stop_price,gross_points,gross_pnl,commission,net_pnl,mfe_points,mae_points,"
    "session_date,ambiguous_exit\n"
    "NQU2026,long,1,2026-06-30T13:34:00Z,20106.25,2026-06-30T13:41:00Z,20074.25,stop,20074.25,"
    "-32.0,-64.0,1.0,-65.0,3.0,32.5,2026-06-30,False\n"
    "NQU2026,short,1,2026-06-30T13:52:00Z,20080.5,2026-06-30T14:31:00Z,20010.0,atf_flip,20110.5,"
    "70.5,141.0,1.0,140.0,70.0,4.0,2026-06-30,False\n"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_parity_delta_report_decomposes_entry_and_exit_checks(tmp_path: Path) -> None:
    tv_trades = load_tv_trades(_write(tmp_path, "tv.csv", TV_EXPORT))
    sim_trades = load_sim_trades(_write(tmp_path, "trades.csv", SIM_TRADES))
    reconciliation = reconcile(tv_trades, sim_trades, tolerance_minutes=3.0)

    parity = build_parity_delta_report(reconciliation)

    assert parity.trade_count_exact is True  # 2 TV, 2 sim, 2 matched, 0 missing, 0 extra
    # Verified against actual build_parity_delta_report() output, not the originally
    # guessed numbers: the long leg's sim entry (20106.25 @ 13:34 UTC) differs from the
    # TV entry (20105.25 @ 09:33 ET = 13:33 UTC) by 1.0 price / 1.0 minute, so only the
    # short leg (exact match on both) is exact -> counts of 1, not 2.
    assert parity.entry_timestamp_exact_count == 1
    assert parity.entry_price_exact_count == 1
    # Normalization is "Stop Loss"/"ATF Flip" -> lowercase, spaces to underscores.
    # "ATF Flip" -> "atf_flip" matches the sim's "atf_flip"; "Stop Loss" -> "stop_loss"
    # does NOT match the sim's "stop". So exactly 1 of the 2 matched pairs is exact
    # under this normalization -- verified by running build_parity_delta_report()
    # against this fixture, not assumed.
    assert parity.exit_reason_exact_count == 1
    assert parity.max_abs_exit_price_delta == 0.0
    assert len(parity.largest_pnl_deltas) == 2
    assert parity.largest_pnl_deltas[0]["tv_trade_number"] in ("1", "2")


def test_parity_delta_report_flags_trade_count_mismatch() -> None:
    from full_python.reconcile import ReconciliationReport

    reconciliation = ReconciliationReport(
        tv_trade_count=3, sim_trade_count=2, matched_count=2,
        missing_in_sim=[{"tv_trade_number": "3"}],
    )

    parity = build_parity_delta_report(reconciliation)

    assert parity.trade_count_exact is False
