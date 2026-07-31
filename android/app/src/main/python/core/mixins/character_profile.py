"""NovelEngine CharacterProfileMixin — 角色人设蒸馏与存取

女娲框架移植：心智模型/决策启发式/表达DNA/反模式/诚实边界。
存储: novels/{novel_id}/character_profiles.json（与 character_bible 分离，兼容旧数据）
"""
import json
import logging
import os
import time

from ..atomic_io import atomic_write_json

log = logging.getLogger(__name__)


class CharacterProfileMixin:
    """角色人设蒸馏（CharacterProfiler）与存取"""

    def distill_character_profile(self, novel_id: str, char_name: str) -> dict:
        """蒸馏指定角色的人设卡（LLM 调用，约 1-2 分钟）并持久化"""
        bible_path = os.path.join(self.memory.get_novel_dir(novel_id), "character_bible.json")
        if not os.path.exists(bible_path):
            return {"error": "人物宝典尚未生成，请先创建小说"}
        with open(bible_path, "r", encoding="utf-8") as f:
            bible = json.load(f)

        # 世界观摘要（帮助 LLM 理解行为背景）
        worldbuilding_summary = ""
        try:
            plan = self.get_novel(novel_id)
            if plan:
                wb = plan.get("worldbuilding") or {}
                worldbuilding_summary = (
                    f"时代: {wb.get('era', '')}\n力量体系: {wb.get('power_system', '')}\n"
                    f"核心冲突: {wb.get('core_conflict', '')}"
                )
        except Exception:
            pass

        result = self.character_profiler.distill(bible, char_name, worldbuilding_summary)
        if "error" in result:
            return result

        # 持久化到 character_profiles.json
        profiles_path = os.path.join(self.memory.get_novel_dir(novel_id), "character_profiles.json")
        profiles = {}
        if os.path.exists(profiles_path):
            try:
                with open(profiles_path, "r", encoding="utf-8") as f:
                    profiles = json.load(f) or {}
            except Exception:
                profiles = {}
        profiles[char_name] = result
        atomic_write_json(profiles_path, profiles)
        log.info(f"Character profile distilled: {novel_id}/{char_name} "
                 f"({len(result.get('mental_models', []))} models, "
                 f"{len(result.get('decision_heuristics', []))} heuristics)")
        return result

    def get_character_profiles(self, novel_id: str) -> dict:
        """列出全部已蒸馏角色人设"""
        profiles_path = os.path.join(self.memory.get_novel_dir(novel_id), "character_profiles.json")
        if not os.path.exists(profiles_path):
            return {}
        try:
            with open(profiles_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def get_character_profile(self, novel_id: str, char_name: str) -> dict:
        """获取单个角色人设"""
        profiles = self.get_character_profiles(novel_id)
        return profiles.get(char_name, {})

    def build_character_rules_context(self, novel_id: str, characters: list) -> str:
        """构建角色人设约束文本（Writer 上下文注入用）

        从 character_profiles.json 读取出场角色的规则，生成精简约束段。
        无蒸馏数据时返回空串（不改变现有行为）。
        """
        profiles = self.get_character_profiles(novel_id)
        if not profiles:
            return ""
        parts = []
        for name in characters or []:
            prof = profiles.get(name)
            if not prof:
                continue
            heuristics = prof.get("decision_heuristics", [])[:4]
            dna = prof.get("expression_dna", [])[:4]
            anti = prof.get("anti_patterns", [])[:3]
            boundary = prof.get("boundary", {}) or {}
            rules = (boundary.get("rules") or boundary.get("anti_collapse_checks") or [])[:3]
            lines = []
            if heuristics:
                lines.append("**决策启发式**（出场必须遵守）：")
                for h in heuristics:
                    if isinstance(h, dict):
                        lines.append(f"- {('' if str(h.get('trigger', '')).startswith('当') else '当')}{h.get('trigger', '')} → {h.get('action', '')}"[:120])
                    else:
                        lines.append(f"- {h}"[:120])
            if dna:
                lines.append("**表达DNA**：")
                for d in dna:
                    if isinstance(d, dict):
                        lines.append(f"- {d.get('name', '')}：{d.get('example', '')}"[:120])
                    else:
                        lines.append(f"- {d}"[:120])
            if anti:
                lines.append("**反模式**（绝对禁止）：")
                for a in anti:
                    lines.append(f"- {a.get('pattern', a) if isinstance(a, dict) else a}"[:120])
            if rules:
                lines.append("**防崩校验**：")
                lines += [f"- {r}" for r in rules]
            if lines:
                parts.append(f"## 🎭 角色人设约束（{name}）\n" + "\n".join(lines))
        return "\n\n".join(parts)
