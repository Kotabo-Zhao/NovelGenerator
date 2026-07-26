"""NovelGenerator — EvaluationSystem: 原子化生成质量评估框架

核心原则：先建尺子再量布。
每个改动必须在评估系统上跑出可量化对比，不接受"感觉上更好"。

指标：
  L1 词汇多样性: distinct-1/2/3 (n-gram 去重率)
  L2 语义多样性: 同 prompt 多轮生成的 pairwise cosine distance
  L3 自我重复度: self-BLEU (越低 = 越多样)
  L4 连贯性: 相邻句 embedding cosine similarity 的均值与方差
  L5 钩子强度: 章末句是否包含"冲突/悬念/反转"语义信号
  L6 爽点密度: 每2000字内的情绪变化点数
  L7 去AI味得分: 现有 AI detector 的评分
  L8 人类可读对比: 并排展示两份生成结果 + 指标仪表板
"""
import json
import os
import math
import logging
import statistics
from typing import Optional
from collections import Counter

log = logging.getLogger(__name__)

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


# ── L1: 词汇多样性 ──

def distinct_n(text: str, n: int = 1) -> float:
    """计算 distinct-n 分数：唯一 n-gram 数 / 总 n-gram 数
    
    distinct-1 衡量用词多样性，distinct-2 衡量短语多样性。
    网文合理范围：distinct-1 ∈ [0.35, 0.60], distinct-2 ∈ [0.70, 0.90]
    太低 → 重复用词；太高 → 可能不连贯。
    """
    words = _tokenize(text)
    if len(words) < n:
        return 0.0
    ngrams = [tuple(words[i:i+n]) for i in range(len(words) - n + 1)]
    if not ngrams:
        return 0.0
    return len(set(ngrams)) / len(ngrams)


def _tokenize(text: str) -> list:
    """简单中文分词：按标点+空格切分，取长度≥1的片段"""
    import re
    # 中文：按常见标点切分，保留汉字词语
    segments = re.split(r'[，。！？；：、""\'\'（）\\s\\n]+', text)
    words = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # 对中文文本，用字符级2-gram近似"词"
        if re.search(r'[\u4e00-\u9fff]', seg):
            chars = list(seg)
            for i in range(0, len(chars), 2):
                chunk = ''.join(chars[i:i+2])
                if chunk:
                    words.append(chunk)
        else:
            words.append(seg)
    return words


# ── L2: 语义多样性 ──

def semantic_diversity(texts: list, embed_fn=None) -> dict:
    """计算多轮生成间的语义多样性
    
    如果有 embed_fn，用 embedding cosine distance；
    否则回退到 n-gram Jaccard distance。
    
    Returns:
        {"mean_pairwise_distance": float, "min_distance": float, "std": float}
    """
    if len(texts) < 2:
        return {"mean_pairwise_distance": 0, "min_distance": 0, "std": 0}
    
    distances = []
    for i in range(len(texts)):
        for j in range(i+1, len(texts)):
            if embed_fn:
                d = 1.0 - _cosine_sim(embed_fn(texts[i]), embed_fn(texts[j]))
            else:
                d = _jaccard_distance(texts[i], texts[j])
            distances.append(d)
    
    return {
        "mean_pairwise_distance": round(statistics.mean(distances), 4),
        "min_distance": round(min(distances), 4),
        "std": round(statistics.stdev(distances), 4) if len(distances) > 1 else 0,
    }


def _cosine_sim(a, b):
    if not HAS_NUMPY:
        return 0.5
    a, b = np.array(a), np.array(b)
    norm_a, norm_b = np.linalg.norm(a), np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _jaccard_distance(t1: str, t2: str) -> float:
    w1, w2 = set(_tokenize(t1)), set(_tokenize(t2))
    if not w1 and not w2:
        return 0.0
    intersection = len(w1 & w2)
    union = len(w1 | w2)
    return 1.0 - intersection / union if union > 0 else 0.0


# ── L3: Self-BLEU（自我重复度） ──

def self_bleu(texts: list, n_gram: int = 3) -> float:
    """计算 self-BLEU：越低 = 越多样
    
    对每个文本，用其他所有文本的 n-gram 作为"参考"，计算 BLEU。
    self-BLEU 高 → 各轮生成高度相似（模式崩溃）。
    网文合理范围：self-BLEU < 0.30
    """
    if len(texts) < 2:
        return 0.0
    
    all_scores = []
    for i, text in enumerate(texts):
        ref_texts = [texts[j] for j in range(len(texts)) if j != i]
        score = _bleu_single(text, ref_texts, n_gram)
        all_scores.append(score)
    
    return round(statistics.mean(all_scores), 4)


def _bleu_single(candidate: str, references: list, n: int) -> float:
    """简化的句子级 BLEU"""
    cand_tokens = _tokenize(candidate)
    if len(cand_tokens) < n:
        return 0.0
    
    # 统计参考文本中的 n-gram
    ref_ngram_counts = Counter()
    for ref in references:
        ref_tokens = _tokenize(ref)
        for i in range(len(ref_tokens) - n + 1):
            ref_ngram_counts[tuple(ref_tokens[i:i+n])] += 1
    
    # 计算匹配
    matches = 0
    total = len(cand_tokens) - n + 1
    for i in range(total):
        ng = tuple(cand_tokens[i:i+n])
        if ref_ngram_counts[ng] > 0:
            matches += 1
            ref_ngram_counts[ng] -= 1
    
    return matches / total if total > 0 else 0.0


# ── L4: 连贯性 ──

def coherence_score(text: str, embed_fn=None) -> dict:
    """评估文本内部连贯性
    
    计算相邻句子的语义相似度。
    均值太高 → 重复；均值太低 → 跳跃。方差太大 → 不连贯。
    理想：均值 0.55-0.75，方差 < 0.15
    """
    import re
    sentences = re.split(r'[。！？!?\n]+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 3]
    
    if len(sentences) < 3:
        return {"mean_adjacent_similarity": 0, "std": 0, "drop_count": 0, "verdict": "too_short"}
    
    sims = []
    drops = 0
    for i in range(len(sentences) - 1):
        if embed_fn:
            sim = _cosine_sim(embed_fn(sentences[i]), embed_fn(sentences[i+1]))
        else:
            sim = 1.0 - _jaccard_distance(sentences[i], sentences[i+1])
        sims.append(sim)
        if sim < 0.2:
            drops += 1
    
    mean_sim = round(statistics.mean(sims), 4) if sims else 0
    std_sim = round(statistics.stdev(sims), 4) if len(sims) > 1 else 0
    
    # 判断
    if mean_sim > 0.85:
        verdict = "过于重复"
    elif mean_sim < 0.30:
        verdict = "跳跃过大"
    elif std_sim > 0.25:
        verdict = "连贯性不稳定"
    else:
        verdict = "良好"
    
    return {
        "mean_adjacent_similarity": mean_sim,
        "std": std_sim,
        "drop_count": drops,
        "total_sentences": len(sentences),
        "verdict": verdict,
    }


# ── L5: 钩子强度 ──

HOOK_SIGNALS = [
    # 危机悬停
    r'(突然|猛然|忽然|猛地|一瞬间).{0,10}(出现|袭来|逼近|炸开|碎裂)',
    r'(刀刃|剑尖|枪口|拳头|黑影).{0,8}(抵|逼近|袭来|出现)',
    # 反转预告
    r'(不知道的是|没料到|想不到|万万没想到)',
    r'(然而|但是|可是)(他|她|它).{0,5}(不知道|没发现|没察觉)',
    # 秘密揭晓
    r'(门.*?(开|推开|打开)).{0,10}(站|出现|竟然是)',
    r'(熟悉|陌生|诡异).{0,5}(声音|身影|气息)',
    r'(竟然|居然是|怎么会|怎么可能)',
    # 打脸前置
    r'(冷笑|不屑|嘲讽|轻蔑).{0,10}(说|道|看着)',
    r'(等着|走着瞧|你会后悔)',
]

def hook_strength(chapter_text: str) -> dict:
    """检测章末钩子强度
    
    取最后 300 字，与 HOOK_SIGNALS 正则匹配。
    Returns:
        {"strength": 0-10, "matched_patterns": [...], "last_300_chars": str}
    """
    import re
    end_text = chapter_text[-300:] if len(chapter_text) > 300 else chapter_text
    
    matched = []
    score = 0
    for pattern in HOOK_SIGNALS:
        matches = re.findall(pattern, end_text)
        if matches:
            matched.append({"pattern": pattern[:40], "count": len(matches)})
            score += len(matches) * 2
    
    # 额外：检查是否以问句或省略号结尾（钩子信号）
    last_sentence = re.split(r'[。！？\n]', end_text)[-1].strip()
    if last_sentence.endswith('……') or last_sentence.endswith('...'):
        score += 3
    if '?' in last_sentence or '？' in last_sentence:
        score += 2
    
    strength = min(10, score)
    
    return {
        "strength": strength,
        "verdict": "强钩子" if strength >= 7 else ("中钩子" if strength >= 4 else "弱/无钩子"),
        "matched_patterns": matched,
        "last_sentence": last_sentence[:100],
    }


# ── L6: 爽点密度 ──

DOPAMINE_SIGNALS = [
    # 打脸/碾压
    r'(碾压|秒杀|一击|瞬间|轰然|炸裂)',
    r'(震惊|愕然|不可置信|目瞪口呆|骇然)',
    r'(跪下|求饶|后悔|晚了|迟了)',
    # 反转/揭秘
    r'(原来|竟然是|居然是|怎么会)',
    r'(真相|秘密|真实身份|真正.*?是)',
    # 升级/突破
    r'(突破|晋升|晋级|升级|进阶)',
    r'(领悟|顿悟|掌握|觉醒|激活)',
    # 甜宠
    r'(握紧|抱紧|搂住|吻|心跳)',
    r'(温柔|宠溺|心疼|舍不得)',
]

def dopamine_density(text: str) -> dict:
    """计算爽点密度：每 2000 字中的爽感信号数"""
    total_chars = len(text)
    if total_chars < 100:
        return {"density_per_2000": 0, "total_signals": 0, "verdict": "too_short"}
    
    import re
    all_matches = []
    for pattern in DOPAMINE_SIGNALS:
        for m in re.finditer(pattern, text):
            all_matches.append({"pattern": pattern[:30], "position": m.start()})
    
    # 按位置去重（相邻50字内的算同一个）
    all_matches.sort(key=lambda x: x["position"])
    unique_signals = []
    last_pos = -100
    for m in all_matches:
        if m["position"] - last_pos > 50:
            unique_signals.append(m)
            last_pos = m["position"]
    
    density = round(len(unique_signals) / (total_chars / 2000), 1)
    
    return {
        "density_per_2000": density,
        "total_signals": len(unique_signals),
        "verdict": "爽点充足" if density >= 4 else ("偏少" if density >= 2 else "严重不足"),
    }


# ── L7: 去AI味得分 ──

def ai_slop_score(text: str) -> dict:
    """检测 AI 味残留
    
    扫描常见 AI 模式：二元对比壳、伪洞察标记、空泛总结、AI过渡词等。
    返回扣分项列表 + 总分（满分100，扣完为止）
    """
    import re
    issues = []
    score = 100
    
    patterns = [
        (r'不是.{1,20}而是', "二元对比壳「不是A而是B」", 5),
        (r'并非.{1,20}而在于', "二元对比壳「并非X而在于Y」", 5),
        (r'真正(的|地)', "伪洞察「真正的XX」", 3),
        (r'本质上', "伪洞察「本质上」", 3),
        (r'核心在于|关键在于', "伪洞察「核心/关键在于」", 3),
        (r'说白了|归根结底', "伪洞察「说白了/归根结底」", 3),
        (r'这不仅仅是|这标志着|这是一个', "空泛总结句", 5),
        (r'与此同时|此外|值得一提的是|总的来看', "AI过渡词", 3),
        (r'不仅仅.{0,5}更是|不只是.{0,5}更是', "二元对比壳", 5),
        (r'随着.{1,20}的发展', "AI套话「随着XX的发展」", 3),
        (r'在这一刻|在那一刻', "AI套路「在X一刻」", 3),
        (r'似乎|仿佛|或许|大概', "模糊词堆砌", 1),  # 每个扣1分
        (r'胸口的|心头的|心跳|呼吸', "AI情感套路「身体反应」", 2),
    ]
    
    for pattern, label, penalty in patterns:
        matches = re.findall(pattern, text)
        count = len(matches)
        if count > 0:
            total_penalty = min(penalty * count, 15)  # 单类最多扣15
            score -= total_penalty
            issues.append({"pattern": label, "count": count, "penalty": total_penalty})
    
    score = max(0, score)
    
    return {
        "score": score,
        "verdict": "优秀" if score >= 85 else ("良好" if score >= 70 else ("一般" if score >= 50 else "AI味重")),
        "issues": issues,
    }


# ── L7.5: 句式碎片化检测 ──

def sentence_fragmentation(text: str) -> dict:
    """检测句式碎片化程度
    
    症状: 大量单句段落，每段一句话就换行。
    正常网文: 每2-4句话为一个自然段。
    碎片化: 连续5+段都是单句段落。
    """
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if len(paragraphs) < 5:
        return {"score": 100, "verdict": "文本太短", "single_sentence_ratio": 0}
    
    # 统计单句段落（以。！？结尾且长度<35字）
    single_count = 0
    consecutive_max = 0
    current_consecutive = 0
    
    for p in paragraphs:
        stripped = p.strip()
        is_single = (len(stripped) < 35 and 
                    any(stripped.rstrip().endswith(c) for c in '。！？'))
        if is_single:
            single_count += 1
            current_consecutive += 1
            consecutive_max = max(consecutive_max, current_consecutive)
        else:
            current_consecutive = 0
    
    ratio = single_count / len(paragraphs)
    
    # 评分: 单句段占比越低越好
    # <20% = 100分, 20-40% = 80分, 40-60% = 50分, >60% = 20分
    if ratio < 0.2: score = 100
    elif ratio < 0.3: score = 85
    elif ratio < 0.4: score = 70
    elif ratio < 0.5: score = 50
    elif ratio < 0.6: score = 30
    else: score = 15
    
    # 连续单句段惩罚
    if consecutive_max >= 8:
        score = max(10, score - 30)
    elif consecutive_max >= 5:
        score = max(15, score - 15)
    
    return {
        "score": score,
        "verdict": "优秀·段落节奏好" if score >= 80 else (
            "良好" if score >= 60 else (
            "碎片化·需合并段落" if score >= 30 else "严重碎片化")),
        "single_sentence_ratio": round(ratio, 2),
        "total_paragraphs": len(paragraphs),
        "single_sentence_count": single_count,
        "max_consecutive_single": consecutive_max,
    }


# ── L8: 综合评估报告 ──

def evaluate_chapter(text: str, texts_for_diversity: list = None, embed_fn=None) -> dict:
    """对单章文本执行全维度评估"""
    report = {
        "distinct_1": round(distinct_n(text, 1), 4),
        "distinct_2": round(distinct_n(text, 2), 4),
        "distinct_3": round(distinct_n(text, 3), 4),
        "coherence": coherence_score(text, embed_fn),
        "hook": hook_strength(text),
        "dopamine": dopamine_density(text),
        "ai_slop": ai_slop_score(text),
        "fragmentation": sentence_fragmentation(text),
    }
    
    if texts_for_diversity and len(texts_for_diversity) >= 2:
        report["semantic_diversity"] = semantic_diversity(texts_for_diversity, embed_fn)
        report["self_bleu_3"] = self_bleu(texts_for_diversity, 3)
    
    # 综合评分（加权）
    scores = []
    # distinct-1 映射到 0-100：0.35→50, 0.55→100
    d1 = report["distinct_1"]
    scores.append(("词汇多样性", min(100, max(0, (d1 - 0.2) / 0.4 * 100)), 0.10))
    # 连贯性
    coh = report["coherence"]
    if coh["verdict"] == "良好":
        scores.append(("连贯性", 85, 0.15))
    elif coh["verdict"] == "连贯性不稳定":
        scores.append(("连贯性", 55, 0.15))
    elif coh["verdict"] == "too_short":
        scores.append(("连贯性", 40, 0.15))
    else:
        scores.append(("连贯性", 30, 0.15))
    # 钩子
    scores.append(("钩子强度", report["hook"]["strength"] * 10, 0.20))
    # 爽点
    dp = report["dopamine"]["density_per_2000"]
    scores.append(("爽点密度", min(100, dp / 6 * 100), 0.20))
    # 去AI味
    scores.append(("去AI味", report["ai_slop"]["score"], 0.15))
    # 句式碎片化
    scores.append(("段落节奏", report["fragmentation"]["score"], 0.10))
    # 语义多样性（如有）
    if "semantic_diversity" in report:
        sd = report["semantic_diversity"]["mean_pairwise_distance"]
        scores.append(("语义多样性", min(100, sd * 100), 0.10))
    # self-BLEU（如有）
    if "self_bleu_3" in report:
        sb = report["self_bleu_3"]
        scores.append(("自我重复度", max(0, (0.5 - sb) / 0.5 * 100), 0.10))
    
    # 加权总分
    total_weight = sum(w for _, _, w in scores)
    if total_weight > 0:
        overall = sum(s * w for _, s, w in scores) / total_weight
    else:
        overall = 50
    
    report["overall_score"] = round(overall, 1)
    report["score_breakdown"] = [{"metric": n, "score": round(s, 1), "weight": w} for n, s, w in scores]
    report["verdict"] = _score_verdict(overall)
    
    return report


def _score_verdict(score: float) -> str:
    if score >= 80: return "优秀 — 可直接使用"
    elif score >= 65: return "良好 — 建议小幅修改"
    elif score >= 50: return "一般 — 需要针对性优化"
    else: return "差 — 需要大幅重写"


# ── A/B 对比报告 ──

def compare_ab(text_a: str, text_b: str, label_a: str = "传统生成", label_b: str = "原子化生成", embed_fn=None) -> dict:
    """A/B 对比两份生成文本"""
    report_a = evaluate_chapter(text_a)
    report_b = evaluate_chapter(text_b)
    
    # 如果两份文本都来自同一 prompt，做多样性评估
    diversity_texts = [text_a, text_b]
    
    comparison = {
        "method_a": {"label": label_a, **report_a},
        "method_b": {"label": label_b, **report_b},
        "winner": {},
        "delta": {},
    }
    
    # 逐项比较
    metrics_to_compare = [
        ("distinct_1", "词汇多样性", "higher_better"),
        ("distinct_2", "短语多样性", "higher_better"),
        ("overall_score", "综合评分", "higher_better"),
    ]
    
    # 钩子
    comparison["delta"]["hook_strength"] = round(report_b["hook"]["strength"] - report_a["hook"]["strength"], 1)
    comparison["winner"]["hook_strength"] = label_b if comparison["delta"]["hook_strength"] > 0 else label_a
    
    # 爽点
    comparison["delta"]["dopamine_density"] = round(report_b["dopamine"]["density_per_2000"] - report_a["dopamine"]["density_per_2000"], 1)
    comparison["winner"]["dopamine_density"] = label_b if comparison["delta"]["dopamine_density"] > 0 else label_a
    
    # 去AI味
    comparison["delta"]["ai_slop"] = report_b["ai_slop"]["score"] - report_a["ai_slop"]["score"]
    comparison["winner"]["ai_slop"] = label_b if comparison["delta"]["ai_slop"] > 0 else label_a
    
    # 连贯性
    coh_a = report_a["coherence"]
    coh_b = report_b["coherence"]
    coh_score = {"良好": 3, "连贯性不稳定": 2, "跳跃过大": 1, "过于重复": 1, "too_short": 0}
    comparison["delta"]["coherence"] = coh_score.get(coh_b["verdict"], 0) - coh_score.get(coh_a["verdict"], 0)
    comparison["winner"]["coherence"] = label_b if comparison["delta"]["coherence"] > 0 else (label_a if comparison["delta"]["coherence"] < 0 else "平手")
    
    # 综合
    comparison["delta"]["overall"] = round(report_b["overall_score"] - report_a["overall_score"], 1)
    comparison["winner"]["overall"] = label_b if comparison["delta"]["overall"] > 0 else (label_a if comparison["delta"]["overall"] < 0 else "平手")
    
    # 语义多样性（AB本身就是2个样本）
    sd = semantic_diversity(diversity_texts, embed_fn)
    comparison["cross_diversity"] = sd
    
    # 胜出统计
    wins = sum(1 for v in comparison["winner"].values() if v == label_b)
    losses = sum(1 for v in comparison["winner"].values() if v == label_a)
    ties = sum(1 for v in comparison["winner"].values() if v == "平手")
    comparison["summary"] = f"{label_b} 胜 {wins} 项, {label_a} 胜 {losses} 项, 平手 {ties} 项"
    
    return comparison


# ── HTML 报告生成 ──

def generate_html_report(comparison: dict, output_path: str = None) -> str:
    """生成 A/B 对比的 HTML 可视化报告"""
    a = comparison["method_a"]
    b = comparison["method_b"]
    delta = comparison["delta"]
    
    def delta_cell(val, higher_better=True):
        if val > 0:
            color = "#16a34a" if higher_better else "#dc2626"
            sign = "+"
        elif val < 0:
            color = "#dc2626" if higher_better else "#16a34a"
            sign = ""
        else:
            color = "#6b7280"
            sign = ""
        return f'<span style="color:{color};font-weight:700">{sign}{round(val,2)}</span>'
    
    def verdict_badge(score):
        if score >= 80: return f'<span style="background:#dcfce7;color:#16a34a;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:700">{score} 优秀</span>'
        elif score >= 65: return f'<span style="background:#fef3c7;color:#d97706;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:700">{score} 良好</span>'
        else: return f'<span style="background:#fee2e2;color:#dc2626;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:700">{score}</span>'
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NovelGenerator A/B 评估报告</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;max-width:900px;margin:0 auto;padding:24px;background:#f5f5f7;color:#1a1a2e;line-height:1.6}}
h1{{font-size:22px;margin-bottom:4px}}h2{{font-size:16px;margin:20px 0 10px;border-bottom:2px solid #e07030;padding-bottom:6px}}
.card{{background:#fff;border-radius:10px;padding:20px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid #e5e7eb}}
th{{background:#f8f9fa;font-weight:600;font-size:12px;color:#555}}
.summary{{background:#fef3c7;border-left:4px solid #d97706;padding:12px 16px;border-radius:0 8px 8px 0;margin:12px 0;font-weight:600}}
.summary-win{{background:#dcfce7;border-left-color:#16a34a}}
.bar{{height:8px;background:#e5e7eb;border-radius:4px;overflow:hidden;margin:4px 0}}
.bar-fill{{height:100%;border-radius:4px;transition:width .3s}}
.metric-name{{font-weight:600}}
.text-compare{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}}
.text-panel{{background:#f8f9fa;border-radius:8px;padding:14px;max-height:400px;overflow-y:auto;font-size:13px;white-space:pre-wrap;border:1px solid #e5e7eb}}
.text-panel h3{{font-size:14px;margin:0 0 8px;padding:0}}
</style>
</head>
<body>
<h1>📊 NovelGenerator A/B 评估报告</h1>
<p style="color:#999;font-size:13px">{a['label']} vs {b['label']}</p>

<div class="summary {'summary-win' if delta['overall']>0 else ''}">
  🏆 {comparison['summary']} | 综合评分 Δ = {delta_cell(delta['overall'])}
</div>

<div class="card">
<h2>综合评分对比</h2>
<table>
<tr><th>指标</th><th>{a['label']}</th><th>{b['label']}</th><th>Δ</th></tr>
<tr><td class="metric-name">综合评分</td><td>{verdict_badge(a['overall_score'])}</td><td>{verdict_badge(b['overall_score'])}</td><td>{delta_cell(delta['overall'])}</td></tr>
<tr><td class="metric-name">词汇多样性</td><td>{a['distinct_1']}</td><td>{b['distinct_1']}</td><td>{delta_cell(b['distinct_1']-a['distinct_1'])}</td></tr>
<tr><td class="metric-name">短语多样性</td><td>{a['distinct_2']}</td><td>{b['distinct_2']}</td><td>{delta_cell(b['distinct_2']-a['distinct_2'])}</td></tr>
<tr><td class="metric-name">钩子强度</td><td>{a['hook']['strength']}/10</td><td>{b['hook']['strength']}/10</td><td>{delta_cell(delta['hook_strength'])}</td></tr>
<tr><td class="metric-name">爽点密度</td><td>{a['dopamine']['density_per_2000']}/2000字</td><td>{b['dopamine']['density_per_2000']}/2000字</td><td>{delta_cell(delta['dopamine_density'])}</td></tr>
<tr><td class="metric-name">去AI味</td><td>{a['ai_slop']['score']}/100</td><td>{b['ai_slop']['score']}/100</td><td>{delta_cell(delta['ai_slop'])}</td></tr>
<tr><td class="metric-name">连贯性</td><td>{a['coherence']['verdict']}</td><td>{b['coherence']['verdict']}</td><td>{delta_cell(delta['coherence'])}</td></tr>
</table>
</div>

<div class="card">
<h2>评分明细</h2>
<table>
<tr><th>维度</th><th>{a['label']} 得分</th><th>{b['label']} 得分</th><th>权重</th></tr>
"""
    for i in range(len(a.get('score_breakdown', []))):
        sa = a['score_breakdown'][i]
        sb = b['score_breakdown'][i] if i < len(b.get('score_breakdown', [])) else {'score': 0}
        html += f"<tr><td>{sa['metric']}</td><td>{sa['score']}</td><td>{sb['score']}</td><td>{sa['weight']*100:.0f}%</td></tr>\n"
    
    html += """</table>
</div>

<div class="card">
<h2>钩子检测详情</h2>
<table>
<tr><th></th><th>信号数</th><th>章末句</th></tr>"""
    html += f"<tr><td><strong>{a['label']}</strong></td><td>{len(a['hook']['matched_patterns'])}</td><td style='font-size:12px'>{a['hook']['last_sentence']}</td></tr>"
    html += f"<tr><td><strong>{b['label']}</strong></td><td>{len(b['hook']['matched_patterns'])}</td><td style='font-size:12px'>{b['hook']['last_sentence']}</td></tr>"
    html += """</table>
</div>

<div class="card">
<h2>语义多样性（跨生成）</h2>
<p>两份生成之间的语义距离：<strong>"""
    if 'cross_diversity' in comparison:
        cd = comparison['cross_diversity']
        html += f"{cd['mean_pairwise_distance']}</strong>（越高=越不同，理想>0.3）"
    else:
        html += "N/A"
    html += """</p>
</div>

<p style="text-align:center;color:#999;font-size:11px;margin-top:32px">NovelGenerator Evaluation System · 自动生成</p>
</body></html>"""
    
    if output_path:
        try:
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html)
            log.info(f"Evaluation report saved: {output_path}")
        except IOError as e:
            log.error(f"Failed to save evaluation report: {e}")
    
    return html


# ── 快速测试 ──

if __name__ == "__main__":
    # 简单自测
    test_text = """
    他推开门的瞬间，一股冷风灌了进来。房间里空无一人，只有桌上的烛火在摇曳。
    突然，一阵轻微的脚步声从身后传来。他猛地转身，却什么也没看到。
    不对劲。他握紧了手中的剑，指节发白。这房子里，绝不止他一个人。
    身后的门，无声无息地关上了。
    """
    
    print("=== 评估系统自测 ===")
    result = evaluate_chapter(test_text)
    print(f"综合评分: {result['overall_score']} — {result['verdict']}")
    print(f"词汇多样性 distinct-1: {result['distinct_1']}")
    print(f"钩子强度: {result['hook']['strength']}/10 ({result['hook']['verdict']})")
    print(f"爽点密度: {result['dopamine']['density_per_2000']}/2000字")
    print(f"AI味: {result['ai_slop']['score']}/100")
    print(f"连贯性: {result['coherence']['verdict']}")
    
    # A/B 对比测试
    test_text_b = """
    门开了。冷风扑面。
    空的。全空了。桌上的蜡烛还亮着，影子在墙上晃。
    脚步声。身后。
    他转身——什么都没有。手不自觉地握紧剑柄。指节咔咔作响。
    门在身后合上，没发出一丝声音。
    """
    
    print("\n=== A/B 对比 ===")
    comp = compare_ab(test_text, test_text_b, "传统写法", "原子化写法")
    print(f"结果: {comp['summary']}")
    print(f"综合评分 Δ: {comp['delta']['overall']}")
    
    html_path = "/tmp/eval_test_report.html"
    generate_html_report(comp, html_path)
    print(f"\nHTML 报告: {html_path}")
