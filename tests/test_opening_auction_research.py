from dataclasses import replace
from pathlib import Path

from full_python.events import EventLedger, EventType
from full_python.models import Trade
from full_python.research.opening_auction import (
    attribute_trades,
    build_opening_auction_report,
)
from full_python.strategy.opening_auction_regime import (
    AuctionClassification,
    AuctionDiagnosticEvent,
    AuctionRegime,
    AuctionSessionSnapshot,
    AuctionSide,
    FAILED_AUCTION_REASON,
    OpeningAuctionFeatures,
)
from scripts.run_opening_auction_experiment import (
    _session_boundary_utc,
    load_bars_before_session,
)


def _trade() -> Trade:
    return Trade(
        symbol="NQ1!",
        side="long",
        quantity=1,
        entry_timestamp_utc="2024-06-03T13:45:00Z",
        entry_price=110.75,
        exit_timestamp_utc="2024-06-03T15:30:00Z",
        exit_price=120.0,
        exit_reason="target",
        stop_price=85.0,
        gross_points=9.25,
        gross_pnl=185.0,
        commission=10.0,
        net_pnl=175.0,
        mfe_points=10.0,
        mae_points=2.0,
        session_date="2024-06-03",
    )


def test_trade_attribution_joins_intent_fill_and_trade() -> None:
    ledger = EventLedger()
    ledger.append(
        EventType.ORDER_INTENT,
        timestamp_utc="2024-06-03T13:44:00Z",
        payload={
            "symbol": "NQ1!",
            "side": "buy",
            "quantity": 1,
            "order_type": "market_entry",
            "reason": FAILED_AUCTION_REASON,
            "signal_price": 110.0,
            "stop_price": 85.0,
            "target_price": 150.0,
            "reference_type": "overnight_and_prior_low",
        },
    )
    ledger.append(
        EventType.FILL,
        timestamp_utc="2024-06-03T13:45:00Z",
        payload={
            "symbol": "NQ1!",
            "side": "buy",
            "quantity": 1,
            "price": 110.75,
            "reason": FAILED_AUCTION_REASON,
        },
    )

    attributed = attribute_trades([_trade()], ledger)

    assert len(attributed) == 1
    assert attributed[0].entry.branch == "failed_auction"
    assert attributed[0].entry.signal_price == 110.0
    assert attributed[0].entry.target_price == 150.0
    assert attributed[0].entry.reference_type == "overnight_and_prior_low"


def test_train_loader_stops_before_first_excluded_session_bar(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    path.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2024-12-31T22:59:00Z,NQ1!,100,101,99,100,10\n"
        "2024-12-31T23:00:00Z,NQ1!,200,201,199,200,20\n"
        "2025-01-02T14:30:00Z,NQ1!,300,301,299,300,30\n",
        encoding="utf-8",
    )

    bars, digest = load_bars_before_session(
        path, end_session_exclusive="2025-01-01"
    )

    assert _session_boundary_utc("2025-01-01") == "2024-12-31T23:00:00Z"
    assert [bar.timestamp_utc for bar in bars] == ["2024-12-31T22:59:00Z"]
    assert bars[0].open == 100.0
    assert len(digest) == 64


def test_candidate_report_discloses_fill_relative_geometry() -> None:
    ledger = EventLedger()
    ledger.append(
        EventType.ORDER_INTENT,
        timestamp_utc="2024-06-03T13:44:00Z",
        payload={
            "symbol": "NQ1!",
            "side": "buy",
            "quantity": 1,
            "order_type": "market_entry",
            "reason": FAILED_AUCTION_REASON,
            "signal_price": 110.0,
            "stop_price": 85.0,
            "target_price": 150.0,
            "reference_type": "overnight_low",
        },
    )
    ledger.append(
        EventType.FILL,
        timestamp_utc="2024-06-03T13:45:00Z",
        payload={
            "symbol": "NQ1!",
            "side": "buy",
            "quantity": 1,
            "price": 110.75,
            "reason": FAILED_AUCTION_REASON,
            "slippage_points": 0.75,
        },
    )
    features = OpeningAuctionFeatures(
        session_date="2024-06-03",
        classification_timestamp_utc="2024-06-03T13:44:00Z",
        complete_observation=True,
        roll_transition=False,
        complete_overnight=True,
        overnight_bar_count=900,
        overnight_max_gap_minutes=1,
        opening_minutes=tuple(range(570, 585)),
        dtr20=100.0,
        opening_volume_ratio=1.1,
        rth_open=120.0,
        opening_high=120.0,
        opening_low=90.0,
        opening_close=110.0,
        opening_width=30.0,
        opening_midpoint=105.0,
        efficiency_ratio=0.2,
        close_location=2 / 3,
        opening_vwap=105.0,
        closes_above_vwap=8,
        closes_below_vwap=7,
        last_vwap_sides=("short",) * 12 + ("long",) * 3,
        overnight_high=130.0,
        overnight_low=100.0,
        prior_rth_high=140.0,
        prior_rth_low=100.0,
        prior_rth_close=150.0,
    )
    snapshot = AuctionSessionSnapshot(
        features,
        AuctionClassification(
            AuctionRegime.FAILED_AUCTION,
            AuctionSide.LONG,
            "failed_low_reclaimed",
            "overnight_low",
            100.0,
        ),
    )
    warmup_snapshot = AuctionSessionSnapshot(
        replace(
            features,
            session_date="2021-03-16",
            classification_timestamp_utc="2021-03-16T13:44:00Z",
            dtr20=None,
            opening_volume_ratio=None,
            prior_rth_high=None,
            prior_rth_low=None,
            prior_rth_close=None,
        ),
        AuctionClassification(
            AuctionRegime.NO_TRADE,
            AuctionSide.NONE,
            "missing_reference_history",
        ),
    )
    events = [
        AuctionDiagnosticEvent(
            "2021-03-16",
            "2021-03-16T13:44:00Z",
            "classified",
            "no_trade",
            "none",
        ),
        AuctionDiagnosticEvent(
            "2024-06-03",
            "2024-06-03T13:44:00Z",
            "classified",
            "failed_auction",
            "long",
        ),
        AuctionDiagnosticEvent(
            "2024-06-03",
            "2024-06-03T13:44:00Z",
            "entry_confirmed",
            "failed_auction",
            "long",
        ),
    ]

    report = build_opening_auction_report(
        trades=[_trade()],
        ledger=ledger,
        snapshots=[warmup_snapshot, snapshot],
        diagnostic_events=events,
        point_value=20.0,
        score_start_session="2021-03-16",
        score_end_session_exclusive="2025-01-01",
    )

    row = report["fill_relative_trades"][0]
    assert row["adverse_entry_gap_points"] == 0.75
    assert row["fill_risk_points"] == 25.75
    assert row["fill_target_reward_points"] == 39.25
    assert row["target_behind_fill_points"] == 0.0
    assert report["execution_diagnostics"]["target_behind_fill_count"] == 0
    assert report["execution_diagnostics"]["modeled_slippage_drag_dollars"] == 15.0
    assert report["funnel"] == {"classified": 1, "entry_confirmed": 1}
    assert report["score_window"]["classified_sessions_total_audit"] == 2
    assert report["score_window"]["gate_eligible_sessions"] == 1
    assert report["daily"]["trading_days"] == 1
    assert report["t1_primary_gates"]["passed"] is False
