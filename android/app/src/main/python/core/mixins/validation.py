"""NovelEngine ValidationMixin — 质量校验 — 逻辑监督 / 大纲一致性 / 章节一致性

由 tools/split_engine.py 从 engine.py 自动拆分。
依赖 NovelEngine 提供的 self.client/self.model/self.memory 等属性。
"""
import os
from typing import AsyncGenerator, Optional, AsyncIterator
from ..atomic_io import safe_read_json




class ValidationMixin:
    def build_logic_fix_prompt(self, result: dict) -> str:
        """根据监督结果生成 Writer 修复提示"""
        return self.logic_supervisor.build_fix_prompt(
            result.get("violations", []),
            result.get("warnings", []),
        )


    def validate_chapter_consistency(
        self, novel_id: str, chapter_num: int, run_deep: bool = True
    ) -> dict:
        """对已生成章节执行逻辑一致性校验"""
        content = self.get_chapter(novel_id, chapter_num)
        if not content:
            return {"error": f"第{chapter_num}章不存在"}

        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}

        # 获取前文
        prev_chapters = {}
        state = self.memory.get_novel_state(novel_id)
        for ch in state.get("completed_chapters", []):
            if ch < chapter_num:
                ch_content = self.get_chapter(novel_id, ch)
                if ch_content:
                    prev_chapters[ch] = ch_content

        # 获取全局状态
        novel_dir = self.memory.get_novel_dir(novel_id)
        state_path = os.path.join(novel_dir, "global_state.json")
        global_state = {}
        if os.path.exists(state_path):
            global_state = safe_read_json(state_path, {})

        # 执行校验 — 使用增强版 LogicSupervisor
        result = self.logic_supervisor.validate_chapter(
            chapter_text=content,
            chapter_num=chapter_num,
            plan=plan,
            prev_chapters=prev_chapters,
            global_state=global_state,
            run_deep=run_deep,
        )
        return result


    def validate_chapter_full(self, novel_id: str, chapter_num: int, run_deep: bool = True) -> dict:
        """全维度逻辑监督（增强版，含 12 大类 + 分类得分 + 修复提示）"""
        return self.validate_chapter_consistency(novel_id, chapter_num, run_deep)


    def validate_outline_consistency(self, novel_id: str) -> dict:
        """校验大纲逻辑一致性"""
        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}
        return self.logic_supervisor.validate_outline(plan)

