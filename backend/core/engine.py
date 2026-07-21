"""NovelGenerator — Engine: 创作管线编排器"""
import json
import os
import sys
import logging
from typing import AsyncGenerator, Optional
from openai import OpenAI

# Allow importing from parent dir (works both as package and standalone)
try:
    from backend import config
except ImportError:
    import config

from .planner import Planner
from .writer import Writer
from .memory import NovelMemory

log = logging.getLogger(__name__)


class NovelEngine:
    """小说创作引擎 — 整合规划、写作、记忆管理"""

    def __init__(self):
        self.client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
        )
        self.model = config.DEEPSEEK_MODEL
        self.planner = Planner(self.client, self.model)
        self.writer = Writer(self.client, self.model)
        self.memory = NovelMemory(config.NOVELS_DIR)
        # 确保小说存储目录存在（Render 冷启动时不会丢）
        os.makedirs(config.NOVELS_DIR, exist_ok=True)

    # ── Phase 1: 规划 ──

    def create_novel(self, creative_input: dict) -> dict:
        """创建新小说：灵感 → 世界观 + 角色 + 大纲
        
        Args:
            creative_input: {genre, style, inspiration, target_words, title?}
        Returns:
            plan dict (结构化设定文档)
        """
        plan = self.planner.plan(creative_input)
        if not plan:
            raise RuntimeError("规划生成失败，请重试")
        
        # 保存规划
        novel_dir = self.memory.get_novel_dir(plan["title"])
        os.makedirs(novel_dir, exist_ok=True)
        
        with open(os.path.join(novel_dir, "plan.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        
        # 初始化状态
        total_chapters = plan.get("outline", {}).get("total_chapters", 0)
        self.memory.save_novel_state(plan["title"], {
            "current_chapter": 0,
            "total_chapters": total_chapters,
            "total_words": 0,
            "status": "planning_done",
            "created_at": plan.get("_meta", {}).get("created_at", ""),
        })
        
        # 初始化伏笔文件
        hooks_path = os.path.join(novel_dir, "foreshadowing.json")
        with open(hooks_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        
        log.info(f"Novel created: {plan['title']} ({total_chapters} chapters)")
        return plan

    def get_novel(self, novel_id: str) -> Optional[dict]:
        """获取已有小说的规划数据"""
        plan_path = os.path.join(self.memory.get_novel_dir(novel_id), "plan.json")
        if not os.path.exists(plan_path):
            return None
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        plan["state"] = self.memory.get_novel_state(novel_id)
        return plan

    def update_plan(self, novel_id: str, plan_data: dict) -> bool:
        """保存用户修改后的大纲
        
        Args:
            novel_id: 小说ID（目录名）
            plan_data: 修改后的完整 plan 字典
        Returns:
            True 表示保存成功
        """
        plan_path = os.path.join(self.memory.get_novel_dir(novel_id), "plan.json")
        if not os.path.exists(plan_path):
            return False
        
        # 保留 _meta 原始信息
        existing = self.get_novel(novel_id)
        if existing and "_meta" in existing:
            plan_data["_meta"] = existing["_meta"]
        
        # 标准化章节号
        for vol in plan_data.get("outline", {}).get("volumes", []):
            vol["number"] = int(vol.get("number", 1))
            for ch in vol.get("chapters", []):
                ch["number"] = int(ch.get("number", 1))
                ch["target_words"] = int(ch.get("target_words", 3000))
        plan_data["outline"]["total_chapters"] = int(plan_data.get("outline", {}).get("total_chapters", 0))
        
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan_data, f, ensure_ascii=False, indent=2)
        
        # 更新 state 中的 total_chapters
        state = self.memory.get_novel_state(novel_id)
        state["total_chapters"] = plan_data.get("outline", {}).get("total_chapters", state.get("total_chapters", 0))
        self.memory.save_novel_state(novel_id, state)
        
        log.info(f"Plan updated: {novel_id}")
        return True

    def list_novels(self) -> list:
        """列出所有小说"""
        novels = []
        if not os.path.exists(config.NOVELS_DIR):
            return novels
        for name in os.listdir(config.NOVELS_DIR):
            plan_path = os.path.join(config.NOVELS_DIR, name, "plan.json")
            if os.path.exists(plan_path):
                with open(plan_path, "r", encoding="utf-8") as f:
                    plan = json.load(f)
                state = self.memory.get_novel_state(name)
                novels.append({
                    "id": name,
                    "title": plan.get("title", name),
                    "genre": plan.get("genre", ""),
                    "style": plan.get("style", ""),
                    "target_words": plan.get("target_words", 0),
                    "state": state,
                })
        return sorted(novels, key=lambda n: n["state"].get("created_at", ""), reverse=True)

    # ── Phase 2: 写作 ──

    async def generate_chapter_stream(
        self, novel_id: str, chapter_num: int
    ) -> AsyncGenerator[dict, None]:
        """流式生成章节 — 前端可实时显示打字效果
        
        Yields:
            {"type": "text", "content": "..."} or {"type": "done", "content": "全文"}
            or {"type": "error", "message": "..."}
        """
        try:
            plan = self.get_novel(novel_id)
            if not plan:
                yield {"type": "error", "message": f"小说 '{novel_id}' 不存在"}
                return

            # 找到本章大纲
            chapter_outline = self._find_chapter_outline(plan, chapter_num)
            if not chapter_outline:
                # 兜底：构造一个基础大纲（防止 DeepSeek JSON 结构异常导致全流程挂掉）
                log.warning(f"Chapter {chapter_num} outline not found in plan, using fallback")
                chapter_outline = {
                    "number": chapter_num,
                    "title": f"第{chapter_num}章",
                    "summary": f"继续推进主线剧情发展",
                    "emotion_curve": "平稳→紧张→悬念",
                    "characters": ["主角"],
                    "hook": "留下悬念引导下一章",
                    "target_words": config.DEFAULT_CHAPTER_WORDS,
                }

            # 组装上下文
            context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)

            # 获取创作参数
            genre = plan.get("genre", "玄幻")
            style = plan.get("style", "热血爽文")
            target_words = chapter_outline.get("target_words", config.DEFAULT_CHAPTER_WORDS)

            # 流式生成
            full_text = ""
            async for text in self.writer.write_stream(
                context=context,
                genre=genre,
                style=style,
                target_words=target_words,
            ):
                full_text += text
                yield {"type": "text", "content": text}

            # 保存章节
            chapter_title = chapter_outline.get("title", f"第{chapter_num}章")
            formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{full_text}"
            self.memory.save_chapter(novel_id, chapter_num, formatted)

            # 更新状态
            state = self.memory.get_novel_state(novel_id)
            state["current_chapter"] = chapter_num
            state["total_words"] = state.get("total_words", 0) + len(full_text)
            self.memory.save_novel_state(novel_id, state)

            log.info(f"Chapter {chapter_num} saved: {len(full_text)} chars")
            yield {"type": "done", "content": formatted, "chapter_num": chapter_num}

        except Exception as e:
            log.exception(f"Chapter generation failed: {e}")
            yield {"type": "error", "message": str(e)}

    def _find_chapter_outline(self, plan: dict, chapter_num: int) -> Optional[dict]:
        """在大纲中查找指定章节（兼容字符串/整数章节号）"""
        volumes = plan.get("outline", {}).get("volumes", [])
        for vol in volumes:
            for ch in vol.get("chapters", []):
                if int(ch.get("number", 0)) == chapter_num:
                    return ch
        return None

    # ── Phase 3: 导出 ──

    def export_novel(self, novel_id: str, fmt: str = "txt") -> tuple:
        """导出小说全文
        
        Returns:
            (content: str|None, error: str|None)
        """
        plan = self.get_novel(novel_id)
        if not plan:
            return None, f"小说 '{novel_id}' 不存在"

        chapters_dir = os.path.join(self.memory.get_novel_dir(novel_id), "chapters")
        if not os.path.exists(chapters_dir):
            return None, "尚未生成任何章节，请先在写作页面生成至少一章"

        title = plan.get("title", novel_id)
        chapters = sorted(
            [f for f in os.listdir(chapters_dir) if f.endswith(".md")],
            key=lambda x: int(x.split("_")[1].split(".")[0]) if "_" in x else 0
        )

        if not chapters:
            return None, "暂无章节内容，请先生成章节"

        if fmt == "txt":
            lines = [f"{title}\n{'=' * 40}\n"]
            for ch_file in chapters:
                with open(os.path.join(chapters_dir, ch_file), "r", encoding="utf-8") as f:
                    lines.append(f.read())
                    lines.append("\n\n" + "—" * 40 + "\n\n")
            return "\n".join(lines), None

        return None, f"暂不支持 {fmt} 格式"
