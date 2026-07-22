"""NovelGenerator — Interactive Outline Engine: 交互式大纲生成

职责: 实现大纲交互式生成，支持结构化反馈和多轮迭代。
- 结构化反馈收集（非自由文本）
- 反馈分类与影响分析
- 针对性重生成（只改受影响的章节）
- 迭代历史追踪
- 变化差异输出
"""

import json
import copy
import logging
from typing import AsyncIterator, Optional
from openai import OpenAI

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# 反馈分类体系
# ═══════════════════════════════════════════

FEEDBACK_CATEGORIES = {
    "pacing": {
        "name": "节奏调整",
        "description": "调整章节密度、高潮分布、张弛比例",
        "impact_scope": "volume",  # 影响范围: chapter/volume/global
        "examples": ["节奏太慢，前10章都在铺垫", "高潮太密集，没有喘息空间"],
    },
    "character_arc": {
        "name": "角色弧线",
        "description": "调整主角/配角成长轨迹",
        "impact_scope": "global",
        "examples": ["主角升级太快", "反派动机不够充分", "配角存在感太弱"],
    },
    "plot_logic": {
        "name": "情节逻辑",
        "description": "修复情节漏洞、增强因果链",
        "impact_scope": "volume",
        "examples": ["第15章和第20章事件矛盾", "第三卷的转折缺乏铺垫"],
    },
    "structure": {
        "name": "结构调整",
        "description": "调整卷/章结构、三幕比例",
        "impact_scope": "global",
        "examples": ["第一卷太长", "第三幕只有两章", "需要增加一卷过渡"],
    },
    "conflict": {
        "name": "冲突升级",
        "description": "调整冲突强度和逐级加码",
        "impact_scope": "volume",
        "examples": ["冲突太弱不够刺激", "敌人太强主角没法打"],
    },
    "hooks": {
        "name": "钩子/悬念",
        "description": "调整章末钩子和悬念密度",
        "impact_scope": "chapter",
        "examples": ["连续三章没有钩子", "钩子太雷同"],
    },
    "ending": {
        "name": "结局设计",
        "description": "调整高潮/结局设计",
        "impact_scope": "global",
        "examples": ["结局太俗套", "大决战不够震撼"],
    },
}


class OutlineInteractive:
    """交互式大纲引擎"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model
        self._iteration_history = []  # 迭代历史

    # ═══════════════════════════════════════════
    # 公开接口
    # ═══════════════════════════════════════════

    def parse_feedback(self, feedback: str) -> dict:
        """解析用户反馈为结构化修改指令
        
        Args:
            feedback: 用户自由文本反馈
            
        Returns:
            {
                "categories": ["pacing", "plot_logic"],
                "instructions": [
                    {category, instruction, scope, target_chapters, action}
                ],
                "original_feedback": str
            }
        """
        instructions = []
        categories = set()
        
        feedback_lower = feedback.lower()
        
        # ── 关键词匹配分类 ──
        category_keywords = {
            "pacing": ["节奏", "太快", "太慢", "密度", "高潮分布", "张弛", "章节数量", "太密集", "太稀疏"],
            "character_arc": ["主角", "角色", "成长", "升级", "弧线", "反派", "配角", "人物", "性格"],
            "plot_logic": ["矛盾", "不合理", "逻辑", "漏洞", "冲突", "因果", "铺垫", "说不通"],
            "structure": ["卷", "结构", "比例", "三幕", "太长", "太短", "分卷", "布局"],
            "conflict": ["冲突", "强度", "不够刺激", "太弱", "太强", "敌人", "对手"],
            "hooks": ["钩子", "悬念", "结尾", "章末", "期待感", "好奇"],
            "ending": ["结局", "高潮", "决战", "收尾", "完结"],
        }
        
        for cat, keywords in category_keywords.items():
            if any(kw in feedback for kw in keywords):
                categories.add(cat)
        
        # 如果没有明确分类，归为通用修改
        if not categories:
            categories.add("plot_logic")
        
        # ── 提取目标章节范围 ──
        target_chapters = self._extract_chapter_range(feedback)
        
        # ── 生成结构化指令 ──
        for cat in categories:
            cat_info = FEEDBACK_CATEGORIES.get(cat, FEEDBACK_CATEGORIES["plot_logic"])
            instructions.append({
                "category": cat,
                "category_name": cat_info["name"],
                "instruction": feedback.strip(),
                "scope": cat_info["impact_scope"],
                "target_chapters": target_chapters,
                "action": "regenerate" if cat_info["impact_scope"] != "chapter" else "modify",
            })
        
        return {
            "categories": list(categories),
            "instructions": instructions,
            "original_feedback": feedback,
        }

    async def regenerate_with_feedback(
        self,
        plan: dict,
        parsed_feedback: dict,
        planner,  # Planner instance (for LLM calls)
    ) -> AsyncIterator[dict]:
        """根据结构化反馈重新生成大纲
        
        核心原则:
        1. 最小化修改 — 只重生成被反馈影响的章节
        2. 保持一致性 — 修改后的部分与未修改部分连贯
        3. 反馈驱动 — 严格按用户指令修改，不自作主张
        
        Yields:
            {"type": "progress", "phase": "analyze|regenerate|merge|done", "pct": 0-100, "label": ""}
            {"type": "diff", "changes": [{section, before, after, reason}]}
            {"type": "done", "plan": updated_plan}
        """
        instructions = parsed_feedback.get("instructions", [])
        if not instructions:
            yield {"type": "error", "message": "未能解析出有效修改指令"}
            return
        
        # Phase 1: 影响分析
        yield {"type": "progress", "phase": "analyze", "pct": 10, "label": "分析修改影响范围…"}
        
        changes = []
        new_plan = copy.deepcopy(plan)
        old_outline = copy.deepcopy(plan.get("outline", {}))
        
        for inst in instructions:
            scope = inst["scope"]
            target = inst["target_chapters"]
            cat = inst["category"]
            
            yield {"type": "progress", "phase": "analyze", "pct": 20, 
                   "label": f"处理「{inst['category_name']}」修改…"}
            
            if scope == "global":
                # 全量重生成大纲
                yield {"type": "progress", "phase": "regenerate", "pct": 40,
                       "label": "全局重新规划大纲…"}
                
                new_outline = await self._regenerate_global_outline(
                    plan, inst["instruction"], planner
                )
                if new_outline:
                    new_plan["outline"] = new_outline
                    changes.append({
                        "section": "outline",
                        "before": f"原大纲({old_outline.get('total_chapters', 0)}章)",
                        "after": f"新大纲({new_outline.get('total_chapters', 0)}章)",
                        "reason": f"全局调整: {inst['category_name']}",
                    })
            
            elif scope == "volume":
                # 指定卷重生成
                volumes = new_plan.get("outline", {}).get("volumes", [])
                target_vols = self._find_affected_volumes(volumes, target)
                
                for vol_idx in target_vols:
                    vol = volumes[vol_idx]
                    vol_num = vol.get("number", vol_idx + 1)
                    yield {"type": "progress", "phase": "regenerate", "pct": 50 + 20 * vol_idx / max(1, len(target_vols)),
                           "label": f"重规划第{vol_num}卷…"}
                    
                    new_vol = await self._regenerate_volume(
                        plan, vol, vol_num, inst["instruction"], planner
                    )
                    if new_vol:
                        volumes[vol_idx] = new_vol
                        changes.append({
                            "section": f"第{vol_num}卷",
                            "before": f"{len(vol.get('chapters',[]))}章",
                            "after": f"{len(new_vol.get('chapters',[]))}章",
                            "reason": inst["category_name"],
                        })
                
                # 更新总章节数
                total = sum(len(v.get("chapters", [])) for v in volumes)
                new_plan["outline"]["total_chapters"] = total
            
            elif scope == "chapter":
                # 指定章节修改
                volumes = new_plan.get("outline", {}).get("volumes", [])
                for ch_num in (target or []):
                    for vol in volumes:
                        for ch in vol.get("chapters", []):
                            if int(ch.get("number", 0)) == ch_num:
                                ch["_modified"] = True
                                ch["_modification"] = inst["instruction"]
                                changes.append({
                                    "section": f"第{ch_num}章",
                                    "before": ch.get("summary", ""),
                                    "after": f"待Writer重写: {inst['category_name']}",
                                    "reason": inst["category_name"],
                                })
        
        # Phase 3: 一致性校验
        yield {"type": "progress", "phase": "merge", "pct": 85, "label": "校验章节连续性…"}
        
        # 重新编号确保连续
        self._renumber_chapters(new_plan)
        
        # Phase 4: 保存迭代记录
        yield {"type": "progress", "phase": "merge", "pct": 95, "label": "保存迭代历史…"}
        
        self._iteration_history.append({
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "feedback": parsed_feedback.get("original_feedback", ""),
            "parsed": parsed_feedback,
            "changes": changes,
            "chapter_count_before": old_outline.get("total_chapters", 0),
            "chapter_count_after": new_plan.get("outline", {}).get("total_chapters", 0),
        })
        
        # 输出差异
        if changes:
            yield {"type": "diff", "changes": changes}
        
        yield {"type": "progress", "phase": "done", "pct": 100, "label": "大纲已更新！"}
        yield {"type": "done", "plan": new_plan}

    def get_iteration_history(self) -> list:
        """获取迭代历史"""
        return self._iteration_history

    def get_diff_summary(self, old_plan: dict, new_plan: dict) -> list:
        """比较两个大纲版本的变化"""
        changes = []
        
        old_outline = old_plan.get("outline", {})
        new_outline = new_plan.get("outline", {})
        
        old_total = old_outline.get("total_chapters", 0)
        new_total = new_outline.get("total_chapters", 0)
        
        if old_total != new_total:
            changes.append({
                "type": "chapter_count",
                "before": old_total,
                "after": new_total,
                "delta": new_total - old_total,
                "description": f"章节总数 {'增加' if new_total > old_total else '减少'}{abs(new_total - old_total)}章",
            })
        
        # 比较每卷章节数
        old_vols = old_outline.get("volumes", [])
        new_vols = new_outline.get("volumes", [])
        
        for i, (ov, nv) in enumerate(zip(old_vols, new_vols)):
            old_ch_count = len(ov.get("chapters", []))
            new_ch_count = len(nv.get("chapters", []))
            if old_ch_count != new_ch_count:
                changes.append({
                    "type": "volume_chapter_count",
                    "volume": ov.get("title", f"第{i+1}卷"),
                    "before": old_ch_count,
                    "after": new_ch_count,
                    "delta": new_ch_count - old_ch_count,
                })
        
        # 章节概要变化
        old_ch_map = {}
        for vol in old_vols:
            for ch in vol.get("chapters", []):
                old_ch_map[int(ch.get("number", 0))] = ch.get("summary", "")
        
        for vol in new_vols:
            for ch in vol.get("chapters", []):
                ch_num = int(ch.get("number", 0))
                new_summary = ch.get("summary", "")
                old_summary = old_ch_map.get(ch_num, "")
                if ch.get("_modified") or (old_summary and old_summary != new_summary):
                    changes.append({
                        "type": "chapter_changed",
                        "chapter": ch_num,
                        "before": old_summary[:50],
                        "after": new_summary[:50],
                    })
        
        return changes

    # ═══════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════

    async def _regenerate_global_outline(self, plan, instruction, planner):
        """完全重生成大纲（保留世界观和角色）"""
        genre = plan.get("genre", "玄幻")
        style = plan.get("style", "热血爽文")
        target_words = plan.get("target_words", 500000)
        wb = plan.get("worldbuilding", {})
        chars = plan.get("characters", {})
        
        prompt = f"""你是一位小说大纲规划师。根据以下设定重新生成完整章节大纲。

已有世界观: {json.dumps(wb, ensure_ascii=False)[:400]}
已有角色: {json.dumps(chars, ensure_ascii=False)[:300]}
题材: {genre}  风格: {style}  总目标: {target_words}字

修改指令: {instruction}

【核心要求】
1. 严格按照修改指令调整大纲，不作无关改动
2. 保留世界观和角色体系不变
3. 确保修改后的章节之间有逻辑连贯性
4. 每章摘要控制在30字内

只输出JSON:
```json
{{"volumes":[{{"number":1,"title":"","act":"第一幕·建置","theme":"","act_function":"","chapters":[{{"number":1,"title":"","summary":"","emotion_curve":"","conflict":"","characters":[""],"hook":"","target_words":3000}}]}}],"total_chapters":0,"three_act_map":"","rhythm_notes":""}}
```"""

        try:
            result = await planner._call_llm(prompt, "outline_regenerate_global", max_tokens=16384)
            if result:
                # 标准化
                for vol in result.get("volumes", []):
                    vol["number"] = int(vol.get("number", 1))
                    for ch in vol.get("chapters", []):
                        ch["number"] = int(ch.get("number", 1))
                result["total_chapters"] = sum(len(v.get("chapters", [])) for v in result.get("volumes", []))
                return result
        except Exception as e:
            log.error(f"Global outline regeneration failed: {e}")
        return None

    async def _regenerate_volume(self, plan, vol, vol_num, instruction, planner):
        """重生成单卷大纲"""
        genre = plan.get("genre", "玄幻")
        style = plan.get("style", "热血爽文")
        wb = plan.get("worldbuilding", {})
        chars = plan.get("characters", {})
        
        chapter_count = len(vol.get("chapters", []))
        vol_theme = vol.get("theme", "")
        vol_act = vol.get("act", "")
        
        prompt = f"""你是小说大纲规划师。根据修改指令重新规划第{vol_num}卷大纲。

卷信息: 第{vol_num}卷「{vol.get('title','')}」, {vol_act}, 主题={vol_theme}
题材: {genre}  风格: {style}
修改指令: {instruction}

【要求】
1. 保留{chapter_count}章左右的结构（除非指令要求增减）
2. 保持与前后卷的逻辑衔接
3. 每章摘要控制在30字内

输出JSON数组:
```json
[{{"number":章节号,"title":"","summary":"","emotion_curve":"","conflict":"","characters":[""],"hook":"","target_words":3000}}]
```"""

        try:
            result = await planner._call_llm(prompt, f"outline_regenerate_vol{vol_num}", max_tokens=4096)
            if result:
                chapters = result if isinstance(result, list) else result.get("chapters", [])
                new_vol = copy.deepcopy(vol)
                new_vol["chapters"] = chapters
                return new_vol
        except Exception as e:
            log.error(f"Volume {vol_num} regeneration failed: {e}")
        return None

    def _extract_chapter_range(self, feedback: str) -> list:
        """从反馈中提取目标章节范围"""
        # 提取明确数字
        numbers = re.findall(r"第\s*(\d+)\s*(?:章|卷)", feedback)
        if numbers:
            return [int(n) for n in numbers]
        
        # 提取范围 (如 "第5-10章")
        range_match = re.findall(r"第?\s*(\d+)\s*(?:-|到|至)\s*(\d+)\s*章", feedback)
        if range_match:
            start, end = range_match[0]
            return list(range(int(start), int(end) + 1))
        
        return []

    def _find_affected_volumes(self, volumes: list, target_chapters: list) -> list:
        """找到受影响的卷索引"""
        if not target_chapters:
            return list(range(len(volumes)))
        
        affected = set()
        for ch_num in target_chapters:
            for i, vol in enumerate(volumes):
                for ch in vol.get("chapters", []):
                    if int(ch.get("number", 0)) == ch_num:
                        affected.add(i)
        return sorted(affected) if affected else list(range(len(volumes)))

    def _renumber_chapters(self, plan: dict):
        """确保章节号连续"""
        counter = 0
        for vol in plan.get("outline", {}).get("volumes", []):
            for ch in vol.get("chapters", []):
                counter += 1
                ch["number"] = counter
        plan["outline"]["total_chapters"] = counter
