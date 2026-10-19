"""Convert a novel's character bible (plan.json) into SillyTavern-style cards.

Two modes are supported:

* ``build_cards_from_plan`` — deterministic, template based (fast, no LLM).
* ``generate_cards_with_ai`` — sends the novel's worldbuilding, character
  bible, distilled character profile (女娲人设卡) and voice card to the active
  LLM, asking it to write complete SillyTavern V2 cards (description,
  personality, scenario, first_mes, mes_example, greetings, tags).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ..resilient_client import ResilientLLMClient
from .card_codec import normalize_card

log = logging.getLogger(__name__)

_AI_CHUNK_SIZE = 2  # smaller batches keep card JSON within typical output limits
_AI_MAX_TOKENS = 4000

AI_CARD_SYSTEM = """你是 SillyTavern 角色卡设计师。请根据给定的中文小说设定，为指定角色生成可直接导入 SillyTavern 的 V2 角色卡。

硬性要求：
1. 必须严格基于给定的小说设定、人物宝典、人设蒸馏卡与声音卡；可在不冲突的前提下合理深化，禁止凭空新增重要设定。
2. 只输出 JSON，禁止输出解释、前后缀或 Markdown 代码块。格式：
   {"cards": [{"name": "...", "description": "...", "personality": "...", "scenario": "...", "first_mes": "...", "mes_example": "...", "system_prompt": "...", "post_history_instructions": "...", "alternate_greetings": ["..."], "tags": ["..."]}]}
3. 每个角色一张卡，name 必须与给定角色名完全一致；不要遗漏、不要合并角色。
4. 字段要求（中文）：
   - description：300-600 字。外貌、身份、背景、与 {{user}} 的关系、当前处境；第三人称。
   - personality：150-300 字。性格底色、缺陷、欲望、说话方式；要具体可演。
   - scenario：100-200 字。一个适合开始角色扮演的具体场景，包含时间地点与 {{user}} 的处境。
   - first_mes：100-250 字。角色在这个场景里的第一反应；用 *斜体* 写动作、用「」写台词；不要把 {{user}} 的台词或决定写出来；结尾自然留出互动钩子。
   - mes_example：2-3 段以 <START> 开头的示例对话，使用 {{user}}: / {{char}}: 的行格式，体现角色语气与口癖。
   - system_prompt：80-150 字。以「你是《小说名》中的角色名」开头，写明角色扮演边界与说话要求。
   - post_history_instructions：50-120 字。防止 OOC、提醒角色当前目标或秘密的约束。
   - alternate_greetings：1-2 条不同氛围的备选开场白。
   - tags：3-5 个短标签（身份/阵营/性格/题材）。
5. 不剧透尚未写到的后期情节；不替 {{user}} 做决定；保持角色视角。"""


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def plan_character_targets(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List plan characters as {name, role, role_label, raw}."""
    if not isinstance(plan, dict):
        return []
    characters = plan.get("characters") or {}
    if not isinstance(characters, dict):
        return []
    targets: List[Dict[str, Any]] = []
    protagonist = characters.get("protagonist")
    if isinstance(protagonist, dict) and str(protagonist.get("name") or "").strip():
        targets.append({
            "name": str(protagonist["name"]).strip(),
            "role": "protagonist",
            "role_label": "主角",
            "raw": protagonist,
        })
    for role_key, role_label in (("supporting", "配角"), ("antagonist", "反派")):
        group = characters.get(role_key)
        if not isinstance(group, list):
            continue
        for item in group:
            if isinstance(item, dict) and str(item.get("name") or "").strip():
                targets.append({
                    "name": str(item["name"]).strip(),
                    "role": role_key,
                    "role_label": role_label,
                    "raw": item,
                })
    return targets


def plan_character_names(plan: Dict[str, Any]) -> List[str]:
    return [target["name"] for target in plan_character_targets(plan)]


def _bible_entry(bible: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    if not isinstance(bible, dict):
        return None
    protagonist = bible.get("protagonist")
    if isinstance(protagonist, dict) and protagonist.get("name") == name:
        return {"role": "主角", **protagonist}
    for group_key, label in (("supporting", "配角"), ("antagonist", "反派")):
        for entry in bible.get(group_key) or []:
            if isinstance(entry, dict) and entry.get("name") == name:
                return {"role": label, **entry}
    return None


def _format_profile(profile: Any) -> str:
    """Flatten a 女娲人设蒸馏卡 into compact prompt text."""
    if not isinstance(profile, dict) or not profile:
        return ""
    lines: List[str] = []
    for key, label in (
        ("mental_models", "心智模型"),
        ("decision_heuristics", "决策启发"),
        ("expression_dna", "表达 DNA"),
        ("anti_patterns", "反模式"),
        ("boundary", "诚实边界"),
        ("behavior_profile", "当前行为倾向"),
    ):
        value = profile.get(key)
        text = _flat(value)
        if text:
            lines.append(f"- {label}：{text[:900]}")
    return "\n".join(lines)


def _format_voice(voice: Any) -> str:
    if not isinstance(voice, dict) or not voice:
        return ""
    lines: List[str] = []
    for key, label in (
        ("catchphrases", "口头禅"),
        ("address_style", "称呼方式"),
        ("sentence_style", "句式"),
        ("emotion_expression", "情绪表达"),
        ("signature_actions", "标志动作"),
    ):
        text = _flat(voice.get(key))
        if text:
            lines.append(f"- {label}：{text[:300]}")
    return "\n".join(lines)


def _world_summary(plan: Dict[str, Any]) -> str:
    world = plan.get("worldbuilding") or {}
    parts: List[str] = []
    if plan.get("genre"):
        parts.append(f"题材：{plan['genre']}")
    if plan.get("style"):
        parts.append(f"文风：{plan['style']}")
    for key, label in (
        ("era", "时代"),
        ("geography", "地理"),
        ("power_system", "力量体系"),
        ("core_conflict", "核心矛盾"),
        ("factions", "势力"),
        ("world_canon", "世界观设定"),
    ):
        text = _flat(world.get(key))
        if text:
            parts.append(f"{label}：{text[:600]}")
    return "\n".join(parts)


def _relationship_summary(plan: Dict[str, Any], bible: Dict[str, Any]) -> str:
    characters = plan.get("characters") or {}
    parts: List[str] = []
    if isinstance(characters, dict) and characters.get("bible_summary"):
        parts.append(str(characters["bible_summary"])[:1200])
    relationship_map = bible.get("relationship_map") if isinstance(bible, dict) else None
    if relationship_map:
        parts.append("关系图：" + _flat(relationship_map)[:1200])
    return "\n".join(part for part in parts if part)


def build_ai_prompt(
    plan: Dict[str, Any],
    novel_id: str,
    targets: List[Dict[str, Any]],
    bible: Optional[Dict[str, Any]] = None,
    profiles: Optional[Dict[str, Any]] = None,
    voices: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the user prompt for one AI card-writing batch."""
    bible = bible or {}
    profiles = profiles or {}
    voices = voices or {}
    title = str(plan.get("title") or novel_id)
    lines: List[str] = [
        f"# 小说《{title}》",
        _world_summary(plan),
        "",
        "## 人物关系",
        _relationship_summary(plan, bible),
        "",
        f"## 本次需要生成角色卡的角色（共 {len(targets)} 个）",
    ]
    for index, target in enumerate(targets, 1):
        name = target["name"]
        raw = target.get("raw") or {}
        lines.append(f"### {index}. {name}（{target['role_label']}）")
        for key, label in (
            ("identity", "身份"),
            ("appearance", "外貌"),
            ("backstory", "背景"),
            ("personality", "性格"),
            ("motivation", "动机"),
            ("arc", "成长弧"),
            ("secret", "秘密"),
            ("catchphrase", "口头禅"),
            ("relationships", "关系"),
            ("power", "实力"),
            ("conflict", "冲突"),
            ("humanity", "人性面"),
            ("relation", "与主角关系"),
            ("role", "定位"),
            ("mini_arc", "自身弧线"),
            ("meaning", "对主角意义"),
            ("speak_style", "说话风格"),
            ("initial_attitude", "初始态度"),
        ):
            text = _flat(raw.get(key))
            if text:
                lines.append(f"- {label}：{text[:700]}")
        bible_entry = _bible_entry(bible, name)
        if bible_entry:
            extra = "；".join(
                f"{key}：{_flat(value)[:500]}"
                for key, value in bible_entry.items()
                if key not in ("name", "role") and _flat(value)
            )
            if extra:
                lines.append(f"- 人物宝典：{extra[:1400]}")
        profile_text = _format_profile(profiles.get(name))
        if profile_text:
            lines.append("- 人设蒸馏卡：\n" + profile_text)
        voice_text = _format_voice(voices.get(name))
        if voice_text:
            lines.append("- 声音卡：\n" + voice_text)

    lines.extend([
        "",
        "## 输出",
        f"请为以上 {len(targets)} 个角色各生成一张 SillyTavern V2 角色卡，"
        "严格输出 JSON：{\"cards\": [...]}。",
    ])
    return "\n".join(line for line in lines if line is not None)


def parse_ai_cards(content: Any) -> List[Dict[str, Any]]:
    """Robustly parse LLM output into a list of card dicts."""
    if not isinstance(content, str) or not content.strip():
        return []
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()

    def _load(candidate: str) -> Any:
        try:
            return json.loads(candidate)
        except Exception:
            return None

    payload = _load(text)
    if payload is None:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            payload = _load(match.group(0))
    if payload is None:
        return []

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("cards") or payload.get("characters") or payload.get("data")
        if items is None and "name" in payload:
            items = [payload]
    else:
        items = []
    if not isinstance(items, list):
        return []
    cards: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and str(item.get("name") or "").strip():
            cards.append(item)
    return cards


def _card_from_ai(
    raw: Dict[str, Any],
    target: Dict[str, Any],
    plan: Dict[str, Any],
    novel_id: str,
) -> Dict[str, Any]:
    title = str(plan.get("title") or novel_id)
    template = _make_card(
        name=target["name"],
        description="",
        personality="",
        scenario=_scenario_from_plan(plan, title),
        system_prompt=(
            f"你正在扮演小说《{title}》中的角色。请严格保持角色身份、性格与说话方式，"
            "不要提及自己是 AI，不要跳出角色。"
        ),
        tags=[target["role_label"], title],
        role=target["role"],
        novel_id=novel_id,
    )
    ai = normalize_card(raw)
    if str(ai.get("name") or "").strip() != target["name"]:
        # Model renamed the character: trust the plan, keep the generated body.
        ai["name"] = target["name"]
    for key in (
        "description", "personality", "scenario", "first_mes", "mes_example",
        "system_prompt", "post_history_instructions", "creator_notes",
    ):
        value = str(ai.get(key) or "").strip()
        if value:
            template[key] = value
    if ai.get("alternate_greetings"):
        template["alternate_greetings"] = ai["alternate_greetings"]
    tags = list(template.get("tags") or [])
    for tag in ai.get("tags") or []:
        if tag not in tags:
            tags.append(tag)
    template["tags"] = tags[:8]
    if not str(template.get("first_mes") or "").strip():
        template["first_mes"] = f"*{target['name']}看向你，安静地等你开口。*"
    template["creator"] = "NovelGenerator AI"
    template["character_version"] = "1.0"
    template["ai_filled"] = True
    template["source"] = "novel"
    template["novel_id"] = novel_id
    template["role_in_novel"] = target["role"]
    now = time.time()
    template["created_at"] = now
    template["updated_at"] = now
    return template


async def generate_cards_with_ai(
    llm_engine: Any,
    plan: Dict[str, Any],
    novel_id: str,
    bible: Optional[Dict[str, Any]] = None,
    profiles: Optional[Dict[str, Any]] = None,
    voices: Optional[Dict[str, Any]] = None,
    names: Optional[List[str]] = None,
    resilient: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Generate complete ST cards via the active LLM.

    Returns (cards, warnings). Roles the model fails to cover fall back to the
    deterministic template so the import never fails wholesale.
    """
    targets = plan_character_targets(plan)
    if names:
        wanted = {str(name) for name in names if str(name).strip()}
        targets = [target for target in targets if target["name"] in wanted]
    if not targets:
        return [], ["小说角色宝典中没有可生成的角色"]

    client = resilient or ResilientLLMClient(llm_engine.client, llm_engine.model)
    cards: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for start in range(0, len(targets), _AI_CHUNK_SIZE):
        chunk = targets[start:start + _AI_CHUNK_SIZE]
        prompt = build_ai_prompt(plan, novel_id, chunk, bible, profiles, voices)
        parsed: List[Dict[str, Any]] = []
        try:
            response = await asyncio.to_thread(
                client.create,
                messages=[
                    {"role": "system", "content": AI_CARD_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.85,
                max_tokens=_AI_MAX_TOKENS,
            )
            # ResilientLLMClient.create returns the OpenAI response object,
            # while some tests/older wrappers may return a plain string.
            if isinstance(response, str):
                content = response
            else:
                try:
                    content = response.choices[0].message.content or ""
                except Exception:
                    content = ""
            parsed = parse_ai_cards(content)
        except Exception as exc:  # noqa: BLE001 - fallback per batch
            log.warning("AI card generation failed: %s: %s", type(exc).__name__, str(exc)[:200])
            warnings.append(f"AI 写卡失败（{type(exc).__name__}），已回退到设定模板")

        by_name = {
            str(card.get("name") or "").strip(): card
            for card in parsed
            if str(card.get("name") or "").strip()
        }
        for target in chunk:
            raw = by_name.get(target["name"])
            if raw:
                cards.append(_card_from_ai(raw, target, plan, novel_id))
            else:
                cards.append(_make_card(
                    name=target["name"],
                    description=_flat(target["raw"].get("identity")),
                    personality=_flat(target["raw"].get("personality")),
                    scenario=_scenario_from_plan(plan, str(plan.get("title") or novel_id)),
                    system_prompt=(
                        f"你正在扮演小说《{plan.get('title') or novel_id}》中的角色。"
                        "请严格保持角色身份、性格与说话方式，不要跳出角色。"
                    ),
                    tags=[target["role_label"]],
                    role=target["role"],
                    novel_id=novel_id,
                ))
                warnings.append(f"「{target['name']}」AI 未返回完整卡片，已用设定兜底")
    return cards, warnings



def _flat(value: Any) -> str:
    """Flatten str/dict/list values from the novel plan into readable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            text = _flat(item)
            if text:
                parts.append(f"{key}：{text}")
        return "；".join(parts)
    if isinstance(value, list):
        return "；".join(_flat(item) for item in value if _flat(item))
    return str(value).strip()


def _scenario_from_plan(plan: Dict[str, Any], title: str) -> str:
    world = plan.get("worldbuilding") or {}
    genre = plan.get("genre") or ""
    parts = [f"《{title}》的世界"]
    if genre:
        parts.append(f"题材：{genre}")
    for key in ("era", "geography", "core_conflict"):
        text = _flat(world.get(key))
        if text:
            parts.append(f"{key}：{text}")
    return "；".join(parts)[:1200]


def build_cards_from_plan(plan: Dict[str, Any], novel_id: str) -> List[Dict[str, Any]]:
    """Build card dicts (without ids/timestamps) from plan characters."""
    if not isinstance(plan, dict):
        return []
    title = str(plan.get("title") or novel_id)
    characters = plan.get("characters") or {}
    if not isinstance(characters, dict):
        return []
    scenario = _scenario_from_plan(plan, title)
    system_prompt = (
        f"你正在扮演小说《{title}》中的角色。请严格保持角色身份、性格与说话方式，"
        "不要提及自己是 AI，不要跳出角色。"
    )
    cards: List[Dict[str, Any]] = []

    protagonist = characters.get("protagonist")
    if isinstance(protagonist, dict) and str(protagonist.get("name") or "").strip():
        name = str(protagonist["name"]).strip()
        description_parts = [
            _flat(protagonist.get("identity")),
            _flat(protagonist.get("appearance")),
            _flat(protagonist.get("backstory")),
        ]
        personality_parts = [
            _flat(protagonist.get("personality")),
            _flat(protagonist.get("motivation")),
            _flat(protagonist.get("catchphrase")),
        ]
        cards.append(_make_card(
            name=name,
            description="\n".join(p for p in description_parts if p),
            personality="；".join(p for p in personality_parts if p),
            scenario=scenario,
            system_prompt=system_prompt,
            tags=["主角", title],
            role="protagonist",
            novel_id=novel_id,
        ))

    for role_key, role_label in (("supporting", "配角"), ("antagonist", "反派")):
        group = characters.get(role_key)
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if role_key == "supporting":
                description = "；".join(p for p in (
                    _flat(item.get("identity")),
                    _flat(item.get("relation")),
                    _flat(item.get("role")),
                    _flat(item.get("mini_arc")),
                    _flat(item.get("meaning")),
                ) if p)
                personality = _flat(item.get("personality"))
            else:
                description = "；".join(p for p in (
                    _flat(item.get("conflict")),
                    _flat(item.get("humanity")),
                ) if p)
                personality = "；".join(p for p in (
                    _flat(item.get("motivation")),
                    _flat(item.get("power")),
                ) if p)
            cards.append(_make_card(
                name=name,
                description=description,
                personality=personality,
                scenario=scenario,
                system_prompt=system_prompt,
                tags=[role_label, title],
                role=role_key,
                novel_id=novel_id,
            ))
    return cards


def _make_card(
    *,
    name: str,
    description: str,
    personality: str,
    scenario: str,
    system_prompt: str,
    tags: List[str],
    role: str,
    novel_id: str,
) -> Dict[str, Any]:
    card = normalize_card({
        "name": name,
        "description": description,
        "personality": personality,
        "scenario": scenario,
        "system_prompt": system_prompt,
        "first_mes": "",
        "mes_example": "",
        "tags": tags,
    })
    card["source"] = "novel"
    card["novel_id"] = novel_id
    card["role_in_novel"] = role
    now = time.time()
    card["created_at"] = now
    card["updated_at"] = now
    return card
