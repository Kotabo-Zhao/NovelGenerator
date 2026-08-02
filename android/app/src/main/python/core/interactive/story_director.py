"""StoryDirector — 互动小说剧情引擎（v3.0）

核心能力（对照 docs/interactive-novel-plan.html §5）：
1. 场景生成（SSE 流式）：叙事段落 + 角色台词，标记语言输出
2. 节点检测三层保障：规则预筛（保节奏下限）→ LLM 精判（防注水）→ 玩家主动（完全兜底）
3. 目标锚定 + 回扣验证：每段必须推进目标 / 回扣 active fact
4. PACT 提取：对话结束 → 结构化剧情事实（Promise/Action/Change/Trust）
5. 事实生命周期：active → fulfilled / expired / broken

性能设计（用户要求：生成不能慢）：
- 节点判定优先规则预筛，LLM 精判只对候选场景
- 场景生成单次 LLM 调用，流式输出，目标锚定检查并入下一段判定
- PACT 提取在 end-chat 时同步执行（1 次调用）
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncIterator, Optional

from ..resilient_client import ResilientLLMClient

log = logging.getLogger(__name__)

# ── System Prompts ──
SCENE_SYSTEM = """你是互动小说导演。你正在导演一部可以随时与读者对话的互动小说。

输出格式（严格遵循标记语言）：
【旁白】叙事段落（1-3 段，文笔优美，类似严肃小说）
【角色名】该角色的台词（一段，符合人设）
【动作】可选：无声的动作描写（如"她指尖一顿，茶水溅出半滴"）
...

规则：
1. 【旁白】是主体，承担叙事推进；台词用于关键时刻点睛
2. 每条台词必须标注说话人角色名；旁白不标注说话人
3. 剧情必须推进当前目标（objective），不可开无关新线
4. 若给定"待兑现事实"（facts），本段必须自然回扣至少 1 个（兑现/提及/利用/其后果显现）
5. 角色说话必须符合各自的人设卡与声音卡（口头禅/句式/情绪表达）
6. 单场景 300-600 字，节奏紧凑，不要在无关细节上停留
只输出标记语言文本，不要输出解释。"""

NODE_SYSTEM = """你是互动小说剧情节奏师。判断当前场景是否应该暂停，让读者与角色对话。

考虑因素：
- 场景中出现重大事件（新线索/新人物/冲突升级/角色态度转变）→ 是
- 场景中有角色与读者有直接互动可能（询问/邀请/威胁/诱惑）→ 是
- 场景只是铺垫/过渡/风景描写 → 否
- 最近已经对话过且无新进展 → 否

输出 JSON: {"is_node": true/false, "chars": ["在场角色名"], "suggested_rounds": 1-8, "confidence": 0-1, "reason": "一句话理由"}
只输出 JSON。"""

PACT_SYSTEM = """你是互动小说因果提取器。从读者与角色的对话中，提取影响后续剧情的事实。

提取规则：
1. Promise（承诺）：读者明确说"我会/我答应/我保证/我欠你"等 → 提取，severity high
2. Action（行动）：读者做的重要行为（威胁/示好/隐瞒/揭露/交易）→ 提取
3. Change（关系变化）：对话导致的关系/态度明显变化
4. Trust（秘密）：读者或角色透露的重要信息 → 提取
5. 读者明确说过的话必须逐条提取，一条都不能漏；宁可多提 low severity 项
6. 空泛客套（"改天请你喝茶"）标记 severity=low，不强制回扣
7. 与世界观冲突的荒诞要求（"我是皇帝"）不提取为事实，只记录为角色反应

输出 JSON:
{"facts": [{"id": "f1", "type": "promise/action/secret/change", "subject": "player/角色名", "target": "角色名/player", "content": "一句话描述", "severity": "high/medium/low", "source_chat": 轮次序号}], "relations": {"角色名": "+/-数值或描述"}, "objective_update": "更新后的目标或空", "tone": "对话基调（试探/交易/亲昵/敌对…）"}
只输出 JSON。"""


def _parse_json(content: str) -> Optional[dict]:
    """容错 JSON 解析（复用项目通用模式）"""
    if not content:
        return None
    text = str(content).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def parse_scene_markup(text: str) -> list:
    """解析标记语言 → [{type, speaker, content}]

    【旁白】... / 【角色名】... / 【动作】...
    """
    blocks = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("【") and "】" in line:
            end = line.index("】")
            speaker = line[1:end].strip()
            content = line[end + 1:].strip()
            if not content:
                continue
            if speaker in ("旁白", "叙述", "旁白/叙述"):
                blocks.append({"type": "narration", "speaker": "", "content": content})
            elif speaker in ("动作", "描写"):
                blocks.append({"type": "action", "speaker": "", "content": content})
            else:
                blocks.append({"type": "dialogue", "speaker": speaker, "content": content})
        else:
            # 未标记的行 → 归入旁白（追加到上一块或新建）
            if blocks and blocks[-1]["type"] == "narration":
                blocks[-1]["content"] += line
            else:
                blocks.append({"type": "narration", "speaker": "", "content": line})
    return blocks


class StoryDirector:
    def __init__(self, client, model: str, store, engine=None):
        self.client = client
        self.model = model
        self.store = store
        self.engine = engine  # NovelEngine 引用（人设蒸馏/读取用，避免重复实例化）
        self._resilient = ResilientLLMClient(client, model)

    # ── LLM 基础 ──
    def _llm(self, system: str, user: str, temperature: float = 0.8,
             max_tokens: int = 2000) -> Optional[str]:
        try:
            resp = self._resilient.create(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content if hasattr(resp, "choices") else resp
            if isinstance(content, str):
                return content.strip()
            return None
        except Exception as e:
            log.warning(f"StoryDirector LLM call failed: {type(e).__name__}: {str(e)[:120]}")
            return None

    async def _llm_stream(self, system: str, user: str,
                          temperature: float = 0.8) -> AsyncIterator[str]:
        try:
            async for chunk in self._resilient.create_stream(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=2500,
            ):
                yield chunk
        except Exception as e:
            log.warning(f"StoryDirector stream failed: {type(e).__name__}: {str(e)[:120]}")
            yield ""  # 空 chunk，让前端感知结束

    # ── 上下文组装 ──
    def _build_scene_prompt(self, state: dict, summary: str) -> str:
        s = state.get("state", {})
        parts = []
        parts.append(f"## 小说：《{state.get('title', '')}》（{state.get('genre', '')}·{state.get('style', '')}）")
        parts.append(f"当前场景号: {state.get('scene_num', 0)}")
        if s.get("location"):
            parts.append(f"地点: {s['location']}")
        if s.get("objective"):
            parts.append(f"主线目标（必须推进）: {s['objective']}")
        if s.get("flags"):
            parts.append(f"剧情标记: {'、'.join(s['flags'][-5:])}")
        # 待兑现事实（硬挂钩）
        facts = [f for f in state.get("facts", []) if f.get("status") == "active"]
        if facts:
            parts.append("待兑现事实（本段必须自然回扣至少 1 个）:")
            for f in facts[:6]:
                parts.append(f"- [{f.get('type')}] {f.get('content')}")
        if summary:
            parts.append(f"前情摘要: {summary[:600]}")
        # 角色卡
        casts = state.get("casts", {})
        if casts:
            parts.append("在场角色人设（说话必须符合）:")
            for name, c in casts.items():
                prof = c.get("profile", {})
                brief = []
                dna = prof.get("expression_dna", [])[:2]
                for d in dna:
                    brief.append(str(d.get("name", d))[:60] if isinstance(d, dict) else str(d)[:60])
                anti = prof.get("anti_patterns", [])[:2]
                for a in anti:
                    brief.append(f"禁:{a.get('pattern', a) if isinstance(a, dict) else a}"[:60])
                if brief:
                    parts.append(f"- {name}: {'；'.join(brief)}")
        return "\n".join(parts)

    # ── 场景生成（SSE）──
    async def generate_scene_stream(self, novel_id: str,
                                    force_node_check: bool = True) -> AsyncIterator[dict]:
        """生成下一场景（流式）+ 结束后自动判定节点

        Yields: {type: scene_chunk/block/scene_end/node_check/error/done}
        """
        state = self.store.load_state(novel_id)
        if state is None:
            yield {"type": "error", "message": "互动存档不存在，请先 start"}
            return

        # 快照（生成前备份）
        self.store.snapshot(novel_id)
        scene_num = state.get("scene_num", 0) + 1
        summary = state.get("summary", "")

        prompt = self._build_scene_prompt(state, summary)
        collected = []

        yield {"type": "scene_chunk", "scene_num": scene_num, "content": ""}
        try:
            async for chunk in self._llm_stream(SCENE_SYSTEM, prompt):
                if chunk:
                    collected.append(chunk)
                    yield {"type": "scene_chunk", "scene_num": scene_num, "content": chunk}
        except Exception as e:
            log.error(f"Scene generation stream error: {e}")
            yield {"type": "error", "message": f"场景生成失败: {type(e).__name__}"}
            return

        scene_text = "".join(collected).strip()
        if not scene_text:
            # 兜底文本
            scene_text = f"【旁白】夜色渐深，{state.get('title', '故事')}还在继续。远处传来更鼓声，故事尚未落幕。"
            yield {"type": "scene_chunk", "scene_num": scene_num, "content": scene_text}

        # 解析 + 持久化
        blocks = parse_scene_markup(scene_text)
        scene_record = {
            "scene_num": scene_num,
            "scene_text": scene_text,
            "blocks": blocks,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.store.append_scene(novel_id, scene_record)

        # 更新状态：场景号、摘要、最近场景
        state["scene_num"] = scene_num
        state["summary"] = scene_text[:300]
        recent = state.get("recent_scenes", [])
        recent.append(scene_text[:300])
        state["recent_scenes"] = recent[-3:]
        # 在场角色（从台词块提取）
        speakers = {b["speaker"] for b in blocks if b["type"] == "dialogue"}
        casts = state.get("casts", {})
        state["casts"] = {k: casts.get(k, {"present": True}) for k in casts}
        for sp in speakers:
            if sp and sp not in casts:
                casts[sp] = {"present": True, "profile": {}}
        state["casts"] = casts
        self.store.save_state(novel_id, state)

        yield {"type": "scene_end", "scene_num": scene_num, "blocks": blocks}

        # ── 节点判定（三层保障的 ① 规则预筛 + ② LLM 精判）──
        if force_node_check:
            is_node, node_chars, rounds, reason = self._decide_node(novel_id, scene_num, blocks, state)
            state = self.store.load_state(novel_id)
            state["pending_node"] = is_node
            state["node_chars"] = node_chars
            state["node_rounds"] = rounds
            self.store.save_state(novel_id, state)
            yield {
                "type": "node_check",
                "is_node": is_node,
                "chars": node_chars,
                "suggested_rounds": rounds,
                "reason": reason,
            }

        yield {"type": "done"}

    # ── 节点判定 ──
    def _decide_node(self, novel_id: str, scene_num: int, blocks: list, state: dict) -> tuple:
        """① 规则预筛 → ② LLM 精判。返回 (is_node, chars, rounds, reason)"""
        # 规则 1：开场第 1 段必触发
        if scene_num <= 1:
            return True, self._scene_chars(blocks), 3, "开场互动"
        # 规则 2：连续 2 段无对话 → 强制（用最近场景判断）
        last_two = self.store.recent_scenes(novel_id, 2)
        if len(last_two) >= 2:
            has_dialogue_prev = any(
                b.get("type") == "dialogue" for b in last_two[-2].get("blocks", [])
            )
            has_dialogue_cur = any(b.get("type") == "dialogue" for b in blocks)
            if not has_dialogue_prev and not has_dialogue_cur:
                return True, self._scene_chars(blocks), 4, "连续两段无对话"
        # 规则 3：场景出现新角色 / 冲突关键词
        text = " ".join(b["content"] for b in blocks)
        conflict_kw = ["怒", "拔剑", "威胁", "揭露", "震惊", "交易", "秘密", "追杀", "真相", "决裂"]
        if any(k in text for k in conflict_kw):
            chars = self._scene_chars(blocks)
            if chars:
                return True, chars, 5, f"冲突场景: {next((k for k in conflict_kw if k in text), '')}"
        # 规则 4：场景中有对话但最近已对话过 → LLM 精判
        chars = self._scene_chars(blocks)
        if not chars:
            return False, [], 0, "无在场角色"
        # LLM 精判（规则未命中才调用）
        result = self._llm_judge_node(text, chars, state)
        if result is None:
            return False, chars, 2, "判定失败，默认不触发（玩家可主动介入）"
        is_node = bool(result.get("is_node"))
        confidence = float(result.get("confidence", 0.5))
        # 防注水：confidence < 0.4 且距上次对话 < 2 段 → 不触发
        if is_node and confidence < 0.4 and scene_num - state.get("_last_chat_scene", 0) < 2:
            return False, chars, 0, "低置信度且刚对话过"
        rounds = int(result.get("suggested_rounds", 3) or 3)
        return is_node, result.get("chars") or chars, rounds, result.get("reason", "")

    def _llm_judge_node(self, text: str, chars: list, state: dict) -> Optional[dict]:
        user = (
            f"场景文本:\n{text[:800]}\n\n"
            f"在场角色: {', '.join(chars)}\n"
            f"距上次对话: {state.get('scene_num', 0) - state.get('_last_chat_scene', 0)} 段\n"
            f"主线目标: {state.get('state', {}).get('objective', '')}\n"
            f"请判断是否应该暂停让读者与角色对话。"
        )
        raw = self._llm(NODE_SYSTEM, user, temperature=0.3, max_tokens=300)
        return _parse_json(raw) if raw else None

    @staticmethod
    def _scene_chars(blocks: list) -> list:
        seen, out = set(), []
        for b in blocks:
            sp = b.get("speaker", "")
            if sp and sp not in seen:
                seen.add(sp)
                out.append(sp)
        return out

    # ── PACT 提取（对话结束）──
    def extract_pact(self, novel_id: str, chat_entries: list) -> dict:
        """对话 → 剧情事实（PACT）。返回提取结果，并写入 state"""
        if not chat_entries:
            return {"facts": [], "relations": {}, "objective_update": "", "tone": ""}
        transcript = []
        for i, e in enumerate(chat_entries):
            role = "读者" if e.get("role") == "user" else f"角色{e.get('speaker', '')}"
            transcript.append(f"[{i}] {role}: {e.get('content', '')[:200]}")
        user = "对话记录:\n" + "\n".join(transcript[-40:])
        raw = self._llm(PACT_SYSTEM, user, temperature=0.3, max_tokens=1500)
        result = _parse_json(raw) if raw else {}
        if not isinstance(result, dict):
            result = {}

        facts = result.get("facts", []) or []
        state = self.store.load_state(novel_id)
        if state is None:
            return result
        existing_ids = {f.get("id") for f in state.get("facts", [])}
        # 内容级去重（防 LLM 重复提取同一事实）
        existing_contents = {str(f.get("content", ""))[:40] for f in state.get("facts", [])}
        for f in facts:
            if not f.get("content"):
                continue
            if f.get("id") in existing_ids:
                continue
            # 相似内容合并（前 40 字相同视为重复）
            if str(f.get("content", ""))[:40] in existing_contents:
                continue
            f.setdefault("id", f"f{int(time.time())}_{uuid.uuid4().hex[:4]}")
            f.setdefault("status", "active")
            f.setdefault("due_scene", state.get("scene_num", 0) + 3)
            f.setdefault("evidence", [])
            f["severity"] = f.get("severity", "medium")
            existing_ids.add(f["id"])
            existing_contents.add(str(f.get("content", ""))[:40])
            state.setdefault("facts", []).append(f)
        # relations 合并（只保留真实角色名 key，过滤 player-xxx / xxx-player 垃圾 key）
        rel = result.get("relations") or {}
        casts = state.get("casts", {}) or {}
        for k, v in rel.items():
            if not isinstance(k, str) or "-" in k or k not in casts:
                continue  # LLM 输出的对话基调（"player-沈砚": "试探"）不是关系值，跳过
            rel_map = state["state"].setdefault("relations", {})
            cur = rel_map.get(k, 0)
            try:
                if isinstance(v, str):
                    # 提取开头的 +/- 数字部分（如 "+1（因坦诚而增加信任）"）
                    import re
                    m = re.match(r"^([+-]\d+)", v.strip())
                    if m:
                        delta = int(m.group(1))
                        rel_map[k] = max(0, min(100, int(cur) + delta))
                    elif isinstance(cur, int):
                        rel_map[k] = v  # 无数字前缀的描述保留原文
                    else:
                        rel_map[k] = v
                elif isinstance(v, (int, float)):
                    rel_map[k] = max(0, min(100, int(cur) + int(v)))
                else:
                    rel_map[k] = v
            except (TypeError, ValueError):
                rel_map[k] = v
        # objective 更新
        if result.get("objective_update"):
            state["state"]["objective"] = result["objective_update"]
        state["_last_chat_scene"] = state.get("scene_num", 0)
        self.store.save_state(novel_id, state)
        return result

    # ── 目标锚定 + 回扣验证（并入节点判定，不单独调用）──
    # 说明：目标锚定通过 _build_scene_prompt 注入 objective + facts 硬约束，
    # 回扣验证合并进下一次 _decide_node 的规则 2（连续 2 段无对话强制节点），
    # 以及状态校验器（每 3 段由前端触发 scene 时附带 summary 比对，v1 简化）。

    # ── 工具 ──
    def build_context_from_bible(self, novel_id: str) -> dict:
        """从 character_bible / plan 构建初始互动上下文

        返回: {title, genre, style, protagonist_name, casts_preview}
        """
        from ..mixins.character_profile import CharacterProfileMixin  # noqa 防循环

        try:
            novel = None
            # 通过 engine 的 get_novel 获取
            from ..engine import NovelEngine
            # 避免重复实例化：直接读 plan.json
            import os
            from config import NOVELS_DIR
            plan_path = os.path.join(NOVELS_DIR, novel_id, "plan.json")
            if os.path.exists(plan_path):
                with open(plan_path, "r", encoding="utf-8") as f:
                    novel = json.load(f)
        except Exception as e:
            log.warning(f"build_context_from_bible read plan failed: {e}")
            novel = None
        if not novel:
            return {"title": novel_id, "genre": "", "style": "",
                    "protagonist_name": "", "casts_preview": {}}
        proto = novel.get("protagonist") or {}
        casts_preview = {}
        if proto.get("name"):
            casts_preview[proto["name"]] = {"role": "protagonist", "desc": str(proto.get("personality", ""))[:80]}
        for c in novel.get("supporting", []) or []:
            if isinstance(c, dict) and c.get("name"):
                casts_preview[c["name"]] = {"role": "supporting", "desc": str(c.get("personality", ""))[:80]}
        for c in novel.get("antagonist", []) or []:
            if isinstance(c, dict) and c.get("name"):
                casts_preview[c["name"]] = {"role": "antagonist", "desc": str(c.get("personality", ""))[:80]}
        return {
            "title": novel.get("title", novel_id),
            "genre": novel.get("genre", ""),
            "style": novel.get("style", ""),
            "protagonist_name": proto.get("name", ""),
            "casts_preview": casts_preview,
            "worldbuilding": novel.get("worldbuilding", {}),
        }

    def attach_cast_profiles(self, novel_id: str, char_names: list):
        """为出场角色挂载人设卡（有蒸馏数据则用，无则留空由对话引擎即时蒸馏兜底）"""
        engine = self.engine
        if engine is None:
            return
        state = self.store.load_state(novel_id)
        if state is None:
            return
        casts = state.get("casts", {})
        changed = False
        for name in char_names:
            if not name:
                continue
            existing = casts.get(name, {})
            if existing.get("profile"):
                continue
            try:
                prof = engine.get_character_profile(novel_id, name)
                if prof and "error" not in prof:
                    casts.setdefault(name, {})["profile"] = prof
                    changed = True
            except Exception:
                pass
        if changed:
            state["casts"] = casts
            self.store.save_state(novel_id, state)
