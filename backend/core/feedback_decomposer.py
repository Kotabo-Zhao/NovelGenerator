"""NovelGenerator — Feedback Decomposer: 反馈语义拆解 Agent

职责: 深度理解用户自然语言修改意见，拆解为具体可执行的结构化修改指令。
与旧版 parse_feedback() 的本质区别:
- 旧: 关键词匹配 → 分类标签 → 原文塞入prompt → 靠LLM自己猜
- 新: LLM深度语义分析 → 逐条拆解 → 具体修改指令 + 验证标准 → 精确驱动regeneration

核心能力:
1. 语义意图识别: 理解用户"为什么改""改什么""期望什么效果"
2. 上下文感知: 读取当前大纲结构，定位受影响的具体章节
3. 指令拆解: 一条模糊意见 → N条具体可执行指令
4. 验证标准生成: 每条指令附带验证条件，确保修改确实生效
5. 影响范围计算: 精确到章节号，避免过度修改
"""

import json
import re
import logging
from typing import Optional
from openai import OpenAI

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# System Prompt — 反馈拆解专家
# ═══════════════════════════════════════════════

DECOMPOSER_SYSTEM = """你是一位资深小说编辑和需求分析师。你的唯一职责是:
将用户对小说大纲的修改意见，拆解为精确、可执行的结构化修改指令。

## 工作流程

1. **理解意图**: 用户到底想改什么？深层动机是什么？
2. **定位目标**: 在大纲中找到需要修改的具体章节/卷
3. **拆解指令**: 一条模糊意见 → 多条具体操作
4. **生成提示**: 为每条操作生成精确的 LLM 提示词（不是笼统描述）
5. **设定验证**: 修改完成后如何验证是否生效

## 输出格式

必须输出严格 JSON:
```json
{
  "intent_analysis": {
    "summary": "一句话概括用户意图",
    "deep_motivation": "深层动机分析",
    "expected_effect": "期望达到的效果"
  },
  "change_plan": [
    {
      "id": "C001",
      "priority": 1,
      "action": "modify|add|remove|reorder",
      "target": {
        "scope": "volume|chapter|character|plot_point",
        "volume_numbers": [],
        "chapter_numbers": [],
        "description": "具体目标描述"
      },
      "what_to_change": "要改什么（具体到字段/内容）",
      "change_to": "改成什么（具体的替代内容）",
      "reason": "为什么这样改（关联用户意图）",
      "regeneration_prompt": "为LLM生成精确的、可直接使用的修改提示词(200字内)",
      "validation": "修改后如何验证是否生效",
      "affected_aspects": ["outline", "characters", "worldbuilding"]
    }
  ],
  "impact_summary": "修改影响范围总览",
  "estimated_changes": "预计修改的章节数量"
}
```

**关于 affected_aspects** — 每项 action 必须包含此字段，标记该修改涉及哪些数据层面:
- `"outline"` — 几乎所有修改都涉及（章节结构/内容）
- `"characters"` — 涉及角色身份/性格/背景/关系/能力/弧线的修改
- `"worldbuilding"` — 涉及世界观设定/时代背景/力量体系/组织架构的修改
- **示例**: "主角改成将军之后" → affected_aspects: ["outline", "characters"]
- **示例**: "力量体系从灵力改为魔法" → affected_aspects: ["outline", "characters", "worldbuilding"]

## 关键原则

- **具体化**: "节奏太慢" → "第3-7章每章删减1个铺垫场景，增加1个冲突事件"
- **可执行**: 每条指令必须能直接驱动代码/LLM执行，不能是模糊建议
- **最小化**: 只改必须改的，不动用户没提到的部分
- **上下文感知**: 仔细阅读当前大纲结构，准确定位目标章节号
- **保持一致性**: 修改后的部分必须与未修改部分逻辑连贯
- **affected_aspects 是关键**: 必须准确标注修改涉及哪些数据层面。缺少标注会导致角色人设/世界观不同步。
- **regeneration_prompt 是关键**: 这个字段会直接发给大纲生成LLM，必须包含:
  - 具体要改什么章节
  - 当前内容是什么
  - 要改成什么
  - 不能改什么（边界约束）

只输出 JSON，不要任何额外文字。"""


class FeedbackDecomposer:
    """反馈拆解 Agent — 深度语义理解 + 精确指令生成"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model

    # ═══════════════════════════════════════════
    # 公开接口
    # ═══════════════════════════════════════════

    def decompose(self, feedback: str, plan: dict) -> dict:
        """将用户自然语言反馈拆解为结构化修改计划
        
        Args:
            feedback: 用户原始修改意见（自然语言）
            plan: 当前小说的完整 plan 数据（含大纲/世界观/角色）
            
        Returns:
            {
                "intent_analysis": {...},
                "change_plan": [{id, priority, action, target, ...}],
                "impact_summary": str,
                "estimated_changes": int,
                "raw_feedback": str
            }
        """
        if not self.client or not self.model:
            # 离线模式: 返回基础拆解（关键词匹配降级）
            return self._offline_decompose(feedback, plan)

        # 构建当前大纲上下文
        outline_context = self._build_outline_context(plan)
        
        user_prompt = f"""请分析以下用户修改意见，并拆解为精确的结构化修改指令。

## 当前大纲结构

{outline_context}

## 用户修改意见

{feedback}

## 任务

1. 深度理解用户意图（不仅仅是字面意思）
2. 在大纲中定位需要修改的具体章节/卷
3. 逐条拆解为可执行指令
4. 为每条指令生成精确的 regeneration_prompt
5. 设定验证标准

只输出 JSON。"""

        log.info(f"FeedbackDecomposer: analyzing '{feedback[:80]}...'")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": DECOMPOSER_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
            
            content = response.choices[0].message.content
            result = json.loads(content)
            
            # 补充元数据
            result["raw_feedback"] = feedback
            result["decomposed_at"] = __import__("datetime").datetime.now().isoformat()
            
            # 后处理: 确保每个 action 都有 affected_aspects（防止 LLM 漏标）
            for item in result.get("change_plan", []):
                if "affected_aspects" not in item:
                    regen_prompt = item.get("regeneration_prompt", "")
                    item["affected_aspects"] = self._detect_affected_aspects(
                        feedback + " " + regen_prompt, fallback=["outline"]
                    )
                    log.info(f"  [{item.get('id','?')}] auto-detected affected_aspects: {item['affected_aspects']}")
            
            plan_count = len(result.get("change_plan", []))
            log.info(f"FeedbackDecomposer: {plan_count} change actions generated")
            
            # 输出调试信息
            for item in result.get("change_plan", [])[:3]:
                log.info(f"  [{item.get('id','?')}] {item.get('action','?')} → "
                        f"{item.get('target',{}).get('description','?')[:60]}")
            
            return result
            
        except json.JSONDecodeError as e:
            log.error(f"FeedbackDecomposer JSON parse failed: {e}")
            return self._offline_decompose(feedback, plan)
        except Exception as e:
            log.error(f"FeedbackDecomposer failed: {e}")
            return self._offline_decompose(feedback, plan)

    def decompose_for_chapter(self, feedback: str, chapter_num: int, 
                              chapter_outline: dict, plan: dict) -> dict:
        """针对单章反馈的拆解（用于已生成章节的修改意见）
        
        Args:
            feedback: 用户修改意见
            chapter_num: 章节号
            chapter_outline: 该章在大纲中的信息
            plan: 完整 plan
            
        Returns:
            结构化的单章修改计划
        """
        if not self.client or not self.model:
            return self._offline_chapter_decompose(feedback, chapter_num, chapter_outline)
        
        outline_context = self._build_outline_context(plan)
        
        user_prompt = f"""用户对第{chapter_num}章提出了修改意见。请拆解为具体修改指令。

## 当前大纲结构
{outline_context[:2000]}

## 第{chapter_num}章大纲
{json.dumps(chapter_outline, ensure_ascii=False)[:500]}

## 用户修改意见
{feedback}

只输出 JSON 格式的 change_plan（与主 decompose 格式相同）。"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": DECOMPOSER_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            log.error(f"Chapter feedback decompose failed: {e}")
            return self._offline_chapter_decompose(feedback, chapter_num, chapter_outline)

    # ═══════════════════════════════════════════
    # 离线降级模式
    # ═══════════════════════════════════════════

    def _offline_decompose(self, feedback: str, plan: dict) -> dict:
        """无LLM时的规则降级拆解"""
        outline = plan.get("outline", {})
        volumes = outline.get("volumes", [])
        total_chapters = outline.get("total_chapters", 0)
        
        # 提取章节引用
        chapter_nums = self._extract_chapter_refs(feedback)
        volume_nums = self._extract_volume_refs(feedback)
        
        # 意图分析（关键词驱动）
        intent = self._infer_intent(feedback)
        
        # 构建基础 change_plan
        change_plan = []
        
        # 确定影响范围
        if any(kw in feedback for kw in ["多", "加", "增加", "补充", "不够", "太少", "短"]):
            # 扩容型修改
            target_vols = volume_nums or list(range(1, len(volumes) + 1))
            for vn in target_vols[:2]:
                change_plan.append({
                    "id": f"C{len(change_plan)+1:03d}",
                    "priority": 1,
                    "action": "add",
                    "target": {
                        "scope": "volume",
                        "volume_numbers": [vn],
                        "chapter_numbers": [],
                        "description": f"第{vn}卷需要扩展内容"
                    },
                    "what_to_change": f"第{vn}卷章节数不足，需要增加细节",
                    "change_to": f"在第{vn}卷中增加2-3章过渡章节，丰富情节展开",
                    "reason": intent,
                    "regeneration_prompt": f"在第{vn}卷大纲中增加2-3章过渡章节，插入在卷首和关键节点之间。每章需要独立的冲突事件和情绪曲线。保持与前后卷的衔接。",
                    "validation": f"检查第{vn}卷章节数是否增加，新增章节是否有效推进剧情",
                    "affected_aspects": self._detect_affected_aspects(feedback, fallback=["outline"])
                })
        
        if any(kw in feedback for kw in ["删", "减", "去掉", "太多", "太长", "冗", "冗余"]):
            target_chs = chapter_nums or list(range(1, min(11, total_chapters + 1)))
            for cn in target_chs[:3]:
                change_plan.append({
                    "id": f"C{len(change_plan)+1:03d}",
                    "priority": 1,
                    "action": "modify",
                    "target": {
                        "scope": "chapter",
                        "volume_numbers": [],
                        "chapter_numbers": [cn],
                        "description": f"第{cn}章需要精简"
                    },
                    "what_to_change": f"第{cn}章内容冗余",
                    "change_to": f"精简第{cn}章情节，合并重复事件，删减冗余描写",
                    "reason": intent,
                    "regeneration_prompt": f"精简第{cn}章大纲。保留核心冲突和关键转折，删除铺垫性质的辅助事件（可合并到前后章节）。确保精简后仍保持逻辑连贯。",
                    "validation": f"检查第{cn}章摘要是否比原来更简洁，核心事件是否保留",
                    "affected_aspects": self._detect_affected_aspects(feedback, fallback=["outline"])
                })
        
        # 修改/替换型（角色/世界观调整）
        if "改" in feedback and any(kw in feedback for kw in ["主角", "角色", "人设", "身份", "名", "背景", "世界观", "设定"]):
            change_plan.append({
                "id": f"C{len(change_plan)+1:03d}",
                "priority": 1,
                "action": "modify",
                "target": {
                    "scope": "global",
                    "volume_numbers": [],
                    "chapter_numbers": [],
                    "description": "根据用户意见调整设定"
                },
                "what_to_change": feedback,
                "change_to": f"根据用户意见「{feedback[:80]}」调整内容",
                "reason": intent,
                "regeneration_prompt": f"根据以下意见修改大纲及相关设定: {feedback}\n\n如果涉及角色/世界观修改，请同步更新对应数据。",
                "validation": "对比修改前后，确认用户意见涉及的问题已解决",
                "affected_aspects": self._detect_affected_aspects(feedback, fallback=["outline"])
            })
        
        if any(kw in feedback for kw in ["逻辑", "矛盾", "不合理", "漏洞", "冲突", "不一致"]):
            target_chs = chapter_nums or list(range(1, min(11, total_chapters + 1)))
            for cn in target_chs[:3]:
                change_plan.append({
                    "id": f"C{len(change_plan)+1:03d}",
                    "priority": 2,
                    "action": "modify",
                    "target": {
                        "scope": "chapter",
                        "volume_numbers": [],
                        "chapter_numbers": [cn],
                        "description": f"修复第{cn}章逻辑问题"
                    },
                    "what_to_change": f"第{cn}章存在逻辑矛盾",
                    "change_to": f"重新设计第{cn}章因果链，确保事件有合理铺垫",
                    "reason": intent,
                    "regeneration_prompt": f"重新规划第{cn}章大纲，修复逻辑矛盾。重点检查：(1)事件的因果链是否完整 (2)角色行为是否符合设定 (3)时间线是否一致。如需修改前后章节的衔接，请在指令中说明。",
                    "validation": f"检查第{cn}章事件因果是否合理，前后章节衔接是否顺畅",
                    "affected_aspects": self._detect_affected_aspects(feedback, fallback=["outline"])
                })
        
        if not change_plan:
            # 兜底: 通用修改 — regen_prompt 根据 affected_aspects 动态生成
            affected = self._detect_affected_aspects(feedback, fallback=["outline"])
            preserve_note = []
            if "characters" not in affected:
                preserve_note.append("角色设定")
            if "worldbuilding" not in affected:
                preserve_note.append("世界观")
            preserve_str = f"保持{'和'.join(preserve_note)}不变。" if preserve_note else ""
            
            change_plan.append({
                "id": "C001",
                "priority": 1,
                "action": "modify",
                "target": {
                    "scope": "global",
                    "volume_numbers": [],
                    "chapter_numbers": [],
                    "description": "根据用户意见进行全局调整"
                },
                "what_to_change": feedback,
                "change_to": f"根据用户意见「{feedback[:80]}」调整大纲",
                "reason": intent,
                "regeneration_prompt": f"根据以下意见重新规划大纲: {feedback}\n\n{preserve_str}重点关注用户提到的具体问题。如果意见涉及特定章节，只修改相关章节。",
                "validation": "对比新旧大纲，确认用户意见涉及的问题已解决",
                "affected_aspects": affected
            })
        
        return {
            "intent_analysis": {
                "summary": f"用户希望: {feedback[:100]}",
                "deep_motivation": intent,
                "expected_effect": "修改后的大纲应符合用户期望"
            },
            "change_plan": change_plan,
            "impact_summary": f"离线模式拆解，影响{len(change_plan)}个修改点",
            "estimated_changes": len(change_plan),
            "raw_feedback": feedback,
            "decomposed_at": __import__("datetime").datetime.now().isoformat(),
            "offline_mode": True,
        }

    def _offline_chapter_decompose(self, feedback, chapter_num, chapter_outline):
        """单章反馈离线拆解"""
        intent = self._infer_intent(feedback)
        return {
            "intent_analysis": {"summary": f"第{chapter_num}章修改: {feedback[:80]}"},
            "change_plan": [{
                "id": "C001",
                "priority": 1,
                "action": "modify",
                "target": {
                    "scope": "chapter",
                    "chapter_numbers": [chapter_num],
                    "description": f"修改第{chapter_num}章"
                },
                "what_to_change": feedback,
                "change_to": f"按照意见修改第{chapter_num}章",
                "reason": intent,
                "regeneration_prompt": f"重新规划第{chapter_num}章大纲。修改意见: {feedback}\n原大纲: {json.dumps(chapter_outline, ensure_ascii=False)[:300]}",
                "validation": "检查修改后的章节是否符合用户意见",
                "affected_aspects": self._detect_affected_aspects(feedback, fallback=["outline"])
            }],
            "raw_feedback": feedback,
            "offline_mode": True,
        }

    # ═══════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════

    def _build_outline_context(self, plan: dict) -> str:
        """构建大纲上下文摘要（供 LLM 理解当前结构）"""
        outline = plan.get("outline", {})
        volumes = outline.get("volumes", [])
        
        if not volumes:
            return "（无现有大纲）"
        
        parts = [f"总章节数: {outline.get('total_chapters', 0)}"]
        parts.append(f"三幕分布: {outline.get('three_act_map', '未知')}")
        parts.append(f"节奏说明: {outline.get('rhythm_notes', '无')}")
        parts.append("")
        parts.append("## 卷/章结构")
        
        for vol in volumes:
            vol_num = vol.get("number", "?")
            title = vol.get("title", "")
            act = vol.get("act", "")
            theme = vol.get("theme", "")
            parts.append(f"\n### 第{vol_num}卷「{title}」({act}) — {theme}")
            
            chapters = vol.get("chapters", [])
            for ch in chapters[:10]:  # 每卷最多展示10章
                ch_num = ch.get("number", "?")
                summary = ch.get("summary", "")
                hook = ch.get("hook", "")
                conflict = ch.get("conflict", "")
                parts.append(f"  Ch{ch_num}: {summary}")
                if hook:
                    parts.append(f"    钩子: {hook[:40]}")
        
        # 附加角色信息
        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})
        if protagonist:
            parts.append(f"\n## 主角信息")
            parts.append(f"  姓名: {protagonist.get('name', '?')}")
            parts.append(f"  成长弧: {protagonist.get('arc', '?')}")
        
        return "\n".join(parts)

    def _extract_chapter_refs(self, text: str) -> list:
        """提取文本中的章节引用"""
        refs = []
        # "第X章"
        for m in re.finditer(r"第\s*(\d+)\s*章", text):
            refs.append(int(m.group(1)))
        # "X-Y章"
        for m in re.finditer(r"(\d+)\s*[-到至]\s*(\d+)\s*章", text):
            refs.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        return sorted(set(refs))

    def _extract_volume_refs(self, text: str) -> list:
        """提取文本中的卷引用"""
        refs = []
        for m in re.finditer(r"第\s*(\d+)\s*卷", text):
            refs.append(int(m.group(1)))
        return sorted(set(refs))

    def _detect_affected_aspects(self, feedback: str, fallback: list = None) -> list:
        """在用户原始反馈上检测影响的数据层面
        
        在原始中文反馈上匹配，比在 LLM 生成的 regen_prompt 上匹配可靠得多。
        返回 ["outline", "characters", "worldbuilding"] 的组合。
        """
        aspects = set(fallback or ["outline"])
        
        # 角色相关关键词 — 覆盖所有可能的表达方式
        char_patterns = [
            r"主角", r"主人公", r"男主", r"女主", r"反派", r"配角", r"角色",
            r"人设", r"性格", r"身世", r"出身", r"背景", r"身份",
            r"金手指", r"能力", r"天赋", r"修为", r"境界", r"实力", r"战力",
            r"成长", r"弧线", r"弧光", r"转变", r"黑化", r"洗白",
            r"后代", r"之后", r"之子", r"孤儿", r"少爷", r"小姐",
        ]
        for pat in char_patterns:
            if pat in feedback:
                aspects.add("characters")
                break
        
        # 世界观相关关键词
        wb_patterns = [
            r"世界观", r"世界", r"时代", r"背景设定", r"力量体系",
            r"修炼体系", r"魔法", r"灵力", r"灵气", r"斗气", r"内力",
            r"宗门", r"皇朝", r"帝国", r"组织", r"势力",
            r"规则", r"法则", r"天道", r"位面", r"界面",
        ]
        for pat in wb_patterns:
            if pat in feedback:
                aspects.add("worldbuilding")
                break
        
        return sorted(aspects)

    def _infer_intent(self, feedback: str) -> str:
        """推断用户深层意图（优先级排序: 角色 > 逻辑 > 节奏 > 长短）"""
        patterns = [
            (r"(?:角色|人物|主角|反派|配角|性格|弧|成长|人设)", "用户对角色塑造有疑问，需要调整角色发展轨迹"),
            (r"(?:逻辑|矛盾|不合理|漏洞|bug|问题|说不通)", "用户发现了情节逻辑问题，需要修复因果链"),
            (r"(?:节奏|太慢|太拖|进展|推进|快慢)", "用户对叙事节奏不满意，希望调整事件密度和张弛比例"),
            (r"(?:太长|太多|冗|删|精简|缩短|减少)", "用户认为某些部分内容过多，希望精简"),
            (r"(?:太短|不够|加|增加|补充|扩展|丰富)", "用户认为内容不够充分，希望扩展"),
            (r"(?:结局|结尾|最后|收尾|高潮)", "用户对结局或高潮部分不满意"),
            (r"(?:结构|布局|分卷|调整|顺序)", "用户希望调整整体结构布局"),
        ]
        for pattern, intent in patterns:
            if re.search(pattern, feedback):
                return intent
        return "用户对大纲有修改意见，需要针对性调整"
