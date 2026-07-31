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

        # ── v2.3.6: 蒸馏成果回写 bible（人物宝典变厚，所有读 bible 的模块受益）──
        try:
            self._merge_profile_into_bible(novel_id, char_name, result)
        except Exception as me:
            log.warning(f"Profile merge into bible failed: {me}")
        return result

    def _merge_profile_into_bible(self, novel_id: str, char_name: str, profile: dict):
        """把蒸馏人设卡回写进 character_bible.json 的角色条目"""
        bible_path = os.path.join(self.memory.get_novel_dir(novel_id), "character_bible.json")
        if not os.path.exists(bible_path):
            return
        with open(bible_path, "r", encoding="utf-8") as f:
            bible = json.load(f)

        summary = {
            "mental_models": profile.get("mental_models", []),
            "decision_heuristics": profile.get("decision_heuristics", []),
            "expression_dna": profile.get("expression_dna", []),
            "anti_patterns": profile.get("anti_patterns", []),
            "boundary": profile.get("boundary", {}),
        }
        updated = False
        for group in ("protagonist", "supporting", "antagonist"):
            items = bible.get(group)
            if isinstance(items, dict):
                if items.get("name") == char_name:
                    items["character_profile"] = summary
                    updated = True
            elif isinstance(items, list):
                for c in items:
                    if isinstance(c, dict) and c.get("name") == char_name:
                        c["character_profile"] = summary
                        updated = True
        if updated:
            atomic_write_json(bible_path, bible)
            log.info(f"Bible enhanced: {char_name} (profile merged)")

    def _merge_voices_into_bible(self, novel_id: str, voices: dict):
        """把角色声音卡回写进 character_bible.json 的角色条目"""
        if not voices:
            return
        bible_path = os.path.join(self.memory.get_novel_dir(novel_id), "character_bible.json")
        if not os.path.exists(bible_path):
            return
        with open(bible_path, "r", encoding="utf-8") as f:
            bible = json.load(f)
        updated = False
        for group in ("protagonist", "supporting", "antagonist"):
            items = bible.get(group)
            if isinstance(items, dict):
                if items.get("name") in voices:
                    items["voice"] = voices[items["name"]]
                    updated = True
            elif isinstance(items, list):
                for c in items:
                    if isinstance(c, dict) and c.get("name") in voices:
                        c["voice"] = voices[c["name"]]
                        updated = True
        if updated:
            atomic_write_json(bible_path, bible)
            log.info(f"Bible enhanced: voices merged ({len(voices)} chars)")

    def generate_all_character_assets(self, novel_id: str) -> dict:
        """一键生成已有书的全部角色资产（蒸馏+声音卡）并回写 bible

        用于旧书补全：profile 未蒸馏的角色逐个蒸馏，然后生成声音卡。
        阻塞式（约 1-3 分钟），返回汇总。
        """
        import asyncio
        bible_path = os.path.join(self.memory.get_novel_dir(novel_id), "character_bible.json")
        if not os.path.exists(bible_path):
            return {"error": "人物宝典不存在"}
        with open(bible_path, "r", encoding="utf-8") as f:
            bible = json.load(f)

        # 全部角色名（主角优先）
        char_names = []
        proto = (bible.get("protagonist") or {}).get("name", "")
        if proto:
            char_names.append(proto)
        for group in ("supporting", "antagonist"):
            for c in bible.get(group, []) or []:
                if isinstance(c, dict) and c.get("name") and c["name"] not in char_names:
                    char_names.append(c["name"])

        results = {"distilled": [], "voices": {}}
        existing = self.get_character_profiles(novel_id)
        for name in char_names[:8]:
            if name not in existing:
                r = self.distill_character_profile(novel_id, name)
                if "error" not in r:
                    results["distilled"].append(name)
                # 失败不阻塞

        # 声音卡
        voices = self.character_voices.generate_all(bible)
        if voices:
            voices_path = os.path.join(self.memory.get_novel_dir(novel_id), "character_voices.json")
            atomic_write_json(voices_path, voices)
            self._merge_voices_into_bible(novel_id, voices)
            results["voices"] = {k: True for k in voices.keys()}
        return results

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

    def get_character_voices(self, novel_id: str) -> dict:
        """获取角色声音卡（v2.3.6，无则返回空）"""
        voices_path = os.path.join(self.memory.get_novel_dir(novel_id), "character_voices.json")
        if not os.path.exists(voices_path):
            return {}
        try:
            with open(voices_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

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


class FeedbackMixin:
    """章节质量反馈闭环（v2.3.5）"""

    def submit_chapter_feedback(self, novel_id: str, chapter_num: int,
                                rating: int, reason: str = "") -> dict:
        """提交章节反馈（rating: 1 赞 / -1 踩 / 0 中性）"""
        ok = self.feedback_store.submit(novel_id, chapter_num, rating, reason)
        if not ok:
            return {"error": "反馈保存失败"}
        return {"ok": True, "saved": True}

    def get_feedback_summary(self, novel_id: str) -> dict:
        """获取反馈汇总（列表 + 统计）"""
        items = self.feedback_store.list(novel_id)
        stats = {
            "total": len(items),
            "likes": sum(1 for i in items if i["rating"] == 1),
            "dislikes": sum(1 for i in items if i["rating"] == -1),
            "neutral": sum(1 for i in items if i["rating"] == 0),
        }
        return {"items": items[:50], "stats": stats}

    def build_preference_instruction(self, novel_id: str) -> str:
        """聚合反馈 → Writer 偏好指令（空串 = 无需注入）"""
        return self.feedback_store.build_preference_instruction(novel_id)
