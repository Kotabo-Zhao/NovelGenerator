"""NovelGenerator v5 Four-Stage Composable Writing Engine (Zero Mixins).

Stages:
  1. Architect        -> Structured ChapterContracts + initial Narrative Ledger
  2. ContextCompiler  -> Dynamic SQLite-backed semantic-budgeted prompt
  3. ProseDrafter     -> Single-pass streaming prose generation + surgical edit
  4. Craft & Commit   -> Zero-cost CraftAnalyzer + QualityGate + ACID StateDelta
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

from .context_compiler import NarrativeContextCompiler
from .craft_analyzer import analyze_prose_craft
from .database import NarrativeLedgerDB
from .llm import AsyncLLMGateway
from .models import ChapterContract, StateDelta
from .quality_gate import dedup_continuation, ensure_complete_ending, validate_chapter_text

log = logging.getLogger(__name__)

STYLE_ATTRIBUTES: dict[str, str] = {
    "克制镜头": "用动作、停顿、物件与非视觉感官推进画面；少用抽象心理判断，情绪留白给读者自行体悟。",
    "快节奏悬疑": "尽早抛入异常与信息差，场景短促有力，对话暗藏试探与博弈，章末停在不可撤回的新后果上。",
    "古典雅致": "行文凝练含蓄，注重声韵节律与景物映衬，人物对话契合身份礼数，忌现代网络流行梗。",
    "凌厉爽感": "目标清晰、推进果断，冲突升级有清晰代价与智谋铺垫，拒绝无逻辑降智与重复打脸。",
    "严肃文学": "人物驱动情节，容忍情感的暧昧与矛盾，语言精确克制，句长随内心起伏自然呼吸。",
}

ARCHITECT_SYSTEM = """你是专业长篇小说总策划。请根据用户的题材、核心构想和目标章数，输出严格 JSON 格式的开篇蓝图与叙事初始账本。
要求：
1. 每章必须具备清晰的主角目标、核心冲突、出场角色、2-3条必发事件和章末钩子方向；
2. 角色设定须包含具体性格特征、初始位置与独特说话风格（voice_notes）；
3. 只输出 JSON，格式如下：
{
  "entities": [{"kind":"character","name":"","profile":"","current_state":"","location":"","voice_notes":""}],
  "foreshadows": [{"title":"","detail":"","status":"planted","planted_chapter":1,"target_chapter":4}],
  "world_entries": [{"category":"rule","title":"","keywords":"","content":""}],
  "chapters": [
    {"chapter_num":1,"title":"","summary":"","goal":"","conflict":"","characters":[""],"must_happen":[""],"hook_direction":"","target_words":2000}
  ]
}"""

STATE_EXTRACTOR_SYSTEM = """你是长篇小说连续性审计员。请阅读刚定稿的章节正文，抽取用于更新叙事状态账本的结构化 JSON：
1. bridge_summary：80-140字概括本章真实发生的剧情推进，供下一章无缝承接；
2. ending_anchor：摘录正文最后1-2句原文作为衔接锚点；
3. entity_updates：仅记录本章状态/位置/持有物品发生真实变化的角色；
4. foreshadow_updates：记录本章新埋设或已回收的伏笔；
5. canon_events：1-3条已成事实的核心事件（后续章节严禁重演）。
只输出 JSON：
{"bridge_summary":"","ending_anchor":"","entity_updates":[],"foreshadow_updates":[],"canon_events":[]}"""


def build_writer_system_prompt(mode: str, style: str, target_words: int) -> str:
    style_desc = STYLE_ATTRIBUTES.get(style, style or STYLE_ATTRIBUTES["克制镜头"])
    mode_note = (
        "你正在创作一部严肃文学作品：人物驱动情节，克制形容词，对话重潜台词与留白，不套网文套路。"
        if mode == "literary"
        else "你正在创作一部高质量类型小说：画面具体、冲突明确、信息差清晰，每场戏都带来局面变化。"
    )
    return (
        f"你是一位专业中文小说作家。{mode_note}\n\n"
        f"## 风格属性\n{style_desc}\n\n"
        "## 核心写作铁律\n"
        "1. 严格执行上下文中的「本章写作契约」与「上章承接」，禁止复制任何元指令或标题符号进入正文。\n"
        "2. 严禁复述或重演「已发生正史事件」清单中的任何桥段；所有人物言行必须符合其当前位置、状态与说话风格。\n"
        "3. 展现而非告知：避免“他感到/她意识到/心中充满/嘴角勾起一抹/与此同时/这不仅仅”等 AI 套话，用动作、物件和声音/气味/触感构建镜头。\n"
        "4. 段落按语义单元自然分段（动作完成、焦点转移或对话换人时分段），引号仅包裹人物实际说出口的话。\n"
        "5. 写完章末钩子后立即停笔，不追加“他终于明白”“这意味着”式主题总结。\n"
        f"6. 直接输出纯正文（不含章节标题），目标篇幅约 {target_words} 字。"
    )


class WritingEngine:
    """Stateless orchestrator composing DB, ContextCompiler, LLM, CraftAnalyzer, and QualityGate."""

    def __init__(self, db: NarrativeLedgerDB, llm: AsyncLLMGateway):
        self.db = db
        self.llm = llm
        self.compiler = NarrativeContextCompiler(db)

    async def plan_novel(self, novel_id: str, chapter_count: int | None = None) -> dict[str, Any]:
        novel = self.db.get_novel(novel_id)
        if not novel:
            raise ValueError("作品不存在")
        count = max(1, min(30, int(chapter_count or min(novel["target_chapters"], 6))))
        user_prompt = (
            f"作品名：《{novel['title']}》\n"
            f"题材：{novel['genre']}｜风格：{novel['style']}｜视角：{novel['pov']}\n"
            f"单章目标字数：{novel['target_words_per_chapter']}\n"
            f"请先规划前 {count} 章的详细大纲契约与初始角色/伏笔/世界观账本。\n"
            f"核心构想：{novel['premise'] or '围绕高悬念核心谜团展开强冲突叙事。'}"
        )
        blueprint = await self.llm.complete_json(ARCHITECT_SYSTEM, user_prompt, task="outline")

        contracts = [
            ChapterContract.from_dict({
                **item,
                "chapter_num": idx + 1,
                "target_words": item.get("target_words") or novel["target_words_per_chapter"],
                "pov": novel["pov"],
            })
            for idx, item in enumerate(blueprint.get("chapters") or [])
        ]
        if contracts:
            self.db.save_chapter_contracts(novel_id, contracts)

        for ent in blueprint.get("entities") or []:
            if ent.get("name"):
                self.db.upsert_entity(novel_id, ent)
        for fs in blueprint.get("foreshadows") or []:
            if fs.get("title"):
                self.db.upsert_foreshadow(novel_id, fs)
        for w in blueprint.get("world_entries") or []:
            if w.get("title"):
                self.db.upsert_world_entry(novel_id, w)

        return {
            "novel": self.db.get_novel(novel_id),
            "ledger": self.db.get_ledger(novel_id),
            "planned_count": len(contracts),
        }

    async def draft_chapter_stream(
        self,
        novel_id: str,
        chapter_num: int,
        *,
        revision_feedback: str = "",
        auto_commit: bool = True,
    ) -> AsyncGenerator[dict[str, Any], None]:
        novel = self.db.get_novel(novel_id)
        if not novel:
            yield {"type": "error", "message": "作品不存在"}
            return

        chapter = self.db.get_chapter(novel_id, chapter_num)
        contract = (
            ChapterContract.from_dict(chapter["contract"])
            if chapter
            else ChapterContract(chapter_num=chapter_num, title=f"第{chapter_num}章", summary=novel["premise"], target_words=novel["target_words_per_chapter"], pov=novel["pov"])
        )

        # Stage 2: Context Compilation
        yield {"type": "stage", "stage": "context", "message": "正在从叙事账本编译本章上下文…"}
        compiled = self.compiler.compile_for_chapter(novel, contract, revision_feedback=revision_feedback)
        yield {
            "type": "context_ready",
            "active_entities": compiled["active_entities"],
            "active_foreshadows": compiled["active_foreshadows"],
            "trimmed_sections": compiled["trimmed_sections"],
        }

        # Stage 3: Streaming Prose Drafting
        yield {"type": "stage", "stage": "drafting", "message": f"正在撰写第{chapter_num}章《{contract.title}》…"}
        system_prompt = build_writer_system_prompt(novel["mode"], novel["style"], contract.target_words)
        chunks: list[str] = []
        async for piece in self.llm.stream_text(
            system_prompt,
            compiled["compiled_context"],
            task="chapter",
            meta={"title": contract.title, "chapter_num": chapter_num},
        ):
            chunks.append(piece)
            yield {"type": "chunk", "text": piece}

        raw_text = "".join(chunks).strip()
        final_text = ensure_complete_ending(raw_text)

        # Stage 4: Quality Gate + Zero-Cost Craft Check + Atomic State Commit
        yield {"type": "stage", "stage": "committing", "message": "正在执行文风体检与状态账本原子提交…"}
        gate_warning = ""
        try:
            validate_chapter_text(final_text, target_words=contract.target_words)
        except Exception as exc:
            gate_warning = str(exc)

        craft = analyze_prose_craft(final_text)
        delta: StateDelta | None = None
        if auto_commit and final_text:
            try:
                delta = await self.extract_state_delta(chapter_num, final_text)
            except Exception as exc:
                log.warning("State extraction skipped: %s", exc)
            self.db.commit_chapter_and_state(
                novel_id,
                chapter_num,
                content=final_text,
                craft_score=craft["craft_score"],
                ai_score=craft["ai_score"],
                version_note="按意见重写" if revision_feedback.strip() else "四阶段生成定稿",
                delta=delta,
            )

        yield {
            "type": "done",
            "chapter_num": chapter_num,
            "content": final_text,
            "craft": craft,
            "gate_warning": gate_warning,
            "state_delta": delta.to_dict() if delta else None,
        }

    async def surgical_rewrite_stream(
        self,
        novel_id: str,
        chapter_num: int,
        *,
        selected_text: str,
        instruction: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        novel = self.db.get_novel(novel_id)
        if not novel:
            yield {"type": "error", "message": "作品不存在"}
            return
        system_prompt = (
            f"你是资深小说文字编辑。风格要求：{STYLE_ATTRIBUTES.get(novel['style'], novel['style'])}\n"
            "仅改写用户选中的片段，保持人物设定、人称与上下文衔接完全一致，直接输出改写后的片段，不要任何解释。"
        )
        user_prompt = f"改写指令：{instruction}\n\n待改写原文片段：\n{selected_text}"
        chunks: list[str] = []
        async for piece in self.llm.stream_text(system_prompt, user_prompt, task="surgical"):
            chunks.append(piece)
            yield {"type": "chunk", "text": piece}
        rewritten = "".join(chunks).strip()
        yield {"type": "done", "rewritten": rewritten, "craft": analyze_prose_craft(rewritten)}

    async def extract_state_delta(self, chapter_num: int, content: str) -> StateDelta:
        data = await self.llm.complete_json(
            STATE_EXTRACTOR_SYSTEM,
            f"第{chapter_num}章正文如下，请抽取叙事状态变更：\n\n{content[:5000]}",
            task="state_delta",
        )
        return StateDelta(
            chapter_num=chapter_num,
            bridge_summary=str(data.get("bridge_summary", "")).strip(),
            ending_anchor=str(data.get("ending_anchor", "")).strip() or content[-120:],
            entity_updates=list(data.get("entity_updates") or []),
            foreshadow_updates=list(data.get("foreshadow_updates") or []),
            canon_events=[str(e).strip() for e in (data.get("canon_events") or []) if str(e).strip()],
        )
