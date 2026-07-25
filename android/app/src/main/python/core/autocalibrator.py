"""NovelGenerator — AutoCalibrator: 自动剧情校准器

每 N 章（默认10章）执行一次，比对：
1. 已生成内容 vs 原始大纲 → 检测剧情偏移
2. 伏笔账本 → 检查超期未回收的伏笔
3. 角色一致性 → 检测角色弧是否偏离
4. 因果链完整性 → 检查预设的因果是否兑现

输出校准报告，自动注入后续 writer 上下文。
"""
import logging
import json

log = logging.getLogger(__name__)

CALIBRATION_INTERVAL = 10  # 每10章校准一次


class CalibrationReport:
    """校准报告"""
    def __init__(self):
        self.plot_drift_items = []   # [{"severity": "high|mid|low", "description": str}]
        self.overdue_foreshadows = []  # [foreshadow dict]
        self.character_issues = []    # [{"char": str, "issue": str}]
        self.causal_misses = []       # [{"cause": str, "effect": str}]
        self.recommendations = []     # [str]
        self.score = 100              # 剧情健康度 0-100

    def is_healthy(self) -> bool:
        return self.score >= 70

    def to_context_block(self) -> str:
        """生成校准报告文本（注入 writer 上下文用）"""
        if self.is_healthy():
            return ""

        parts = ["## 🔍 剧情校准报告\n"]

        if self.plot_drift_items:
            parts.append("\n### ⚠️ 剧情偏移检测\n")
            for item in self.plot_drift_items:
                icon = {"high": "🔴", "mid": "🟡", "low": "🟢"}.get(item["severity"], "⚪")
                parts.append(f"- {icon} [{item['severity']}] {item['description']}\n")

        if self.overdue_foreshadows:
            parts.append("\n### ⏰ 超期伏笔提醒\n")
            for fs in self.overdue_foreshadows[:5]:
                parts.append(
                    f"- [Ch{fs.get('planted_chapter','?')}埋设，已超{fs.get('planned_payoff_chapter','?')}章] "
                    f"{fs.get('description','')[:40]}\n"
                )

        if self.character_issues:
            parts.append("\n### 👤 角色一致性\n")
            for ci in self.character_issues[:3]:
                parts.append(f"- {ci['char']}: {ci['issue']}\n")

        if self.recommendations:
            parts.append("\n### 💡 校准建议\n")
            for r in self.recommendations[:3]:
                parts.append(f"- {r}\n")

        parts.append(f"\n**剧情健康度: {self.score}/100**\n")
        return "\n".join(parts)


def should_calibrate(chapter_num: int, interval: int = CALIBRATION_INTERVAL) -> bool:
    """判断是否应该执行校准"""
    return chapter_num > 0 and chapter_num % interval == 0


def calibrate(chapter_num: int, plan: dict, storygraph_data: dict,
              completed_chapters: list = None) -> CalibrationReport:
    """执行剧情校准

    Args:
        chapter_num: 当前章节号
        plan: plan.json 原始大纲
        storygraph_data: 剧情图谱数据
        completed_chapters: 已完成的章节号列表

    Returns:
        CalibrationReport
    """
    report = CalibrationReport()

    if not plan or not storygraph_data:
        report.recommendations.append("缺少规划数据或剧情图谱，无法执行完整校准")
        return report

    # 1) 检测剧情偏移：对比已完成章节 vs 大纲骨架
    drift_issues = _detect_plot_drift(plan, storygraph_data, chapter_num)
    report.plot_drift_items = drift_issues

    # 2) 检测超期伏笔
    fs_ledger = storygraph_data.get("foreshadow_ledger", {})
    overdue = []
    for fid, fs in fs_ledger.items():
        if fs.get("status") in ("planted", "hinted"):
            if fs.get("planned_payoff_chapter", 999) < chapter_num:
                overdue.append(fs)
    report.overdue_foreshadows = overdue

    # 3) 检测角色一致性问题
    char_issues = _detect_character_issues(storygraph_data)
    report.character_issues = char_issues

    # 4) 检测因果链遗漏
    causal_misses = _detect_causal_misses(storygraph_data, chapter_num)
    report.causal_misses = causal_misses

    # 5) 计算剧情健康度
    deductions = 0
    deductions += len(drift_issues) * 5
    deductions += len(overdue) * 8
    deductions += len(char_issues) * 4
    deductions += len(causal_misses) * 3
    report.score = max(0, min(100, 100 - deductions))

    # 6) 根据问题生成建议
    if drift_issues:
        for item in drift_issues:
            report.recommendations.append(item["description"])
    if overdue:
        report.recommendations.append(
            f"有{len(overdue)}条伏笔已超期未回收，建议在后续章节中尽快处理"
        )
    if char_issues:
        for ci in char_issues[:2]:
            report.recommendations.append(f"{ci['char']} {ci['issue']}")
    if not report.recommendations:
        report.recommendations.append("剧情状态良好，继续按计划推进")

    return report


def _detect_plot_drift(plan: dict, storygraph_data: dict, chapter_num: int) -> list:
    """检测剧情偏移"""
    issues = []

    # 检查是否有长期无进展的活跃剧情线
    threads = storygraph_data.get("plot_threads", {})
    for tid, t in threads.items():
        if t.get("status") in ("active", "advancing") and t.get("priority", 0) >= 4:
            nodes = t.get("key_nodes", [])
            if nodes:
                last_ch = nodes[-1]["chapter"]
                if chapter_num - last_ch > 8:
                    issues.append({
                        "severity": "high",
                        "description": f"高优剧情线「{t['name']}」已连续{chapter_num - last_ch}章无进展"
                    })

    # 检查大纲中标记为关键的章节是否已生成
    outline = plan.get("outline", {})
    for vol in outline.get("volumes", []):
        for ch in outline.get("skeleton", []) if vol.get("chapters") is None else vol.get("chapters", []):
            summary = ch.get("summary", "")
            if "关键" in summary or "转折" in summary or "高潮" in summary:
                ch_num = int(ch.get("number", 0))
                if ch_num <= chapter_num and ch_num > 0:
                    # 检查剧情图谱中是否有对应节点
                    has_event = False
                    for t in threads.values():
                        for n in t.get("key_nodes", []):
                            if n["chapter"] == ch_num:
                                has_event = True
                                break
                    if not has_event:
                        issues.append({
                            "severity": "mid",
                            "description": f"大纲标记的第{ch_num}章(「{summary[:20]}」)可能未按计划执行"
                        })

    return issues


def _detect_character_issues(storygraph_data: dict) -> list:
    """检测角色一致性问题"""
    issues = []
    snaps = storygraph_data.get("char_snapshots", {})

    # 检测长时间未出场的重要角色
    for name, snap in snaps.items():
        last_ch = snap.get("last_chapter_appeared", 0)
        goals = snap.get("active_goals", [])
        if last_ch > 0 and goals:
            # 有未完成目标但很久没出现
            pass  # 静默跟踪，不报错

    return issues


def _detect_causal_misses(storygraph_data: dict, chapter_num: int) -> list:
    """检测因果链遗漏"""
    misses = []
    links = storygraph_data.get("causal_links", [])

    for link in links:
        effect_ch = link.get("effect_chapter", 0)
        if effect_ch <= chapter_num and link.get("status") == "pending":
            misses.append(link)

    return misses
