from datetime import date

from full_python.data.databento import front_contract_for_session
from full_python.livedata.contract_authority import ContractAuthority


def test_front_contract_delegates_to_validated_roll_logic():
    auth = ContractAuthority(root="NQ")
    # Equivalence to the already-validated function across a spread of
    # sessions spanning multiple quarterly contracts -- ContractAuthority
    # must not reimplement roll math, only wrap it.
    for d in (date(2025, 1, 15), date(2025, 3, 20), date(2025, 6, 2),
              date(2025, 9, 10), date(2025, 12, 1), date(2026, 2, 26),
              date(2026, 6, 20)):
        assert auth.front_contract(d) == front_contract_for_session(d, "NQ", None)


def test_roll_override_is_passed_through():
    override = {"NQH6": date(2026, 3, 10)}
    auth = ContractAuthority(root="NQ", roll_overrides=override)
    for d in (date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11)):
        assert auth.front_contract(d) == front_contract_for_session(d, "NQ", override)
    # the override actually changes at least one session's answer vs no-override
    assert any(
        auth.front_contract(d) != front_contract_for_session(d, "NQ", None)
        for d in (date(2026, 3, 9), date(2026, 3, 10), date(2026, 3, 11))
    )
