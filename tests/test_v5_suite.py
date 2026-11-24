"""End-to-end & unit test suite for NovelGenerator v5."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import create_app
from backend.core.context_compiler import ContextBlock, compile_budgeted_blocks
from backend.core.craft_analyzer import analyze_prose_craft
from backend.core.quality_gate import (
    ChapterQualityError,
    dedup_continuation,
    ensure_complete_ending,
    validate_chapter_text,
)


def _parse_sse_events(raw_text: str) -> list[dict]:
    events = []
    for line in raw_text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    return events


class NovelGenV5Tests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = create_app(data_dir=self._tmp.name)
        self.client = TestClient(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    def test_health_frontend_and_masked_llm_settings(self):
        r = self.client.get("/api/system/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["version"], "5.0.0")
        self.assertEqual(r.json()["storage"], "sqlite-wal-fts5")

        # Save a secret key and verify it is never returned in plaintext
        upd = self.client.put(
            "/api/system/llm",
            json={"mode": "demo", "api_key": "sk-super-secret-value-123456"},
        )
        self.assertEqual(upd.status_code, 200)
        self.assertTrue(upd.json()["has_api_key"])
        self.assertNotIn("super-secret", json.dumps(upd.json()))

        # Verify frontend entry & ES modules are served cleanly
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/styles/tokens.css").status_code, 200)
        self.assertEqual(self.client.get("/js/app.js").status_code, 200)

    def test_full_four_stage_writing_pipeline_and_state_continuity(self):
        # 1. Create novel with automatic Stage-1 Architect blueprint
        created = self.client.post(
            "/api/novels",
            json={
                "title": "雨夜三号仓库",
                "genre": "都市悬疑",
                "style": "克制镜头",
                "premise": "档案修复师在旧码头发现沉船账册残页。",
                "auto_plan": True,
            },
        )
        self.assertEqual(created.status_code, 200)
        payload = created.json()
        novel_id = payload["novel"]["id"]
        self.assertGreaterEqual(payload["planned_count"], 3)
        self.assertGreaterEqual(len(payload["ledger"]["entities"]), 2)

        # 2. Stream-draft Chapter 1 (Stage 2 Context -> Stage 3 Draft -> Stage 4 Craft & Atomic State Commit)
        stream_resp = self.client.post(
            f"/api/novels/{novel_id}/chapters/1/draft-stream",
            json={"revision_feedback": "", "auto_commit": True},
        )
        self.assertEqual(stream_resp.status_code, 200)
        events = _parse_sse_events(stream_resp.text)
        types = [e["type"] for e in events]
        self.assertIn("context_ready", types)
        self.assertIn("chunk", types)
        self.assertIn("done", types)
        done_evt = [e for e in events if e["type"] == "done"][-1]
        self.assertGreater(len(done_evt["content"]), 200)
        self.assertIsNotNone(done_evt["state_delta"])

        # 3. Verify SQLite Ledger updated atomically + FTS5 search finds committed prose/entities
        ledger = self.client.get(f"/api/novels/{novel_id}/ledger").json()
        self.assertGreaterEqual(len(ledger["canon_events"]), 2)
        search_res = self.client.get(f"/api/novels/{novel_id}/ledger/search", params={"q": "火漆"}).json()
        self.assertGreater(len(search_res["hits"]), 0)

        # 4. Verify Chapter 2 context compiler automatically includes Chapter 1 bridge & canon events
        novel = self.app.state.db.get_novel(novel_id)
        ch2 = self.app.state.db.get_chapter(novel_id, 2)
        from backend.core.models import ChapterContract
        compiled = self.app.state.engine.compiler.compile_for_chapter(
            novel, ChapterContract.from_dict(ch2["contract"])
        )
        self.assertIn("上章承接与结尾锚点", compiled["compiled_context"])
        self.assertIn("已发生正史事件", compiled["compiled_context"])

        # 5. Surgical rewrite SSE
        surg = self.client.post(
            f"/api/novels/{novel_id}/chapters/1/surgical-stream",
            json={"selected_text": "雨点砸在三号仓库的铁皮顶上。", "instruction": "强化紧张感"},
        )
        self.assertEqual(surg.status_code, 200)
        surg_events = _parse_sse_events(surg.text)
        self.assertTrue(any(e["type"] == "done" and e.get("rewritten") for e in surg_events))

        # 6. Manual edit + version restore
        edit_resp = self.client.put(
            f"/api/novels/{novel_id}/chapters/1",
            json={"content": "这是手工精修后的第二版正文内容，镜头更加克制。", "note": "手工版"},
        )
        self.assertEqual(edit_resp.status_code, 200)
        versions = edit_resp.json()["versions"]
        self.assertGreaterEqual(len(versions), 2)
        first_vid = versions[-1]["id"]
        restored = self.client.post(f"/api/novels/{novel_id}/chapters/1/versions/{first_vid}/restore")
        self.assertEqual(restored.status_code, 200)
        self.assertIn("火漆", restored.json()["content"])

        # 7. Export TXT, Markdown & JSON ledger
        for fmt in ("txt", "md", "json"):
            exp = self.client.get(f"/api/novels/{novel_id}/export", params={"fmt": fmt})
            self.assertEqual(exp.status_code, 200)
            self.assertGreater(len(exp.content), 100)

    def test_quality_gate_context_budget_and_craft_analyzer(self):
        # Context budget preserves required floor
        rendered, trimmed = compile_budgeted_blocks(
            [
                ContextBlock("大设定", "核心世界观。" * 100, priority=10, required=True),
                ContextBlock("本章契约", "必发事件：雨夜夺回账本。", priority=9, required=True),
                ContextBlock("闲笔", "次要说明。" * 50, priority=1, required=False),
            ],
            max_chars=320,
        )
        self.assertIn("雨夜夺回账本", rendered)
        self.assertIn("闲笔", trimmed)

        # Quality gate & dedup
        self.assertEqual(dedup_continuation("他握紧了伞柄。", "\n他握紧了伞柄。转身离去。"), "转身离去。")
        self.assertTrue(ensure_complete_ending("雨落在站台。" * 20 + "未完——").endswith("。"))
        with self.assertRaises(ChapterQualityError):
            validate_chapter_text("正文" * 80 + "「未闭合的引号。", target_words=400)

        # Craft analyzer detects AI cliches & yields revision prompt
        ai_text = "他感到心中充满愤怒，嘴角勾起一抹冷笑，与此同时这不仅仅是一次考验。" * 6
        craft = analyze_prose_craft(ai_text)
        self.assertGreater(craft["ai_score"], 35)
        self.assertTrue(craft["suggested_revision_prompt"])


if __name__ == "__main__":
    unittest.main()
