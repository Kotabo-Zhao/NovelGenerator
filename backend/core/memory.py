"""NovelGenerator — Memory: 分层上下文管理"""
import json
import os
import sqlite3
from datetime import datetime
from typing import Optional
from .atomic_io import atomic_write_json, safe_read_json, atomic_write_text

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
        plan = safe_read_json(os.path.join(self.get_novel_dir(novel_id), "plan.json"), {})
        
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
        """获取需要回收的伏笔（按时序紧迫度排序）"""
        hooks = safe_read_json(os.path.join(self.get_novel_dir(novel_id), "foreshadowing.json"), [])
        
        # 按紧迫度分组
        active = []
        for h in hooks:
            if h.get("resolved"):
                continue
            reveal = h.get("reveal_chapter", 999)
            if reveal <= current_chapter + 3:  # 3章内需回收
                urgency = "🔴 必须" if reveal <= current_chapter else ("🟡 建议" if reveal <= current_chapter + 1 else "🟢 可选")
                active.append((urgency, reveal, h))
        
        if not active:
            return ""
        
        # 按紧迫度排序：必须 > 建议 > 可选
        urgency_order = {"🔴 必须": 0, "🟡 建议": 1, "🟢 可选": 2}
        active.sort(key=lambda x: (urgency_order.get(x[0], 99), x[1]))
        
        context = "## 📌 伏笔回收提醒\n\n"
        context += "以下伏笔需要在近期回收。优先处理「必须」级别的伏笔：\n\n"
        for urgency, reveal, h in active[:8]:
            context += (f"- {urgency} [第{h.get('plant_chapter', '?')}章埋设 → 计划第{reveal}章回收] {h.get('description', '')}\n")
        
        return context

    # ── 综合构建 ──

    def build_writer_context(self, novel_id: str, chapter_num: int, chapter_outline: dict) -> str:
        """为 Writer 组装完整的写作上下文（强化连续性）
        
        五层上下文:
        - L1: 核心设定（世界观+主角档案，永远注入）
        - L2: 上一章结尾（最后500字，紧接上文）
        - L3: 全局状态快照（角色位置/力量/近3章摘要）
        - L4: 伏笔回收提醒
        - L5: 本章大纲
        """
        parts = []
        novel_dir = self.get_novel_dir(novel_id)
        
        # ── L1: 核心设定 ──
        core = self.get_core_context(novel_id)
        if core:
            parts.append(core)
        
        # ── L2: 上一章结尾（连续性关键）──
        prev_chapter = chapter_num - 1
        if prev_chapter >= 1:
            ch_path = os.path.join(novel_dir, "chapters", f"chapter_{prev_chapter:04d}.md")
            if os.path.exists(ch_path):
                with open(ch_path, "r", encoding="utf-8") as f:
                    prev_content = f.read()
                # 取上一章最后 500 字 — 确保写手知道故事停在哪里
                prev_ending = prev_content[-500:] if len(prev_content) > 500 else prev_content
                parts.append(f"## ⬆️ 上一章结尾（必须从这里接着写）\n\n{prev_ending}")
                
                # 同时提供上一章的钩子信息
                plan = safe_read_json(os.path.join(novel_dir, "plan.json"), {})
                prev_outline = None
                for vol in plan.get("outline", {}).get("volumes", []):
                    for ch in vol.get("chapters", []):
                        if int(ch.get("number", 0)) == prev_chapter:
                            prev_outline = ch
                            break
                if prev_outline:
                    hook = prev_outline.get("hook", "")
                    if hook:
                        parts.append(f"## 🔗 上一章留下的钩子（本章需要回应）\n{hook}")
        
        # ── L3: 全局状态快照 ──
        state_path = os.path.join(novel_dir, "global_state.json")
        if os.path.exists(state_path):
            state = safe_read_json(state_path, {})
            parts.append(self._build_state_snapshot(state, chapter_num))
        
        # ── L4: 伏笔 ──
        hooks = self.get_foreshadowing_context(novel_id, chapter_num)
        if hooks:
            parts.append(hooks)
        
        # ── L5: 本章大纲 ──
        outline_text = self._build_outline_context(chapter_num, chapter_outline)
        parts.append(outline_text)
        
        return "\n\n---\n\n".join(parts)
    
    def _build_state_snapshot(self, state: dict, chapter_num: int) -> str:
        """构建角色状态快照"""
        lines = ["## 📊 全局状态快照"]
        
        # 近期章节摘要
        summaries = state.get("chapters_summary", {})
        recent = sorted([(int(k), v) for k, v in summaries.items() if int(k) >= chapter_num - 5 and int(k) < chapter_num])
        if recent:
            lines.append("\n### 前情提要")
            for ch, summary in recent:
                lines.append(f"- 第{ch}章: {summary}")
        
        # 角色最新状态
        chars = state.get("characters", {})
        if chars:
            lines.append("\n### 角色状态")
            for name, changes in list(chars.items())[:10]:
                latest = changes[-1] if changes else ""
                lines.append(f"- **{name}**: {latest}")
        
        # 力量等级
        powers = state.get("power_levels", {})
        if powers:
            lines.append("\n### 力量等级")
            for name, level in powers.items():
                lines.append(f"- {name}: {level}")
        
        # 当前位置
        locations = state.get("locations", [])
        if locations:
            lines.append(f"\n### 已知地点: {', '.join(locations[-5:])}")
        
        return "\n".join(lines)
    
    def _build_outline_context(self, chapter_num: int, chapter_outline: dict) -> str:
        """构建章节大纲上下文（含 scene_beats）"""
        beats_text = ""
        beats = chapter_outline.get("scene_beats", [])
        if beats:
            beats_text = "\n### 场景节拍\n"
            for b in beats:
                beats_text += f"- 节拍{b.get('beat','?')}「{b.get('name','')}」: {b.get('function','')} → {b.get('key_action','')}\n"
        
        meta = chapter_outline.get("conflict", "") or chapter_outline.get("scene_type", "")
        
        return f"""## 本章大纲

- 章节: 第{chapter_num}章「{chapter_outline.get('title', '')}」
- 核心事件: {chapter_outline.get('summary', '')}
- 情绪曲线: {chapter_outline.get('emotion_curve', '')}
- 出场角色: {', '.join(chapter_outline.get('characters', []))}
- 结尾钩子: {chapter_outline.get('hook', '')}
- 目标字数: {chapter_outline.get('target_words', 3000)} 字
{f'{{meta}}' if meta else ''}{beats_text}"""

    # ── 状态更新 ──

    def save_chapter(self, novel_id: str, chapter_num: int, content: str):
        """保存章节并更新状态"""
        chapters_dir = os.path.join(self.get_novel_dir(novel_id), "chapters")
        os.makedirs(chapters_dir, exist_ok=True)
        
        ch_path = os.path.join(chapters_dir, f"chapter_{chapter_num:04d}.md")
        atomic_write_text(ch_path, content)

    def update_foreshadowing(self, novel_id: str, chapter_num: int, 
                             planted: list = None, resolved: list = None):
        """更新伏笔状态"""
        hooks_path = os.path.join(self.get_novel_dir(novel_id), "foreshadowing.json")
        hooks = safe_read_json(hooks_path, [])
        
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

        atomic_write_json(hooks_path, hooks)

    def save_novel_state(self, novel_id: str, state: dict):
        """保存小说整体状态"""
        state_path = os.path.join(self.get_novel_dir(novel_id), "state.json")
        atomic_write_json(state_path, state)

    def get_novel_state(self, novel_id: str) -> dict:
        """读取小说状态（损坏时自动修复）"""
        state_path = os.path.join(self.get_novel_dir(novel_id), "state.json")
        state = safe_read_json(state_path, {})
        # 向后兼容：旧数据可能没有 completed_chapters，从磁盘扫描
        if "completed_chapters" not in state:
            state["completed_chapters"] = self._scan_chapters(novel_id)
        # Ensure sorted
        if state["completed_chapters"]:
            state["completed_chapters"] = sorted(state["completed_chapters"])
        if "current_chapter" not in state:
            chs = state["completed_chapters"]
            state["current_chapter"] = max(chs) if chs else 0
        return state

    def _scan_chapters(self, novel_id: str) -> list:
        """扫描磁盘实际存在的章节文件，返回章节号列表"""
        chapters_dir = os.path.join(self.get_novel_dir(novel_id), "chapters")
        if not os.path.exists(chapters_dir):
            return []
        chapters = []
        for f in os.listdir(chapters_dir):
            if f.startswith("chapter_") and f.endswith(".md"):
                try:
                    num = int(f.replace("chapter_", "").replace(".md", ""))
                    chapters.append(num)
                except ValueError:
                    pass
        return sorted(chapters)
