"""Observe-mode live session runner (Gate 5, demo environment).

Composition root only: wires existing pieces together. Observe mode is
pinned HERE as literals (observe_adapter_config) -- no CLI flag, env
var, or parameter exists to enable orders, and the broker's REST client
is a sentinel that raises on any attribute access, so even a future
code path that tried to place an order would fail loudly. Enabling
orders is a different spec (the demo order test), not a config change.

Shutdown model: the persistent ledger flushes every event, so Ctrl+C
and crashes lose nothing; the runner's job on exit is only to cancel
the chart subscription, close the socket, and run the shadow report.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from full_python.data.sessions import classify_timestamp
from full_python.execution.live_loop import LiveLoop
from full_python.execution.supervisor import RiskSupervisor, RiskSupervisorConfig
from full_python.live.persistence import PersistentEventLedger
from full_python.live.recording import RecordingStrategy
from full_python.live.risk_probe import run_risk_probe
from full_python.live.session_report import run_report
from full_python.livedata.clock import Clock, SystemClock
from full_python.livedata.contract_authority import ContractAuthority
from full_python.livedata.live_bar_source import ActiveWindow, LiveBarSource
from full_python.models import MarketBar
from full_python.instruments import instrument_spec
from full_python.strategy.adaptive_trend import AdaptiveTrendStrategy
from full_python.strategy.adaptive_trend_config import production_am_config
from full_python.tradovate.auth import TradovateAuthClient
from full_python.tradovate.broker import TradovateBroker
from full_python.tradovate.config import (
    DEMO_ENVIRONMENT,
    TradovateAdapterConfig,
    credentials_from_env,
)
from full_python.tradovate.errors import TradovateOrderSafetyError
from full_python.tradovate.feed import TradovateMarketDataFeed
from full_python.tradovate.http import TradovateHttpClient, UrllibHttpTransport
from full_python.tradovate.transport import connect_websocket
from full_python.tradovate.ws import TradovateWebSocketClient

logger = logging.getLogger("full_python.live")

def observe_adapter_config(
    account_spec: str, account_id: int, root_symbol: str = "NQ"
) -> TradovateAdapterConfig:
    # The ONLY adapter config this runner can produce. Observe literals,
    # pinned by tests/test_live_runner.py; changing them is a spec change.
    spec = instrument_spec(root_symbol)
    return TradovateAdapterConfig(
        environment=DEMO_ENVIRONMENT,
        account_spec=account_spec,
        account_id=account_id,
        root_symbol=root_symbol,
        order_enabled=False,
        flatten_enabled=False,
        dollar_point_value=spec.dollar_point_value,
    )


class _NoOrderRestClient:
    """TradovateBroker with orders disabled never touches its REST
    client; this sentinel turns any attempt into a loud failure."""

    def __getattr__(self, name: str):
        raise TradovateOrderSafetyError(
            f"observe mode attempted broker REST call {name!r}"
        )


def now_utc_iso(clock: Clock) -> str:
    return clock.now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bars_until(
    source: Iterable[MarketBar],
    clock: Clock,
    end_minutes_et: int,
    maintenance: Optional[Callable[[], None]] = None,
) -> Iterator[MarketBar]:
    """Yields bars until the ET wall clock passes end_minutes_et; runs
    maintenance (token renewal) between bars. Returning ends LiveLoop's
    iteration cleanly (close_end_of_data path)."""
    for bar in source:
        yield bar
        if maintenance is not None:
            maintenance()
        session = classify_timestamp(now_utc_iso(clock))
        if session.minutes_from_midnight_et >= end_minutes_et:
            logger.info("session end time reached; stopping")
            return


def _report_path_for_events(events_path: Path) -> Path:
    """events.jsonl -> report.html, events-2.jsonl -> report-2.html: derive
    the report filename from the events stem so a second --report-only run
    doesn't clobber the first run's report."""
    stem = events_path.stem
    report_stem = "report" + stem[len("events"):] if stem.startswith("events") else f"report-{stem}"
    return events_path.with_name(report_stem + ".html")


def _fresh_run_paths(session_dir: Path) -> "tuple[Path, Path]":
    """One ledger file per run: events.jsonl, then events-2.jsonl, ...
    (PersistentEventLedger refuses to reopen an existing file)."""
    suffix = 1
    while True:
        name = "events.jsonl" if suffix == 1 else f"events-{suffix}.jsonl"
        events_path = session_dir / name
        if not events_path.exists():
            report_name = "report.html" if suffix == 1 else f"report-{suffix}.html"
            return events_path, session_dir / report_name
        suffix += 1


@dataclass
class ObserveSession:
    loop: LiveLoop
    broker: TradovateBroker
    ledger: PersistentEventLedger
    events_path: Path
    report_path: Path
    feed: TradovateMarketDataFeed
    ws: object


def build_observe_session(
    *,
    ws_client,
    clock: Clock,
    account_spec: str,
    account_id: int,
    data_dir: Path,
    bars_back: int,
    end_minutes_et: int,
    symbol_root: str = "NQ",
    maintenance: Optional[Callable[[], None]] = None,
) -> ObserveSession:
    session_info = classify_timestamp(now_utc_iso(clock))
    session_dir = Path(data_dir) / session_info.session_date.isoformat()
    events_path, report_path = _fresh_run_paths(session_dir)

    authority = ContractAuthority(symbol_root)
    front = authority.front_contract(session_info.session_date)
    logger.info("front contract for %s: %s", session_info.session_date, front)
    feed = TradovateMarketDataFeed(ws_client, symbol=front)
    feed.subscribe(closest_timestamp=now_utc_iso(clock), bars_back=bars_back)

    ledger = PersistentEventLedger(events_path)
    strategy_config = production_am_config()
    strategy = RecordingStrategy(AdaptiveTrendStrategy(strategy_config), ledger)
    broker = TradovateBroker(
        observe_adapter_config(account_spec, account_id, symbol_root),
        _NoOrderRestClient(),
    )
    window = ActiveWindow(
        strategy_config.entry_start_minutes_et, strategy_config.entry_end_minutes_et
    )
    calendar_end = (
        end_minutes_et
        if session_info.rth_close_minutes_et is None
        else min(end_minutes_et, session_info.rth_close_minutes_et + 5)
    )
    source = LiveBarSource(
        feed, clock, authority, window,
        position_provider=lambda: broker.position is not None,
        session_end_minutes_et=calendar_end,
    )
    bar_stream = bars_until(source, clock, calendar_end, maintenance)
    supervisor = RiskSupervisor(
        RiskSupervisorConfig(point_value=instrument_spec(symbol_root).dollar_point_value)
    )
    loop = LiveLoop(bar_stream, strategy, broker, supervisor, ledger)
    return ObserveSession(
        loop=loop, broker=broker, ledger=ledger, events_path=events_path,
        report_path=report_path, feed=feed, ws=ws_client,
    )


def run_observe_session(session: ObserveSession, *, reference_bars_path=None) -> int:
    halted: Optional[str] = None
    try:
        result = session.loop.run()
        halted = result.halted_reason
    except KeyboardInterrupt:
        logger.info("operator interrupt (Ctrl+C); ending session")
    except Exception as exc:  # noqa: BLE001 -- top-level catch-all per spec:
        # any uncaught error (e.g. TradovateWebSocketError on a TCP drop,
        # ValueError on a malformed chart bar) must still yield a report on
        # whatever was recorded and a nonzero exit, not a raw traceback.
        halted = f"unhandled: {exc}"
        logger.error("FATAL: %s", exc)
    finally:
        for closer in (session.feed.cancel, session.ws.close, session.ledger.close):
            try:
                closer()
            except Exception as exc:  # best-effort shutdown; report still runs
                logger.warning("shutdown step failed: %s", exc)
    if halted is not None:
        logger.error("HALT: %s", halted)
    logger.info("events: %s", session.events_path)
    report_exit = run_report(
        session.events_path,
        session.report_path,
        reference_bars_path=reference_bars_path,
    )
    return report_exit if halted is None else 2


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m full_python.live",
        description=(
            "Gate 5 observe-mode demo session runner. Orders are impossible "
            "by construction; there is no flag to enable them."
        ),
    )
    parser.add_argument("--data-dir", default="runs/live")
    parser.add_argument("--end-et", default="16:05",
                        help="ET wall-clock session end (HH:MM), default 16:05")
    parser.add_argument("--bars-back", type=int, default=400,
                        help="history bars for indicator warm-up (default 400)")
    parser.add_argument("--symbol-root", default="NQ")
    parser.add_argument("--report-only", metavar="EVENTS_JSONL", default=None,
                        help="skip the session; rebuild the report from a JSONL")
    parser.add_argument(
        "--reference-bars",
        default=None,
        help="independent canonical CSV used to verify captured OHLCV bars",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.report_only is not None:
        events_path = Path(args.report_only)
        return run_report(
            events_path,
            _report_path_for_events(events_path),
            reference_bars_path=args.reference_bars,
        )

    hours, minutes = args.end_et.split(":")
    end_minutes_et = int(hours) * 60 + int(minutes)

    credentials = credentials_from_env()
    http = TradovateHttpClient(DEMO_ENVIRONMENT.rest_base_url, UrllibHttpTransport())
    auth = TradovateAuthClient(http, credentials)
    token = auth.request_access_token()
    authed_http = http.with_access_token(token.access_token)

    accounts = authed_http.account_list()
    if not isinstance(accounts, list) or not accounts:
        raise SystemExit("no Tradovate accounts visible with these credentials")
    account = accounts[0]
    logger.info("account: %s (id %s)", account.get("name"), account.get("id"))

    clock = SystemClock()
    session_dir = (
        Path(args.data_dir)
        / classify_timestamp(now_utc_iso(clock)).session_date.isoformat()
    )
    run_risk_probe(authed_http, session_dir / "account_risk.json")

    transport = connect_websocket(DEMO_ENVIRONMENT.md_ws_base_url)
    ws_client = TradovateWebSocketClient(transport)
    ws_client.authorize(token.md_access_token)

    token_state = {"token": token}

    def maintenance() -> None:
        if token_state["token"].should_renew(clock.now()):
            token_state["token"] = auth.renew_access_token(token_state["token"])
            logger.info("REST access token renewed")

    session = build_observe_session(
        ws_client=ws_client, clock=clock,
        account_spec=str(account.get("name")), account_id=int(account["id"]),
        data_dir=Path(args.data_dir), bars_back=args.bars_back,
        end_minutes_et=end_minutes_et, symbol_root=args.symbol_root,
        maintenance=maintenance,
    )
    return run_observe_session(session, reference_bars_path=args.reference_bars)
