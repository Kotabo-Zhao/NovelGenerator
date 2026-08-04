# -*- coding: utf-8 -*-
"""WorldState — 互动小说世界状态三支柱（v3.6）

三支柱：time（时间） / location（地点） / chars（人物在场）
铁律：**状态变化由确定性规则驱动，LLM 输出仅作候选**（防幻觉）。
      LLM 负责填内容（对话/描写），不负责改状态。

数据结构（state["world"]）：
{
  "time":     {"label": "正午", "slot": 3, "day": 1},
  "location": "茶楼",
  "chars":    {"林晚晚": {"present": true, "location": "茶楼", "relation": 62}},
  "locations": {                       # 地点图谱
    "茶楼": {"desc": "...", "connected": ["街市"], "chars": ["林晚晚"], "items": []}
  }
}
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# ── 时间档位表（确定性推进）──
TIME_SLOTS = ["清晨", "上午", "正午", "下午", "傍晚", "夜晚", "深夜"]

# v3.6 P2: 时段 → 场景氛围提示（时间驱动内容生成——LLM 描写必须体现）
TIME_SCENE_HINTS = {
    "清晨": "清晨：天色初亮、空气清冷，街市刚开张，人们睡眼惺忪",
    "上午": "上午：日光充足，市面繁忙，正是办事的好时候",
    "正午": "正午：日头高照，行人稀少，酒楼茶馆里人声鼎沸",
    "下午": "下午：光线渐斜，长街的影子拉长，茶客陆续散去",
    "傍晚": "傍晚：暮色四合、灯火初上，归家与开夜市的人流交汇",
    "夜晚": "夜晚：灯火明亮或昏黄，月色或明或暗，坊市或喧嚣或沉寂",
    "深夜": "深夜：万籁俱寂，只有更声与犬吠，多数门户紧闭",
}


def time_scene_hint(world: dict) -> str:
    """当前时段的场景氛围提示（空则无提示）。"""
    t = world.get("time") or {}
    return TIME_SCENE_HINTS.get(str(t.get("label", "")), "")

# ── 移动目标规则解析 ──
# 高置信移动表达（命中 → travel 意图）
_TRAVEL_VERBS = (
    "回家|回府|回宫|回房|回宅|回府邸|回去|回城|进城|出城|上楼|下楼|"
    "进屋|出屋|进门|出门|回客栈|回酒楼|回店铺|回到|赶回|赶往|"
    "去(?:了|到|往|向)?|前往|来到|走到|抵达|到达|离开|出发|"
    "回(?:到)?"
)
TRAVEL_RE = re.compile(
    r"^(?:(?:我|咱|咱们|我们|人家)?(?:这就|现在|马上|要|想|打算|就|已经|先)?)"
    r"(" + _TRAVEL_VERBS + r")([\u4e00-\u9fff]{0,12})[。！!]?$")

# 无目标纯移动词（"走吧/出发/我们走"——目标由剧情/默认决定，P0 走对话不硬转）
_TRAVEL_BARE = re.compile(r"^(?:走吧|出发|我们走|咱们走|走)[。！!]?$")

# 确认/否定词（回问确认流）
# v3.6.2: 确认/取消正则——支持自然语言确认（"好，去吧"/"行 走吧"/"ok 走"）——
# 原先只匹配 1-2 字短词，带逗号/语气词的确认全部漏掉 → 确认流形同虚设
CONFIRM_RE = re.compile(
    r"^(?:嗯+|好[的嘛]?|行[的嘛]?|确定|是的?|是|对|可以|没问题|ok|OK|好呀|好啊|"
    r"走|去吧?|出发(?:吧)?|走起|带路吧?|就[这那]样|听你的|都行)"
    r"(?:[，,、\s]*(?:那就?|就|也|都)?(?:走|去|出发|吧|呗|去吧?|听你的)?)*[。！!～~]?$")
DENY_RE = re.compile(
    r"^(?:不|算了|取消|等等|别|没|不是|不行|不去|再想想|稍等|逗你的|开玩笑的?|"
    r"先不去|不去了|算了吧?|改天吧?|回头再说|以后再说)[。！!，,～~]?$")

# "家"类节点别名（图谱内查找居住地）
_HOME_ALIASES = ("家", "府", "宅", "住处", "居所", "小院", "院子", "府邸", "房")
# 反义词：明确非家（防止把"茶楼"当回家目标）
_NON_HOME = ("茶楼", "茶馆", "酒楼", "酒店", "客栈", "店铺", "铺", "衙门",
             "宫", "殿", "集市", "码头", "野外", "山上", "路上", "坊", "档口")
# 语气词（"回家了"的"了"不算目标）
_TONE_WORDS = "了吧呀啊呢的么嘛"


# ── 三支柱初始化/迁移 ──
def ensure_world(state: dict) -> dict:
    """旧存档迁移 + 三支柱初始化（幂等，任何入口可安全调用）。"""
    w = state.setdefault("world", {})
    t = w.setdefault("time", {})
    if not isinstance(t, dict):
        t = {}
        w["time"] = t
    if not t.get("slot") or not t.get("label"):
        # 从 player_state.time（LLM 历史提取）迁移；无则默认正午
        ps = state.get("player_state") or {}
        old = str(ps.get("time", "")).strip()
        idx = TIME_SLOTS.index(old) if old in TIME_SLOTS else 2
        t["slot"] = idx
        t["label"] = TIME_SLOTS[idx]
        t["day"] = int(t.get("day") or 1)
    else:
        # label 与 slot 不一致时以 label 为准（防脏数据）
        try:
            _idx = TIME_SLOTS.index(str(t.get("label", "")))
            if _idx != int(t.get("slot", _idx)):
                t["slot"] = _idx
        except (ValueError, TypeError):
            t["slot"] = int(t.get("slot", 2))
            t["label"] = TIME_SLOTS[int(t["slot"]) % len(TIME_SLOTS)]
    w.setdefault("location", "")
    w.setdefault("chars", {})
    w.setdefault("locations", {})
    # 图谱缺失时尝试构建（幂等：已有节点不动）
    if not w.get("locations"):
        w["locations"] = build_location_graph(state)
    # location 与 state.location / player_state.location 对齐（三处一致性）
    s = state.get("state") or {}
    ps = state.get("player_state") or {}
    cur = w.get("location") or clean_loc(s.get("location")) or clean_loc(ps.get("location")) or ""
    if cur:
        w["location"] = cur
        if s.get("location") != cur:
            s["location"] = cur
        if ps.get("location") != cur:
            ps["location"] = cur
        state["player_state"] = ps
    # chars 与 cast_states 对齐
    _sync_chars_from_cast(state, w)
    return w


def clean_loc(loc) -> str:
    """地点字符串清洗（去 JSON 残留/多段列表，取第一段）。"""
    if not loc:
        return ""
    loc = str(loc).strip()
    m = re.search(r'"(?:name|location)"\s*:\s*"([^"]+)"', loc)
    if m:
        loc = m.group(1).strip()
    for sep in ("，", ",", "；", ";"):
        if sep in loc:
            return loc.split(sep)[0].strip()[:60]
    return loc[:60]


# ── 地点图谱 ──
def build_location_graph(state: dict) -> dict:
    """规则构建地点图谱（零 LLM）：
    - 来源：known_locations（去过）、player_state.location（当前）、
      cast_states[].location（角色位置）、worldbuilding_brief 地理串
    - 边：同批出现的候选地点彼此互连（当前地点 ↔ 已知地点）
    """
    w = state.get("world") or {}
    locations: dict = {}
    known = [clean_loc(x) for x in (state.get("known_locations") or []) if clean_loc(x)]
    ps = state.get("player_state") or {}
    cur = clean_loc(w.get("location")) or clean_loc(ps.get("location"))
    cs = state.get("cast_states") or {}
    char_locs = [clean_loc(c.get("location")) for c in cs.values() if clean_loc(c.get("location"))]

    cands = []
    for x in known + [cur] + char_locs:
        if x and x not in cands:
            cands.append(x)
    # worldbuilding 地理串拆分（"上海，陆家嘴、前滩" → 上海/陆家嘴/前滩）
    wb = str(state.get("worldbuilding_brief", ""))
    for seg in re.split(r"[，,、;；\n]", wb):
        seg = clean_loc(seg)
        if seg and 1 <= len(seg) <= 12 and seg not in cands:
            cands.append(seg)
    for name in cands:
        locations[name] = {
            "desc": "",
            "connected": [c for c in cands if c != name][:8],
            "chars": [],
            "items": [],
        }
    # 图谱为空且至少知道当前地点 → 注册当前地点
    if not locations and cur:
        locations[cur] = {"desc": "", "connected": [], "chars": [], "items": []}
    return locations


def find_home_location(state: dict) -> str:
    """在图谱中找"家"类节点（回家目标）。规则：优先名字含家/府/宅且非茶楼类。"""
    w = state.get("world") or {}
    locations = w.get("locations") or {}
    if not locations:
        return ""
    for name in locations:
        if any(a in name for a in _HOME_ALIASES) and not any(n in name for n in _NON_HOME):
            return name
    # 兜底：known_locations 里找；再兜底第一个节点（当前地点的 connected 中第一个）
    known = [clean_loc(x) for x in (state.get("known_locations") or [])]
    for name in known:
        if any(a in name for a in _HOME_ALIASES):
            return name
    return ""


# ── 移动目标解析（规则，零 LLM）──
def resolve_travel_target(text: str, state: dict) -> Tuple[str, bool]:
    """解析玩家输入的移动目标。
    返回 (target, ok)；ok=False 表示解析不出明确目标（不硬转 travel）。
    """
    m = TRAVEL_RE.match(text.strip())
    if not m:
        return "", False
    verb, raw = m.group(1), m.group(2).strip()
    raw = raw.rstrip(_TONE_WORDS)  # 去语气词（"回家了"→"回家"+目标空）
    # "回X"类动词自带目标（"回家吃饭"→家，"回府"→府）；"回去/回来/回到"无明确目标
    if verb.startswith("回") and verb not in ("回去", "回来", "回到", "回归"):
        raw = verb[1:] or raw
    if not raw:
        return "", False
    w = state.get("world") or {}
    locations = w.get("locations") or {}
    cur = clean_loc(w.get("location")) or ""

    # 1) 回家类（动词含"回"且无具体地点）→ 图谱 home 节点
    if verb.startswith("回") and raw in ("家", "府", "宅", "房", "府邸", "住处"):
        home = find_home_location(state)
        if home and home != cur:
            return home, True
        # 图谱无 home → 返回原始目标，走确认流注册（v3.6：绝不静默退回对话）
        return raw, True
    # 2) 直接匹配图谱节点
    for name in locations:
        if raw == name or raw in name or name in raw:
            if name != cur:
                return name, True
            return "", False
    # 3) 未命中图谱 → 原样返回 raw（走确认流，确认后注册）
    if raw != cur:
        return raw, True
    return "", False


# ── 时间推进（确定性）──
def advance_time(world: dict, steps: int = 1) -> List[str]:
    """时间推进：slot +steps，跨过深夜 → 次日清晨，day+1。返回变化描述。"""
    t = world.setdefault("time", {})
    try:
        slot = int(t.get("slot", 3)) + int(steps)
    except (TypeError, ValueError):
        slot = int(t.get("slot", 3)) + 1
    day = int(t.get("day") or 1)
    while slot >= len(TIME_SLOTS):
        slot -= len(TIME_SLOTS)
        day += 1
    old_label = t.get("label", "")
    t["slot"] = slot
    t["label"] = TIME_SLOTS[slot]
    t["day"] = day
    changes = []
    if old_label != t["label"]:
        changes.append(f"时间: {old_label or '?'} → {t['label']}")
    if day > 1 and steps > 0:
        changes.append(f"第{day}天")
    return changes


def time_label(world: dict) -> str:
    t = world.get("time") or {}
    label = t.get("label") or ""
    day = int(t.get("day") or 1)
    return f"第{day}天·{label}" if day > 1 else label


# ── 人物在场重算（跟随规则）──
def recompute_presence(state: dict, world: dict, moved_from: str = "") -> List[str]:
    """地点变化后重算人物在场 + 跟随。
    规则：
    - player_state.with 中的角色 → 跟随（location 同步）
    - cast_states 中 location == 旧地点 且非跟随 → 留在原地
    - 其余角色位置未知 → 保留原标记
    返回变化描述列表。
    """
    cur = clean_loc(world.get("location"))
    ps = state.get("player_state") or {}
    with_chars = [str(x) for x in (ps.get("with") or [])]
    cs = state.get("cast_states") or {}
    changes = []
    for name, c in cs.items():
        cloc = clean_loc(c.get("location"))
        if name in with_chars:
            if cloc != cur:
                c["location"] = cur
                c["present"] = True
                changes.append(f"{name}跟随来到{cur}")
        elif moved_from and cloc == moved_from:
            c["present"] = False
            changes.append(f"{name}留在{moved_from}")
    state["cast_states"] = cs
    # v3.6 P2: 图谱 chars 双写（locations[地点].chars 与 cast_states 一致）
    try:
        locations = world.setdefault("locations", {})
        if moved_from and moved_from in locations:
            _chars = locations[moved_from].setdefault("chars", [])
            locations[moved_from]["chars"] = [x for x in _chars if x not in with_chars]
        if cur and cur in locations:
            _chars = locations[cur].setdefault("chars", [])
            for _nm in with_chars:
                if _nm not in _chars:
                    _chars.append(_nm)
    except Exception:
        pass
    # world.chars 同步
    _sync_chars_from_cast(state, world)
    return changes


def _sync_chars_from_cast(state: dict, world: dict):
    """world.chars 与 cast_states 对齐（present/location/relation 镜像）。"""
    chars = world.setdefault("chars", {})
    cs = state.get("cast_states") or {}
    rel = (state.get("state") or {}).get("relations") or {}
    for name, c in cs.items():
        entry = chars.setdefault(str(name), {})
        entry["present"] = bool(c.get("present", entry.get("present", False)))
        loc = clean_loc(c.get("location"))
        if loc:
            entry["location"] = loc
        if name in rel:
            entry["relation"] = rel[name]
    # 保留不在 cast_states 的关系角色
    for name, v in rel.items():
        chars.setdefault(str(name), {}).setdefault("relation", v)


# ── 移动执行器 ──
def execute_travel(state: dict, target: str, register_new: bool = True) -> Tuple[List[str], bool]:
    """执行移动：图谱校验 → 更新 location → 时间推进 → 在场重算。
    返回 (changes, ok)。图谱无此节点且 register_new=True → 注册后移动（确认流已通过）。
    """
    w = ensure_world(state)
    locations = w.get("locations") or {}
    target = clean_loc(target)
    cur = clean_loc(w.get("location"))
    if not target or target == cur:
        return [], False
    if target not in locations:
        if not register_new:
            return [], False
        # 注册新节点（确认流通过后）
        locations[target] = {"desc": "", "connected": [cur] if cur else [], "chars": [], "items": []}
        if cur and target not in locations[cur]["connected"]:
            locations[cur]["connected"].append(target)
    changes = [f"地点: {cur or '?'} → {target}"]
    w["location"] = target
    s = state.get("state") or {}
    s["location"] = target
    ps = state.get("player_state") or {}
    ps["location"] = target
    ps["situation"] = f"刚到达{target}"
    state["player_state"] = ps
    state.setdefault("known_locations", [])
    if target not in state["known_locations"]:
        state["known_locations"].append(target)
    # 时间推进（移动 +1 档）
    changes.extend(advance_time(w, 1))
    # 在场重算（跟随/留守）
    changes.extend(recompute_presence(state, w, moved_from=cur))
    return changes, True


# ── 三支柱注入文本（场景/行动 prompt 用）──
def world_brief(state: dict, max_chars: int = 400) -> str:
    """确定性三支柱快照（不依赖 LLM 记忆）。"""
    w = ensure_world(state)
    lines = []
    loc = clean_loc(w.get("location"))
    locations = w.get("locations") or {}
    desc = ""
    if loc in locations:
        desc = str(locations[loc].get("desc") or "")[:120]
    lines.append(f"时间: {time_label(w)}")
    lines.append(f"地点: {loc}{('（' + desc + '）') if desc else ''}")
    chars = w.get("chars") or {}
    present = [n for n, c in chars.items() if c.get("present")]
    if present:
        lines.append(f"在场人物: {'、'.join(present[:6])}")
    return "\n".join(lines)[:max_chars]


# ── v3.6 P3: 对话承诺 → 行动兑现（地点锚定）──
# 约定地点模式（"在码头见/码头等你/去茶楼碰头"——图谱节点名 + 位置介词）
_LOCATION_PATTERNS = [
    re.compile(r"(?:在|到|去|回|来|上|于)\s*([\u4e00-\u9fff]{1,8})\s*(?:见|等|碰头|会合|汇合|找你|见面|赴宴|吃饭|喝茶|等)", re.S),
    re.compile(r"([\u4e00-\u9fff]{1,8})\s*(?:见|碰头|会合|汇合|等我|找我)", re.S),
]


def extract_location_from_text(text: str, state: dict) -> str:
    """从约定文本中规则提取地点（图谱节点匹配，零 LLM）。
    优先"在X见"类模式；其次扫描图谱节点名在文本中的出现。"""
    if not text:
        return ""
    w = state.get("world") or {}
    locations = w.get("locations") or {}
    names = sorted(locations.keys(), key=len, reverse=True)  # 长名优先
    for pat in _LOCATION_PATTERNS:
        m = pat.search(text)
        if m:
            cand = m.group(1).strip()
            for n in names:
                if cand == n or cand in n or n in cand:
                    return n
            return cand  # 图谱无此节点也返回候选（后续到达时图谱可能已注册）
    for n in names:
        if n and n in text:
            return n
    return ""


def fulfill_promises_at(state: dict, location: str) -> List[str]:
    """玩家到达某地点 → 兑现该地点的待兑现约定（规则，零 LLM）。
    返回变化描述列表。"""
    location = clean_loc(location)
    if not location:
        return []
    ledger = state.get("pending_promises") or []
    changes = []
    for p in ledger:
        if p.get("status") != "pending":
            continue
        ploc = clean_loc(p.get("location"))
        if ploc and ploc == location:
            p["status"] = "fulfilled"
            changes.append(
                f"约定兑现: 与{p.get('who', '?')}的约定（{p.get('what', '')}）在{location}履行")
            try:
                from .char_memory import add_event
                add_event(state, f"约定兑现: 与{p.get('who', '?')}{p.get('what', '')}"
                                 f"（{p.get('when_raw', '')}）在{location}", "promise")
            except Exception:
                pass
    if changes:
        state["pending_promises"] = ledger
    return changes


def pending_promises_brief(state: dict, location: str = "", max_chars: int = 200) -> str:
    """待兑现约定简报（注入场景 prompt：地点匹配优先）。"""
    ledger = state.get("pending_promises") or []
    loc = clean_loc(location)
    pending = [p for p in ledger if p.get("status") == "pending"]
    if not pending:
        return ""
    lines = []
    for p in pending[:3]:
        ploc = clean_loc(p.get("location"))
        if loc and ploc and ploc == loc:
            lines.append(f"● {p.get('who', '?')}约你{p.get('when_raw', '')}在此地{p.get('what', '')}（待赴约）")
        elif not ploc:
            lines.append(f"● 你与{p.get('who', '?')}有约：{p.get('what', '')}（{p.get('when_raw', '') or '未定时间'}，待赴约）")
    return "\n".join(lines)[:max_chars]


# ── v3.6.4: 行动选项生成器（按钮化——玩家点按钮 = 意图已确定，零 LLM 识别）──

# 图谱节点清洗：worldbuilding 解析可能塞入设定描述片段（"era: 2020年代"/"市值超千亿"），
# 这些不是地点，不能进行动按钮
_NON_LOC_RE = re.compile(
    r"era:|power_|core_|faction|世界观|规则|体系|市值|离婚|婚姻|父亲|母亲|家庭|"
    r"声誉|社交|资本|权[力势]?$|商业|利益|象征|编织|丑闻|女主|男主|与|和|的$|"
    r"当代|年代|世纪|——|—|：|:|,|，|。")
_LOC_MAX_LEN = 8  # 地点名超过 8 字基本是描述片段（"上海陆家嘴金融区——甜品店内"除外，它带——）

# 脏角色名清洗（LLM 状态提取把内心描写当角色名的历史脏数据）
_DIRTY_CHAR_RE = re.compile(r"^你|^我|心中|冷笑|面上|不露|内心|感到|觉得")


def _clean_action_target(text: str, kind: str) -> bool:
    """行动目标有效性校验（清洗脏数据）"""
    t = str(text or "").strip()
    if not t or len(t) > 24:
        return False
    if kind == "travel":
        if _NON_LOC_RE.search(t):
            return False
        if len(t) > _LOC_MAX_LEN and "——" not in t and "甜品店" not in t:
            return False
        if t in ("家", "街市", "码头", "茶楼"):  # 白名单短地点
            return True
    elif kind == "char":
        if _DIRTY_CHAR_RE.match(t):
            return False
        if len(t) > 10:
            return False
    return True

# 通用动作模板（按角色/物品/地点场景套用）
_GENERIC_ACTIONS = [
    {"type": "interact", "tpl": "和{name}说话", "target_kind": "char"},
    {"type": "interact", "tpl": "仔细打量{name}", "target_kind": "char"},
    {"type": "interact", "tpl": "把随身的东西递给{name}", "target_kind": "char"},
    {"type": "use", "tpl": "使用{name}", "target_kind": "item"},
    {"type": "use", "tpl": "仔细查看{name}", "target_kind": "item"},
    {"type": "investigate", "tpl": "观察四周环境", "target_kind": "none"},
    {"type": "investigate", "tpl": "查看随身物品", "target_kind": "none"},
]


def action_options(state: dict, max_actions: int = 8) -> list:
    """上下文行动按钮列表（确定性规则，零 LLM）。

    来源：
    - 移动：当前地点图谱 connected 节点（"去X"）
    - 交互：当前在场角色（"和X说话"等）
    - 物品：玩家背包 + 当前地点 items
    返回 [{id, label, intent, target, kind, emoji}]
    """
    try:
        w = state.get("world") or {}
        locations = w.get("locations") or {}
        ps = state.get("player_state") or {}
        s = state.get("state") or {}
        cur = clean_loc(w.get("location")) or clean_loc(ps.get("location")) or clean_loc(s.get("location"))
        player_name = (state.get("player_char") or {}).get("name", "")
        opts = []

        # 1) 移动选项（图谱 connected，清洗非地点节点）
        entry = locations.get(cur) if cur else None
        connected = []
        if isinstance(entry, dict):
            connected = [str(x) for x in (entry.get("connected") or []) if str(x) != cur]
        if not connected and cur:  # 兜底：全部图谱节点（防图谱边缺失导致无路可走）
            connected = [str(x) for x in locations.keys() if str(x) != cur]
        for loc in connected[:6]:
            if not _clean_action_target(loc, "travel"):
                continue
            opts.append({
                "id": f"go_{loc}", "label": f"去{loc}", "intent": "travel",
                "target": loc, "kind": "travel", "emoji": "📍",
            })

        # 2) 在场角色交互（清洗脏角色名）
        try:
            from .story_director import compute_present
            present, _away = compute_present(state)
        except Exception:
            present = []
        for name in present[:4]:
            if name and name != player_name and _clean_action_target(name, "char"):
                opts.append({
                    "id": f"talk_{name}", "label": f"和{name}说话", "intent": "talk",
                    "target": name, "kind": "char", "emoji": "👥",
                })

        # 3) 物品选项（背包 + 地点物品）
        inv = [str(x) for x in (s.get("inventory") or [])]
        loc_items = [str(x) for x in ((entry or {}).get("items") or [])] if isinstance(entry, dict) else []
        for item in (inv + loc_items)[:3]:
            if item and not any(o.get("target") == item for o in opts):
                opts.append({
                    "id": f"use_{item}", "label": f"使用{item}", "intent": "act",
                    "target": item, "kind": "item", "emoji": "📦",
                })

        # 4) 常用行动（兜底，保证按钮条不空）
        if not opts:
            opts.append({"id": "observe", "label": "观察四周环境", "intent": "act",
                         "target": "", "kind": "investigate", "emoji": "🔍"})
        return opts[:max_actions]
    except Exception as e:
        log.warning(f"action_options failed: {e}")
        return []


# ── LLM 状态提取结果校验（防幻觉：候选 → 规则裁决）──
def validate_llm_state(state: dict, extracted_ps: dict) -> dict:
    """场景/对话后的 LLM 状态提取结果与三支柱合并：
    - location：LLM 候选过图谱/known 校验（图谱外 → 不采纳）
    - time：LLM 候选过单调校验（与 world 冲突 → 不采纳；world 空才采纳）
    - with/holding/situation/condition：直接采纳（不破坏世界结构）
    """
    w = ensure_world(state)
    if not isinstance(extracted_ps, dict):
        return state.get("player_state") or {}
    ps = state.get("player_state") or {}
    # time 由规则驱动（world 档位推进）：LLM 的 time 不采纳，同步档位
    ps["time"] = (w.get("time") or {}).get("label", ps.get("time", ""))
    llm_loc = clean_loc(extracted_ps.get("location"))
    if llm_loc:
        locations = w.get("locations") or {}
        known = [clean_loc(x) for x in (state.get("known_locations") or [])]
        if llm_loc in locations or llm_loc in known:
            if llm_loc != clean_loc(w.get("location")):
                # 场景叙述显示玩家换地 → 同步三支柱（LLM 候选 + 图谱可查 → 采纳）
                w["location"] = llm_loc
                ps["location"] = llm_loc
                recompute_presence(state, w)
    for k in ("with", "holding", "situation", "condition", "disguise", "money"):
        if k in extracted_ps and extracted_ps.get(k):
            ps[k] = extracted_ps[k]
    state["player_state"] = ps
    return ps
