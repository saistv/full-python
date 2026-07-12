from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentSpec:
    root: str
    dollar_point_value: float
    tick_size: float
    commission_per_contract_round_trip: float


INSTRUMENTS = {
    "NQ": InstrumentSpec(
        root="NQ", dollar_point_value=20.0, tick_size=0.25,
        commission_per_contract_round_trip=10.0,
    ),
    "MNQ": InstrumentSpec(
        root="MNQ", dollar_point_value=2.0, tick_size=0.25,
        commission_per_contract_round_trip=1.0,
    ),
}


def instrument_spec(root: str) -> InstrumentSpec:
    normalized = root.upper()
    try:
        return INSTRUMENTS[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported execution instrument: {root!r}") from exc


def instrument_for_point_value(point_value: float) -> InstrumentSpec:
    matches = [
        spec for spec in INSTRUMENTS.values()
        if spec.dollar_point_value == point_value
    ]
    if len(matches) != 1:
        raise ValueError(
            f"point_value {point_value!r} does not identify exactly one supported instrument"
        )
    return matches[0]
