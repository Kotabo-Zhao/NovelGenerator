"""ActionEngine — 互动小说行动引擎（v3.4）

**核心能力：对话即剧情——玩家输入的行动直接推进剧情，不等按钮。**

老赵需求："角色要玩家上车，玩家输入上车，剧情应该就可以推进到玩家上车，以此类推。"
即：玩家输入不只是"话"，还可能是"剧情操作"（上车/推门/拔剑/答应/拒绝/交出物品…）。
系统识别行动 → 当场更新剧情状态（location/flags/inventory/relations）→ 生成行动结果场景。

流程（chat_stream 每轮集成）：
1. 规则预筛（零成本）：括号动作描写 / 高置信行动词 / 低置信回应词
2. LLM 精判（只对候选，~300 tokens）：is_action + type + state_updates + end_chat
3. 执行：写回 state → 生成行动结果场景（流式，1-3 句）→ 角色后续反应
4. 非行动输入 → 走原对话链路（零额外成本）

成本设计：多数玩家输入是对话（不触发检测），行动输入平均每轮 +1 次轻调用。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import AsyncIterator, Optional

from ..resilient_client import ResilientLLMClient

log = logging.getLogger(__name__)

# ── 规则预筛 ──
# 括号动作描写（（压低声音）（沉默片刻）…）→ 必为行动
ACTION_DESC_RE = re.compile(
    r"（[^）]{1,30}(?:走|上|下|进|出|开|关|坐|站|拿|递|拔|放|看|推|跟|点|摇|转|跪|抱|拍|"
    r"摸|掏|捡|踢|跳|冲|挡|闪|压|低|沉|默|笑|哭|叹|瞪|盯|握|拉|拽|扯|蹲|躺|跑|追|躲|藏|"
    r"翻|搜|查|找|问|答|说|讲|告|亲|吻|拥|搂|喝|吃|用)[^）]{0,30}）")

# 动作动词表（高置信：命中 → 必为行动，LLM 只负责提取状态）
_ACTION_VERBS = (
    "上车|下车|上马|下马|上楼|下楼|上船|进去|进来|出去|出来|进屋|出屋|进城|出城|"
    "开门|推门|推开|关门|锁门|开窗|转身|回头|跟上|跟着|坐下|站起来|起身|"
    "走到|走进|走出|来到|到达|离开|逃走|逃跑|逃离|躲进|躲藏|藏进|尾随|追赶|"
    "推开门|跟着走|跟着你|拔剑|拔刀|出手|攻击|跪下|叩头|低头|鞠躬|行礼|作揖|抱拳|点头|摇头|"
    "掏出|拿出|交出|交给|递上|接过|放回|收起|捡起|穿上|脱下|戴上|摘下|"
    "答应|拒绝|同意|接受|成交|收下|归还|签字|画押|发誓|承诺|威胁|示好|坦白|隐瞒|"
    "倒茶|斟酒|敬酒|服药|喝下|吃下|使用|启动|按下|拉下|搬开|踢开|"
    "回家|回府|回宫|回房|握住|拉起|抱住|亲吻|拥抱|搂住|走|走开"
)

# 高置信行动词（允许"好，上车"前缀 + "你/他/了"后缀）
HIGH_CONF_ACTION_RE = re.compile(
    r"^(?:(?:我|咱|咱们|我们|人家)?(?:这就|现在|马上|要|想|打算)?|(?:好|行|嗯|成|好的)?[，,、\s]*|(?:把|向|对|朝)[\u4e00-\u9fff]{1,6})?(?:"
    + _ACTION_VERBS
    + r")(?:了|吧|你|他|她|下|下去|进来|进去|出来|出去|过来|过去|一下|走|门|给(?:你|他|她)?|"
    r"走了进去|走进去|走了进来|走进来|走了出去|走出去|走了出来|走出来|走了过去|走过去|"
    r"了(?:走(?:进去|出来|过去|过来)?)?)?[。！!]?$"
)

# 低置信行动词（"好/行/可以/走"等——可能是应答，LLM 精判）
LOW_CONF_ACTION_RE = re.compile(r"^(?:好|行|可以|好的|行吧|好吧|嗯|走|走吧|那就|就这么办|成交|听你的|随你|不|不要|不行|拒绝|算了)[。！!]?$")

# 裸行动词（"上车" 两个字本身）
NAKED_ACTION_RE = re.compile(r"^(?:上车|下车|进去|出来|走吧|开门|推门|跟上|坐下|过来|等等|停下|住手|别动|放手|松手|给|拿来|走开|退下|成交)[。！!]?$")

ACTION_DETECT_SYSTEM = """你是互动小说行动识别器。判断读者刚输入的内容是"剧情行动"还是"普通对话"。

**剧情行动**：读者通过输入执行了改变剧情状态的动作——
- 身体动作：上车/推门/离开/拔剑/跪下/跟上/躲藏…
- 交易承诺：答应/拒绝/成交/签字/交出物品…
- 物品使用：掏出/递上/服用/使用…
- 态度行动：威胁/示好/坦白/隐瞒…

**普通对话**：提问、陈述、闲聊、情绪表达、信息交换——不改变剧情状态。

判断规则：
1. 角色发出邀请/命令（"上车""跟我走"）后，读者输入"上车""好，上车""走" → 是行动（执行邀请）
2. 纯回应词"好""行""可以"：若紧跟角色请求/交易 → 行动（accept）；若只是敷衍 → 对话
3. 括号动作描写 → 行动
4. 读者明确说"我要…/我想…" + 具体动作 → 行动
5. 不确定时 is_action=false（宁可是对话，不打断体验）

输出 JSON:
{"is_action": true/false, "type": "move/interact/accept/refuse/use/combat/leave/other",
 "summary": "一句话描述读者做了什么",
 "state_updates": {"location": "新地点或空", "flags": ["新flag或空"], "inventory": ["物品变化或空"],
                   "relations": {"角色名": "关系变化描述或空"}},
 "end_chat": false, "reason": "一句话依据"}
只输出 JSON。"""

ACTION_SCENE_SYSTEM = """你是互动小说即时行动导演。读者刚刚执行了一个剧情行动，你需要生成行动的结果场景。

要求：
1. 1-3 句，标记语言：【旁白】叙事 / 【角色名】在场角色的反应台词
2. 行动结果必须真实反映在叙事里（上车→车身晃动、车门合拢；拔剑→气氛骤变；答应→对方态度变化）
3. 行动改变了场景（离开/进入新地点）→ 给新地点一笔环境描写
4. 保持小说文笔与世界观风格，不要机械描述
5. 若【角色名】给了反应台词，该台词要符合角色人设

只输出标记语言文本，不要解释。"""


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


def rule_prescreen(user_input: str) -> Optional[dict]:
    """规则预筛：命中高置信 → 直接判定为行动；命中低置信 → 需要 LLM 精判；未命中 → 对话

    返回: {"candidate": True/False, "forced": True/False, "hint": "预筛依据"}
    - forced=True：必为行动（括号动作/高置信词）
    - candidate=True：疑似行动（低置信词），LLM 精判
    - candidate=False：普通对话，零成本跳过
    """
    text = user_input.strip()
    if not text:
        return {"candidate": False, "forced": False, "hint": "空输入"}
    if ACTION_DESC_RE.search(text):
        return {"candidate": True, "forced": True, "hint": "括号动作描写"}
    if NAKED_ACTION_RE.match(text) or HIGH_CONF_ACTION_RE.match(text):
        return {"candidate": True, "forced": True, "hint": "高置信行动词"}
    if LOW_CONF_ACTION_RE.match(text):
        return {"candidate": True, "forced": False, "hint": "低置信回应词，需精判"}
    return {"candidate": False, "forced": False, "hint": "普通对话"}


class ActionEngine:
    def __init__(self, client, model: str, store):
        self.client = client
        self.model = model
        self.store = store
        self._resilient = ResilientLLMClient(client, model)

    # ── LLM 基础 ──
    def _llm(self, system: str, user: str, temperature: float = 0.3,
             max_tokens: int = 600) -> Optional[str]:
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
            log.warning(f"ActionEngine LLM failed: {type(e).__name__}: {str(e)[:120]}")
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
                max_tokens=800,
            ):
                yield chunk
        except Exception as e:
            log.warning(f"ActionEngine stream failed: {type(e).__name__}: {str(e)[:120]}")
            yield ""

    # ── 行动识别 ──
    def detect_action(self, user_input: str, state: dict) -> Optional[dict]:
        """识别玩家输入是否行动。返回行动意图 dict 或 None（普通对话/判定失败）。

        返回: {type, summary, state_updates, end_chat, reason, forced}
        """
        pre = rule_prescreen(user_input)
        if not pre["candidate"]:
            return None
        # 强制行动（括号动作/高置信词）也要 LLM 提取状态——但可以给强提示
        s = state.get("state", {})
        agenda = state.get("agenda") or {}
        hooks = agenda.get("hooks", []) or []
        hook_lines = "\n".join(
            f"- hook[{i}] trigger: {h.get('trigger', '')} → outcome: {h.get('outcome', '')}"
            for i, h in enumerate(hooks)
        ) or "（无）"
        chars = list(state.get("casts", {}).keys())
        user = (
            f"当前地点: {s.get('location', '') or '（未定）'}\n"
            f"主线目标: {s.get('objective', '') or '（未定）'}\n"
            f"在场角色: {', '.join(chars) or '（无）'}\n"
            f"对话议程推进开关:\n{hook_lines}\n"
            f"预筛: {pre['hint']}（forced={pre['forced']}）\n"
            f"读者输入: {user_input[:200]}\n"
            f"请识别这是否是剧情行动。"
        )
        raw = self._llm(ACTION_DETECT_SYSTEM, user, temperature=0.2, max_tokens=400)
        result = _parse_json(raw) if raw else None
        if not isinstance(result, dict):
            return None
        is_action = bool(result.get("is_action"))
        # 预筛强制行动时，即使 LLM 犹豫也按行动处理（LLM 负责状态提取）
        if not is_action and not pre["forced"]:
            return None
        action = {
            "type": str(result.get("type", "other"))[:20] or "other",
            "summary": str(result.get("summary", user_input[:60]))[:120],
            "state_updates": result.get("state_updates") or {},
            "end_chat": bool(result.get("end_chat", False)),
            "reason": str(result.get("reason", ""))[:100],
            "forced": pre["forced"],
        }
        # 规范化 state_updates
        su = action["state_updates"]
        if not isinstance(su, dict):
            su = {}
        action["state_updates"] = {
            "location": str(su.get("location", ""))[:60] if su.get("location") else "",
            "flags": [str(f)[:60] for f in (su.get("flags") or [])[:3]],
            "inventory": [str(i)[:60] for i in (su.get("inventory") or [])[:3]],
            "relations": {str(k)[:30]: str(v)[:60] for k, v in (su.get("relations") or {}).items() if isinstance(k, str)},
        }
        return action

    # ── 行动执行：更新状态 ──
    def apply_action(self, novel_id: str, action: dict) -> dict:
        """把行动结果写回 state（location/flags/inventory/relations）"""
        state = self.store.load_state(novel_id)
        if state is None:
            return {}
        su = action.get("state_updates") or {}
        s = state.setdefault("state", {})
        changed = []
        if su.get("location"):
            old = s.get("location", "")
            if old != su["location"]:
                s["location"] = su["location"]
                changed.append(f"地点: {old or '?'} → {su['location']}")
        for f in su.get("flags", []):
            if f and f not in s.setdefault("flags", []):
                s["flags"].append(f)
                changed.append(f"flag: {f}")
        inv = s.setdefault("inventory", [])
        for item in su.get("inventory", []):
            if item and item not in inv:
                inv.append(item)
                changed.append(f"物品: {item}")
        rel_map = s.setdefault("relations", {})
        for k, v in (su.get("relations") or {}).items():
            if k and k != "player":
                rel_map[k] = v
                changed.append(f"关系[{k}]: {v}")
        # 记录最近一次行动（PACT/场景生成可感知）
        state["last_action"] = {
            "type": action.get("type", "other"),
            "summary": action.get("summary", ""),
            "ts": time.strftime("%H:%M:%S"),
        }
        self.store.save_state(novel_id, state)
        return {"changed": changed, "state": state}

    # ── 行动结果场景（流式）──
    async def action_scene_stream(self, novel_id: str, action: dict,
                                  changed: list) -> AsyncIterator[dict]:
        """生成行动结果场景（1-3 句，流式）

        Yields: {type: action_chunk/action_end/error}
        """
        state = self.store.load_state(novel_id)
        if state is None:
            yield {"type": "error", "message": "互动存档不存在"}
            return
        s = state.get("state", {})
        chars = list(state.get("casts", {}).keys())
        char_briefs = []
        for name in chars[:3]:
            prof = (state.get("casts", {}).get(name) or {}).get("profile", {})
            dna = prof.get("expression_dna", [])[:1]
            brief = str(dna[0].get("name", dna[0]))[:40] if dna else "（人设未蒸馏）"
            char_briefs.append(f"- {name}: {brief}")
        user = (
            f"小说: 《{state.get('title', '')}》 {state.get('genre', '')}·{state.get('style', '')}\n"
            f"当前地点: {s.get('location', '') or '（未定）'}\n"
            f"读者行动: {action.get('summary', '')}\n"
            f"行动类型: {action.get('type', 'other')}\n"
            f"状态变化: {'；'.join(changed) or '（无）'}\n"
            f"在场角色:\n{chr(10).join(char_briefs) or '（无）'}\n"
            f"生成这段行动的结果场景（1-3 句）。"
        )
        collected = []
        yield {"type": "action_chunk", "content": ""}
        try:
            async for chunk in self._llm_stream(ACTION_SCENE_SYSTEM, user):
                if chunk:
                    collected.append(chunk)
                    yield {"type": "action_chunk", "content": chunk}
        except Exception as e:
            log.error(f"Action scene stream error: {e}")
            yield {"type": "error", "message": f"行动场景生成失败: {type(e).__name__}"}
            return
        text = "".join(collected).strip()
        if not text:
            text = f"【旁白】你做了这个决定，{state.get('title', '故事')}的走向因此改变。"
            yield {"type": "action_chunk", "content": text}
        # 落盘（进 chat_logs 保持时序，标记 action_result）
        self.store.append_chat(novel_id, {
            "role": "assistant", "speaker": "旁白", "type": "action_result",
            "content": text, "action": action.get("type", "other"),
            "ts": time.strftime("%H:%M:%S"),
        })
        yield {"type": "action_end", "content": text, "action": action}
