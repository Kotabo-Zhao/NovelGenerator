"""CharacterVoices — 角色声音卡（解决角色同质化，v2.3.6）

问题: 生成的角色说话都是一个味道——配角只有一两句设定，模型自由发挥 → 同质化。
方案: 一次 LLM 调用为全部角色生成"声音卡"（语言指纹），写入 character_voices.json，
写作时注入 writer 上下文 + 硬约束（对话必须能判断是谁说的）。

声音卡结构（每角色）:
  catchphrases      口头禅（1-2 个，含使用场景）
  address_style     称呼模式（对陌生人/对敌人/对亲近的人）
  sentence_style    句式偏好（短句/长句/反问/陈述）
  emotion_expression 情绪表达方式（愤怒时如何表现/紧张时如何表现）
  signature_actions 标志性小动作（紧张咬唇/摩挲刀柄等，用于动作描写区分）
"""
import json
import logging

from .resilient_client import ResilientLLMClient

log = logging.getLogger(__name__)

VOICES_SYSTEM = """你是小说角色语言设计师。你的任务：为一本书的所有角色设计"声音卡"——让每个角色开口就能被认出来。

要求：
- 每个角色给出 5 个维度：catchphrases（口头禅，含使用场景）、address_style（称呼模式：
  对陌生人/对敌人/对亲近的人分别怎么称呼）、sentence_style（句式偏好：短句/长句/反问/陈述、
  有无句末语气词）、emotion_expression（愤怒/紧张/高兴时的语言与动作表现）、
  signature_actions（标志性小动作，1-2 个）
- 所有角色的声音必须**显著不同**：同一个场景里，两个角色的台词绝不能是同一个味道
- 基于设定推导，不新造背景；配角也要有辨识度（哪怕是"爱用比喻的老铁匠"）
- 反派和主角的声音差异要最大（口癖/句式/情绪表达都不同）
- 输出 JSON: {"角色名": {"catchphrases": [...], "address_style": "...", "sentence_style": "...", "emotion_expression": "...", "signature_actions": [...]}}
只输出 JSON。"""


def build_voices_prompt(bible: dict) -> str:
    """从 bible 构建角色声音卡生成 prompt"""
    lines = []
    protagonist = bible.get("protagonist") or {}
    if protagonist:
        lines.append(f"## 主角：{protagonist.get('name', '')}")
        lines.append(f"- 身份: {protagonist.get('identity', '')}")
        lines.append(f"- 性格: {str(protagonist.get('personality', ''))[:200]}")
        lines.append(f"- 口头禅: {protagonist.get('catchphrase', '无')}")
        lines.append("")
    for c in bible.get("supporting", []) or []:
        if isinstance(c, dict) and c.get("name"):
            lines.append(f"## 配角：{c.get('name', '')}")
            lines.append(f"- 身份: {c.get('identity', '')}")
            lines.append(f"- 性格: {str(c.get('personality', ''))[:150]}")
            lines.append(f"- 口头禅: {c.get('catchphrase', '无')}")
            lines.append("")
    for c in bible.get("antagonist", []) or []:
        if isinstance(c, dict) and c.get("name"):
            lines.append(f"## 反派：{c.get('name', '')}")
            lines.append(f"- 身份: {c.get('identity', '')}")
            lines.append(f"- 性格: {str(c.get('personality', ''))[:150]}")
            lines.append("")
    return "\n".join(lines)


class CharacterVoices:
    """角色声音卡生成器"""

    def __init__(self, client, model: str):
        self.client = client
        self.model = model
        self._resilient = ResilientLLMClient(client, model)

    def generate_all(self, bible: dict) -> dict:
        """为 bible 中所有角色生成声音卡（一次 LLM 调用）"""
        if not bible or not (bible.get("protagonist") or bible.get("supporting") or bible.get("antagonist")):
            return {}
        prompt = build_voices_prompt(bible)
        try:
            resp = self._resilient.create(
                messages=[
                    {"role": "system", "content": VOICES_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=2500,
            )
            # ResilientLLMClient.create 返回 response 对象
            content = resp.choices[0].message.content if hasattr(resp, "choices") else resp
            if isinstance(content, str):
                result = self._parse_json(content)
            elif isinstance(content, dict):
                result = content
            else:
                result = None
            if isinstance(result, dict):
                return result
            return {}
        except Exception as e:
            log.warning(f"CharacterVoices generation failed: {e}")
            return {}

    @staticmethod
    def _parse_json(content: str) -> dict | None:
        """容错 JSON 解析"""
        if not content:
            return None
        text = str(content).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None


def build_voices_context(voices: dict, characters: list = None) -> str:
    """构建角色声音表注入文本（限定出场角色，控制 token）"""
    if not voices:
        return ""
    chars = characters or list(voices.keys())
    lines = []
    for name in chars:
        v = voices.get(name)
        if not v or not isinstance(v, dict):
            continue
        parts = []
        if v.get("catchphrases"):
            parts.append("口头禅:" + "、".join(str(x) for x in v["catchphrases"][:2]))
        if v.get("address_style"):
            parts.append("称呼:" + str(v["address_style"])[:40])
        if v.get("sentence_style"):
            parts.append("句式:" + str(v["sentence_style"])[:40])
        if v.get("emotion_expression"):
            parts.append("情绪表现:" + str(v["emotion_expression"])[:40])
        if v.get("signature_actions"):
            parts.append("标志动作:" + "、".join(str(x) for x in v["signature_actions"][:2]))
        if parts:
            lines.append(f"- {name}：{'；'.join(parts)}")
    if not lines:
        return ""
    return "## 🎙 角色声音表（对话必须按此区分，禁止角色说同一个味道的话）\n" + "\n".join(lines)
