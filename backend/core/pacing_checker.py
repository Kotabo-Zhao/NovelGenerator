"""NovelGenerator — Pacing Checker: 节奏控制师 Agent

职责: 分析章节节奏质量，给出量化分数和改进建议
参考: snowflake-subagents 的节奏控制师 + StoryScope 的叙事特征分析
"""

import re
import json
import logging
from openai import OpenAI
from .resilient_client import ResilientLLMClient

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
        self._resilient = ResilientLLMClient(client, model)

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
            response = self._resilient.create(
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
            # v2.3.7 活人感检测
            "dialogue_tag_density": round(
                len(re.findall(r'说道|笑道|答道|问道|喝道|喊道|说道|冷冷道|淡淡道|沉声道|哼道|叹道|低声道|冷声|柔声|厉声|平静道|开口道|接口道|接过话|说道|开口', text)) / max(len(text), 1) * 1000, 1),
            "emotion_statement_density": round(
                len(re.findall(r'他感到|她感到|心中涌起|内心充满|心里升起|只觉得|莫名地|突然觉得|感到一阵|一股.*涌上|眼中闪过一丝|眼底闪过一丝|心里一沉|心头一紧', text)) / max(len(text), 1) * 1000, 1),
            "filler_desc_density": round(
                len(re.findall(r'缓缓|轻轻|微微|静静|默默|淡淡|悄悄|慢慢', text)) / max(len(text), 1) * 1000, 1),
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
            if stats["dialogue_ratio"] > 40: issues.append(f"快餐: 对话占比过高({stats['dialogue_ratio']}%)"); score -= 35
            elif stats["dialogue_ratio"] > 30: issues.append(f"快餐: 对话偏多({stats['dialogue_ratio']}%)"); score -= 20
            if stats["water_dialogue_ratio"] > 10: issues.append(f"快餐: 水对话({stats['water_dialogue_ratio']}%)"); score -= 25
            if stats["max_consecutive_dialogue"] > 3: issues.append(f"快餐: 连续对话{stats['max_consecutive_dialogue']}段"); score -= 20
            if stats["action_ratio"] < 40: issues.append(f"快餐: 动作不足({stats['action_ratio']}%)"); score -= 20
            if stats["shuangdian_per_1000"] < 12: issues.append(f"快餐: 爽点不足({stats['shuangdian_per_1000']}/千字,需≥12)"); score -= 30
            if stats["avg_sentence_len"] < 8 and stats["sentence_count"] > 30: issues.append(f"快餐: 碎片化"); score -= 10
            if len(text) < 2500: issues.append(f"快餐: 字数不足({len(text)}字)"); score -= 15
            # 新增：打脸闭环检查 — 必须同时有冲突信号和逆转信号
            has_conflict = any(kw in text[:1000] for kw in ['杀','死','血','辱','逼','退婚','背叛','陷害'])
            has_reversal = any(kw in text for kw in ['反转','震惊','怎么可能','竟然是','原来','冷笑','不屑','跪下','饶命'])
            if not has_conflict: issues.append("快餐: 前1000字无冲突信号"); score -= 20
            if not has_reversal: issues.append("快餐: 全章无反转信号"); score -= 20
            # 章末钩子检测：最后200字必须有钩子关键词
            last200 = text[-200:] if len(text) > 200 else text
            hook_keywords = ['突然','忽然','竟然','怎么可能','但是','然而','却','这时','那一刻','明天', '不知道','没想到','意味着','原来','正要','刚','谁知','不料','却发现','下一瞬']
            has_hook = any(kw in last200 for kw in hook_keywords)
            if not has_hook: issues.append("快餐: 章末无钩子(最后200字缺少悬念词)"); score -= 25
        else:
            # v2.4.4: 非快餐模式阈值按文体弹性化
            # 对话占比: >65% 才扣（对话流/剧本感文体常见 50%+）
            if stats["dialogue_ratio"] > 65: issues.append(f"对话占比过高({stats['dialogue_ratio']}%)"); score -= 20
            elif stats["dialogue_ratio"] > 55: issues.append(f"对话偏多({stats['dialogue_ratio']}%)"); score -= 10
            if stats["water_dialogue_ratio"] > 25: issues.append(f"水对话过多({stats['water_dialogue_ratio']}%)"); score -= 15
            if stats["max_consecutive_dialogue"] > 8: issues.append(f"连续对话{stats['max_consecutive_dialogue']}段"); score -= 10
            if stats["action_ratio"] < 20: issues.append(f"动作不足({stats['action_ratio']}%)"); score -= 10
            # v2.4.4: 非快餐模式移除爽点检查——爽点密度是快餐/爽文专属指标，
            # 悬疑铺垫/种田/文学慢热文前期无爽点信号词，检查会导致误杀重写
            if stats["avg_sentence_len"] < 10 and stats["sentence_count"] > 30: issues.append(f"碎片化(均{stats['avg_sentence_len']}字)"); score -= 10

        # ── v2.3.7 活人感检测（v2.4.4: 按文体弹性，非快餐模式放宽阈值）──
        # 对话标签滥用：快餐 >6 / 常规 >9 个"说道/笑道/冷冷道" = AI 腔
        _tag_th = 6 if fast_food else 9
        _tag_th2 = 4 if fast_food else 7
        if stats["dialogue_tag_density"] > _tag_th:
            issues.append(f"对话标签过多({stats['dialogue_tag_density']}/千字·说道/笑道类)"); score -= 20
        elif stats["dialogue_tag_density"] > _tag_th2:
            issues.append(f"对话标签偏多({stats['dialogue_tag_density']}/千字)"); score -= 10
        # 情绪直接陈述：快餐 >3 / 常规 >5（言情/心理流可放宽）
        _emo_th = 3 if fast_food else 5
        if stats["emotion_statement_density"] > _emo_th:
            issues.append(f"情绪直述过多({stats['emotion_statement_density']}/千字·他感到/涌起类)"); score -= 15
        # 填充副词：快餐 >12 / 常规 >16（文艺/氛围流可放宽）
        _fill_th = 12 if fast_food else 16
        if stats["filler_desc_density"] > _fill_th:
            issues.append(f"填充描写词过多({stats['filler_desc_density']}/千字·缓缓/轻轻类)"); score -= 15

        passed = score >= 60
        return {"pass": passed, "issues": issues, "score": score, "stats": stats}
