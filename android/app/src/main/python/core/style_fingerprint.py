"""NovelGenerator — Style Fingerprint: 定量风格指纹分析

5维DNA分析:
1. 句长基线 — 平均句长/句长方差/短句比例
2. 段落节奏 — 平均段长/段长方差/短段比例
3. 感官偏好 — 视觉/听觉/触觉/嗅觉描写的分布
4. 比喻指纹 — 比喻类型(明喻/暗喻/拟人)和使用频率
5. 标点指纹 — 感叹号/问号/省略号/破折号/分号密度

参考:
- Lorn.NovelWriteSkills 5维DNA
- novel-writer-style-cn 57ms/万词风格分析
"""

import re
import json
import logging
from collections import Counter

log = logging.getLogger(__name__)


# ── 感官词典 ──
SENSORY_DICT = {
    "visual": ["看", "见", "望", "观", "颜色", "光", "暗", "亮", "红", "蓝", "绿", "黑", "白", "金", "银",
              "闪烁", "辉", "影", "影子", "映", "照", "透明", "浮现", "显现", "轮廓"],
    "auditory": ["听", "闻", "声", "音", "响", "静", "寂", "喧", "鸣", "吼", "叫", "喊", "低语", "耳语",
                "回响", "余音", "轰鸣", "喧嚣", "炸", "碎裂声"],
    "tactile": ["触", "摸", "冷", "热", "烫", "凉", "冰", "温", "软", "硬", "粗", "滑", "刺", "痛",
               "麻", "痒", "湿", "干", "重", "轻", "沉", "压", "震动", "颤抖"],
    "olfactory": ["嗅", "闻", "香", "臭", "腥", "甜腻", "腐", "烟味", "血腥", "药味", "花草香", "焦"],
    "gustatory": ["尝", "味", "甜", "苦", "酸", "辣", "咸", "涩", "甘", "醇", "鲜"],
}

# ── 比喻标记 ──
METAPHOR_MARKERS = {
    "simile": ["像", "如", "似", "仿佛", "宛如", "犹如", "好比", "如同"],
    "metaphor": ["是", "就是", "便是", "成了", "化作", "变成"],
    "personification": ["仿佛在", "好似在", "似乎在", "像是活", "像在呼吸", "般发出"],
}


class StyleFingerprint:
    """定量风格指纹分析器"""

    def analyze(self, text: str) -> dict:
        """分析文本的5维风格指纹
        
        Returns:
            5维DNA dict + 综合风格特征
        """
        if not text or len(text) < 500:
            return {"error": "文本过短，至少需要500字"}
        
        return {
            "word_count": len(text),
            "sentence_dna": self._analyze_sentences(text),
            "paragraph_dna": self._analyze_paragraphs(text),
            "sensory_dna": self._analyze_sensory(text),
            "metaphor_dna": self._analyze_metaphors(text),
            "punctuation_dna": self._analyze_punctuation(text),
            "summary": self._generate_summary(text),
        }

    def _analyze_sentences(self, text: str) -> dict:
        """句长DNA"""
        sentences = re.split(r"[。！？……\n]", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 1]
        
        if not sentences:
            return {}
        
        lens = [len(s) for s in sentences]
        avg = sum(lens) / len(lens)
        variance = sum((l - avg) ** 2 for l in lens) / len(lens)
        short_ratio = sum(1 for l in lens if l <= 8) / len(lens) * 100
        long_ratio = sum(1 for l in lens if l >= 30) / len(lens) * 100
        
        # 句长分布直方图
        bins = {"<=5": 0, "6-10": 0, "11-20": 0, "21-30": 0, "31-50": 0, ">50": 0}
        for l in lens:
            if l <= 5: bins["<=5"] += 1
            elif l <= 10: bins["6-10"] += 1
            elif l <= 20: bins["11-20"] += 1
            elif l <= 30: bins["21-30"] += 1
            elif l <= 50: bins["31-50"] += 1
            else: bins[">50"] += 1
        
        return {
            "avg_len": round(avg, 1),
            "variance": round(variance, 1),
            "short_ratio": round(short_ratio, 1),
            "long_ratio": round(long_ratio, 1),
            "total_sentences": len(sentences),
            "distribution": {k: round(v / len(sentences) * 100, 1) for k, v in bins.items()},
        }

    def _analyze_paragraphs(self, text: str) -> dict:
        """段落节奏DNA"""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return {}
        
        lens = [len(p) for p in paragraphs]
        avg = sum(lens) / len(lens)
        variance = sum((l - avg) ** 2 for l in lens) / len(lens)
        
        # 段落句数
        para_sent_counts = []
        for p in paragraphs:
            sent_count = len(re.split(r"[。！？]", p))
            para_sent_counts.append(sent_count)
        
        avg_sents = sum(para_sent_counts) / len(para_sent_counts)
        
        # 单句段比例
        single_sent_ratio = sum(1 for s in para_sent_counts if s <= 1) / len(para_sent_counts) * 100
        
        return {
            "count": len(paragraphs),
            "avg_chars": round(avg, 1),
            "variance": round(variance, 1),
            "avg_sentences": round(avg_sents, 1),
            "single_sent_ratio": round(single_sent_ratio, 1),
        }

    def _analyze_sensory(self, text: str) -> dict:
        """感官偏好DNA"""
        result = {}
        for sense, words in SENSORY_DICT.items():
            count = sum(len(re.findall(re.escape(w), text)) for w in words)
            result[sense] = count
        
        total = sum(result.values()) or 1
        ratios = {k: round(v / total * 100, 1) for k, v in result.items()}
        
        dominant = max(result, key=result.get) if result else "visual"
        
        return {
            "counts": result,
            "ratios": ratios,
            "dominant": dominant,
            "total_hits": total,
        }

    def _analyze_metaphors(self, text: str) -> dict:
        """比喻指纹DNA"""
        result = {}
        for mtype, markers in METAPHOR_MARKERS.items():
            count = sum(len(re.findall(re.escape(m), text)) for m in markers)
            result[mtype] = count
        
        density = round(sum(result.values()) / max(len(text), 1) * 1000, 2)
        
        return {
            "counts": result,
            "total": sum(result.values()),
            "density_per_1000": density,
            "dominant_type": max(result, key=result.get) if any(result.values()) else "none",
        }

    def _analyze_punctuation(self, text: str) -> dict:
        """标点指纹DNA"""
        puncts = {
            "exclamation": text.count("！"),
            "question": text.count("？"),
            "ellipsis": text.count("……") + text.count("…"),
            "dash": text.count("—") + text.count("——"),
            "semicolon": text.count("；"),
            "comma": text.count("，"),
            "period": text.count("。"),
        }
        
        word_count = len(text)
        densities = {k: round(v / max(word_count, 1) * 1000, 1) for k, v in puncts.items()}
        
        return {
            "counts": puncts,
            "densities_per_1000": densities,
            "excl_ratio": round(puncts["exclamation"] / max(puncts["period"] + puncts["exclamation"], 1) * 100, 1),
        }

    def _generate_summary(self, text: str) -> str:
        """生成综合风格特征描述"""
        sent = self._analyze_sentences(text)
        para = self._analyze_paragraphs(text)
        sensory = self._analyze_sensory(text)
        punct = self._analyze_punctuation(text)
        
        features = []
        
        # 句长风格
        if sent.get("short_ratio", 0) > 30:
            features.append("短句主导型")
        elif sent.get("long_ratio", 0) > 20:
            features.append("长句主导型")
        else:
            features.append("长短均衡型")
        
        # 段落风格
        if para.get("avg_chars", 0) < 100:
            features.append("轻段落")
        elif para.get("avg_chars", 0) > 300:
            features.append("重段落")
        
        # 感官主导
        features.append(f"{SENSORY_DICT.get(sensory.get('dominant', 'visual'), ['视觉'])[0]}主导")
        
        # 感叹号风格
        excl_ratio = punct.get("excl_ratio", 0)
        if excl_ratio > 20:
            features.append("高情绪密度")
        elif excl_ratio < 5:
            features.append("克制型")
        
        return " · ".join(features)

    def compare(self, text_a: str, text_b: str) -> dict:
        """对比两个文本的风格差异"""
        fp_a = self.analyze(text_a)
        fp_b = self.analyze(text_b)
        
        if "error" in fp_a or "error" in fp_b:
            return {"error": "文本过短", "fp_a": fp_a, "fp_b": fp_b}
        
        return {
            "a_style": fp_a.get("summary", ""),
            "b_style": fp_b.get("summary", ""),
            "diff": {
                "sentence": {
                    "a_avg_len": fp_a["sentence_dna"]["avg_len"],
                    "b_avg_len": fp_b["sentence_dna"]["avg_len"],
                    "delta": round(fp_b["sentence_dna"]["avg_len"] - fp_a["sentence_dna"]["avg_len"], 1),
                },
                "paragraph": {
                    "a_avg_chars": fp_a["paragraph_dna"]["avg_chars"],
                    "b_avg_chars": fp_b["paragraph_dna"]["avg_chars"],
                    "delta": round(fp_b["paragraph_dna"]["avg_chars"] - fp_a["paragraph_dna"]["avg_chars"], 1),
                },
                "sensory_shift": {
                    "a": fp_a["sensory_dna"]["dominant"],
                    "b": fp_b["sensory_dna"]["dominant"],
                },
            }
        }
