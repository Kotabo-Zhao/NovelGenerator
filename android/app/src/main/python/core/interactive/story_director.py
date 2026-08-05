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
import re
import time
import uuid
from typing import AsyncIterator, Optional

from ..resilient_client import ResilientLLMClient
from .action_engine import _state_snapshot, clean_location

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# v1.1 锚点式剧情控制 — P0 纯规则函数（零 LLM，可离线测试）
# ═══════════════════════════════════════════════════════════════

def tension_update(tension, mode="neutral"):
    """张力更新纯函数（0-10）。

    mode: drift=偏离主线(+2) / neutral=中性(+1) / progress=推进主线(-1)
    任何未知 mode 按 neutral 处理。clamp 到 [0, 10]。
    """
    try:
        cur = max(0, min(10, int(tension)))
    except (TypeError, ValueError):
        cur = 0
    if mode == "drift":
        return min(10, cur + 2)
    if mode == "progress":
        return max(0, cur - 1)
    return min(10, cur + 1)  # neutral / 未知


def chapter_complete(state: dict, min_scenes: int = 2) -> bool:
    """切章判定纯函数（v1.1 锚点式）。

    优先：本章 beats 全部 done 且场景数 ≥ 2（消除 5 节点 vs 3 场景矛盾）
    兜底：无 beats（旧存档/残缺）→ 场景数 ≥ min_scenes
    任何异常 → False（不切章，不抛异常）
    """
    try:
        op = state.get("outline_progress") or {}
        cnt = max(int(op.get("scene_in_chapter", 0)), 0)
        cb = state.get("chapter_beats") or {}
        beats = cb.get("beats") or []
        # beats 与当前章节匹配才生效（防切章后残留旧 beats 误判）
        if beats and int(cb.get("chapter_idx", -1)) == int(op.get("idx", 0)):
            if cnt < 2:
                return False  # 最小场景数保护，防异常快速切章
            return all(str(b.get("status", "")) == "done" for b in beats)
        return cnt >= max(1, int(min_scenes))
    except Exception:
        return False


def mainline_check(state: dict) -> dict:
    """L0 主线健康度对账纯函数（v1.1 保险④）。

    输入 state.mainline: {required_flags: [...], acquired: [...], expected_by_chapter: N}
    输出 {shortcut: bool, gap: int}——进度落后 → shortcut=True（P4 注入捷径）
    无配置/异常 → {shortcut: False, gap: 0}
    """
    try:
        ml = state.get("mainline") or {}
        required = ml.get("required_flags") or []
        if not required:
            return {"shortcut": False, "gap": 0}
        acquired = set(ml.get("acquired") or [])
        # expected 优先读 mainline，其次 outline_progress（随章节推进变化）
        expected = int(ml.get("expected_by_chapter", 0) or 0)
        if expected <= 0:
            expected = int((state.get("outline_progress") or {}).get("expected_by_chapter", 0) or 0)
        if expected <= 0:
            expected = len(required)
        gap = max(0, expected - len([f for f in required[:expected] if f in acquired]))
        return {"shortcut": gap > 0, "gap": gap}
    except Exception:
        return {"shortcut": False, "gap": 0}


def _cond_match(field, op, value, state: dict, s: dict, tension: int, target=None) -> bool:
    """锚点触发条件单项匹配（纯规则，零 LLM）。任何异常 → False。"""
    try:
        if field == "tension":
            v = tension
            if op == ">=":
                return v >= int(value)
            if op == "<=":
                return v <= int(value)
            return v == int(value)
        if field == "flag":
            flags = [str(f) for f in (s.get("flags") or [])]
            if op == "has":
                return str(value) in flags
            if op == "not_has":
                return str(value) not in flags
            return False
        if field == "location":
            loc = clean_location(s.get("location") or "")
            if op == "==":
                return loc == str(value)
            if op == "!=":
                return loc != str(value)
            return False
        if field == "relations":
            rel = s.get("relations") or {}
            v = rel.get(str(target or value))
            if isinstance(v, (int, float)):
                if op == ">=":
                    return float(v) >= float(value)
                if op == "<=":
                    return float(v) <= float(value)
                return float(v) == float(value)
            return False
        if field == "inventory":
            inv = [str(i) for i in (s.get("inventory") or [])]
            if op == "has":
                return str(value) in inv
            if op == "not_has":
                return str(value) not in inv
            return False
        return False
    except Exception:
        return False


# ── v3.6 P5: 行动 ↔ 章节 beat 联动（玩家行动直接推进主线节点，规则零 LLM）──
_BEAT_STOP_CHARS = set("的了在要去来回我们你们他们你我这那是和与及就都也很把被从往向到于为以之其这那").union(
    set("，。！？、；：""''（）《》…—"))


def _beat_key_chars(text: str) -> set:
    return {c for c in str(text or "") if c not in _BEAT_STOP_CHARS and '\u4e00' <= c <= '\u9fff'}


def beat_action_match(state: dict, summary: str, target: str = "") -> Optional[dict]:
    """行动是否推进当前 beat（规则匹配，零 LLM）。

    判据（命中任一）：
    1. travel 目标地点名出现在 beat 文本（"去码头" ↔ beat"码头接头"）
    2. 行动摘要与 beat 文本有 >=2 个公共非停用字（"答应" ↔ "答应林晚晚护送"）
    返回 {"beat_id", "desc"} 或 None。
    """
    cb = state.get("chapter_beats") or {}
    beats = cb.get("beats") or []
    cur = next((b for b in beats if b.get("status") == "current"), None)
    if not cur:
        return None
    text = f"{cur.get('desc', '')} {cur.get('entry_hook', '')}"
    if target and str(target).strip() and str(target) in text:
        return {"beat_id": cur.get("id"), "desc": str(cur.get("desc", ""))[:60]}
    common = _beat_key_chars(summary) & _beat_key_chars(text)
    if len(common) >= 2:
        return {"beat_id": cur.get("id"), "desc": str(cur.get("desc", ""))[:60]}
    return None


def beat_advance_by_action(state: dict, action: dict) -> List[str]:
    """行动后调用：命中当前 beat → 推进（含 state_output 应用）；未命中 → 偏离计数。

    偏离计数 >= DRIFT_LIMIT → 标记 beat_drift（场景生成注入主线牵引）。
    返回变化描述列表。
    """
    changes: List[str] = []
    try:
        summary = str(action.get("summary", ""))
        target = str(action.get("target", ""))
        m = beat_action_match(state, summary, target)
        if not m:
            state["drift_count"] = int(state.get("drift_count", 0) or 0) + 1
            if int(state["drift_count"]) >= 3:
                state["beat_drift"] = True
            return changes
        # 命中 → 推进当前 beat
        cb = state.get("chapter_beats") or {}
        beats = cb.get("beats") or []
        cur_i = next((i for i, b in enumerate(beats) if b.get("status") == "current"), None)
        if cur_i is None:
            return changes
        beats[cur_i]["status"] = "done"
        # state_output 应用（与锚点推进一致）
        _so = (beats[cur_i].get("trigger") or {}).get("state_output") or {}
        _s = state.setdefault("state", {})
        for _f in (_so.get("flags") or []):
            _fl = _s.setdefault("flags", [])
            if str(_f) not in _fl and len(_fl) < 20:
                _fl.append(str(_f))
                append_change(state, {"field": "flag", "new": str(_f)}, "action:beat")
        for _k, _v in (_so.get("relations") or {}).items():
            _rel = _s.setdefault("relations", {})
            _rel[str(_k)[:30]] = max(0, min(100, int(_v))) if isinstance(_v, (int, float)) else str(_v)[:60]
            append_change(state, {"field": f"relation.{_k}", "new": str(_v)[:60]}, "action:beat")
        if cur_i + 1 < len(beats):
            beats[cur_i + 1]["status"] = "current"
        try:
            from .char_memory import add_event
            add_event(state, f"章节节点完成（行动推进）: {str(beats[cur_i].get('desc', ''))[:50]}", "beat")
        except Exception:
            pass
        # 偏离清零 + 推进提示
        state["drift_count"] = 0
        state.pop("beat_drift", None)
        changes.append(f"章节推进: {str(beats[cur_i].get('desc', ''))[:40]}")
    except Exception as e:
        log.warning(f"beat_advance_by_action failed: {e}")
    return changes


def beat_drift_hint(state: dict) -> str:
    """偏离收束提示（注入场景 prompt：玩家连续偏离主线时的自然拉回）。"""
    if state.get("beat_drift"):
        cb = state.get("chapter_beats") or {}
        beats = cb.get("beats") or []
        cur = next((b for b in beats if b.get("status") == "current"), None)
        obj = str(state.get("state", {}).get("objective", ""))[:120]
        lines = ["⚠ 你已连续几轮游离于主线之外。本场景需自然牵引："]
        if cur and cur.get("desc"):
            lines.append(f"  本章节点: {str(cur.get('desc', ''))[:60]}")
        if obj:
            lines.append(f"  主线目标: {obj}")
        lines.append("  （让事件/角色主动找上门，或玩家偶然发现线索——不要硬掰，保持自然）")
        return "\n".join(lines)
    return ""


def anchor_trigger_check(state: dict) -> Optional[dict]:
    """锚点条件检查纯函数（v1.1 P2，零 LLM）。

    只检查 status=current 的 beat：
    - trigger.conditions 全部满足 → 触发（reason=condition）
    - trigger.timeout_scenes 超限 → 强制触发（reason=timeout，防死锁）
    返回 {beat_id, reason, hook} 或 None。任何异常 → None（不炸）。
    """
    try:
        cb = state.get("chapter_beats") or {}
        beats = cb.get("beats") or []
        op = state.get("outline_progress") or {}
        idx = int(op.get("idx", 0))
        if int(cb.get("chapter_idx", -1)) != idx:
            return None  # beats 与当前章节不符
        cur = next((b for b in beats if b.get("status") == "current"), None)
        if not cur:
            return None
        trigger = cur.get("trigger") or {}
        if not trigger:
            # 旧数据无 trigger（v1.1 前的 beats）→ legacy 无条件触发，
            # 保持"每场景推进 1 个"的旧行为（不卡死）
            return {"beat_id": int(cur.get("id", 0)), "reason": "legacy",
                    "hook": str(cur.get("entry_hook", ""))[:120]}
        s = state.get("state") or {}
        tension = int(state.get("tension", 0) or 0)
        # 1) 条件判定（conditions 空 = 无条件即触发）
        conds = trigger.get("conditions") or []
        matched = True
        for c in conds:
            if not isinstance(c, dict):
                matched = False
                break
            if not _cond_match(c.get("field"), c.get("op"), c.get("value"),
                               state, s, tension, c.get("target")):
                matched = False
                break
        if matched:
            return {"beat_id": int(cur.get("id", 0)),
                    "reason": "condition",
                    "hook": str(cur.get("entry_hook", ""))[:120]}
        # 2) timeout 兜底（相对本章场景起点）
        timeout = int(trigger.get("timeout_scenes", 0) or 0)
        if timeout > 0:
            sn = int(state.get("scene_num") or 0)
            start = int(op.get("scene_start") or 1)
            if sn - start >= timeout:
                return {"beat_id": int(cur.get("id", 0)),
                        "reason": "timeout",
                        "hook": str(cur.get("entry_hook", ""))[:120]}
        return None
    except Exception:
        return None


def location_valid(current: str, target: str, known_locations: list) -> bool:
    """P3 地点可达性（L2 领域约束，零 LLM）。

    - 同地点/空值 → False（无变化/非法）
    - 已知地点（去过）→ 可达；新地点 → 允许探索（调用方累积进 known_locations）
    """
    try:
        cur = clean_location(current)
        tgt = clean_location(target)
        if not cur or not tgt or cur == tgt:
            return False
        return True
    except Exception:
        return False


_TIME_ORDER = ["清晨", "上午", "正午", "下午", "傍晚", "夜晚", "深夜"]


def time_valid(new_time: str, old_time: str) -> bool:
    """P3 时间单调校验（L2 领域约束，零 LLM）。

    新旧时间都在档位表 → 新必须 >= 旧（防时间倒流）；
    未知文本（如"午夜子时"）/空值 → 放行（LLM 自由表达不拦）。
    """
    try:
        nt = str(new_time or "").strip()
        ot = str(old_time or "").strip()
        if not nt or not ot:
            return True
        if nt not in _TIME_ORDER or ot not in _TIME_ORDER:
            return True  # 自由文本不拦
        return _TIME_ORDER.index(nt) >= _TIME_ORDER.index(ot)
    except Exception:
        return True


def consistency_repair(state: dict) -> dict:
    """P3 状态一致性规则修复（L3，零 LLM）——切章时调用。

    修复项：
    1. state.state.location 与 player_state.location 不一致 → 对齐 player_state（玩家卡更准）
    2. flags 去重
    3. 在场角色（present=True）location 与玩家不一致 → 对齐
    4. tension clamp 0-10
    返回修复后的 state（原地修改 + 返回）。任何异常 → 原样返回。
    """
    try:
        s = state.setdefault("state", {})
        ps = state.get("player_state") or {}
        # 1) location 对齐（player_state 为准）
        _ps_loc = clean_location(ps.get("location") or "")
        if _ps_loc:
            _s_loc = clean_location(s.get("location") or "")
            if _s_loc and _s_loc != _ps_loc:
                s["location"] = _ps_loc
        # 2) flags 去重
        _fl = s.get("flags")
        if isinstance(_fl, list):
            s["flags"] = list(dict.fromkeys(str(f) for f in _fl))
        # 3) 在场角色位置对齐
        cs = state.get("cast_states")
        if isinstance(cs, dict) and _ps_loc:
            for _n, _c in cs.items():
                if isinstance(_c, dict) and _c.get("present") is True:
                    _cl = clean_location(_c.get("location") or "")
                    if _cl and _cl != _ps_loc:
                        _c["location"] = _ps_loc
        # 4) tension clamp
        try:
            state["tension"] = max(0, min(10, int(state.get("tension", 0) or 0)))
        except (TypeError, ValueError):
            state["tension"] = 0
        return state
    except Exception:
        return state


def append_change(state: dict, change: dict, reason: str) -> None:
    """P3 L1 统一写入审计——所有状态变更追加日志（带原因，上限 50 条）。

    不拦截写入（写拦截由各领域约束负责），只保证可追溯。
    """
    try:
        if not isinstance(change, dict):
            return
        log_ = state.setdefault("change_log", [])
        log_.append({"ts": time.strftime("%H:%M:%S"), "reason": str(reason)[:40],
                     "change": {str(k)[:24]: str(v)[:60] for k, v in change.items()}})
        if len(log_) > 50:
            state["change_log"] = log_[-50:]
    except Exception:
        pass


def mainline_pressure(state: dict) -> Optional[str]:
    """P4 跨章张力介入（保险③）：连续 ≥2 章高位偏离 → 主线势力施压文案。

    返回介入文案（注入场景 prompt）或 None。
    """
    try:
        dc = int(state.get("tension_drift_chapters", 0) or 0)
        if dc >= 2:
            return ("世界施压（主线势力正在逼近）：与核心冲突相关的人物/组织已经"
                    "注意到你，正在寻找你——他们在推动剧情走向主线。")
        return None
    except Exception:
        return None


def side_event_hint(state: dict) -> Optional[str]:
    """P4 填充事件（节奏调节）：张力 ≥6 且本场景未触发锚点 → 节奏事件动因。

    返回填充事件提示（注入场景 prompt）或 None。张力高位不触发锚点时，
    用填充事件维持节奏（偶遇/冲突/消息），避免玩家在自由区失去方向感。
    """
    try:
        t = int(state.get("tension", 0) or 0)
        if t >= 6 and not state.get("anchor_triggered"):
            return ("剧情张力很高：周围的世界正在产生新的事件（偶遇/冲突/消息），"
                    "与主线相关或关键人物登场——让事件自然发生。")
        return None
    except Exception:
        return None


def scene_repeat_check(new_text: str, recent_texts: list, threshold: float = 0.13) -> bool:
    """防重复生成规则兜底（纯函数，零 LLM）：语义级重复检测。

    LLM 的重演是"改写式重述"（字符相似度仅 ~0.15，与不同事件几乎无异），
    因此用【内容词 bigram 重叠比例】为主信号（同人物/物品/动作 → 重叠高），
    字符相似度（前 200 字）≥ 0.4 为辅助信号。任一命中 → 判重复。
    空输入/异常 → False（不误杀、不炸）。
    """
    try:
        if not new_text or not recent_texts:
            return False
        grams_new = _content_bigrams(str(new_text))
        if not grams_new:
            return False
        from difflib import SequenceMatcher
        for old in recent_texts:
            old_t = str(old or "")
            grams_old = _content_bigrams(old_t)
            if not grams_old:
                continue
            ov = len(grams_new & grams_old) / min(len(grams_new), len(grams_old))
            if ov >= float(threshold):
                return True
            if SequenceMatcher(None, str(new_text)[:200], old_t[:200]).ratio() >= 0.4:
                return True
        return False
    except Exception:
        return False


_CONTENT_STOP = set(
    "的了着在你是我他她它们和与把被对从向给让就都也很又再才只等这那一个不没有其之而于吧呢啊么"
    "已已经将正在着过要会能可但却并或及若如虽然因为所以然后最后终于突然忽然仿佛好像似乎"
)


def _content_bigrams(text: str) -> set:
    """提取文本的内容 bigram（去停用字后相邻 2 字对）——同人物/物品/动作的信号。"""
    t2 = "".join(c for c in str(text)
                 if c not in _CONTENT_STOP and "\u4e00" <= c <= "\u9fff")
    return set(t2[i:i + 2] for i in range(len(t2) - 1))


# ═══════════════ v2.5.57: 承诺台账 + 时间锚定（周五变周三 bug 修复） ═══════════════

_TIME_SEG = ("晚上|下午|上午|中午|清晨|傍晚|深夜|夜里|凌晨|早上|午间|午后|夜里|夜间")
_STRONG_ANCHOR_RE = re.compile(
    r"(?:(?:下|本|这|上|大上|大下)?周[一二三四五六日天]|星期[一二三四五六日天]|礼拜[一二三四五六日天])"
    r"(?:" + _TIME_SEG + r")?"
    r"|(?:[一二三四五六七八九十0-9]+(?:天|日)后)"
    r"|(?:大后天|后天)"
)
_WEEKDAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}


def promise_anchor_of(text: str):
    """提取文本中的强时间锚（周几/具体日期类），返回原始表述或 None。

    强锚 = 周X/星期X/礼拜X（可带时段词）/ N日后 / 后天·大后天。
    弱锚（明天/今晚/改天）不提取——可变性强，不进入台账校验，避免误杀。
    """
    try:
        m = _STRONG_ANCHOR_RE.search(str(text or ""))
        if not m:
            return None
        return m.group(0)
    except Exception:
        return None


def _weekday_of(expr: str):
    """周几归一化：'周五'/'星期五'/'礼拜五' → 5；无法归一化 → None。"""
    try:
        m = re.search(r"[一二三四五六日天]", str(expr or ""))
        if m:
            return _WEEKDAY_MAP.get(m.group(0))
    except Exception:
        pass
    return None


def promise_ledger_update(state: dict, new_promises: list, action_summary: str = "") -> dict:
    """承诺台账写入/兑现/违约（纯函数，零 LLM）。

    - 时间锚定承诺（type=promise 且 time_anchor 非空）→ 写入 pending_promises
    - 同对象同时间同事件（去锚后内容相似）→ 防重复不写入
    - action_summary 含赴约词 → 全部 pending 兑现（fulfilled）+ 写事件时间线
    - action_summary 含拒绝词 → 全部 broken（违约）
    返回 {"added": n, "fulfilled": m, "broken": k}
    """
    try:
        ledger = state.setdefault("pending_promises", [])
        if not isinstance(ledger, list):
            ledger = state["pending_promises"] = []
        added = fulfilled = broken = 0
        # 兑现/违约判定（优先违约——"拒绝赴约"算拒绝）
        as_ = str(action_summary or "")
        if any(w in as_ for w in ("拒绝", "不去", "取消", "放鸽子", "失约", "爽约", "不去了")):
            for p in ledger:
                if p.get("status") == "pending":
                    p["status"] = "broken"
                    broken += 1
            return {"added": 0, "fulfilled": 0, "broken": broken}
        if any(w in as_ for w in ("赴约", "应约", "如约", "赴宴", "赴会", "赴饭局")):
            for p in ledger:
                if p.get("status") == "pending":
                    p["status"] = "fulfilled"
                    fulfilled += 1
                    from .char_memory import add_event
                    add_event(state, f"约定兑现: 与{p.get('who', '?')}{p.get('what', '')}"
                                     f"（{p.get('when_raw', '')}）", "promise")
            return {"added": 0, "fulfilled": fulfilled, "broken": 0}
        # 写入：过滤时间锚定承诺
        for f in (new_promises or []):
            try:
                if not isinstance(f, dict) or f.get("type") != "promise":
                    continue
                anchor = str(f.get("time_anchor", "") or "").strip()
                if not anchor:
                    continue
                # who 判定：读者承诺 target=角色；角色邀约 target=player → who=subject
                target = str(f.get("target", "") or "").strip()
                subject = str(f.get("subject", "") or "").strip()
                who = target if target and target != "player" else subject
                content = str(f.get("content", "") or "").strip()
                if not who or not content:
                    continue
                # 防重复：同 who 同 when_raw 且去锚后内容相似
                dup = False
                base = content.replace(anchor, "")
                for p in ledger:
                    if p.get("status") != "pending" or p.get("who") != who:
                        continue
                    if p.get("when_raw") != anchor:
                        continue
                    from difflib import SequenceMatcher
                    if SequenceMatcher(None, base, str(p.get("what", "")).replace(anchor, "")).ratio() >= 0.3:
                        dup = True
                        break
                if dup:
                    continue
                # v3.6 P3: 地点锚定——从约定内容规则提取地点（图谱匹配，零 LLM）
                _loc = ""
                try:
                    from .world_state import extract_location_from_text
                    _loc = extract_location_from_text(content, state)
                except Exception:
                    _loc = ""
                ledger.append({
                    "who": who[:30],
                    "what": content[:60],
                    "when_raw": anchor[:20],
                    "location": _loc[:30],   # v3.6 P3: 约定地点（玩家到达时自动兑现）
                    "scene_num": int(state.get("scene_num", 0) or 0),
                    # v2.5.59: 推进时钟——约定后 3 个场景内必须推进兑现（防剧情打转）
                    "due_scene": int(state.get("scene_num", 0) or 0) + 3,
                    "status": "pending",
                })
                added += 1
            except Exception:
                continue
        return {"added": added, "fulfilled": fulfilled, "broken": broken}
    except Exception:
        return {"added": 0, "fulfilled": 0, "broken": 0}


def promise_conflict_check(text: str, state: dict):
    """新场景文本 vs 未兑现约定台账的时间冲突检测（纯函数）。

    提取文本中所有强时间锚，与 pending 约定的 when_raw 做周几归一化比对：
    周几不同 → 返回冲突描述（含约定原始时间）；无约定/无周几可比/异常 → None。
    """
    try:
        if not text or not state:
            return None
        ledger = state.get("pending_promises") or []
        if not isinstance(ledger, list) or not ledger:
            return None
        pending = [p for p in ledger if isinstance(p, dict) and p.get("status") == "pending"]
        if not pending:
            return None
        anchors = re.findall(_STRONG_ANCHOR_RE, str(text))
        if not anchors:
            return None
        for a in anchors:
            wd = _weekday_of(a)
            if wd is None:
                continue  # N日后/后天类无法周几比对，保守放过
            for p in pending:
                pwd = _weekday_of(str(p.get("when_raw", "") or ""))
                if pwd is not None and pwd != wd:
                    return (f"时间冲突: 本场景出现'{a}'，但未兑现约定"
                            f"'{p.get('when_raw', '?')}'与{p.get('who', '?')}约定{p.get('what', '')}"
                            f"——约定时间已锚定不得改写")
        return None
    except Exception:
        return None


# v1.1 P4: 动态大纲微调——玩家自由行为导致锚点不合适时，目标等价替换
ADAPT_OUTLINE_SYSTEM = """你是互动小说大纲微调师。玩家的自由行为可能让原大纲的某个锚点不再合适，
你需要判断并给出替换锚点（目标等价——保持故事结构和最终目标不变，只换达成方式）。

判断原则：
1. 玩家现状（位置/处境/已有关键标记/最近行动）与锚点明显冲突（关键物品已毁/关键角色关系破裂/
   地点已不可达）→ 不合适
2. 轻微偏离 → 合适（不替换，玩家路径自由是设计核心）
3. 替换锚点必须目标等价：同样的叙事功能（冲突升级/信息揭露/转折收束），
   但达成方式贴合玩家现状（信物毁了 → 发现副本/敌人手中的另一条线索）

输出 JSON:
{"appropriate": true/false, "replacement": [5个锚点数组（同 scene_beats 结构：beat/name/function/key_action/
trigger/reject_outcome/state_output/entry_hook）] 或 null, "reason": "一句话依据"}
只输出 JSON。"""


def state_context_brief(state: dict, max_chars: int = 320) -> str:
    """v2.5.58: 当前世界状态简报——玩家状态 + 角色情绪/态度 + 关系 + 最近事件。

    供行动结果场景/场景生成注入 prompt，让 LLM 生成行为后果时
    真正考虑玩家处境与角色的状态和性格（mood/stance/agenda）。
    空状态/异常 → 空串（不炸）。
    """
    try:
        if not state:
            return ""
        segs = []
        # v3.6: 三支柱确定性快照（时间/地点/在场——规则驱动，不依赖 LLM 记忆）
        try:
            from .world_state import world_brief
            _wb = world_brief(state, max_chars=200)
            if _wb:
                segs.append(_wb)
        except Exception:
            pass
        # 玩家状态卡（现状/身体/物品/钱财）
        ps = state.get("player_state") or {}
        if ps:
            _c = str(ps.get("condition", "") or "")
            _sit = str(ps.get("situation", "") or "")
            _h = "、".join(ps.get("holding") or []) or ""
            _m = str(ps.get("money", "") or "")
            _parts = [f"位置[{clean_location(ps.get('location'))}]"]
            if _c and _c != "健康":
                _parts.append(f"身体[{_c}]")
            if _m:
                _parts.append(f"钱财[{_m}]")
            if _h:
                _parts.append(f"随身[{_h}]")
            _line = " ".join(_parts)
            if _sit:
                _line += f"；处境: {_sit[:60]}"
            segs.append(f"主角现状: {_line}")
        # 在场角色情绪/态度/议程（cast_states 全维度）
        cs = state.get("cast_states") or {}
        moods = []
        if isinstance(cs, dict):
            for _n, _c in list(cs.items())[:4]:
                if not isinstance(_c, dict) or _c.get("present") is False:
                    continue
                _mood = str(_c.get("mood", "") or "")
                _st = str(_c.get("stance", "") or "")
                _ag = str(_c.get("agenda", "") or "")
                _parts = []
                if _mood:
                    _parts.append(f"情绪[{_mood}]")
                if _st:
                    _parts.append(f"态度[{_st}]")
                if _ag:
                    _parts.append(f"意图[{_ag[:24]}]")
                if _parts:
                    moods.append(f"{_n}{' '.join(_parts)}")
        if moods:
            segs.append("在场角色: " + "；".join(moods[:3]))
        # 关系值（状态矩阵）
        rel = (state.get("state") or {}).get("relations") or {}
        if isinstance(rel, dict) and rel:
            rels = [f"{k}♥{v}" for k, v in list(rel.items())[:4]
                    if isinstance(v, (int, float))]
            if rels:
                segs.append("关系值: " + " ".join(rels))
        # 最近事件（刚发生的事——后果生成要承接）
        evs = [(str(e.get("summary", ""))[:40]) for e in (state.get("events") or [])[-2:]
               if e.get("summary")]
        if evs:
            segs.append("最近: " + "；".join(evs))
        out = "\n".join(segs)
        return out[:max_chars] if len(out) > max_chars else out
    except Exception:
        return ""


def merge_cast_states(old_cs, new_cs) -> dict:
    """v2.5.60: NPC 状态卡合并更新（纯函数）——修复整体覆盖丢失 bug。

    _extract_player_state 的 LLM 只输出"本场景出场角色"的状态，旧实现整体覆盖
    cast_states 导致未出场角色的情绪/立场/议程全部丢失 → 后续场景按初始状态生成
    → 已完成事件重复触发。合并规则：LLM 输出的角色更新，未输出的角色保留旧状态。
    异常 → 返回 {}（不炸，调用方自行兜底）。
    """
    try:
        if not isinstance(old_cs, dict):
            old_cs = {}
        if not isinstance(new_cs, dict):
            return dict(old_cs)
        merged = {str(k): dict(v) for k, v in old_cs.items() if isinstance(v, dict)}
        for name, c in new_cs.items():
            if not isinstance(c, dict):
                continue
            key = str(name)
            prev = merged.get(key, {})
            if not isinstance(prev, dict):
                prev = {}
            # knows 是累积知识（知道了不会忘）——新旧并集；其他字段新值覆盖
            _nc = dict(c)
            if isinstance(_nc.get("knows"), list) and isinstance(prev.get("knows"), list):
                _seen = []
                for _k in list(prev.get("knows", [])) + list(_nc.get("knows", [])):
                    if str(_k) not in _seen and len(_seen) < 6:
                        _seen.append(str(_k))
                _nc["knows"] = _seen
            merged[key] = {**prev, **{str(k): v for k, v in _nc.items()}}
        return merged
    except Exception:
        return {}


# v2.5.60: 状态变化词检测——场景文本出现明确状态转折 → 规则层记录事件（零 LLM）
_STATE_CHANGE_RE = re.compile(
    r"答应|同意|接受|拒绝|决裂|翻脸|背叛|反目|摊牌|和好|言和|结盟|联手|原谅|"
    r"求婚|表白|分手|辞职|离职|被捕|遇害|昏迷|苏醒|失踪|归来|越狱|投降|叛变"
)


def state_change_detect(scene_text) -> Optional[str]:
    """v2.5.60: 检测场景中的明确状态变化（纯函数，零 LLM）。

    命中变化词 → 返回一句话事件描述（供事件时间线记录）；
    未命中/空/异常 → None。即使 LLM 状态提取失败，关键转折也不会丢。
    """
    try:
        t = str(scene_text or "")
        if not t:
            return None
        m = _STATE_CHANGE_RE.search(t)
        if not m:
            return None
        # 取变化词附近的一句话作为事件描述
        w = m.group(0)
        start = max(0, t[:m.start()].rfind("。") + 1)
        end = t.find("。", m.end())
        if end == -1:
            end = t.find("！", m.end())
        if end == -1:
            end = min(len(t), m.end() + 40)
        seg = t[start:end + 1].strip()
        return f"状态变化: {seg[:60]}"
    except Exception:
        return None


def promise_due_check(state: dict) -> dict:
    """v2.5.59: 约定推进时钟（纯函数）——检查未兑现约定的到期/过期状态。

    约定写入时带 due_scene（约定场景 + 3，3 个场景内必须推进兑现）。
    - 未到期：不注入（防 NPC 反复提起同一约定导致剧情打转）
    - 到期（scene_num >= due_scene）：注入推进指令，本场景必须推进兑现
    - 过期（scene_num >= due_scene + 2）：注入追问指令，NPC 追问/关系受损
    返回 {"due": [约定], "overdue": [约定]}；无台账/异常 → 空结构（不炸）。
    """
    try:
        due, overdue = [], []
        ledger = state.get("pending_promises") or []
        if not isinstance(ledger, list):
            return {"due": [], "overdue": []}
        scene_num = int(state.get("scene_num", 0) or 0)
        for p in ledger:
            if not isinstance(p, dict) or p.get("status") != "pending":
                continue
            d = int(p.get("due_scene", 0) or 0)
            if d <= 0:
                continue  # 旧数据无时钟 → 不参与到期判定
            if scene_num >= d + 2:
                overdue.append(p)
            elif scene_num >= d:
                due.append(p)
        return {"due": due, "overdue": overdue}
    except Exception:
        return {"due": [], "overdue": []}


def generate_suggestions(client, model, store, state: dict, chars: list) -> list:
    """v3.5.40/v2.5.59: 生成 3 个建议回应（真两难）。模块级——场景节点停顿 + 对话后刷新共用。

    上下文增强（v2.5.59）：注入世界状态简报（玩家现状/角色情绪/关系）+ 最近事件
    + 未兑现约定到期信息——选项贴合最新剧情进度，不再与现状脱节。
    """
    try:
        from ..resilient_client import ResilientLLMClient
        s = state.get("state", {}) or {}
        ps = state.get("player_state") or {}
        agenda = state.get("agenda") or {}
        _ctx = state_context_brief(state, 220)
        _due_state = promise_due_check(state)
        _due = _due_state.get("due") or []
        _due_line = ""
        if _due:
            _due_line = "待兑现（时间已到，剧情必须推进）: " + "；".join(
                f"{p.get('when_raw', '?')}与{p.get('who', '?')}约定{p.get('what', '')}" for p in _due[:2])
        user = (
            f"剧情目标: {s.get('objective', '')}\n"
            f"当前处境: {ps.get('situation', '') or '（未知）'}\n"
            + (f"当前世界状态: {_ctx}\n" if _ctx else "")
            + (f"{_due_line}\n" if _due_line else "")
            + f"在场角色: {', '.join(chars)}\n"
            f"对话议程: {str(agenda.get('goal', ''))[:100]}\n"
            f"请为读者生成 3 个建议回应（Galgame 风格选项）——"
            f"必须是有分量的真两难（答应/拒绝/追问/试探/沉默/转移话题…），"
            f"每个选项 4-15 字，口语化，符合当前角色的处境、关系与最新剧情进度。"
        )
        _rc = ResilientLLMClient(client, model)
        resp = _rc.create(
            messages=[
                {"role": "system", "content": SUGGESTION_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.7, max_tokens=300,
        )
        raw = getattr(resp, "choices", [{}])[0].message.content if resp else None
        result = _parse_json(raw) if raw else None
        opts = (result or {}).get("options") or []
        if isinstance(opts, list):
            cleaned = [str(o)[:30] for o in opts if str(o).strip()][:3]
            if cleaned:
                return cleaned
    except Exception as e:
        log.warning(f"suggestions gen failed: {e}")
    return []


# ── v2.5.61: 章节回流重构——幂等标记 / 补漏判定 / global_state 同步 ──

def sync_skip_check(state: dict, chapter_num: int, scene_start: int, scene_end: int) -> bool:
    """回流幂等判定（纯函数）：该章是否已回流过覆盖 [scene_start, scene_end] 的场景区间。

    返回 True = 已同步过（跳过回流，不重复生成）；False = 需要回流。
    无记录/异常 → False（宁可多回流一次，不可漏）。
    """
    try:
        ch_num = int(chapter_num)
        if ch_num <= 0 or scene_end < scene_start:
            return False
        recs = ((state or {}).get("synced_chapters") or {}).get(str(ch_num)) or []
        if not recs:
            return False
        for r in recs:
            if int(r.get("start", 0) or 0) <= scene_start and scene_end <= int(r.get("end", 0) or 0):
                return True
        return False
    except Exception:
        return False


def sync_mark_record(state: dict, chapter_num: int, scene_start: int, scene_end: int) -> None:
    """回流完成后记录同步区间（纯函数）：同章区间追加并合并相邻，按 start 排序。"""
    try:
        ch_num = int(chapter_num)
        if ch_num <= 0 or scene_end < scene_start:
            return
        recs = ((state or {}).get("synced_chapters") or {}).get(str(ch_num)) or []
        # 合并：与新区间重叠/相邻的旧区间并入
        new = {"start": int(scene_start), "end": int(scene_end)}
        merged = []
        for r in recs:
            rs, re = int(r.get("start", 0) or 0), int(r.get("end", 0) or 0)
            if rs <= new["end"] + 1 and new["start"] <= re + 1:  # 重叠或相邻
                new["start"] = min(new["start"], rs)
                new["end"] = max(new["end"], re)
            else:
                merged.append(r)
        merged.append(new)
        merged.sort(key=lambda x: int(x.get("start", 0) or 0))
        (state or {}).setdefault("synced_chapters", {})[str(ch_num)] = merged
    except Exception:
        pass


def backfill_list(state: dict, existing: dict, max_sync: int = 2) -> list:
    """补漏判定（纯函数）：互动已完成、但正式章节缺失/未同步的章节号列表。

    判定规则：大纲进度 idx 表示"正在玩第 idx+1 章"——已完成章节是 [1..idx]；
    final_done 时 [1..len] 全部完成。existing 是 {文件名: 内容} 或 {章号: 存在}。
    返回需要补漏的章号列表（升序，最多 max_sync 章，避免一次补太多 LLM 调用）。
    """
    try:
        op = (state or {}).get("outline_progress") or {}
        idx = int(op.get("idx", 0) or 0)
        chs = (state or {}).get("outline_chapters") or []
        if not chs:
            return []
        total = len(chs)
        done_upto = total if op.get("final_done") else min(idx + 1, total)
        if done_upto <= 0:
            return []
        # 章号 → 是否已存在正式章节（兼容 {文件名: 内容} 和 {章号: bool}）
        need = []
        for ch in chs[:done_upto]:
            num = int(ch.get("number", 0) or 0)
            if num <= 0:
                continue
            fname = f"chapter_{num:04d}.md"
            exists = existing.get(fname) or existing.get(str(num)) or existing.get(num)
            synced = sync_skip_check(state, num, 0, 10 ** 9)
            if not exists and not synced:
                need.append(num)
        return need[:max_sync]
    except Exception:
        return []


def gs_merge_sync(gs: dict, chapter_num: int, title: str, body: str, summary: str) -> dict:
    """回流写入合并到 global_state.json（纯函数）：
    chapters_summary / timeline.chapters 初始化 / chapter_titles——不覆盖已有时序与角色数据。

    返回合并后的 gs（gs 为 None 时返回 None，调用方跳过）。
    """
    try:
        if gs is None:
            return None
        ch_num = str(int(chapter_num))
        # chapters_summary（正式小说体系的章节摘要——上下文更新/一致性校验消费）
        if not isinstance(gs.get("chapters_summary"), dict):
            gs["chapters_summary"] = {}
        if summary:
            gs["chapters_summary"][ch_num] = str(summary)[:300]
        # timeline.chapters：只初始化缺失的章（已有 days_elapsed 的保留——时序归正式管线管）
        tl = gs.get("timeline")
        if not isinstance(tl, dict):
            tl = {"total_days": 0, "chapters": {}}
            gs["timeline"] = tl
        if not isinstance(tl.get("chapters"), dict):
            tl["chapters"] = {}
        if ch_num not in tl["chapters"]:
            tl["chapters"][ch_num] = {"days_elapsed": 0, "chapter_start_time": "夜晚"}
        # chapter_titles（章节列表展示用）
        if title:
            gs.setdefault("chapter_titles", {})[ch_num] = str(title)[:60]
        return gs
    except Exception:
        return gs or None


# ── v2.5.62: 角色选择扮演——全角色预设 / 选择应用 ──

def cast_presets_build(plan: dict) -> list:
    """从 plan.json 构建全角色可扮演预设（纯函数，零 LLM）。

    输入 plan.characters {protagonist, supporting[], antagonist[]}，
    输出标准化档案列表：
    [{name, identity, personality, backstory, motivation, speak_style,
      initial_attitude, role}]——speak_style/initial_attitude/backstory 缺失时
    从既有字段推导兜底（配角档案常缺这三项）。
    异常/空输入 → []（不炸）。
    """
    try:
        chars = (plan or {}).get("characters") or {}
        out = []

        def _push(name, identity, personality, backstory, motivation, role, relation=""):
            name = str(name or "").strip()
            if not name:
                return
            pers = personality if isinstance(personality, str) else (
                " ".join(str(v) for v in (personality or {}).values()) if isinstance(personality, dict) else "")
            speak = f"说话风格贴合性格「{pers[:40]}」" if pers else f"说话风格符合{role}身份"
            att = f"与主角关系：{relation}" if relation else f"以{role}身份与玩家相处"
            out.append({
                "name": name[:30],
                "identity": str(identity or "")[:80],
                "personality": pers[:200],
                "backstory": str(backstory or "")[:200] or (f"{name}的过往（{pers[:30]}）" if pers else ""),
                "motivation": str(motivation or "")[:150] if isinstance(motivation, str) else
                              " ".join(str(v) for v in (motivation or {}).values())[:150],
                "speak_style": speak[:120],
                "initial_attitude": att[:120],
                "role": role,
            })

        proto = chars.get("protagonist") or {}
        if isinstance(proto, dict):
            _push(proto.get("name"), proto.get("identity"),
                  proto.get("personality"), proto.get("backstory"),
                  proto.get("motivation"), "protagonist",
                  relation="主角")
        for c in chars.get("supporting") or []:
            if isinstance(c, dict):
                _push(c.get("name"), c.get("identity"), c.get("personality"),
                      c.get("backstory"), c.get("motivation"), "supporting",
                      relation=c.get("relation") or c.get("meaning") or "")
        for c in chars.get("antagonist") or []:
            if isinstance(c, dict):
                # 反派 schema 无 identity/personality 字段 → 用 conflict/motivation/humanity 兜底
                _ident = c.get("identity") or c.get("conflict") or f"反派·{c.get('power', '')}"
                _pers = c.get("personality") or c.get("humanity") or c.get("motivation") or "立场坚定的对手"
                _push(c.get("name"), _ident, _pers,
                      c.get("backstory"), c.get("motivation"), "antagonist",
                      relation=c.get("conflict") or "")
        # v2.5.63: 同名去重保留第一个（同一角色出现在 supporting+antagonist 双列表时，
        # 后者（冲突描述兜底）会覆盖前者（完整档案）→ 角色选择后档案错乱）
        _seen_n = set()
        _deduped = []
        for _p in out:
            if _p["name"] not in _seen_n:
                _seen_n.add(_p["name"])
                _deduped.append(_p)
        return _deduped
    except Exception:
        return []


def choose_char_apply(state: dict, char_name: str, presets_map: dict) -> tuple:
    """应用角色选择（纯函数）：玩家扮演 char_name，其余角色 NPC 化进 casts。

    规则：
    - 角色必须在 presets_map 中，否则返回 (False, 原因)
    - player_char 设为该角色完整档案（name/identity/personality/backstory/
      motivation/speak_style/initial_attitude/role）
    - 该角色不进 casts（玩家控制）；其余角色进 casts 并挂 role（NPC 化）
    - 返回 (True, '') 或 (False, 原因)
    """
    try:
        name = str(char_name or "").strip()
        if not name:
            return False, "未选择角色"
        p = presets_map.get(name)
        if not p:
            return False, f"角色不存在: {name}"
        st = state or {}
        # v3.7: 玩家角色属性数值（从档案规则推断，供对话/判定注入锚点）
        from .attr_system import ensure_stats
        _pc_profile = {
            "identity": p.get("identity", ""),
            "personality": p.get("personality", ""),
            "backstory": p.get("backstory", ""),
            "motivation": p.get("motivation", ""),
            "speak_style": p.get("speak_style", ""),
            "role": p.get("role", ""),
            "cultivation": p.get("cultivation", ""),
        }
        st["player_char"] = {
            "name": p["name"],
            "identity": p.get("identity", ""),
            "personality_brief": p.get("personality", "")[:120],
            "backstory": p.get("backstory", "")[:200],
            "motivation": p.get("motivation", "")[:150],
            "speak_style": p.get("speak_style", ""),
            "initial_attitude": p.get("initial_attitude", ""),
            "role": p.get("role", ""),
            "stats": ensure_stats(_pc_profile),  # v3.7: 玩家属性数值
        }
        casts = st.setdefault("casts", {})
        casts.pop(name, None)  # 被选角色由玩家控制，不 NPC 化
        for other, cp in presets_map.items():
            if other != name:
                # v3.7: NPC 属性数值（从各自档案推断）
                _np_profile = {
                    "identity": cp.get("identity", ""),
                    "personality": cp.get("personality", ""),
                    "backstory": cp.get("backstory", ""),
                    "motivation": cp.get("motivation", ""),
                    "speak_style": cp.get("speak_style", ""),
                    "role": cp.get("role", ""),
                    "cultivation": cp.get("cultivation", ""),
                }
                _cast = casts.setdefault(other, {
                    "present": True,
                    "profile": {
                        "identity": cp.get("identity", ""),
                        "personality": cp.get("personality", "")[:120],
                        "speak_style": cp.get("speak_style", ""),
                    },
                    "role": cp.get("role", ""),
                })
                # v3.7: 强制写入/刷新 stats（老存档 setdefault 不会更新已有角色）
                if not isinstance(_cast.get("profile"), dict):
                    _cast["profile"] = {}
                _cast["profile"]["stats"] = ensure_stats(_np_profile, inplace=False)
        return True, ""
    except Exception as e:
        return False, f"角色选择失败: {str(e)[:60]}"


# v3.5.29: 互动场景 → 正式章节正文（互动进度回流小说）
INTERACTIVE_TO_CHAPTER_SYSTEM = """你是小说章节整理师。把互动模式的场景记录整合为正式的小说章节正文。

要求：
1. 以"本章大纲摘要"为骨架，以"互动场景记录"为血肉——玩家在互动中实际经历的
   情节、做出的选择、说过的话、产生的关系变化，都必须体现在正文里
2. 视角转换：互动记录是第二人称"你"，正文改为第三人称（用主角姓名），
   保持主角内心戏的细腻度
3. 去除互动痕迹：不出现【旁白】【动作】标签、不出现"场景N"字样、不出现
   "读者""玩家"字样；整合为连贯的段落与对话
4. 小说文笔：环境描写、人物神态、对话自然，与前文风格一致；不要列提纲、
   不要总结、不要"本章讲述了"之类的说明
5. 篇幅：接近目标字数（上下浮动 20% 可接受），宁可充实不要干瘪
   只输出章节正文，不要输出标题以外的任何解释。"""

# ── v3.6: 地点图谱 desc 补全（start 时后台一次调用——LLM 填内容，规则管结构）──
LOC_DESC_SYSTEM = """你是互动小说场景美术师。为地点写一句贴合世界观的环境描写。

要求：
1. 每句 20-40 字，至少包含光线/氛围/标志物中的两项
2. 贴合小说世界观与文风，不出现原文没有的超现实元素
3. 输出格式：每行一个「地点名: 描写」，地点名严格用给出的名字
4. 某地点无法描写 → 跳过该行
只输出内容，不要解释。"""

# ── v3.5.37: 主角状态卡提取（后台，场景后更新——LLM 结构化识别主角当前状态）──
PLAYER_STATE_SYSTEM = """你是互动小说的【世界状态追踪器】。根据最新场景，更新主角状态卡 + 全体在场 NPC 状态卡 + 角色间关系矩阵。

三者都是固定结构化字段，供后续生成完整识别当前世界状态（防地点/时间/关系/立场错乱）。

当前主角状态卡（旧）:
{old_state}

当前 NPC 状态卡（旧）:
{old_cast_states}

当前关系矩阵（旧）:
{old_relations}

最新场景:
{scene_text}

请输出 JSON（不要解释）:
{{"player": {{"location": "主角当前精确位置（如：陆氏集团28层，陆廷深办公室门口）",
  "time": "当前时间段（清晨/上午/正午/下午/傍晚/夜晚/深夜）——仅填场景中明确表现的时间段，系统会以规则时间为准自动校准，不要自行推断或臆造",
  "with": ["当前与主角同行的角色名（没有则空数组）"],
  "holding": ["主角随身携带的重要物品（没有则空数组）"],
  "condition": "身体状况（健康/轻伤/重伤/醉酒/疲惫/发烧等，没有异常则健康）",
  "disguise": "当前身份（默认用本名；若主角伪装成他人则填伪装身份名）",
  "money": "随身钱财（充裕/够用/拮据/身无分文，或'一笔现金'等）",
  "situation": "主角当前处境一句话（正在做什么/刚发生了什么）"}},
  "casts": {{"角色名": {{"present": true/false（该角色此刻是否与主角在同一地点/场景——离开场景必须翻转为 false，进入场景翻转为 true，这是最重要的字段）",
    "location": "该角色当前所在位置（与主角同场景则同位置）",
    "mood": "当前情绪（冷静/愤怒/心虚/欣喜/戒备…）",
    "stance": "对主角的态度（敌视/缓和/合作/暧昧/怀疑…）",
    "knows": ["该角色当前知道的关键信息（秘密/真相/计划，不知道就不列）"],
    "condition": "身体状况（健康/受伤/醉酒…）",
    "agenda": "该角色当前想达成什么（一句话）"}}}},
  "relations": {{"A与B": "关系描述（如：顾衍之与林佳期——利益同盟，互相提防）"}}}}
规则：location 精确到场景级；未变化保持旧值；场景中明确的变化必须更新；只更新场景中出场的角色；关系矩阵覆盖重要角色之间（主角与 NPC、NPC 与 NPC）。"""


def compute_present(state: dict) -> tuple:
    """v3.5.46: 推导【本场景在场/不在场角色名单】——防角色乱入的架构基石（模块级共享）。

    依据（多源加权，行为驱动闭环）：
    - player_state.with（与主角同行 → 一定在场）
    - cast_states.present（LLM 行为驱动标记，最权威）
    - cast_states.location == 主角位置（位置吻合 → 在场）
    - cast_states.location != 主角位置（明确在别处 → 不在场，硬约束）
    - 最近场景台词说话人（默认还在现场，除非已被标记不在场）

    返回 (present_list, away_list)。
    """
    s = state.get("state", {})
    ps = state.get("player_state") or {}
    cs = state.get("cast_states") or {}
    casts = state.get("casts") or {}
    player_name = (state.get("player_char") or {}).get("name", "")
    my_loc = clean_location(ps.get("location") or s.get("location") or "")
    with_chars = [str(x) for x in (ps.get("with") or [])]

    present = set(with_chars)
    away = set()
    for name, c in (cs or {}).items():
        name = str(name)
        loc = clean_location(c.get("location") or "")
        # 优先级：present=False 无条件不在场；位置数据可用时以位置为准
        # （防 LLM 偷懒全标 present=True——位置冲突时位置是硬数据）
        if c.get("present") is False:
            away.add(name)
        elif loc and my_loc:
            if loc == my_loc:
                present.add(name)
            elif name not in with_chars:
                away.add(name)
        elif c.get("present"):
            present.add(name)
    # 最近场景说话人默认还在场（除非明确标记不在场）
    try:
        rb = state.get("recent_blocks") or []
        for b in rb[-12:]:
            if b.get("type") == "dialogue" and b.get("speaker"):
                sp = str(b["speaker"])
                if sp not in away:
                    present.add(sp)
    except Exception:
        pass
    # v3.6: 地点图谱显式在场（locations[当前地点].chars —— 规则绑定，权威）
    try:
        _w = state.get("world") or {}
        _locs = _w.get("locations") or {}
        _entry = _locs.get(clean_location(my_loc)) if my_loc else None
        if isinstance(_entry, dict):
            for _nm in (_entry.get("chars") or []):
                _nm = str(_nm)
                if _nm and _nm not in away:
                    present.add(_nm)
    except Exception:
        pass
    # casts.present=True（v3.5.41 旧字段兼容）→ 在场
    for name, c in (casts or {}).items():
        if (c or {}).get("present") and name not in away:
            present.add(name)
    present.discard(player_name)
    away.discard(player_name)
    return sorted(present), sorted(away)


# ── v3.5.47: 后台任务串行队列——防止后台 LLM 与主流程并发抢 API 导致限流变慢 ──
# 背景：每次场景生成后，后台要做状态提取(2 次 LLM)+章节回流(1 次大 LLM)。
# 原来每个场景直接起线程 → 快速推进时 N 个线程并发打 LLM → 429 限流 →
# sleep 重试 → "剧情越深生成越慢"。
# 方案：单 worker 串行执行（同一时刻最多 1 个后台 LLM）；主流程生成时让路
# （用户操作优先，后台任务等待最多 90s）。
import queue as _bgqueue
import threading as _bthread

_bg_queue = _bgqueue.Queue(maxsize=4)
_bg_worker_started = False
_bg_worker_lock = _bthread.Lock()
main_flow_active = False  # 主流程（场景/对话/行动生成）活跃标志——后台任务须让路


def set_main_flow(v: bool):
    """v3.5.47: 设置主流程活跃标志（场景/对话/行动生成期间 True）——
    后台串行 worker 在 main_flow_active 时让路，用户操作优先不抢 LLM"""
    global main_flow_active
    main_flow_active = v


def _bg_worker_loop():
    while True:
        try:
            fn, args, critical = _bg_queue.get(timeout=5)
        except _bgqueue.Empty:
            continue
        try:
            waited = 0
            while main_flow_active and waited < 90:
                time.sleep(1)
                waited += 1
            fn(*args)
        except Exception as e:
            log.warning(f"bg task failed: {type(e).__name__}: {str(e)[:80]}")
        finally:
            _bg_queue.task_done()


def enqueue_background(fn, *args, critical: bool = False):
    """后台任务入队：单 worker 串行执行，主流程优先。

    critical=True（如章节回流）：队列满时丢弃最旧的非关键任务腾位，绝不丢。
    critical=False（如状态提取）：队列满直接丢弃（最新场景的状态会覆盖旧的，
    丢无妨——保持场景流不被阻塞）。
    """
    global _bg_worker_started
    if not _bg_worker_started:
        with _bg_worker_lock:
            if not _bg_worker_started:
                _bthread.Thread(target=_bg_worker_loop, daemon=True).start()
                _bg_worker_started = True
    if critical:
        # 队列满 → 先丢一个非关键任务腾位
        if _bg_queue.full():
            try:
                _drop_one_non_critical()
            except Exception:
                pass
    try:
        _bg_queue.put_nowait((fn, args, critical))
    except _bgqueue.Full:
        if critical:
            # 实在放不下（全是关键任务）→ 起临时线程，保证不丢
            try:
                _bthread.Thread(target=fn, args=args, daemon=True).start()
            except Exception:
                log.warning("bg critical task dropped!")
        # 非关键任务满时静默丢弃（状态提取可丢）


def _drop_one_non_critical():
    """丢弃队列中最早的非关键任务（FIFO 顺序扫描）"""
    items = []
    try:
        while not _bg_queue.empty():
            items.append(_bg_queue.get_nowait())
    except _bgqueue.Empty:
        pass
    kept = []
    dropped = False
    for it in items:
        if not dropped and not it[2]:
            dropped = True  # 丢第一个非关键
            continue
        kept.append(it)
    for it in kept:
        try:
            _bg_queue.put_nowait(it)
        except _bgqueue.Full:
            break  # 极端情况，剩余丢弃（可丢任务优先）


# ── v3.5.40: 建议选项生成（Galgame 式真两难）──
SUGGESTION_SYSTEM = """你是互动小说的选项设计师。为读者生成 3 个建议回应选项。

要求：
1. 每个选项必须是有分量的【真两难】——答应/拒绝/追问/试探/沉默/转移话题，
   没有明显"正确"答案，玩家会停顿犹豫
2. 选项来自当前角色的处境与关系（不是通用敷衍句），符合在场角色的性格
3. 每个选项 4-15 字，口语化，第一人称（读者视角）
4. 输出 JSON: {"options": ["选项1", "选项2", "选项3"]}
只输出 JSON。"""

# ── System Prompts ──
SCENE_SYSTEM = """你是互动小说导演。你正在导演一部可以随时与读者对话的互动小说。

**角色扮演（v3.5.12 最高优先级）**：读者不是旁观者，而是故事的主角——「读者化身」
（player_char，见输入中的"你扮演的主角"）。你就是以这个角色的身份在故事里生活，
场景必须完全以 TA 的视角展开。

输出格式（严格遵循标记语言）：
【旁白】叙事段落（1-3 段，文笔优美，类似严肃小说）——指代主角时用"你"，写主角的所见所闻所感
【角色名】该角色的台词（一段，符合人设）——NPC 对"你"说话
【动作】可选：无声的动作描写（如"她指尖一顿，茶水溅出半滴"）

规则：
1. 【旁白】是主体，承担叙事推进；台词用于关键时刻点睛
2. 每条台词必须标注说话人角色名；旁白不标注说话人
3. 剧情必须推进当前目标（objective），不可开无关新线
4. 若给定"待兑现事实"（facts），本段必须自然回扣至少 1 个（兑现/提及/利用/其后果显现）
5. 角色说话必须符合各自的人设卡与声音卡（口头禅/句式/情绪表达）
6. 单场景 300-600 字，节奏紧凑，不要在无关细节上停留
7. v3.5.20 收尾规则（替代 v3.5.5 发问收尾）：场景正常自然收尾，不要每段都以
   角色发问结尾（"一步一问"会让玩家疲惫、剧情推进慢）。**仅当本场景真的包含
   必须由读者当场决定的重大抉择**（生死/去留/信任/交易/身份揭晓）时，才以发问、
   邀约或对峙收尾；普通场景的悬念用旁白收（"她望着你的背影，欲言又止"），
   把话留给后续剧情自然展开。
8. v3.5.7 承接性（最高优先级）：若给定"读者上一步做了什么"（last_action）或
   "刚结束的对话"，本段场景必须从它的后果/余波/反应开始写——
   行动已改变剧情（地点/关系/物品/承诺），严禁无视玩家行为另起炉灶或时间倒流；
   若没有给定，则正常推进剧情
   v1.1 锚点式顺承（替代 v3.5.20 回轨）：玩家的行动塑造过程——永远不要否认
   玩家的行动，让它的后果与剧情推进相连（如玩家执意逛街 → 逛街中偶遇关键
   人物/发现线索）；承接篇幅点到为止（1-2 句），随即展开新的进展；
   v2.5.55 防重复铁律（P0）：严禁复述/重写【前情中已写过的任何内容】——
   环境描写、台词、动作、事件，哪怕换措辞也不行；上一场景的结尾只作为
   背景前提，本场景必须从新的事件/新的人物反应/新的进展开始写
9. v3.5.12 视角规则（代入感核心）：
   - 主角（读者化身）是场景中心，旁白写 TA 的所见所闻、内心活动与身体感受
   - 指代主角一律用"你"（如"你推开门""你感到手心发凉"），严禁用"她/他/沈念薇"旁观式转述
   - 主角是行动主体：场景中的事件发生在"你"身上或眼前，不要写成上帝视角的群像
   - NPC 的台词、动作、反应都是冲着"你"来的
10. v3.5.18 铁律（绝对禁止）：严禁生成【主角名】的台词块——主角（如【沈念薇】）的
    台词/行动只能由读者输入决定，你替 TA 说话就是破坏角色扮演。若主角需要反应，
    用旁白写 TA 的心声/身体反应（如"你心中冷笑，面上不露分毫"），而不是台词。
    输出中不得出现以主角名标注的台词行。
11. v3.5.21 空间与时间连续性（P0 级）：前情摘要包含上一场景结尾（谁在场/谁刚
    离开/去了哪里/时间点）。本场景必须严格遵守——已离开的角色不能立即出现在
    现场（除非有新剧情交代其返回）；时间只能向前流动；地点的变化必须有过渡。
    若上一场景角色"推门离去"，本场景他不在场，除非剧情明确安排他回来。
12. v3.5.27 角色白名单（P0）：本场景出场角色【仅限于】"在场角色人设"名单中的角色。
    严禁引入名单之外的角色——读者没有召唤的人不会凭空出现；若剧情确实需要
    新角色，先写环境暗示（脚步声/通报/敲门声），下一场景再登场。
13. v3.5.27 环境交代（P0）：场景【开头必须】先用 1-2 句交代当前环境——
    地点（街道名/房间/氛围）、时间（时辰/光线）、天气/声响等感官细节，
    让读者清楚"我在哪里、什么情况"。禁止一上来就抛对话或直接推进动作。
    环境交代最多 2 句，禁止大段铺陈。
14. v3.5.32 篇幅精简（P0，v3.5.54 再收紧）：单个场景总长度【150-280 字】
    （含旁白与台词），硬上限 300 字。一个场景只推进一个事件/一个对话回合：
   - 旁白简洁：环境交代 1-2 句 + 事件推进 2-3 句，心理描写最多 1 句
   - 台词克制：每个角色 1-2 句，点到为止，让玩家有接话空间
   - 禁止：大段环境铺陈、多段连续心理活动、重复描述已知信息、
     形容词堆砌、无信息量的过渡句（"空气仿佛凝固了"这类删掉）
   - 写完后自查：删掉一切不影响事件推进的句子
   玩家要在移动端快速读完，宁可少写不可啰嗦——**剧情推进比描写重要**。
15. v3.5.35 停顿衔接（节奏关键）：场景若含角色对话或情节冲突，结尾用角色的
    一个发问/邀请/等待自然收尾（1 句）——让读者清楚"该我回应了"，停顿不突兀；
    纯推进场景（无对话）结尾则正常收束，不要硬塞提问（停顿由系统节奏兜底，
    读者可用「我要说话」随时介入）。
只输出标记语言文本，不要输出解释。"""

INTRO_SYSTEM = """你是互动小说开场解说。为玩家写一份简洁的开场背景介绍（250-350 字），
用第二人称（"你"）写，像小说序章的开头，文笔凝练有氛围感。必须覆盖以下内容（缺一不可）：

一、世界观：时代背景、主要地点、势力格局（谁掌握权力/财富，社会规则是什么）
二、主要人物背景：每个出场角色的身份、与你的关系、性格底色（人人有交代，别只列名字）
三、你的处境：你现在是谁、经历了什么、正处在什么局面
四、你的目标：当前主线目标是什么、为什么

段落分明（用空行分段），先世界观后人物再处境再目标，层层递进。
基于给定资料组织，不要编造资料之外的设定；不要写成教程，要写成有代入感的开场。
只输出介绍文本，不要输出标题和解释。

【v2.5.63 角色扮演铁律】：资料里的"你扮演的角色"一节是**玩家在故事中的身份**。
整个开场介绍中，第二人称"你"必须严格指代该角色：以 TA 的身份背景、处境、
人际关系、目标来写"你"的故事。若该角色不是小说主角（如配角/反派），
开场必须切换到 TA 的视角和处境，**严禁把主角的身份、经历、婚姻状况安在"你"头上**。
主角在介绍中按"主要人物"提及（如"她叫XXX，是…"），而不是用"你"指代。
这是最重要的一条，违反即失败。"""

# v3.5.33: 开场背景压缩——超长时语义压缩为精简版（非截断，保留完整信息）
INTRO_COMPRESS_SYSTEM = """你是文案压缩师。把下面的开场背景介绍压缩到 300 字以内。

要求：
1. 保留全部关键信息：主角是谁、在哪、和谁什么关系、当前处境、要做什么
2. 删除铺陈性描写、形容词堆砌、重复信息
3. 保持第二人称"你"和小说语气，通顺自然
只输出压缩后的文本，不要解释。"""

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
- v3.5.20：角色的一般性发问（征求意见"你怎么看"、寒暄式提问"最近好吗"、
  随口试探）——**提问本身不构成节点**，剧情继续推进，读者想答随时可用按钮介入

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
8. v3.5.48 防重复：若输入中给出"已有事实"清单，**不得重复提取同一事件/承诺**
   （同一件事换个说法也算重复，如"透露把柄"与"准备拿把柄说事"是同一件事）——只提取新进展
9. v2.5.57 时间锚（time_anchor）：约定/承诺若含**具体时间**（周几/星期几/几月几号/N日后）——
   必须把原始表述填入 time_anchor（如"周五晚上"），这是该约定的唯一权威时间，后续剧情不得改写；
   只有相对弱锚（明天/今晚/改天）或无时间 → time_anchor 留空
10. v2.5.57 慎用具体时间：角色/读者**不要轻易约定具体周几或日期**——只有剧情关键约定
    （必须特定时间发生的事件）才用具体时间；普通邀约（吃饭/见面/拜访）用"改天/明天"这类弱锚，
    避免制造需要全局记忆的时间承诺
11. v2.5.57 角色发起的约定：**角色主动向读者提出含具体时间的邀约/约定**
    （"周五晚上一起吃饭吧"）→ 提取为 promise，subject=角色名，target="player"，
    content 保留原始约定内容，time_anchor 必填原始时间表述

输出 JSON:
{"facts": [{"id": "f1", "type": "promise/action/secret/change", "subject": "player/角色名", "target": "角色名/player", "content": "一句话描述", "severity": "high/medium/low", "source_chat": 轮次序号, "time_anchor": "含具体时间时的原始表述，否则空字符串"}], "relations": {"角色名": "+/-数值或描述"}, "objective_update": "更新后的目标或空", "tone": "对话基调（试探/交易/亲昵/敌对…）"}
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

# ── v3.5.49: 章节事件序列（beats）——每场景推进 1 个事件，杜绝重复生成 ──
BEAT_SYSTEM = """你是互动小说的剧情拆解师。把本章目标拆解为 N 个前后连贯的事件节点（beats）。

规则：
1. 输出恰好 {n} 个事件，每个事件对应后续的 1 个场景——事件与场景一一对应
2. 事件必须：因果递进（前一个事件的结果引发后一个事件）、覆盖本章目标、
   每个事件都有明确的新进展（严禁事件之间是同义反复）
3. 每个事件一句话（15-40 字），聚焦"发生了什么"，如："签约酒会上陆廷深当众羞辱你的作品"
4. 最后一个事件必须让本章目标达成或留下明确的下章引子

输出 JSON: {{"beats": [{{"id": 1, "desc": "事件描述"}}, ...]}}
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


def _clean_player_dialogue(blocks: list, player_name: str) -> list:
    """v3.5.27: 过滤玩家角色的自动台词（场景/对话/行动结果通用）——
    LLM 偶发替玩家说话（如【沈念薇】xxx），玩家言行只能由读者输入决定；
    转成旁白心声（不占对话气泡、不触发语音）"""
    if not player_name:
        return blocks
    cleaned = []
    for b in blocks:
        if b.get("type") == "dialogue" and b.get("speaker") == player_name:
            cleaned.append({"type": "narration", "speaker": "",
                            "content": f"你心中所想：{b.get('content', '')}"})
        else:
            cleaned.append(b)
    return cleaned


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
        self._tracker = None   # v3.5.22: 复用小说模式 CharacterStateTracker（懒加载）
        self._supervisor = None  # v3.5.22: 复用小说模式 LogicSupervisor（懒加载）

    # ── v3.5.22: 复用小说模式逻辑引擎（不另起炉灶）──
    def _logic_tracker(self):
        """角色状态追踪器（小说模式 CharacterStateTracker）——跟踪角色位置/状态，
        保证互动场景的空间连续性有结构化依据"""
        if self._tracker is None and self.engine is not None:
            try:
                from ..character_state import CharacterStateTracker
                self._tracker = CharacterStateTracker(
                    self.engine.client, self.engine.model, self.engine.memory)
            except Exception as e:
                log.warning(f"CharacterStateTracker init failed: {e}")
        return self._tracker

    def _logic_supervisor(self):
        """逻辑监督器（小说模式 LogicSupervisor）——L1 规则引擎检查
        时间线/空间/行为/物品矛盾"""
        if self._supervisor is None and self.engine is not None:
            self._supervisor = getattr(self.engine, "logic_supervisor", None)
        return self._supervisor

    def _logic_context(self, novel_id: str) -> str:
        """复用角色状态追踪：返回当前角色状态文本（位置/健康等）供场景注入"""
        try:
            tr = self._logic_tracker()
            if tr is None:
                return ""
            tr.init_from_plan(novel_id)  # 幂等：已有状态不覆盖
            return tr.build_context(novel_id) or ""
        except Exception as e:
            log.warning(f"logic_context failed: {e}")
            return ""

    def enrich_location_descs(self, novel_id: str, state: dict):
        """v3.6: 地点图谱 desc 一次性补全（LLM 填内容，规则管结构——防幻觉）。

        start 时后台触发：只补 desc 为空的节点（≤12 个），失败静默（空 desc
        不影响移动/在场等规则功能，只影响环境描写素材）。
        """
        try:
            w = state.get("world") or {}
            locs = w.get("locations") or {}
            missing = {k: v for k, v in locs.items()
                       if not str(v.get("desc") or "").strip()}
            if not missing:
                return
            names = list(missing.keys())[:12]
            user = (
                f"小说《{state.get('title', '')}》{state.get('genre', '')}·{state.get('style', '')}世界观。\n"
                f"当前地点: {str((w.get('location') or ''))}\n"
                f"为下列地点各写一句环境描写（20-40 字）：\n"
                + "\n".join(f"- {n}" for n in names)
            )
            raw = self._llm(LOC_DESC_SYSTEM, user, temperature=0.4, max_tokens=500)
            import re as _re
            for seg in _re.split(r"\n(?=-)", raw or ""):
                m = _re.match(r"-?\s*([^:：]{1,12})[:：]\s*(.+)", seg.strip())
                if m:
                    name, desc = m.group(1).strip(), m.group(2).strip()
                    if name in locs and desc and not str(locs[name].get("desc") or ""):
                        locs[name]["desc"] = desc[:80]
            w["locations"] = locs
            self.store.save_state(novel_id, state)
        except Exception as e:
            log.warning(f"enrich desc failed: {type(e).__name__}: {str(e)[:80]}")

    def _extract_player_state(self, novel_id: str, scene_text: str):
        """v3.5.37/43: 场景后（后台）提取【世界状态】——主角状态卡 + NPC 状态卡 + 关系矩阵"""
        try:
            st = self.store.load_state(novel_id) or {}
            old_ps = st.get("player_state") or {}
            old_cs = st.get("cast_states") or {}
            old_rel = st.get("npc_relations") or {}
            user = PLAYER_STATE_SYSTEM.format(
                old_state=old_ps or {},
                old_cast_states=old_cs or {},
                old_relations=old_rel or {},
                scene_text=str(scene_text)[:1600],
            )
            raw = self._llm(PLAYER_STATE_SYSTEM, user, temperature=0.2, max_tokens=700)
            import re as _re
            # v3.5.43: 贪婪匹配整个 JSON 对象（嵌套结构用非贪婪会截断在第一个 }）
            m = _re.search(r"\{.*\}", raw or "", _re.S)
            if not m:
                return
            data = json.loads(m.group(0))
            if not isinstance(data, dict):
                return
            # ── 主角状态卡 ──
            ps = data.get("player") or {}
            # v3.6.5: time 由规则引擎唯一决定——LLM 提取的时间不采信，
            # 直接同步 world.time（异常路径 validate_llm_state 未覆盖时的兜底）
            _wt5 = (st.get("world") or {}).get("time") or {}
            _pt5 = str(_wt5.get("label", "") or "") or str(
                ps.get("time", old_ps.get("time", "")))[:20]
            clean = {
                # v3.5.51: location 必须过 clean_location——LLM 偶尔输出对象/数组
                # （JSON 片段被 str() 强转进字段），导致场景地点脏数据
                "location": clean_location(str(ps.get("location", old_ps.get("location", ""))))[:80],
                "time": _pt5[:20],
                "with": [str(x)[:20] for x in (ps.get("with") or [])][:4],
                "holding": [str(x)[:30] for x in (ps.get("holding") or [])][:5],
                "situation": str(ps.get("situation", old_ps.get("situation", "")))[:120],
                "condition": str(ps.get("condition", old_ps.get("condition", "健康")))[:20],
                "disguise": str(ps.get("disguise", old_ps.get("disguise", "")))[:30],
                "money": str(ps.get("money", old_ps.get("money", "")))[:30],
            }
            # v3.6: LLM 提取结果过规则校验（防幻觉）——location 过图谱/known 校验、
            # with/holding 等采纳；world 三支柱同步（location 冲突时以 world 为准）
            try:
                from .world_state import validate_llm_state
                st["player_state"] = validate_llm_state(st, clean)
            except Exception:
                st["player_state"] = clean
            # ── v3.5.43: NPC 状态卡（行为驱动更新，LLM 完整了解所有角色状态）──
            cs = data.get("casts") or {}
            clean_cs = {}
            for name, c in cs.items():
                if not isinstance(c, dict):
                    continue
                clean_cs[str(name)[:20]] = {
                    "present": bool(c.get("present", old_cs.get(str(name), {}).get("present", True))),
                    # v3.5.51: 同样清洗 NPC 位置（防 JSON 脏数据污染场景地点）
                    "location": clean_location(str(c.get("location", "")))[:60],
                    "mood": str(c.get("mood", ""))[:20],
                    "stance": str(c.get("stance", ""))[:30],
                    "knows": [str(k)[:50] for k in (c.get("knows") or [])][:4],
                    "condition": str(c.get("condition", "健康"))[:20],
                    "agenda": str(c.get("agenda", ""))[:60],
                }
            # v2.5.60: 合并更新（修复整体覆盖丢失 bug）——LLM 输出的角色更新，
            # 未输出的角色保留旧状态（情绪/立场/议程不丢 → 已完成事件不重演）
            st["cast_states"] = merge_cast_states(old_cs, clean_cs)
            # ── v3.5.43: NPC↔NPC 关系矩阵（防止 AI 搞错角色间恩怨）──
            rel = data.get("relations") or {}
            clean_rel = {str(k)[:40]: str(v)[:60] for k, v in rel.items() if isinstance(k, str)}
            if clean_rel:
                st["npc_relations"] = clean_rel
            self.store.save_state(novel_id, st)
        except Exception as e:
            log.warning(f"player_state extract failed: {type(e).__name__}: {str(e)[:80]}")

    def compute_present(self, state: dict) -> tuple:
        """v3.5.46: 推导【本场景在场/不在场角色名单】（兼容包装，实现在模块级）"""
        return compute_present(state)

    def _validate_scene_present(self, novel_id: str, scene_num: int, blocks: list):
        """v3.5.46: 场景后在场校验（后台防线）——台词角色命中 away 名单 →
        记录 violation + 合理化 cast_states（位置/在场同步，防后续矛盾继续扩散）"""
        try:
            st = self.store.load_state(novel_id) or {}
            _present, _away = self.compute_present(st)
            if not _away:
                return
            hits = [str(b.get("speaker")) for b in (blocks or [])
                    if b.get("type") == "dialogue" and b.get("speaker") in _away]
            if not hits:
                return
            hits = sorted(set(hits))
            # 合理化：LLM 已让 TA 出场 → 状态同步（行为既成事实，硬删会产生更多矛盾）
            cs = st.get("cast_states") or {}
            ps = st.get("player_state") or {}
            my_loc = clean_location(ps.get("location") or "")
            for sp in hits:
                c = cs.setdefault(sp, {})
                c["present"] = True
                if my_loc:
                    c["location"] = my_loc
            st["cast_states"] = cs
            st["present_violations"] = st.get("present_violations", []) + [
                {"scene": scene_num, "chars": hits,
                 "ts": time.strftime("%m-%d %H:%M")}]
            st["present_violations"] = st["present_violations"][-5:]
            self.store.save_state(novel_id, st)
            log.warning(f"[在场校验] 场景{scene_num} 不在场角色乱入: {hits}（已合理化，违规次数:{len(st['present_violations'])}）")
        except Exception as e:
            log.warning(f"validate_present failed: {type(e).__name__}: {str(e)[:80]}")

    def _post_scene_logic_check(self, novel_id: str, scene_num: int, scene_text: str, blocks: list = None):
        """场景生成后（后台）：复用小说模式引擎做状态更新 + 矛盾检查"""
        try:
            # v3.6: 场景推进 → 时间规则推进（每 2 个场景 +1 档，与 world 三支柱同步）
            try:
                from .world_state import ensure_world, advance_time
                _st0 = self.store.load_state(novel_id) or {}
                ensure_world(_st0)
                if int(scene_num or 0) % 2 == 1:
                    advance_time(_st0["world"], 1)
                    _ps0 = _st0.get("player_state") or {}
                    _ps0["time"] = (_st0["world"].get("time") or {}).get("label", "")
                    _st0["player_state"] = _ps0
                    self.store.save_state(novel_id, _st0)
            except Exception as e:
                log.warning(f"scene time advance failed: {e}")
            # v3.5.46: 在场校验（不在场角色乱入检测 + 合理化）
            try:
                self._validate_scene_present(novel_id, scene_num, blocks or [])
            except Exception as e:
                log.warning(f"validate present failed: {e}")
            # v3.5.37: 主角状态卡提取（精确位置/时间/同行/物品/处境）
            try:
                self._extract_player_state(novel_id, scene_text)
            except Exception as e:
                log.warning(f"ps extract failed: {e}")
            # v1.1 P3: 时间单调校验（L2 领域约束）——LLM 提取时间不得倒流
            try:
                _st3 = self.store.load_state(novel_id) or {}
                _ps3 = _st3.get("player_state") or {}
                _prev_t = str(_st3.get("_prev_time", "") or "")
                _cur_t = str(_ps3.get("time", "") or "")
                if _prev_t and _cur_t and not time_valid(_cur_t, _prev_t):
                    _ps3["time"] = _prev_t  # 时间倒流 → 保留旧值
                    _st3["player_state"] = _ps3
                _st3["_prev_time"] = _cur_t or _prev_t
                self.store.save_state(novel_id, _st3)
            except Exception as e:
                log.warning(f"time valid failed: {e}")
            # v3.6.5: 场景文本时间漂移检测（规则兜底）——LLM 叙事提到穿越时段
            # （深夜场景写晨阳 / 下午场景写半夜）→ 记录 violation + 强制对齐
            try:
                from .world_state import time_drift_check
                _st5 = self.store.load_state(novel_id) or {}
                _drift = time_drift_check(scene_text, _st5.get("world") or {})
                if _drift:
                    _st5["time_violations"] = _st5.get("time_violations", []) + [
                        {"scene": scene_num, "drift": str(_drift)[:120],
                         "ts": time.strftime("%m-%d %H:%M")}]
                    _st5["time_violations"] = _st5["time_violations"][-5:]
                    self.store.save_state(novel_id, _st5)
                    log.warning(f"[时间连续性] 场景{scene_num} 漂移: {_drift}")
            except Exception as e:
                log.warning(f"time drift failed: {e}")
            # v2.5.57: 承诺时间冲突检测——新场景时间表述 vs 未兑现约定台账
            try:
                _st4 = self.store.load_state(novel_id) or {}
                _conf = promise_conflict_check(scene_text, _st4)
                if _conf:
                    _st4["promise_conflict"] = str(_conf)[:120]
                    self.store.save_state(novel_id, _st4)
                    log.warning(f"[承诺台账] 场景{scene_num} 时间冲突: {_conf}")
            except Exception as e:
                log.warning(f"promise conflict failed: {e}")
            # v2.5.60: 状态变化规则兜底——场景文本出现明确转折词（拒绝/决裂/翻脸…）
            # → 强制记录事件时间线（零 LLM；即使 LLM 状态提取失败，关键事件也不丢）
            try:
                _chg = state_change_detect(scene_text)
                if _chg:
                    _st5 = self.store.load_state(novel_id) or {}
                    from .char_memory import add_event
                    add_event(_st5, _chg, "change")
                    self.store.save_state(novel_id, _st5)
            except Exception as e:
                log.warning(f"state change detect failed: {e}")
            # 1) 角色状态更新（提取位置/状态变化 → global_state.json）
            tr = self._logic_tracker()
            if tr is not None:
                import asyncio
                asyncio.run(tr.update_from_chapter(novel_id, scene_num, scene_text))
            # 2) L1 逻辑监督（时间线/空间/行为/物品矛盾，规则引擎零 LLM 成本）
            sup = self._logic_supervisor()
            if sup is not None:
                plan = None
                gs = None
                try:
                    plan = self.engine.memory.read("plan", novel_id)
                    gs = self.engine.memory.read("global_state", novel_id) or {}
                except Exception:
                    pass
                prev = {}
                if scene_num > 1:
                    last_scenes = self.store.recent_scenes(novel_id, 1) or []
                    if last_scenes:
                        prev[scene_num - 1] = str(last_scenes[0].get("scene_text", ""))
                res = sup.validate_chapter(scene_text, scene_num, plan or {},
                                           prev, gs, run_deep=False)
                # 视角适配：互动模式恒为第二人称（"你"指代主角），小说模式的
                # "主角全名未出现"类检查是无条件误报——直接过滤（v1.1 修复：
                # 原 count("你")>10 阈值在 300 字场景下不稳定导致误报反复出现）
                violations = [v for v in (res.get("violations") or [])
                              if "未出现" not in str(v.get("description", ""))]
                p0 = [v for v in violations if v.get("severity") == "P0"]
                if p0:
                    cats = [f"{v.get('category', '?')}:{v.get('description', '')[:40]}" for v in p0[:3]]
                    log.warning(f"[逻辑监督] 场景{scene_num} P0 矛盾: {' | '.join(cats)}")
                    try:
                        st = self.store.load_state(novel_id)
                        if st:
                            from .char_memory import add_event
                            add_event(st, f"⚠ 检测到剧情矛盾（已记录待修正）: {p0[0].get('description', '')[:40]}", "warning")
                            self.store.save_state(novel_id, st)
                    except Exception:
                        pass
        except Exception as e:
            log.warning(f"post_scene_logic_check failed: {type(e).__name__}: {str(e)[:100]}")
        # 3) AI 痕迹检测（复用小说模式 AIDetector 离线规则，零 LLM 成本）
        try:
            from ..ai_detector import AIDetector
            det = AIDetector._offline_detect(scene_text)
            if det.get("ai_score", 0) >= 60:
                log.warning(f"[AI检测] 场景{scene_num} AI 痕迹 {det.get('ai_score')}/100")
                try:
                    st = self.store.load_state(novel_id)
                    if st:
                        from .char_memory import add_event
                        add_event(st, f"⚠ 本段 AI 腔较重（{det.get('ai_score')}/100）", "warning")
                        self.store.save_state(novel_id, st)
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"ai_detect failed: {e}")

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
                          temperature: float = 0.8, max_tokens: int = 2500) -> AsyncIterator[str]:
        # v3.5.47: 主流程 LLM 活跃标志——后台任务（状态提取/章节回流）让路，
        # 防止并发打 API 触发限流导致生成变慢
        set_main_flow(True)
        try:
            async for chunk in self._resilient.create_stream(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield chunk
        except Exception as e:
            log.warning(f"StoryDirector stream failed: {type(e).__name__}: {str(e)[:120]}")
            yield ""  # 空 chunk，让前端感知结束
        finally:
            set_main_flow(False)

    # ── 上下文组装 ──
    def _build_scene_prompt(self, state: dict, summary: str) -> str:
        s = state.get("state", {})
        parts = []
        parts.append(f"## 小说：《{state.get('title', '')}》（{state.get('genre', '')}·{state.get('style', '')}）")
        # v3.5.34: 文风要求（来自小说风格配置——文笔/语气/对话风格必须符合）
        _sb = state.get("style_brief") or ""
        if _sb:
            parts.append("## 文风要求（必须严格遵守）:\n" + _sb[:400])
        # v3.5.12: 玩家角色扮演——读者化身是主角，场景以 TA 视角写（代入感核心）
        # v2.5.62: 角色选择扮演——玩家可扮演任意角色（主角/配角/反派），注入完整档案
        pc = state.get("player_char") or {}
        if pc.get("name"):
            parts.append(f"## 你扮演的角色（读者化身）: {pc['name']}")
            if pc.get("identity"):
                parts.append(f"身份: {pc['identity']}")
            if pc.get("personality_brief"):
                parts.append(f"性格: {pc['personality_brief'][:120]}")
            if pc.get("speak_style"):
                parts.append(f"说话风格: {pc['speak_style'][:100]}")
            if pc.get("initial_attitude"):
                parts.append(f"初始处境: {pc['initial_attitude'][:100]}")
            if pc.get("backstory"):
                parts.append(f"过往: {pc['backstory'][:100]}")
            if pc.get("motivation"):
                parts.append(f"动机: {pc['motivation'][:100]}")
            parts.append("本场景完全以这位角色的视角展开：旁白用'你'指代 TA，TA 是场景中心，"
                         "事件发生在 TA 身上/眼前，严禁旁观者视角")
        # v3.2: 世界观注入（保证剧情贴合本小说设定）
        wb = state.get("worldbuilding_brief") or ""
        if wb:
            parts.append(f"## 世界观设定（必须严格遵守，不得偏离）:\n{wb[:600]}")
        # v3.5.20: 复用全局状态——时间线/章节脉络（剧情连续）+ 未回收伏笔（可呼应）
        tl = state.get("timeline_brief") or ""
        if tl:
            parts.append(f"故事时间线（保持连续，不要与已发生的事件矛盾）: {tl[:200]}")
        fs = state.get("foreshadows_brief") or ""
        if fs:
            parts.append(f"未揭晓的伏笔（剧情中可自然铺垫/呼应，不必强行回收）: {fs[:200]}")
        # v3.5.22: 复用小说模式角色状态追踪——当前角色位置/状态（结构化，防瞬移）
        nid = state.get("novel_id", "")
        if nid:
            ctx = self._logic_context(nid)
            if ctx:
                parts.append(f"当前角色状态（必须遵守，场景中角色的位置/状态以此为准）:\n{ctx[:300]}")
        parts.append(f"当前场景号: {state.get('scene_num', 0)}")
        # v3.5.28: 大纲驱动——当前章节目标（互动剧情按大纲章节推进）
        oc = state.get("outline_chapters") or []
        op = state.get("outline_progress") or {}
        if oc:
            ci = min(int(op.get("idx", 0)), len(oc) - 1)
            ch = oc[ci]
            parts.append(f"当前剧情章节（本章目标，场景必须围绕它推进）: "
                         f"第{ch.get('number', ci + 1)}章《{ch.get('title', '')}》"
                         f"（{ch.get('volume', '')}）—— {ch.get('summary', '')}")
            # v3.5.49: 本章事件进度（beats）——本场景只能推进"进行中"事件，
            # 已完成事件严禁重演（只在对话中可被提及）。beats 由切章时后台
            # 预生成（_ensure_chapter_beats），这里只读缓存，零额外延迟
            try:
                _cb = state.get("chapter_beats") or {}
                beats = _cb.get("beats") or [] if _cb.get("chapter_idx") == ci else []
                if beats:
                    _b_lines = []
                    for _b in beats:
                        _mk = {"done": "已完成", "current": "进行中(本场景推进)",
                               "pending": "未开始"}.get(_b.get("status"), "未开始")
                        _b_lines.append(f"  [{_mk}] {_b.get('desc', '')}")
                    parts.append("## 本章事件进度（P0 硬约束——防重复生成）:\n"
                                 + "\n".join(_b_lines)
                                 + "\n规则: 每个事件只演一次；进行中事件是本场景的唯一焦点；"
                                   "已完成事件不得重新上演（最多在台词中被提及作为铺垫）；"
                                   "未开始事件不提前发生。")
            except Exception as e:
                log.warning(f"beats inject failed: {e}")
        # v1.1 锚点式: 剧情引导（替代 v3.5.49 主线规则 P0 硬约束——软牵引）
        # 本章终点 = 当前主线目标（P2 条件检查器后改为当前锚点 desc）
        _tension = int(state.get("tension", 0) or 0)
        _obj = str(s.get("objective", "") or "")
        _endpoint = _obj or "推进本章目标"
        _push = (f"当前主线（{_obj}）正在逼近，与它相关的事件/人物正朝你而来"
                 if _obj else "故事正在酝酿新的变化")
        parts.append(
            f"## 剧情引导（v1.1 锚点式）:\n"
            f"本章终点: {_endpoint[:80]}\n"
            f"剧情张力: {_tension}/10\n"
            + (f"世界推力: {_push}\n" if _tension >= 3 else "")
            + "引导原则: 玩家的行动塑造过程——永远不要否认玩家的行动，让它的后果"
              "与剧情推进相连（如玩家执意逛街 → 逛街中偶遇关键人物/发现线索）；"
              "张力较高时，让世界事件/角色主动推动剧情（事件找上门）；"
              "玩家明确拒绝当前事件时，以'拒绝及其后果'推进事件"
              "（如拒绝交易→对方翻脸），而不是跳过事件另起炉灶。")
        # v2.5.58: 世界状态简报（玩家现状/角色情绪态度/关系/最近事件——角色反应与剧情展开的依据）
        try:
            _wctx = state_context_brief(state)
            if _wctx:
                parts.append(f"## 当前世界状态（生成角色反应/剧情后果必须基于此）:\n{_wctx}")
        except Exception:
            pass
        # v3.6.5 P0: 权威时间横幅——现在是什么时候由规则引擎唯一决定，
        # 场景叙事/台词的时间表述必须以此为基准（防时间错乱：深夜与晨阳共存、
        # 问候语与时段不符、凭空跳日等）
        try:
            from .world_state import ensure_world, time_now_brief
            ensure_world(state)
            _tb = time_now_brief(state.get("world") or {})
            if _tb:
                parts.append(f"## {_tb}")
        except Exception:
            pass
        # v1.1 P2: 锚点触发进入方式（entry_hook）——本场景以它开场，事件找上门
        _at = state.get("anchor_triggered")
        if _at and _at.get("hook"):
            parts.append(f"当前剧情事件（本场景以此开场，自然引入，不得无视）: {_at['hook']}")
        # v3.6 P5: 偏离主线收束提示（连续行动游离主线 → 自然牵引）
        try:
            _dh = beat_drift_hint(state)
            if _dh:
                parts.append(f"## 主线牵引:\n{_dh}")
        except Exception:
            pass
        # v1.1 P4: 跨章张力介入（保险③）——连续 2 章偏离，主线势力施压
        try:
            _mp = mainline_pressure(state)
            if _mp:
                parts.append(f"## 世界施压:\n{_mp}")
        except Exception:
            pass
        # v1.1 P4: 填充事件（节奏调节）——张力高位未触发锚点时维持节奏
        try:
            _se = side_event_hint(state)
            if _se:
                parts.append(f"## 填充事件:\n{_se}")
        except Exception:
            pass
        # v1.1 P4: 主线健康度捷径（保险④）——切章对账落后，注入捷径提示
        if state.get("mainline_shortcut"):
            parts.append("## 主线捷径: 一位与主线相关的角色主动向你提供关键线索"
                         "（帮你补上错过的进度），自然地出现在本场景。")
        # v3.5.49: 对话结论落地——上一场对话解决了什么，本场景必须承接
        _cc = state.get("chat_conclusion") or ""
        if _cc:
            parts.append(f"刚结束的对话结论（本场景必须承接其成果/后果，不得当没发生过）: {str(_cc)[:150]}")
            state["chat_conclusion"] = ""  # 只承接一次，用后即清
        if s.get("location"):
            parts.append(f"地点: {clean_location(s['location'])}")
        # v3.5.37: 主角状态卡（精确位置/时间/同行/物品/处境——LLM 结构化识别）
        ps = state.get("player_state") or {}
        if ps:
            # v3.6.5: 时间字段强制取 world.time（规则权威），不采信 LLM 提取值
            _wt0 = (state.get("world") or {}).get("time") or {}
            _pt0 = str(_wt0.get("label", "") or "") or str(ps.get("time", "") or "")
            _pd0 = int(_wt0.get("day") or 1)
            _when0 = f"第{_pd0}天·{_pt0}" if _pd0 > 1 else _pt0
            parts.append(f"主角状态卡（以此为准，保持状态连续）: "
                         f"位置[{clean_location(ps.get('location'))}] 时间[{_when0}] "
                         f"同行[{','.join(ps.get('with') or []) or '无'}] "
                         f"物品[{','.join(ps.get('holding') or []) or '无'}] "
                         f"身体[{ps.get('condition', '健康')}] 身份[{ps.get('disguise', '本名') or '本名'}] "
                         f"钱[{ps.get('money', '') or '未定'}] 处境[{ps.get('situation', '')}]")
        # v3.5.43: NPC 状态卡（行为驱动——每个角色当前在哪/情绪/立场/知道什么/想干什么）
        cs = state.get("cast_states") or {}
        if cs:
            _cs_lines = []
            for _n, _c in cs.items():
                _cs_lines.append(f"{_n}[位置{clean_location(_c.get('location'))} 情绪{_c.get('mood', '')} "
                                 f"立场{_c.get('stance', '')} 身体{_c.get('condition', '健康')} "
                                 f"知道[{','.join(_c.get('knows') or []) or '无'}] "
                                 f"想[{_c.get('agenda', '')}]]")
            parts.append("NPC 状态卡（以此为准——角色行为/台词必须符合其状态）:\n" + "\n".join(_cs_lines))
        # v3.5.46: 本场景在场角色 P0 硬约束——防角色乱入（谁在场/谁不在场一清二楚）
        _present, _away = self.compute_present(state)
        _loc = clean_location(ps.get("location") or s.get("location") or "")
        if _present or _away:
            _pl = "、".join(_present) if _present else "（仅主角一人）"
            _al = ("明确不在场（严禁让 TA 们在本场景现身或说话，最多在台词中被提及）: "
                   + "、".join(_away)) if _away else ""
            parts.append(f"## 本场景在场角色（P0 硬约束——防角色乱入）:\n"
                         f"当前地点: {_loc or '（未定）'}\n"
                         f"在场（本场景只能让这些角色现身/说话/被互动）: {_pl}\n"
                         f"{_al}\n"
                         f"规则: 不在场角色无论与主角多熟都不得凭空出现；新人登场必须由剧情自然引出"
                         f"（如读者走入新地点遇到的人），出场后即视为在场。")
        # v3.5.43: 角色间关系矩阵（防止 AI 搞错 NPC 之间的恩怨）
        nr = state.get("npc_relations") or {}
        if nr:
            parts.append("角色间关系（以此为准）: " + "；".join(
                f"{k}→{v}" for k, v in list(nr.items())[:6]))
        if s.get("objective"):
            parts.append(f"主线目标（必须推进）: {s['objective']}")
        if s.get("flags"):
            parts.append(f"剧情标记: {'、'.join(s['flags'][-5:])}")
        # 待兑现事实（硬挂钩）
        facts = [f for f in state.get("facts", []) if f.get("status") == "active"]
        if facts:
            parts.append("玩家的重要选择（Galgame 式延迟回响：存在自然时机时让角色提起这些旧承诺/共同经历——'你当初答应我的事'，不要每段都提，也不要装作不记得）:")
            for f in facts[:6]:
                parts.append(f"- [{f.get('type')}] {f.get('content')}")
        # v2.5.59: 未兑现约定——分级注入（推进时钟防打转）：
        # 未到期不注入（NPC 不再每轮提起同一约定）；到期注入推进指令；
        # 过期注入追问指令（NPC 追问/关系受损）
        try:
            _due_state = promise_due_check(state)
            _due = _due_state.get("due") or []
            _overdue = _due_state.get("overdue") or []
            if _due:
                parts.append("约定时间已到（本场景必须推进兑现——赴约/取消/变故，"
                             "不能再停留在'约定好了'的状态，让剧情向前走）:")
                for _p in _due[-2:]:
                    parts.append(f"- [{_p.get('when_raw', '?')}] 与{_p.get('who', '?')}约定: {_p.get('what', '')}")
            if _overdue:
                parts.append("约定已过期未兑现（相关角色本场景必须追问/表达失望，"
                             "或出现变故导致约定作废——约定不能再无限期挂着）:")
                for _p in _overdue[-2:]:
                    parts.append(f"- [{_p.get('when_raw', '?')}] 与{_p.get('who', '?')}约定: {_p.get('what', '')}")
        except Exception:
            pass
        if state.get("promise_conflict"):
            parts.append(f"⚠ 系统检测到上一场景时间表述与未兑现约定冲突（{state['promise_conflict']}）——"
                         f"本场景必须修正：提到该约定一律沿用原始时间，禁止再出现冲突的新时间表述")
        # v3.3.1: 上一场对话未达成的目标（missing hooks）——软约束：后果显现/角色惦记
        missing = state.get("pending_missing_hooks") or []
        if missing:
            parts.append("上一场对话未谈成的事（本段剧情可让其后顾显现，或角色主动提起追问）:")
            for m in missing[:3]:
                parts.append(f"- {str(m)[:60]}")
        if summary:
            parts.append(f"前情摘要（这是已发生的历史，严禁重演——本场景必须承接其后果并推进新的剧情事件，"
                          f"不得把前情内容换个说法重新写一遍，特别是'上一场景结尾'只能作为背景铺垫，"
                          f"禁止复述其环境描写/台词/动作）: {summary[:600]}")
        # v2.5.55: 近期事件时间线注入（防重复治本——给 LLM 明确的"已发生"记忆）
        _evs = [(str(e.get("ts", "")), str(e.get("summary", ""))[:32])
                for e in (state.get("events") or [])[-8:] if e.get("summary")]
        if _evs:
            parts.append("本互动最近已发生的事件（均已发生过，严禁重演——本场景必须推进新的事件）:\n"
                         + "\n".join(f"- [{ts}] {s}" for ts, s in _evs))
        # v2.5.55: 上一场景重复检测命中 → 强制本场景全新（规则兜底，LLM 约束不可靠时生效）
        if state.get("scene_repeat"):
            parts.append("⚠ 系统检测到上一场景与更早场景内容重复——本场景必须生成全新的剧情事件："
                         "新地点/新人物/新冲突/时间推进，严禁延续或复述之前任何场景的内容！")
        # v3.5.7: 读者上一步行动（承接性——新场景必须从行动后果写起）
        la = state.get("last_action") or {}
        if la and la.get("summary"):
            parts.append(f"读者上一步做了什么（本段必须从这件事的后果/余波写起，严禁无视）:")
            parts.append(f"- [{la.get('type', '行动')}] {la.get('summary', '')[:200]}")
        # v3.5.7: 刚结束的对话（承接对话结论）
        nid = state.get("novel_id", "")
        player_name = (state.get("player_char") or {}).get("name", "读者")
        recent_chats = self.store.recent_chats(nid, 6) if (nid and hasattr(self.store, "recent_chats")) else []
        chat_lines = [f"{player_name if c.get('role') == 'user' else c.get('speaker', '角色')}: {str(c.get('content', ''))[:80]}"
                      for c in recent_chats if c.get("content")]
        if chat_lines:
            parts.append("刚结束的对话（本段可自然承接其中情绪/未尽话题，但不要复述）:")
            for line in chat_lines[-4:]:
                parts.append(f"- {line}")
        # v3.5.9: 事件时间线（刚发生的事——保持剧情连续性）
        from .char_memory import events_brief
        ev_brief = events_brief(state, 5)
        if ev_brief:
            parts.append(f"最近发生的事（承接时间线，不要时间倒流）: {ev_brief}")
        # 角色卡（v3.5.12: 主角标注，防止 LLM 替主角写台词/用第三人称转述）
        casts = state.get("casts", {})
        # v3.5.46: 人设只注入【在场角色】——不在场角色档案不展示，杜绝 LLM 乱入素材
        # （不在场名单已通过 cast_states 位置可见，但细节档案不再供给）
        # v3.5.41: temp 角色（临时登场）不入白名单人设——防乱入合法化
        _present_set = set(_present)
        _whitelist = {n: c for n, c in casts.items()
                      if not (c or {}).get("temp") and n in _present_set}
        # 兜底：状态未建立的旧档退化为全量（保证人设不缺失）
        if not _whitelist and casts:
            _whitelist = {n: c for n, c in casts.items() if not (c or {}).get("temp")}
        if _whitelist:
            parts.append("在场角色人设（v3.5.50 全维度——行为准则/说话风格/绝对底线，必须严格遵守）:")
            for name, c in _whitelist.items():
                if name == player_name:
                    parts.append(f"- {name}（主角，由读者扮演——不要替 TA 写台词，TA 的言行由读者决定）")
                    continue
                prof = c.get("profile", {})
                if not isinstance(prof, dict):  # v3.6.1: 字符串档案防御
                    prof = {}
                # v3.5.50: 全维度消费角色蒸馏——行为规则（决策启发式）是人设
                # 的核心，之前只注入台词碎片（dna+anti 各2条）导致行为脱人设
                segs = []
                mm = prof.get("mental_models", [])[:1]
                for m in mm:
                    if isinstance(m, dict):
                        segs.append(f"心智[{str(m.get('name', ''))[:16]}:{str(m.get('principle', m.get('description', '')))[:36]}]")
                    else:
                        segs.append(f"心智[{str(m)[:50]}]")
                heur = prof.get("decision_heuristics", [])[:2]
                for h in heur:
                    if isinstance(h, dict):
                        _tr = str(h.get("trigger", ""))[:30]
                        if _tr.startswith("当"):
                            _tr = _tr[1:]
                        _ac = str(h.get("action", ""))[:50]
                        segs.append(f"当{_tr}→{_ac}")
                    else:
                        segs.append(str(h)[:80])
                dna = prof.get("expression_dna", [])[:2]
                for d in dna:
                    if isinstance(d, dict):
                        segs.append(f"风格[{d.get('name', '')}:{str(d.get('example', ''))[:30]}]")
                    else:
                        segs.append(f"风格[{str(d)[:40]}]")
                anti = prof.get("anti_patterns", [])[:3]
                for a in anti:
                    segs.append(f"绝不[{a.get('pattern', a) if isinstance(a, dict) else a}"[:60] + "]")
                boundary = prof.get("boundary", {}) or {}
                rules = (boundary.get("rules") or boundary.get("anti_collapse_checks") or [])[:1]
                for r in rules:
                    segs.append(f"底线[{str(r)[:60]}]")
                if segs:
                    parts.append(f"- {name}: " + "；".join(segs))
        # v3.5.51: 在场角色记忆注入——角色记得的旧事（承诺/共同经历/秘密）必须
        # 在场景行为中体现。之前只有对话引擎消费记忆，场景生成对角色记忆一无所知
        # → 场景里角色行为与记忆矛盾（该记得的不记得/凭空忘记承诺）
        try:
            from .char_memory import get_memories
            _mem_lines = []
            for name in list(_whitelist.keys())[:4]:
                mems = get_memories(state, name, 3)
                if mems:
                    _ms = "；".join(str(m.get("content", ""))[:45] for m in mems)
                    _mem_lines.append(f"- {name}: {_ms}")
            if _mem_lines:
                parts.append("在场角色记忆（角色明确记得的事——行为/台词必须与之吻合，"
                             "不得装作不记得，也不得每场景都重提）:\n" + "\n".join(_mem_lines))
        except Exception as e:
            log.warning(f"mem inject failed: {e}")
        return "\n".join(parts)

    # ── 开场背景介绍（v3.5.13：玩家打开互动模式先知道"我是谁/在哪/要做什么"）──
    def generate_intro(self, novel_id: str, state: dict, force: bool = False) -> str:
        """生成/取缓存的故事背景介绍（v3.5.18: 500-700 字，覆盖世界观/人物/处境/目标）

        v2.5.63: intro 缓存绑定角色——state['intro_char'] != player_char.name 时
        强制重新生成（角色切换后旧视角开场白不得复用）
        """
        pc = state.get("player_char") or {}
        pc_name = pc.get("name", "")
        cached = state.get("intro")
        cached_char = state.get("intro_char") or ""
        # 缓存命中条件：有缓存 + 缓存属于当前扮演角色 + 非强制
        if cached and not force and cached_char == pc_name:
            return cached
        s = state.get("state", {})
        parts = []
        # v2.5.63: 你扮演的角色放最前 + 加粗强调（LLM 易被世界观简报带偏写成主角）
        if pc.get("name"):
            parts.append(f"【你扮演的角色（全文'你'必须指代 TA，最高优先级）】: {pc['name']}")
            if pc.get("identity"):
                parts.append(f"身份: {pc['identity']}")
            if pc.get("personality_brief"):
                parts.append(f"性格: {pc['personality_brief'][:120]}")
            if pc.get("speak_style"):
                parts.append(f"说话风格: {pc['speak_style'][:100]}")
            if pc.get("initial_attitude"):
                parts.append(f"处境/关系: {pc['initial_attitude'][:120]}")
            if pc.get("backstory"):
                parts.append(f"过往: {pc['backstory'][:150]}")
            if pc.get("motivation"):
                parts.append(f"动机: {pc['motivation'][:100]}")
            parts.append("注意：若该角色不是小说主角，主角只作为'主要人物'以第三人称提及，绝不与'你'混淆")
        parts.append(f"小说：《{state.get('title', '')}》（{state.get('genre', '')}·{state.get('style', '')}）")
        wb = state.get("worldbuilding_brief") or ""
        if wb:
            parts.append(f"世界观（时代/地点/势力/规则）：\n{wb[:600]}")
        # v3.5.18: 注入每个角色的人设档案（身份/性格/与主角关系）
        casts = state.get("casts") or {}
        player_name = pc.get("name", "")
        if casts:
            cast_lines = []
            for name, c in casts.items():
                if name == player_name:
                    continue
                prof = (c.get("profile") or {})
                brief = []
                if prof.get("identity"):
                    brief.append(f"身份:{str(prof['identity'])[:50]}")
                dna = prof.get("expression_dna") or []
                if dna:
                    d0 = dna[0]
                    brief.append(f"性格:{str(d0.get('name', d0))[:40] if isinstance(d0, dict) else str(d0)[:40]}")
                role = c.get("role", "")
                if role:
                    brief.append(f"定位:{role}")
                cast_lines.append(f"- {name}{'（' + '，'.join(brief) + '）' if brief else ''}")
            if cast_lines:
                parts.append("主要人物档案：\n" + "\n".join(cast_lines[:8]))
        if s.get("objective"):
            parts.append(f"主线目标：{s['objective'][:250]}")
        user = "\n".join(parts)
        intro = ""
        try:
            raw = self._llm(INTRO_SYSTEM, user, temperature=0.7, max_tokens=420)  # v3.5.33 精简版
            intro = (raw or "").strip()
            # v3.5.33: 超长时语义压缩（非截断——保留完整语义的精简版）
            if len(intro) > 400:
                try:
                    comp = self._llm(INTRO_COMPRESS_SYSTEM, intro, temperature=0.5, max_tokens=350)
                    if comp and 150 < len(comp.strip()) < len(intro):
                        intro = comp.strip()
                except Exception as e:
                    log.warning(f"intro compress failed: {e}")
            if len(intro) < 120:
                intro = ""
        except Exception as e:
            log.warning(f"intro 生成失败: {e}")
        if not intro:
            # 降级：模板拼接（保底有背景可看）
            name = pc.get("name", "你")
            lines = [f"你是{name}。"]
            if pc.get("identity"):
                lines.append(f"身份：{pc['identity']}。")
            if s.get("objective"):
                lines.append(f"你当前的目标：{s['objective'][:120]}。")
            if casts:
                lines.append(f"与你相关的人：{'、'.join(list(casts.keys())[:6])}。")
            if wb:
                lines.append(str(wb).replace("\n", " ")[:200])
            intro = "".join(lines)
        state["intro"] = intro
        state["intro_char"] = pc_name  # v2.5.63: 缓存绑定角色（角色切换后旧 intro 不可复用）
        try:
            self.store.save_state(novel_id, state)
        except Exception:
            pass
        return intro

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

        # v3.5.51: 存量脏 location 自愈（早期版本 LLM 输出 JSON 片段进字段）
        # v3.5.52: 补 state.state.location + clean_location 升级为 JSON 剥离
        try:
            _ps_old = state.get("player_state") or {}
            _loc_old = str(_ps_old.get("location", "") or "")
            if _loc_old and any(_c in _loc_old for _c in ('{', '[', ':', '"', 'null')):
                _ps_old["location"] = clean_location(_loc_old)
                state["player_state"] = _ps_old
                _cs_old = state.get("cast_states") or {}
                for _n, _c in _cs_old.items():
                    _cl = str(_c.get("location", "") or "")
                    if _cl and any(_c2 in _cl for _c2 in ('{', '[', ':', '"', 'null')):
                        _c["location"] = clean_location(_cl)
                state["cast_states"] = _cs_old
                # v3.5.52: 主状态 location（prompt 注入"当前地点"用）一并清洗
                _s_old = state.get("state") or {}
                _sl = str(_s_old.get("location", "") or "")
                if _sl and any(_c3 in _sl for _c3 in ('{', '[', ':', '"', 'null')):
                    _s_old["location"] = clean_location(_sl)
                    state["state"] = _s_old
                try:
                    self.store.save_state(novel_id, state)
                    log.info(f"存量脏 location 已清洗: {_loc_old[:30]} → {_ps_old['location'][:30]}")
                except Exception:
                    pass
        except Exception:
            pass

        # v3.5.53: 本章事件序列（beats）同步兜底——beats 只在切章时后台预生成，
        # 第 1 章（idx=0）从生成开始就没有 beats → 前 2-4 个场景无事件约束，
        # LLM 只能复述上一场景 → 重复剧情（实测：新存档场景2 复刻场景1）。
        # 场景生成前检查：beats 缺失/章节不符 → 同步生成（仅首次 +2~5s，之后走缓存）
        try:
            _op = state.get("outline_progress") or {}
            _oc = state.get("outline_chapters") or []
            if _oc:
                _ci = min(int(_op.get("idx", 0)), len(_oc) - 1)
                _cb = state.get("chapter_beats") or {}
                if not _cb.get("beats") or _cb.get("chapter_idx") != _ci:
                    self._ensure_chapter_beats(novel_id, state)
                    try:
                        self.store.save_state(novel_id, state)
                    except Exception:
                        pass
        except Exception as e:
            log.warning(f"beats sync ensure failed: {e}")

        # 快照（生成前备份）
        self.store.snapshot(novel_id)
        scene_num = state.get("scene_num", 0) + 1
        summary = state.get("summary", "")

        # v1.1 P2: 锚点条件检查（场景生成前，规则零 LLM）——触发则本场景
        # 以 entry_hook 开场（事件找上门），场景后推进锚点；未触发玩家自由
        try:
            self._check_anchor_trigger(state)
            self.store.save_state(novel_id, state)
        except Exception as e:
            log.warning(f"anchor trigger check failed: {e}")

        prompt = self._build_scene_prompt(state, summary)
        collected = []

        # v3.5.19: 阶段提示——生成前告知前端（显示"正在生成…"避免用户以为卡住）
        yield {"type": "phase", "label": "📖 正在展开剧情…"}
        yield {"type": "scene_chunk", "scene_num": scene_num, "content": ""}
        try:
            # v3.5.55: max_tokens 300（≈200-280字）——架构级硬上限（API 物理截断，
            # 不依赖 LLM 自觉）；配合落盘前长度校验双保险
            async for chunk in self._llm_stream(SCENE_SYSTEM, prompt, max_tokens=300):
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

        # v3.5.55: 架构级长度兜底——即使 max_tokens 截断/API 超发，
        # 落盘前按句子边界裁剪（只删尾部未完成内容，不切语义）
        # （v3.5.33 旧注释：不硬截断——已被 v3.5.55 架构强制替代，
        #   max_tokens 300 + 此处校验双保险，LLM 无法输出超长文本）
        if len(scene_text) > 330:
            _cut = scene_text[:330]
            _last_end = max(_cut.rfind("。"), _cut.rfind("！"), _cut.rfind("？"),
                            _cut.rfind("…"), _cut.rfind("”"), _cut.rfind("】"))
            if _last_end > 200:  # 只在有完整句号边界时裁剪，避免切掉关键收尾
                scene_text = _cut[:_last_end + 1]

        # v2.5.55: 场景重复检测（规则兜底，零 LLM）——与最近 2 个旧场景比对，
        # 命中 → 标记 scene_repeat（下一场景 prompt 强制全新事件）
        try:
            _recent_old = [s.get("scene_text", "") for s in self.store.recent_scenes(novel_id, 2)]
            if scene_repeat_check(scene_text, _recent_old, 0.35):
                state["scene_repeat"] = True
                log.warning(f"场景{scene_num} 与最近场景重复（规则兜底触发，下一场景强制全新）")
            else:
                state.pop("scene_repeat", None)
            self.store.save_state(novel_id, state)
        except Exception as e:
            log.warning(f"scene repeat check failed: {e}")

        # 解析 + 持久化
        blocks = parse_scene_markup(scene_text)
        # v3.5.18/v3.5.27: 过滤玩家角色的自动台词（通用函数，场景/对话/行动结果共用）
        player_name = (state.get("player_char") or {}).get("name", "")
        blocks = _clean_player_dialogue(blocks, player_name)
        scene_record = {
            "scene_num": scene_num,
            "scene_text": scene_text,
            "blocks": blocks,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.store.append_scene(novel_id, scene_record)
        # v3.5.42: 进度恢复——场景 blocks 同步入 state（前端切走再切回时
        # 从 state.recent_blocks 恢复完整剧情，不再空白/对不上）
        try:
            rb = state.get("recent_blocks") or []
            rb.extend(blocks)
            if len(rb) > 260:
                rb = rb[-260:]
            state["recent_blocks"] = rb
            self.store.save_state(novel_id, state)
        except Exception as e:
            log.warning(f"recent_blocks save failed: {e}")
        # v3.5.28: 大纲章节推进——每章约 3 个场景后切下一章，目标随章更新
        try:
            self._advance_outline(novel_id, state)
        except Exception as e:
            log.warning(f"advance_outline failed: {e}")
        # v3.5.49: 事件序列推进——本场景已演完，当前事件标记 done，下一事件置 current
        try:
            self._advance_beat(state)
            self.store.save_state(novel_id, state)
        except Exception as e:
            log.warning(f"advance_beat failed: {e}")
        # v1.1 锚点式 P0: 张力更新（规则版）——玩家执行了行动但未触发锚点
        # （beat 未全部推进）→ 视为偏离，张力 +2；否则中性 +1
        # P2 引入条件检查器后改为精确偏离判定
        try:
            _t_mode = "drift" if (state.get("last_action") or {}).get("summary") else "neutral"
            self._update_tension(state, _t_mode)
            self.store.save_state(novel_id, state)
        except Exception as e:
            log.warning(f"tension update failed: {e}")
        # v3.5.22: 复用小说模式逻辑引擎（后台，不阻塞场景流）——
        # 角色状态更新 + L1 矛盾检查（v3.5.47: 走串行队列，不与主流程抢 LLM）
        try:
            enqueue_background(self._post_scene_logic_check, novel_id, scene_num, scene_text, blocks)
        except Exception:
            pass

        # 更新状态：场景号、摘要、最近场景
        state["scene_num"] = scene_num
        # v3.5.21: 前情摘要改"开头+结尾"双段——开头交代情境、结尾保留空间/人物状态
        # （原只取开头 300 字：角色"出门/离开"发生在场景结尾时会被截断丢失，
        #  下一场景不知情 → 出现"已出门又回到椅子上"的空间矛盾）
        _head = scene_text[:150].strip()
        _tail = scene_text[-260:].strip()
        _summary = (_head + "……" + _tail) if len(scene_text) > 420 else scene_text[:300]
        state["summary"] = _summary
        recent = state.get("recent_scenes", [])
        recent.append(_summary)
        state["recent_scenes"] = recent[-3:]
        # v3.3.1: missing hooks 只影响本段场景，用后即清（软约束不过期悬挂）
        state.pop("pending_missing_hooks", None)
        # 在场角色（从台词块提取）
        speakers = {b["speaker"] for b in blocks if b["type"] == "dialogue"}
        casts = state.get("casts", {})
        state["casts"] = {k: casts.get(k, {"present": True}) for k in casts}
        # v3.5.41: 新角色标记 temp（临时登场，不自动转正）；本场景出场角色 present=True
        for sp in speakers:
            if sp and sp not in casts:
                casts[sp] = {"present": True, "profile": {}, "temp": True}
        for name, c in casts.items():
            if name in speakers:
                c["present"] = True
        # v3.5.41: temp 角色连续 3 个场景未出场自动清理（防 casts 膨胀导致乱入合法化）
        ps = state.get("player_state") or {}
        with_chars = set(ps.get("with") or [])
        for name in list(casts.keys()):
            c = casts.get(name) or {}
            if c.get("temp") and name not in speakers and name not in with_chars:
                c["absent_count"] = int(c.get("absent_count", 0)) + 1
                if c.get("absent_count", 0) >= 3:
                    casts.pop(name, None)
            elif c.get("temp"):
                c["absent_count"] = 0
        state["casts"] = casts
        # v3.5.46: 出场角色同步 cast_states.present=True（行为驱动闭环——
        # 只要在场景里说了话就视为在场，与 LLM 标记一致，防止前后状态打架）
        _cs = state.get("cast_states") or {}
        _my_loc = clean_location(ps.get("location") or "")
        for sp in speakers:
            if not sp:
                continue
            if sp in _cs:
                _cs[sp]["present"] = True
                if not _cs[sp].get("location") and _my_loc:
                    _cs[sp]["location"] = _my_loc
            else:
                # v3.5.51: 说了话但状态卡缺失（后台提取失败/队列丢弃）→
                # 兜底建条目，防 LLM 对无状态角色自由发挥
                _cs[sp] = {"present": True, "location": _my_loc or "",
                           "mood": "", "stance": "", "knows": [],
                           "condition": "健康", "agenda": ""}
        state["cast_states"] = _cs
        self.store.save_state(novel_id, state)

        yield {"type": "scene_end", "scene_num": scene_num, "blocks": blocks,
               "snapshot": _state_snapshot(state)}

        # ── 节点判定（三层保障的 ① 规则预筛 + ② LLM 精判）──
        if force_node_check:
            # v3.5.19: 节点判定/议程生成可能耗时 3-8s——先提示用户
            yield {"type": "phase", "label": "🤔 正在判断剧情走向…"}
            is_node, node_chars, rounds, reason = self._decide_node(novel_id, scene_num, blocks, state)
            # v3.5.16: 对话候选排除玩家自己（玩家是主角，只跟 NPC 对话）
            player_name = (state.get("player_char") or {}).get("name", "")
            if player_name and player_name in node_chars:
                node_chars = [c for c in node_chars if c != player_name]
            state = self.store.load_state(novel_id)
            state["pending_node"] = is_node
            state["node_chars"] = node_chars
            state["node_rounds"] = rounds
            # v3.5.35: 停顿理由存入 state——前端展示"为什么停、要做什么"，
            # 玩家不再不知所措
            state["pending_reason"] = str(reason or "")[:80]
            agenda = None
            if is_node:
                # v3.3: Agenda 机制——对话前生成议程（目标/推进开关/边界），对话围绕它推进
                yield {"type": "phase", "label": "📋 正在安排这场对话…"}
                agenda = self._generate_agenda(novel_id, node_chars, state)
                state["agenda"] = agenda
                # v3.5.40: 建议选项（Galgame 式真两难）——玩家不知所措时一键可答
                try:
                    state["suggestions"] = self._generate_suggestions(novel_id, state, node_chars)
                except Exception as e:
                    log.warning(f"suggestions failed: {e}")
                    state["suggestions"] = []
            self.store.save_state(novel_id, state)
            yield {
                "type": "node_check",
                "is_node": is_node,
                "chars": node_chars,
                "suggested_rounds": rounds,
                "reason": reason,
                "agenda": agenda,   # 前端可展示"这场对话要谈什么"
                "suggestions": state.get("suggestions", []),  # v3.5.40
                "snapshot": _state_snapshot(state),
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
        # v3.5.17: 场景纯旁白（v3.5.12 后"你"视角开场可能无 NPC 台词）→
        # 用在场角色兜底，保证开场节点必有对话对象
        if not chars:
            player_name = (state.get("player_char") or {}).get("name", "")
            chars = [n for n in (state.get("casts") or {}) if n != player_name][:3]
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
                return True, chars, 4, "剧情已推进一段，给你一个说话的机会（不想说可直接继续剧情）"
        # 规则 3：强冲突事件（真正需要玩家抉择的时刻）
        text = " ".join(b["content"] for b in blocks)
        strong_kw = ["拔剑", "刀架", "生死", "追杀", "真相大白", "身份暴露", "决裂", "挟持",
                     "下跪", "自尽", "灭口", "当场", "对质", "摊牌", "交易达成", "背叛"]
        hit = next((k for k in strong_kw if k in text), "")
        if hit:
            return True, chars, 5, f"重大事件: {hit}"
        # v3.5.33: 纯叙事场景（无台词/无强冲突）跳过 LLM 精判——规则 2（连续 3 段
        # 无对话强制）已保底互动频率，省每次 3-8s；玩家「我要说话」随时可介入
        has_dialogue = any(b.get("type") == "dialogue" for b in blocks)
        if not has_dialogue:
            return False, chars, 0, "纯叙事场景，规则 2 保底即可"
        # LLM 精判（规则未命中且有台词才调用）
        result = self._llm_judge_node(text, chars, state)
        if result is None:
            return False, chars, 2, "判定失败，默认不触发（玩家可主动介入）"
        is_node = bool(result.get("is_node"))
        confidence = float(result.get("confidence", 0.5))
        # v3.5.20: 阈值 0.55（过滤一般性发问型场景——"你怎么看"类提问置信度
        # 通常中等，不再触发节点）；刚对话过（<2 段）且置信度一般时抑制
        last_chat_gap = scene_num - state.get("_last_chat_scene", 0)
        if is_node:
            if confidence < 0.55:
                return False, chars, 0, f"置信度不足({confidence:.1f})"
            if confidence < 0.7 and last_chat_gap < 2:
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

    # ── v3.5.40: 建议选项（Galgame 式）──
    def _generate_suggestions(self, novel_id: str, state: dict, chars: list) -> list:
        """节点停顿时生成 2-3 个建议回应——真两难（有分量），非敷衍选项（v2.5.59 走模块级）"""
        return generate_suggestions(self.client, self.model, self.store, state, chars)

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
        # v3.5.48: 注入已有 facts——LLM 知道哪些事件已记录，防止同一件事反复提取
        try:
            _prev = self.store.load_state(novel_id) or {}
            _pf = [str(f.get("content", ""))[:60] for f in (_prev.get("facts") or [])[-8:]]
            if _pf:
                user += "\n\n已有事实（不得重复提取同一事件）:\n- " + "\n- ".join(_pf)
        except Exception:
            pass
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
        # v3.5.49: 目标仲裁——对话结论不再覆盖主线 objective（主线=大纲章节目标/
        # 事件序列），降级为 chat_conclusion 注入下一场景强制承接。
        # 玩家方向是分支张力，不是主线；主线被闲聊绑架是"剧情无法按大纲推进"的根因之一
        if result.get("objective_update"):
            state["chat_conclusion"] = str(result["objective_update"])[:150]
        # v3.5.9: 对话沉淀为角色记忆——PACT facts 同步进目标角色的专属记忆
        from .char_memory import add_event, add_memory
        for f in state.get("facts", []):
            target = f.get("target") or ""
            if not target or target == "player":
                continue
            tag = {"promise": "承诺", "threat": "威胁", "request": "请求",
                   "secret": "秘密", "info": "告知", "break": "违约"}.get(
                str(f.get("type", "")), "约定")
            add_memory(state, target, "promise",
                       f"读者{tag}了你：{f.get('content', '')}",
                       source="pact")
        # v2.5.57: 承诺台账同步——时间锚定承诺进 pending_promises（周五变周三 bug 防护）
        # 覆盖两类：读者承诺（target=角色） + 角色邀约（target=player，who=subject）
        try:
            _act_sum = str((state.get("last_action") or {}).get("summary", "") or "")
            promise_ledger_update(state, state.get("facts", []), action_summary=_act_sum)
        except Exception as e:
            log.warning(f"promise ledger failed: {e}")
        # 关系变化 → 事件时间线
        if result.get("relations"):
            rel_changed = [f"{k} ♥{v}" for k, v in result.get("relations", {}).items()
                           if isinstance(k, str) and "-" not in k and k in (state.get("casts") or {})]
            if rel_changed:
                add_event(state, "关系变化: " + "、".join(rel_changed[:3]), "relation")
        state["_last_chat_scene"] = state.get("scene_num", 0)
        self.store.save_state(novel_id, state)
        return result

    # ── 目标锚定 + 回扣验证（并入节点判定，不单独调用）──
    # 说明：目标锚定通过 _build_scene_prompt 注入 objective + facts 硬约束，
    # 回扣验证合并进下一次 _decide_node 的规则 2（连续 2 段无对话强制节点），
    # 以及状态校验器（每 3 段由前端触发 scene 时附带 summary 比对，v1 简化）。

    # ── 工具 ──
    def _load_plan(self, novel_id: str) -> dict:
        """读取 plan.json（v2.5.62 角色预设构建用）——失败返回 {}"""
        try:
            import os
            from config import NOVELS_DIR
            plan_path = os.path.join(NOVELS_DIR, novel_id, "plan.json")
            if os.path.exists(plan_path):
                with open(plan_path, "r", encoding="utf-8") as f:
                    return json.load(f) or {}
        except Exception as e:
            log.warning(f"_load_plan failed: {type(e).__name__}: {str(e)[:80]}")
        return {}

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

    def _ensure_chapter_beats(self, novel_id: str, state: dict, force: bool = False) -> list:
        """v3.5.49: 章节事件序列（beats）——把本章目标拆成 N 个事件，每场景推进 1 个。

        切章时生成（同步，一次 LLM 调用）。事件与场景一一对应，场景 prompt 注入
        "本章事件进度"，已完成事件严禁重演 → 结构性杜绝重复生成。
        v3.5.54: 优先消费大纲 scene_beats（Galgame 节点图——大纲生成时就规划好的
        关键节点），LLM 拆解仅作兜底；节点数=每章场景数（剧情按节点收束）。
        返回 beats 列表（未就绪返回空，调用方用章节目标兜底）。
        """
        op = state.get("outline_progress") or {}
        idx = int(op.get("idx", 0))
        chs = state.get("outline_chapters") or []
        if not chs:
            return []
        ch = chs[min(idx, len(chs) - 1)]
        cb = state.get("chapter_beats") or {}
        if (not force and cb.get("chapter_idx") == idx and cb.get("beats")):
            return cb["beats"]
        # v3.5.54: 大纲节点优先（Galgame 关键节点——剧情收束的锚点）
        _sb = ch.get("scene_beats") or []
        if _sb and any(str(b.get("key_action", "")).strip() for b in _sb if isinstance(b, dict)):
            beats = []
            for _i, _b in enumerate(_sb):
                if not isinstance(_b, dict):
                    continue
                _act = str(_b.get("key_action", "")).strip()[:60]
                _name = str(_b.get("name", "")).strip()[:20]
                _trg = _b.get("trigger")
                beats.append({
                    "id": int(_b.get("beat", _i + 1)),
                    "desc": f"{_name}：{_act}" if _name else _act,
                    "status": "pending",
                    # v1.1 P2: 锚点触发字段（条件检查器消费）
                    "trigger": (_trg if isinstance(_trg, dict) else None) or {},
                    "entry_hook": str(_b.get("entry_hook", "") or _act)[:120],
                })
            if beats:
                beats[0]["status"] = "current"
                state["chapter_beats"] = {"chapter_idx": idx, "beats": beats}
                try:
                    self.store.save_state(novel_id, state)
                except Exception:
                    pass
                log.info(f"Galgame 节点 beats: 第{ch.get('number', idx + 1)}章 {len(beats)} 节点")
                return beats
        tw = int(ch.get("target_words", 0) or 0)
        n = 4 if tw >= 5000 else (2 if 0 < tw < 2500 else 3)
        n = max(2, min(n, 4))
        try:
            user = (f"本章目标: 第{ch.get('number', idx + 1)}章《{ch.get('title', '')}》"
                    f"—— {ch.get('summary', '')}\n"
                    f"请拆解为 {n} 个事件节点。")
            raw = self._llm(BEAT_SYSTEM, user, temperature=0.4, max_tokens=500)
            import re as _re3
            m = _re3.search(r"\{.*\}", raw or "", _re3.S)
            data = json.loads(m.group(0)) if m else {}
            beats = [{"id": int(b.get("id", i + 1)), "desc": str(b.get("desc", ""))[:60],
                      "status": "pending",
                      # v1.1 P2: 拆解路径补默认 trigger（timeout 兜底防死锁）
                      "trigger": {"type": "event", "conditions": [], "timeout_scenes": 3},
                      "entry_hook": ""} for i, b in enumerate((data.get("beats") or [])[:n])]
            if len(beats) < 2:  # 拆解失败兜底：目标本身就是事件
                beats = [{"id": 1, "desc": str(ch.get("summary", ""))[:60], "status": "pending",
                          "trigger": {"type": "event", "conditions": [], "timeout_scenes": 3},
                          "entry_hook": ""}]
        except Exception as e:
            log.warning(f"beats extract failed: {type(e).__name__}: {str(e)[:80]}")
            beats = [{"id": 1, "desc": str(ch.get("summary", ""))[:60], "status": "pending",
                      "trigger": {"type": "event", "conditions": [], "timeout_scenes": 3},
                      "entry_hook": ""}]
        if beats:
            beats[0]["status"] = "current"
            state["chapter_beats"] = {"chapter_idx": idx, "beats": beats}
            try:
                self.store.save_state(novel_id, state)
            except Exception:
                pass
        return beats

    def _check_anchor_trigger(self, state: dict) -> Optional[dict]:
        """v1.1 P2: 锚点条件检查（规则，零 LLM）——场景生成前调用。

        触发（条件满足/timeout 兜底）→ 记录 state["anchor_triggered"]
        （场景 prompt 注入 entry_hook，场景后 _advance_beat 推进锚点）
        未触发 → 清除标记（玩家自由，张力继续累积）
        """
        try:
            trig = anchor_trigger_check(state)
            if trig:
                state["anchor_triggered"] = trig
                log.info(f"锚点触发: beat#{trig.get('beat_id')} reason={trig.get('reason')}")
            else:
                state.pop("anchor_triggered", None)
            return trig
        except Exception as e:
            log.warning(f"check_anchor_trigger failed: {e}")
            state.pop("anchor_triggered", None)
            return None

    def _advance_beat(self, state: dict):
        """v1.1 P2: 锚点推进（替代 v3.5.49 无条件每场景推进 1 个 beat）。

        仅当本场景触发了锚点（anchor_triggered）才推进 current → done；
        未触发 → 不推进（玩家自由区，锚点保持 current，张力累积牵引）。
        """
        try:
            trig = state.get("anchor_triggered")
            if not trig:
                return
            cb = state.get("chapter_beats") or {}
            beats = cb.get("beats") or []
            if not beats:
                return
            cur_i = next((i for i, b in enumerate(beats) if b.get("status") == "current"), None)
            if cur_i is None:
                return
            beats[cur_i]["status"] = "done"
            # 触发结果写入（state_output：flags/relations/inventory——锚点产出）
            _trigger = beats[cur_i].get("trigger") or {}
            _so = _trigger.get("state_output") or {}
            _s = state.setdefault("state", {})
            for _f in (_so.get("flags") or []):
                _fl = _s.setdefault("flags", [])
                if str(_f) not in _fl and len(_fl) < 20:
                    _fl.append(str(_f))
                    append_change(state, {"field": "flag", "new": str(_f)}, "anchor:state_output")
            for _k, _v in (_so.get("relations") or {}).items():
                _rel = _s.setdefault("relations", {})
                _rel[str(_k)[:30]] = max(0, min(100, int(_v))) if isinstance(_v, (int, float)) else str(_v)[:60]
                append_change(state, {"field": f"relation.{_k}", "new": str(_v)[:60]}, "anchor:state_output")
            if cur_i + 1 < len(beats):
                beats[cur_i + 1]["status"] = "current"
            # v2.5.60: 锚点完成写入事件时间线——已完成的关键节点进 LLM 显式记忆，
            # 防"已完成的剧情又触发一遍"（事件记忆窗口已扩容至 30）
            try:
                from .char_memory import add_event
                add_event(state, f"本章节点完成: {str(beats[cur_i].get('desc', ''))[:50]}", "beat")
            except Exception:
                pass
            state.pop("anchor_triggered", None)
        except Exception as e:
            log.warning(f"advance_beat failed: {e}")

    def _update_tension(self, state: dict, mode: str = "neutral") -> int:
        """v1.1 锚点式 P0: 场景后更新张力值（规则版，零 LLM）。

        mode: drift=偏离 / neutral=中性 / progress=推进
        写回 state["tension"]；张力跨章不清零（主线偏离度累积，P4 用）
        """
        try:
            cur = int(state.get("tension", 0) or 0)
            state["tension"] = tension_update(cur, mode)
            return state["tension"]
        except Exception as e:
            log.warning(f"update_tension failed: {e}")
            return int(state.get("tension", 0) or 0)

    def _adapt_outline(self, novel_id: str, state: dict) -> Optional[dict]:
        """v1.1 P4: 动态大纲微调（保险②）——下一章锚点与玩家现状冲突时
        目标等价替换（终点不变，达成工具可换：信物毁→副本线索）。

        触发时机：切章时（mainline 落后或跨章偏离 ≥1 章）。
        成本：一次 LLM 调用。LLM 失败/合适 → 返回 None（保持原大纲）。
        """
        try:
            chs = state.get("outline_chapters") or []
            op = state.get("outline_progress") or {}
            idx = int(op.get("idx", 0))
            if idx + 1 >= len(chs):
                return None
            nch = chs[idx + 1]
            ps = state.get("player_state") or {}
            s = state.get("state") or {}
            _beats_json = json.dumps(
                nch.get("scene_beats", [])[:3], ensure_ascii=False)[:800]
            user = (
                f"下一章: 第{nch.get('number', idx + 2)}章《{nch.get('title', '')}》\n"
                f"下一章锚点(前3): {_beats_json or '（无）'}\n\n"
                f"玩家现状: 位置[{ps.get('location', '')}] 处境[{ps.get('situation', '')}]\n"
                f"已有关键标记: {('、'.join((s.get('flags') or [])[-5:])) or '（无）'}\n"
                f"最近行动: {(state.get('last_action') or {}).get('summary', '')}\n"
                f"主线健康度: {json.dumps(state.get('mainline', {}), ensure_ascii=False)[:300]}\n\n"
                f"这些锚点是否仍与玩家现状相容？不相容 → 给出目标等价的替换锚点。"
            )
            raw = self._llm(ADAPT_OUTLINE_SYSTEM, user, temperature=0.3, max_tokens=1200)
            if not raw:
                return None
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                return None
            data = json.loads(raw[start:end + 1])
            if not isinstance(data, dict):
                return None
            repl = data.get("replacement")
            if isinstance(repl, list) and repl and data.get("appropriate") is False:
                log.info(f"动态大纲微调: {str(data.get('reason', ''))[:80]}")
                return {"appropriate": False, "replacement": repl}
            return {"appropriate": True, "replacement": None}
        except Exception as e:
            log.warning(f"adapt_outline failed: {type(e).__name__}: {str(e)[:80]}")
            return None

    def _advance_outline(self, novel_id: str, state: dict):
        """v3.5.28: 大纲章节推进——场景数达阈值切下一章，objective 随章更新

        每章约 3 个场景（场景数/章按章节 target_words 微调：<2500 字 2 场景，>=5000 字 4 场景）
        v3.5.29: 切章时后台把本章互动剧情沉淀为小说章节正文（互动→章节回流）
        v3.5.48: 修复切章卡死——nch 未定义 NameError + final_done 误标记导致
        剧情永远停在第 1 章目标，一件事反复生成；增加场景号自愈（旧存档自动校准）
        """
        chs = state.get("outline_chapters") or []
        if not chs:
            return
        op = dict(state.get("outline_progress") or {})
        idx = int(op.get("idx", 0))
        # v3.5.30: 最后一章已回流过（final_done）→ 不再触发
        if op.get("final_done"):
            return
        sn = int(state.get("scene_num") or 0)
        ss = int(op.get("scene_start") or 1)
        # v3.5.48: 用场景号差校准章节内计数——即使状态保存失败/旧存档卡死
        # （scene_in_chapter 停滞），也能按实际已玩场景数推进，自动自愈
        cnt = max(int(op.get("scene_in_chapter", 0)) + 1, sn - ss + 1)
        ch = chs[min(idx, len(chs) - 1)]
        tw = int(ch.get("target_words", 0) or 0)
        # v1.1 锚点式 P0: 切章判定——锚点完成度优先（beats 全 done），
        # 场景数仅作无 beats 旧存档的兜底（消除 5 节点 vs 3 场景矛盾）
        _sb = ch.get("scene_beats") or []
        if _sb and any(str(b.get("key_action", "")).strip() for b in _sb if isinstance(b, dict)):
            per = max(2, min(len(_sb), 6))
        else:
            per = 4 if tw >= 5000 else (2 if 0 < tw < 2500 else 3)
        # v1.1: 锚点式切章判定（有 beats 时以全 done 为准，场景数不再硬性提前切章）
        _do_cut = chapter_complete(state, per)
        # v1.1 P3: 切章时状态一致性修复（L3 规则修复，零 LLM）——先修复再切章
        if _do_cut:
            try:
                consistency_repair(state)
            except Exception as e:
                log.warning(f"consistency repair failed: {e}")
        # v1.1 P4: 主线健康度对账（保险④）——进度落后 → 捷径标记（下一场景注入）
        if _do_cut:
            try:
                _mc = mainline_check(state)
                if _mc.get("shortcut"):
                    state["mainline_shortcut"] = True
                    log.info(f"主线健康度落后 gap={_mc.get('gap')}，注入捷径")
                else:
                    state.pop("mainline_shortcut", None)
                # v1.1 P4: 动态大纲微调——玩家现状与下一章锚点冲突时替换（终点不变）
                if _mc.get("shortcut") or int(state.get("tension_drift_chapters", 0) or 0) >= 1:
                    _adapt = self._adapt_outline(novel_id, state)
                    if _adapt and _adapt.get("appropriate") is False and _adapt.get("replacement"):
                        _nch = (state.get("outline_chapters") or [])[int((state.get("outline_progress") or {}).get("idx", 0)) + 1] \
                            if len(state.get("outline_chapters") or []) > int((state.get("outline_progress") or {}).get("idx", 0)) + 1 else None
                        if _nch:
                            _nch["scene_beats"] = _adapt["replacement"]
                            log.info(f"动态大纲微调: 第{_nch.get('number', '?')}章锚点已替换")
            except Exception as e:
                log.warning(f"mainline/adapt failed: {e}")
        # 跨章张力记录（v1.1 保险③）：切章时张力不清零，高位偏离跨章累积
        if _do_cut and int(state.get("tension", 0) or 0) >= 3:
            state["tension_drift_chapters"] = int(state.get("tension_drift_chapters", 0) or 0) + 1
        # v3.5.30: 最后一章也回流（原条件 idx < len-1 导致最后一章永远不生成章节正文）
        if _do_cut:
            # ── v3.6.6: 本章完成——不再自动回流章节正文（互动与正式写作分离）。
            # 互动剧情保留在互动存档（recent_scenes），需要成书时由
            # 「导出为小说」功能手动触发（写独立目录，不碰正式 chapters/）。
            done_idx = idx
            scene_start = int(op.get("scene_start", 1) or 1)
            if idx < len(chs) - 1:
                idx += 1
                cnt = 0
                # v3.5.48: 修复——nch 从未定义（NameError 导致切章状态保存被跳过）
                _nch = chs[min(idx, len(chs) - 1)]
                log.info(f"Outline advanced → 第{_nch.get('number', idx + 1)}章《{_nch.get('title', '')}》")
                # v3.5.49: 后台预生成新章事件序列（beats）——下个场景生成时
                # 注入"本章事件进度"，每场景推进 1 个事件，杜绝重复生成
                try:
                    _st_new = self.store.load_state(novel_id) or state
                    enqueue_background(self._ensure_chapter_beats, novel_id, _st_new, True, critical=True)
                except Exception as e:
                    log.warning(f"beats pregen failed: {e}")
                # v3.5.48: 非最后一章不设 final_done（否则下一场景直接 return 再次卡死）
                state["outline_progress"] = {"idx": idx, "scene_in_chapter": cnt,
                                             "scene_start": state.get("scene_num", 0) or 0}
            else:
                # 最后一章完成：保持 idx 不变，标记 final_done 防重复回流
                state["outline_progress"] = {"idx": idx, "scene_in_chapter": cnt,
                                             "scene_start": state.get("scene_num", 0) or 0,
                                             "final_done": True}
        else:
            state["outline_progress"] = {"idx": idx, "scene_in_chapter": cnt,
                                         "scene_start": op.get("scene_start", 1) or 1}
        try:
            self.store.save_state(novel_id, state)
        except Exception as e:
            log.warning(f"advance_outline save failed: {e}")

    def _sync_chapter_from_interactive(self, novel_id: str, chapter_idx: int,
                                       scene_start: int, scene_end: int):
        """v3.5.29: 互动→章节回流——把一章的互动场景 + 玩家行动整合为正式章节正文

        后台线程执行（不阻塞场景流）。场景文本（第二人称"你"）→ 章节正文
        （第三人称主角名），玩家的选择与行动必须体现在正文中。
        v2.5.61: 幂等重构——synced_chapters 区间标记防重复回流；
        回流写入同步 global_state.json（timeline/chapters_summary），小说体系可感知。
        """
        try:
            # ── v2.5.61: 幂等判定——该章该场景区间已回流过 → 跳过（防重复生成）──
            _cur = self.store.load_state(novel_id) or {}
            chs = _cur.get("outline_chapters") or []
            if chapter_idx >= len(chs):
                return
            ch = chs[chapter_idx]
            ch_num = int(ch.get("number", chapter_idx + 1))
            if sync_skip_check(_cur, ch_num, scene_start, scene_end):
                log.info(f"Chapter {ch_num} already synced [{scene_start}-{scene_end}], skip")
                return
            # 收集本章场景
            scenes = []
            for rec in self.store.recent_scenes(novel_id, 200):
                sn = int(rec.get("scene_num", 0) or 0)
                if scene_start <= sn <= max(scene_end, scene_start):
                    scenes.append((sn, rec.get("scene_text", "")))
            scenes.sort()
            if not scenes:
                return
            # 收集玩家行动/对话
            player_acts = []
            try:
                for h in self.store.recent_chats(novel_id, 200):
                    if h.get("role") == "user" and h.get("content"):
                        player_acts.append(str(h.get("content"))[:100])
            except Exception:
                pass
            scene_text = "\n\n".join(f"[场景{sn}]\n{t}" for sn, t in scenes)
            user = (
                f"## 本章大纲摘要（骨架）\n第{ch_num}章《{ch.get('title', '')}》"
                f"（{ch.get('volume', '')}）\n{ch.get('summary', '')}\n\n"
                f"## 互动场景记录（本玩家真实经历，含其选择与行动）\n{scene_text[:6000]}\n\n"
                + (f"## 玩家在互动中的行动/对话（必须体现在正文）\n"
                   + "\n".join(f"- {a}" for a in player_acts[-8:]) if player_acts else "")
                + f"\n\n请把以上内容整理成正式章节正文（{max(800, int(ch.get('target_words', 1500) or 1500))} 字左右）。"
            )
            raw = self._llm(INTERACTIVE_TO_CHAPTER_SYSTEM, user, temperature=0.7, max_tokens=4000)
            body = (raw or "").strip()
            if len(body) < 200:
                return
            # v3.5.29: LLM 输出可能自带章节标题（## 第X章），剥掉避免与文件头重复
            import re as _re
            body = _re.sub(r"^#{1,3}\s*第?\s*[一二三四五六七八九十百千零两\d]+\s*章.*?\n+", "", body, count=1)
            # 写入 chapters/
            import os
            from config import NOVELS_DIR
            ch_dir = os.path.join(NOVELS_DIR, novel_id, "chapters")
            os.makedirs(ch_dir, exist_ok=True)
            fname = f"chapter_{ch_num:04d}.md"
            fpath = os.path.join(ch_dir, fname)
            if os.path.exists(fpath):
                os.replace(fpath, fpath + f".bak_{int(time.time())}")  # 保底备份
            content = f"# 第{ch_num}章 {ch.get('title', '')}\n\n{body}\n"
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            # 同步根 state 进度
            gs_path = os.path.join(NOVELS_DIR, novel_id, "state.json")
            if os.path.exists(gs_path):
                try:
                    with open(gs_path, "r", encoding="utf-8") as f:
                        gs = json.load(f)
                    gs["current_chapter"] = ch_num
                    if ch_num not in (gs.get("completed_chapters") or []):
                        gs.setdefault("completed_chapters", []).append(ch_num)
                    gs["total_words"] = int(gs.get("total_words", 0) or 0) + len(body)
                    gs.setdefault("summaries", {})[str(ch_num)] = body[:120]
                    with open(gs_path, "w", encoding="utf-8") as f:
                        json.dump(gs, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    log.warning(f"gs update failed: {e}")
            # ── v2.5.61: global_state.json 同步（timeline/chapters_summary——正式小说体系感知互动进度）──
            try:
                _gs_path = os.path.join(NOVELS_DIR, novel_id, "global_state.json")
                if os.path.exists(_gs_path):
                    with open(_gs_path, "r", encoding="utf-8") as f:
                        _gs = json.load(f)
                    _summary = body[:150].replace("\n", " ")
                    gs_merge_sync(_gs, ch_num, ch.get("title", ""), body, _summary)
                    with open(_gs_path, "w", encoding="utf-8") as f:
                        json.dump(_gs, f, ensure_ascii=False, indent=2)
            except Exception as e:
                log.warning(f"global_state sync failed: {e}")
            # ── v2.5.61: 幂等标记——本区间已回流 ──
            try:
                sync_mark_record(_cur, ch_num, scene_start, max(scene_end, scene_start))
                self.store.save_state(novel_id, _cur)
            except Exception as e:
                log.warning(f"sync mark failed: {e}")
            log.info(f"Chapter {ch_num} synced from interactive ({len(scenes)} scenes, {len(body)} chars)")
        except Exception as e:
            log.warning(f"_sync_chapter_from_interactive failed: {type(e).__name__}: {str(e)[:100]}")
        # v3.5.31: 章节结束 → 滚动压缩角色记忆（后台，LLM 提炼旧记忆为长期摘要）
        try:
            from .char_memory import compress_all_memories
            _st = self.store.load_state(novel_id)
            if _st and compress_all_memories(_st, self._llm):
                self.store.save_state(novel_id, _st)
                log.info("Memories compressed after chapter sync")
        except Exception as e:
            log.warning(f"memory compress failed: {type(e).__name__}: {str(e)[:80]}")

    # ── v3.6.6: 互动模式 → 导出为小说（互动与正式写作分离后的唯一成书通道）──
    def export_novel_from_interactive(self, novel_id: str, progress_cb=None) -> dict:
        """把互动模式的全部剧情导出为独立小说（手动触发，写独立目录）。

        设计原则（v3.6.6）：
        - 互动与正式写作彻底分离：导出文件写入 novels/<id>/interactive_export/export_<ts>/，
          不碰正式 chapters/、state.json、global_state.json（互动是平行世界，导出是快照）
        - 分章：每 2 个互动场景为一章（互动单场景 300-600 字 → 每章约 1200 字）；
          章节标题优先复用大纲章节标题，场景不够则只导出有内容的章
        - 视角转换：第二人称"你" → 第三人称主角名（复用 INTERACTIVE_TO_CHAPTER_SYSTEM）

        Args:
            novel_id: 小说 id
            progress_cb: 可选回调 progress_cb(idx, total, ch_title, chars) 供 SSE 推进度
        Returns:
            {"chapters": int, "total_chars": int, "dir_path": str, "novel_md": str}
        """
        import os as _os
        from config import NOVELS_DIR
        st = self.store.load_state(novel_id) or {}
        scenes = self.store.recent_scenes(novel_id, 500) or []
        scenes = [s for s in scenes if s.get("scene_text")]
        scenes.sort(key=lambda s: int(s.get("scene_num", 0) or 0))
        if not scenes:
            return {"chapters": 0, "total_chars": 0, "dir_path": "", "novel_md": ""}
        # 玩家行动（对话记录里的用户输入）
        player_acts = []
        try:
            for h in self.store.recent_chats(novel_id, 500) or []:
                if h.get("role") == "user" and h.get("content"):
                    player_acts.append(str(h.get("content"))[:100])
        except Exception:
            pass
        acts_brief = "\n".join(f"- {a}" for a in player_acts[-12:]) if player_acts else ""
        # 大纲章节（标题复用）
        chs = st.get("outline_chapters") or []
        # 分章：每 2 场景一组
        per_chapter = 2
        groups = [scenes[i:i + per_chapter] for i in range(0, len(scenes), per_chapter)]
        # 导出目录
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_dir = _os.path.join(NOVELS_DIR, novel_id, "interactive_export", f"export_{ts}")
        _os.makedirs(out_dir, exist_ok=True)
        # 每章正文
        bodies = []
        total_chars = 0
        for gi, group in enumerate(groups):
            ch_title = ""
            if gi < len(chs):
                ch_title = str(chs[gi].get("title", "") or "")
            sc_text = "\n\n".join(
                f"[场景{int(s.get('scene_num', 0) or 0)}]\n{s.get('scene_text', '')}"
                for s in group)
            user = (
                f"## 本章定位\n第{gi + 1}章" + (f"《{ch_title}》" if ch_title else "")
                + "\n\n## 互动场景记录（玩家真实经历，含其选择与行动）\n"
                + sc_text[:6000]
                + (f"\n\n## 玩家在互动中的行动/对话（必须体现在正文）\n{acts_brief}"
                   if acts_brief else "")
                + f"\n\n请把以上内容整理成正式章节正文（约 1200 字）。"
            )
            raw = (self._llm(INTERACTIVE_TO_CHAPTER_SYSTEM, user,
                             temperature=0.7, max_tokens=3000) or "").strip()
            if len(raw) < 150:
                continue
            import re as _re
            raw = _re.sub(r"^#{1,3}\s*第?\s*[一二三四五六七八九十百千零两\d]+\s*章.*?\n+", "", raw, count=1)
            fname = f"第{gi + 1:03d}章.md"
            with open(_os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
                f.write(f"# 第{gi + 1}章 {ch_title}\n\n{raw}\n")
            bodies.append(f"# 第{gi + 1}章 {ch_title}\n\n{raw}\n")
            total_chars += len(raw)
            if progress_cb:
                try:
                    progress_cb(gi + 1, len(groups), ch_title or f"第{gi + 1}章", len(raw))
                except Exception:
                    pass
        novel_md = "\n\n".join(bodies)
        with open(_os.path.join(out_dir, "全书.md"), "w", encoding="utf-8") as f:
            f.write(novel_md)
        info = {
            "exported_at": ts, "chapters": len(bodies), "total_chars": total_chars,
            "scene_count": len(scenes), "dir": out_dir,
            "chapters_list": [{"num": i + 1,
                               "title": (str(chs[i].get("title", "")) if i < len(chs) else ""),
                               "chars": len(b)}
                              for i, b in enumerate(bodies)],
        }
        with open(_os.path.join(out_dir, "export_info.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        log.info(f"Interactive exported: {len(bodies)} chapters, {total_chars} chars → {out_dir}")
        return {"chapters": len(bodies), "total_chars": total_chars,
                "dir_path": out_dir, "novel_md": novel_md, "info": info}

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
        player_name = (state.get("player_char") or {}).get("name", "")
        for name in char_names:
            if not name:
                continue
            # v3.5.16: 玩家角色不挂人设（由玩家扮演，不需要 AI 蒸馏）
            if player_name and name == player_name:
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

    def backfill_sync(self, novel_id: str, max_sync: int = 2):
        """v2.5.61: 回流补漏——互动已完成但正式章节缺失的章节自动补回流。

        进入互动模式 / 打开小说页时调用（幂等：已同步区间跳过，不重复生成）。
        后台执行，不阻塞主流程。
        """
        try:
            state = self.store.load_state(novel_id)
            if not state or not state.get("outline_chapters"):
                return
            # 现有正式章节清单
            import os
            from config import NOVELS_DIR
            ch_dir = os.path.join(NOVELS_DIR, novel_id, "chapters")
            existing = {}
            if os.path.isdir(ch_dir):
                for fn in os.listdir(ch_dir):
                    if fn.startswith("chapter_") and fn.endswith(".md"):
                        existing[fn] = True
            need = backfill_list(state, existing, max_sync=max_sync)
            if not need:
                return
            chs = state.get("outline_chapters") or []
            # 补漏：按章号找 idx + 场景区间（取该章对应的场景区间）
            for ch_num in need:
                for idx, ch in enumerate(chs):
                    if int(ch.get("number", 0) or 0) == ch_num:
                        # 场景区间：章号 → 章节场景数估算（每章场景数 = beats 数或 3）
                        _sb = ch.get("scene_beats") or []
                        per = max(2, len(_sb)) if _sb else 3
                        s_start = (idx * per) + 1
                        s_end = s_start + per - 1
                        if sync_skip_check(state, ch_num, s_start, s_end):
                            continue
                        log.info(f"Backfill sync: 第{ch_num}章 场景[{s_start}-{s_end}]")
                        enqueue_background(
                            self._sync_chapter_from_interactive,
                            novel_id, idx, s_start, s_end,
                            critical=False,
                        )
                        break
        except Exception as e:
            log.warning(f"backfill_sync failed: {type(e).__name__}: {str(e)[:100]}")
