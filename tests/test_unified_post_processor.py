"""Regression tests for S3P UnifiedPostProcessor & streamlined main writing pipeline."""

import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from core.unified_post_processor import UnifiedPostProcessor


class _FakeResponse:
    def __init__(self, payload: dict):
        class _Msg:
            content = json.dumps(payload, ensure_ascii=False)

        class _Choice:
            message = _Msg()

        self.choices = [_Choice()]


class _FakeResilientClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return _FakeResponse(self.payload)


class _FakeMemory:
    def __init__(self):
        self.bridges = {}
        self.global_state = {"characters": {"沈砚": {"location": "旧码头"}}, "summaries": {}, "foreshadowing": []}

    def read(self, memory_type, novel_id, skip_cache=False):
        return dict(self.global_state) if memory_type == "global_state" else {}

    def save_bridge(self, novel_id, chapter_num, bridge):
        self.bridges[(novel_id, chapter_num)] = bridge

    def update(self, memory_type, novel_id, fn):
        if memory_type == "global_state":
            self.global_state = fn(self.global_state)


class UnifiedPostProcessorTests(unittest.TestCase):
    def test_single_llm_call_extracts_all_five_artifacts_and_persists(self):
        fake_client = _FakeResilientClient({
            "bridge": {
                "summary": "沈砚在旧仓库拿到残页，断电瞬间发现门外湿鞋印。",
                "ending_scene": "惨白手电光照在半枚湿鞋印上。",
                "next_beat": "查明闯入者身份并转移残页",
                "unresolved_tension": "谁在暗中撬锁？",
            },
            "summary_entry": {
                "summary": "沈砚发现07号航运单残页并与周明霁交锋。",
                "key_events": ["发现07号残页", "三号仓库突发断电"],
                "characters": ["沈砚", "周明霁"],
            },
            "character_updates": {
                "沈砚": {"location": "三号仓库", "status": "高度戒备", "items": ["07号残页"]},
            },
            "foreshadow_updates": [
                {"title": "机油味湿鞋印", "status": "planted", "detail": "断电时出现在门口"},
            ],
            "consistency_violations": [
                {"severity": "P1", "quote": "他抬腕看表", "description": "前文未交代戴表", "fix_hint": "改为看墙上挂钟"},
            ],
        })
        mem = _FakeMemory()
        proc = UnifiedPostProcessor(fake_client, "test-model")
        res = proc.process_and_persist(
            mem, "novel_1", 1, "雨夜三号仓库的正文内容。" * 20, {"title": "雨夜来客", "summary": "测试概要"}
        )

        # Strictly 1 LLM call for all 5 tasks!
        self.assertEqual(fake_client.calls, 1)
        self.assertFalse(res["fallback"])
        self.assertIn(("novel_1", 1), mem.bridges)
        self.assertIn("1", mem.global_state["summaries"])
        self.assertEqual(mem.global_state["characters"]["沈砚"]["location"], "三号仓库")
        self.assertEqual(len(mem.global_state["foreshadowing"]), 1)
        self.assertEqual(len(res["consistency_violations"]), 1)

    def test_fallback_mode_never_crashes_when_client_unavailable(self):
        mem = _FakeMemory()
        proc = UnifiedPostProcessor(None, "test-model")
        res = proc.process_and_persist(
            mem, "novel_1", 2, "断电三分钟后的紧张对峙。" * 15, {"title": "断电三分钟", "summary": "断电脱身"}
        )
        self.assertTrue(res["fallback"])
        self.assertIn(("novel_1", 2), mem.bridges)
        self.assertIn("2", mem.global_state["summaries"])


if __name__ == "__main__":
    unittest.main()
