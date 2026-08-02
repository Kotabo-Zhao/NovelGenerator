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
7. v3.5.5 对话衔接：若场景涉及需要读者抉择/回应的关键节点，场景可以以角色的发问、
   邀约、警告或悬念收尾（如"……他忽然停步：'你，真的决定了吗？'"），
   让后续对话自然衔接，不要生硬结束
8. v3.5.7 承接性（最高优先级）：若给定"读者上一步做了什么"（last_action）或
   "刚结束的对话"，本段场景必须从它的后果/余波/反应开始写——
   行动已改变剧情（地点/关系/物品/承诺），严禁无视玩家行为另起炉灶或时间倒流；
   若没有给定，则正常推进主线
只输出标记语言文本，不要输出解释。"""

NODE_SYSTEM = """你是互动小说剧情节奏师。判断当前场景是否应该暂停，让读者与角色对话。

**核心原则（v3.5.3）：对话只在影响剧情走向的地方出现。** 不是"该不该聊"，
而是"这一停，会不会改变剧情走向"——不会就不停。对话是剧情的岔路口，不是聊天室。

应该触发（confidence ≥ 0.65）：
- 读者的一句话/一个决定会改变后续剧情（答应/拒绝/信任谁/跟谁走/说出秘密）
- 关键信息即将揭晓，读者有权追问或阻止（真相、阴谋、身世）
- 关系重大转折点（表白/决裂/结盟/背叛前夕）
- 角色提出明确邀请/交易/威胁，读者必须当场回应

不应该触发（confidence ≤ 0.35）：
- 场景本身已有对话且无新决策点（**已有 2 条以上角色台词 → 默认不触发**）
- 过渡、铺垫、日常推进、风景描写——叙事自行推进即可
- 只是读者想插话的场合——读者有「我要说话」按钮，想聊随时能聊，不需要系统停
- 对话无法改变剧情走向时（闲聊、寒暄、信息已定）

输出 JSON: {"is_node": true/false, "chars": ["在场角色名"], "suggested_rounds": 2-4, "confidence": 0-1, "reason": "一句话理由"}
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

AGENDA_SYSTEM = """你是互动小说对话编排师。为即将开始的角色对话制定议程（Agenda）。

**Agenda 的目的**：让对话有方向——角色带着目的聊天，而不是陪读者闲聊。对话结束后剧情必须因这场对话而推进。

设计规则：
1. goal：这场对话要达成的目标（获取信息/说服读者/建立关系/考验读者），一句话，必须与当前主线目标相关
2. hooks：2-4 条"推进开关"——读者说出/做出什么，剧情就向前走（如"读者提到金吾卫 → 苏晚松口给线索"）。钩子是对话推进剧情的机关
3. boundaries：角色在这场对话中绝不主动透露/绝不做的事（1-3 条，如"苏晚绝不主动承认认识绣衣使"）——保留剧情张力
4. exit：对话自然收尾条件（min_rounds 最少轮数、condition 何时可以收尾）

输出 JSON:
{"goal": "一句话目标", "hooks": [{"trigger": "读者行为/话语", "outcome": "剧情推进结果"}], "boundaries": ["角色绝不主动做的事"], "exit": {"min_rounds": 3, "condition": "收尾条件"}}
只输出 JSON。"""

HOOK_VERIFY_SYSTEM = """你是互动小说钩子核对器。判断读者与角色的对话中，议程（Agenda）的"推进开关"（hooks）是否已被触发。

判断标准：
- hook 触发 = 对话中读者说/做了与 trigger 实质相符的事（包含威胁、交易、承诺、追问关键信息等）
- 读者明确拒绝/回避该话题 → hit=false，记入拒绝
- 只有闲聊寒暄 → 全部 hit=false

输出 JSON:
{"hook_hits": [{"hook_index": 0, "hit": true/false, "evidence": "对话原文摘录或'未触发'"}], "all_hit": true/false}
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
        # v3.2: 世界观注入（保证剧情贴合本小说设定）
        wb = state.get("worldbuilding_brief") or ""
        if wb:
            parts.append(f"## 世界观设定（必须严格遵守，不得偏离）:\n{wb[:600]}")
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
        # v3.3.1: 上一场对话未达成的目标（missing hooks）——软约束：后果显现/角色惦记
        missing = state.get("pending_missing_hooks") or []
        if missing:
            parts.append("上一场对话未谈成的事（本段剧情可让其后顾显现，或角色主动提起追问）:")
            for m in missing[:3]:
                parts.append(f"- {str(m)[:60]}")
        if summary:
            parts.append(f"前情摘要: {summary[:600]}")
        # v3.5.7: 读者上一步行动（承接性——新场景必须从行动后果写起）
        la = state.get("last_action") or {}
        if la and la.get("summary"):
            parts.append(f"读者上一步做了什么（本段必须从这件事的后果/余波写起，严禁无视）:")
            parts.append(f"- [{la.get('type', '行动')}] {la.get('summary', '')[:200]}")
        # v3.5.7: 刚结束的对话（承接对话结论）
        nid = state.get("novel_id", "")
        recent_chats = self.store.recent_chats(nid, 6) if (nid and hasattr(self.store, "recent_chats")) else []
        chat_lines = [f"{'读者' if c.get('role') == 'user' else c.get('speaker', '角色')}: {str(c.get('content', ''))[:80]}"
                      for c in recent_chats if c.get("content")]
        if chat_lines:
            parts.append("刚结束的对话（本段可自然承接其中情绪/未尽话题，但不要复述）:")
            for line in chat_lines[-4:]:
                parts.append(f"- {line}")
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
        # v3.3.1: missing hooks 只影响本段场景，用后即清（软约束不过期悬挂）
        state.pop("pending_missing_hooks", None)
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
            agenda = None
            if is_node:
                # v3.3: Agenda 机制——对话前生成议程（目标/推进开关/边界），对话围绕它推进
                agenda = self._generate_agenda(novel_id, node_chars, state)
                state["agenda"] = agenda
            self.store.save_state(novel_id, state)
            yield {
                "type": "node_check",
                "is_node": is_node,
                "chars": node_chars,
                "suggested_rounds": rounds,
                "reason": reason,
                "agenda": agenda,   # 前端可展示"这场对话要谈什么"
            }

        yield {"type": "done"}

    # ── 节点判定 ──
    def _decide_node(self, novel_id: str, scene_num: int, blocks: list, state: dict) -> tuple:
        """① 规则预筛 → ② LLM 精判。返回 (is_node, chars, rounds, reason)

        v3.5.3 节奏定稿（老赵："对话只在影响剧情走向的地方"）：
        - 场景已有 ≥2 条角色台词 → 不触发（叙事里已经对话过了，不再打断）
        - 规则 2 保底：连续 3 段无对话才强制（防纯文字荒漠，但不频繁）
        - LLM 精判为主力：阈值 0.5，判定导向"是否影响剧情走向"
        - 玩家主动权兜底：「我要说话」按钮随时可发起
        """
        chars = self._scene_chars(blocks)
        if not chars:
            return False, [], 0, "无在场角色"
        # 规则 1：开场第 1 段必触发（首次体验，优先于其他规则）
        if scene_num <= 1:
            return True, chars, 3, "开场互动"
        # 规则 0（v3.5.3）：场景已有充分对话 → 不触发（不再打断）
        dialogue_count = sum(1 for b in blocks if b.get("type") == "dialogue")
        if dialogue_count >= 2:
            return False, chars, 0, "场景已有充分对话"
        # 规则 2：连续 3 段无对话 → 强制（保互动频率下限）
        last_three = self.store.recent_scenes(novel_id, 3)
        if len(last_three) >= 3:
            no_dialogue = all(
                not any(b.get("type") == "dialogue" for b in sc.get("blocks", []))
                for sc in last_three[-3:]
            )
            if no_dialogue:
                return True, chars, 4, "连续三段无对话"
        # 规则 3：强冲突事件（真正需要玩家抉择的时刻）
        text = " ".join(b["content"] for b in blocks)
        strong_kw = ["拔剑", "刀架", "生死", "追杀", "真相大白", "身份暴露", "决裂", "挟持",
                     "下跪", "自尽", "灭口", "当场", "对质", "摊牌", "交易达成", "背叛"]
        hit = next((k for k in strong_kw if k in text), "")
        if hit:
            return True, chars, 5, f"重大事件: {hit}"
        # LLM 精判（规则未命中才调用）
        result = self._llm_judge_node(text, chars, state)
        if result is None:
            return False, chars, 2, "判定失败，默认不触发（玩家可主动介入）"
        is_node = bool(result.get("is_node"))
        confidence = float(result.get("confidence", 0.5))
        # v3.5.3: 阈值 0.5；刚对话过（<2 段）且置信度一般时抑制
        last_chat_gap = scene_num - state.get("_last_chat_scene", 0)
        if is_node:
            if confidence < 0.5:
                return False, chars, 0, f"置信度不足({confidence:.1f})"
            if confidence < 0.65 and last_chat_gap < 2:
                return False, chars, 0, "刚对话过且置信度一般"
        rounds = int(result.get("suggested_rounds", 3) or 3)
        rounds = max(2, min(rounds, 5))
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

    # ── Agenda 机制（v3.3：对话轨道）──
    def _generate_agenda(self, novel_id: str, chars: list, state: dict) -> Optional[dict]:
        """对话前生成议程：goal（目标）/ hooks（推进开关）/ boundaries（边界）/ exit（收尾条件）

        对话引擎据此"带目标对话"，PACT 提取后据此核对钩子是否命中——
        对话从自由漫游变为受控推进。
        """
        if not chars:
            return None
        s = state.get("state", {})
        casts = state.get("casts", {})
        char_briefs = []
        for name in chars[:3]:
            prof = (casts.get(name) or {}).get("profile", {})
            dna = prof.get("expression_dna", [])[:2]
            brief = "；".join(
                str(d.get("name", d))[:50] if isinstance(d, dict) else str(d)[:50] for d in dna
            ) or "（人设未蒸馏）"
            char_briefs.append(f"- {name}: {brief}")
        facts = [f for f in state.get("facts", []) if f.get("status") == "active"]
        user = (
            f"主线目标: {s.get('objective', '') or '（未定）'}\n"
            f"剧情标记: {'、'.join(s.get('flags', [])[-5:]) or '（无）'}\n"
            f"待兑现事实: {'；'.join(f.get('content', '') for f in facts[:5]) or '（无）'}\n"
            f"最近剧情: {state.get('summary', '')[:200]}\n"
            f"对话角色:\n{chr(10).join(char_briefs)}\n"
            f"请为这场对话制定议程（goal 必须与主线相关，hooks 是剧情推进开关）。"
        )
        raw = self._llm(AGENDA_SYSTEM, user, temperature=0.4, max_tokens=500)
        agenda = _parse_json(raw) if raw else None
        if not isinstance(agenda, dict):
            log.warning(f"Agenda 生成失败，使用默认议程: {novel_id}")
            agenda = {
                "goal": f"推进主线：{s.get('objective', '继续旅程')}",
                "hooks": [],
                "boundaries": [],
                "exit": {"min_rounds": 3, "condition": "读者已了解当前处境"},
            }
        # 规范化
        agenda.setdefault("goal", s.get("objective", "") or "继续旅程")
        agenda["hooks"] = [h for h in agenda.get("hooks", []) if isinstance(h, dict)][:4]
        agenda["boundaries"] = [str(b)[:80] for b in agenda.get("boundaries", [])[:3]]
        ex = agenda.get("exit") or {}
        try:
            min_rounds = max(2, min(int(ex.get("min_rounds", 3) or 3), 10))
        except (TypeError, ValueError):
            min_rounds = 3
        agenda["exit"] = {"min_rounds": min_rounds,
                          "condition": str(ex.get("condition", ""))[:100] or "目标已达成"}
        return agenda

    def verify_hooks(self, novel_id: str, agenda: dict) -> dict:
        """钩子核对：对话结束后检查议程的推进开关是否被触发（1 次轻量 LLM 调用）

        返回: {hook_hits: [{hook_index, hit, evidence}], all_hit, missing: [未触发钩子]}
        """
        hooks = (agenda or {}).get("hooks", [])
        if not hooks:
            return {"hook_hits": [], "all_hit": True, "missing": []}
        chat = self.store.recent_chats(novel_id, 40)
        transcript = []
        for i, e in enumerate(chat):
            if e.get("type") == "action_result":
                role = "行动结果"
            else:
                role = "读者" if e.get("role") == "user" else f"角色{e.get('speaker', '')}"
            transcript.append(f"[{i}] {role}: {e.get('content', '')[:150]}")
        hook_lines = "\n".join(
            f"- hook[{i}] trigger: {h.get('trigger', '')} → outcome: {h.get('outcome', '')}"
            for i, h in enumerate(hooks)
        )
        user = f"对话记录:\n" + "\n".join(transcript[-30:]) + f"\n\nAgenda 推进开关:\n{hook_lines}\n请逐条核对。"
        raw = self._llm(HOOK_VERIFY_SYSTEM, user, temperature=0.2, max_tokens=500)
        result = _parse_json(raw) if raw else None
        if not isinstance(result, dict):
            return {"hook_hits": [], "all_hit": False, "missing": [h.get("trigger", "") for h in hooks]}
        hits = result.get("hook_hits", []) or []
        hit_map = {}
        for hh in hits:
            if isinstance(hh, dict) and "hook_index" in hh:
                hit_map[int(hh["hook_index"])] = bool(hh.get("hit"))
        hook_hits = []
        missing = []
        for i, h in enumerate(hooks):
            evidence = ""
            hit = hit_map.get(i, False)
            for hh in hits:
                if isinstance(hh, dict) and hh.get("hook_index") == i:
                    evidence = str(hh.get("evidence", ""))[:80]
                    break
            hook_hits.append({"hook_index": i, "trigger": h.get("trigger", ""),
                              "hit": hit, "evidence": evidence})
            if not hit:
                missing.append(h.get("trigger", ""))
        return {"hook_hits": hook_hits, "all_hit": len(missing) == 0, "missing": missing}

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
        # v3.5.5: characters 字段结构（characters.protagonist）优先
        chars_field = novel.get("characters") or {}
        if not proto and isinstance(chars_field, dict):
            proto = chars_field.get("protagonist") or {}
        casts_preview = {}
        if proto.get("name"):
            casts_preview[proto["name"]] = {"role": "protagonist", "desc": str(proto.get("personality", ""))[:80]}
        for c in novel.get("supporting", []) or []:
            if isinstance(c, dict) and c.get("name"):
                casts_preview[c["name"]] = {"role": "supporting", "desc": str(c.get("personality", ""))[:80]}
        for c in novel.get("antagonist", []) or []:
            if isinstance(c, dict) and c.get("name"):
                casts_preview[c["name"]] = {"role": "antagonist", "desc": str(c.get("personality", ""))[:80]}
        # v3.5.5: characters 字段里的配角/反派
        if isinstance(chars_field, dict):
            for role_key in ("supporting", "antagonist"):
                group = chars_field.get(role_key) or []
                if isinstance(group, list):
                    for c in group:
                        if isinstance(c, dict) and c.get("name") and c["name"] not in casts_preview:
                            casts_preview[c["name"]] = {"role": role_key,
                                                        "desc": str(c.get("personality", ""))[:80]}
        return {
            "title": novel.get("title", novel_id),
            "genre": novel.get("genre", ""),
            "style": novel.get("style", ""),
            "protagonist_name": proto.get("name", ""),
            "protagonist": proto,   # v3.5.5: 完整主角信息（玩家扮演角色）
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
