"""NovelEngine GenerationMixin — 章节生成 — 普通路径（Writer 两遍式）与原子化路径（逐 beat）

由 tools/split_engine.py 从 engine.py 自动拆分。
依赖 NovelEngine 提供的 self.client/self.model/self.memory 等属性。
"""
import logging

log = logging.getLogger(__name__)

import asyncio
import hashlib
import json
import os
import re
import time
# Allow importing from parent dir (works both as package and standalone)
try:
    from backend import config
except ImportError:
    import config
from typing import AsyncGenerator, Optional, AsyncIterator
from ..atomic_io import atomic_write_json, safe_read_json
from ..beat_decomposer import BeatDecomposer, Beat
from ..pacing_checker import PacingChecker
from ..evaluation_system import evaluate_chapter
from ..chapter_summarizer import check_and_compress
from ..usage_context import report_response_usage


# ── 章节生成并发锁（防两个 Tab/进程并发生成同一章）──
_GEN_LOCK_STALE_SECONDS = 900  # 15 分钟，必须大于一章真实生成时长（多次 LLM 调用常超 5 分钟）


def _pid_alive(pid: int) -> bool:
    """跨平台探测进程是否存活。无法判定时保守返回 True，绝不误删活跃锁。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _read_lock_pid(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip().split("|", 1)[0])
    except (OSError, ValueError):
        return 0


def _write_gen_lock(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()}|{time.time()}")


def _gen_lock_is_stale(lock_file: str) -> bool:
    """判断章节锁是否可安全清除：PID 已死，或无 PID 且超时。"""
    try:
        lock_age = time.time() - os.path.getmtime(lock_file)
    except OSError:
        return True
    pid = _read_lock_pid(lock_file)
    if pid:
        return not _pid_alive(pid)
    return lock_age >= _GEN_LOCK_STALE_SECONDS


def _normalize_chapter_text(text: str) -> str:
    """按当前包导入方式加载段落规范化器，兼容 backend/core 两种入口。"""
    try:
        from ..shared_memory import normalize_chapter_paragraphs
    except ImportError:
        from core.shared_memory import normalize_chapter_paragraphs
    return normalize_chapter_paragraphs(text)


def _merge_quality_with_consistency(quality: dict, consistency: dict) -> dict:
    """把后置一致性结果合并进最终质量报告，避免用旧分数掩盖逻辑硬伤。"""
    merged = dict(quality or {})
    issues = list(merged.get("issues") or [])
    violations = (consistency or {}).get("violations") or []
    validation_error = str((consistency or {}).get("error") or "").strip()
    if validation_error:
        merged["score"] = 0
        item = f"[P1] 一致性校验未完成: {validation_error}"
        if item not in issues:
            issues.append(item)
    if violations:
        try:
            quality_score = int(float(merged.get("score", 0)))
        except (TypeError, ValueError):
            quality_score = 0
        try:
            consistency_score = int(float((consistency or {}).get("score", 0)))
        except (TypeError, ValueError):
            consistency_score = 0
        merged["score"] = min(quality_score, consistency_score)
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            severity = str(violation.get("severity") or "P1").upper()
            description = str(violation.get("description") or "逻辑一致性问题").strip()
            item = f"[{severity}] {description}"
            if item not in issues:
                issues.append(item)
    merged["issues"] = issues[:20]
    return merged


def _merge_quality_with_narrative(quality: dict, narrative: dict) -> dict:
    """将章节结构契约并入本地质量门，避免只按句式通过的空转章节落盘。"""
    merged = dict(quality or {})
    if not isinstance(narrative, dict):
        return merged
    merged["narrative"] = narrative
    structure = narrative.get("structure") or {}
    structure_score = structure.get("score")
    if structure_score is None:
        return merged
    try:
        base_score = float(merged.get("score", 0))
        structure_score = float(structure_score)
        hook_score = float((narrative.get("hook") or {}).get("strength", 0)) * 10
    except (TypeError, ValueError):
        return merged

    # 结构是辅助质量门：保留 pacing checker 的主体权重，但让结构明显失真
    # 的文本无法用“句长合格”掩盖问题。
    structural_blend = structure_score * 0.75 + hook_score * 0.25
    merged["score"] = int(round(min(base_score, base_score * 0.7 + structural_blend * 0.3)))
    issues = list(merged.get("issues") or [])
    for issue in structure.get("issues") or []:
        item = f"[叙事结构] {issue}"
        if item not in issues:
            issues.append(item)
    merged["issues"] = issues[:20]
    return merged


def _consistency_candidate_rank(consistency: dict) -> tuple:
    """Rank repair candidates: fewer P0 first, then higher score, then fewer issues."""
    violations = (consistency or {}).get("violations") or []
    try:
        score = int(float((consistency or {}).get("score", 0)))
    except (TypeError, ValueError):
        score = 0
    return (
        sum(1 for item in violations if item.get("severity") == "P0"),
        -score,
        len(violations),
    )


def _save_final_quality_report(memory, novel_id: str, chapter_num: int, report: dict, *, stage: str) -> dict:
    """Persist the post-mutation quality result instead of reusing a stale score."""
    path = os.path.join(memory.get_novel_dir(novel_id), "quality_reports.json")
    reports = safe_read_json(path, {})
    if not isinstance(reports, dict):
        reports = {}
    record = {
        "version": "quality-gate-v3",
        "stage": stage,
        "chapter_num": chapter_num,
        "score": report.get("score", 0),
        "issues": list(report.get("issues", []))[:20],
        "word_count": len(report.get("text", "")),
        "text_sha256": hashlib.sha256(report.get("text", "").encode("utf-8")).hexdigest(),
        "evaluated_at": time.time(),
    }
    narrative = report.get("narrative") or {}
    if narrative:
        record["narrative_score"] = narrative.get("structure", {}).get("score", 0)
        record["narrative_metrics"] = narrative.get("structure", {}).get("metrics", {})
        record["narrative_issues"] = narrative.get("structure", {}).get("issues", [])[:10]
    reports[str(chapter_num)] = record
    atomic_write_json(path, reports)
    return record


def choose_draft_strategy(chapter_outline: dict | None, *, batch_mode: bool = False) -> str:
    """Choose an internal draft strategy from contract readiness.

    This is intentionally not a user-facing writing mode.  Batch generation favors
    the lower-call standard path; a fully specified single chapter can use atomic
    beat drafting without exposing implementation terminology in the product UI.
    """
    if batch_mode:
        return "standard"
    chapter = chapter_outline or {}
    beats = chapter.get("scene_beats") or []
    concrete = 0
    for beat in beats:
        action = beat.get("key_action") if isinstance(beat, dict) else beat
        if str(action or "").strip():
            concrete += 1
    return "atomic" if chapter.get("contract_status") == "ready" and concrete >= 3 else "standard"


def _hard_requirement_failures(report: dict | None) -> list[dict]:
    return [
        item
        for item in ((report or {}).get("results") or [])
        if item.get("status") == "failed" and item.get("hard_gate") is True
    ]


def _hard_outline_failures(report: dict | None) -> list[dict]:
    if (report or {}).get("ok") is False:
        return [{"id": "validation_unavailable", "label": "大纲合规校验未完成"}]
    if (report or {}).get("passed") is not False:
        return []
    return [
        item
        for item in ((report or {}).get("critical_missing") or [])
        if isinstance(item, dict)
    ]




# ===== _extract_key_ending (从 engine.py 迁移) =====
def _extract_key_ending(text: str, window: int = 600) -> dict | None:
    """提取原文关键结尾信息，用于 Humanizer 后恢复保护。
    
    返回 {sentences, ngrams, has_hook} 或 None。
    """
    import re
    if len(text) < 100:
        return None
    
    # 按句子边界切分
    raw = re.split(r'(?<=[。！？……])', text[-window:])
    sentences = [s.strip() for s in raw if s.strip() and len(s.strip()) > 2]
    
    if len(sentences) < 2:
        return None
    
    # 最后 2-3 句为关键结尾
    key = sentences[-3:] if len(sentences) >= 3 else sentences[-2:]
    ending_text = ''.join(key)
    
    # 3-gram 特征集
    chars = ending_text.replace(' ', '').replace('\n', '')
    ngrams = {chars[i:i+3] for i in range(len(chars) - 2)} if len(chars) >= 3 else set()
    
    # 勾子检测
    hook_markers = ['突然', '忽然', '猛地', '即将', '等待', '然而', '可是',
                    '但', '只见', '……', '—', '未完', '待续', '下一章']
    has_hook = any(m in ending_text for m in hook_markers)
    
    return {
        "text": ending_text,
        "ngrams": ngrams,
        "has_hook": has_hook,
        "sentence_count": len(key),
    }



# ===== _protect_ending_semantic (从 engine.py 迁移) =====
def _protect_ending_semantic(original: str, rewritten: str, ending_info: dict) -> str:
    """v2.13: 语义结尾保护 — 三重检测决定是否恢复原文结尾。
    
    1. n-gram 重叠率 < 40% → 结尾已被大改
    2. 原文结尾长度 > 改写结尾 1.5x → 被截断
    3. 原文有勾子改写版没有 → 悬念被抹掉
    
    满足任一条件即恢复原文结尾。
    """
    import re
    
    # 提取改写版结尾
    raw = re.split(r'(?<=[。！？……])', rewritten[-600:])
    r_sentences = [s.strip() for s in raw if s.strip() and len(s.strip()) > 2]
    if len(r_sentences) < 2:
        # 改写版结尾太短 — 直接用原文结尾替换
        if len(ending_info["text"]) > 80:
            cutoff = max(0, len(rewritten) - len(ending_info["text"]))
            protected = rewritten[:cutoff] + ending_info["text"]
            log.info(f"Humanizer: ending protected (rewrite too short, {len(ending_info['text'])} chars kept)")
            return protected
        return rewritten
    
    r_ending_text = ''.join(r_sentences[-3:] if len(r_sentences) >= 3 else r_sentences[-2:])
    
    # 指标1: n-gram 重叠
    if ending_info["ngrams"]:
        r_chars = r_ending_text.replace(' ', '').replace('\n', '')
        r_ngrams = {r_chars[i:i+3] for i in range(len(r_chars) - 2)} if len(r_chars) >= 3 else set()
        if ending_info["ngrams"]:
            overlap = len(ending_info["ngrams"] & r_ngrams) / len(ending_info["ngrams"])
        else:
            overlap = 1.0
    else:
        overlap = 1.0
    
    # 指标2: 长度剧烈缩短
    length_shrink = len(ending_info["text"]) > len(r_ending_text) * 1.5
    
    # 指标3: 勾子丢失
    hook_markers = ['突然', '忽然', '猛地', '即将', '等待', '然而', '可是',
                    '但', '只见', '……', '—', '未完', '待续', '下一章']
    r_has_hook = any(m in r_ending_text for m in hook_markers)
    hook_lost = ending_info["has_hook"] and not r_has_hook
    
    needs_protection = overlap < 0.4 or length_shrink or hook_lost
    
    if needs_protection:
        # 用原文结尾替换改写版结尾
        ending_len = len(ending_info["text"])
        cutoff = max(0, len(rewritten) - len(r_ending_text))
        protected = rewritten[:cutoff] + ending_info["text"]
        
        reasons = []
        if overlap < 0.4: reasons.append(f"overlap={overlap:.0%}")
        if length_shrink: reasons.append("length_shrink")
        if hook_lost: reasons.append("hook_lost")
        
        log.info(f"Humanizer: ending semantically protected ({', '.join(reasons)})")
        return protected
    
    return rewritten




# ===== _get_style_guide (从 engine.py 迁移) =====
def _get_style_guide(style: str, genre: str) -> str:
    """获取简化的风格指南（用于 AtomicWriter）"""
    from ..styles import get_style, build_style_prompt
    try:
        style_config = get_style(style)
        return build_style_prompt(style_config)
    except Exception:
        return f"写作风格：{style}。题材：{genre}。"


class GenerationMixin:
    def _record_revision_candidate(
        self,
        novel_id: str,
        chapter_num: int,
        text: str,
        formatted: str,
        *,
        reason: str,
        requirement_ids: list[str] | None = None,
        quality_record: dict | None = None,
    ) -> dict:
        """Preserve a rejected candidate outside the published chapter directory."""
        draft_saved = False
        try:
            self.memory.write_chapter_draft(novel_id, chapter_num, formatted)
            draft_saved = True
        except Exception as exc:
            log.warning("Could not preserve revision draft for chapter %s: %s", chapter_num, exc)
        finally:
            self.memory.remove_incomplete_chapter(novel_id, chapter_num)

        repaired = self.memory.repair_state(novel_id)
        state = dict(repaired.get("state") or self.memory.get_novel_state(novel_id) or {})
        published_chapter_preserved = self.memory.chapter_exists(novel_id, chapter_num)
        completed = list(state.get("completed_chapters") or [])
        if not published_chapter_preserved:
            completed = [value for value in completed if int(value) != int(chapter_num)]
        state["completed_chapters"] = sorted(set(completed))
        state["current_chapter"] = max(completed) if completed else 0
        digest = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
        status = {
            "status": "needs_revision",
            "reason": reason,
            "requirement_ids": list(requirement_ids or []),
            "quality_score": (quality_record or {}).get("score"),
            "text_sha256": digest,
            "draft_saved": draft_saved,
            "published_chapter_preserved": published_chapter_preserved,
            "updated_at": time.time(),
        }
        state.setdefault("chapter_statuses", {})[str(chapter_num)] = status
        self.memory.save_novel_state(novel_id, state)
        return {"chapter_num": chapter_num, **status}

    def _preserve_generation_failure(
        self,
        novel_id: str,
        chapter_num: int,
        text: str,
        formatted: str,
        error: Exception,
    ) -> dict | None:
        """Keep usable generated text when a post-generation check raises."""
        candidate = str(text or "").strip()
        if not candidate:
            self.memory.remove_incomplete_chapter(novel_id, chapter_num)
            return None
        rendered = str(formatted or "").strip() or candidate
        return self._record_revision_candidate(
            novel_id,
            chapter_num,
            candidate,
            rendered,
            reason=f"生成后校验失败：{error}",
        )

    def _finalize_chapter_candidate(
        self,
        novel_id: str,
        chapter_num: int,
        text: str,
        formatted: str,
        requirement_report: dict | None,
        quality_record: dict | None,
        outline_report: dict | None = None,
    ) -> dict:
        """Apply hard gates, then atomically expose one completed candidate."""
        requirement_failures = _hard_requirement_failures(requirement_report)
        outline_failures = _hard_outline_failures(outline_report)
        if requirement_failures or outline_failures:
            ids = [
                str(item.get("subtask_id") or "")
                for item in requirement_failures
                if item.get("subtask_id")
            ]
            ids.extend(
                f"outline:{item.get('id')}"
                for item in outline_failures
                if item.get("id")
            )
            reasons = []
            if requirement_failures:
                titles = [
                    str(item.get("title") or item.get("subtask_id") or "未命名需求")
                    for item in requirement_failures
                ]
                reasons.append("P0 需求未满足：" + "；".join(titles))
            if outline_failures:
                labels = [
                    str(item.get("label") or item.get("id") or "未命名核心事件")
                    for item in outline_failures
                ]
                reasons.append("大纲核心事件未落地：" + "；".join(labels))
            return self._record_revision_candidate(
                novel_id,
                chapter_num,
                text,
                formatted,
                reason="｜".join(reasons),
                requirement_ids=ids,
                quality_record=quality_record,
            )

        self.memory.save_chapter(novel_id, chapter_num, formatted)
        repaired = self.memory.repair_state(novel_id)
        state = dict(repaired.get("state") or self.memory.get_novel_state(novel_id) or {})
        completed = [int(value) for value in (state.get("completed_chapters") or [])]
        if chapter_num not in completed:
            completed.append(chapter_num)
        state["completed_chapters"] = sorted(set(completed))
        state["current_chapter"] = max(completed) if completed else 0
        digest = hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
        status = {
            "status": "completed",
            "quality_score": (quality_record or {}).get("score"),
            "text_sha256": digest,
            "updated_at": time.time(),
        }
        state.setdefault("chapter_statuses", {})[str(chapter_num)] = status
        self.memory.save_novel_state(novel_id, state)
        self.memory.delete_chapter_draft(novel_id, chapter_num)
        return {"chapter_num": chapter_num, **status}

    async def unified_generate_chapter_stream(
        self,
        novel_id: str,
        chapter_num: int,
        writing_mode: str = "webnovel",
        feedback: str | None = None,
        batch_mode: bool = False,
        force_strategy: str = "",
    ) -> AsyncGenerator[dict, None]:
        """Single public chapter stream with an internal drafting strategy."""
        plan = self.get_novel(novel_id) or {}
        chapter_outline = self._find_chapter_outline(plan, chapter_num) or {}
        strategy = force_strategy if force_strategy in {"standard", "atomic"} else choose_draft_strategy(
            chapter_outline, batch_mode=batch_mode
        )
        yield {
            "type": "draft_strategy",
            "strategy": strategy,
            "message": "正在按章节契约组织草稿…",
        }
        if strategy == "atomic":
            async for event in self.atomic_generate_chapter_stream(
                novel_id, chapter_num, writing_mode, feedback=feedback
            ):
                yield event
            return
        async for event in self.generate_chapter_stream(
            novel_id,
            chapter_num,
            writing_mode,
            feedback=feedback,
            batch_mode=batch_mode,
        ):
            yield event

    def _find_chapter_outline(self, plan: dict, chapter_num: int) -> Optional[dict]:
        """在大纲中查找指定章节（兼容字符串/整数章节号，防御脏数据）"""
        volumes = plan.get("outline", {}).get("volumes", [])
        for vol in volumes:
            if not isinstance(vol, dict):
                continue
            for ch in vol.get("chapters", []):
                if not isinstance(ch, dict):
                    continue
                if int(ch.get("number", 0)) == chapter_num:
                    return ch
        return None

    # ── v2.8: 章节大纲细化（旧大纲只有一句话 summary → 扩成 scene_beats 事件契约）──

    def _chapter_needs_outline_expansion(self, chapter_outline: dict) -> bool:
        """判断本章大纲是否足够支撑正文生成：
        - 缺 scene_beats（旧版大纲/手工编辑）
        - 或 scene_beats 是占位符（key_action 与 summary 无关、全是通用描述）
        """
        try:
            beats = chapter_outline.get("scene_beats") or []
            if not beats:
                return True
            summary = str(chapter_outline.get("summary", "") or "").strip()
            # 占位判定：summary 太短，或 beats 无具体 key_action
            if len(summary) < 20:
                return True
            concrete = 0
            for b in beats:
                if isinstance(b, dict):
                    ka = str(b.get("key_action", "") or "").strip()
                    if ka and len(ka) > 8 and "未设定" not in ka and "占位" not in ka:
                        concrete += 1
                elif isinstance(b, str) and b.strip() and len(b.strip()) > 8:
                    concrete += 1
            if concrete < 3:
                return True
            return False
        except Exception:
            return False

    def _expand_chapter_outline(self, plan: dict, chapter_outline: dict,
                                novel_id: str, chapter_num: int) -> bool:
        """用 LLM 把单句 summary 细化成 5 个 scene_beats 事件契约，写回 plan.json。

        返回是否成功。失败时静默返回 False（正文仍可按旧大纲生成，不阻断主流程）。
        """
        try:
            summary = str(chapter_outline.get("summary", "") or "").strip()
            title = str(chapter_outline.get("title", "") or f"第{chapter_num}章")
            hook = str(chapter_outline.get("hook", "") or "").strip()
            chars = ", ".join(chapter_outline.get("characters", []) or ["主角"])
            bridge = str(chapter_outline.get("bridge_to_next", "") or "").strip()
            cause = str(chapter_outline.get("cause_from_prev", "") or "").strip()
            prev_tail = ""
            if chapter_num > 1:
                try:
                    _pt = self.memory.read_chapter(novel_id, chapter_num - 1)
                    if _pt:
                        _lines = [l for l in _pt.strip().splitlines() if l.strip()]
                        prev_tail = (_lines[-1] if _lines else _pt.strip()[-200:])[:150]
                except Exception:
                    pass

            prompt = (
                f"你是资深网文大纲细化师。现有章节大纲信息不足，请把这一章扩写成 5 个连续事件节拍（scene_beats），"
                f"作为正文生成的硬性事件契约。\n\n"
                f"小说：{plan.get('title', '')}（{plan.get('genre', '')} / {plan.get('style', '')}）\n"
                f"章节：第{chapter_num}章「{title}」\n"
                f"核心事件：{summary}\n"
                f"章末钩子：{hook or '未指定，请自设'}\n"
                f"出场角色：{chars}\n"
                f"承接：{cause or '开篇'}\n"
                f"后续：{bridge or '未指定'}\n"
                + (f"上一章结尾：「{prev_tail}」\n" if prev_tail else "")
                + (
                    f"\n要求：\n"
                    f"1. 输出 JSON 数组，5 个元素，依次为：开篇钩子、冲突升级、转折高潮、余波收束、章末钩子。\n"
                    f"2. 每个 beat 字段：{{\"beat\":序号,\"name\":\"节拍名\",\"function\":\"该节拍在剧情中的作用\","
                    f"\"key_action\":\"本章该节点发生的具体事件（谁对谁做了什么，15-40字，必须是本章具体情节，不得是通用描述）\"}}\n"
                    f"3. key_action 必须承接「{summary}」展开具体动作与对话事件，禁止写「主角继续前进」「两人交谈」这类空话。\n"
                    f"4. 5 个 beat 合起来必须完整覆盖本章核心事件并自然推进到章末钩子。\n"
                    f"只输出 JSON 数组，不要任何解释、不要 markdown 代码块。"
                )
            )
            resp = self.writer._create(
                messages=[
                    {"role": "system", "content": "你只输出合法 JSON，不输出任何其他文字。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=1800,
            )
            raw = (resp.choices[0].message.content or "").strip()
            # 剥掉可能的 markdown 代码块围栏
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            import json as _json
            beats = _json.loads(raw)
            if not isinstance(beats, list) or len(beats) < 3:
                log.warning(f"Ch{chapter_num} outline expansion: bad beats shape ({type(beats).__name__})")
                return False
            # 规范化 beat 字段
            norm = []
            for i, b in enumerate(beats[:5], 1):
                if isinstance(b, dict):
                    norm.append({
                        "beat": int(b.get("beat", i)),
                        "name": str(b.get("name", f"节拍{i}"))[:20],
                        "function": str(b.get("function", ""))[:100],
                        "key_action": str(b.get("key_action", ""))[:120],
                    })
                elif isinstance(b, str):
                    norm.append({"beat": i, "name": f"节拍{i}", "function": "", "key_action": str(b)[:120]})
            if not any(n["key_action"] for n in norm):
                log.warning(f"Ch{chapter_num} outline expansion: all beats lack key_action")
                return False
            # 写回 plan.json（持久化，后续生成/重生成直接复用）
            chapter_outline["scene_beats"] = norm
            from ..chapter_contract import normalize_chapter_contract

            normalized_chapter = normalize_chapter_contract(chapter_outline)
            chapter_outline.clear()
            chapter_outline.update(normalized_chapter)
            if not chapter_outline.get("conflict_intensity"):
                chapter_outline["conflict_intensity"] = 3
            try:
                from ..atomic_io import atomic_write_json
                _plan_path = os.path.join(self.memory.get_novel_dir(novel_id), "plan.json")
                atomic_write_json(_plan_path, plan)
            except Exception as _save_err:
                log.warning(f"Ch{chapter_num} outline expansion persist failed (non-fatal): {_save_err}")
            log.info(f"Ch{chapter_num} outline expanded: {len(norm)} beats generated from one-line summary")
            return True
        except Exception as e:
            log.warning(f"Ch{chapter_num} outline expansion failed (non-fatal): {e}")
            return False

    def _render_scene_beats(self, chapter_outline: dict) -> str:
        """把 chapter_outline 的 scene_beats 渲染为紧凑文本（供精简版硬约束注入）。"""
        try:
            beats = chapter_outline.get("scene_beats") or []
            if not beats:
                return ""
            lines = []
            for i, b in enumerate(beats[:6], 1):
                if isinstance(b, dict):
                    ka = str(b.get("key_action", "") or "").strip()
                    name = str(b.get("name", "") or "").strip()
                    if ka:
                        lines.append(f"  {i}. {name}：{ka}" if name else f"  {i}. {ka}")
                elif b is not None and str(b).strip():
                    lines.append(f"  {i}. {str(b).strip()}")
            return "\n".join(lines)
        except Exception:
            return ""

    def _hard_outline_text(self, chapter_outline: dict) -> str:
        """精简版「本章大纲·硬约束」：含标题/核心事件/钩子/角色 + scene_beats（如有）。

        与 build_writer_context 的 L5 完整大纲互补：本精简版保证 scene_beats 在
        allocate_context 高优先级预算内必定保留，正文生成不会退化成只看一句话 summary。
        """
        from ..chapter_contract import normalize_chapter_contract

        chapter_outline = normalize_chapter_contract(chapter_outline)
        text = (
            "标题：{title}\n核心事件：{summary}\n结尾钩子：{hook}\n角色：{characters}".format(
                title=chapter_outline.get("title", ""),
                summary=chapter_outline.get("summary", ""),
                hook=chapter_outline.get("hook", ""),
                characters=", ".join(chapter_outline.get("characters", []) or []),
            )
        )
        beats = self._render_scene_beats(chapter_outline)
        if beats:
            text += "\n场景节拍：\n" + beats
        contract_fields = (
            ("章前状态", "before_state"),
            ("必须发生", "must_happen"),
            ("章后状态", "after_state"),
            ("新增信息", "new_information"),
            ("未闭环", "open_loop"),
            ("禁止重复", "forbidden_repeats"),
        )
        for label, key in contract_fields:
            values = chapter_outline.get(key) or []
            if values:
                text += f"\n{label}：" + "；".join(map(str, values[:8]))
        return text

    def _decompose_chapter_beats(self, chapter_outline: dict, chapter_num: int,
                                 novel_dir: str, is_first: bool = False) -> list:
        """AUDIT P1-1: 统一的章节 beat 分解助手（普通/原子路径共用节奏骨架）"""
        is_climax = bool(chapter_outline.get("is_climax") or chapter_outline.get("climax"))
        chars = chapter_outline.get("characters", []) or []
        decomposer = BeatDecomposer(seed=chapter_num * 100 + int(time.time()) % 100)
        return decomposer.decompose(
            chapter_outline, chapter_num,
            is_first_chapter=is_first,
            is_climax_chapter=is_climax,
            available_characters=chars,
        )

    def _format_beats_instruction(self, beats: list) -> str:
        """AUDIT P1-1: 把 beat 列表渲染为紧凑节拍骨架，供普通 Writer 单次生成"""
        lines = [
            "## 🎬 本章叙事契约（先完成事件，再展开文风；节拍顺序不可跳跃、不可合并）",
            "- 开篇必须让主角或现场发生一个可见动作，不要从设定说明起笔。",
            "- 中段必须出现具体阻力，并让主角做出选择；选择要产生可观察的状态变化。",
            "- 章末必须留下风险、信息、决定或下一步行动，最后一句必须完整收束，不得半句截断。",
            "## 节拍明细",
        ]
        for b in beats:
            lines.append(
                f"{b.index + 1}. {b.function}｜{b.goal}｜"
                f"情绪 {b.emotion_start}→{b.emotion_end}｜"
                f"冲突 {b.conflict_type}/{b.conflict_intensity}｜"
                f"字数 {b.min_words}-{b.max_words}"
            )
            if b.key_event:
                lines.append(f"   └ 核心事件: {b.key_event}")
            if b.character_focus:
                lines.append(f"   └ 聚焦角色: {b.character_focus}")
            if b.opposition:
                lines.append(f"   └ 主要阻力: {b.opposition}")
            if b.choice:
                lines.append(f"   └ 选择/代价: {b.choice}")
            if b.state_change:
                lines.append(f"   └ 状态变化: {b.state_change}")
            if b.reader_question:
                lines.append(f"   └ 读者未解问题: {b.reader_question}")
        return "\n".join(lines)

    def _strip_instruction_leaks(self, text: str) -> tuple:
        """AUDIT P0-1: 扫描正文中的元指令泄漏，返回 (清理后文本, 泄漏行数)"""
        markers = ("═══", "以下为写作元指令", "本章必须覆盖以下核心事件", "章末钩子方向")
        leaked = [ln for ln in text.splitlines() if any(m in ln for m in markers)]
        if not leaked:
            return text, 0
        clean = [ln for ln in text.splitlines() if not any(m in ln for m in markers)]
        return "\n".join(clean).strip(), len(leaked)

    def build_requirements_instruction(self, novel_id: str, chapter_num: int,
                                       plan: dict = None) -> str:
        """Build the complete, chapter-scoped requirement contract for the writer."""
        try:
            from ..requirements_contract import active_requirements

            req_store = getattr(self, "_req_store", None)
            if req_store is None:
                return ""
            plan_data = plan if isinstance(plan, dict) else {}
            if not plan_data:
                try:
                    loaded = self.memory.read("plan", novel_id)
                    plan_data = loaded if isinstance(loaded, dict) else {}
                except Exception:
                    plan_data = {}
            meta_req = (plan_data.get("_meta") or {}).get("requirements") or {}
            keys = [meta_req.get("store_key"), novel_id, plan_data.get("title")]
            requirements = {}
            for key in keys:
                if key:
                    requirements = req_store.get(str(key)) or {}
                    if requirements:
                        break
            if not requirements:
                return ""
            total = int(((plan_data.get("outline") or {}).get("total_chapters") or 0))
            active = active_requirements(requirements, int(chapter_num), total)
            if not active:
                return ""

            lines = [
                "## USER REQUIREMENT CONTRACT",
                "Use this as silent writing guidance. Never mention the contract or its IDs in the prose.",
            ]
            groups = (
                (
                    "HARD CHAPTER COMMITMENTS",
                    [item for item in active if item.get("kind") in {"chapter_event", "milestone"}],
                    "These events belong to this chapter or milestone window and must be delivered.",
                ),
                (
                    "BOOK INVARIANTS",
                    [item for item in active if item.get("kind") == "book_invariant"],
                    "Preserve these facts and prohibitions; do not force every positive fact into this chapter.",
                ),
                (
                    "SOFT PREFERENCES",
                    [item for item in active if item.get("kind") == "preference"],
                    "Follow when natural; these are quality preferences, not checklist events.",
                ),
            )
            emitted = 0
            for heading, items, guidance in groups:
                if not items:
                    continue
                lines.extend((f"### {heading}", guidance))
                for item in items:
                    if emitted >= 14:
                        break
                    emitted += 1
                    lines.append(
                        f"- [{item.get('id', '')}][{item.get('priority', 'P1')}] "
                        f"{item.get('title', '')}"
                    )
                    for label, value in (
                        ("description", item.get("description")),
                        ("must_include", ", ".join(map(str, (item.get("must_include") or [])[:5]))),
                        ("must_avoid", ", ".join(map(str, (item.get("must_avoid") or [])[:5]))),
                        ("generation_hint", item.get("generation_hint")),
                        ("quality_criteria", "; ".join(map(str, (item.get("quality_criteria") or [])[:4]))),
                    ):
                        if value:
                            lines.append(f"  {label}: {str(value)[:360]}")
            return "\n".join(lines)
        except Exception as exc:
            log.warning("Requirements contract build failed (non-fatal): %s", exc)
            return ""

    def _evaluate_chapter_requirements(self, novel_id: str, chapter_num: int,
                                       text: str, plan: dict | None = None) -> dict:
        """Persist deterministic requirement evidence after the final text mutation."""
        try:
            from ..requirements_contract import evaluate_requirements_against_text

            requirements = {}
            if hasattr(self, "_load_requirements_contract"):
                requirements, _ = self._load_requirements_contract(novel_id, plan)
            if not requirements:
                req_store = getattr(self, "_req_store", None)
                key = (plan or {}).get("title") or novel_id
                requirements = req_store.get(key) if req_store else {}
            total = int(((plan or {}).get("outline") or {}).get("total_chapters") or 0)
            report = evaluate_requirements_against_text(
                text, requirements, chapter_num=chapter_num, total_chapters=total
            )
            state = self.memory.get_novel_state(novel_id)
            state.setdefault("requirement_compliance", {})[str(chapter_num)] = report
            self.memory.save_novel_state(novel_id, state)
            return report
        except Exception as exc:
            log.warning("Chapter requirement evaluation skipped: %s", exc)
            return {"overall_status": "error", "error": str(exc), "results": []}

    _SCALE_KEYWORDS = (
        "成人", "亲密", "情欲", "尺度", "暧昧", "激情", "欲望", "肉体",
        "性感", "擦边", "香艳", "露骨", "床戏", "缠绵", "荷尔蒙",
        "成年", "恋人", "身体接触", "亲热", "18+", "18禁", "R18",
    )

    def _build_scale_instruction(self, novel_id: str, plan: dict = None) -> str:
        """v2.6: 检测用户需求/灵感中的尺度关键词 → 注入成熟题材写作指令

        实测：DeepSeek 默认（创作自由声明）输出偏保守；显式授权后
        能写到"擦边文学"尺度（衣物/亲吻/身体接触/感官细节）。
        这里把用户的尺度诉求转成模型可执行的指令。
        """
        import re as _re
        haystacks = []
        # 1) creative_input（创建时的灵感/要求全文）
        try:
            ci = (plan or {}).get("_meta", {}).get("creative_input", {}) or {}
            for _k, _v in ci.items():
                if isinstance(_v, str) and _v.strip():
                    haystacks.append(_v)
                elif isinstance(_v, (list, tuple)):
                    haystacks.extend(str(x) for x in _v if isinstance(x, str))
        except Exception:
            pass
        # 2) RequirementsStore 子任务全量（title/description/must_include）
        try:
            req_store = getattr(self, "_req_store", None)
            title = ""
            if plan and plan.get("title"):
                title = plan["title"]
            if title and req_store:
                reqs = req_store.get(title)
                if reqs:
                    for t in (reqs.get("subtasks", []) or [])[:30]:
                        haystacks.append(str(t.get("title", "")))
                        haystacks.append(str(t.get("description", "")))
                        haystacks.extend(str(x) for x in (t.get("must_include", []) or [])[:6])
        except Exception:
            pass

        joined = "\n".join(x for x in haystacks if x)
        if not joined:
            return ""
        hit = [k for k in self._SCALE_KEYWORDS if k in joined]
        if not hit:
            return ""

        log.info(f"Scale instruction injected (keywords: {hit[:6]})")
        return (
            "## 🎯 成熟题材指令（用户需求，必须执行）\n"
            "- 本章若涉及成年角色之间的亲密场景，必须正面写、写完整：不要用'一夜过去''次日清晨'跳过，不要淡化处理\n"
            "- 允许并鼓励：身体接触的温度与触感、亲吻与抚摸的推进过程、衣物的变化、呼吸与声音、欲望的张力\n"
            "- 具体到动作和细节：从哪个动作开始、手落在哪里、感官如何变化——画面感来自细节\n"
            "- 保持文学性：用意象与动作写身体语言，浓而不腻、露而不俗\n"
            "- 所有角色均为成年人\n"
        )


    async def atomic_generate_chapter_stream(
        self, novel_id: str, chapter_num: int, writing_mode: str = "webnovel",
        feedback: str = None,
    ) -> AsyncGenerator[dict, None]:
        """原子化生成章节：逐beat独立LLM调用 → 装配 → 评估
        
        与 generate_chapter_stream 的区别：
        - 传统: 1次LLM调用 → 整章2000字（趋同）
        - 原子化: 5-7次独立LLM调用 → 每beat 200-400字 → 装配（多样性爆炸）
        """
        novel_dir = self.memory.get_novel_dir(novel_id)
        lock_file = os.path.join(novel_dir, f".generating_{chapter_num:04d}.lock")
        
        # 并发锁（与generate_chapter_stream共享）—— PID 存活判定，不再按 300s 误删活跃锁
        if os.path.exists(lock_file):
            if not _gen_lock_is_stale(lock_file):
                yield {"type": "error", "message": f"第{chapter_num}章正在生成中"}
                return
            try:
                os.remove(lock_file)
            except OSError:
                pass
        
        _write_gen_lock(lock_file)
        
        try:
            # v2.7.5: 生成前懒补窗口内缺失摘要（历史/原子章节欠账）
            try:
                _bf = await self._backfill_missing_summaries(novel_id, chapter_num)
                if _bf:
                    yield {"type": "status", "message": f"补齐 {_bf} 章缺失的剧情摘要…"}
            except Exception as _bfe:
                log.warning(f"Backfill call failed (atomic, non-fatal): {_bfe}")

            plan = self.get_novel(novel_id)
            if not plan:
                yield {"type": "error", "message": f"❌ 小说 '{novel_id}' 不存在。请从书架选择有效的小说。"}
                return
            
            chapter_outline = self._find_chapter_outline(plan, chapter_num)
            if not chapter_outline:
                chapter_outline = {
                    "number": chapter_num, "title": f"第{chapter_num}章",
                    "summary": "继续推进主线", "emotion_curve": "平稳→紧张→悬念",
                    "characters": ["主角"], "hook": "留下悬念",
                    "target_words": config.DEFAULT_CHAPTER_WORDS,
                }
            
            # ── v2.8: 大纲细化——旧大纲只有一句话 summary 时，先扩成 scene_beats 事件契约 ──
            if self._chapter_needs_outline_expansion(chapter_outline):
                yield {"type": "status", "message": f"细化第{chapter_num}章大纲（生成事件节拍）..."}
                self._expand_chapter_outline(plan, chapter_outline, novel_id, chapter_num)
                # 重新构建上下文以纳入新生成的 scene_beats
                chapter_context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)
            else:
                chapter_context = None
            
            # ── v2.9 Phase 0: 生成章节蓝图 ──
            yield {"type": "status", "message": "生成章节蓝图..."}

            # 流派/风格提前定义（供 playbook/声音注入使用，v2.6: 修复 UnboundLocalError）
            genre = plan.get("genre", "玄幻")
            style = plan.get("style", "热血爽文")

            # 构建完整写作上下文（常规Writer用的五层上下文）
            if chapter_context is None:
                chapter_context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)
            
            # v2.4.7: 注入用户需求（章节正文此前完全丢失需求传导）
            try:
                _req_ctx = self.build_requirements_instruction(novel_id, chapter_num, plan)
                if _req_ctx:
                    chapter_context = chapter_context + "\n\n" + _req_ctx
            except Exception as rce:
                log.warning(f"Requirements injection skipped (non-fatal): {rce}")

            # v2.6: 注入成熟题材指令（用户需求含尺度关键词时生效）
            try:
                _scale_ctx = self._build_scale_instruction(novel_id, plan)
                if _scale_ctx:
                    chapter_context = chapter_context + "\n\n" + _scale_ctx
            except Exception as _sce:
                log.warning(f"Scale instruction injection skipped (atomic, non-fatal): {_sce}")

            # v2.51: 注入角色当前状态
            char_ctx3 = self.context_updater.get_context_for_writer(novel_id, chapter_num, self.memory)
            if char_ctx3:
                chapter_context = chapter_context + "\n\n" + char_ctx3

            # v2.3.6: 注入流派黄金法则（与普通路径一致）
            try:
                from ..genre_playbooks import build_playbook_context
                _pb_ctx = build_playbook_context(genre)
                if _pb_ctx:
                    chapter_context = chapter_context + "\n\n" + _pb_ctx
            except Exception as _gpe:
                log.warning(f"Playbook injection skipped (atomic): {_gpe}")

            # v2.3.6: 注入角色声音表（对话辨识度，解决角色同质化）
            try:
                from ..character_voices import build_voices_context
                _vctx = build_voices_context(
                    self.get_character_voices(novel_id),
                    chapter_outline.get("characters", []) or None,
                )
                if _vctx:
                    chapter_context = chapter_context + "\n\n" + _vctx

            except Exception as _vce:
                log.warning(f"Voices injection skipped (atomic): {_vce}")

            from ..context_budget import ContextSection, allocate_context
            chapter_context, _context_trimmed = allocate_context(
                [
                    ContextSection(
                        "本章大纲 · 硬约束",
                        self._hard_outline_text(chapter_outline),
                        priority=100,
                        required=True,
                    ),
                    ContextSection("写作上下文", chapter_context, priority=60),
                ],
                max_chars=max(4000, config.MAX_CONTEXT_TOKENS * 4),
            )
            if _context_trimmed:
                log.info("Context budget trimmed sections: %s", _context_trimmed)
            
            # 用LLM生成300字叙事蓝图
            blueprint = ""
            try:
                # v2.36: 提取写作指令放到蓝图prompt最前面，否则被chapter_context[:2000]截断
                outline_instr = ""
                instr_marker = "═══ 以下为写作元指令"
                if instr_marker in chapter_context:
                    instr_start = chapter_context.find(instr_marker)
                    # 找到写作指令的结束（下一个 ## section 或文件末尾）
                    remaining = chapter_context[instr_start:]
                    instr_end_marker = remaining.find("\n\n## ")
                    if instr_end_marker > 0:
                        outline_instr = remaining[:instr_end_marker].strip()
                    else:
                        outline_instr = remaining[:800].strip()
                    # AUDIT P0-1: 剥离 ═══ 标记行
                    outline_instr = "\n".join(
                        ln for ln in outline_instr.splitlines() if "═══" not in ln
                    ).strip()
                
                blueprint_prompt = f"""根据以下写作指令和大纲，写出本章的叙事蓝图。蓝图是一段200-300字的连贯叙事概要，严格遵循写作指令中的核心事件和章末钩子。

{outline_instr}

---
{chapter_context[:1500]}

只输出蓝图正文，不要标题。"""
                
                bp_resp = await asyncio.to_thread(self.client.chat.completions.create,
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是一位小说策划。把大纲转化为叙事蓝图。"},
                        {"role": "user", "content": blueprint_prompt},
                    ],
                    temperature=0.7,
                    max_tokens=600,
                )
                report_response_usage(bp_resp, model=self.model)
                blueprint = bp_resp.choices[0].message.content.strip()
                log.info(f"Blueprint generated for Ch{chapter_num}: {len(blueprint)} chars")
            except Exception as e:
                log.warning(f"Blueprint generation failed, using summary: {e}")
                blueprint = chapter_outline.get("summary", "继续推进主线剧情")
            
            yield {"type": "blueprint", "text": blueprint[:100] + "..."}
            
            # ── Phase 1: 拆解为 beat ──
            yield {"type": "status", "message": f"拆解第{chapter_num}章为节拍..."}
            
            is_first = (chapter_num == 1)
            
            is_climax = bool(chapter_outline.get("is_climax") or chapter_outline.get("climax"))
            chars = chapter_outline.get("characters", []) or []
            char_context = str(chapter_outline.get("character_context") or "")[:1200]
            decomposer = BeatDecomposer(seed=chapter_num * 100 + int(time.time()) % 100)
            beats = decomposer.decompose(
                chapter_outline, chapter_num,
                is_first_chapter=is_first,
                is_climax_chapter=is_climax,
                available_characters=chars,
            )
            
            yield {"type": "beats_decomposed", "count": len(beats),
                   "functions": [b.function for b in beats]}
            
            # ── Phase 2: 逐 beat 独立生成 ──
            # (genre/style 已在 Phase 0 定义)
            style_guide = _get_style_guide(style, genre)
            
            beats_text = []
            async for beat_result in self.atomic_writer.write_beats_stream(
                beats, char_context, style_guide, chapter_num,
                blueprint=blueprint, chapter_context=chapter_context  # v2.35: 全文传递，由beat prompt内部提取
            ):
                beats_text.append({
                    "index": beat_result["beat_index"],
                    "function": beat_result["beat_function"],
                    "text": beat_result["text"],
                    "temperature": beat_result["temperature"],
                })
                yield {
                    "type": "beat_progress",
                    "beat_index": beat_result["beat_index"],
                    "total_beats": len(beats),
                    "function": beat_result["beat_function"],
                }
                # v3.6.6: 逐 beat 流式输出正文——beat 完成即推送给前端（打字机效果），
                # 装配/规范化后由 done 事件触发前端拉取保存版替换，二者不冲突
                if beat_result.get("text"):
                    yield {"type": "text", "content": beat_result["text"] + "\n\n"}
            
            # ── Phase 3: 装配章节 ──
            yield {"type": "status", "message": "装配节拍..."}
            
            assembly = await asyncio.to_thread(
                self.beat_assembler.assemble,
                beats_text,
                chapter_outline.get("title", f"第{chapter_num}章"),
                chapter_num,
            )
            
            full_text = assembly["raw_text"]
            formatted = assembly["full_text"]
            
            # AUDIT P0-1: 指令泄漏检测（原子路径）
            full_text, _leak_count = self._strip_instruction_leaks(full_text)
            if _leak_count:
                log.warning(f"Instruction leak stripped (atomic) Ch{chapter_num}: {_leak_count} lines")
                formatted = f"# 第{chapter_num}章 {chapter_outline.get('title', f'第{chapter_num}章')}\n\n{full_text}"

            # ── v2.4.1: 段落规范化安全网 ──
            try:
                before_lines = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                full_text = _normalize_chapter_text(full_text)
                # v2.11: 确保结尾完整句子
                from ..writer import _ensure_complete_ending
                full_text = _ensure_complete_ending(full_text)
                formatted = f"# 第{chapter_num}章 {chapter_outline.get('title', f'第{chapter_num}章')}\n\n{full_text}"
                after_lines = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                if before_lines != after_lines:
                    log.info(f"Atomic paragraph normalize: ≤10char lines reduced {before_lines}→{after_lines}")
            except Exception as e:
                log.warning(f"Atomic paragraph normalize skipped: {e}")
            
            # ── Phase 4: 保存 ──
            from ..quality_gate import validate_chapter_text
            # v2.7.6: 超短篇超长兜底（装配/一致性可能扩写）
            from ..writer import _prepare_chapter_text_for_validation
            full_text = _prepare_chapter_text_for_validation(
                full_text, chapter_outline.get("target_words", config.DEFAULT_CHAPTER_WORDS)
            )
            formatted = f"# 第{chapter_num}章 {chapter_outline.get('title', f'第{chapter_num}章')}\n\n{full_text}"
            validate_chapter_text(
                full_text,
                target_words=chapter_outline.get("target_words", config.DEFAULT_CHAPTER_WORDS),
            )
            self.memory.write_chapter_draft(novel_id, chapter_num, formatted)
            
            # v2.40: 第一章生成后自动检测叙事人称
            if chapter_num == 1:
                self._save_narrative_pov(novel_id, full_text)

            # v2.42: 提取角色状态变化（原子路径）
            try:
                from ..character_state import CharacterStateTracker
                tracker = CharacterStateTracker(self.client, self.model, self.memory)
                asyncio.ensure_future(tracker.update_from_chapter(
                    novel_id, chapter_num, full_text
                ))
            except Exception as e:
                log.warning(f"Character state extraction failed (non-fatal): {e}")

            # ── 完整度验证 ──
            from ..writer import _check_truncation, _dedup_continuation
            is_trunc, reason = _check_truncation(full_text, chapter_outline.get("target_words", 2000))
            if is_trunc:
                log.warning(f"Atomic chapter {chapter_num} may be incomplete: {reason}")
                yield {"type": "warning", "message": f"⚠️ 原子模式生成的本章可能不完整（{reason}），建议使用常规模式重新生成此章"}
            
            # ── v2.10: 提取章节桥接数据（v2.15: 使用韧性客户端）──
            try:
                bridge = await asyncio.to_thread(
                    self.memory.extract_bridge_from_chapter,
                    full_text, chapter_num, chapter_outline,
                    client=self._resilient, model=self.model,
                )
                if bridge:
                    self.memory.save_bridge(novel_id, chapter_num, bridge)
                    log.info(f"ChapterBridge (atomic) saved for chapter {chapter_num}")
            except Exception as e:
                log.warning(f"ChapterBridge extraction failed in atomic (non-fatal): {e}")
            
            # ── Phase 5: 质量评估 ──
            yield {"type": "status", "message": "质量评估..."}
            
            evaluation = assembly["quality_report"]
            
            yield {
                "type": "evaluation",
                "overall_score": evaluation.get("overall_score", 0),
                "verdict": evaluation.get("verdict", ""),
                "distinct_1": evaluation.get("distinct_1", 0),
                "hook_strength": evaluation.get("hook", {}).get("strength", 0),
                "dopamine_density": evaluation.get("dopamine", {}).get("density_per_2000", 0),
                "ai_slop_score": evaluation.get("ai_slop", {}).get("score", 100),
                "coherence": evaluation.get("coherence", {}).get("verdict", ""),
                "duplicates_removed": assembly["duplicates_removed"],
                "transitions_added": len(assembly["transitions"]),
            }
            
            # ── done 事件（正文已逐 beat 流式输出过，这里不再重复 yield full_text，
            #    避免前端 writing.content 收到「逐beat文本 + 全文」两遍重复）──
            # 大纲合规校验：缺失节拍只做场景级局部修订。
            _outline_compliance_report = None
            try:
                async for _cev in self._outline_compliance_step(
                    novel_id, chapter_num, chapter_outline, plan, full_text,
                    batch_mode=False,
                ):
                    if _cev.get("type") == "outline_compliance":
                        _outline_compliance_report = _cev
                        if _cev.get("revised_text"):
                            full_text = _cev["revised_text"]
                            formatted = ("# 第{chapter_num}章 {chapter_title}\n\n{full_text}"
                                         .format(chapter_num=chapter_num,
                                                 chapter_title=chapter_outline.get(
                                                     "title", f"第{chapter_num}章"),
                                                 full_text=full_text))
                            self.memory.write_chapter_draft(novel_id, chapter_num, formatted)
                            self.memory.invalidate("state", novel_id)
                    yield _cev
            except Exception as _ce:
                log.warning(f"Outline compliance step skipped: {_ce}")

            # ── v2.0: 生成后自动设定一致性检查（正文内联标注）──
            try:
                _setting_annos = []
                async for _sev in self._setting_annotation_step(
                        novel_id, chapter_num, full_text):
                    if isinstance(_sev, list):
                        _setting_annos = _sev
                    else:
                        yield _sev
                if _setting_annos:
                    yield {"type": "outline_compliance_annotations",
                           "chapter": chapter_num, "annotations": _setting_annos}
            except Exception as _se:
                log.warning(f"Setting annotation step skipped: {_se}")

            from ..quality_gate import validate_chapter_text
            from ..writer import _prepare_chapter_text_for_validation
            full_text = _prepare_chapter_text_for_validation(
                full_text,
                chapter_outline.get("target_words", config.DEFAULT_CHAPTER_WORDS),
            )
            formatted = (
                f"# 第{chapter_num}章 "
                f"{chapter_outline.get('title', f'第{chapter_num}章')}\n\n{full_text}"
            )
            validate_chapter_text(
                full_text,
                target_words=chapter_outline.get("target_words", config.DEFAULT_CHAPTER_WORDS),
            )
            self.memory.write_chapter_draft(novel_id, chapter_num, formatted)
            _requirement_report = self._evaluate_chapter_requirements(
                novel_id, chapter_num, full_text, plan
            )
            yield {"type": "requirement_compliance", "chapter": chapter_num, **_requirement_report}
            _final_quality = await asyncio.to_thread(
                PacingChecker(self.client, self.model).quick_quality_check,
                full_text,
                fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
            )
            _narrative_quality = await asyncio.to_thread(
                evaluate_chapter, full_text, None, None, chapter_outline, genre
            )
            _final_quality = _merge_quality_with_narrative(
                _final_quality, _narrative_quality
            )
            _final_quality_record = _save_final_quality_report(
                self.memory,
                novel_id,
                chapter_num,
                {**_final_quality, "text": full_text},
                stage="post_outline_compliance",
            )
            yield {"type": "final_quality", **_final_quality_record}

            # Both drafting strategies share this hard gate and final committer.
            _completion = self._finalize_chapter_candidate(
                novel_id,
                chapter_num,
                full_text,
                formatted,
                _requirement_report,
                _final_quality_record,
                _outline_compliance_report,
            )
            if _completion["status"] == "needs_revision":
                yield {
                    "type": "needs_revision",
                    "message": _completion["reason"],
                    **_completion,
                }
                return

            # ── v2.7.5: 原子化路径补每章自动摘要（与 generate_chapter_stream 对齐）──
            # 此前 atomic 路径完全无摘要逻辑 → 原子章节不进 summaries → L2c
            # 事件清单 fallback 大纲计划（失真）。此处补上与标准路径一致的
            # summarize + check_and_compress。
            try:
                chapter_text = self.get_chapter(novel_id, chapter_num) or full_text
                if chapter_text and len(chapter_text) > 200:
                    summary_result = await asyncio.to_thread(
                        self.chapter_summarizer.summarize_chapter,
                        chapter_num, chapter_text
                    )
                    if summary_result:
                        def _add_summary(gs):
                            if not isinstance(gs, dict):
                                gs = {}
                            gs.setdefault("summaries", {})[str(chapter_num)] = summary_result
                            return gs
                        self.memory.update("global_state", novel_id, _add_summary)
                        log.info(f"Auto-summary for Ch{chapter_num} (atomic): {len(summary_result.get('summary',''))} chars")
            except Exception as e:
                log.warning(f"Auto-summary for Ch{chapter_num} (atomic) skipped: {e}")

            # 渐进式摘要压缩（每8章间隔，与标准路径一致）
            try:
                compress_result = check_and_compress(
                    self.memory, novel_id, chapter_num, self.chapter_summarizer
                )
                if compress_result:
                    yield {"type": "compression", "chapters_summarized": len(compress_result)}
            except Exception as e:
                log.warning(f"Auto-compression skipped (atomic): {e}")

            yield {"type": "done", "content": formatted, "chapter_num": chapter_num,
                   "atomic": True, "beat_count": len(beats)}
            
            
        except Exception as e:
            log.exception(f"Atomic chapter generation failed: {e}")
            _preserved = self._preserve_generation_failure(
                novel_id,
                chapter_num,
                locals().get("full_text", ""),
                locals().get("formatted", ""),
                e,
            )
            if _preserved:
                yield {"type": "needs_revision", "message": _preserved["reason"], **_preserved}
            else:
                yield {"type": "error", "message": f"章节生成失败：{e}"}
        finally:
            try:
                if os.path.exists(lock_file):
                    os.remove(lock_file)
            except OSError:
                pass

    async def _backfill_missing_summaries(self, novel_id: str, chapter_num: int) -> int:
        """v2.7.5: 生成前懒补「事件清单窗口内缺失摘要」的章节

        背景: 原子化路径此前没有摘要逻辑，且历史章节（自动摘要机制上线前
        生成）永远缺摘要 → L2c 已发生事件清单 fallback 到大纲「计划」内容，
        防重复依赖失真数据。此方法在生成前补齐窗口内缺失的摘要，让事件清单
        使用真实发生内容。

        窗口: L2c 覆盖最近 3 章 + 最近 15 早章 → [chapter-18, chapter-1]，
        这里保险起见检查最近 19 章。从最新往前补（最新章在窗口内待最久，
        价值最大），上限 5 章/次避免拖慢首章（旧书首次补完即不再缺）。

        Returns: 本次补齐的章节数
        """
        if chapter_num <= 1:
            return 0
        try:
            state = self.memory.read("global_state", novel_id) or {}
            summaries = state.get("summaries", {}) if isinstance(state, dict) else {}
            disk = set(self.memory.scan_chapters(novel_id))
            missing = []
            for ch in range(max(1, chapter_num - 19), chapter_num):
                if str(ch) not in summaries and ch in disk:
                    missing.append(ch)
            if not missing:
                return 0
            missing.sort(reverse=True)  # 最新优先
            contents = {}
            for ch in missing[:5]:
                content = self.memory.read_chapter(novel_id, ch)
                if content and len(content) > 200:
                    contents[ch] = content
            if not contents:
                return 0
            results = await asyncio.to_thread(
                self.chapter_summarizer.summarize_batch, contents, novel_id
            )
            if not results:
                return 0

            def _merge(gs):
                if not isinstance(gs, dict):
                    gs = {}
                gs.setdefault("summaries", {})
                for ch_num, summary in results.items():
                    gs["summaries"][str(ch_num)] = summary
                return gs

            self.memory.update("global_state", novel_id, _merge)
            log.info(
                f"Summary backfill for Ch{chapter_num}: {len(results)} chapters "
                f"{sorted(contents.keys())}"
            )
            return len(results)
        except Exception as e:
            log.warning(f"Summary backfill skipped (Ch{chapter_num}): {e}")
            return 0

    async def _outline_compliance_step(
        self, novel_id: str, chapter_num: int, chapter_outline: dict,
        plan: dict, full_text: str, batch_mode: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """大纲合规校验：核对本章是否落地核心事件/节拍/角色。

        缺失核心节拍时只允许替换对应局部场景，修订后必须重新校验；
        不再将一段“补丁”追加到章末。

        Yields: {type: outline_compliance, chapter, pct, passed, level,
                 missing, checked, patched_text, message}
        """
        try:
            from ..outline_compliance import OutlineComplianceChecker
        except Exception as e:
            log.warning(f"outline_compliance import failed: {e}")
            yield {"type": "outline_compliance", "chapter": chapter_num,
                   "ok": False, "error": str(e)[:80]}
            return
        checker = OutlineComplianceChecker(self.client, self.model)
        yield {"type": "status", "message": "📋 正在校验大纲合规（核心事件/节拍落地）…"}
        try:
            report = await asyncio.to_thread(
                checker.check_chapter, full_text, chapter_outline,
                run_deep=not batch_mode)
        except Exception as e:
            log.warning(f"Outline compliance check failed: {e}")
            yield {"type": "outline_compliance", "chapter": chapter_num,
                   "ok": False, "error": str(e)[:80]}
            return
        pre_edit_pct = None
        revised_text = ""
        scene_edits = []
        revision_result = "not_needed"
        pct = report.get("pct", 100)
        level = report.get("level", "ok")
        missing = report.get("missing", [])
        critical_missing = report.get("critical_missing", [])
        repair_items = critical_missing or (missing if level == "fail" else [])

        if repair_items and not batch_mode:
            yield {"type": "status", "message": "✍️ 正在将缺失事件修回对应场景…"}
            try:
                revision = await asyncio.to_thread(
                    checker.revise_missing_scenes,
                    full_text,
                    repair_items,
                    chapter_outline,
                )
                if revision.get("applied") and revision.get("text"):
                    candidate_text = revision["text"]
                    post_report = await asyncio.to_thread(
                        checker.check_chapter,
                        candidate_text,
                        chapter_outline,
                        run_deep=True,
                    )
                    post_pct = post_report.get("pct", 0)
                    if post_report.get("passed") or post_pct > pct:
                        pre_edit_pct = pct
                        revised_text = candidate_text
                        scene_edits = revision.get("edits", [])
                        report = post_report
                        pct = post_pct
                        level = report.get("level", "partial")
                        missing = report.get("missing", [])
                        critical_missing = report.get("critical_missing", [])
                        revision_result = "applied"
                        log.info(
                            "Outline scene revision Ch%s: %s%% -> %s%% (%s scenes)",
                            chapter_num, pre_edit_pct, pct, len(scene_edits),
                        )
                    else:
                        revision_result = "rejected_no_improvement"
                else:
                    revision_result = revision.get("reason", "revision_rejected")
            except Exception as pe:
                revision_result = "revision_error"
                log.warning(f"Outline scene revision failed: {pe}")

        passed = report.get("passed", True)
        missing_labels = [
            f"{m.get('label', '')}: {str(m.get('needle', ''))[:40]}" for m in missing]
        # 只保存局部修订并复检后的最终合规结果。
        try:
            state = self.memory.get_novel_state(novel_id)
            state.setdefault("outline_compliance", {})[str(chapter_num)] = {
                "pct": pct,
                "level": level,
                "passed": passed,
                "missing": missing_labels,
                "revision_result": revision_result,
                "scene_edits": scene_edits,
                "pre_edit_pct": pre_edit_pct,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.memory.save_novel_state(novel_id, state)
        except Exception as e:
            log.warning(f"outline compliance report save failed: {e}")

        if level == "ok":
            message = f"📋 大纲合规 ✓ {pct}%"
        elif revision_result == "applied":
            message = f"✍️ 场景级修订后大纲合规 {pct}%，仍缺失 {len(missing)} 项"
        elif level == "partial":
            message = f"⚠️ 大纲合规 {pct}%，缺失 {len(missing)} 项"
        else:
            message = f"❌ 大纲合规 {pct}%，场景级修订未通过复检"
        yield {"type": "outline_compliance",
               "chapter": chapter_num, "pct": pct, "passed": passed,
               "level": level, "checked": report.get("results", []),
               "missing": missing_labels, "patched_text": "",
               "critical_missing": critical_missing,
               "revised_text": revised_text, "scene_edits": scene_edits,
               "pre_edit_pct": pre_edit_pct, "revision_result": revision_result,
               "message": message}

    async def _setting_annotation_step(self, novel_id: str, chapter_num: int,
                                       full_text: str) -> list:
        """v2.0: 生成后自动设定一致性检查（本章 vs 前文），返回 annotations。

        与大纲合规并行，输出可直接供前端标注的格式。
        """
        try:
            from ..settings_scanner import SettingsScanner
            scanner = SettingsScanner(self.client, self.model)
            novel_dir = self.memory.get_novel_dir(novel_id)
            annotations = await asyncio.to_thread(
                scanner.check_chapter_against_prior,
                novel_id, novel_dir, chapter_num, full_text)
            log.info(f"Ch{chapter_num} setting check done: {len(annotations)} annotations")
            if annotations:
                yield {"type": "status",
                       "message": f"⚠️ 发现 {len(annotations)} 处设定疑点（已在正文标注）"}
            yield annotations
        except Exception as e:
            log.warning(f"Setting annotation step failed (non-fatal): {e}")
            yield []

    async def generate_chapter_stream(
        self, novel_id: str, chapter_num: int, writing_mode: str = "webnovel",
        feedback: str = None, batch_mode: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """流式生成章节 — 前端可实时显示打字效果
        
        Args:
            feedback: 用户修改意见（用于重生成，不改大纲结构）
            batch_mode: v2.6 批量模式 — 速度优先：
                - Humanizer 质量阈值 70→60，改写后跳过再检测（省1-2次LLM）
                - 一致性校验只跑 L1 规则（省1次LLM）
                - 桥接/ContextUpdater 后台化（省2次关键路径LLM）
                单章模式保持全量校验（质量优先）
        """
        # ── 并发锁：防止同一章被两个Tab同时生成（PID 存活判定）──
        novel_dir = self.memory.get_novel_dir(novel_id)
        lock_file = os.path.join(novel_dir, f".generating_{chapter_num:04d}.lock")
        if os.path.exists(lock_file):
            if not _gen_lock_is_stale(lock_file):
                try:
                    lock_age = time.time() - os.path.getmtime(lock_file)
                except OSError:
                    lock_age = 0
                yield {"type": "error", "message": f"⏳ 第{chapter_num}章正在生成中（已运行{int(lock_age)}秒），请等待完成后再试"}
                return
            try:
                log.warning(f"Stale lock file for chapter {chapter_num}, removing")
                os.remove(lock_file)
            except OSError:
                pass
        
        # 写入锁文件（含 PID）
        _write_gen_lock(lock_file)
        
        try:
            # v2.7.5: 生成前懒补窗口内缺失摘要（历史/原子章节欠账），
            # 让 L2c 事件清单使用真实发生内容而非大纲计划
            try:
                _bf = await self._backfill_missing_summaries(novel_id, chapter_num)
                if _bf:
                    yield {"type": "status", "message": f"补齐 {_bf} 章缺失的剧情摘要…"}
            except Exception as _bfe:
                log.warning(f"Backfill call failed (non-fatal): {_bfe}")

            plan = self.get_novel(novel_id)
            if not plan:
                yield {"type": "error", "message": f"❌ 小说 '{novel_id}' 不存在。请从书架选择有效的小说。"}
                return

            # 找到本章大纲
            chapter_outline = self._find_chapter_outline(plan, chapter_num)
            if not chapter_outline:
                # 兜底：构造一个基础大纲（防止 DeepSeek JSON 结构异常导致全流程挂掉）
                log.warning(f"Chapter {chapter_num} outline not found in plan, using fallback")
                # AUDIT P1-5: 兜底基于前文自动推断，不再是「继续推进主线剧情发展」空壳
                _fb_summary = "推进主线剧情：主角直面新的挑战，冲突升级，并自然收束本章。"
                _fb_hook = "章末局势急转直下，留下新的悬念。"
                try:
                    if chapter_num > 1:
                        _prev_text = self.memory.read_chapter(novel_id, chapter_num - 1)
                        if _prev_text:
                            _prev_tail = _prev_text.strip()[-300:]
                            _prev_lines = [l for l in _prev_tail.splitlines() if l.strip()]
                            _prev_last = (_prev_lines[-1] if _prev_lines else _prev_tail)[:80]
                            _fb_summary = (f"紧接上一章结尾继续：{_prev_last}。"
                                           f"在此基础上推进核心矛盾、展开新冲突，并自然收束本章。")
                            _fb_hook = f"围绕上一章结尾「{_prev_last[-40:]}」的悬念展开，章末制造新的反转钩子。"
                except Exception:
                    pass
                chapter_outline = {
                    "number": chapter_num,
                    "title": f"第{chapter_num}章",
                    "summary": _fb_summary,
                    "emotion_curve": "平稳→紧张→悬念",
                    "characters": ["主角"],
                    "hook": _fb_hook,
                    "target_words": config.DEFAULT_CHAPTER_WORDS,
                }

            # ── v2.8: 大纲细化——旧大纲只有一句话 summary 时，先扩成 scene_beats 事件契约 ──
            if self._chapter_needs_outline_expansion(chapter_outline):
                yield {"type": "status", "message": f"细化第{chapter_num}章大纲（生成事件节拍）..."}
                self._expand_chapter_outline(plan, chapter_outline, novel_id, chapter_num)

            # 组装上下文
            context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)

            # v2.4.7: 注入用户需求（章节正文此前完全丢失需求传导）
            try:
                req_ctx = self.build_requirements_instruction(novel_id, chapter_num, plan)
                if req_ctx:
                    context = context + "\n\n" + req_ctx
            except Exception as rce:
                log.warning(f"Requirements injection skipped (non-fatal): {rce}")

            # v2.6: 注入成熟题材指令（用户需求含尺度关键词时生效）
            try:
                scale_ctx = self._build_scale_instruction(novel_id, plan)
                if scale_ctx:
                    context = context + "\n\n" + scale_ctx
            except Exception as sce:
                log.warning(f"Scale instruction injection skipped (non-fatal): {sce}")

            # v2.51: 注入角色当前状态 — 写作前读取每个角色的位置/情绪/健康/目标
            char_ctx = self.context_updater.get_context_for_writer(novel_id, chapter_num, self.memory)
            if char_ctx:
                context = context + "\n\n" + char_ctx

            # 获取创作参数（提前定义，供 playbook/角色声音等注入使用）
            genre = plan.get("genre", "玄幻")
            style = plan.get("style", "热血爽文")

            # v2.3.5: 注入用户偏好指令（反馈闭环，≥3 条反馈才生效）
            try:
                pref_ctx = self.build_preference_instruction(novel_id)
                if pref_ctx:
                    context = context + "\n\n" + pref_ctx
            except Exception as pe:
                log.warning(f"Preference injection skipped: {pe}")

            # v2.3.6: 注入流派黄金法则（网文技法研究提炼）
            try:
                from ..genre_playbooks import build_playbook_context
                pb_ctx = build_playbook_context(genre)
                if pb_ctx:
                    context = context + "\n\n" + pb_ctx
            except Exception as gpe:
                log.warning(f"Playbook injection skipped: {gpe}")

            # v2.3.6: 注入角色声音表（对话辨识度，解决角色同质化）
            try:
                from ..character_voices import build_voices_context
                vctx = build_voices_context(
                    self.get_character_voices(novel_id),
                    chapter_outline.get("characters", []) or None,
                )
                if vctx:
                    context = context + "\n\n" + vctx
            except Exception as vce:
                log.warning(f"Voices injection skipped: {vce}")
            log.info(f"Character state injected into writer context ({len(char_ctx)} chars)")

            # 高潮标记由正常线大纲显式提供，不再依赖剧情图谱。
            if chapter_outline.get("is_climax") or chapter_outline.get("climax"):
                try:
                    twist = self.twist_designer.design_chapter_twist(
                        chapter_num=chapter_num,
                        plan=plan,
                        chapter_outline=chapter_outline,
                    )
                    if twist and twist.get("twist_text"):
                        context += "\n\n## 本章反转设计\n" + str(twist["twist_text"])[:1200]
                        log.info(f"Twist injected for climax chapter {chapter_num}")
                except Exception as e:
                    log.warning(f"Twist injection skipped: {e}")
            # 注入修改意见（重生成场景）
            if feedback and feedback.strip():
                context = (
                    f"【重写指令】以下是上一版存在的问题，请在重写时修正。\n"
                    f"注意：章节大纲、核心事件、出场角色、scene_beats 和结局钩子不变！\n"
                    f"只改进行文质量和具体表达，不改变叙事结构。\n\n"
                    f"用户修改意见：{feedback.strip()}\n\n"
                    f"---\n\n{context}"
                )

            # 获取创作参数（genre/style 已在函数前部定义）
            target_words = chapter_outline.get("target_words", config.DEFAULT_CHAPTER_WORDS)
            # v2.7: 快餐模式字数自适应 — 2500字/章(短剧化节奏)
            if plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False):
                target_words = 2500

            # AUDIT P1-1: 普通 Writer 注入节拍骨架 — 与原子化路径共享节奏，
            # 让单次生成也按 beat 推进，而非只靠 outline summary 一句话
            try:
                _beats = self._decompose_chapter_beats(
                    chapter_outline, chapter_num,
                    self.memory.get_novel_dir(novel_id),
                    is_first=(chapter_num == 1),
                )
                if _beats:
                    context += "\n\n" + self._format_beats_instruction(_beats)
                    log.info(f"Beats injected into writer context (Ch{chapter_num}, {len(_beats)} beats)")
            except Exception as _be:
                log.warning(f"Beat injection skipped (non-fatal): {_be}")

            from ..context_budget import ContextSection, allocate_context
            context, _context_trimmed = allocate_context(
                [
                    ContextSection(
                        "本章大纲 · 硬约束",
                        self._hard_outline_text(chapter_outline),
                        priority=100,
                        required=True,
                    ),
                    ContextSection("用户反馈", feedback or "", priority=95, required=bool(feedback)),
                    ContextSection("写作上下文", context, priority=60),
                ],
                max_chars=max(4000, config.MAX_CONTEXT_TOKENS * 4),
            )
            if _context_trimmed:
                log.info("Context budget trimmed sections: %s", _context_trimmed)

            # 流式生成 + 增量保存（每500字写盘，防断线丢内容）
            full_text = ""
            last_save_len = 0
            chapter_title = chapter_outline.get("title", f"第{chapter_num}章")
            async for text in self.writer.write_stream(
                context=context,
                genre=genre,
                style=style,
                target_words=target_words,
                writing_mode=writing_mode,
                normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False), fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
                chapter_outline=chapter_outline,  # v2.12: 传递大纲用于两阶段结尾生成
            ):
                full_text += text
                # 每500字增量保存一次
                if len(full_text) - last_save_len >= 500:
                    try:
                        formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{full_text}"
                        self.memory.write_chapter_draft(novel_id, chapter_num, formatted)
                        last_save_len = len(full_text)
                    except Exception as e:
                        log.warning(f"Incremental save failed (non-fatal): {e}")
                yield {"type": "text", "content": text}

            # AUDIT P0-1: 指令泄漏检测 — 正文中不得出现元指令标记
            full_text, _leak_count = self._strip_instruction_leaks(full_text)
            if _leak_count:
                log.warning(f"Instruction leak stripped from Ch{chapter_num}: {_leak_count} lines")
                yield {"type": "warning", "message": "已清理正文中的指令泄漏文本"}

            # ── v2.27: 先跑质量门（本地规则，毫秒级），用结果控制后续处理 ──
            quality_report = None
            try:
                is_fast_food = plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False)
                checker = PacingChecker(self.client, self.model)
                qr = await asyncio.to_thread(
                    checker.quick_quality_check,
                    full_text,
                    fast_food=is_fast_food,
                )
                _narrative_probe = await asyncio.to_thread(
                    evaluate_chapter, full_text, None, None, chapter_outline, genre
                )
                qr = _merge_quality_with_narrative(qr, _narrative_probe)
                quality_report = qr
                log.info(f"Quality gate: score={qr['score']}, issues={len(qr['issues'])}")

                if qr["score"] < 40 and qr["issues"]:
                    issues_text = "; ".join(qr["issues"])
                    log.warning(f"Quality gate CRITICAL (score={qr['score']}): {issues_text}")
                    _before_text = full_text

                    # AUDIT P0-2: LLM 复核防误杀 — 规则初筛命中后由 LLM 确认是否真需重写
                    llm_confirm = None
                    if not batch_mode:
                        try:
                            _resp = await asyncio.to_thread(self.client.chat.completions.create,
                                model=self.model,
                                messages=[
                                    {"role": "system", "content": "你是一位严格的网络小说编辑。判断规则引擎的检测结果是否属实、文本是否真的需要重写。只输出JSON。"},
                                    {"role": "user", "content": (
                                        f"规则引擎认为本章存在以下问题：{issues_text}\n\n"
                                        f"章节开头1200字：\n{full_text[:1200]}\n\n"
                                        '请判断：这些问题是否真实存在且严重影响阅读？如果问题被夸大，'
                                        '或属于正常文风（例如爽文打脸场景节奏快、对话多是优点），请给高分并 needs_rewrite=false。'
                                        '只输出JSON: {"score": 0-100 可接受度评分, "needs_rewrite": true/false, "reason": "一句话理由"}'
                                    )},
                                ],
                                temperature=0.2,
                                max_tokens=250,
                            )
                            report_response_usage(_resp, model=self.model)
                            _content = (_resp.choices[0].message.content or "").strip()
                            try:
                                llm_confirm = json.loads(_content)
                            except Exception:
                                _mj = re.search(r"\{.*\}", _content, re.S)
                                llm_confirm = json.loads(_mj.group(0)) if _mj else None
                        except Exception as _ce:
                            log.warning(f"Quality gate LLM confirm failed, default to rewrite: {_ce}")

                    _llm_accept = False
                    _llm_score = 0
                    if llm_confirm and isinstance(llm_confirm, dict):
                        try:
                            _llm_score = float(llm_confirm.get("score", 0))
                        except (TypeError, ValueError):
                            _llm_score = 0
                        _llm_accept = (llm_confirm.get("needs_rewrite") is False and _llm_score >= 55)

                    if _llm_accept:
                        log.info(f"Quality gate LLM override: 保留原文 (llm_score={_llm_score})")
                        quality_report = qr
                        yield {"type": "quality_minor", "score": qr["score"], "issues": qr["issues"],
                               "note": f"LLM复核通过，保留原文（{llm_confirm.get('reason', '')}）"}
                    else:
                        yield {"type": "quality_warning", "score": qr["score"], "issues": qr["issues"],
                               "message": f"📝 严重质量瑕疵（评分 {qr['score']}），自动续写优化..."}
                        yield {"type": "status", "message": "✍️ 质量门未过，正在优化改写（约1分钟）…"}

                        # v2.49: 用 multi-turn history 续写修复，而不是从头重写
                        retry_text = ""
                        async for text in self.writer.write_stream(
                            context=context + f"\n\n⚠️ 上一版质量不合格（评分{qr['score']}）。以下问题必须修正：{issues_text}\n\n【已生成内容参考】\n{full_text[-500:]}",
                            genre=genre, style=style, target_words=target_words, writing_mode=writing_mode,
                            normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False),
                            fast_food=is_fast_food,
                            chapter_outline=chapter_outline, skip_ending=True,
                        ):
                            retry_text += text

                        if retry_text and len(retry_text) > len(full_text) * 0.6:
                            qr2 = checker.quick_quality_check(retry_text, fast_food=is_fast_food)
                            qr2 = _merge_quality_with_narrative(
                                qr2,
                                evaluate_chapter(retry_text, None, None, chapter_outline, genre),
                            )
                            if qr2["score"] > qr["score"] + 10 or qr2["score"] >= 60:
                                full_text = retry_text
                                quality_report = qr2
                                log.info(f"Quality gate retry PASSED: {qr['score']} → {qr2['score']}")
                                yield {"type": "quality_retry", "score_before": qr["score"], "score_after": qr2["score"]}
                            else:
                                log.warning("Quality gate retry no improvement, keeping original")
                        else:
                            log.warning("Quality gate retry text too short, keeping original")

                    # AUDIT P0-2: 误杀率数据采集（积累 before/after 数据集供阈值调优）
                    try:
                        _qlog = os.path.join(self.memory.get_novel_dir(novel_id), "quality_gate_log.jsonl")
                        with open(_qlog, "a", encoding="utf-8") as _qf:
                            _qf.write(json.dumps({
                                "ts": time.time(),
                                "novel": novel_id,
                                "chapter": chapter_num,
                                "score_before": qr["score"],
                                "score_after": quality_report.get("score") if quality_report else None,
                                "rewritten": full_text != _before_text,
                                "llm_accept": _llm_accept,
                                "issues": qr["issues"][:5],
                            }, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                elif 40 <= qr["score"] < 60 and qr["issues"]:
                    log.info(f"Quality gate MILD (score={qr['score']}): {len(qr['issues'])} minor issues, accepting as-is")
                    yield {"type": "quality_minor", "score": qr["score"], "issues": qr["issues"]}
                    
            except Exception as e:
                log.warning(f"Quality gate skipped: {e}")

            # ── v2.27: Humanizer 移到质量门之后 — 用分数决定是否跑 ──
            ai_report = None
            # v2.6: 批量模式阈值 70→60（更多章节直接通过，跳过 humanizer 2-3次LLM）
            _quality_ok = quality_report and quality_report.get("score", 0) >= (60 if batch_mode else 70)  # v2.3.5: 50→70 提高润色覆盖
            # v2.7.6: 超短篇（小目标<1500字）跳过 Humanizer——重写不受字数约束，
            # 常把 800 字目标扩写到 1800+，且短内容 AI 味影响小
            _flash_target = int(target_words) < 1500
            if not _quality_ok and not _flash_target:
                yield {"type": "status", "message": "🎨 正在消除 AI 痕迹、润色文笔…"}
                try:
                    from ..ai_detector import AIDetector, HumanRewriter, humanize_pipeline
                    detector = AIDetector(self.client, self.model)
                    rewriter = HumanRewriter(self.client, self.model)
                    
                    chapter_summary = chapter_outline.get("summary", "") or chapter_outline.get("title", "")
                    _ending_saved = _extract_key_ending(full_text)
                    result = await asyncio.to_thread(
                        humanize_pipeline, full_text, detector, rewriter,
                        scene_desc=chapter_summary,
                        target_length=target_words,
                        min_score_threshold=(50 if batch_mode else 40),  # v2.6: 批量模式更宽松，AI味<50不改写
                        skip_verify=batch_mode,  # v2.6: 批量模式跳过改写后再检测
                    )
                    if result["rewritten"]:
                        ai_report = result
                        rewritten = result["text"]
                        if len(rewritten) > 500 and _ending_saved:
                            protected = _protect_ending_semantic(full_text, rewritten, _ending_saved)
                            full_text = protected if protected != rewritten else rewritten
                        else:
                            full_text = rewritten
                        log.info(f"AI Humanizer: score {ai_report['ai_score_before']}→{ai_report.get('ai_score_after','?')}, rewritten")
                        yield {"type": "ai_report", 
                               "score_before": ai_report["ai_score_before"],
                               "score_after": ai_report.get("ai_score_after", ai_report["ai_score_before"]),
                               "rewritten": True}
                except Exception as e:
                    log.warning(f"AI Humanizer skipped: {e}")
            else:
                log.info(f"AI Humanizer skipped: quality score={quality_report['score']}≥50, text already human-like")

            # ── v2.11: 确保结尾是完整句子 ──
            from ..writer import _ensure_complete_ending
            original_len = len(full_text)
            full_text = _ensure_complete_ending(full_text)
            if len(full_text) != original_len:
                log.info(f"Ending trimmed from {original_len} to {len(full_text)} chars (removed incomplete sentence)")

            # ── v2.31: 后处理清除AI惯用语（prompt拦不住的硬过滤）──
            _ai_replacements = {
                '嘴角勾起一抹诡异的弧度': '冷冷一笑',
                '嘴角勾起一抹冷笑': '冷笑一声',
                '嘴角勾起一抹弧度': '笑了笑',
                '嘴角勾起': '嘴角微动',
                '瞳孔骤缩': '眼神一凝',
                '瞳孔猛地一缩': '目光一沉',
                '浑身一震': '身体一僵',
                '脊背发凉': '汗毛倒竖',
                '后背一凉': '汗毛倒竖',
                '倒吸一口凉气': '呼吸一紧',
                '眼底闪过一丝寒光': '眼神冷下来',
                '眼底闪过一丝': '眼神中透着',
                '眸光': '目光',
                '眸色': '眼神',
                '眸子': '眼睛',
            }
            _replaced_count = 0
            for old, new in _ai_replacements.items():
                if old in full_text:
                    full_text = full_text.replace(old, new)
                    _replaced_count += 1
            if _replaced_count:
                log.info(f"AI cliché filter: {_replaced_count} replacements")

            # ── v2.4.1: 段落规范化安全网（必须在 Humanizer 和质量门之后执行）──
            try:
                before_short = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                full_text = _normalize_chapter_text(full_text)
                after_short = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                if before_short != after_short:
                    log.info(f"Paragraph normalize: short fragments {before_short}→{after_short}")
            except Exception as e:
                log.warning(f"Paragraph normalize skipped: {e}")

            # ── v3.8 (AUDIT P1-3): 章末钩子 LLM 判断 — 规则初筛未通过时确认并局部强化 ──
            if not batch_mode and len(full_text) >= 1500:
                try:
                    _hook_kws = ["突然", "忽然", "那一刻", "奇怪", "竟然", "没想到", "却见", "就在这时", "……", "？", "?"]
                    if not any(k in full_text[-100:] for k in _hook_kws):
                        _hook_res = await asyncio.to_thread(
                            self.writer.assess_and_enhance_hook,
                            full_text,
                            min_rewrite_score=35,
                        )
                        if _hook_res.get("rewritten") and _hook_res.get("text"):
                            full_text = _hook_res["text"]
                            log.info(f"Hook enhanced for Ch{chapter_num} (score={_hook_res.get('score')})")
                            yield {"type": "hook_enhanced", "score": _hook_res.get("score"),
                                   "message": "🔗 章末钩子偏弱，已自动强化结尾悬念"}
                        elif _hook_res.get("assessed"):
                            log.info(f"Hook assessment: score={_hook_res.get('score')}, no rewrite needed")
                except Exception as _he:
                    log.warning(f"Hook assessment skipped (non-fatal): {_he}")

            # 最终保存章节（覆盖增量保存的临时文件）
            # v2.31: 去除 Writer 自动生成的所有标题行（# 开头且含章节号），防止多标题
            _text_to_save = full_text
            _lines = _text_to_save.split('\n')
            while _lines and _lines[0].strip().startswith('#') and (
                str(chapter_num) in _lines[0] or
                any(w in _lines[0] for w in ['第一章','第二章','第三章','第四章','第五章','第六章','第七章','第八章','第九章','第十章',
                                              '第一回','第二回','第三回','第四回','第五回','第六回','第七回','第八回','第九回','第十回'])
            ):
                _lines.pop(0)
                # 跳过标题后的空行
                while _lines and not _lines[0].strip():
                    _lines.pop(0)
            _text_to_save = '\n'.join(_lines)
            from ..quality_gate import validate_chapter_text
            validate_chapter_text(_text_to_save, target_words=target_words)
            formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{_text_to_save}"
            self.memory.write_chapter_draft(novel_id, chapter_num, formatted)

            # v2.40: 第一章生成后自动检测叙事人称
            if chapter_num == 1:
                self._save_narrative_pov(novel_id, full_text)

            # v2.42: 提取角色状态变化
            try:
                from ..character_state import CharacterStateTracker
                tracker = CharacterStateTracker(self.client, self.model, self.memory)
                asyncio.ensure_future(tracker.update_from_chapter(
                    novel_id, chapter_num, full_text
                ))
            except Exception as e:
                log.warning(f"Character state extraction failed (non-fatal): {e}")

            # ── v2.10: 提取章节桥接数据 → 保证下章接续（v2.15: 使用韧性客户端）──
            # v2.6: 批量模式后台化（流水线：下一章启动前 await_pending_bridge 衔接）
            try:
                def _extract_bridge():
                    bridge = self.memory.extract_bridge_from_chapter(
                        full_text, chapter_num, chapter_outline,
                        client=self._resilient, model=self.model,
                    )
                    if bridge:
                        self.memory.save_bridge(novel_id, chapter_num, bridge)
                        log.info(f"ChapterBridge saved for chapter {chapter_num}: "
                                f"next_beat={bridge.get('next_beat','')[:60]}...")
                    else:
                        log.warning(f"ChapterBridge extraction returned None for chapter {chapter_num}")
                    return bridge

                if batch_mode:
                    _bkey = (novel_id, chapter_num)
                    _btask = asyncio.ensure_future(asyncio.to_thread(_extract_bridge))
                    self._pending_bridges[_bkey] = _btask
                    # 任务完成后自动清理（防止未 await 时残留）
                    _btask.add_done_callback(lambda _fut, _k=_bkey: self._pending_bridges.pop(_k, None))
                else:
                    # 非批量路径也走线程池，避免同步 LLM 调用（含重试 time.sleep）阻塞事件循环。
                    await asyncio.to_thread(_extract_bridge)
            except Exception as e:
                log.warning(f"ChapterBridge extraction failed (non-fatal): {e}")

            # Keep the published state unchanged while consistency, outline,
            # requirement and final quality passes can still mutate the draft.
            state = self.memory.get_novel_state(novel_id)
            completed = list(state.get("completed_chapters", []))

            # 一致性校验用上下文（前文章节 + 全局状态）
            prev_chapters_ctx = {}
            for _pc in completed:
                if _pc < chapter_num:
                    _pc_text = self.get_chapter(novel_id, _pc)
                    if _pc_text:
                        prev_chapters_ctx[_pc] = _pc_text
            _gs_path = os.path.join(novel_dir, "global_state.json")
            gs_ctx = safe_read_json(_gs_path, {}) if os.path.exists(_gs_path) else {}
            
            yield {"type": "status", "message": "📝 草稿已暂存，正在执行最终校验…"}

            # ── v2.3.5: 一致性校验（L1 规则 + L2 LLM 语义，P0 自动重写）──
            try:
                yield {"type": "status", "message": "🔍 正在校验逻辑一致性（亲属关系/时空/设定）…"}
                cv_result = await asyncio.to_thread(
                    self.consistency_validator.validate_chapter,
                    chapter_text=full_text,
                    chapter_num=chapter_num,
                    plan=plan,
                    prev_chapters=prev_chapters_ctx,
                    global_state=gs_ctx,
                    run_deep=False,  # 精简：此处只做 L1 规则快筛；L2 深度校验统一由下方「方案A」块执行，避免每章 2 次 L2 LLM 校验
                )
                all_v = cv_result.get("violations", [])
                p0_v = [v for v in all_v if v.get("severity") == "P0"]
                p1_v = [v for v in all_v if v.get("severity") == "P1"]

                # P0 致命错误 → 自动重写本章
                if p0_v:
                    yield {"type": "status", "message": f"🔧 检测到 {len(p0_v)} 处逻辑硬伤，正在自动重写…"}
                    log.warning(f"P0 issues Ch{chapter_num}: {[v.get('description','')[:50] for v in p0_v[:3]]}")
                    try:
                        fix_prompt = self.consistency_validator.build_fix_prompt(p0_v + p1_v[:3])
                        rewritten = ""
                        _nl2 = chr(10) + chr(10)
                        async for text in self.writer.write_stream(
                            context=context + _nl2 + fix_prompt,
                            genre=genre, style=style, target_words=target_words,
                            writing_mode=writing_mode, chapter_outline=chapter_outline,
                            skip_ending=True,
                        ):
                            rewritten += text
                        if rewritten and len(rewritten) > len(full_text) * 0.6:
                            full_text = rewritten
                            formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{full_text}"
                            self.memory.write_chapter_draft(novel_id, chapter_num, formatted)
                            self.memory.invalidate("state", novel_id)
                            yield {"type": "consistency_fixed", "chapter": chapter_num,
                                   "fixed": [v.get("description", "")[:80] for v in p0_v[:3]]}
                            log.info(f"Ch{chapter_num} auto-rewritten for {len(p0_v)} P0 issues")
                        else:
                            log.warning(f"Rewrite for Ch{chapter_num} too short, keeping original")
                    except Exception as rw_e:
                        log.warning(f"Auto-rewrite failed: {rw_e}")

                # P1 问题 → 注入下一章修正
                if p1_v:
                    issues = [v.get("description", "") for v in p1_v[:5]]
                    state["consistency_issues"] = state.get("consistency_issues", {})
                    state["consistency_issues"][str(chapter_num)] = issues
                    self.memory.save_novel_state(novel_id, state)
                    yield {"type": "consistency_warning", "chapter": chapter_num, "issues": issues}
                    log.warning(f"Consistency P1 issues Ch{chapter_num}: {len(issues)}")
            except Exception as cv_e:
                log.warning(f"Consistency check skipped: {cv_e}")

            # ── 完整度验证 ──
            from ..writer import _check_truncation, _dedup_continuation
            is_trunc, reason = _check_truncation(full_text, target_words)
            if is_trunc:
                log.warning(f"Chapter {chapter_num} incomplete after Writer retries: {reason}. "
                           f"Engine fallback: continuing from breakpoint...")
                yield {"type": "warning", "message": f"⏳ 本章内容不完整（{reason}），正在自动补全内容..."}
                
                # v2.49: Engine-level retry — 从断点续写，不重写
                try:
                    retry_context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)
                    # v2.4.7: 注入用户需求（重写/续写同样需要）
                    try:
                        _rreq = self.build_requirements_instruction(novel_id, chapter_num, plan)
                        if _rreq:
                            retry_context = retry_context + "\n\n" + _rreq
                    except Exception as _rre:
                        pass
                    # v2.51: 注入角色状态
                    char_ctx2 = self.context_updater.get_context_for_writer(novel_id, chapter_num, self.memory)
                    if char_ctx2:
                        retry_context = retry_context + "\n\n" + char_ctx2
                    # 注入已生成内容作为参考
                    retry_context = (
                        f"## 📝 已生成草稿（请从断点继续，不要重复）\n\n"
                        f"以下是已经写完的内容，字数约{len(full_text)//2}字，请从断点处直接续写：\n\n"
                        f"{full_text[-800:]}\n\n"
                        f"---\n\n"
                        f"⚠️ 继续写的要求：1) 不要重复上述内容 2) 从断点直接衔接 3) 补足到{target_words}字\n\n"
                        f"{retry_context}"
                    )
                    retry_text = ""
                    async for text in self.writer.write_stream(
                        context=retry_context,
                        genre=genre,
                        style=style,
                        target_words=max(target_words, int(len(full_text) * 1.5 / 2)),
                        writing_mode=writing_mode,
                        normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False),
                        fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
                        chapter_outline=chapter_outline,
                        skip_ending=True,
                    ):
                        retry_text += text
                        yield {"type": "text", "content": text}
                    
                    if retry_text:
                        # v2.49: 智能去重拼接
                        cleaned = _dedup_continuation(full_text, retry_text)
                        candidate = full_text + cleaned
                        is_trunc2, reason2 = _check_truncation(candidate, target_words)
                        if not is_trunc2 or len(candidate) > len(full_text):
                            full_text = candidate
                            log.info(f"Engine continuation OK: +{len(cleaned)} → {len(full_text)} chars")
                            yield {"type": "status", "message": "补全完成"}
                        else:
                            log.warning(f"Engine continuation no improvement")
                    else:
                        log.warning(f"Engine continuation empty, keeping original")
                except Exception as re:
                    log.warning(f"Engine retry failed: {re}, keeping original")

            # 大纲合规校验：缺失节拍只做场景级局部修订。
            _outline_compliance_report = None
            try:
                async for _cev in self._outline_compliance_step(
                    novel_id, chapter_num, chapter_outline, plan, full_text, batch_mode,
                ):
                    if _cev.get("type") == "outline_compliance":
                        _outline_compliance_report = _cev
                        if _cev.get("revised_text"):
                            full_text = _cev["revised_text"]
                            formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{full_text}"
                            self.memory.write_chapter_draft(novel_id, chapter_num, formatted)
                            self.memory.invalidate("state", novel_id)
                    yield _cev
            except Exception as _ce:
                log.warning(f"Outline compliance step skipped: {_ce}")

            # ── v2.0: 生成后自动设定一致性检查（正文内联标注）──
            try:
                _setting_annos = []
                async for _sev in self._setting_annotation_step(
                        novel_id, chapter_num, full_text):
                    if isinstance(_sev, list):
                        _setting_annos = _sev
                    else:
                        yield _sev
                if _setting_annos:
                    yield {"type": "outline_compliance_annotations",
                           "chapter": chapter_num, "annotations": _setting_annos}
            except Exception as _se:
                log.warning(f"Setting annotation step skipped: {_se}")

            # ── 自动执行 ContextUpdater: 更新全局角色状态 ──
            # v2.6: 批量模式后台化（不阻塞正文流程）；单章模式保持同步
            try:
                novel_dir = self.memory.get_novel_dir(novel_id)
                state_path = os.path.join(novel_dir, "global_state.json")
                current_state = {}
                if os.path.exists(state_path):
                    current_state = safe_read_json(state_path)

                def _ctx_update():
                    new_state = self.context_updater.update(novel_id, chapter_num, full_text, current_state)
                    atomic_write_json(state_path, new_state)
                    log.info(f"ContextUpdater: state updated after chapter {chapter_num}")

                if batch_mode:
                    asyncio.ensure_future(asyncio.to_thread(_ctx_update))
                else:
                    # v3.8: 单章模式也放线程池——ContextUpdater 内含同步 LLM（含 sleep 重试），
                    # 直接调用会阻塞整个事件循环（其他用户的 SSE 心跳/请求全部卡死）
                    await asyncio.to_thread(_ctx_update)
            except Exception as e:
                log.warning(f"ContextUpdater skipped: {e}")

            # ── 方案A: 一致性校验（ConsistencyValidator L1+L2 每章，P0 自动重写）──
            _post_consistency_validation = {
                "score": 0,
                "violations": [],
                "warnings": [],
                "error": "consistency validation was not completed",
            }
            _post_consistency_auto_fixed = False
            try:
                # 获取前文章节
                prev_chapters = {}
                for ch in completed:
                    if ch < chapter_num:
                        ch_content = self.get_chapter(novel_id, ch)
                        if ch_content:
                            prev_chapters[ch] = ch_content
                
                # 获取全局状态
                novel_dir_gs = self.memory.get_novel_dir(novel_id)
                gs_path = os.path.join(novel_dir_gs, "global_state.json")
                global_state = safe_read_json(gs_path, {}) if os.path.exists(gs_path) else {}
                
                # 执行校验（v2.3.5: 统一 ConsistencyValidator，L2 每章深检，含亲属关系检测）
                # v2.6: 批量模式跳过 — 前面 v2.3.5 校验已跑 L1 规则，避免重复校验白烧 LLM
                if batch_mode:
                    validation = {"violations": [], "score": 100, "warnings": []}
                else:
                    validation = await asyncio.to_thread(
                        self.consistency_validator.validate_chapter,
                        chapter_text=full_text,
                        chapter_num=chapter_num,
                        plan=plan,
                        prev_chapters=prev_chapters,
                        global_state=global_state,
                        chapter_outline=chapter_outline,
                        run_deep=True,  # 每章 L2 语义校验（v2.3.5: 原每3章→每章）
                    )
                _post_consistency_validation = validation
                
                violations = validation.get("violations", [])
                p0_count = 0
                if violations:
                    p0_count = sum(1 for v in violations if v.get("severity") == "P0")
                    score = validation.get("score", 100)
                    
                    log.warning(f"ConsistencyValidator(atomic): {len(violations)} violations "
                               f"(P0:{p0_count}) score={score}")
                    
                    yield {"type": "consistency_check",
                           "violations": violations,
                           "score": score}
                    
                    # P0违规 ≥1 → 自动修复
                    if p0_count >= 1:
                        fix_prompt = self.consistency_validator.build_fix_prompt(violations)
                        log.info(f"Auto-fix triggered: {p0_count} P0 violations")
                        
                        # 构造修复上下文
                        fix_context = f"{context}\n\n{fix_prompt}"
                        fixed_text = ""
                        
                        async for fix_chunk in self.writer.write_stream(
                            context=fix_context,
                            genre=genre,
                            style=style,
                            target_words=target_words,
                            writing_mode=writing_mode,
                            normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False), fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
                            chapter_outline=chapter_outline,
                            skip_ending=True,  # v2.12: 修复重试不重复生成结尾
                        ):
                            fixed_text += fix_chunk
                        
                        if fixed_text and len(fixed_text) > len(full_text) * 0.5:
                            full_text = fixed_text
                            # 重新保存
                            formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{fixed_text}"
                            self.memory.write_chapter_draft(novel_id, chapter_num, formatted)
                            _post_consistency_auto_fixed = True
                            log.info(f"Auto-fix completed for chapter {chapter_num}")
                            yield {"type": "auto_fix", "applied": True, "violations_fixed": p0_count}

                # P1 问题 → 注入下一章修正（与普通路径对齐）
                p1_issues = [v.get("description", "") for v in violations if v.get("severity") == "P1"]
                if p1_issues and not p0_count:
                    state = self.memory.get_novel_state(novel_id)
                    state["consistency_issues"] = state.get("consistency_issues", {})
                    state["consistency_issues"][str(chapter_num)] = p1_issues[:5]
                    self.memory.save_novel_state(novel_id, state)
                    yield {"type": "consistency_warning", "chapter": chapter_num, "issues": p1_issues[:5]}
            except Exception as e:
                _post_consistency_validation = {
                    "score": 0,
                    "violations": [],
                    "warnings": [],
                    "error": str(e),
                }
                log.warning(f"Consistency auto-validation skipped: {e}")

            # 自动修复后重新校验一次，最终报告必须反映修复后的正文，而不是旧正文的分数。
            if _post_consistency_auto_fixed:
                try:
                    # P0 修复可能引入新的时间线/因果问题；最多再补一轮，避免无限重写。
                    best_recheck_text = None
                    best_recheck_validation = None
                    best_recheck_rank = None
                    for repair_round in range(2):
                        _post_consistency_validation = await asyncio.to_thread(
                            self.consistency_validator.validate_chapter,
                            chapter_text=full_text,
                            chapter_num=chapter_num,
                            plan=plan,
                            prev_chapters=prev_chapters,
                            global_state=global_state,
                            chapter_outline=chapter_outline,
                            run_deep=not batch_mode,
                        )
                        recheck_violations = _post_consistency_validation.get("violations", [])
                        recheck_rank = _consistency_candidate_rank(
                            _post_consistency_validation
                        )
                        if best_recheck_rank is None or recheck_rank < best_recheck_rank:
                            best_recheck_rank = recheck_rank
                            best_recheck_text = full_text
                            best_recheck_validation = _post_consistency_validation
                        yield {
                            "type": "consistency_recheck",
                            "round": repair_round + 1,
                            "score": _post_consistency_validation.get("score", 100),
                            "violations": recheck_violations,
                        }
                        remaining_p0 = [
                            item for item in recheck_violations
                            if item.get("severity") == "P0"
                        ]
                        if not remaining_p0 or repair_round >= 1:
                            break

                        fix_prompt = self.consistency_validator.build_fix_prompt(
                            recheck_violations
                        )
                        repair_context = (
                            f"{context}\n\n"
                            "## 当前草稿（只修复下列逻辑问题，保留已成立的情节）\n"
                            f"{full_text}\n\n{fix_prompt}"
                        )
                        repaired_text = ""
                        async for repair_chunk in self.writer.write_stream(
                            context=repair_context,
                            genre=genre,
                            style=style,
                            target_words=target_words,
                            writing_mode=writing_mode,
                            normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False),
                            fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
                            chapter_outline=chapter_outline,
                            skip_ending=True,
                        ):
                            repaired_text += repair_chunk
                        if not repaired_text or len(repaired_text) <= len(full_text) * 0.5:
                            log.warning("Consistency repair round %s produced no usable draft", repair_round + 2)
                            break
                        full_text = repaired_text
                        formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{full_text}"
                        self.memory.write_chapter_draft(novel_id, chapter_num, formatted)
                        yield {
                            "type": "consistency_fixed",
                            "round": repair_round + 2,
                            "applied": True,
                            "violations_fixed": len(remaining_p0),
                        }

                    # 修复模型可能在后续轮次引入更严重的问题，最终只保留最佳候选。
                    if best_recheck_text is not None and full_text != best_recheck_text:
                        full_text = best_recheck_text
                        _post_consistency_validation = best_recheck_validation
                        formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{full_text}"
                        self.memory.write_chapter_draft(novel_id, chapter_num, formatted)
                        log.info(
                            "Consistency repair selected best candidate: rank=%s",
                            best_recheck_rank,
                        )
                except Exception as recheck_error:
                    _post_consistency_validation = {
                        "score": 0,
                        "violations": [],
                        "warnings": [],
                        "error": str(recheck_error),
                    }
                    log.warning(f"Consistency recheck skipped: {recheck_error}")

            # Freeze the exact candidate before any final report is calculated.
            # No text mutation is allowed after this point.
            from ..writer import _prepare_chapter_text_for_validation
            from ..quality_gate import validate_chapter_text
            full_text = _prepare_chapter_text_for_validation(full_text, target_words)
            formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{full_text}"
            validate_chapter_text(full_text, target_words=target_words)
            self.memory.write_chapter_draft(novel_id, chapter_num, formatted)

            # ── 最终质量报告：必须位于所有改写/一致性修复之后 ──
            _final_quality = await asyncio.to_thread(
                PacingChecker(self.client, self.model).quick_quality_check,
                full_text,
                fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
            )
            _narrative_quality = await asyncio.to_thread(
                        evaluate_chapter, full_text, None, None, chapter_outline, genre
            )
            _final_quality = _merge_quality_with_narrative(
                _final_quality, _narrative_quality
            )
            _final_quality = _merge_quality_with_consistency(
                _final_quality, _post_consistency_validation
            )
            _requirement_report = self._evaluate_chapter_requirements(
                novel_id, chapter_num, full_text, plan
            )
            yield {"type": "requirement_compliance", "chapter": chapter_num, **_requirement_report}
            _final_quality_record = _save_final_quality_report(
                self.memory,
                novel_id,
                chapter_num,
                {**_final_quality, "text": full_text},
                stage="final",
            )
            yield {"type": "final_quality", **_final_quality_record}

            # Both drafting strategies share this hard gate and final committer.
            _completion = self._finalize_chapter_candidate(
                novel_id,
                chapter_num,
                full_text,
                formatted,
                _requirement_report,
                _final_quality_record,
                _outline_compliance_report,
            )
            if _completion["status"] == "needs_revision":
                yield {
                    "type": "needs_revision",
                    "message": _completion["reason"],
                    **_completion,
                }
                return

            # ── v2.14: 每章自动摘要生成（注入后续章节上下文）──
            try:
                chapter_text = self.get_chapter(novel_id, chapter_num) or full_text
                if chapter_text and len(chapter_text) > 200:
                    summary_result = await asyncio.to_thread(
                        self.chapter_summarizer.summarize_chapter,
                        chapter_num, chapter_text
                    )
                    if summary_result:
                        # 摘要必须写入 global_state.json（消费方 build_summary_context
                        # 读的是 global_state），此前误写进 state.json 导致"前情摘要"
                        # 永远拿不到。用 update() 原子读-改-写，避免并发覆盖。
                        def _add_summary(gs):
                            if not isinstance(gs, dict):
                                gs = {}
                            gs.setdefault("summaries", {})[str(chapter_num)] = summary_result
                            return gs
                        self.memory.update("global_state", novel_id, _add_summary)
                        log.info(f"Auto-summary for Ch{chapter_num}: {len(summary_result.get('summary',''))} chars")
            except Exception as e:
                log.warning(f"Auto-summary for Ch{chapter_num} skipped: {e}")

            # ── 渐进式摘要压缩（每10章）──
            try:
                compress_result = check_and_compress(
                    self.memory, novel_id, chapter_num, self.chapter_summarizer
                )
                if compress_result:
                    yield {"type": "compression", "chapters_summarized": len(compress_result)}
            except Exception as e:
                log.warning(f"Auto-compression skipped: {e}")
            
            yield {"type": "status", "message": "✅ 章节正文与质量报告已同步保存"}
            yield {"type": "text", "content": "\n\n"}
            yield {"type": "done", "content": formatted, "chapter_num": chapter_num}

        except Exception as e:
            log.exception(f"Chapter generation failed: {e}")
            _preserved = self._preserve_generation_failure(
                novel_id,
                chapter_num,
                locals().get("full_text", ""),
                locals().get("formatted", ""),
                e,
            )
            if _preserved:
                yield {"type": "needs_revision", "message": _preserved["reason"], **_preserved}
            else:
                yield {"type": "error", "message": f"章节生成失败：{e}"}
        finally:
            # 清理并发锁
            try:
                if os.path.exists(lock_file):
                    os.remove(lock_file)
            except OSError:
                pass
