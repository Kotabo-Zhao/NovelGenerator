"""NovelEngine AnalysisMixin — 创作分析 — 开局评估 / 开局备选 / 反转设计

由 tools/split_engine.py 从 engine.py 自动拆分。
依赖 NovelEngine 提供的 self.client/self.model/self.memory 等属性。
"""
from typing import AsyncGenerator, Optional, AsyncIterator




class AnalysisMixin:
    def analyze_opening(self, novel_id: str, chapter_num: int = 1) -> dict:
        """分析章节开头吸引力"""
        content = self.get_chapter(novel_id, chapter_num)
        if not content:
            return {"error": f"第{chapter_num}章不存在"}

        plan = self.get_novel(novel_id)
        style = plan.get("style", "热血爽文") if plan else "热血爽文"

        return self.opening_optimizer.analyze_opening(
            chapter_text=content,
            chapter_num=chapter_num,
            style=style,
            is_first_chapter=(chapter_num == 1),
        )


    def design_chapter_twist(self, novel_id: str, chapter_num: int) -> dict:
        """为单章设计反转钩子"""
        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}

        chapter_outline = self._find_chapter_outline(plan, chapter_num)
        if not chapter_outline:
            return {"error": f"第{chapter_num}章大纲不存在"}

        # 获取前情摘要
        prev_summary = ""
        state = self.memory.get_novel_state(novel_id)
        for ch in sorted(state.get("completed_chapters", []))[-3:]:
            prev_summary += f"第{ch}章已完成\n"

        return self.twist_designer.design_chapter_twist(
            chapter_num=chapter_num,
            plan=plan,
            chapter_outline=chapter_outline,
            prev_chapters_summary=prev_summary,
        )


    def design_twists(self, novel_id: str) -> dict:
        """为整部小说规划反转点"""
        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}
        return self.twist_designer.design_twists(plan)


    async def generate_opening_alternatives(
        self, novel_id: str, chapter_num: int = 1, count: int = 3
    ) -> list:
        """生成替代开头方案"""
        content = self.get_chapter(novel_id, chapter_num)
        if not content:
            return [{"error": f"第{chapter_num}章不存在"}]

        plan = self.get_novel(novel_id)
        style = plan.get("style", "热血爽文") if plan else "热血爽文"

        return self.opening_optimizer.generate_alternatives(
            chapter_text=content,
            chapter_num=chapter_num,
            plan=plan or {},
            style=style,
            count=count,
        )

