"""NovelGenerator — AI Detector & Human Rewriter: 去AI味架构层

Pipeline: 生成初稿 → AI检测(标记AI段落) → 重点改写 → 终稿

不做规则堆砌。用独立的检测→改写循环。
"""

import json
import logging
from typing import Optional
from openai import OpenAI
from .resilient_client import ResilientLLMClient

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# AI Pattern Detector
# ═══════════════════════════════════════════

DETECTOR_SYSTEM = """你是一位专业的文字编辑。你的唯一任务是：识别文本中的 AI 写作痕迹。

## AI 写作的典型特征

1. **心理动词滥用**: "他感到""他意识到""他决定""他认为""他明白"——真人通过动作和身体反应表达内心
2. **情感词直给**: "他愤怒了""她悲伤不已""心中充满喜悦"——真人写"他把杯子砸了""她没说话，把照片翻过去""嘴角动了动，没笑出来"
3. **万能金句**: "这不仅仅是一次突破""标志着一个新的里程碑""真正的力量来自内心"——真人不会这样总结
4. **句式重复**: 连续3句以上长度相似（都是15-25字）——真人会长短交替
5. **过度解释**: 把事情的原因、过程、结果全写清楚——真人会留白、跳跃
6. **万能过渡词**: "与此同时""在这个过程中""随着……的发展""此外"——真人的过渡更自然或直接不过渡

## 输出格式

只输出 JSON:
```json
{
  "ai_score": 65,
  "flagged_sections": [
    {"start": "原文摘录(前20字)", "reason": "具体AI特征", "severity": "high|medium|low"},
  ],
  "summary": "一句话概括主要问题"
}
```
"""


class AIDetector:
    """AI 痕迹检测 Agent"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model
        if client and model:
            self._resilient = ResilientLLMClient(client, model)

    def detect(self, text: str) -> dict:
        """检测文本中的 AI 痕迹"""
        if not self.client or not self.model:
            return self._offline_detect(text)

        try:
            response = self._resilient.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": DETECTOR_SYSTEM},
                    {"role": "user", "content": f"请检测以下文本的AI痕迹:\n\n{text[:4000]}"},
                ],
                temperature=0.2,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            log.info(f"AIDetector: score={result.get('ai_score', 0)}")
            return result
        except Exception as e:
            log.error(f"AIDetector failed: {e}")
            return self._offline_detect(text)

    @staticmethod
    def analyze_prose_craft(text: str) -> dict:
        """零 LLM 成本、毫秒级中文小说文风体检与可操作改稿建议。"""
        import math
        import re as _re

        clean = (text or "").strip()
        if not clean:
            return {
                "ai_score": 0,
                "craft_score": 100,
                "char_count": 0,
                "paragraph_count": 0,
                "dialogue_ratio_pct": 0.0,
                "sentence_rhythm_cv": 0.0,
                "sensory_per_1k": 0.0,
                "flagged_sections": [],
                "suggestions": [],
                "suggested_revision_prompt": "",
                "summary": "正文为空",
                "offline": True,
            }

        cliche_groups = {
            "心理动词直给": (
                "他感到", "她感到", "我感到", "他意识到", "她意识到", "他明白", "她明白",
                "他决定", "她决定", "心中暗想", "心里明白", "不禁想到",
            ),
            "情绪直白标签": (
                "心中充满", "内心深处", "一股难以言喻", "莫名的情绪", "复杂的情绪",
                "百感交集", "五味杂陈", "悲伤不已", "愤怒不已", "心中一紧",
            ),
            "套路动作神态": (
                "嘴角勾起一抹", "眼中闪过一丝", "深吸一口气", "缓缓开口", "淡淡地说道",
                "微微一笑", "眉头微皱", "空气仿佛凝固", "瞳孔骤缩", "倒吸一口凉气",
            ),
            "AI总结过渡词": (
                "与此同时", "在这个过程中", "随着……的发展", "这不仅仅", "标志着",
                "值得一提的是", "本质上", "毫无疑问", "不可否认", "总而言之",
            ),
        }

        flagged = []
        category_hits = {}
        total_cliche_hits = 0
        for category, patterns in cliche_groups.items():
            cat_count = 0
            for pat in patterns:
                cnt = clean.count(pat)
                if cnt > 0:
                    cat_count += cnt
                    total_cliche_hits += cnt
                    idx = clean.find(pat)
                    excerpt = clean[max(0, idx - 6):min(len(clean), idx + len(pat) + 12)].replace("\n", " ")
                    flagged.append({
                        "start": excerpt,
                        "pattern": pat,
                        "category": category,
                        "count": cnt,
                        "reason": f"{category}：「{pat}」(出现{cnt}次)",
                        "severity": "high" if cnt >= 2 else "medium",
                    })
            if cat_count > 0:
                category_hits[category] = cat_count

        # 句长节奏分析（变异系数 CV 越高说明长短句交替越自然）
        raw_sentences = _re.split(r"[。！？!?…]+", clean)
        sent_lengths = [len(s.strip()) for s in raw_sentences if len(s.strip()) >= 2]
        rhythm_cv = 0.0
        if len(sent_lengths) >= 4:
            mean_len = sum(sent_lengths) / len(sent_lengths)
            if mean_len > 0:
                variance = sum((l - mean_len) ** 2 for l in sent_lengths) / len(sent_lengths)
                rhythm_cv = round(math.sqrt(variance) / mean_len, 2)
                if rhythm_cv < 0.35:
                    flagged.append({
                        "start": "整体句长节奏",
                        "pattern": "rhythm_flat",
                        "category": "节奏单调",
                        "count": len(sent_lengths),
                        "reason": f"句长过于均匀（变异系数 {rhythm_cv:.2f} < 0.35），建议增加短句锤击与长短交替",
                        "severity": "high",
                    })

        # 对话占比
        dialogue_matches = _re.findall(r'[“「『"][^”」』"]+[”」』"]', clean)
        dialogue_chars = sum(len(m) for m in dialogue_matches)
        dialogue_ratio = round(dialogue_chars / max(len(clean), 1) * 100, 1)

        # 非视觉感官密度（听觉/嗅觉/触觉/味觉/温度）
        sensory_cues = (
            "声音", "响声", "脚步", "呼吸", "低语", "嘶哑", "风声", "雨声", "回声",
            "气味", "香味", "血腥", "铁锈", "潮湿", "烟味", "苦涩", "咸味",
            "冰冷", "滚烫", "刺痛", "粗糙", "发麻", "发抖", "汗水", "微凉", "灼热",
        )
        sensory_hits = sum(clean.count(cue) for cue in sensory_cues)
        sensory_per_1k = round(sensory_hits / max(len(clean) / 1000.0, 0.2), 1)

        paragraphs = [p.strip() for p in clean.split("\n") if p.strip()]

        # 综合计算 AI 痕迹分（0-100，越低越自然）与文风质感分（0-100，越高越好）
        cliche_density = total_cliche_hits / max(len(clean) / 1000.0, 0.5)
        ai_score = min(100, int(cliche_density * 14) + (20 if rhythm_cv and rhythm_cv < 0.35 else 0))
        craft_score = max(0, min(100, 100 - ai_score - (8 if len(clean) >= 800 and sensory_per_1k < 1.0 else 0)))

        suggestions = []
        revision_parts = []
        if category_hits.get("心理动词直给") or category_hits.get("情绪直白标签"):
            suggestions.append("减少“感到/意识到/心中充满”等心理直给，改用具体动作、停顿或物件细节展现情绪。")
            revision_parts.append("删去“感到/意识到/心中充满”等心理直述，改用人物动作、神态细节和留白来表现情绪")
        if category_hits.get("套路动作神态"):
            top_p = [f["pattern"] for f in flagged if f.get("category") == "套路动作神态"][:3]
            suggestions.append(f"替换高频网文套路动作（如 {'、'.join(top_p)}），换成贴合当前场景的具体动作。")
            revision_parts.append(f"替换“{'、'.join(top_p)}”等高频套路动作，改写为更具画面感的具体动作")
        if category_hits.get("AI总结过渡词"):
            suggestions.append("去掉“与此同时/这不仅仅/标志着”等论文式过渡或总结句，直接切入下一镜头。")
            revision_parts.append("去掉“与此同时/这不仅仅/标志着”等过渡和总结句，保留留白")
        if rhythm_cv and rhythm_cv < 0.35:
            suggestions.append("当前句长过于均匀，建议在冲突处加入 10 字以内的短句，拉开长短句节奏。")
            revision_parts.append("拉开长短句节奏，在冲突或转折处加入短句锤击")
        if len(clean) >= 800 and sensory_per_1k < 1.0:
            suggestions.append("非视觉感官细节偏少，可补充 1-2 处声音、气味、温度或触感描写增强沉浸感。")
            revision_parts.append("补充 1-2 处声音、气味或触感等非视觉细节增强镜头感")
        if len(clean) >= 1000 and dialogue_ratio < 10.0:
            suggestions.append(f"当前对话占比仅 {dialogue_ratio}%，叙述偏密，可适当增加带潜台词的人物对话。")

        flagged.sort(key=lambda x: (0 if x["severity"] == "high" else 1, -x.get("count", 1)))
        summary = (
            f"文风质感 {craft_score}/100 · AI 痕迹 {ai_score}/100（命中 {total_cliche_hits} 处高频套话，对话占比 {dialogue_ratio}%）"
            if total_cliche_hits or ai_score > 0
            else f"文风质感 {craft_score}/100 · 未检出明显 AI 套话（对话占比 {dialogue_ratio}%，节奏 CV {rhythm_cv:.2f}）"
        )
        return {
            "ai_score": ai_score,
            "craft_score": craft_score,
            "char_count": len(clean),
            "paragraph_count": len(paragraphs),
            "dialogue_ratio_pct": dialogue_ratio,
            "sentence_rhythm_cv": rhythm_cv,
            "sensory_per_1k": sensory_per_1k,
            "category_hits": category_hits,
            "flagged_sections": flagged[:10],
            "suggestions": suggestions,
            "suggested_revision_prompt": "；".join(revision_parts) + "。" if revision_parts else "",
            "summary": summary,
            "offline": True,
        }

    @staticmethod
    def _offline_detect(text: str) -> dict:
        """离线规则检测（委托给升级后的 analyze_prose_craft，保持返回字段向后兼容）"""
        return AIDetector.analyze_prose_craft(text)

# ═══════════════════════════════════════════
# Human Rewriter Agent
# ═══════════════════════════════════════════

REWRITER_SYSTEM = """你是一位文字改写专家。你的任务是把"AI味儿"的文字改写成"人味儿"。

## 改写原则（不是规则，是原则）

### 1. 展现，不告知
- 不写"他很愤怒" → 写他做了什么（砸杯子？沉默？笑了？）
- 不写"她很悲伤" → 写她没做的事（没吃饭？没回消息？把照片收起来了？）
- 不写"气氛紧张" → 写具体的感官细节（钟的声音？没人敢动？汗？）

### 2. 句长呼吸
- 短句（≤10字）用来锤击
- 中句（15-25字）用来叙事
- 长句（>30字）用来沉浸
- 三种交替出现，像呼吸

### 3. 段落格式
- 段落按语义单元自然组织（动作完成、焦点转移或对话换人时分段），避免每句都用空行隔开。
- 禁止把每句都用空行隔开。这是AI最明显的特征。
- 对话场景的连续对话在同一段内，不要每句话一段。

### 4. 留白
- 不要把因果关系写全
- 不要把角色的心理活动都交代清楚
- 让读者自己想——他们想出来的永远比你写出来的好

### 5. 具体的感官
- 每200字至少一处非视觉感官：声音、气味、触感、温度
- "茶是苦的""风里有铁锈味""布料磨得皮肤发痒"

### 6. 对话的潜台词
- 角色说的和想的不一样
- 对话不是传信息——是博弈、试探、回避
- 沉默也是对话的一部分

### 7. 动作的质感
- 不写"他快步走进房间" → 写"门撞在墙上弹回来"
- 不写"他小心翼翼地拿起杯子" → 写"两个手指捏着杯沿，像捏一片刀片"

### 8. 尺度保真（v2.6）
- 原文中的亲密场景、身体接触、欲望张力描写，改写时**保留原尺度**，不得弱化、删减或"文学化"成含蓄表达
- 只改表达方式的"人味儿"，不改内容的露骨程度

## 输出
直接输出改写后的文字。按语义单元自然分段。保持原意，但用"人"的方式写。只输出改写内容，不要说明。"""


class HumanRewriter:
    """人类化改写 Agent"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model
        if client and model:
            self._resilient = ResilientLLMClient(client, model)

    def rewrite(self, text: str, detection: dict = None, 
                examples: str = "", target_length: int = None) -> str:
        """改写 AI 文本为更自然的文风

        Args:
            text: 需要改写的文本
            detection: AI检测结果（用于定位问题）
            examples: 真人写作示例（few-shot）
            target_length: 目标字数

        长文本（>5000 字）按段落边界分块改写后再拼接，避免只改写开头
        导致中部/结尾内容静默丢失。
        """
        if not self.client or not self.model:
            return text  # 离线模式原样返回

        # 构建改写 prompt 的公共素材
        issues = ""
        if detection and detection.get("flagged_sections"):
            issues = "## 需要修正的问题\n"
            for f in detection["flagged_sections"][:5]:
                issues += f"- {f.get('reason', '')}\n"

        example_block = ""
        if examples:
            example_block = f"\n## 真人写作参考\n{examples[:2000]}\n"

        # 长文本分块改写（按段落边界，避免切断句子）
        chunks = self._split_chunks(text, max_chars=4000) if len(text) > 5000 else [text]
        if len(chunks) > 1:
            log.info(f"HumanRewriter: rewriting {len(text)} chars in {len(chunks)} chunks")
            parts = []
            for chunk in chunks:
                part = self._rewrite_single(chunk, issues, example_block, target_length,
                                            is_fragment=True)
                if not part:
                    # 单块失败保留原文块，绝不丢内容
                    parts.append(chunk)
                else:
                    parts.append(part)
            return "\n\n".join(parts)

        return self._rewrite_single(text, issues, example_block, target_length, is_fragment=False)

    @staticmethod
    def _split_chunks(text: str, max_chars: int = 4000) -> list:
        """按段落边界把长文本切成不超过 max_chars 的块（段间空行拼接）。"""
        paragraphs = text.split("\n\n")
        chunks = []
        current = ""
        for para in paragraphs:
            candidate = (current + "\n\n" + para) if current else para
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = para
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    def _rewrite_single(self, text: str, issues: str, example_block: str,
                        target_length: int = None, is_fragment: bool = False) -> str:
        """改写单块文本（<5000 字）。失败时返回空串，由调用方决定保留原文。"""
        fragment_hint = "（这是章节的一个片段，请只改写本片段内容，不要重复前文或自行收尾。）" if is_fragment else ""
        len_hint = f"目标字数约{target_length}字（±20%）。" if target_length else ""

        prompt = f"""请改写以下文本，保留核心情节和对话，但用更自然、更像人写的方式表达。{fragment_hint}

{issues}
{example_block}
## 原文
{text}

{len_hint}

只输出改写后的正文。"""

        try:
            response = self._resilient.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": REWRITER_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=8000,
            )
            result = response.choices[0].message.content or ""
            log.info(f"HumanRewriter: {len(text)} chars -> {len(result)} chars")
            return result
        except Exception as e:
            log.error(f"HumanRewriter failed: {e}")
            return ""


# ═══════════════════════════════════════════
# Pipeline: Detect → Rewrite
# ═══════════════════════════════════════════

def humanize_pipeline(text: str, detector: AIDetector, rewriter: HumanRewriter,
                      scene_desc: str = "", target_length: int = None,
                      min_score_threshold: int = 30,
                      skip_verify: bool = False) -> dict:
    """完整的人类化管线

    Args:
        skip_verify: v2.6 批量模式 — 改写后跳过再检测（省1次LLM调用），
                     用于批量生成场景（速度优先，检测+改写已足够）

    Returns:
        {"text": final_text, "ai_score_before": int, "ai_score_after": int, 
         "rewritten": bool, "detection": dict}
    """
    from .writing_examples import get_examples_for_scene
    
    # Step 1: 检测
    detection = detector.detect(text)
    ai_score = detection.get("ai_score", 0)
    
    if ai_score < min_score_threshold and not detection.get("offline"):
        return {"text": text, "ai_score_before": ai_score, "ai_score_after": ai_score,
                "rewritten": False, "detection": detection}
    
    # Step 2: 改写
    examples = get_examples_for_scene(scene_desc)
    rewritten = rewriter.rewrite(text, detection, examples, target_length)
    
    if not rewritten or len(rewritten) < len(text) * 0.8:
        # 改写结果显著短于原文（>20% 内容丢失）→ 保留原稿，绝不静默截断正文。
        return {"text": text, "ai_score_before": ai_score, "ai_score_after": ai_score,
                "rewritten": False, "detection": detection, "error": "rewrite too short"}
    
    # v2.6: 批量模式跳过再检测（省 1 次 LLM 调用，改写结果直接采用）
    if skip_verify:
        return {
            "text": rewritten,
            "ai_score_before": ai_score,
            "ai_score_after": ai_score,
            "rewritten": True,
            "detection": detection,
        }
    
    # Step 3: 再检测
    detection2 = detector.detect(rewritten)
    ai_score2 = detection2.get("ai_score", 0)
    
    # 如果改写后分数更差，用原稿
    final_text = rewritten if ai_score2 <= ai_score else text
    
    return {
        "text": final_text, 
        "ai_score_before": ai_score, 
        "ai_score_after": ai_score2,
        "rewritten": final_text != text,
        "detection": detection,
    }
