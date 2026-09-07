import requirementseeker_agent as agent


def test_m1_rules_are_public_and_versioned() -> None:
    assert agent.__version__ == "0.2.0"
    assert agent.RULES_VERSION == "m1.0"
    assert callable(agent.evaluate_consensus)
