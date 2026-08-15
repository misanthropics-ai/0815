from contracts.check_contract import check_fixtures


def test_committed_contract_and_fixtures_are_compatible() -> None:
    assert check_fixtures() == 0
