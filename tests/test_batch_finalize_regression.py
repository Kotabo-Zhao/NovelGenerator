import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestBatchFinalizeAndLoadingRegression(unittest.TestCase):
    def test_standard_stream_calls_finalize_chapter_candidate(self):
        src = (ROOT / "backend/core/mixins/generation.py").read_text(encoding="utf-8")
        # Extract generate_chapter_stream body
        start = src.index("async def generate_chapter_stream(")
        body = src[start:]
        finalize_pos = body.find("self._finalize_chapter_candidate(")
        done_pos = body.find('yield {"type": "done"')
        self.assertNotEqual(finalize_pos, -1, "generate_chapter_stream must call _finalize_chapter_candidate")
        self.assertNotEqual(done_pos, -1, "generate_chapter_stream must yield done")
        self.assertLess(
            finalize_pos,
            done_pos,
            "_finalize_chapter_candidate must execute before yielding done",
        )
        self.assertIn('"type": "needs_revision"', body[finalize_pos:done_pos])

    def test_frontend_select_chapter_falls_back_to_draft(self):
        src = (ROOT / "web/assets/workshop.js").read_text(encoding="utf-8")
        m = re.search(r"function selectChapter\([^\)]*\)\s*\{[\s\S]*?\n    \}", src)
        self.assertIsNotNone(m, "selectChapter function not found")
        fn = m.group(0)
        self.assertIn("d.content||d.draft", fn.replace(" ", ""))

    def test_frontend_batch_releases_busy_lock_before_select_chapter(self):
        src = (ROOT / "web/assets/workshop.js").read_text(encoding="utf-8")
        start = src.index("async function doBatchGenerate()")
        end = src.index("async function doRegenerate()")
        fn = src[start:end]

        clear_pos = fn.rfind("loading.batch=false;")
        select_pos = fn.rfind("selectChapter(")
        self.assertNotEqual(clear_pos, -1)
        self.assertNotEqual(select_pos, -1)
        self.assertLess(
            clear_pos,
            select_pos,
            "loading.batch must be cleared BEFORE calling selectChapter so isBusy does not drop the fetch",
        )

    def test_frontend_batch_progress_safe_for_single_chapter(self):
        src = (ROOT / "web/assets/workshop.js").read_text(encoding="utf-8")
        start = src.index("async function doBatchGenerate()")
        end = src.index("async function doRegenerate()")
        fn = src[start:end]
        self.assertNotIn("/(end-start)*100", fn.replace(" ", ""))
        self.assertIn("Math.max(1, end - start + 1)", fn)


if __name__ == "__main__":
    unittest.main()
