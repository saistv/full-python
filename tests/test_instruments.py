import pytest

from full_python.instruments import instrument_for_point_value, instrument_spec
from full_python.tradovate.config import DEMO_ENVIRONMENT, TradovateAdapterConfig
from full_python.tradovate.errors import TradovateConfigError


def test_nq_and_mnq_specs_are_explicit_and_distinct() -> None:
    assert instrument_spec("NQ").dollar_point_value == 20.0
    assert instrument_spec("MNQ").dollar_point_value == 2.0
    assert instrument_for_point_value(20.0).root == "NQ"
    assert instrument_for_point_value(2.0).root == "MNQ"


def test_unsupported_or_ambiguous_point_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="identify exactly one"):
        instrument_for_point_value(5.0)


def test_tradovate_config_rejects_cross_instrument_point_value() -> None:
    with pytest.raises(TradovateConfigError, match="MNQ requires"):
        TradovateAdapterConfig(
            environment=DEMO_ENVIRONMENT,
            account_spec="DEMO",
            account_id=1,
            root_symbol="MNQ",
            dollar_point_value=20.0,
        )
