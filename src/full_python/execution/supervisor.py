"""Account-level hard limits, independent of strategy internals.

Defense-in-depth: the strategy's own DLL is edge logic (part of the
validated config); this supervisor is an account guard that must hold
even if strategy state is corrupted. It consults only broker-reported
position, closed trades, and the current bar mark.

`daily_loss_stop` IS PER-INSTRUMENT AND IN DOLLARS -- set it explicitly
per account, and always name the instrument when quoting a cap:

  * NQ = $20/point: one 30pt stop = $600/contract, AM4 worst single
    loss ~= $2,600, and the strategy's own validated DLL is $1,000.
    A live 1-NQ cap must sit ABOVE that $1,000 DLL (reference range
    ~$1,500-2,000) so the validated DLL stays the primary control and
    this supervisor only catches runaway/corruption. A cap below one
    stop ($600) flattens on the first adverse mark -- untradeable.
  * MNQ = $2/point (1/10th NQ): Gate 7's pilot cap is $150/day
    (~2.5 micro stops). $150 IS AN MNQ NUMBER -- never set it on an NQ
    account, where it is ~10x too tight.

Test fixtures pass daily_loss_stop=150.0 only to trip a synthetic loss;
that is not a deployment default. The real cap is operational config
set when an account is wired.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from full_python.execution.broker_protocol import BrokerPosition
from full_python.models import MarketBar, Trade


@dataclass(frozen=True)
class RiskSupervisorConfig:
    point_value: float
    daily_loss_stop: Optional[float] = None
    max_position_contracts: Optional[int] = None
    kill_switch_path: Optional[Path] = None


class RiskSupervisor:
    def __init__(self, config: RiskSupervisorConfig) -> None:
        self.config = config
        self._breached_reason: Optional[str] = None
        self._breached_session: Optional[str] = None

    def entries_allowed(self) -> bool:
        return self._breached_reason is None

    def check_mark(
        self,
        *,
        session_date: str,
        bar: MarketBar,
        position: Optional[BrokerPosition],
        trades: list[Trade],
    ) -> Optional[str]:
        if self._breached_session is not None and self._breached_session != session_date:
            self._breached_reason = None
            self._breached_session = None
        if self._breached_reason is not None:
            return self._breached_reason

        reason = self._evaluate(session_date, bar, position, trades)
        if reason is not None:
            self._breached_reason = reason
            self._breached_session = session_date
        return reason

    def _evaluate(
        self,
        session_date: str,
        bar: MarketBar,
        position: Optional[BrokerPosition],
        trades: list[Trade],
    ) -> Optional[str]:
        cfg = self.config
        if cfg.kill_switch_path is not None and cfg.kill_switch_path.exists():
            return "supervisor_kill_switch"
        if (
            cfg.max_position_contracts is not None
            and position is not None
            and position.quantity > cfg.max_position_contracts
        ):
            return "supervisor_max_position"
        if cfg.daily_loss_stop is not None:
            realized = sum(t.net_pnl for t in trades if t.session_date == session_date)
            unrealized = 0.0
            if position is not None:
                direction = 1 if position.side == "long" else -1
                unrealized = (
                    (bar.close - position.entry_price)
                    * direction
                    * cfg.point_value
                    * position.quantity
                )
            if realized + unrealized <= -cfg.daily_loss_stop:
                return "supervisor_daily_loss"
        return None
