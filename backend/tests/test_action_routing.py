# -*- coding: utf-8 -*-
"""行动路由回归测试（v3.6）——LLM 判定被 mock，验证规则层路由：
- intent 四分类正确落到规则处理
- travel 目标解析 + 图谱校验 + 确认流标记
- apply_action 对 travel 的确定性执行（三支柱同步落盘）
"""
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.interactive.action_engine import ActionEngine
from core.interactive.interact_store import InteractStore, new_state
from core.interactive import world_state as ws


class _FakeStore:
    """内存 store（免磁盘，测 detect 层足够）"""

    def __init__(self, state):
        self._state = state

    def load_state(self, novel_id):
        return self._state

    def save_state(self, novel_id, state):
        self._state = state


def fake_llm(system, user, temperature=0.2, max_tokens=400):
    """模拟 LLM 判定：按输入关键词返回意图（规则映射，模拟真实 LLM 的合理行为）。"""
    m = re.search(r"读者输入: ([^\n]+)", user or "")
    text = m.group(1) if m else ""
    if any(k in text for k in ("存档", "读档", "设置", "音量")):
        return '{"intent": "meta", "type": "other", "summary": "系统操作", "target": "", "end_chat": false, "reason": "系统指令"}'
    if any(k in text for k in ("回家", "回府", "回房", "上楼", "下楼", "进城", "出城", "去", "到", "前往", "离开")):
        return '{"intent": "travel", "type": "travel", "summary": "移动", "target": "家", "end_chat": true, "reason": "移动意图"}'
    if any(k in text for k in ("上车", "推门", "答应", "拔剑", "（", "）")):
        return '{"intent": "act", "type": "interact", "summary": "执行动作", "target": "", "end_chat": false, "reason": "动作"}'
    return '{"intent": "talk", "type": "other", "summary": "", "target": "", "end_chat": false, "reason": "对话"}'


@pytest.fixture
def state():
    st = new_state("t", "《测试》", "都市", "写实", "主角")
    st["known_locations"] = ["茶楼", "街市", "家"]
    st["state"]["location"] = "茶楼"
    st["player_state"] = {"location": "茶楼", "time": "正午", "with": ["林晚晚"],
                          "holding": [], "situation": ""}
    st["cast_states"] = {
        "林晚晚": {"present": True, "location": "茶楼"},
        "掌柜": {"present": True, "location": "茶楼"},
    }
    st["worldbuilding_brief"] = "都市现代，普通人的世界"
    ws.ensure_world(st)
    # 图谱补家节点
    st["world"]["locations"]["家"] = {"desc": "城南小院", "connected": ["街市"], "chars": [], "items": []}
    return st


@pytest.fixture
def engine(state):
    e = ActionEngine(None, "mock", _FakeStore(state))
    e._llm = fake_llm
    return e


# ── 意图路由（20 条回归输入）──
ROUTE_CASES = [
    # (输入, 预期 intent 或 None)
    ("你好啊", None),               # talk
    ("你吃饭了吗", None),           # talk
    ("今天天气不错", None),         # talk
    ("我饿了", None),               # talk（陈述）
    ("你去哪了", None),             # talk
    ("（推门）", "act"),            # 括号动作
    ("上车", "act"),                # 裸行动词
    ("好，上车", "act"),            # 高置信行动词
    ("我答应你", "act"),            # 承诺
    ("我要回家了", "travel"),       # 核心场景
    ("我想回家", "travel"),
    ("回家吃饭", "travel"),         # 尾巴词
    ("回府", "travel"),
    ("去城西码头", "travel"),
    ("进城", "travel"),             # LLM target 兜底
    ("我要去北山", "travel"),       # 图谱外
    ("存档", "meta"),               # 系统指令
    ("读档", "meta"),
    ("天黑了", None),               # 环境陈述
    ("你是个好人", None),           # talk
]


class TestIntentRouting:
    @pytest.mark.parametrize("text,expect", ROUTE_CASES, ids=[c[0] for c in ROUTE_CASES])
    def test_route(self, state, engine, text, expect):
        action = engine.detect_action(text, state)
        if expect is None:
            assert action is None, f"{text} 应判 talk（得到 {action}）"
        else:
            assert action is not None, f"{text} 应判 {expect}"
            assert action["intent"] == expect, f"{text} intent={action['intent']}"


class TestTravelResolution:
    def test_go_home_resolves_graph(self, state, engine):
        action = engine.detect_action("我要回家了", state)
        assert action["target"] == "家"
        assert not action.get("need_confirm")     # 图谱有家 → 直接执行

    def test_unknown_place_requires_confirm(self, state, engine):
        action = engine.detect_action("我要去北山", state)
        assert action["target"] == "北山"
        assert action.get("need_confirm") is True  # 图谱外 → 确认流

    def test_go_home_no_graph_requires_confirm(self, state, engine):
        del state["world"]["locations"]["家"]
        action = engine.detect_action("我要回家了", state)
        assert action is not None
        assert action["target"] == "家"
        assert action.get("need_confirm") is True  # 图谱无家 → 确认后注册

    def test_llm_target_fallback(self, state, engine):
        action = engine.detect_action("上楼", state)   # 规则解析不出目标
        assert action is not None
        assert action["target"] == "家"                # 采用 LLM target 候选
        assert not action.get("need_confirm")          # 图谱有"家" → 直接执行


class TestApplyTravel:
    def test_travel_persists_triad(self, tmp_path):
        """apply_action 走确定性移动执行器：三支柱同步 + 落盘。"""
        store = InteractStore(str(tmp_path))
        st = new_state("t", "《测试》", "都市", "写实", "主角")
        st["known_locations"] = ["茶楼", "街市", "家"]
        st["state"]["location"] = "茶楼"
        st["player_state"] = {"location": "茶楼", "with": ["林晚晚"], "holding": [], "situation": ""}
        st["cast_states"] = {"林晚晚": {"present": True, "location": "茶楼"},
                             "掌柜": {"present": True, "location": "茶楼"}}
        ws.ensure_world(st)
        st["world"]["locations"]["家"] = {"desc": "城南小院", "connected": ["街市"], "chars": [], "items": []}
        store.save_state("t", st)

        e = ActionEngine(None, "mock", store)
        action = {"intent": "travel", "type": "travel", "summary": "回家",
                  "target": "家", "end_chat": True, "confirmed": True}
        applied = e.apply_action("t", action)
        assert any("地点" in c for c in applied["changed"])
        # 落盘后验证三支柱
        saved = store.load_state("t")
        assert saved["world"]["location"] == "家"
        assert saved["state"]["location"] == "家"
        assert saved["player_state"]["location"] == "家"
        assert saved["world"]["time"]["label"] in ws.TIME_SLOTS
        assert saved["cast_states"]["林晚晚"]["location"] == "家"   # 跟随
        assert saved["cast_states"]["掌柜"]["location"] == "茶楼"   # 留守
        assert saved["cast_states"]["掌柜"]["present"] is False

    def test_travel_unknown_registers_after_confirm(self, tmp_path):
        store = InteractStore(str(tmp_path))
        st = new_state("t", "《测试》", "都市", "写实", "主角")
        st["state"]["location"] = "茶楼"
        st["player_state"] = {"location": "茶楼", "with": [], "holding": [], "situation": ""}
        ws.ensure_world(st)
        store.save_state("t", st)
        e = ActionEngine(None, "mock", store)
        applied = e.apply_action("t", {"intent": "travel", "type": "travel",
                                       "summary": "去北山", "target": "北山",
                                       "end_chat": True, "confirmed": True})
        assert any("地点" in c for c in applied["changed"])
        saved = store.load_state("t")
        assert saved["world"]["location"] == "北山"
        assert "北山" in saved["world"]["locations"]      # 确认后注册

    def test_travel_fulfills_promise(self, tmp_path):
        """P3 集成：到达约定地点 → 待兑现约定自动兑现 + 变化提示"""
        store = InteractStore(str(tmp_path))
        st = new_state("t", "《测试》", "都市", "写实", "主角")
        st["state"]["location"] = "茶楼"
        st["player_state"] = {"location": "茶楼", "with": [], "holding": [], "situation": ""}
        ws.ensure_world(st)
        st["world"]["locations"]["码头"] = {"desc": "货运码头", "connected": ["茶楼"],
                                            "chars": [], "items": []}
        st["pending_promises"] = [{
            "who": "林晚晚", "what": "午时在码头见面", "when_raw": "午时",
            "location": "码头", "scene_num": 2, "due_scene": 5, "status": "pending",
        }]
        store.save_state("t", st)
        e = ActionEngine(None, "mock", store)
        applied = e.apply_action("t", {"intent": "travel", "type": "travel",
                                       "summary": "去码头", "target": "码头",
                                       "end_chat": True, "confirmed": True})
        assert any("约定兑现" in c for c in applied["changed"])
        saved = store.load_state("t")
        assert saved["pending_promises"][0]["status"] == "fulfilled"
        assert saved["world"]["location"] == "码头"


class TestMetaIntent:
    def test_meta_no_state_change(self, state, engine):
        action = engine.detect_action("存档", state)
        assert action is not None and action["intent"] == "meta"
        assert action["type"] == "meta"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
