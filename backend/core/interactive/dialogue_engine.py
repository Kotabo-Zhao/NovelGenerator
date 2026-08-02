"""DialogueEngine — 互动小说对话引擎（v3.0）

多轮对话（SSE 流式），核心设计（docs/interactive-novel-plan.html §6）：
1. 人设卡三明治：人设卡（心智模型/决策启发式/表达DNA/反模式/边界）+ 声音卡 + 剧情状态
2. @角色切换：玩家 @苏晚 切换交流对象；多角色插话支持
3. OOC 抽检（每 10 轮一次，轻量）
4. 对话轮次强制上限（防止上下文膨胀）：100 轮
5. 输出标记语言：单角色纯台词 / 多角色【角色名】分段

性能设计：
- 每轮单次 LLM 调用（流式），上下文固定 3-4k tokens
- 50 轮摘要压缩（复用 chapter_summarizer 思路，v1 简化：截断 + 摘要）
"""
from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Optional

from ..resilient_client import ResilientLLMClient
from .action_engine import ActionEngine

log = logging.getLogger(__name__)

CHAT_SYSTEM = """你是小说角色扮演引擎。你扮演小说中的角色，与读者（玩家的化身）对话。

规则：
1. 严格忠于角色人设卡与声音卡——口头禅、句式、情绪表达方式必须符合
2. 绝对禁止 OOC：不脱离角色身份说话，不解释自己在扮演，不说"作为AI"
3. 读者的输入可能带动作描写（如"（压低声音）…"），正常回应即可
4. 剧情状态中的 facts 会影响你的态度（读者承诺过/威胁过你，你要有相应的反应）
5. 单轮回复 30-120 字，口语化，自然，不要说教
6. 若多个角色在场且合适，可用【角色名】分段让其他角色插话（最多 2 个角色）
7. 【对话议程（Agenda）】这场对话你是有目的的（见 agenda）：
   - 对话围绕 goal 推进，不把 agenda 直接说破，自然引导读者
   - 读者触发了 hooks 的推进开关 → 按 outcome 给出相应反应（松口/让步/反击…）
   - 遵守 boundaries：boundaries 里的事你绝不主动透露，读者追问时也可回避
   - 读者闲聊/跑题超过 2 轮 → 你必须主动把话题拉回 goal（可自然："说回正事…"，也可带情绪：着急、警惕、不耐烦——符合人设）
8. 对话不是闲聊——但单轮回复仍是自然的角色回应，不要变成任务汇报
只输出你的台词。"""

DRIFT_SYSTEM = """你是对话轨道检测器。判断最近几轮对话是否偏离本次对话的目标（Agenda 的 goal），以及推进开关（hooks）是否已被读者触发。

判断标准：
- on_track=false：对话连续在闲聊/无关话题上，超过 2 轮未触及 goal 相关话题
- on_track=true：对话围绕 goal 推进，或刚被拉回
- hooks_hit：读者已做出与 hook.trigger 实质相符的行为（威胁/承诺/追问/交易…）

输出 JSON: {"on_track": true/false, "drift_rounds": 2, "drift_reason": "一句话原因", "hooks_hit": [{"trigger": "已触发的开关", "evidence": "对话原文摘录"}]}
只输出 JSON。"""

OOC_SYSTEM = """你是角色一致性检测器。检查最近 10 轮对话中，角色台词是否符合其人设卡。

判断标准：
- 说话风格（口头禅/句式/情绪表达）是否与声音卡一致
- 是否出现与角色身份/性格/立场矛盾的话
- 是否有明显出戏内容（解释AI身份、跳出角色）

输出 JSON: {"violations": [{"chat_index": 轮次序号, "reason": "一句话原因", "suggested": "建议的修正台词"}], "ok": true/false}
只输出 JSON。"""


class DialogueEngine:
    def __init__(self, client, model: str, store, engine=None):
        self.client = client
        self.model = model
        self.store = store
        self.engine = engine
        self._resilient = ResilientLLMClient(client, model)
        # v3.4: 行动引擎（对话即剧情——玩家输入行动直接推进）
        self.action = ActionEngine(client, model, store)

    # ── LLM 基础 ──
    def _llm(self, system: str, user: str, temperature: float = 0.8,
             max_tokens: int = 1200) -> Optional[str]:
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
            return str(content).strip() if content else None
        except Exception as e:
            log.warning(f"DialogueEngine LLM failed: {type(e).__name__}: {str(e)[:120]}")
            return None

    async def _llm_stream(self, system: str, user: str,
                          temperature: float = 0.85) -> AsyncIterator[str]:
        try:
            async for chunk in self._resilient.create_stream(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=1200,
            ):
                yield chunk
        except Exception as e:
            log.warning(f"DialogueEngine stream failed: {type(e).__name__}: {str(e)[:120]}")
            yield ""

    # ── 上下文组装 ──
    def _build_chat_prompt(self, state: dict, target_char: str,
                           history: list, last_action: Optional[dict] = None) -> str:
        parts = []
        # v3.4: 读者刚执行的行动（角色需即时反应，不复述行动本身）
        if last_action:
            parts.append(f"## 读者刚刚执行了行动: {last_action.get('summary', '')}"
                         f"{'（' + str(last_action.get('reason', ''))[:60] + '）' if last_action.get('reason') else ''}")
            parts.append("你的反应要求：对行动给出即时、符合人设的反应（言语/态度/小动作），"
                         "不要复述行动本身，不要总结发生了什么。")
        casts = state.get("casts", {})
        target = casts.get(target_char, {})
        prof = target.get("profile", {})
        if prof:
            parts.append(f"## 你扮演：{target_char}")
            mm = prof.get("mental_models", [])[:3]
            if mm:
                parts.append("心智模型（你的内在逻辑）:")
                for m in mm:
                    if isinstance(m, dict):
                        parts.append(f"- {m.get('name', '')}: {str(m.get('principle', m.get('description', '')))[:120]}")
                    else:
                        parts.append(f"- {str(m)[:120]}")
            heur = prof.get("decision_heuristics", [])[:4]
            if heur:
                parts.append("决策启发式（你的行为准则）:")
                for h in heur:
                    if isinstance(h, dict):
                        parts.append(f"- {h.get('trigger', '')} → {h.get('action', '')}"[:150])
                    else:
                        parts.append(f"- {str(h)[:150]}")
            dna = prof.get("expression_dna", [])[:4]
            if dna:
                parts.append("表达DNA（你的说话风格）:")
                for d in dna:
                    if isinstance(d, dict):
                        parts.append(f"- {d.get('name', '')}: {d.get('example', '')}"[:150])
                    else:
                        parts.append(f"- {str(d)[:150]}")
            anti = prof.get("anti_patterns", [])[:3]
            if anti:
                parts.append("反模式（你绝对不做的）:")
                for a in anti:
                    parts.append(f"- {a.get('pattern', a) if isinstance(a, dict) else a}"[:100])
            boundary = prof.get("boundary", {}) or {}
            rules = (boundary.get("rules") or boundary.get("anti_collapse_checks") or [])[:3]
            if rules:
                parts.append("防崩校验:")
                for r in rules:
                    parts.append(f"- {str(r)[:100]}")
        else:
            parts.append(f"## 你扮演：{target_char}（角色卡暂未蒸馏，按小说设定扮演）")

        # 声音卡
        voices = self._get_voices(state, target_char)
        if voices:
            parts.append(f"## 声音卡（说话风格必须符合）:")
            v = voices
            if v.get("catchphrases"):
                parts.append(f"口头禅: {', '.join(str(x) for x in v['catchphrases'][:2])}")
            if v.get("address_style"):
                parts.append(f"称呼: {str(v['address_style'])[:60]}")
            if v.get("sentence_style"):
                parts.append(f"句式: {str(v['sentence_style'])[:60]}")
            if v.get("emotion_expression"):
                parts.append(f"情绪表达: {str(v['emotion_expression'])[:60]}")

        # 剧情状态（态度依据）
        s = state.get("state", {})
        rel = s.get("relations", {}).get(target_char)
        if rel:
            parts.append(f"## 读者与你的关系值: {rel}/100（影响你的态度）")
        facts = [f for f in state.get("facts", []) if f.get("status") == "active"
                 and f.get("subject") == "player" and f.get("target") == target_char]
        if facts:
            parts.append("读者对你做过的事（你要有相应态度）:")
            for f in facts[:4]:
                parts.append(f"- [{f.get('type')}] {f.get('content')}")
        if s.get("objective"):
            parts.append(f"当前主线: {s['objective']}")

        # v3.3: Agenda 议程注入（对话轨道——带目标对话，不走偏）
        agenda = state.get("agenda") or {}
        if agenda.get("goal"):
            parts.append("## 本次对话议程（你的目的，对话围绕它推进）:")
            parts.append(f"目标: {agenda['goal']}")
            hooks = agenda.get("hooks", [])
            if hooks:
                parts.append("推进开关（读者做出这些事 → 剧情推进）:")
                for h in hooks[:4]:
                    trigger = str(h.get("trigger", ""))[:60]
                    outcome = str(h.get("outcome", ""))[:60]
                    parts.append(f"- 读者「{trigger}」→ {outcome}")
            if agenda.get("boundaries"):
                parts.append("你绝不主动透露:")
                for b in agenda["boundaries"][:3]:
                    parts.append(f"- {b}")
            exit_c = (agenda.get("exit") or {}).get("condition", "")
            if exit_c:
                parts.append(f"收尾条件: {exit_c[:80]}")
            parts.append("收尾策略: 收尾条件达成后，在合适时机主动自然收尾（告别/约定/转移话题），不必等读者操作；若读者明显想继续聊，可再回应 1-2 轮再收尾")
        # 离题拉回提示（drift 轻校验发现跑偏后注入）
        drift_note = state.get("drift_note")
        if drift_note:
            parts.append(f"⚠ 检测到话题跑偏: {drift_note}——本轮必须自然地把话题拉回议程目标，不要再顺着闲聊")

        # 对话历史
        if history:
            parts.append("## 对话历史（最近）:")
            for h in history[-16:]:
                role = "读者" if h.get("role") == "user" else h.get("speaker", target_char)
                parts.append(f"{role}: {h.get('content', '')[:200]}")
        parts.append(f"## 现在读者对你说：\n{history[-1].get('content', '') if history else ''}")
        return "\n".join(parts)

    def _get_voices(self, state: dict, char_name: str) -> dict:
        """获取声音卡（从 engine bible voices 读取，v1 简化）"""
        engine = self.engine
        if engine is None:
            return {}
        try:
            voices = engine.get_character_voices(state.get("novel_id", ""))
            if voices:
                return voices.get(char_name, {}) or {}
        except Exception:
            pass
        return {}

    # ── 对话（SSE）──
    async def chat_stream(self, novel_id: str, user_input: str,
                          target_char: Optional[str] = None) -> AsyncIterator[dict]:
        """玩家发消息 → 角色回复（流式）

        Yields: {type: chat_chunk/chat_end/ooc_check/error/done}
        """
        state = self.store.load_state(novel_id)
        if state is None:
            yield {"type": "error", "message": "互动存档不存在，请先 start"}
            return

        # @角色 切换
        target = target_char
        if not target:
            target = self._detect_target(state, user_input)
        if not target:
            casts = state.get("casts", {})
            # 默认当前在场第一个有 profile 的角色
            for name, c in casts.items():
                if c.get("profile"):
                    target = name
                    break
        if not target:
            target = state.get("node_chars") or list(casts.keys())
            target = target[0] if target else ""
        if not target:
            yield {"type": "error", "message": "没有可对话的角色"}
            return

        # 清理玩家输入中的 @
        clean_input = user_input.strip()
        if "@" in clean_input:
            import re
            clean_input = re.sub(r"@\S+", "", clean_input).strip() or "…"

        # 记录对话
        entry_user = {"role": "user", "content": clean_input, "speaker": "player",
                      "ts": time.strftime("%H:%M:%S")}
        self.store.append_chat(novel_id, entry_user)

        # v3.4: 行动识别——玩家输入是"剧情操作"（上车/推门/答应/拒绝…）→ 直接推进剧情
        action = self.action.detect_action(clean_input, state)
        if action:
            yield {"type": "action_detect", "action_type": action.get("type", "other"),
                   "summary": action.get("summary", ""), "end_chat": action.get("end_chat", False)}
            applied = self.action.apply_action(novel_id, action)
            changed = applied.get("changed", [])
            # 行动结果场景（流式，1-3 句）
            async for ev in self.action.action_scene_stream(novel_id, action, changed):
                yield ev
            # 行动改变场景（离开/进入新地点）→ 对话自然收尾
            if action.get("end_chat"):
                yield {"type": "action_done", "end_chat": True, "action": action}
                # 行动命中 agenda hooks 的核对由 end-chat 的 verify_hooks 统一处理（行动已入日志）
                yield {"type": "done"}
                return
            # 行动后角色反应（对话继续）
            yield {"type": "action_done", "end_chat": False, "action": action}
            last_action = action
        else:
            last_action = None

        # 组装上下文
        history = self.store.recent_chats(novel_id, 20)
        history = [h for h in history if h.get("role") in ("user", "assistant")
                   and h.get("type") != "action_result"]
        prompt = self._build_chat_prompt(state, target, history, last_action)

        # 流式回复
        collected = []
        yield {"type": "chat_chunk", "speaker": target, "content": ""}
        try:
            async for chunk in self._llm_stream(CHAT_SYSTEM, prompt):
                if chunk:
                    collected.append(chunk)
                    yield {"type": "chat_chunk", "speaker": target, "content": chunk}
        except Exception as e:
            log.error(f"Chat stream error: {e}")
            yield {"type": "error", "message": f"对话失败: {type(e).__name__}"}
            return

        reply = "".join(collected).strip()
        if not reply:
            reply = "（沉默片刻）……你说什么？"
            yield {"type": "chat_chunk", "speaker": target, "content": reply}

        # 多角色分段解析（【角色名】...）
        segments = self._parse_segments(reply, target)
        entry_assistant = {"role": "assistant", "speaker": target,
                           "content": reply, "segments": segments,
                           "ts": time.strftime("%H:%M:%S")}
        self.store.append_chat(novel_id, entry_assistant)

        yield {"type": "chat_end", "speaker": target, "content": reply, "segments": segments}

        # OOC 抽检（每 10 轮）
        chat_count = len(self.store.recent_chats(novel_id, 100))
        if chat_count % 10 == 0:
            viol = self._ooc_check(novel_id, target, state)
            yield {"type": "ooc_check", "violations": viol}

        # v3.3: 对话轨道轻校验（每 4 轮）——检测跑偏，注入下轮拉回
        if chat_count % 4 == 0:
            drift = self._check_drift(novel_id, target, state)
            state = self.store.load_state(novel_id) or state
            if drift is None:
                state.pop("drift_note", None)
            else:
                if not drift.get("on_track", True):
                    state["drift_note"] = str(drift.get("drift_reason", "话题跑偏了"))[:100]
                else:
                    state.pop("drift_note", None)
                self.store.save_state(novel_id, state)
            yield {"type": "drift_check", "on_track": bool(drift.get("on_track")) if drift else True,
                   "reason": str(drift.get("drift_reason", "")) if drift else ""}
            # v3.3.1: 自然收尾检测——超过最少轮数且对话在轨道上 → 提示可收尾
            if drift is not None and drift.get("on_track", True):
                agenda = state.get("agenda") or {}
                ex = agenda.get("exit") or {}
                min_rounds = ex.get("min_rounds", 3)
                if chat_count >= min_rounds:
                    yield {"type": "natural_end",
                           "message": "对话目标已达成，可以继续剧情了",
                           "chat_rounds": chat_count}

        yield {"type": "done"}

    def _check_drift(self, novel_id: str, char_name: str, state: dict) -> Optional[dict]:
        """对话轨道轻校验：最近 4 轮是否偏离议程目标 + 哪些推进开关已被触发"""
        agenda = state.get("agenda") or {}
        if not agenda.get("goal"):
            return None  # 无议程不校验（普通闲聊模式兼容）
        recent = self.store.recent_chats(novel_id, 6)
        transcript = []
        for h in recent:
            role = "读者" if h.get("role") == "user" else h.get("speaker", char_name)
            transcript.append(f"{role}: {h.get('content', '')[:120]}")
        if not transcript:
            return None
        hooks = agenda.get("hooks", [])
        hook_lines = "\n".join(
            f"- hook[{i}] trigger: {h.get('trigger', '')}" for i, h in enumerate(hooks)
        ) or "（无）"
        user = (
            f"议程目标: {agenda['goal']}\n"
            f"推进开关:\n{hook_lines}\n"
            f"最近对话:\n" + "\n".join(transcript[-4:])
        )
        raw = self._llm(DRIFT_SYSTEM, user, temperature=0.2, max_tokens=300)
        return _parse_json(raw) if raw else None

    def _detect_target(self, state: dict, text: str) -> Optional[str]:
        """从 @角色名 提取交流对象"""
        import re
        m = re.search(r"@([\u4e00-\u9fff\w]{1,8})", text)
        if m:
            name = m.group(1)
            casts = state.get("casts", {})
            if name in casts:
                return name
            # 模糊匹配
            for cn in casts:
                if name in cn or cn in name:
                    return cn
        return None

    def _parse_segments(self, reply: str, default_speaker: str) -> list:
        """解析多角色回复 → [{speaker, content}]；纯单角色时返回单段"""
        segments = []
        import re
        parts = re.split(r"【([^】]+)】", reply)
        # parts: [前文, 角色1, 内容1, 角色2, 内容2, ...]
        buffer = parts[0].strip() if parts and parts[0].strip() else ""
        i = 1
        while i + 1 < len(parts):
            speaker, content = parts[i].strip(), parts[i + 1].strip()
            if buffer:
                segments.append({"speaker": default_speaker, "content": buffer})
                buffer = ""
            segments.append({"speaker": speaker, "content": content})
            i += 2
        if buffer:
            segments.append({"speaker": default_speaker, "content": buffer})
        if not segments:
            segments = [{"speaker": default_speaker, "content": reply}]
        return segments

    def _ooc_check(self, novel_id: str, char_name: str, state: dict) -> list:
        """OOC 抽检最近 10 轮（轻量）"""
        recent = self.store.recent_chats(novel_id, 10)
        transcript = []
        for h in recent:
            role = "读者" if h.get("role") == "user" else h.get("speaker", char_name)
            transcript.append(f"{role}: {h.get('content', '')[:150]}")
        if not transcript:
            return []
        casts = state.get("casts", {})
        prof = casts.get(char_name, {}).get("profile", {})
        brief = ""
        if prof:
            dna = prof.get("expression_dna", [])[:2]
            anti = prof.get("anti_patterns", [])[:2]
            brief = "表达DNA: " + "；".join(
                str(d.get("name", d))[:50] if isinstance(d, dict) else str(d)[:50] for d in dna
            ) + " 反模式: " + "；".join(
                str(a.get("pattern", a))[:50] if isinstance(a, dict) else str(a)[:50] for a in anti
            )
        user = f"角色: {char_name}\n人设参考: {brief or '（无）'}\n对话记录:\n" + "\n".join(transcript)
        raw = self._llm(OOC_SYSTEM, user, temperature=0.2, max_tokens=400)
        result = _parse_json(raw) if raw else {}
        return result.get("violations", []) if isinstance(result, dict) else []


def _parse_json(content: str) -> Optional[dict]:
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
