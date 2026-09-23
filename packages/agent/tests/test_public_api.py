import requirementseeker_agent as agent


def test_m1_rules_are_public_and_versioned() -> None:
    assert agent.__version__ == "0.3.0"
    assert agent.RULES_VERSION == "m1.0"
    assert callable(agent.evaluate_consensus)


def test_m2_pipeline_is_public_and_versioned() -> None:
    assert callable(agent.analyze_m2)
    assert agent.SAMPLING_POLICY_VERSION == "m2.0"
    assert agent.SamplingManifest.model_fields["sampling_schema_version"] is not None
    assert agent.SamplingPlan is not None
    assert agent.ScenarioModelGateway is not None
    assert agent.M2AnalysisResult is not None
    assert agent.__version__ == "0.3.0"
