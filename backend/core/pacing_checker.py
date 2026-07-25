"""NovelGenerator — Pacing Checker: 节奏控制师 Agent

职责: 分析章节节奏质量，给出量化分数和改进建议
参考: snowflake-subagents 的节奏控制师 + StoryScope 的叙事特征分析
"""

import re
import json
import logging
from openai import OpenAI

log = logging.getLogger(__name__)

PACING_SYSTEM = """你是一位专业的节奏控制师，专精于分析小说章节的叙事节奏。

## 分析维度

1. **高潮密度**: 情感峰值的分布是否合理（不应全程高压，也不应全程平淡）
2. **张弛比**: 紧张段落与舒缓段落的比例（理想为 6:4 到 7:3）
3. **段落节奏**: 段落长度变化、句式交替（长句抒情 vs 短句紧张）
4. **场景切换频率**: 同一场景持续太久会疲劳，切换太快会碎片化
5. **信息密度**: 每段是否推进了剧情或塑造了角色（无"水字数"段落）

## 输出格式

返回 JSON:
```json
{
  "overall_score": 0-100,
  "climax_density": {"score": 0-100, "note": "说明"},
  "tension_ratio": {"score": 0-100, "note": "说明"},
  "paragraph_rhythm": {"score": 0-100, "note": "说明"},
  "scene_transitions": {"score": 0-100, "note": "说明"},
  "info_density": {"score": 0-100, "note": "说明"},
  "issues": ["节奏问题1", "节奏问题2"],
  "suggestions": ["改进建议1", "改进建议2"]
}
```

只输出 JSON，不要其他内容。"""


class PacingChecker:
    """节奏控制师 — 独立检测章节节奏"""

    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def analyze(self, chapter_text: str, chapter_num: int) -> dict:
        """对章节进行节奏分析
        
        两步: ① 本地统计量化 ② LLM 节奏评价
        """
        stats = self._local_stats(chapter_text)
        
        # 截取章节进行 LLM 分析 (前3000字)
        snippet = chapter_text[:3000] if len(chapter_text) > 3000 else chapter_text
        
        user_prompt = f"""请分析第{chapter_num}章的叙事节奏。

本地统计数据:
- 总字数: {stats['word_count']}
- 段落数: {stats['paragraph_count']}
- 平均段长: {stats['avg_paragraph_chars']} 字
- 短段比例(<=2句): {stats['short_para_ratio']}%
- 长段比例(>=5句): {stats['long_para_ratio']}%
- 句长方差: {stats['sentence_variance']}
- 对话比例: {stats['dialogue_ratio']}%
- 感叹号密度: {stats['exclamation_density']:.1f}/千字
- 破折号密度: {stats['dash_density']:.1f}/千字

章节内容片段:
{snippet[:2000]}

请输出 JSON 格式的节奏分析报告。"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PACING_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            result["_stats"] = stats
            log.info(f"PacingChecker: chapter {chapter_num} score={result.get('overall_score', '?')}")
            return result
        except Exception as e:
            log.error(f"PacingChecker failed: {e}")
            return {"overall_score": 0, "error": str(e), "_stats": stats}

    def _local_stats(self, text: str) -> dict:
        """本地统计：句长、段落、对话比、标点密度 + v2.6 对话深度检测"""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        para_count = len(paragraphs)
        
        # 段落统计
        short_paras = sum(1 for p in paragraphs if p.count("。") + p.count("！") + p.count("？") <= 2)
        long_paras = sum(1 for p in paragraphs if p.count("。") + p.count("！") + p.count("？") >= 5)
        
        # 句长统计
        sentences = re.split(r"[。！？……\n]", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 1]
        sent_lens = [len(s) for s in sentences] if sentences else [0]
        avg_sent = sum(sent_lens) / len(sent_lens)
        variance = sum((l - avg_sent) ** 2 for l in sent_lens) / len(sent_lens)
        
        # 对话比例
        dialogue_chars = len(re.findall(r"[「「""''][^」」""'']*[」」""'']", text))
        dialogue_ratio = round(dialogue_chars / max(len(text), 1) * 100)
        
        # ── v2.6: 对话深度分析 ──
        # 对话轮次计数（每次引号对=1轮）
        dialogue_pairs = re.findall(r'[「「""]([^」」""'']*)[」」""'']', text)
        dialogue_turn_count = len(dialogue_pairs)
        
        # 水对话检测：对话内容 ≤4字 = 灌水（"嗯""哦""知道了"等）
        water_turns = sum(1 for d in dialogue_pairs if len(d.strip()) <= 4)
        water_dialogue_ratio = round(water_turns / max(dialogue_turn_count, 1) * 100)
        
        # 最长连续对话段（连续的对话段落数）
        max_consecutive = 0
        current_streak = 0
        for p in paragraphs:
            has_dialogue = bool(re.search(r'[「「""]', p))
            has_action = bool(re.search(r'[。！？](?![""」」])', p))  # 句号后不紧跟引号
            if has_dialogue and not (has_action and len(p) > 30):
                current_streak += 1
                max_consecutive = max(max_consecutive, current_streak)
            else:
                current_streak = 0
        
        # 动作句比例（不以引号开头/结尾的句子）
        action_sentences = sum(1 for s in sentences if not re.match(r'^[「「""]', s) and not re.search(r'[」」""'']$', s))
        action_ratio = round(action_sentences / max(len(sentences), 1) * 100)
        
        # ── 爽点密度估算 ──
        # 爽点信号词：反转/揭露/碾压/冲突关键词
        shuangdian_signals = [
            r'突然|忽然|竟然|居然|怎么可能|没想到|原来',
            r'冷笑|不屑|碾压|秒杀|一招|瞬间',
            r'震惊|愣住|瞪大|呆住|说不出话',
            r'跪下|求饶|饶命|不敢|怕了',
            r'杀|死|血|剑|刀|掌',
        ]
        shuangdian_hits = sum(
            len(re.findall(pattern, text)) for pattern in shuangdian_signals
        )
        shuangdian_per_1000 = round(shuangdian_hits / max(len(text), 1) * 1000, 1)
        
        # 标点密度
        word_count = len(text)
        excl = text.count("！")
        dash = text.count("—")
        
        return {
            "word_count": word_count,
            "paragraph_count": para_count,
            "sentence_count": len(sentences),
            "avg_sentence_len": round(avg_sent, 1),
            "sentence_variance": round(variance, 1),
            "avg_paragraph_chars": round(word_count / max(para_count, 1)),
            "short_para_ratio": round(short_paras / max(para_count, 1) * 100),
            "long_para_ratio": round(long_paras / max(para_count, 1) * 100),
            "dialogue_ratio": dialogue_ratio,
            "exclamation_density": round(excl / max(word_count, 1) * 1000, 1),
            "dash_density": round(dash / max(word_count, 1) * 1000, 1),
            # v2.6
            "dialogue_turn_count": dialogue_turn_count,
            "water_dialogue_ratio": water_dialogue_ratio,
            "max_consecutive_dialogue": max_consecutive,
            "action_ratio": action_ratio,
            "shuangdian_per_1000": shuangdian_per_1000,
        }

    def build_pacing_prompt(self, result: dict) -> str:
        """将节奏分析结果转为 Writer 可用的改进提示"""
        if not result or result.get("overall_score", 0) >= 75:
            return ""
        
        suggestions = result.get("suggestions", [])
        issues = result.get("issues", [])
        
        if not suggestions and not issues:
            return ""
        
        parts = ["## 节奏改进建议\n"]
        if issues:
            parts.append("### 节奏问题")
            parts.extend(f"- {i}" for i in issues)
        if suggestions:
            parts.append("\n### 改进方向")
            parts.extend(f"- {s}" for s in suggestions)
        
        return "\n".join(parts)

    def quick_quality_check(self, text: str, fast_food: bool = False) -> dict:
        """快速质量检查（纯本地）。
        
        Args:
            text: 章节文本
            fast_food: 是否快餐模式（更严格的标准）"""
        stats = self._local_stats(text)
        issues = []
        score = 100
        
        # 快餐模式：更严格的阈值
        if fast_food:
            if stats["dialogue_ratio"] > 45: issues.append(f"快餐模式: 对话占比过高({stats['dialogue_ratio']}%)"); score -= 35
            elif stats["dialogue_ratio"] > 35: issues.append(f"快餐模式: 对话偏多({stats['dialogue_ratio']}%)"); score -= 20
            if stats["water_dialogue_ratio"] > 15: issues.append(f"快餐模式: 水对话过多({stats['water_dialogue_ratio']}%)"); score -= 25
            if stats["max_consecutive_dialogue"] > 4: issues.append(f"快餐模式: 连续对话{stats['max_consecutive_dialogue']}段"); score -= 20
            if stats["action_ratio"] < 35: issues.append(f"快餐模式: 动作不足({stats['action_ratio']}%)"); score -= 20
            if stats["shuangdian_per_1000"] < 8: issues.append(f"快餐模式: 爽点不足({stats['shuangdian_per_1000']}/千字)"); score -= 25
            if stats["avg_sentence_len"] < 8 and stats["sentence_count"] > 30: issues.append(f"快餐模式: 碎片化(均{stats['avg_sentence_len']}字)"); score -= 10
            if len(text) < 2500: issues.append(f"快餐模式: 字数不足({len(text)}字,需≥2500)"); score -= 15
        else:
            if stats["dialogue_ratio"] > 50: issues.append(f"对话占比过高({stats['dialogue_ratio']}%)"); score -= 30
            elif stats["dialogue_ratio"] > 40: issues.append(f"对话偏多({stats['dialogue_ratio']}%)"); score -= 15
            if stats["water_dialogue_ratio"] > 20: issues.append(f"水对话过多({stats['water_dialogue_ratio']}%)"); score -= 20
            if stats["max_consecutive_dialogue"] > 6: issues.append(f"连续对话{stats['max_consecutive_dialogue']}段"); score -= 15
            if stats["action_ratio"] < 25: issues.append(f"动作不足({stats['action_ratio']}%)"); score -= 15
            if stats["shuangdian_per_1000"] < 5: issues.append(f"爽点不足({stats['shuangdian_per_1000']}/千字)"); score -= 15
            if stats["avg_sentence_len"] < 10 and stats["sentence_count"] > 30: issues.append(f"碎片化(均{stats['avg_sentence_len']}字)"); score -= 10
        
        passed = score >= 60
        return {"pass": passed, "issues": issues, "score": score, "stats": stats}
