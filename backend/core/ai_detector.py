"""NovelGenerator — AI Detector & Human Rewriter: 去AI味架构层

Pipeline: 生成初稿 → AI检测(标记AI段落) → 重点改写 → 终稿

不做规则堆砌。用独立的检测→改写循环。
"""

import json
import logging
from typing import Optional
from openai import OpenAI

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

    def detect(self, text: str) -> dict:
        """检测文本中的 AI 痕迹"""
        if not self.client or not self.model:
            return self._offline_detect(text)

        try:
            response = self.client.chat.completions.create(
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
    def _offline_detect(text: str) -> dict:
        """离线规则检测"""
        ai_patterns = [
            "他感到", "她感到", "他意识到", "她意识到", 
            "他决定", "她决定", "他明白", "她明白",
            "心中充满", "内心深处", "真正的", "本质上",
            "与此同时", "在这个过程中", "随着……的发展",
            "这不仅仅", "标志着", "值得一提的是",
        ]
        flagged = []
        for pat in ai_patterns:
            count = text.count(pat)
            if count > 0:
                flagged.append({
                    "start": pat, "reason": f"AI常用句式(出现{count}次)", "severity": "medium"
                })
        
        # 句长单调检测
        sentences = text.replace("！", "。").replace("？", "。").replace("…", "。").split("。")
        lengths = [len(s.strip()) for s in sentences if s.strip()]
        if lengths:
            avg = sum(lengths) / len(lengths)
            similar = sum(1 for l in lengths if abs(l - avg) < 5)
            if similar > len(lengths) * 0.6:
                flagged.append({
                    "start": "整体", "reason": "句长过于均匀(>60%句子长度接近)", "severity": "high"
                })

        score = min(100, len(flagged) * 15)
        return {
            "ai_score": score,
            "flagged_sections": flagged[:8],
            "summary": f"检测到 {len(flagged)} 处AI痕迹" if flagged else "未检测到明显AI痕迹",
            "offline": True,
        }


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

### 3. 留白
- 不要把因果关系写全
- 不要把角色的心理活动都交代清楚
- 让读者自己想——他们想出来的永远比你写出来的好

### 4. 具体的感官
- 每200字至少一处非视觉感官：声音、气味、触感、温度
- "茶是苦的""风里有铁锈味""布料磨得皮肤发痒"

### 5. 对话的潜台词
- 角色说的和想的不一样
- 对话不是传信息——是博弈、试探、回避
- 沉默也是对话的一部分

### 6. 动作的质感
- 不写"他快步走进房间" → 写"门撞在墙上弹回来"
- 不写"他小心翼翼地拿起杯子" → 写"两个手指捏着杯沿，像捏一片刀片"

## 输出

直接输出改写后的文字。保持原意，但用"人"的方式写。只输出改写内容，不要说明。"""


class HumanRewriter:
    """人类化改写 Agent"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model

    def rewrite(self, text: str, detection: dict = None, 
                examples: str = "", target_length: int = None) -> str:
        """改写 AI 文本为更自然的文风
        
        Args:
            text: 需要改写的文本
            detection: AI检测结果（用于定位问题）
            examples: 真人写作示例（few-shot）
            target_length: 目标字数
        """
        if not self.client or not self.model:
            return text  # 离线模式原样返回

        # 构建改写 prompt
        issues = ""
        if detection and detection.get("flagged_sections"):
            issues = "## 需要修正的问题\n"
            for f in detection["flagged_sections"][:5]:
                issues += f"- {f.get('reason', '')}\n"

        example_block = ""
        if examples:
            example_block = f"\n## 真人写作参考\n{examples[:2000]}\n"

        len_hint = f"目标字数约{target_length}字（±20%）。" if target_length else ""

        prompt = f"""请改写以下文本，保留核心情节和对话，但用更自然、更像人写的方式表达。

{issues}
{example_block}
## 原文
{text[:5000]}

{len_hint}

只输出改写后的正文。"""

        log.info(f"HumanRewriter: rewriting {len(text)} chars")

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": REWRITER_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=8000,
            )
            result = response.choices[0].message.content
            log.info(f"HumanRewriter: output {len(result)} chars")
            return result
        except Exception as e:
            log.error(f"HumanRewriter failed: {e}")
            return text


# ═══════════════════════════════════════════
# Pipeline: Detect → Rewrite
# ═══════════════════════════════════════════

def humanize_pipeline(text: str, detector: AIDetector, rewriter: HumanRewriter,
                      scene_desc: str = "", target_length: int = None,
                      min_score_threshold: int = 30) -> dict:
    """完整的人类化管线
    
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
    
    if not rewritten or len(rewritten) < len(text) * 0.3:
        return {"text": text, "ai_score_before": ai_score, "ai_score_after": ai_score,
                "rewritten": False, "detection": detection, "error": "rewrite too short"}
    
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
