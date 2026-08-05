#!/usr/bin/env python3
"""attr_system 单元测试 — 属性数值系统（v3.7 分层混合架构·状态层）"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.interactive.attr_system import (
    STAT_KEYS, clamp,
    infer_stats_from_text, infer_stats_from_profile,
    render_stats_card, ensure_stats,
)


def test_clamp():
    assert clamp(0) == 1
    assert clamp(200) == 95
    assert clamp(50) == 50
    assert clamp(88.4) == 88
    print("✅ test_clamp")


def test_combat_character():
    """战斗型角色：力量/敏捷应显著高于中性 50"""
    s = infer_stats_from_text("剑客出身，剑气凌厉，身法矫健，面如冠玉", "金丹")
    assert s["力量"] >= 80, f"力量应高: {s}"
    assert s["敏捷"] >= 70, f"敏捷应高: {s}"
    assert s["智力"] >= 40, f"智力不偏低: {s}"
    print("✅ test_combat_character", s)


def test_scholar_character():
    """谋士型角色：智力高、力量低"""
    s = infer_stats_from_text("算无遗策的军师，文弱书生，擅长谋略与医术", "")
    assert s["智力"] >= 80, f"智力应高: {s}"
    assert s["力量"] <= 40, f"力量应低: {s}"
    print("✅ test_scholar_character", s)


def test_charm_character():
    """魅力型角色：魅力高"""
    s = infer_stats_from_text("倾国倾城的花魁，交际手腕高超，体态纤细", "")
    assert s["魅力"] >= 80, f"魅力应高: {s}"
    print("✅ test_charm_character", s)


def test_negative_signal():
    """负面信号：病弱降低体魄"""
    s = infer_stats_from_text("体弱多病的书生，聪颖过人", "")
    assert s["体魄"] <= 40, f"体魄应低: {s}"
    assert s["智力"] >= 80, f"智力应高: {s}"
    print("✅ test_negative_signal", s)


def test_realm_boost():
    """修为加成：境界越高力量越强，且封顶 95"""
    low = infer_stats_from_text("剑客", "炼气")
    high = infer_stats_from_text("剑客", "大乘")
    assert high["力量"] >= low["力量"], f"大乘应≥炼气: {low} vs {high}"
    assert high["力量"] <= 95, f"封顶95: {high}"
    assert all(1 <= v <= 95 for v in high.values()), "全部在 [1,95]"
    print("✅ test_realm_boost", low["力量"], high["力量"])


def test_ensure_stats_idempotent():
    """ensure_stats 幂等且写回 profile"""
    prof = {"identity": "青楼花魁", "personality": "温婉善解人意",
            "backstory": "出身江南，琴棋书画精通", "role": "关键NPC"}
    s1 = ensure_stats(prof)
    assert prof.get("stats") is not None
    assert ensure_stats(prof) is prof["stats"]  # 二次调用返回同一对象（不重算）
    assert all(k in s1 for k in STAT_KEYS)
    print("✅ test_ensure_stats_idempotent", s1)


def test_render_card():
    """属性卡渲染格式"""
    s = infer_stats_from_text("剑客出身，剑气凌厉", "")
    card = render_stats_card(s, "剑客")
    assert "属性卡" in card and "力量" in card and "剑客" in card
    assert render_stats_card({}) == ""
    assert render_stats_card(None) == ""
    print("✅ test_render_card")


def test_infer_from_profile():
    """profile 推断（多字段拼接）"""
    prof = {"identity": "隐世医仙", "personality": "沉稳淡然",
            "backstory": "精通炼丹之术，救人无数", "cultivation": "化神"}
    s = infer_stats_from_profile(prof)
    assert s["智力"] >= 70, f"医者智力应高: {s}"
    assert s["力量"] >= 60, f"化神境界力量应高: {s}"
    print("✅ test_infer_from_profile", s)


def test_dialogue_injection():
    """对话引擎注入验证（真实模块）"""
    from core.interactive.dialogue_engine import DialogueEngine
    state = {
        "style_brief": "古风",
        "state": {"location": "山门前", "relations": {"师兄": 60}, "objective": "查明真相"},
        "player_state": {"location": "山门前"},
        "world": {"time": {"label": "正午", "day": 1}},
        "player_char": {"name": "沈青", "identity": "剑客",
                        "stats": {"力量": 80, "敏捷": 75, "智力": 60, "魅力": 55, "体魄": 70}},
        "casts": {"师兄": {"profile": {
            "identity": "剑客师兄",
            "stats": {"力量": 88, "敏捷": 82, "智力": 58, "魅力": 66, "体魄": 75},
        }}},
        "facts": [], "agenda": {"goal": "劝师弟"}, "novel_id": "test",
    }
    eng = DialogueEngine(client=None, model="test", store=None, engine=None)
    prompt = eng._build_chat_prompt(state, "师兄", [{"role": "user", "content": "我想下山"}])
    assert "力量 88" in prompt, "目标角色属性卡注入"
    assert "力量 80" in prompt, "玩家属性卡注入"

    # 老存档（无 stats）→ 兜底推断仍有锚点
    state["player_char"].pop("stats")
    state["casts"]["师兄"]["profile"].pop("stats")
    prompt2 = eng._build_chat_prompt(state, "师兄", [{"role": "user", "content": "我想下山"}])
    assert "属性卡" in prompt2, "老存档兜底推断"
    print("✅ test_dialogue_injection")


if __name__ == "__main__":
    test_clamp()
    test_combat_character()
    test_scholar_character()
    test_charm_character()
    test_negative_signal()
    test_realm_boost()
    test_ensure_stats_idempotent()
    test_render_card()
    test_infer_from_profile()
    test_dialogue_injection()
    print("\n🎉 attr_system 全部测试通过")
