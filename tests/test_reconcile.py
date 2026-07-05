from pathlib import Path

import pytest

from full_python.reconcile import load_sim_trades, load_tv_trades, reconcile

TV_EXPORT = (
    "﻿Trade #,Type,Date and time,Signal,Price USD,Size (qty),Size (value),Net P&L USD\n"
    "1,Entry long,2026-06-30 09:33,Long,20105.25,1,402105,0\n"
    "1,Exit long,2026-06-30 09:41,Stop Loss,20074.25,1,401485,-625\n"
    "2,Entry short,2026-06-30 09:52,Short,20080.50,1,401610,0\n"
    "2,Exit short,2026-06-30 10:31,ATF Flip,20010.00,1,400200,1400\n"
    "3,Entry long,2026-07-01 09:36,Long,20150.00,1,403000,0\n"
    "3,Exit long,2026-07-01 15:59,Hard Backstop,20200.00,1,404000,995\n"
)

# Sim: trade A matches TV #1 (entry 1 minute later), trade B matches TV #2,
# TV #3 is missing in sim, and the 11:20 long is extra in sim.
SIM_TRADES = (
    "symbol,side,quantity,entry_timestamp_utc,entry_price,exit_timestamp_utc,exit_price,"
    "exit_reason,stop_price,gross_points,gross_pnl,commission,net_pnl,mfe_points,mae_points,"
    "session_date,ambiguous_exit\n"
    "NQU2026,long,1,2026-06-30T13:34:00Z,20106.25,2026-06-30T13:41:00Z,20074.0,stop,20074.25,"
    "-32.25,-64.5,1.0,-65.5,3.0,32.5,2026-06-30,False\n"
    "NQU2026,short,1,2026-06-30T13:52:00Z,20079.5,2026-06-30T14:31:00Z,20011.0,atf_flip,20110.5,"
    "68.5,137.0,1.0,136.0,70.0,4.0,2026-06-30,False\n"
    "NQU2026,long,1,2026-06-30T15:20:00Z,20120.0,2026-06-30T15:45:00Z,20125.0,atf_flip,20090.0,"
    "5.0,10.0,1.0,9.0,8.0,2.0,2026-06-30,False\n"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_tv_export_parsing_pairs_entry_and_exit_legs(tmp_path: Path) -> None:
    trades = load_tv_trades(_write(tmp_path, "tv.csv", TV_EXPORT))

    assert len(trades) == 3
    first = trades[0]
    assert first.side == "long"
    assert first.entry_price == 20105.25
    assert first.exit_signal == "Stop Loss"
    assert first.entry_time.utcoffset().total_seconds() == -4 * 3600  # EDT


def test_reconcile_matches_missing_and_extra(tmp_path: Path) -> None:
    tv_trades = load_tv_trades(_write(tmp_path, "tv.csv", TV_EXPORT))
    sim_trades = load_sim_trades(_write(tmp_path, "trades.csv", SIM_TRADES))

    report = reconcile(tv_trades, sim_trades, tolerance_minutes=3.0)

    assert report.tv_trade_count == 3
    assert report.sim_trade_count == 3
    assert report.matched_count == 2
    assert len(report.missing_in_sim) == 1
    assert report.missing_in_sim[0]["tv_trade_number"] == "3"
    assert len(report.extra_in_sim) == 1
    assert report.extra_in_sim[0]["exit_reason"] == "atf_flip"

    long_match = next(m for m in report.matches if m["side"] == "long")
    assert long_match["entry_time_delta_minutes"] == 1.0
    assert long_match["entry_price_delta"] == 1.0
    assert long_match["sim_exit_reason"] == "stop"

    summary = report.summary()
    assert summary["match_rate"] == 2 / 3
    assert summary["max_abs_entry_price_delta"] == 1.0


def test_tv_export_parses_net_pnl_column(tmp_path: Path) -> None:
    trades = load_tv_trades(_write(tmp_path, "tv_pnl.csv", TV_EXPORT))
    assert trades[0].net_pnl == -625.0
    assert trades[1].net_pnl == 1400.0


def test_matched_pair_carries_exit_time_delta_and_pnl_delta(tmp_path: Path) -> None:
    tv_trades = load_tv_trades(_write(tmp_path, "tv.csv", TV_EXPORT))
    sim_trades = load_sim_trades(_write(tmp_path, "trades.csv", SIM_TRADES))

    report = reconcile(tv_trades, sim_trades, tolerance_minutes=3.0)

    long_match = next(m for m in report.matches if m["side"] == "long")
    assert long_match["exit_time_delta_minutes"] == 0.0
    assert long_match["tv_exit_signal"] == "Stop Loss"
    # sim net_pnl (-65.5) - tv net_pnl (-625.0)
    assert long_match["net_pnl_delta"] == pytest.approx(-65.5 - (-625.0))


def test_side_mismatch_never_matches(tmp_path: Path) -> None:
    tv_trades = load_tv_trades(_write(tmp_path, "tv.csv", TV_EXPORT))
    flipped = SIM_TRADES.replace(",long,", ",short,").replace(
        ",short,1,2026-06-30T13:52", ",long,1,2026-06-30T13:52"
    )
    sim_trades = load_sim_trades(_write(tmp_path, "trades.csv", flipped))

    report = reconcile(tv_trades, sim_trades, tolerance_minutes=3.0)

    assert report.matched_count == 0
