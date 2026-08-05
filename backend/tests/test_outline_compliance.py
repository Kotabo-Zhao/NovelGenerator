# -*- coding: utf-8 -*-
"""大纲合规校验器测试（v3.10）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.outline_compliance import OutlineComplianceChecker, _clean_patch
from core.mixins.generation import GenerationMixin


def _outline(**kw):
    base = {
        "number": 1, "title": "第一章",
        "summary": "主角进入宗门，参加入门考核",
        "emotion_curve": "平稳→紧张→悬念",
        "characters": ["主角", "师姐", "反派"],
        "conflict": "考核中遭遇暗算",
        "scene_beats": [
            {"beat": 1, "name": "入门", "key_action": "主角踏入山门"},
            {"beat": 2, "name": "考核", "key_action": "师姐暗中相助"},
        ],
        "hook": "反派现身", "target_words": 1500,
    }
    base.update(kw)
    return base


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs.get("messages") or [])
        if not self.responses:
            return _resp(None)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return _resp(r)


def _resp(content):
    if content is None:
        return type("R", (), {"choices": []})()
    m = type("M", (), {"content": content})()
    c = type("C", (), {"message": m})()
    return type("R", (), {"choices": [c]})()


def test_extract_check_items():
    c = OutlineComplianceChecker(None, "x")
    items = c.extract_check_items(_outline())
    ids = [i["id"] for i in items]
    assert "summary" in ids and "beat1" in ids and "beat2" in ids
    assert any(i["id"].startswith("char_") for i in items)
    assert "conflict" in ids
    assert next(i for i in items if i["id"] == "summary")["critical"] is True
    assert next(i for i in items if i["id"].startswith("char_"))["critical"] is False


def test_check_chapter_rules_only():
    c = OutlineComplianceChecker(None, "x")
    text = ("主角踏入山门，气势如虹。师姐暗中相助，助他化解险境。\n"
            "师姐笑道：小心些。反派忽然现身，气氛骤变。")
    r = c.check_chapter(text, _outline(), run_deep=True)
    assert r["pct"] >= 80 and r["passed"] is True and r["level"] == "ok"
    char_rows = [x for x in r["results"] if x["id"].startswith("char_")]
    assert char_rows and all(x["present"] for x in char_rows)


def test_check_chapter_missing_character():
    c = OutlineComplianceChecker(None, "x")
    text = "主角踏入山门。反派现身。但没有师姐。"
    r = c.check_chapter(text, _outline(), run_deep=True)
    rows = {x["id"]: x for x in r["results"]}
    assert rows["char_师姐"]["present"] is False
    assert rows["char_师姐"]["by"] == "rule"


def test_check_chapter_llm_hit():
    llm = _FakeLLM([
        '{"results": [{"id": "summary", "present": true, "evidence": "主角入宗门考核"},'
        '{"id": "conflict", "present": true, "evidence": "遭遇暗算"}]}'
    ])
    c = OutlineComplianceChecker(None, "x")
    c._resilient = llm
    text = "主角踏入山门。师姐暗中相助。反派现身暗算。"
    r = c.check_chapter(text, _outline(), run_deep=True)
    assert r["pct"] == 100 and r["passed"] is True
    assert next(x for x in r["results"] if x["id"] == "beat1")["by"] == "rule"


def test_check_chapter_llm_missing_then_patch():
    llm = _FakeLLM([
        '{"results": [{"id": "summary", "present": false, "evidence": ""},'
        '{"id": "conflict", "present": false, "evidence": ""}]}',
        "主角终于踏入宗门，参加入门考核，却在最后一关遭遇暗算，师姐出手相助。",
    ])
    c = OutlineComplianceChecker(None, "x")
    c._resilient = llm
    text = "宗门大殿内一片肃静。长老宣布开始考核。"
    r = c.check_chapter(text, _outline(), run_deep=True)
    assert r["level"] == "fail"
    assert not r["passed"]
    assert any(m["id"] == "summary" for m in r["missing"])
    patch = c.patch_missing(text, r["missing"])
    assert patch and "入门考核" in patch
    assert len(llm.calls) == 2


def test_check_chapter_llm_down_no_false_positive():
    llm = _FakeLLM([RuntimeError("llm down")])
    c = OutlineComplianceChecker(None, "x")
    c._resilient = llm
    text = "主角踏入山门。师姐相助。反派现身。"
    r = c.check_chapter(text, _outline(), run_deep=True)
    unknown = [x for x in r["results"] if x["by"] == "unknown"]
    assert unknown and all(x["present"] for x in unknown)
    assert next(x for x in r["results"] if x["id"] == "char_师姐")["present"] is True


def test_clean_patch():
    assert _clean_patch("# 标题\n\n正文内容") == "正文内容"
    assert _clean_patch("（补写：这样写）\n\n正文") == "正文"
    assert _clean_patch("正文\n\n\n\n结尾") == "正文\n\n结尾"


class _FakeMem:
    def __init__(self):
        self.state = {}
    def get_novel_state(self, novel_id):
        return self.state
    def save_novel_state(self, novel_id, state):
        self.state = state


class _FakeEngine:
    client = None
    model = "test-model"
    memory = None


def test_mixin_step_ok():
    eng = _FakeEngine()
    eng.memory = _FakeMem()
    events = []
    async def _run():
        async for ev in GenerationMixin._outline_compliance_step(
                eng, "novel", 1, _outline(), {"genre": "玄幻"}, 
                "主角踏入山门。师姐暗中相助。反派现身。考核开始。", False):
            events.append(ev)
    import asyncio
    asyncio.run(_run())
    comp = [e for e in events if e["type"] == "outline_compliance"]
    assert len(comp) == 1
    ev = comp[0]
    assert ev["chapter"] == 1 and ev["level"] == "ok"
    assert ev["patched_text"] == ""
    assert eng.memory.state["outline_compliance"]["1"]["level"] == "ok"