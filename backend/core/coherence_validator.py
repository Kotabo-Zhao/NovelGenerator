"""NovelGenerator — CoherenceValidator: 大纲逻辑连贯性校验

职责: 大纲生成后检查因果链完整性，检测断裂点并自动修补。
在 planner 生成 outline 之后、writing 开始之前执行。

v2.10: 新增，解决"章节前后没有逻辑"的根本原因——大纲层面缺乏因果约束。
"""

import json
import logging
import re
from typing import Optional

log = logging.getLogger(__name__)


class CoherenceValidator:
    """大纲因果链校验器
    
    分三级检查：
    1. P0-致命: 因果链为空或"无"、冲突强度上下跳、bridge/next不匹配
    2. P1-警告: opening_scene 无法从上一章推导、因果链过于模糊
    3. P2-建议: 冲突多样性不足、钩子质量
    
    发现 P0 错误会尝试自动修补（用规则重写而不是 LLM，避免二次出错）。
    """

    def __init__(self):
        self.issues = []
        self.fixes_applied = []

    def validate(self, outline: dict) -> dict:
        """验证大纲的因果链完整性

        Args:
            outline: plan["outline"] dict with volumes.chapters

        Returns:
            {
                "passed": bool,
                "score": int (0-100),
                "issues": [{"severity": "P0"|"P1"|"P2", "chapter": int, "field": str, "message": str}],
                "fixes_applied": [str]
            }
        """
        self.issues = []
        self.fixes_applied = []

        # 展平所有章节
        all_chapters = self._flatten_chapters(outline)
        if not all_chapters:
            return {"passed": True, "score": 100, "issues": [], "fixes_applied": []}

        # ── P0: 因果链字段是否存在 ──
        self._check_causal_fields_exist(all_chapters)

        # ── P0: bridge_to_next(N) ≈ cause_from_prev(N+1) ──
        self._check_bridge_cause_alignment(all_chapters)

        # ── P0: 冲突强度递进 ──
        self._check_conflict_progression(all_chapters)

        # ── P1: opening_scene 推导性 ──
        self._check_opening_derivability(all_chapters)

        # ── P1: 因果链语义质量 ──
        self._check_causal_quality(all_chapters)

        # ── P2: 冲突多样性 ──
        self._check_conflict_diversity(all_chapters)

        # 计算评分
        p0_count = sum(1 for i in self.issues if i["severity"] == "P0")
        p1_count = sum(1 for i in self.issues if i["severity"] == "P1")
        p2_count = sum(1 for i in self.issues if i["severity"] == "P2")
        
        score = max(0, 100 - (p0_count * 20) - (p1_count * 5) - (p2_count * 1))
        passed = p0_count == 0

        result = {
            "passed": passed,
            "score": score,
            "issues": self.issues,
            "fixes_applied": self.fixes_applied,
        }

        if not passed:
            log.warning(f"CoherenceValidator: {p0_count}P0 {p1_count}P1 {p2_count}P2 issues, score={score}")
        else:
            log.info(f"CoherenceValidator: all checks passed, score={score}")

        return result

    # ═══════════════════════════════════════════
    # 展平工具
    # ═══════════════════════════════════════════

    def _flatten_chapters(self, outline: dict) -> list:
        """将 outline.volumes[].chapters[] 展平为有序列表"""
        chapters = []
        volumes = outline.get("volumes", [])
        if not isinstance(volumes, list):
            return chapters
        for vol in volumes:
            if not isinstance(vol, dict):
                continue
            for ch in vol.get("chapters", []):
                if isinstance(ch, dict):
                    chapters.append(ch)
        return sorted(chapters, key=lambda c: int(c.get("number", 0)))

    # ═══════════════════════════════════════════
    # P0 检查
    # ═══════════════════════════════════════════

    def _check_causal_fields_exist(self, chapters: list):
        """检查每章是否填写了因果链字段"""
        for i, ch in enumerate(chapters):
            ch_num = int(ch.get("number", i + 1))
            
            cause = ch.get("cause_from_prev", "")
            bridge = ch.get("bridge_to_next", "")
            opening = ch.get("opening_scene", "")
            intensity = ch.get("conflict_intensity", "")

            if ch_num > 1:  # 第1章 allow "开篇"
                if not cause or cause.strip() in ("", "无", "暂无", "待定"):
                    self._add_issue("P0", ch_num, "cause_from_prev", "因果链缺失——未说明本章如何由上章引发")
                    # 自动修补：生成占位因果
                    if i > 0:
                        prev_summary = chapters[i - 1].get("summary", "上章事件")
                        ch["cause_from_prev"] = f"承接上章'{prev_summary}'的后续发展"
                        self._add_fix(f"Ch{ch_num}: 自动填充 cause_from_prev")

            if not bridge or bridge.strip() in ("", "无", "暂无", "待定"):
                if i < len(chapters) - 1:  # 最后一章也允许无bridge
                    self._add_issue("P0", ch_num, "bridge_to_next", "引出字段缺失——未说明本章如何引向下一章")
                    ch["bridge_to_next"] = f"本章事件'{ch.get('summary','')}'导致下一章衔接"
                    self._add_fix(f"Ch{ch_num}: 自动填充 bridge_to_next")

            if not opening or opening.strip() in ("", "无", "待定"):
                self._add_issue("P1", ch_num, "opening_scene", "开场场景缺失")
                ch["opening_scene"] = "承接上章结尾"
                self._add_fix(f"Ch{ch_num}: 自动填充 opening_scene")

            if not intensity or str(intensity).strip() in ("", "?") :
                self._add_issue("P0", ch_num, "conflict_intensity", "冲突强度未填写")
                ch["conflict_intensity"] = min(3 + i, 5)  # 默认递增
                self._add_fix(f"Ch{ch_num}: 自动填充 conflict_intensity={ch['conflict_intensity']}")

    def _check_bridge_cause_alignment(self, chapters: list):
        """检查 N章的bridge_to_next 与 N+1章的cause_from_prev 是否对齐"""
        for i in range(len(chapters) - 1):
            ch_a = chapters[i]
            ch_b = chapters[i + 1]
            num_a = int(ch_a.get("number", i + 1))
            num_b = int(ch_b.get("number", i + 2))
            
            bridge = str(ch_a.get("bridge_to_next", "")).strip()
            cause = str(ch_b.get("cause_from_prev", "")).strip()

            if not bridge or not cause:
                continue

            # 检测完全不一致的情况：bridge描述的事件与cause描述的事件不同
            # 简单语义检测——如果bridge和cause没有共同的关键词（排除虚词）
            bridge_keywords = self._extract_keywords(bridge)
            cause_keywords = self._extract_keywords(cause)
            
            if bridge_keywords and cause_keywords:
                overlap = bridge_keywords & cause_keywords
                if not overlap:
                    self._add_issue(
                        "P0", num_b, "cause_from_prev↔bridge_to_next",
                        f"对齐失败: Ch{num_a} bridge='{bridge[:40]}' 与 Ch{num_b} cause='{cause[:40]}' 共同关键词=0"
                    )
                    # 自动修补：用bridge改写cause
                    ch_b["cause_from_prev"] = bridge.replace("导致下章必须", "因为")
                    self._add_fix(f"Ch{num_b}: cause_from_prev 从 Ch{num_a} bridge 自动对齐")
    
    def _check_conflict_progression(self, chapters: list):
        """检查冲突强度递进"""
        prev_intensity = 0
        low_count = 0
        for i, ch in enumerate(chapters):
            ch_num = int(ch.get("number", i + 1))
            try:
                intensity = int(ch.get("conflict_intensity", 0))
            except (ValueError, TypeError):
                intensity = 0
            
            if intensity <= 0:
                continue
            
            if i > 0 and intensity < prev_intensity - 1:  # 允许降1级（缓冲章）
                self._add_issue(
                    "P0", ch_num, "conflict_intensity",
                    f"冲突强度下跌: Ch{ch_num-1}={prev_intensity} → Ch{ch_num}={intensity} (降压超过1级)"
                )
                ch["conflict_intensity"] = max(intensity, prev_intensity - 1)
                self._add_fix(f"Ch{ch_num}: conflict_intensity {intensity}→{ch['conflict_intensity']}")
            elif i > 2 and intensity <= low_count:
                # 连续3章都在低强度，警告
                self._add_issue("P1", ch_num, "conflict_intensity",
                    f"连续{low_count+1}章冲突强度≤{intensity}，可能读者失去兴趣")
            
            if intensity <= prev_intensity:
                low_count += 1
            else:
                low_count = 0
            
            prev_intensity = intensity
    
    # ═══════════════════════════════════════════
    # P1 检查
    # ═══════════════════════════════════════════

    def _check_opening_derivability(self, chapters: list):
        """检查 opening_scene 能否从上一章推导"""
        for i in range(1, len(chapters)):
            ch_a = chapters[i - 1]
            ch_b = chapters[i]
            num_b = int(ch_b.get("number", i + 1))
            
            prev_summary = ch_a.get("summary", "")
            prev_hook = ch_a.get("hook", "")
            opening = ch_b.get("opening_scene", "")
            
            if not opening or not prev_summary:
                continue
            
            # 检测：opening_scene 是否与上一章 summary/hook 有明显矛盾
            # 比如: 上一章在山洞，opening说在集市 → 标记
            prev_locations = self._extract_locations(prev_summary + prev_hook)
            opening_locations = self._extract_locations(opening)
            
            if prev_locations and opening_locations:
                overlap = prev_locations & opening_locations
                if not overlap and len(prev_locations) >= 1:
                    self._add_issue(
                        "P1", num_b, "opening_scene",
                        f"场景跳转: 上章场景={prev_locations}，本章开场场景={opening_locations}，无共同地点"
                    )
                    
    def _check_causal_quality(self, chapters: list):
        """检查因果链的语义质量——是否太模糊"""
        fuzzy_patterns = [
            r"后续发展",
            r"引出下[一壹]章",
            r"承接上[一文]",
            r"故事继续",
            r"事件发酵",
        ]
        for i, ch in enumerate(chapters):
            ch_num = int(ch.get("number", i + 1))
            cause = str(ch.get("cause_from_prev", ""))
            bridge = str(ch.get("bridge_to_next", ""))
            
            for pat in fuzzy_patterns:
                if re.search(pat, cause) and len(cause) < 20:
                    self._add_issue("P1", ch_num, "cause_from_prev",
                        f"因果链过于模糊: '{cause[:50]}'。应该具体说明X→Y的因果关系。")
                    break
            
            for pat in fuzzy_patterns:
                if re.search(pat, bridge) and len(bridge) < 20:
                    self._add_issue("P1", ch_num, "bridge_to_next",
                        f"引出字段过于模糊: '{bridge[:50]}'。应该具体说明Z→W的因果。")
                    break
    
    # ═══════════════════════════════════════════
    # P2 检查
    # ═══════════════════════════════════════════

    def _check_conflict_diversity(self, chapters: list):
        """检查冲突类型多样性"""
        types = []
        for ch in chapters:
            conflict = str(ch.get("conflict", ""))
            for t in ["IN", "IR", "EN", "DE"]:
                if t in conflict:
                    types.append(t)
                    break
            else:
                types.append("??")
        
        # 连续4章同一冲突类型 → 警告
        for i in range(len(types) - 3):
            if len(set(types[i:i+4])) == 1:
                self._add_issue("P2", int(chapters[i+3].get("number", i+4)), "conflict",
                    f"连续4章冲突类型为 {types[i]}，读者可能感到单调")
                break
    
    # ═══════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════

    def _extract_keywords(self, text: str) -> set:
        """从文本中提取关键名词/动词（1-6字词组）"""
        # 去掉虚词和标记
        text = re.sub(r'[，。！？、\s→导致下章必须因为上章所以本章承接]', ' ', text)
        words = []
        for w in text.split():
            w = w.strip()
            if 1 <= len(w) <= 6:
                words.append(w)
        return set(words)

    def _extract_locations(self, text: str) -> set:
        """从文本中提取可能的场景位置"""
        # 常见场景词汇
        loc_patterns = r'(山[洞谷巅]|[集街]市|宫殿|城[池堡]|村[落庄]|森林|沙漠|海边|酒楼|客[栈店]|学院|宗门|洞府|密室|广场|郊外|屋顶|塔[楼顶]|[船舰]上|地[牢下])'
        locations = set(re.findall(loc_patterns, text))
        # 也提取"在XX"中的XX
        at_patterns = re.findall(r'在([\u4e00-\u9fff]{2,4})[内中里外前后旁]', text)
        locations.update(at_patterns)
        return locations

    def _add_issue(self, severity: str, chapter: int, field: str, message: str):
        self.issues.append({
            "severity": severity,
            "chapter": chapter,
            "field": field,
            "message": message,
        })

    def _add_fix(self, fix_text: str):
        self.fixes_applied.append(fix_text)


def validate_and_repair_outline(outline: dict, auto_fix: bool = True) -> dict:
    """便捷函数：验证并修复大纲
    
    Args:
        outline: plan["outline"]
        auto_fix: 是否自动修补 P0 错误
    
    Returns:
        {"outline": 修复后的大纲, "report": 验证报告}
    """
    validator = CoherenceValidator()
    report = validator.validate(outline)
    
    if not report["passed"] and auto_fix:
        # P0 错误已在 validate 中自动修补
        # 再次验证确认
        report2 = validator.validate(outline)
        log.info(f"CoherenceValidator auto-fix: {len(validator.fixes_applied)} fixes, "
                f"score {report['score']}→{report2['score']}")
        report = report2
    
    return {
        "outline": outline,
        "report": report,
    }
