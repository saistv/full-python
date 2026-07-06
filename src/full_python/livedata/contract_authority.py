"""Which specific futures contract is tradeable for a session.

Thin wrapper over the validated data.databento roll authority (expiry-3
calendar days with an observed-override table). Rolls occur only at
session boundaries; the strategy is always flat overnight (backstop
flatten), so this never has to reconcile an open position across a roll.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from full_python.data.databento import front_contract_for_session


class ContractAuthority:
    def __init__(
        self, root: str = "NQ", roll_overrides: Optional[dict[str, date]] = None
    ) -> None:
        self._root = root
        self._roll_overrides = roll_overrides

    def front_contract(self, session_date: date) -> str:
        return front_contract_for_session(session_date, self._root, self._roll_overrides)
