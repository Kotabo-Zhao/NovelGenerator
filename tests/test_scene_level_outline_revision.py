import asyncio
from pathlib import Path
from types import SimpleNamespace

from backend.core.outline_compliance import OutlineComplianceChecker
from backend.core.mixins.generation import GenerationMixin


class _SequenceClient:
    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def test_scene_beats_receive_semantic_llm_adjudication():
    client = _SequenceClient(
        '{"results":[{"id":"beat1","present":true,'
        '"evidence":"他拆掉火漆，读到了父亲留下的坐标"}]}'
    )
    checker = OutlineComplianceChecker(client, "test-model")

    report = checker.check_chapter(
        "林岚用刀尖挑开红色封口，纸上那串经纬度正是父亲的笔迹。",
        {
            "scene_beats": [
                {"beat": 1, "name": "密信", "key_action": "林岚拆开密信并发现父亲留下的坐标"}
            ]
        },
    )

    assert report["passed"] is True
    assert report["results"][0]["by"] == "llm"
    assert client.calls


def test_batch_mode_does_not_treat_absent_literal_beat_wording_as_semantic_failure():
    checker = OutlineComplianceChecker()

    report = checker.check_chapter(
        "林岚用刀尖挑开红色封口，纸上那串经纬度正是父亲的笔迹。",
        {"scene_beats": [{"beat": 1, "key_action": "林岚拆开密信并发现父亲留下的坐标"}]},
        run_deep=False,
    )

    assert report["passed"] is True
    assert report["results"][0]["by"] == "skipped"
    assert report["critical_missing"] == []


def test_missing_beat_replaces_its_scene_instead_of_appending_to_chapter_end():
    replacement = "林岚回到工作台，拆开密信。坐标露出来时，她先把父亲的旧地图压在纸边，确认两处标记完全重合。"
    client = _SequenceClient(replacement)
    checker = OutlineComplianceChecker(client, "test-model")
    original = "\n\n".join([
        "雨从檐角落下，林岚关上院门。",
        "她沿着走廊检查每一扇窗。",
        "工作台上放着一只没有署名的信封。",
        "她没有动信封，先去确认门锁。",
        "巷口传来汽车熄火的声音。",
        "林岚抬头，看见窗帘后掠过一道影子。",
    ])

    result = checker.revise_missing_scenes(
        original,
        [{"id": "beat2", "label": "节拍2: 拆信", "needle": "林岚拆开密信并发现坐标"}],
        {
            "scene_beats": [
                {"beat": 1, "key_action": "回到旧宅"},
                {"beat": 2, "key_action": "林岚拆开密信并发现坐标"},
                {"beat": 3, "key_action": "窗外出现跟踪者"},
            ]
        },
    )

    assert result["applied"] is True
    assert result["text"].startswith("雨从檐角落下")
    assert result["text"].endswith("林岚抬头，看见窗帘后掠过一道影子。")
    assert replacement in result["text"]
    assert result["text"].index(replacement) < result["text"].index("巷口传来")
    assert result["edits"][0]["operation"] == "replace_scene"
    system_prompt = client.calls[0]["messages"][0]["content"]
    assert "不是续写" in system_prompt
    assert "只输出修订后的目标场景" in system_prompt


def test_generation_paths_consume_full_scene_revision_without_tail_append():
    root = Path(__file__).parents[1]
    source = (root / "backend" / "core" / "mixins" / "generation.py").read_text(encoding="utf-8")
    frontend = (root / "web" / "index.html").read_text(encoding="utf-8")

    assert source.count('get("revised_text")') == 2
    assert 'full_text.rstrip() + "\\n\\n" + _cev["patched_text"]' not in source
    assert frontend.count("if(evt.revised_text) writing.content=evt.revised_text;") == 2
    assert "evt.patched_text" not in frontend


def test_outline_step_rechecks_and_persists_only_the_scene_revised_result(monkeypatch):
    missing = [{"id": "beat2", "label": "节拍2", "needle": "拆开密信", "critical": True}]

    class _Checker:
        def __init__(self, *_args):
            self.check_count = 0

        def check_chapter(self, text, _outline, run_deep=True):
            self.check_count += 1
            if "坐标" in text:
                return {
                    "pct": 100, "passed": True, "level": "ok",
                    "results": [], "missing": [], "critical_missing": [],
                }
            return {
                "pct": 25, "passed": False, "level": "fail",
                "results": missing, "missing": missing, "critical_missing": missing,
            }

        def revise_missing_scenes(self, *_args):
            return {
                "text": "林岚在工作台拆开密信，确认纸上的坐标。",
                "applied": True,
                "edits": [{"operation": "replace_scene", "scene": 2}],
            }

    class _Memory:
        def __init__(self):
            self.state = {}

        def get_novel_state(self, _novel_id):
            return self.state

        def save_novel_state(self, _novel_id, state):
            self.state = state

    class _Probe(GenerationMixin):
        def __init__(self):
            self.client = object()
            self.model = "test"
            self.memory = _Memory()

    monkeypatch.setattr("backend.core.outline_compliance.OutlineComplianceChecker", _Checker)
    probe = _Probe()

    async def _collect():
        return [event async for event in probe._outline_compliance_step(
            "novel-1",
            2,
            {"scene_beats": [{"beat": 1}, {"beat": 2}, {"beat": 3}]},
            {},
            "原始正文没有密信事件。",
        )]

    events = asyncio.run(_collect())
    result = next(event for event in events if event.get("type") == "outline_compliance")

    assert result["revised_text"].endswith("坐标。")
    assert result["pre_edit_pct"] == 25
    assert result["pct"] == 100
    assert result["revision_result"] == "applied"
    stored = probe.memory.state["outline_compliance"]["2"]
    assert stored["pct"] == 100
    assert stored["missing"] == []
