import asyncio
import re

from backend.core.styles import build_style_prompt, get_all_style_names, get_style
from backend.core.writer import LITERARY_SYSTEM, WRITER_SYSTEM, Writer


def _collect(stream):
    async def _run():
        return [chunk async for chunk in stream]

    return asyncio.run(_run())


def test_writer_prompt_uses_semantic_paragraphs_instead_of_numeric_formulae():
    prompt = WRITER_SYSTEM + LITERARY_SYSTEM

    for formula in (
        "3-5句",
        "80-150字",
        "对话占比",
        "每章 70%",
        "常规每章推进一级关系",
        "章末必须是能截图传播的金句",
        "人物动作和对话用「」包裹",
    ):
        assert formula not in prompt

    assert "段落按语义单元组织" in prompt
    assert "引号只包住人物实际说出口的话" in prompt


def test_style_prompt_uses_compact_attributes_without_legacy_samples():
    style = get_style("轻松脑洞")
    prompt = build_style_prompt(style)

    assert "风格示例" not in prompt
    assert "语言特征（适当使用）" not in prompt
    assert "严禁写法" not in prompt
    assert "《" not in prompt
    assert "每300字" not in prompt
    assert "%" not in prompt
    assert len(prompt) < 1800


def test_all_public_style_prompts_are_free_of_fixed_numeric_quotas():
    for name in get_all_style_names():
        prompt = build_style_prompt(get_style(name))
        assert "《" not in prompt, name
        assert "%" not in prompt, name
        assert not re.search(r"\d+\s*章", prompt), name
        assert not re.search(r"每\s*\d+", prompt), name
        assert len(prompt) < 1800, name


def test_writer_does_not_run_hidden_rewrite_passes_after_streaming_draft():
    draft = "雨落在站台边缘，他没有回头。" * 45

    class FakeResilient:
        async def create_stream(self, **_kwargs):
            yield draft

    writer = Writer(object(), "test-model")
    writer._resilient = FakeResilient()
    hidden_calls = []

    async def _hidden_pass(**_kwargs):
        hidden_calls.append(True)
        return ["不应生成的隐藏改写"]

    writer._create_stream_chunks = _hidden_pass
    chunks = _collect(
        writer.write_stream(
            context="本章写一次雨夜离站。",
            genre="都市",
            style="克制哲思",
            target_words=600,
        )
    )

    assert "".join(chunks) == draft
    assert hidden_calls == []
