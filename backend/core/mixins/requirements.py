"""NovelEngine RequirementsMixin — 需求拆解与监督 — 拆解 / 更新 / 监督 / 闭环修复

由 tools/split_engine.py 从 engine.py 自动拆分。
依赖 NovelEngine 提供的 self.client/self.model/self.memory 等属性。
"""
import os
from typing import AsyncGenerator, Optional, AsyncIterator
from ..atomic_io import atomic_write_json




class RequirementsMixin:
    def _load_requirements_contract(self, novel_id: str, plan: dict | None = None):
        from ..requirements_contract import normalize_requirements

        plan = plan if isinstance(plan, dict) else {}
        meta = (plan.get("_meta") or {}).get("requirements") or {}
        for key in (meta.get("store_key"), novel_id, plan.get("title")):
            if key:
                value = self._req_store.get(str(key))
                if value:
                    return normalize_requirements(value, novel_id=str(novel_id)), str(key)
        return {}, str(novel_id)

    def _persist_requirements_contract(self, novel_id: str, requirements: dict, plan: dict | None = None):
        from ..requirements_contract import normalize_requirements

        normalized = normalize_requirements(requirements, novel_id=str(novel_id))
        self._req_store.set(str(novel_id), normalized)
        if isinstance(plan, dict) and plan.get("title") and plan["title"] != str(novel_id):
            self._req_store.set(str(plan["title"]), normalized)
        return normalized

    def _sync_requirements_contract(self, novel_id: str, plan: dict, requirements: dict | None = None):
        from ..requirements_contract import assign_requirements_to_outline
        from ..chapter_contract import migrate_plan_chapter_contracts

        if requirements is None:
            requirements, _ = self._load_requirements_contract(novel_id, plan)
        if not requirements:
            return {}
        normalized = assign_requirements_to_outline(requirements, plan)
        migrate_plan_chapter_contracts(plan)
        normalized = self._persist_requirements_contract(novel_id, normalized, plan)
        meta = plan.setdefault("_meta", {}).setdefault("requirements", {})
        meta.update({
            "store_key": str(novel_id),
            "summary": normalized.get("summary", ""),
            "core_theme": normalized.get("core_theme", ""),
        })
        return normalized

    def decompose_requirements(self, novel_id: str, inspiration: str) -> dict:
        plan = self.get_novel(novel_id) or {}
        existing, _ = self._load_requirements_contract(novel_id, plan)
        result = self.requirement_decomposer.decompose(inspiration, plan=plan, existing_requirements=existing)
        normalized = self._sync_requirements_contract(novel_id, plan, result)
        if plan and getattr(self, "memory", None):
            novel_dir = self.memory.get_novel_dir(novel_id)
            atomic_write_json(os.path.join(novel_dir, "plan.json"), plan)
            try:
                self.memory._invalidate(novel_id, "plan")
            except Exception:
                pass
        return normalized

    def supervise_requirements(self, novel_id: str) -> dict:
        plan = self.get_novel(novel_id)
        requirements, _ = self._load_requirements_contract(novel_id, plan)
        if not requirements:
            return {"error": "requirements_not_decomposed"}
        if not plan:
            return {"error": f"novel_not_found:{novel_id}"}
        report = self.requirement_supervisor.supervise(requirements, plan)
        # The supervisor mutates subtasks; persist the mutation before any feedback is read.
        self._persist_requirements_contract(novel_id, requirements, plan)
        report = dict(report or {})
        report["requirements_version"] = requirements.get("version", 1)
        report["requirements_key"] = str(novel_id)
        return report

    def update_requirements(self, novel_id: str, new_inspiration: str) -> dict:
        plan = self.get_novel(novel_id) or {}
        existing, _ = self._load_requirements_contract(novel_id, plan)
        if existing:
            result = self.requirement_decomposer.update_requirements(existing, new_inspiration)
        else:
            result = self.requirement_decomposer.decompose(new_inspiration, plan=plan, existing_requirements={})
        normalized = self._sync_requirements_contract(novel_id, plan, result)
        if plan and getattr(self, "memory", None):
            atomic_write_json(os.path.join(self.memory.get_novel_dir(novel_id), "plan.json"), plan)
        return normalized

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
            requirements, _ = self._load_requirements_contract(novel_id, self.get_novel(novel_id))
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

