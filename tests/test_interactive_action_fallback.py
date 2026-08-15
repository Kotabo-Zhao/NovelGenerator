from backend.core.interactive.action_engine import ActionEngine


def _engine_without_llm():
    engine = ActionEngine(client=None, model="offline-test", store=None)
    engine._llm = lambda *args, **kwargs: None
    return engine


def _state(worldbuilding: str):
    return {
        "state": {"location": "青云山", "objective": "调查宗门秘辛"},
        "worldbuilding_brief": worldbuilding,
        "casts": {},
        "agenda": {},
    }


def test_fantasy_capabilities_are_not_blocked_when_llm_is_offline():
    engine = _engine_without_llm()
    state = _state("仙侠修真世界，弟子可以御剑飞行，也能施展瞬移遁术。")

    for user_input in ("我御剑飞上天空", "我施展遁术瞬移到后山"):
        action = engine.detect_action(user_input, state)
        assert action is not None
        assert action["intent"] == "act"
        assert not action.get("blocked", False)


def test_extraordinary_action_is_blocked_in_realistic_world_when_offline():
    action = _engine_without_llm().detect_action(
        "我直接飞上天",
        _state("现代都市现实世界，普通人没有任何超能力。"),
    )

    assert action is not None
    assert action["blocked"] is True


def test_first_person_long_action_survives_offline_fallback():
    action = _engine_without_llm().detect_action(
        "我虽然心里不舍，但还是把玉佩递了回去",
        _state("现代都市现实世界。"),
    )

    assert action is not None
    assert action["intent"] == "act"
    assert action["summary"] == "我虽然心里不舍，但还是把玉佩递了回去"


def test_first_person_opinion_remains_dialogue_when_offline():
    action = _engine_without_llm().detect_action(
        "我不认同你的说法",
        _state("现代都市现实世界。"),
    )

    assert action is None
