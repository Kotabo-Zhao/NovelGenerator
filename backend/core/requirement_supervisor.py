"""NovelGenerator — Requirement Supervisor Agent: 监督生成质量与需求完成度

职责: 对照拆解清单逐条检查生成内容是否达标，输出完成度报告。
未达标项自动反馈给生成Agent修正，支持循环校验至全部通过。
"""

import json
import logging
from typing import Optional
from openai import OpenAI

log = logging.getLogger(__name__)

SUPERVISOR_SYSTEM = """你是一位严格的小说质量审核员。你的唯一职责是：对照需求清单，逐条检查生成内容是否满足要求。

## 检查原则

1. **只判对错，不解释原因**: 达标就是达标，未达标就是未达标，不需要长篇大论
2. **以用户需求为准**: 用户明确要求的必须做到，未提到的可以灵活处理
3. **P0 必过**: 优先级 P0 的子任务如果未达标，整个方案不合格
4. **具体化反馈**: 不能只说"不够好"，必须指出具体哪里不满足

## 输出格式

```json
{
  "overall_score": 85,
  "overall_status": "passed|partial|failed",
  "passed_count": 5,
  "failed_count": 2,
  "pending_count": 0,
  "results": [
    {
      "subtask_id": "R001",
      "category": "character",
      "passed": true,
      "score": 90,
      "issues": [],
      "suggestion": ""
    }
  ],
  "summary_report": "总体评价一段话"
}
```

评分标准:
- 90-100: 完全达标甚至超出预期
- 70-89: 基本达标，有小瑕疵
- 50-69: 部分达标，有明显不足
- 0-49: 严重不达标，需重做

只输出 JSON。"""


class RequirementSupervisor:
    """需求监督 Agent — 逐条检查 + 循环校验"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model
        self._max_retries = 3  # 单个子任务最大重试次数

    def supervise(self, requirements: dict, plan: dict) -> dict:
        """对照需求清单检查 plan 完成度
        
        Args:
            requirements: decompose() 返回的需求拆解结果
            plan: 当前的小说创作方案
            
        Returns:
            {"overall_score": int, "results": [...], "summary_report": str}
        """
        subtasks = requirements.get("subtasks", [])
        if not subtasks:
            return {"overall_score": 100, "results": [], "summary_report": "无子任务可检查"}

        if not self.client or not self.model:
            return self._offline_supervise(requirements, plan)

        # 截取 plan 关键部分
        plan_summary = {
            "title": plan.get("title", ""),
            "genre": plan.get("genre", ""),
            "style": plan.get("style", ""),
            "worldbuilding": plan.get("worldbuilding", {}),
            "characters": {
                "protagonist": plan.get("characters", {}).get("protagonist", {}),
                "supporting_count": len(plan.get("characters", {}).get("supporting", [])),
                "antagonist_count": len(plan.get("characters", {}).get("antagonist", [])),
            },
            "outline": {
                "total_chapters": plan.get("outline", {}).get("total_chapters", 0),
                "volume_count": len(plan.get("outline", {}).get("volumes", [])),
            }
        }

        user_prompt = f"""请对照以下需求清单，检查创作方案是否达标。

## 需求清单

{json.dumps(subtasks, ensure_ascii=False, indent=2)[:3000]}

## 创作方案

{json.dumps(plan_summary, ensure_ascii=False, indent=2)[:3000]}

请逐条检查并输出 JSON。"""

        log.info(f"RequirementSupervisor: checking {len(subtasks)} subtasks")

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SUPERVISOR_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            result = json.loads(content)

            # 更新子任务状态
            results = result.get("results", [])
            for r in results:
                sid = r.get("subtask_id", "")
                for t in subtasks:
                    if t["id"] == sid:
                        t["status"] = "passed" if r.get("passed") else "failed"
                        t["feedback"] = "; ".join(r.get("issues", [])) or "达标"
                        if not r.get("passed"):
                            t["retry_count"] = t.get("retry_count", 0) + 1

            # 统计
            result["passed_count"] = sum(1 for r in results if r.get("passed"))
            result["failed_count"] = sum(1 for r in results if not r.get("passed"))
            result["pending_count"] = sum(1 for t in subtasks if t.get("status") == "pending")

            all_p0_passed = all(
                r.get("passed") for r in results
                for t in subtasks
                if t["id"] == r.get("subtask_id") and t.get("priority") == "P0"
            )
            result["overall_status"] = "passed" if all(r.get("passed") for r in results) else (
                "failed" if not all_p0_passed else "partial"
            )

            log.info(f"Supervision done: {result['passed_count']}/{len(results)} passed, score={result.get('overall_score', 0)}")
            return result

        except Exception as e:
            log.error(f"RequirementSupervisor failed: {e}")
            return self._offline_supervise(requirements, plan)

    def _offline_supervise(self, requirements: dict, plan: dict) -> dict:
        """离线降级：基于规则检查"""
        subtasks = requirements.get("subtasks", [])
        results = []

        for t in subtasks:
            cat = t.get("category", "")
            passed = True
            issues = []

            if cat == "worldbuilding":
                wb = plan.get("worldbuilding", {})
                if not wb.get("era"): issues.append("缺少时代背景")
                if not wb.get("power_system"): issues.append("缺少力量体系")
                passed = len(issues) == 0

            elif cat == "character":
                chars = plan.get("characters", {})
                protag = chars.get("protagonist", {})
                if not protag.get("name"): issues.append("主角缺少姓名")
                if not protag.get("identity"): issues.append("主角缺少身份")
                if not protag.get("motivation"): issues.append("主角缺少动机")
                passed = len(issues) == 0

            elif cat == "plot":
                outline = plan.get("outline", {})
                if outline.get("total_chapters", 0) < 3:
                    issues.append("章节数过少(<3章)")
                if not outline.get("volumes"):
                    issues.append("缺少卷结构")
                passed = len(issues) == 0

            elif cat == "style":
                style = plan.get("style", "")
                if not style:
                    issues.append("未指定风格")
                passed = len(issues) == 0

            elif cat == "structure":
                outline = plan.get("outline", {})
                vols = outline.get("volumes", [])
                if vols:
                    for v in vols:
                        if len(v.get("chapters", [])) == 0:
                            issues.append(f"第{v.get('number','?')}卷无章节")
                            break
                passed = len(issues) == 0

            t["status"] = "passed" if passed else "failed"
            t["feedback"] = "; ".join(issues) if issues else "达标"

            results.append({
                "subtask_id": t["id"],
                "category": cat,
                "passed": passed,
                "score": 100 if passed else 50,
                "issues": issues,
                "suggestion": "请补充缺失内容" if issues else "",
            })

        passed_count = sum(1 for r in results if r["passed"])
        failed_count = sum(1 for r in results if not r["passed"])
        overall_score = int(passed_count / max(len(results), 1) * 100) if results else 100

        return {
            "overall_score": overall_score,
            "overall_status": "passed" if failed_count == 0 else "failed",
            "passed_count": passed_count,
            "failed_count": failed_count,
            "pending_count": 0,
            "results": results,
            "summary_report": f"通过 {passed_count}/{len(results)} 项" + (". 全部通过!" if failed_count == 0 else f"。{failed_count} 项未达标。"),
            "offline_mode": True,
        }

    def should_retry(self, supervision_result: dict, requirements: dict) -> bool:
        """判断是否需要重新生成"""
        subtasks = requirements.get("subtasks", [])
        for t in subtasks:
            if t.get("status") == "failed" and t.get("retry_count", 0) < self._max_retries:
                return True
        return False

    def get_failed_feedback(self, requirements: dict) -> str:
        """汇总所有失败子任务的反馈，用于重生成 prompt"""
        lines = ["## 以下需求未达标，请重新生成时特别注意:\n"]
        for t in requirements.get("subtasks", []):
            if t.get("status") == "failed":
                lines.append(f"- [{t['id']}] {t.get('title', '')}: {t.get('feedback', '')}")
        return "\n".join(lines)
