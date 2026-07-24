"""NovelGenerator — ArcPlanner: 剧情弧规划器

将零散的骨架章节打包为 3-8 章的剧情弧，每弧有明确的：
- 弧类型: setup(铺垫) | rising(升级) | climax(高潮) | resolution(余波)
- 弧目标
- 情绪曲线
- 角色参与

在 plan_stream() 生成 skeleton 后运行，输出结构化的弧规划注入 writer 上下文。
"""
import logging
import json
from typing import Optional

log = logging.getLogger(__name__)

ARC_ORDER = {"setup": 0, "rising": 1, "climax": 2, "resolution": 3}


def plan_arcs(skeleton_chapters: list, total_chapters: int,
              core_conflict: str = "", arcs_hint: list = None) -> list:
    """将骨架章节分组为剧情弧

    Args:
        skeleton_chapters: 骨架章节列表 [{"number": int, "summary": str, ...}, ...]
        total_chapters: 总章节数
        core_conflict: 核心冲突描述
        arcs_hint: 可选的弧提示（from 需求拆解）

    Returns:
        arcs: [{
            "arc_id": int,
            "chapters": [int, ...],  # 章节号列表
            "type": "setup|rising|climax|resolution",
            "label": str,
            "goal": str,
            "start_chapter": int,
            "end_chapter": int,
            "phase": int,  # 0=铺垫 1=升级 2=高潮 3=余波
            "emotional_arc": str,
            "characters": [str, ...],
            "key_conflict": str,
        }, ...]
    """
    if not skeleton_chapters:
        return []

    # 排序
    sorted_chs = sorted(skeleton_chapters, key=lambda c: int(c.get("number", 0)))

    # 如果给了弧提示，用提示分组
    if arcs_hint and len(arcs_hint) > 1:
        return _plan_from_hints(sorted_chs, arcs_hint, total_chapters)

    return _auto_plan_arcs(sorted_chs, total_chapters, core_conflict)


def _auto_plan_arcs(sorted_chs: list, total_chapters: int, core_conflict: str) -> list:
    """自动将章节分组为弧（基于三幕结构 + 章节数量自适应）"""
    n = len(sorted_chs)
    if n == 0:
        return []

    # 根据总章节数决定每弧大小
    if n <= 12:
        arc_sizes = _distribute_arcs(n, [
            ("setup", 0.25), ("rising", 0.35), ("climax", 0.20), ("resolution", 0.20)
        ])
    elif n <= 30:
        # 较多章节：每个弧 4-6 章
        arc_sizes = _distribute_arcs(n, [
            ("setup", 0.20), ("rising", 0.40), ("climax", 0.20), ("resolution", 0.20)
        ])
    else:
        # 大量章节：每个弧 6-10 章
        arc_sizes = _distribute_arcs(n, [
            ("setup", 0.15), ("rising", 0.45), ("climax", 0.20), ("resolution", 0.20)
        ])

    arcs = []
    idx = 0
    for arc_type, size in arc_sizes:
        if idx >= n:
            break
        chunk = sorted_chs[idx:idx + size]
        if not chunk:
            continue

        ch_nums = [int(c.get("number", 0)) for c in chunk]
        first_ch = ch_nums[0]
        last_ch = ch_nums[-1]

        label, goal, emo_arc = _get_arc_meta(arc_type, core_conflict, chunk)

        arcs.append({
            "arc_id": len(arcs) + 1,
            "chapters": ch_nums,
            "type": arc_type,
            "label": label,
            "goal": goal,
            "start_chapter": first_ch,
            "end_chapter": last_ch,
            "phase": ARC_ORDER.get(arc_type, 0),
            "emotional_arc": emo_arc,
            "characters": _extract_chars(chunk),
            "key_conflict": chunk[0].get("summary", "")[:60] if chunk else "",
        })
        idx += size

    return arcs


def _plan_from_hints(sorted_chs: list, hints: list, total_chapters: int) -> list:
    """根据弧提示分组"""
    arcs = []
    used = 0
    for i, hint in enumerate(hints):
        arc_type = hint.get("type", "rising")
        target_pct = hint.get("target_pct", 0.25)
        n_chs = max(2, int(total_chapters * target_pct))
        chunk = sorted_chs[used:used + n_chs]
        if not chunk:
            break

        ch_nums = [int(c.get("number", 0)) for c in chunk]
        arcs.append({
            "arc_id": i + 1,
            "chapters": ch_nums,
            "type": arc_type,
            "label": hint.get("label", f"弧{i+1}"),
            "goal": hint.get("goal", ""),
            "start_chapter": ch_nums[0],
            "end_chapter": ch_nums[-1],
            "phase": ARC_ORDER.get(arc_type, 0),
            "emotional_arc": hint.get("emotional_arc", "平稳→紧张→释放"),
            "characters": _extract_chars(chunk),
            "key_conflict": chunk[0].get("summary", "")[:60] if chunk else "",
        })
        used += n_chs

    return arcs


def _distribute_arcs(total: int, type_pcts: list) -> list:
    """将章节分配到各弧类型"""
    result = []
    remaining = total
    for i, (arc_type, pct) in enumerate(type_pcts):
        if i == len(type_pcts) - 1:
            size = remaining
        else:
            size = max(2, int(total * pct))
        remaining -= size
        result.append((arc_type, size))
    return result


def _get_arc_meta(arc_type: str, core_conflict: str, chapters: list) -> tuple:
    """获取弧的元数据"""
    meta = {
        "setup": {
            "label": "开局建置弧",
            "goal": "建立世界观，引入核心冲突，主角做出不可逆选择",
            "emo_arc": "好奇→危机感→决意",
        },
        "rising": {
            "label": "冲突升级弧",
            "goal": "主角在对抗中成长，遭遇中点转折，局势逐步升级",
            "emo_arc": "希望→挫折→坚持",
        },
        "climax": {
            "label": "高潮决战弧",
            "goal": "所有伏笔在此回收，决战/最终冲突爆发",
            "emo_arc": "紧张→爆发→震撼",
        },
        "resolution": {
            "label": "结局余韵弧",
            "goal": "战后重建/新平衡建立，角色弧完整收束",
            "emo_arc": "释放→余韵→新生（或悲伤→接受）",
        },
    }
    m = meta.get(arc_type, meta["rising"])
    return m["label"], m["goal"], m["emo_arc"]


def _extract_chars(chapters: list) -> list:
    """提取章节中的角色"""
    chars = set()
    for ch in chapters:
        for c in ch.get("characters", []):
            chars.add(c)
    return list(chars)


def inject_arc_context(arc: dict, chapter_num: int) -> str:
    """为当前章节生成弧上下文（注入 writer 用）"""
    if not arc:
        return ""

    phase_names = {0: "🥚 铺垫阶段", 1: "⚔️ 升级阶段", 2: "🔥 高潮阶段", 3: "🌅 余波阶段"}
    phase_str = phase_names.get(arc.get("phase", 0), "未知")

    # 计算本章在弧中的位置
    ch_list = arc.get("chapters", [])
    if chapter_num in ch_list:
        pos = ch_list.index(chapter_num) + 1
        total = len(ch_list)
    else:
        pos = 0
        total = 0

    position_str = f"{pos}/{total}" if total > 0 else "?"

    return (f"## 🎯 剧情弧上下文\n"
            f"- 弧名: {arc.get('label', '')} (弧{arc.get('arc_id', '?')})\n"
            f"- 阶段: {phase_str}\n"
            f"- 本章在弧中位置: 第{position_str}章\n"
            f"- 弧目标: {arc.get('goal', '')}\n"
            f"- 情绪曲线: {arc.get('emotional_arc', '')}\n"
            f"- 关键冲突: {arc.get('key_conflict', '')}\n")


def is_arc_climax(arc: dict, chapter_num: int) -> bool:
    """判断某章节是否为弧的高潮章"""
    ch_list = arc.get("chapters", [])
    if not ch_list or chapter_num not in ch_list:
        return False
    arc_type = arc.get("type", "")
    if arc_type == "climax":
        return True
    # 如果弧没有明确类型，最后 1-2 章视为高潮
    if arc_type in ("setup", "rising"):
        pos = ch_list.index(chapter_num)
        return pos >= len(ch_list) - 2
    return False
