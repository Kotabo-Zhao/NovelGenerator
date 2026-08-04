# -*- coding: utf-8 -*-
"""WorldState 三支柱单元测试（纯规则，零 LLM 依赖）。"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.interactive import world_state as ws
from core.interactive.interact_store import new_state


def base_state(**kw):
    st = new_state("t", "《测试》", "都市", "写实", "主角")
    st["known_locations"] = ["茶楼", "街市"]
    st["state"]["location"] = "茶楼"
    st["player_state"] = {"location": "茶楼", "time": "", "with": ["林晚晚"],
                          "holding": [], "situation": ""}
    st["cast_states"] = {
        "林晚晚": {"present": True, "location": "茶楼"},
        "掌柜": {"present": True, "location": "茶楼"},
    }
    st.update(kw)
    return st


# ── ensure_world 迁移 ──
class TestEnsureWorld:
    def test_old_save_migration(self):
        st = base_state()
        w = ws.ensure_world(st)
        assert w["time"]["slot"] == 2          # 默认正午
        assert w["location"] == "茶楼"          # 与 state.location 对齐
        assert "茶楼" in w["locations"]         # 图谱已构建
        assert st["state"]["location"] == "茶楼"

    def test_idempotent(self):
        st = base_state()
        ws.ensure_world(st)
        w1 = ws.ensure_world(st)
        assert w1 is st["world"]               # 不重建
        assert len(st["world"]["locations"]) == len(w1["locations"])


# ── 地点图谱构建 ──
class TestBuildGraph:
    def test_sources_merged(self):
        st = base_state()
        st["cast_states"]["林晚晚"]["location"] = "林府"
        w = ws.ensure_world(st)
        locs = w["locations"]
        assert "茶楼" in locs and "街市" in locs and "林府" in locs

    def test_worldbuilding_split(self):
        st = base_state()
        st["worldbuilding_brief"] = "上海，陆家嘴、前滩；夜晚霓虹"
        w = ws.ensure_world(st)
        names = "|".join(w["locations"])
        assert "上海" in names

    def test_find_home(self):
        st = base_state()
        w = ws.ensure_world(st)
        w["locations"]["家"] = {"desc": "", "connected": ["街市"], "chars": [], "items": []}
        assert ws.find_home_location(st) == "家"

    def test_find_home_excludes_commercial(self):
        st = base_state()
        w = ws.ensure_world(st)
        w["locations"]["林家茶馆"] = {"desc": "", "connected": [], "chars": [], "items": []}
        assert ws.find_home_location(st) == ""   # 茶楼类不算家


# ── 移动目标解析（规则）──
class TestResolveTravel:
    def test_go_home_with_graph(self):
        st = base_state()
        ws.ensure_world(st)
        st["world"]["locations"]["家"] = {"desc": "", "connected": ["街市"], "chars": [], "items": []}
        tgt, ok = ws.resolve_travel_target("我要回家了", st)
        assert ok and tgt == "家"

    def test_go_home_without_graph(self):
        """图谱无家 → 返回"家"走确认流（绝不退回对话）"""
        st = base_state()
        ws.ensure_world(st)
        tgt, ok = ws.resolve_travel_target("我想回家", st)
        assert ok and tgt == "家"

    def test_go_known_place(self):
        st = base_state()
        ws.ensure_world(st)
        st["world"]["locations"]["城西码头"] = {"desc": "", "connected": ["茶楼"], "chars": [], "items": []}
        tgt, ok = ws.resolve_travel_target("去城西码头", st)
        assert ok and tgt == "城西码头"

    def test_go_unknown_place(self):
        st = base_state()
        ws.ensure_world(st)
        tgt, ok = ws.resolve_travel_target("去北山", st)
        assert ok and tgt == "北山"              # 图谱外 → 确认流

    def test_not_travel(self):
        st = base_state()
        ws.ensure_world(st)
        assert ws.resolve_travel_target("你好啊", st) == ("", False)
        assert ws.resolve_travel_target("你吃饭了吗", st) == ("", False)

    def test_upstairs(self):
        """"上楼"无明确目标地点 → 规则不猜测（返回 False，由 LLM target 兜底走确认流）"""
        st = base_state()
        ws.ensure_world(st)
        assert ws.resolve_travel_target("上楼", st) == ("", False)


# ── 时间推进 ──
class TestTime:
    def test_advance_one(self):
        st = base_state()
        w = ws.ensure_world(st)
        changes = ws.advance_time(w, 1)
        assert w["time"]["label"] == "下午"
        assert any("时间" in c for c in changes)

    def test_advance_to_next_day(self):
        st = base_state()
        w = ws.ensure_world(st)
        w["time"] = {"label": "深夜", "slot": 6, "day": 1}
        ws.advance_time(w, 1)
        assert w["time"]["slot"] == 0
        assert w["time"]["label"] == "清晨"
        assert w["time"]["day"] == 2

    def test_time_label(self):
        st = base_state()
        w = ws.ensure_world(st)
        w["time"] = {"label": "夜晚", "slot": 5, "day": 3}
        assert ws.time_label(w) == "第3天·夜晚"


# ── 移动执行器 ──
class TestExecuteTravel:
    def test_travel_updates_triad(self):
        st = base_state()
        ws.ensure_world(st)
        st["world"]["locations"]["家"] = {"desc": "城南小院", "connected": ["街市"], "chars": [], "items": []}
        changes, ok = ws.execute_travel(st, "家")
        assert ok
        assert st["world"]["location"] == "家"
        assert st["state"]["location"] == "家"
        assert st["player_state"]["location"] == "家"
        assert "家" in st["known_locations"]
        assert any("地点" in c for c in changes)
        assert any("时间" in c for c in changes)      # 移动推进时间

    def test_follower_comes_along(self):
        st = base_state()
        ws.ensure_world(st)
        st["world"]["locations"]["家"] = {"desc": "", "connected": [], "chars": [], "items": []}
        ws.execute_travel(st, "家")
        # 林晚晚在 with 列表 → 跟随
        assert st["cast_states"]["林晚晚"]["location"] == "家"
        assert st["cast_states"]["林晚晚"]["present"] is True
        # 掌柜不在 with → 留在原地
        assert st["cast_states"]["掌柜"]["location"] == "茶楼"
        assert st["cast_states"]["掌柜"]["present"] is False

    def test_travel_to_unknown_registers(self):
        st = base_state()
        ws.ensure_world(st)
        changes, ok = ws.execute_travel(st, "北山", register_new=True)
        assert ok
        assert st["world"]["location"] == "北山"
        assert "北山" in st["world"]["locations"]      # 确认后注册

    def test_same_location_rejected(self):
        st = base_state()
        ws.ensure_world(st)
        changes, ok = ws.execute_travel(st, "茶楼")
        assert not ok and changes == []


# ── LLM 提取校验（防幻觉）──
class TestValidateLLM:
    def test_unknown_location_rejected(self):
        st = base_state()
        ws.ensure_world(st)
        # LLM 幻觉：跑到图谱外地点
        ps = ws.validate_llm_state(st, {"location": "月球基地"})
        assert st["world"]["location"] == "茶楼"       # 不采纳
        assert ps["location"] == "茶楼"

    def test_known_location_accepted(self):
        st = base_state()
        ws.ensure_world(st)
        st["world"]["locations"]["林府"] = {"desc": "", "connected": ["茶楼"], "chars": [], "items": []}
        ps = ws.validate_llm_state(st, {"location": "林府", "situation": "做客"})
        assert st["world"]["location"] == "林府"       # 图谱内 → 采纳
        assert ps["situation"] == "做客"

    def test_with_holding_accepted(self):
        st = base_state()
        ws.ensure_world(st)
        ps = ws.validate_llm_state(st, {"with": ["林晚晚"], "holding": ["玉佩"]})
        assert ps["with"] == ["林晚晚"]
        assert ps["holding"] == ["玉佩"]


# ── 三支柱简报 ──
class TestWorldBrief:
    def test_brief_content(self):
        st = base_state()
        ws.ensure_world(st)
        st["world"]["locations"]["茶楼"]["desc"] = "临街二层木楼"
        brief = ws.world_brief(st)
        assert "茶楼" in brief
        assert "临街二层木楼" in brief
        assert "林晚晚" in brief                     # 在场人物


# ── v3.6 P1: 场景时间推进 / LLM time 同步 / 图谱显式在场 ──
class TestP1:
    def test_llm_time_not_adopted(self):
        """LLM 提取的 time 不被采纳——同步规则档位（防 LLM 自由编时间）"""
        st = base_state()
        ws.ensure_world(st)
        st["world"]["time"] = {"label": "傍晚", "slot": 4, "day": 1}
        ws.validate_llm_state(st, {"time": "三百年后", "location": "茶楼"})
        assert st["player_state"]["time"] == "傍晚"   # 规则时间

    def test_scene_time_advance_every_2(self, monkeypatch):
        """场景推进每 2 场景 +1 档（由 story_director 调 advance_time，这里测规则）"""
        st = base_state()
        w = ws.ensure_world(st)
        ws.advance_time(w, 1)
        assert w["time"]["label"] == "下午"
        ws.advance_time(w, 1)
        assert w["time"]["label"] == "傍晚"

    def test_graph_presence_source(self):
        """图谱 locations[loc].chars 是显式在场来源（compute_present 会读取）"""
        from core.interactive.story_director import compute_present
        st = base_state()
        ws.ensure_world(st)
        st["world"]["locations"]["茶楼"]["chars"] = ["林晚晚", "掌柜", "神秘客"]
        present, away = compute_present(st)
        assert "神秘客" in present                  # 图谱绑定 → 在场

    def test_validate_time_sync_after_advance(self):
        st = base_state()
        ws.ensure_world(st)
        ws.advance_time(st["world"], 1)
        ws.validate_llm_state(st, {"with": [], "holding": [], "location": "茶楼"})
        assert st["player_state"]["time"] == "下午"   # 与 world 档位同步


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
