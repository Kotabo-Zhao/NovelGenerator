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


def clean_location(loc) -> str:
    """v3.5.36: 地点清洗——列表式字符串只取第一段（'上海，陆家嘴、前滩'→'上海'）"""
    if not loc:
        return ""
    loc = str(loc).strip()
    for sep in ("，", ",", "；", ";"):
        if sep in loc:
            return loc.split(sep)[0].strip()[:60]
    return loc[:60]

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
    "走到|走进|走出|来到|到达|离开|逃走|逃跑|逃离|躲进|躲藏|藏进|尾随|追赶|追上|出发|"
    "追(?:上|到|来|去)?|"
    "推开门|跟着走|跟着你|拔剑|拔刀|出手|攻击|跪下|叩头|低头|鞠躬|行礼|作揖|抱拳|点头|摇头|"
    "掏出|拿出|交出|交给|递上|接过|放回|收起|捡起|穿上|脱下|戴上|摘下|"
    "答应|拒绝|同意|接受|成交|收下|归还|签字|画押|发誓|承诺|威胁|示好|坦白|隐瞒|"
    "倒茶|斟酒|敬酒|服药|喝下|吃下|使用|启动|按下|拉下|搬开|踢开|"
    "回家|回府|回宫|回房|握住|拉起|抱住|亲吻|拥抱|搂住|走|走开|杀(?:死|了|掉|人|害)?"
)

# 高置信行动词（允许"好，上车"前缀 + "你/他/了"后缀）
HIGH_CONF_ACTION_RE = re.compile(
    r"^(?:(?:我|咱|咱们|我们|人家)?(?:这就|现在|马上|要|想|打算)?|(?:好|行|嗯|成|好的)?[，,、\s]*|(?:别|别想|别要)?|(?:把|向|对|朝)[\u4e00-\u9fff]{1,6})?(?:"
    + _ACTION_VERBS
    + r")(?:了|吧|你|他|她|下|下去|进来|进去|出来|出去|过来|过去|一下|走|门|给(?:你|他|她)?|"
    r"这[\u4e00-\u9fff]{0,4}|那[\u4e00-\u9fff]{0,4}|"
    r"走了进去|走进去|走了进来|走进来|走了出去|走出去|走了出来|走出来|走了过去|走过去|"
    r"了(?:走(?:进去|出来|过去|过来)?)?)?[。！!]?$"
)

# 低置信行动词（"好/行/可以/走"等——可能是应答，LLM 精判）
LOW_CONF_ACTION_RE = re.compile(r"^(?:好|行|可以|好的|行吧|好吧|嗯|走|走吧|那就|就这么办|成交|听你的|随你|不|不要|不行|拒绝|算了)[。！!]?$")

# 裸行动词（"上车" 两个字本身）
NAKED_ACTION_RE = re.compile(r"^(?:上车|下车|进去|出来|走吧|开门|推门|跟上|坐下|过来|等等|停下|住手|别动|放手|松手|给|拿来|走开|退下|成交)[。！!]?$")

# v3.5.7: 句首行动动词 + 补充内容（"我答应你，今晚就去"这类长句）→ LLM 精判
# 之前整句匹配漏掉带补充的长句 → 全部当闲聊，剧情不动——这是"行为不推进剧情"的机制根因
VERB_PREFIX_RE = re.compile(
    r"^(?:(?:我|咱|咱们|我们|人家)?(?:这就|现在|马上|要|想|打算|就|已经|去|来)?)(?:"
    + _ACTION_VERBS + r")[\u4e00-\u9fff]{0,24}"
)

# ── 行动护栏（v3.4.1 Guardrails）──

# 超现实拦截词（规则层硬拦截：本世界不存在的能力/物品）
UNREALISTIC_RE = re.compile(
    r"瞬移|穿越|时空|时间倒流|平行宇宙|隐身|隐形|飞天|飞行|腾云|御剑|复活|起死回生|"
    r"点石成金|凭空|无中生有|一百万|一个亿|一千万|神器|无敌|金刚不坏|读心|隔空取物|"
    r"千里之外|长生不老|不死之身|召唤神龙|核弹|原子弹|ufo|UFO|光速|超能力|"
    r"飞(?:上|到|向|在)?天|炸(?:了|掉|毁)?(?:整[个座]|全)?(?:城|楼|山|村|镇|屋|房|桥|船)"
)

# 高破坏性行动（杀/毁关键角色与关键物品——通常不会得逞，除非剧情允许）
DESTRUCTIVE_RE = re.compile(
    r"杀(?:死|了|掉|人)?|宰了|灭口|毁(?:了|掉)?|烧(?:了|掉|毁)?|撕(?:了|掉|毁)?|"
    r"摔碎|砸碎|砍(?:死|掉|伤)?|刺(?:死|掉|伤)?|毒(?:死|掉|杀)?|掐死|勒死|枪毙"
)

# 凭空获得类（防"我获得了一百万"状态污染）
UNREAL_GAIN_RE = re.compile(r"(?:获得|得到|赚到|捡到|赢来)(?:了|到)?(?:一百[万两]|一千[万两]|一亿|一个亿|万两黄金|绝世武功|神功|内力大增|修为大涨)")

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

【行动边界（必须遵守，v3.5.23 改为 LLM 裁决）】：
- 越界判断要【结合本小说世界观】：修真/玄幻/奇幻世界里的飞行、瞬移、御剑、
  法术可能是正常能力（不越界）；现实/都市/历史世界里这些才越界
- 关键角色（主角/重要配角/反派）不可能被读者一句话杀死或重伤——他们的命运由剧情
  推进决定；读者可"出手尝试"，结果通常是被拦下/失手/悬念
- 读者不能凭空获得巨额财富/绝世能力（可以"索要/抢夺/寻找"，结果由剧情决定）
- 违背主线 ≠ 越界：读者可以拒绝、背叛、逃跑——这是剧情分支，正常识别为行动
- blocked=true 仅当行动【确定违反世界观且无合理解释】时输出

输出 JSON:
{"is_action": true/false, "type": "move/interact/accept/refuse/use/combat/leave/other",
 "summary": "一句话描述读者做了什么",
 "state_updates": {"location": "新地点或空", "flags": ["新flag或空"], "inventory": ["物品变化或空"],
                   "relations": {"角色名": "关系变化描述或空"}},
 "end_chat": false, "blocked": false, "reason": "一句话依据"}
只输出 JSON。"""

ACTION_SCENE_SYSTEM = """你是互动小说即时行动导演。读者刚刚执行了一个剧情行动，你需要生成行动的结果场景。

要求：
1. 1-3 句，标记语言：【旁白】叙事 / 【角色名】在场角色的反应台词
2. 行动结果必须真实反映在叙事里（上车→车身晃动、车门合拢；拔剑→气氛骤变；答应→对方态度变化）
3. 行动改变了场景（离开/进入新地点）→ 给新地点一笔环境描写（地点/氛围/光线）
4. 保持小说文笔与世界观风格，不要机械描述
5. 若【角色名】给了反应台词，该台词要符合角色人设
6. v3.5.27 角色白名单：反应台词只来自"在场角色"名单，严禁名单外角色出现
7. v3.5.27 铁律：严禁生成【主角名】的台词——主角言行由读者决定，需要反应时用旁白写"你…"

【行动结果的三条铁律】：
1. **行动可能失败**：考虑读者实力、在场角色、关系值、剧情阶段——行动可以部分成功或失败（对方躲开/被拦下/自己犹豫/时机不对）。失败也是剧情，不是系统限制
2. **关键角色不可被秒杀**：读者试图杀死/重伤主角、重要配角、反派 → 行动结果必须被拦下/失手/出现变数（有人挡刀、对方早有防备、读者下不了手），留下悬念，除非剧情已到决战
3. **超现实尝试**：读者尝试本世界不存在的事（飞行/瞬移/凭空变出东西）→ 结果是无果的尝试（纹丝不动/被嘲笑/尴尬收场），并让在场角色对此有符合人设的反应

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
    """规则预筛（v3.5.23：只做【成本门卫】+【LLM 失败兜底】，不做判断）

    返回: {"candidate": True/False, "forced": True/False, "hint": "预筛依据"}
    - candidate=True：值得花一次轻量 LLM 调用精判
    - candidate=False：大概率对话（简短/无行动特征），零成本跳过——判断权仍在 LLM
    - forced=True：LLM 不可用时的兜底默认（仅 LLM 失败时生效，不否决 LLM 结论）
    """
    text = user_input.strip()
    if not text:
        return {"candidate": False, "forced": False, "hint": "空输入"}
    if ACTION_DESC_RE.search(text):
        return {"candidate": True, "forced": True, "hint": "括号动作描写"}
    if NAKED_ACTION_RE.match(text) or HIGH_CONF_ACTION_RE.match(text):
        return {"candidate": True, "forced": True, "hint": "高置信行动词"}
    # v3.5.7: 句首动词+补充长句 → LLM 精判（forced=False，LLM 可纠偏为对话）
    if VERB_PREFIX_RE.match(text):
        return {"candidate": True, "forced": False, "hint": "句首行动动词，需精判"}
    if LOW_CONF_ACTION_RE.match(text):
        return {"candidate": True, "forced": False, "hint": "低置信回应词，需精判"}
    # v3.5.23: 第一人称主语即值得 LLM 判断——行动/对话都可能，
    # "我虽然不舍，但还是把玉佩递了回去"这类长句行动不被动词表漏判
    if re.match(r"^(?:我|咱|咱们|我们|人家)", text):
        return {"candidate": True, "forced": False, "hint": "第一人称陈述，需精判"}
    return {"candidate": False, "forced": False, "hint": "普通对话"}


# v3.5.8: 状态快照（SSE 事件携带，前端实时更新状态卡，免滚动查看）
def _state_snapshot(state: dict) -> dict:
    s = state.get("state", {}) or {}
    rel = {}
    for k, v in (s.get("relations") or {}).items():
        rel[str(k)[:12]] = v if isinstance(v, (int, float)) else str(v)[:20]
    # v3.5.9: 事件时间线（状态卡"刚发生的事"）
    events = [{"ts": e.get("ts", ""), "type": e.get("type", "event"),
               "summary": str(e.get("summary", ""))[:50]}
              for e in (state.get("events") or [])[-4:]]
    return {
        "scene_num": state.get("scene_num", 0),
        "location": s.get("location", "") or "",
        "objective": s.get("objective", "") or "",
        "relations": rel,
        "flags": [str(f)[:40] for f in (s.get("flags") or [])[-4:]],
        "facts_count": len([f for f in state.get("facts", []) if f.get("status") == "active"]),
        "last_action": (state.get("last_action") or {}).get("summary", "")[:60],
        "events": events,
    }


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
        # v3.5.47: 主流程 LLM 活跃标志——后台任务让路（防并发限流变慢）
        try:
            from .story_director import set_main_flow
        except Exception:
            set_main_flow = None
        if set_main_flow:
            set_main_flow(True)
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
        finally:
            if set_main_flow:
                set_main_flow(False)

    # ── 行动识别 ──
    def detect_action(self, user_input: str, state: dict) -> Optional[dict]:
        """识别玩家输入是否行动。返回行动意图 dict 或 None（普通对话/判定失败）。

        返回: {type, summary, state_updates, end_chat, reason, forced, blocked?}
        - blocked=True：超现实/凭空获得——规则层拦截，结果场景按"尝试无果"生成
        """
        # v3.5.23: 护栏改 LLM 判断（世界观自适应）——硬编码词表只作【兜底】，
        # AI 可用时绝不直接拦截：修真世界观里"御剑飞行/瞬移"可能完全合理，
        # 一刀切会让小说死板。正则命中仅作为 prompt 提示让 LLM 结合世界观裁决。
        guard_hint = ""
        if UNREALISTIC_RE.search(user_input) or UNREAL_GAIN_RE.search(user_input):
            guard_hint = ("⚠ 预筛提示：输入含疑似超现实/凭空获得词汇"
                          "（瞬移/飞行/凭空造物/巨额财富等），请结合世界观判断是否越界")
        pre = rule_prescreen(user_input)
        if not pre["candidate"]:
            return None
        s = state.get("state", {})
        agenda = state.get("agenda") or {}
        hooks = agenda.get("hooks", []) or []
        hook_lines = "\n".join(
            f"- hook[{i}] trigger: {h.get('trigger', '')} → outcome: {h.get('outcome', '')}"
            for i, h in enumerate(hooks)
        ) or "（无）"
        # v3.5.46: 在场角色用推导名单（不在场角色不得在行动中现身/反应）
        try:
            from .story_director import compute_present
            chars, _away = compute_present(state)
        except Exception:
            chars = list(state.get("casts", {}).keys())
        # v3.4.1：注入世界观边界 + 关键角色保护
        wb = str(state.get("worldbuilding_brief", ""))[:400]
        # v3.5.37: 主角状态卡注入（精确位置/时间/同行/处境）
        _ps = state.get("player_state") or {}
        _ps_line = (f"主角状态卡: 位置[{_ps.get('location', '')}] 时间[{_ps.get('time', '')}] "
                    f"同行[{','.join(_ps.get('with') or []) or '无'}] "
                    f"身体[{_ps.get('condition', '健康')}] 身份[{_ps.get('disguise', '本名') or '本名'}] "
                    f"处境[{_ps.get('situation', '')}]\n") if _ps else ""
        user = (
            f"当前地点: {clean_location(s.get('location', '')) or '（未定）'}\n"
            + _ps_line
            + f"主线目标: {s.get('objective', '') or '（未定）'}\n"
            f"世界观边界: {wb or '（无，按现实世界逻辑）'}\n"
            f"在场角色: {', '.join(chars) or '（无）'}\n"
            f"对话议程推进开关:\n{hook_lines}\n"
            f"预筛: {pre['hint']}\n"
            + (guard_hint + "\n" if guard_hint else "")
            + f"读者输入: {user_input[:200]}\n"
            f"请识别这是否是剧情行动。"
        )
        raw = self._llm(ACTION_DETECT_SYSTEM, user, temperature=0.2, max_tokens=400)
        result = _parse_json(raw) if raw else None
        if not isinstance(result, dict):
            # LLM 不可用/解析失败 → 规则兜底（护栏优先，防状态污染）
            if guard_hint:
                return {
                    "type": "unrealistic", "summary": f"读者试图：{user_input[:40]}",
                    "state_updates": {}, "end_chat": False,
                    "reason": "超现实/凭空获得（LLM 不可用，规则兜底）",
                    "forced": True, "blocked": True,
                }
            if pre.get("forced"):
                return {
                    "type": "other", "summary": user_input[:60],
                    "state_updates": {}, "end_chat": False,
                    "reason": "LLM 不可用，规则兜底", "forced": True,
                }
            return None
        is_action = bool(result.get("is_action"))
        blocked = bool(result.get("blocked", False))
        # v3.5.23: LLM 结论为准——规则不再否决 LLM（forced 只用于 LLM 失败时兜底）
        if not is_action:
            return None
        action = {
            "type": str(result.get("type", "other"))[:20] or "other",
            "summary": str(result.get("summary", user_input[:60]))[:120],
            "state_updates": result.get("state_updates") or {},
            "end_chat": bool(result.get("end_chat", False)),
            "reason": str(result.get("reason", ""))[:100],
            "forced": pre["forced"],
            "blocked": blocked,
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
        """把行动结果写回 state（location/flags/inventory/relations）

        v3.4.1 护栏：状态变更 sanity check——location 只随移动类行动改、
        物品只随物品类行动改、flags 限量、relations clamp。
        """
        if action.get("blocked"):
            # 超现实/凭空获得：不更新剧情状态，只记录尝试痕迹（结果场景按"无果"生成）
            state = self.store.load_state(novel_id) or {}
            if state:
                state["last_action"] = {
                    "type": action.get("type", "unrealistic"),
                    "summary": action.get("summary", ""),
                    "blocked": True,
                    "ts": time.strftime("%H:%M:%S"),
                }
                self.store.save_state(novel_id, state)
            return {"changed": [], "state": state}
        state = self.store.load_state(novel_id)
        if state is None:
            return {}
        su = dict(action.get("state_updates") or {})
        a_type = action.get("type", "other")
        s = state.setdefault("state", {})
        changed = []
        # 护栏 1：location 只允许 move/leave/interact 类行动修改
        if su.get("location"):
            if a_type in ("move", "leave", "interact", "combat"):
                old = s.get("location", "")
                if old != su["location"]:
                    s["location"] = su["location"]
                    changed.append(f"地点: {old or '?'} → {su['location']}")
            else:
                su["location"] = ""  # 其他类型行动不许改地点
        # 护栏 2：flags 限量（单次 ≤3，总量 ≤20）
        flags = s.setdefault("flags", [])
        for f in su.get("flags", []):
            if f and f not in flags and len(flags) < 20:
                flags.append(f)
                changed.append(f"flag: {f}")
        # 护栏 3：物品只随 use/interact/accept/combat 类行动变化
        if a_type in ("use", "interact", "accept", "combat", "other"):
            inv = s.setdefault("inventory", [])
            for item in su.get("inventory", []):
                if item and item not in inv and len(inv) < 30:
                    inv.append(item)
                    changed.append(f"物品: {item}")
        # 护栏 4：relations 数值 clamp 0-100（字符串描述保留）
        rel_map = s.setdefault("relations", {})
        for k, v in (su.get("relations") or {}).items():
            if k and k != "player":
                if isinstance(v, (int, float)):
                    rel_map[k] = max(0, min(100, int(v)))
                else:
                    rel_map[k] = str(v)[:60]
                changed.append(f"关系[{k}]: {v}")
        # v3.5.37: 行动同步到主角状态卡（location/物品/处境更新）
        ps = state.get("player_state") or {}
        if action.get("type") in ("move", "leave", "interact") and su.get("location"):
            ps["location"] = str(su["location"])[:80]
        if action.get("type") in ("use", "interact") and su.get("inventory"):
            ps["holding"] = [str(x)[:30] for x in su["inventory"]][:5]
        if action.get("type") in ("move", "leave"):
            ps["situation"] = f"刚执行行动：{str(action.get('summary', ''))[:60]}"
        if ps:
            state["player_state"] = ps
        # 记录最近一次行动（PACT/场景生成可感知）
        state["last_action"] = {
            "type": action.get("type", "other"),
            "summary": action.get("summary", ""),
            "blocked": bool(action.get("blocked")),
            "ts": time.strftime("%H:%M:%S"),
        }
        # v3.5.9: 行动沉淀为角色记忆 + 事件时间线（行动真实留在角色记忆里）
        if not action.get("blocked"):
            from .char_memory import add_event, add_memory
            summary = action.get("summary", "")[:60]
            add_event(state, summary or f"{action.get('type', '行动')}", "action")
            # 关系变化 → 对应角色记忆
            for k, v in (su.get("relations") or {}).items():
                if k and k != "player" and k in (state.get("casts") or {}):
                    add_memory(state, k, "attitude",
                               f"读者对你做了行动：{summary or '…'}（你对他的态度因此变化）",
                               source="action")
            # location 变化 → 全体在场角色记忆
            if changed and any(c.startswith("地点") for c in changed):
                loc = s.get("location", "")
                for name in (state.get("casts") or {}):
                    add_memory(state, name, "event",
                               f"读者去了{loc or '新的地方'}",
                               source="action")
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
        # v3.5.46: 在场角色用推导名单（不在场角色不得反应/出现）
        try:
            from .story_director import compute_present
            chars, _away = compute_present(state)
        except Exception:
            chars = list(state.get("casts", {}).keys())
        char_briefs = []
        for name in chars[:3]:
            prof = (state.get("casts", {}).get(name) or {}).get("profile", {})
            # v3.5.51: 行动结果场景人设全维度（对齐场景生成——行为准则/风格/绝不）
            _segs = []
            heur = prof.get("decision_heuristics", [])[:1]
            for h in heur:
                if isinstance(h, dict):
                    _tr = str(h.get("trigger", ""))[:20]
                    if _tr.startswith("当"):
                        _tr = _tr[1:]
                    _segs.append(f"当{_tr}→{str(h.get('action', ''))[:30]}")
                else:
                    _segs.append(str(h)[:40])
            dna = prof.get("expression_dna", [])[:1]
            for d in dna:
                _segs.append(f"风格[{str(d.get('name', d) if isinstance(d, dict) else d)[:20]}]")
            anti = prof.get("anti_patterns", [])[:2]
            for a in anti:
                _segs.append(f"绝不[{a.get('pattern', a) if isinstance(a, dict) else a}"[:36] + "]")
            brief = "；".join(_segs) if _segs else "（人设未蒸馏）"
            char_briefs.append(f"- {name}: {brief}")
        user = (
            f"小说: 《{state.get('title', '')}》 {state.get('genre', '')}·{state.get('style', '')}\n"
            f"当前地点: {clean_location(s.get('location', '')) or '（未定）'}\n"
            f"你的行动（主角 {((state.get('player_char') or {}).get('name', '你'))} 刚刚做的）: {action.get('summary', '')}\n"
            f"行动类型: {action.get('type', 'other')}\n"
            f"状态变化: {'；'.join(changed) or '（无）'}\n"
            f"在场角色:\n{chr(10).join(char_briefs) or '（无）'}\n"
            f"生成这段行动的结果场景（1-3 句）。"
        )
        collected = []
        # v3.5.19: 阶段提示（行动结果生成中）
        yield {"type": "phase", "label": "⚡ 正在生成行动结果…"}
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
        # v3.5.27: 行动结果里的玩家自动台词 → 转旁白心声
        pname = (state.get("player_char") or {}).get("name", "")
        if pname:
            from .story_director import parse_scene_markup, _clean_player_dialogue
            cleaned = _clean_player_dialogue(parse_scene_markup(text), pname)
            if cleaned:
                text = "\n".join(
                    f"【{b['speaker']}】{b['content']}" if b.get("speaker")
                    else f"【旁白】{b['content']}" for b in cleaned)
        # v3.5.8: 状态变化实时推送（前端对话流内直接展示 + 状态卡实时更新，免滚动）
        if changed:
            yield {"type": "state_change", "changes": changed,
                   "snapshot": _state_snapshot(state)}
        # 落盘（进 chat_logs 保持时序，标记 action_result）
        self.store.append_chat(novel_id, {
            "role": "assistant", "speaker": "旁白", "type": "action_result",
            "content": text, "action": action.get("type", "other"),
            "ts": time.strftime("%H:%M:%S"),
        })
        # v3.5.42: 行动结果同步进 recent_blocks（切回时进度恢复完整）
        try:
            rb = state.get("recent_blocks") or []
            rb.append({"type": "narration", "speaker": "", "content": text})
            state["recent_blocks"] = rb[-260:]
            self.store.save_state(novel_id, state)
        except Exception:
            pass
        yield {"type": "action_end", "content": text, "action": action,
               "snapshot": _state_snapshot(state)}
