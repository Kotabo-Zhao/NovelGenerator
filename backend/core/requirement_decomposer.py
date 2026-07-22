"""NovelGenerator — Requirement Decomposer Agent: 灵感逐层拆解为可执行子任务

职责: 接收用户输入的小说核心灵感，拆解为结构化的可执行子任务列表。
每个子任务包含: 类别、描述、预期输出、质量标准、优先级。
"""

import json
import logging
from typing import Optional
from openai import OpenAI

log = logging.getLogger(__name__)

DECOMPOSER_SYSTEM = """你是一位资深小说策划编辑，专精于将模糊的创意灵感拆解为精确可执行的具体任务。

## 你的任务

分析用户的小说灵感，将其拆解为可逐条验证的子任务。每个子任务必须是：
1. **可执行的**: 能用一条具体的 LLM 指令完成
2. **可验证的**: 完成后可以判断是否达标
3. **覆盖全面的**: 覆盖世界观、角色、情节、风格、结构五个维度

## 输出格式

```json
{
  "summary": "用户需求一句话概括",
  "subtasks": [
    {
      "id": "R001",
      "category": "worldbuilding|character|plot|style|structure",
      "title": "子任务简短标题",
      "description": "具体要做什么，包含细节要求",
      "expected_output": "期望的输出内容描述",
      "quality_criteria": ["达标标准1", "达标标准2"],
      "priority": "P0|P1|P2",
      "depends_on": []
    }
  ],
  "total_count": 0
}
```

## 拆解原则

- 世界观: 时代背景、地理、力量体系、势力分布
- 角色: 主角完整档案、配角设定、反派动机、角色关系
- 情节: 三幕结构、卷章规划、关键转折、结局设计
- 风格: 文笔特征、对话风格、节奏控制、情感基调
- 结构: 章节分配、视角选择、悬念布局、伏笔规划

用户说"随便"或没提到的维度，也要拆解出来并标注"由AI自由发挥"，保证完整性。

只输出 JSON，不要其他内容。"""


class RequirementDecomposer:
    """需求拆解 Agent — 灵感 → 结构化子任务列表"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model

    def decompose(self, inspiration: str, plan: dict = None, 
                  existing_requirements: dict = None) -> dict:
        """将用户灵感拆解为子任务列表
        
        Args:
            inspiration: 用户输入的核心灵感
            plan: 已有的 plan 数据（如果有，用于增量拆解）
            existing_requirements: 已有的需求（用于追加/修改）
            
        Returns:
            {"summary": str, "subtasks": [...], "total_count": int, "created_at": str}
        """
        if not self.client or not self.model:
            return self._offline_decompose(inspiration, plan)

        context_parts = ["## 用户灵感", inspiration]
        
        if plan:
            # 如果已有 plan，分析现有内容与用户需求的差距
            context_parts.append("\n## 已有创作方案")
            outline = plan.get("outline", {})
            chars = plan.get("characters", {})
            wb = plan.get("worldbuilding", {})
            context_parts.append(f"- 书名: {plan.get('title', '')}")
            context_parts.append(f"- 题材: {plan.get('genre', '')}  风格: {plan.get('style', '')}")
            context_parts.append(f"- 总章数: {outline.get('total_chapters', 0)}")
            context_parts.append(f"- 主角: {chars.get('protagonist', {}).get('name', '')}")
            context_parts.append(f"- 世界观: {wb.get('era', '')} / {wb.get('power_system', '')}")
        
        if existing_requirements:
            context_parts.append(f"\n## 已有需求（需追加/修改）")
            context_parts.append(json.dumps(existing_requirements, ensure_ascii=False)[:1000])

        user_prompt = "\n".join(context_parts)
        user_prompt += "\n\n请将以上灵感拆解为可执行子任务。每个子任务都必须有明确的验证标准。只输出 JSON。"

        log.info(f"RequirementDecomposer: analyzing '{inspiration[:80]}...'")

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": DECOMPOSER_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            result = json.loads(content)
            
            # 补充元数据
            result["original_inspiration"] = inspiration
            result["created_at"] = __import__("datetime").datetime.now().isoformat()
            result["status"] = "active"
            
            # 初始化所有子任务状态
            for t in result.get("subtasks", []):
                t["status"] = t.get("status", "pending")
                t["feedback"] = t.get("feedback", "")
                t["retry_count"] = t.get("retry_count", 0)
            
            log.info(f"RequirementDecomposer: {len(result.get('subtasks', []))} subtasks generated")
            return result

        except json.JSONDecodeError as e:
            log.error(f"RequirementDecomposer JSON parse failed: {e}")
            return self._offline_decompose(inspiration, plan)
        except Exception as e:
            log.error(f"RequirementDecomposer failed: {e}")
            return self._offline_decompose(inspiration, plan)

    def _offline_decompose(self, inspiration: str, plan: dict = None) -> dict:
        """离线降级：基于关键词启发式拆解"""
        subtasks = []
        rid = 1

        # 世界观
        if any(kw in inspiration for kw in ["世界", "时代", "大陆", "王朝", "宗门", "帝国", "宇宙"]):
            subtasks.append(self._make_task(rid, "worldbuilding", "P0",
                f"构建世界观设定：{inspiration}",
                "包含时代背景、地理环境、力量体系、势力分布",
                ["世界设定完整且自洽", "力量体系有明确的层级和规则", "至少有3个势力/组织"]))
            rid += 1

        # 角色
        if any(kw in inspiration for kw in ["主角", "角色", "英雄", "反派", "配角", "人", "王", "帝"]):
            subtasks.append(self._make_task(rid, "character", "P0",
                f"设计核心角色体系：{inspiration}",
                "包含主角完整档案(姓名/身份/性格/动机/金手指/成长弧) + 2-4个配角 + 1-2个反派",
                ["主角有明确的性格缺陷和成长弧线", "配角各有用处(盟友/导师/对手/爱慕)", "反派动机合理，不是纯粹的恶"]))
            rid += 1

        # 情节
        if any(kw in inspiration for kw in ["故事", "情节", "剧情", "冒险", "战斗", "复仇", "成长", "修炼"]):
            subtasks.append(self._make_task(rid, "plot", "P0",
                f"规划故事主线：{inspiration}",
                "包含三幕结构、关键转折点、高潮和结局",
                ["三幕结构完整", "每幕有明确的功能和情绪", "关键转折点不少于3个"]))
            rid += 1

        # 风格
        subtasks.append(self._make_task(rid, "style", "P1",
            f"确定文笔风格与叙事基调：{inspiration}",
            "包含文笔特征、对话风格、节奏控制、情感基调",
            ["风格描述具体可用", "有明确的标志性写法", "有禁用的AI套路"]))
        rid += 1

        # 结构
        subtasks.append(self._make_task(rid, "structure", "P1",
            f"规划章节结构：{inspiration}",
            "包含卷章分配、视角选择、悬念布局",
            ["章节数合理(10-40章)", "每章有明确的冲突和钩子", "视角选择一致"]))
        rid += 1

        # 兜底：至少有一个任务
        if not subtasks:
            subtasks.append(self._make_task(1, "plot", "P0",
                f"根据灵感创作完整故事：{inspiration}",
                "生成覆盖世界观、角色、情节的完整创作方案",
                ["内容覆盖用户提到的主要方向", "逻辑自洽", "创意有记忆点"]))

        return {
            "original_inspiration": inspiration,
            "summary": f"用户需求: {inspiration[:100]}",
            "subtasks": subtasks,
            "total_count": len(subtasks),
            "created_at": __import__("datetime").datetime.now().isoformat(),
            "status": "active",
            "offline_mode": True,
        }

    @staticmethod
    def _make_task(rid, category, priority, title, expected, criteria):
        return {
            "id": f"R{rid:03d}",
            "category": category,
            "title": title,
            "description": title,
            "expected_output": expected,
            "quality_criteria": criteria,
            "priority": priority,
            "depends_on": [],
            "status": "pending",
            "feedback": "",
            "retry_count": 0,
        }

    def update_requirements(self, requirements: dict, 
                            new_inspiration: str) -> dict:
        """追加/修改需求后重新拆解，标记变更项
        
        Returns: 合并后的需求，新增/修改的任务带有 change_type 标记
        """
        # 标记旧任务
        old_subtasks = requirements.get("subtasks", [])
        old_ids = {t["id"] for t in old_subtasks}
        
        # 重新拆解
        new_req = self.decompose(new_inspiration)
        new_subtasks = new_req.get("subtasks", [])
        
        # 对比: 新增的标记为 "new"，已有的保留
        for t in new_subtasks:
            if t["id"] not in old_ids:
                t["change_type"] = "new"
        
        # 合并
        merged = dict(requirements)
        merged["subtasks"] = old_subtasks + [t for t in new_subtasks if t["id"] not in old_ids]
        merged["total_count"] = len(merged["subtasks"])
        merged["updated_at"] = __import__("datetime").datetime.now().isoformat()
        merged["status"] = "active"
        
        return merged
