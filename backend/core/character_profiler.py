"""CharacterProfiler — 角色人设蒸馏器（女娲框架移植）

将女娲.skill（github.com/alchaincyf/nuwa-skill, MIT）的思维蒸馏框架
适配为小说角色人设生成：

输入：角色 bible 设定（身份/性格/动机/秘密/弧线/口头禅）+ 可选世界观上下文
输出：结构化人设卡 —
  - mental_models      心智模型（他怎么看世界，3-7 个）
  - decision_heuristics 决策启发式（他怎么做选择，5-10 条）
  - expression_dna      表达 DNA（口癖/句式/称呼/情绪泄漏点，5-8 条）
  - anti_patterns       反模式（他绝不会做的事，3-6 条）
  - boundary            诚实边界（不可变/可演变+触发条件/防崩校验规则）

对虚构角色的两处框架适配：
1. 采集源：无公开资料 → 直接蒸馏现有设定（本地语料模式）
2. 诚实边界：真实人物"不编造"→ 虚构角色"设定一致性边界"
"""
import json
import logging

from .resilient_client import ResilientLLMClient

log = logging.getLogger(__name__)

DISTILL_SYSTEM = """你是小说角色人设蒸馏师。你的工作不是创造新设定，而是从给定的角色设定中，
提炼出"可运行的人设卡"——让任何写作者拿到它都能写出行为一致、说话有辨识度的角色。

你输出五个维度的结构化内容：
1. mental_models（心智模型）：角色用来看世界的透镜。每个模型必须能从给定设定中找到依据，
   禁止凭空添加设定。命名要像"万灵墟等价交换律"这样具体、有小说味。
2. decision_heuristics（决策启发式）：角色做选择时的直觉规则。必须是"当X时，他做Y"的可执行规则，
   冲突和剧情从这里长出来。
3. expression_dna（表达DNA）：说话习惯——口头禅、句式偏好、称呼模式、情绪泄漏点、沉默语义。
   每条都要给出具体表现。
4. anti_patterns（反模式）：这个角色绝不会做的事。这是角色的底线，也是防崩人设的护栏。
5. boundary（诚实边界）：不可变底线（任何章节不得违反）、可演变点（必须给出事件触发条件）、
   以及写作时的防崩校验规则（如"角色长篇大论讲道理则违规"）。

要求：
- 全部内容必须基于给定设定推导，可以在合理范围内深化，但不得与设定冲突、不得新造背景
- 数量：心智模型 3-7 个，决策启发式 5-10 条，表达DNA 5-8 条，反模式 3-6 条
- 每条都要具体、可执行，拒绝空话（如"他很坚强"不合格，"重伤时独自硬撑到无人处才咳血"合格）
- 只输出 JSON，不要任何解释文字"""


class CharacterProfiler:
    """角色人设蒸馏器 — 用 LLM 从 bible 设定中提炼可运行人设卡"""

    def __init__(self, client, model: str):
        self.client = client
        self.model = model
        self._resilient = ResilientLLMClient(client, model)

    def distill(self, bible: dict, char_name: str, worldbuilding_summary: str = "") -> dict:
        """蒸馏指定角色的人设卡

        Args:
            bible: character_bible.json 内容
            char_name: 角色名（主角或配角）
            worldbuilding_summary: 世界观摘要（可选，帮助理解角色行为背景）
        Returns:
            结构化人设卡 dict；失败返回 {"error": ...}
        """
        char_data = self._extract_character(bible, char_name)
        if char_data is None:
            return {"error": f"角色「{char_name}」不在人物宝典中"}

        prompt = self._build_prompt(char_name, char_data, worldbuilding_summary)
        result = self._call_llm(prompt)
        if not result:
            return {"error": "人设蒸馏失败（LLM 输出解析失败），请重试"}

        result["character"] = char_name
        return result

    # ── 内部 ──

    def _extract_character(self, bible: dict, char_name: str) -> dict | None:
        """从 bible 中提取角色设定块"""
        if not bible or not isinstance(bible, dict):
            return None
        protagonist = bible.get("protagonist") or {}
        if protagonist.get("name") == char_name:
            return {"role": "主角", **protagonist}
        for c in bible.get("supporting", []) or []:
            if isinstance(c, dict) and c.get("name") == char_name:
                return {"role": "配角", **c}
        for c in bible.get("antagonist", []) or []:
            if isinstance(c, dict) and c.get("name") == char_name:
                return {"role": "反派", **c}
        return None

    def _build_prompt(self, char_name: str, char_data: dict, worldbuilding_summary: str) -> str:
        """构建蒸馏 prompt"""
        fields = []
        for key, label in [
            ("role", "角色定位"),
            ("identity", "身份"),
            ("personality", "性格"),
            ("motivation", "动机"),
            ("secret", "秘密"),
            ("arc", "成长弧线"),
            ("catchphrase", "口头禅"),
            ("meaning", "象征意义"),
        ]:
            val = char_data.get(key)
            if val:
                fields.append(f"- {label}: {val}")

        supporting_context = ""
        if worldbuilding_summary:
            supporting_context = f"\n## 世界观背景（供理解角色行为）\n{worldbuilding_summary[:800]}\n"

        return f"""## 角色设定：{char_name}（{char_data.get('role', '')}）
{chr(10).join(fields)}

{supporting_context}
请基于以上设定，为「{char_name}」蒸馏完整的人设卡。"""

    def _call_llm(self, prompt: str) -> dict | None:
        """调用 LLM 并解析 JSON（带重试）"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": DISTILL_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=3000,
                extra_body={"thinking": {"type": "disabled"}} if "v4" in self.model else None,
            )
            content = response.choices[0].message.content or ""
            return self._parse_json(content)
        except Exception as e:
            log.warning(f"CharacterProfiler LLM call failed: {e}")
            # 降级：走韧性客户端重试
            try:
                content = self._resilient.create(
                    messages=[
                        {"role": "system", "content": DISTILL_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.7,
                    max_tokens=3000,
                )
                return self._parse_json(content) if isinstance(content, str) else content
            except Exception as e2:
                log.error(f"CharacterProfiler retry failed: {e2}")
                return None

    @staticmethod
    def _parse_json(content: str) -> dict | None:
        """健壮 JSON 解析（容忍 markdown 围栏/前后杂文本）"""
        if not content:
            return None
        text = content.strip()
        # 去掉 ```json ... ``` 围栏
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 提取第一个 { ... } 块
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
        return None
