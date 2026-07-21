"""NovelGenerator — Memory: 分层上下文管理"""
import json
import os
import sqlite3
from datetime import datetime
from typing import Optional

class NovelMemory:
    """分层记忆管理器
    
    三层架构:
    - L1 核心设定: 世界观规则 + 主角档案 (永远注入, ~500 tokens)
    - L2 近期上下文: 最近3章摘要 + 角色状态快照 (~1500 tokens)
    - L3 伏笔追踪: 所有未闭合伏笔 + 相关度检索 (~1000 tokens)
    """

    def __init__(self, novels_dir: str = None):
        self.novels_dir = novels_dir or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "novels"
        )

    def get_novel_dir(self, novel_id: str) -> str:
        return os.path.join(self.novels_dir, novel_id)

    # ── L1: 核心设定 ──
    
    def get_core_context(self, novel_id: str) -> str:
        """获取永不遗忘的核心设定"""
        plan_path = os.path.join(self.get_novel_dir(novel_id), "plan.json")
        if not os.path.exists(plan_path):
            return ""
        
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        
        wb = plan.get("worldbuilding", {})
        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})
        
        context = f"""## 核心设定（永远记住）

### 世界观
- 时代: {wb.get('era', '')}
- 力量体系: {wb.get('power_system', '')}
- 核心冲突: {wb.get('core_conflict', '')}

### 主角档案
- 姓名: {protagonist.get('name', '')}
- 身份: {protagonist.get('identity', '')}
- 性格: {protagonist.get('personality', '')}
- 金手指: {protagonist.get('cheat', '')}
- 核心动机: {protagonist.get('motivation', '')}
"""
        # 配角摘要
        supporting = chars.get("supporting", [])
        if supporting:
            context += "\n### 重要配角\n"
            for c in supporting[:5]:
                context += f"- {c.get('name', '?')}: {c.get('identity', '')}, {c.get('relation', '')}\n"
        
        return context

    # ── L2: 近期上下文 ──

    def get_recent_context(self, novel_id: str, current_chapter: int) -> str:
        """获取最近 3 章的上下文"""
        chapters_dir = os.path.join(self.get_novel_dir(novel_id), "chapters")
        if not os.path.exists(chapters_dir):
            return ""
        
        context_parts = []
        for ch_num in range(max(1, current_chapter - 3), current_chapter):
            ch_path = os.path.join(chapters_dir, f"chapter_{ch_num:04d}.md")
            if os.path.exists(ch_path):
                with open(ch_path, "r", encoding="utf-8") as f:
                    content = f.read()
                # 只取前 1000 字作为摘要
                summary = content[:1000] + ("..." if len(content) > 1000 else "")
                context_parts.append(f"### 第{ch_num}章摘要\n{summary}")
        
        return "\n\n".join(context_parts)

    # ── L3: 伏笔追踪 ──

    def get_foreshadowing_context(self, novel_id: str, current_chapter: int) -> str:
        """获取需要回收的伏笔"""
        hooks_path = os.path.join(self.get_novel_dir(novel_id), "foreshadowing.json")
        if not os.path.exists(hooks_path):
            return ""
        
        with open(hooks_path, "r", encoding="utf-8") as f:
            hooks = json.load(f)
        
        # 找到应该在当前章节附近回收的伏笔
        active = [h for h in hooks if not h.get("resolved") and h.get("reveal_chapter", 999) <= current_chapter + 2]
        
        if not active:
            return ""
        
        context = "## 待回收伏笔\n"
        for h in active[:10]:
            context += (f"- [第{h.get('plant_chapter', '?')}章埋设] {h.get('description', '')} "
                       f"(计划第{h.get('reveal_chapter', '?')}章回收)\n")
        return context

    # ── 综合构建 ──

    def build_writer_context(self, novel_id: str, chapter_num: int, chapter_outline: dict) -> str:
        """为 Writer 组装完整的写作上下文"""
        parts = []
        
        # L1: 核心设定
        core = self.get_core_context(novel_id)
        if core:
            parts.append(core)
        
        # L2: 近期上下文
        recent = self.get_recent_context(novel_id, chapter_num)
        if recent:
            parts.append(f"\n## 前情回顾\n{recent}")
        
        # L3: 伏笔
        hooks = self.get_foreshadowing_context(novel_id, chapter_num)
        if hooks:
            parts.append(f"\n{hooks}")
        
        # 本章大纲
        outline_text = f"""## 本章大纲

- 章节: 第{chapter_num}章「{chapter_outline.get('title', '')}」
- 核心事件: {chapter_outline.get('summary', '')}
- 情绪曲线: {chapter_outline.get('emotion_curve', '')}
- 出场角色: {', '.join(chapter_outline.get('characters', []))}
- 结尾钩子: {chapter_outline.get('hook', '')}
- 目标字数: {chapter_outline.get('target_words', 3000)} 字"""
        parts.append(outline_text)
        
        return "\n\n---\n\n".join(parts)

    # ── 状态更新 ──

    def save_chapter(self, novel_id: str, chapter_num: int, content: str):
        """保存章节并更新状态"""
        chapters_dir = os.path.join(self.get_novel_dir(novel_id), "chapters")
        os.makedirs(chapters_dir, exist_ok=True)
        
        ch_path = os.path.join(chapters_dir, f"chapter_{chapter_num:04d}.md")
        with open(ch_path, "w", encoding="utf-8") as f:
            f.write(content)

    def update_foreshadowing(self, novel_id: str, chapter_num: int, 
                             planted: list = None, resolved: list = None):
        """更新伏笔状态"""
        hooks_path = os.path.join(self.get_novel_dir(novel_id), "foreshadowing.json")
        hooks = []
        if os.path.exists(hooks_path):
            with open(hooks_path, "r", encoding="utf-8") as f:
                hooks = json.load(f)
        
        # 添加新伏笔
        for p in (planted or []):
            hooks.append({
                "plant_chapter": chapter_num,
                "description": p.get("description", ""),
                "reveal_chapter": p.get("reveal_chapter", chapter_num + 5),
                "resolved": False,
            })
        
        # 标记已回收
        for r in (resolved or []):
            for h in hooks:
                if r in h.get("description", ""):
                    h["resolved"] = True
                    h["resolved_chapter"] = chapter_num
        
        with open(hooks_path, "w", encoding="utf-8") as f:
            json.dump(hooks, f, ensure_ascii=False, indent=2)

    def save_novel_state(self, novel_id: str, state: dict):
        """保存小说整体状态"""
        state_path = os.path.join(self.get_novel_dir(novel_id), "state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def get_novel_state(self, novel_id: str) -> dict:
        """读取小说状态"""
        state_path = os.path.join(self.get_novel_dir(novel_id), "state.json")
        if os.path.exists(state_path):
            with open(state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"current_chapter": 0, "total_words": 0}
