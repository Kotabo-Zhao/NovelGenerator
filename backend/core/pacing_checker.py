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
        """本地统计：句长、段落、对话比、标点密度"""
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
