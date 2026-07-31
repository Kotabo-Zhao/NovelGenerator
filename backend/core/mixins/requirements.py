"""NovelEngine RequirementsMixin — 需求拆解与监督 — 拆解 / 更新 / 监督 / 闭环修复

由 tools/split_engine.py 从 engine.py 自动拆分。
依赖 NovelEngine 提供的 self.client/self.model/self.memory 等属性。
"""
import os
from typing import AsyncGenerator, Optional, AsyncIterator




class RequirementsMixin:
    def decompose_requirements(self, novel_id: str, inspiration: str) -> dict:
        """拆解用户灵感为可执行子任务
        
        Args:
            novel_id: 小说ID（已保存在plan中的）
            inspiration: 用户输入的核心灵感
        Returns:
            requirements dict with subtasks
        """
        plan = self.get_novel(novel_id)
        existing = self._req_store.get(novel_id)

        result = self.requirement_decomposer.decompose(
            inspiration, plan=plan, existing_requirements=existing
        )
        self._req_store.set(novel_id, result)
        return result


    def supervise_requirements(self, novel_id: str) -> dict:
        """监督当前 plan 是否满足需求
        
        Returns:
            supervision report with overall_score, results, etc.
        """
        requirements = self._req_store.get(novel_id)
        if not requirements:
            return {"error": "尚未拆解需求，请先提交灵感"}

        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}

        return self.requirement_supervisor.supervise(requirements, plan)


    def update_requirements(self, novel_id: str, new_inspiration: str) -> dict:
        """追加/修改需求"""
        existing = self._req_store.get(novel_id)
        if existing:
            result = self.requirement_decomposer.update_requirements(
                existing, new_inspiration
            )
        else:
            result = self.decompose_requirements(novel_id, new_inspiration)
        self._req_store.set(novel_id, result)
        return result


    async def verify_and_fix_loop(self, novel_id: str, max_iterations: int = 3
                                   ) -> dict:
        """循环校验：监督→修正→再监督，直至全部通过或达到最大迭代
        
        Args:
            novel_id: 小说ID
            max_iterations: 最大重试次数
            
        Yields progress events + final report.
        """
        requirements = self._req_store.get(novel_id)
        if not requirements:
            yield {"type": "error", "message": "尚未拆解需求"}
            return

        for iteration in range(1, max_iterations + 1):
            yield {"type": "progress", "phase": "verify",
                   "pct": int(100 * iteration / max_iterations),
                   "label": f"第 {iteration}/{max_iterations} 轮校验…"}

            # 监督
            report = self.supervise_requirements(novel_id)
            yield {"type": "supervision", "report": report}

            if report.get("overall_status") == "passed":
                yield {"type": "progress", "phase": "done", "pct": 100,
                       "label": "全部通过!"}
                yield {"type": "done", "iterations": iteration, "result": "passed"}
                return

            # 收集失败反馈
            failed_feedback = self.requirement_supervisor.get_failed_feedback(
                requirements
            )
            if not failed_feedback:
                yield {"type": "done", "iterations": iteration, "result": "no_feedback"}
                return

            yield {"type": "progress", "phase": "fix",
                   "pct": int(100 * iteration / max_iterations),
                   "label": f"修正 {report.get('failed_count', 0)} 项…"}

            # 使用 outline_interactive 机制重新生成
            plan = self.get_novel(novel_id)
            async for event in self.outline_interactive.process_feedback(
                failed_feedback, plan, self.planner
            ):
                if event["type"] == "done":
                    new_plan = event["plan"]
                    new_plan["outline"] = self.planner.repair_outline(
                        new_plan.get("outline", {})
                    )
                    novel_dir = self.memory.get_novel_dir(novel_id)
                    atomic_write_json(os.path.join(novel_dir, "plan.json"), new_plan)
                    self.save_character_bible(novel_id, new_plan, novel_dir)

        yield {"type": "done", "iterations": max_iterations,
               "result": "max_iterations_reached",
               "message": f"已达最大迭代次数 {max_iterations}，仍有未达标项"}

