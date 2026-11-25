"""Shared post-chapter pipeline for standard, batch, and atomic generation streams.

Consolidates S3P 5-in-1 structured archiving (bridge + summary + character states +
foreshadowing + consistency audit), zero-LLM-cost prose craft diagnostics, and
periodic rolling summary compression so all drafting strategies stay in sync.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from ..chapter_summarizer import check_and_compress

log = logging.getLogger(__name__)


async def run_post_chapter_pipeline(
    engine: Any,
    novel_id: str,
    chapter_num: int,
    full_text: str,
    chapter_outline: dict,
    *,
    mode_label: str = "standard",
) -> AsyncIterator[dict]:
    """Execute unified post-chapter archiving, zero-cost craft check, and compression."""
    # 1. S3P 五合一单次结构化后处理（桥接 + 摘要 + 角色状态 + 伏笔 + 一致性审计）
    try:
        from ..unified_post_processor import UnifiedPostProcessor

        yield {"type": "status", "message": "🧠 正在执行五合一状态归档与逻辑体检…"}
        upp = UnifiedPostProcessor(engine._resilient, engine.model)
        upp_res = await asyncio.to_thread(
            upp.process_and_persist,
            engine.memory,
            novel_id,
            chapter_num,
            full_text,
            chapter_outline,
        )
        violations = upp_res.get("consistency_violations") or []
        p0_count = sum(
            1 for v in violations if isinstance(v, dict) and v.get("severity") == "P0"
        )
        score = max(0, 100 - p0_count * 25 - (len(violations) - p0_count) * 8)
        if violations:
            yield {"type": "consistency_check", "violations": violations, "score": score}
            issues = [
                v.get("description", "")
                for v in violations[:5]
                if isinstance(v, dict) and v.get("description")
            ]
            if issues:
                state = engine.memory.get_novel_state(novel_id)
                state["consistency_issues"] = state.get("consistency_issues", {})
                state["consistency_issues"][str(chapter_num)] = issues
                engine.memory.save_novel_state(novel_id, state)
                yield {
                    "type": "consistency_warning",
                    "chapter": chapter_num,
                    "issues": issues,
                }
    except Exception as exc:
        log.warning(
            "UnifiedPostProcessor skipped (%s) Ch%s: %s", mode_label, chapter_num, exc
        )

    # 2. 零 LLM 消耗毫秒级文风体检（直推前端面板，联动一键改稿/局部精修）
    try:
        from ..ai_detector import AIDetector

        craft = AIDetector.analyze_prose_craft(full_text)
        yield {"type": "craft_report", "chapter": chapter_num, "report": craft}
    except Exception as exc:
        log.warning(
            "Zero-cost craft report skipped (%s) Ch%s: %s", mode_label, chapter_num, exc
        )

    # 3. 渐进式摘要压缩（定期归档远期章节）
    try:
        compress_result = check_and_compress(
            engine.memory, novel_id, chapter_num, engine.chapter_summarizer
        )
        if compress_result:
            yield {
                "type": "compression",
                "chapters_summarized": len(compress_result),
            }
    except Exception as exc:
        log.warning(
            "Auto-compression skipped (%s) Ch%s: %s", mode_label, chapter_num, exc
        )
