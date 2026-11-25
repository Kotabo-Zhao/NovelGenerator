"""Pure narrative-state rules, promise ledger, and cast-presence helpers for StoryDirector.

Extracted from story_director.py in v4.6.0 for modular maintainability; all public
symbols remain re-exported from core.interactive.story_director for backward compatibility.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Optional

from .action_engine import clean_location

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
            terminal = ('done','declined','failed') if state.get('participation') else ('done',)
            return all(str(b.get("status", "")) in terminal for b in beats)
        if state.get('participation'):
            return False
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


# ── v4.3: only validated action outcomes may complete chapter events. ──
def beat_advance_by_action(state: dict, action: dict) -> List[str]:
    """完成有执行证据且满足条件的当前事件，并提交节点产出。

    意图文本、拒绝、未决或失败不触发完成，也不累积强制回轨计数。
    返回变化描述列表。
    """
    changes: List[str] = []
    try:
        from .participation import event_matches
        if not event_matches(state, action):
            return changes
        # 命中 → 推进当前 beat
        cb = state.get("chapter_beats") or {}
        beats = cb.get("beats") or []
        cur_i = next((i for i, b in enumerate(beats) if b.get("status") == "current"), None)
        if cur_i is None:
            return changes
        beats[cur_i]["status"] = "done"
        contract = state.get('participation')
        if contract:
            contract.setdefault('completed_events',[]).append({
                'id':beats[cur_i].get('id'), 'chapter':contract.get('chapter_number'),
                'event':beats[cur_i].get('desc'), 'action_id':action['resolution']['id']})
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
    if state.get('participation'):
        return None
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
    if state.get('participation'):
        here = (state.get('world') or {}).get('location') or (state.get('state') or {}).get('location','')
        world_chars = (state.get('world') or {}).get('chars') or {}
        casts = state.get('casts') or {}
        player = (state.get('player_char') or {}).get('name')
        present, away = [], []
        for name, cast in casts.items():
            if name == player:
                continue
            entry = world_chars.get(name) or (state.get('cast_states') or {}).get(name) or cast
            if cast.get('alive') is False or entry.get('present') is False or entry.get('location') not in (None,'',here):
                away.append(name)
            elif entry.get('present') is True:
                present.append(name)
            else:
                away.append(name)
        return present, away
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
