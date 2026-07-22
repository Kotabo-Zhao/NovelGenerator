"""NovelGenerator — Opening Optimizer: 开头吸引力优化 Agent

职责: 分析并优化小说开头，确保具备强吸引力，能迅速抓住读者注意力。
覆盖:
- 第一章开头强度评分
- 每章开头承接质量
- 钩子类型识别与优化
- 开篇句子冲击力分析
- AI味开头检测与替换
"""

import json
import re
import logging
from typing import Optional
from openai import OpenAI

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# 开头钩子类型分类
# ═══════════════════════════════════════════

HOOK_TYPES = {
    "action": {
        "name": "动作冲击型",
        "description": "以剧烈动作/战斗/危机开场，瞬间抓住注意力",
        "strength": 9,
        "examples": [
            "剑尖离他喉咙只有三寸。",
            "血从他指缝间渗出来，一滴，两滴。",
            "那道雷劈下来的时候，他正在煮面。",
        ],
        "keywords": ["轰", "炸", "碎", "血", "剑", "刀", "杀", "死", "飞", "落", "掉", "破"],
    },
    "mystery": {
        "name": "悬念牵引型",
        "description": "以谜题/异常现象开场，引发读者好奇心",
        "strength": 8,
        "examples": [
            "桌子上多了一封信。没有署名，没有邮戳——但它就在那里。",
            "所有人都说他已经死了。所以当他推开门走进来时，整个大厅安静了三秒钟。",
            "她每天早上醒来，都会发现床头多一枝花。但她独居在十八楼。",
        ],
        "keywords": ["为什么", "怎么", "谁", "竟然", "居然", "突然", "奇怪", "不可能是"],
    },
    "dialogue": {
        "name": "对话切入型",
        "description": "以一句有冲击力的对话开场，立刻进入场景",
        "strength": 7,
        "examples": [
            "「你杀不了我。」少年抬起头，嘴角还有血。",
            "「师父，你骗了我二十年。」",
            "「跪下。」那个声音很轻，但方圆百丈内所有人膝盖都弯了。",
        ],
        "keywords": ["「", "」", "\"", "\"", "说", "道", "问"],
    },
    "emotional": {
        "name": "情感共鸣型",
        "description": "以强烈情感开场，让读者产生共情",
        "strength": 7,
        "examples": [
            "母亲走的那天，他没有哭。他只是把那碗没吃完的面，放进了冰箱。放了十年。",
            "她记得那个下午的每一秒。阳光的角度，风的味道，他说那句话时的口型。",
        ],
        "keywords": ["哭", "笑", "痛", "恨", "爱", "记得", "想起"],
    },
    "environment": {
        "name": "氛围渲染型",
        "description": "以独特环境/氛围开场（风险：容易AI味重）",
        "strength": 5,
        "examples": [],
        "keywords": ["天空", "阳光", "风", "雨", "雪", "雾", "月光"],
        "warning": "AI高频开头模式，需谨慎使用，除非渲染极具特色",
    },
    "philosophical": {
        "name": "哲思切入型",
        "description": "以一句思想性强的句子开场（仅适合特定风格）",
        "strength": 4,
        "examples": [
            "人这一生，总要为一件事不顾一切。他的这件事，来得比所有人都早。",
        ],
        "keywords": ["人生", "命运", "生命", "时间", "世界"],
        "warning": "非猫腻/priest风格慎用，容易显得说教",
    },
}


class OpeningOptimizer:
    """开头吸引力优化 Agent"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model

    # ═══════════════════════════════════════════
    # 公开接口
    # ═══════════════════════════════════════════

    def analyze_opening(
        self,
        chapter_text: str,
        chapter_num: int,
        style: str = "热血爽文",
        is_first_chapter: bool = False,
    ) -> dict:
        """分析开头质量
        
        Args:
            chapter_text: 章节正文
            chapter_num: 章节号
            style: 目标风格
            is_first_chapter: 是否全书第一章（要求更高）
            
        Returns:
            {
                "score": 0-100,
                "hook_type": str,
                "hook_strength": 1-10,
                "first_sentence_impact": 1-10,
                "issues": [...],
                "suggestions": [...],
                "alternative_openings": [...]  # 可选替代开头
            }
        """
        opening = self._extract_opening(chapter_text, words=300)
        first_sentence = self._get_first_sentence(chapter_text)
        first_para = self._get_first_paragraph(chapter_text)
        
        issues = []
        suggestions = []
        
        # ── 本地分析 ──
        local = self._local_analysis(opening, first_sentence, first_para, style, is_first_chapter)
        
        hook_type = local["hook_type"]
        hook_strength = local["hook_strength"]
        first_impact = local["first_sentence_impact"]
        issues = local["issues"]
        suggestions = local["suggestions"]
        
        # ── 综合评分 ──
        score = self._calculate_score(hook_strength, first_impact, len(issues), is_first_chapter)
        
        return {
            "score": score,
            "hook_type": hook_type,
            "hook_type_name": HOOK_TYPES.get(hook_type, {}).get("name", "未知"),
            "hook_strength": hook_strength,
            "first_sentence_impact": first_impact,
            "first_sentence": first_sentence,
            "issues": issues,
            "suggestions": suggestions,
            "opening_sample": opening[:200],
        }

    def generate_alternatives(
        self,
        chapter_text: str,
        chapter_num: int,
        plan: dict,
        style: str = "热血爽文",
        count: int = 3,
    ) -> list:
        """生成替代开头
        
        Args:
            chapter_text: 当前开头
            plan: 小说规划数据
            style: 目标风格
            count: 生成几个替代方案
            
        Returns:
            [{type, text, strength, explanation}]
        """
        if not self.client or not self.model:
            return self._local_alternatives(chapter_text, count)
        
        opening = self._extract_opening(chapter_text, 200)
        chapter_outline = self._find_chapter_outline(plan, chapter_num)
        genre = plan.get("genre", "玄幻")
        
        outline_text = ""
        if chapter_outline:
            outline_text = f"本章核心事件: {chapter_outline.get('summary', '')}\n出场角色: {chapter_outline.get('characters', [])}"
        
        system = """你是小说开头优化专家。为一章生成多个高吸引力开头方案。

每个方案要求:
- 30-80字
- 直接用动作/对话/悬念开篇，不要环境描写
- 不同类型: 动作型、悬念型、对话型各一个
- 保持与原文相同的核心事件和角色

输出 JSON 数组:
```json
[
  {"type": "action", "text": "开头文本", "strength": 8, "explanation": "为什么这个开头有效"},
  {"type": "mystery", "text": "开头文本", "strength": 7, "explanation": "为什么这个开头有效"},
  {"type": "dialogue", "text": "开头文本", "strength": 7, "explanation": "为什么这个开头有效"}
]
```"""

        user = f"""小说信息:
- 题材: {genre}
- 风格: {style}
- {outline_text}

当前开头:
{opening}

请生成 {count} 个替代开头方案。只输出JSON。"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.8,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            if isinstance(result, dict):
                result = result.get("alternatives", result.get("openings", []))
            return result[:count] if isinstance(result, list) else []
        except Exception as e:
            log.error(f"OpeningOptimizer alternatives failed: {e}")
            return self._local_alternatives(chapter_text, count)

    def build_optimization_prompt(self, analysis: dict) -> str:
        """将开头分析结果转为 Writer 可用的优化提示"""
        if analysis.get("score", 100) >= 80:
            return ""
        
        parts = ["## 🎯 开头优化要求\n"]
        
        issues = analysis.get("issues", [])
        if issues:
            parts.append("### 当前问题")
            for i, issue in enumerate(issues[:5]):
                parts.append(f"{i+1}. {issue}")
        
        suggestions = analysis.get("suggestions", [])
        if suggestions:
            parts.append("\n### 优化方向")
            for i, s in enumerate(suggestions[:5]):
                parts.append(f"{i+1}. {s}")
        
        parts.append(f"\n### 具体要求")
        parts.append("- 开头第一句必须用动作/对话/悬念，不得用环境描写")
        parts.append("- 前100字内出现冲突或悬念")
        parts.append("- 去AI味：避免'阳光''天空''大地'等高频环境词开篇")
        
        return "\n".join(parts)

    # ═══════════════════════════════════════════
    # 内部分析
    # ═══════════════════════════════════════════

    def _extract_opening(self, text: str, words: int = 300) -> str:
        """提取开头N字"""
        if len(text) <= words:
            return text
        return text[:words]

    def _get_first_sentence(self, text: str) -> str:
        """获取第一句话"""
        for delim in ["。", "！", "？", "……", "\n"]:
            idx = text.find(delim)
            if idx > 0:
                return text[:idx+1].strip()
        return text[:50].strip()

    def _get_first_paragraph(self, text: str) -> str:
        """获取第一段"""
        idx = text.find("\n\n")
        if idx > 0:
            return text[:idx].strip()
        # 取前100字
        return text[:100].strip()

    def _local_analysis(self, opening, first_sentence, first_para, style, is_first_chapter):
        """本地规则分析"""
        issues = []
        suggestions = []
        
        # 1. 检测开头类型
        hook_type = "environment"  # 默认
        hook_strength = 5
        first_impact = 5
        
        # 动作型检测
        action_kw = ["轰", "炸", "碎", "血", "剑", "刀", "杀", "死", "飞", "落", "掉", "破", "撞", "劈"]
        if any(kw in first_sentence for kw in action_kw):
            hook_type = "action"
            hook_strength = 9
            first_impact = 9
        # 对话型检测
        elif any(q in first_sentence for q in ["「", "\"", "「"]):
            hook_type = "dialogue"
            hook_strength = 7
            first_impact = 8
        # 悬念型检测
        elif any(kw in opening[:100] for kw in ["突然", "竟然", "居然", "为什么", "奇怪", "不可能是", "没有"]):
            hook_type = "mystery"
            hook_strength = 8
            first_impact = 8
        # 环境型检测
        elif any(kw in first_sentence for kw in ["阳光", "天空", "风", "月光", "大地", "云", "星"]):
            hook_type = "environment"
            hook_strength = 5
            first_impact = 4
            issues.append("以环境描写开篇 — AI高频模式，难以抓住读者注意力")
            suggestions.append("替换为动作/对话/悬念开篇")
        
        # 2. AI味检测
        ai_openings = [
            (r"^(?:阳光|月光|天空|大地|风|雨|雪|雾|云)", "环境描写开篇（AI高频）"),
            (r"^(?:在这|那是|这是).{0,20}(?:世界|大陆|时代|天地)", "模板化世界观介绍开篇"),
            (r"^(?:众所周知|自古以来|相传|传说).{0,30}", "百科式开篇"),
            (r"^.{0,30}(?:平静|宁静|安静|祥和|繁华)", "万能形容词开篇"),
            (r"^(?:少年|少女|青年|男子|女子).{0,10}(?:名叫|叫做|姓)", "人物介绍式开篇"),
            (r"^(?:随着|伴随着|自从|自从……以来)", "通用过渡词开篇"),
        ]
        
        for pattern, desc in ai_openings:
            if re.search(pattern, opening[:100]):
                issues.append(f"AI味开篇: {desc}")
                if not suggestions:
                    suggestions.append("用具体动作或悬念句替代模板化开篇")
        
        # 3. 第一句冲击力分析
        if len(first_sentence) > 50:
            first_impact = max(3, first_impact - 2)
            issues.append("第一句话过长（>50字），削弱冲击力")
            suggestions.append("第一句控制在15-30字")
        
        if len(first_sentence) < 5:
            first_impact = max(4, first_impact - 1)
        
        # 4. 第一章特殊要求
        if is_first_chapter:
            if hook_strength < 7:
                issues.append("全书第一章开头强度不足（需要≥7分）")
                suggestions.append("第一章必须用高冲击力动作/强悬念开篇")
            # 检查是否在前200字内出现了冲突
            conflict_kw = ["但是", "然而", "却", "可", "不料", "谁知", "忽然", "突然", "竟"]
            if not any(kw in opening[:200] for kw in conflict_kw):
                issues.append("第一章前200字内没有冲突/转折信号")
                suggestions.append("在前200字内埋入冲突暗示或意外转折")
        
        return {
            "hook_type": hook_type,
            "hook_strength": hook_strength,
            "first_sentence_impact": first_impact,
            "issues": issues,
            "suggestions": suggestions,
        }

    def _calculate_score(self, hook_strength, first_impact, issue_count, is_first_chapter):
        """综合评分"""
        base = (hook_strength + first_impact) * 5
        penalty = issue_count * 8
        if is_first_chapter:
            penalty *= 1.5  # 第一章标准更严
        return max(0, min(100, int(base - penalty)))

    def _find_chapter_outline(self, plan, chapter_num):
        """查找章节大纲"""
        for vol in plan.get("outline", {}).get("volumes", []):
            for ch in vol.get("chapters", []):
                if int(ch.get("number", 0)) == chapter_num:
                    return ch
        return None

    def _local_alternatives(self, chapter_text, count):
        """无LLM时的本地替代方案生成（基于模板）"""
        alternatives = []
        templates = [
            {
                "type": "action",
                "text": "（请配置LLM以生成个性化替代开头）",
                "strength": 0,
                "explanation": "需要API支持",
            }
        ]
        return alternatives * min(count, 1)
