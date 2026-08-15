"""NovelGenerator — 世界圣经管理 API（创作者工具 M2）

bible.json 是**人工可编辑**的世界设定层，与 character_bible.json（AI 生成层）共存：
- 首次访问时从 character_bible + plan.json 自动合并初始化
- 人工编辑优先展示；AI 生成层只读，不覆盖人工修改
"""
import json
import os
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter()

from ..deps import engine, log, _validate_novel_id
from ..auth import require_owner, is_internal_version
from core.atomic_io import atomic_write_json

SCHEMA_VERSION = 1


def _bible_path(novel_id: str) -> str:
    return os.path.join(engine.memory.get_novel_dir(novel_id), "bible.json")


def _default_bible() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "characters": {},
        "locations": {},
        "terms": {},
        "relations": [],
    }


def _merge_from_character_bible(bible: dict, novel_id: str) -> dict:
    """从 character_bible.json 合并人物（仅当 bible 中没有该人物时补充）。"""
    cbp = os.path.join(engine.memory.get_novel_dir(novel_id), "character_bible.json")
    if not os.path.exists(cbp):
        return bible
    try:
        with open(cbp, "r", encoding="utf-8") as f:
            cb = json.load(f)
        for name, prof in (cb or {}).items():
            if not isinstance(prof, dict) or name in bible.get("characters", {}):
                continue
            bible.setdefault("characters", {})[name] = {
                "name": name,
                "identity": str(prof.get("identity", "")),
                "appearance": str(prof.get("appearance", "")),
                "personality": prof.get("personality", {}) if isinstance(prof.get("personality"), dict) else {},
                "motivation": prof.get("motivation", {}) if isinstance(prof.get("motivation"), dict) else {},
                "arc": str(prof.get("arc", "")),
                "note": "",
            }
    except Exception as e:
        log.warning(f"Bible merge from character_bible failed: {e}")
    return bible


def _merge_from_plan(bible: dict, novel_id: str) -> dict:
    """从 plan.json 合并主角/配角/地点/势力（补充缺失项）。"""
    try:
        plan = engine.get_novel(novel_id) or {}
        chars = plan.get("characters", {}) or {}
        prot = chars.get("protagonist", {}) or {}
        if prot.get("name") and prot["name"] not in bible.get("characters", {}):
            bible.setdefault("characters", {})[prot["name"]] = {
                "name": prot["name"],
                "identity": str(prot.get("identity", "")),
                "appearance": str(prot.get("appearance", "")),
                "personality": prot.get("personality", {}) if isinstance(prot.get("personality"), dict) else {},
                "motivation": prot.get("motivation", {}) if isinstance(prot.get("motivation"), dict) else {},
                "arc": str(prot.get("arc", "")),
                "note": "主角",
            }
        for c in (chars.get("supporting", []) or [])[:10]:
            if isinstance(c, dict) and c.get("name") and c["name"] not in bible.get("characters", {}):
                bible.setdefault("characters", {})[c["name"]] = {
                    "name": c["name"],
                    "identity": str(c.get("identity", "")),
                    "appearance": "",
                    "personality": {},
                    "motivation": {},
                    "arc": str(c.get("mini_arc", "")),
                    "note": "配角",
                }
        wb = plan.get("worldbuilding", {}) or {}
        geo = str(wb.get("geography", ""))
        for loc_name in re.findall(r"[（(]?([\u4e00-\u9fff]{2,8})[）)]?", geo)[:8]:
            if loc_name and loc_name not in bible.get("locations", {}):
                bible.setdefault("locations", {})[loc_name] = {"description": "", "note": "自动提取"}
        for f in (wb.get("factions", []) or [])[:8]:
            if isinstance(f, dict) and f.get("name") and f["name"] not in bible.get("terms", {}):
                bible.setdefault("terms", {})[f["name"]] = {
                    "category": "势力", "description": str(f.get("description", "")), "note": ""
                }
    except Exception as e:
        log.warning(f"Bible merge from plan failed: {e}")
    return bible


def _load_bible(novel_id: str, auto_merge: bool = True) -> dict:
    path = _bible_path(novel_id)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                bible = json.load(f)
            for k, v in _default_bible().items():
                bible.setdefault(k, v if isinstance(v, (dict, list)) else v)
            return bible
        except Exception:
            pass
    bible = _default_bible()
    if auto_merge:
        bible = _merge_from_character_bible(bible, novel_id)
        bible = _merge_from_plan(bible, novel_id)
    return bible


def _save_bible(novel_id: str, bible: dict) -> None:
    bible["schema_version"] = SCHEMA_VERSION
    atomic_write_json(_bible_path(novel_id), bible)


def _require_owner(request: Request, novel_id: str) -> None:
    owner_id = getattr(request.state, "owner_id", None)
    if not owner_id and not is_internal_version():
        raise HTTPException(status_code=401, detail="需要登录")
    _validate_novel_id(novel_id)


@router.get("/api/novels/{novel_id}/bible")
async def get_bible(novel_id: str, request: Request):
    """读取世界圣经（自动合并初始化）。"""
    _require_owner(request, novel_id)
    bible = _load_bible(novel_id)
    return {"bible": bible}


@router.put("/api/novels/{novel_id}/bible")
async def save_bible(novel_id: str, req: dict, request: Request):
    """全量保存世界圣经（前端整份提交）。"""
    _require_owner(request, novel_id)
    if not isinstance(req, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    _save_bible(novel_id, req)
    return {"status": "ok", "bible": _load_bible(novel_id, auto_merge=False)}


@router.post("/api/novels/{novel_id}/bible/terms/extract")
async def extract_terms(novel_id: str, request: Request):
    """从已写正文提取专有名词候选（正则初筛人名/地名/术语 + 去重）。"""
    _require_owner(request, novel_id)
    chapters_dir = os.path.join(engine.memory.get_novel_dir(novel_id), "chapters")
    text = ""
    if os.path.isdir(chapters_dir):
        for fname in sorted(os.listdir(chapters_dir)):
            if fname.endswith(".md"):
                try:
                    with open(os.path.join(chapters_dir, fname), "r", encoding="utf-8") as f:
                        text += f.read() + "\n"
                except Exception:
                    continue
    if len(text) < 50:
        return {"status": "ok", "candidates": []}

    candidates = {}
    # 2-4 字中文词频统计（粗略提取：书名号、引号内、高频词）
    for m in re.finditer(r"[《「『「]([^《「』」]{2,12})[》」』」]", text):
        name = m.group(1).strip()
        if len(name) >= 2 and name not in candidates:
            candidates[name] = {"type": "quoted", "count": text.count(name), "category": "术语"}
    # 高频 2-4 字词（排除常见虚词）
    stop = {"我们", "他们", "自己", "这个", "那个", "一个", "没有", "什么", "就是", "知道", "已经", "突然", "然后", "因为", "所以", "如果", "可以", "起来", "时候", "觉得", "还是", "但是", "不过", "非常", "一定", "终于", "现在", "这里", "那里", "主角", "本章", "正文"}
    words = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
    from collections import Counter
    for w, cnt in Counter(words).most_common(80):
        if w in stop or w in candidates or cnt < 3:
            continue
        candidates[w] = {"type": "freq", "count": cnt, "category": "术语"}
    # 已存在的名词跳过（返回时前端标记）
    bible = _load_bible(novel_id)
    existing = set(bible.get("terms", {}).keys()) | set(bible.get("characters", {}).keys())
    result = []
    for name, info in candidates.items():
        result.append({"name": name, "category": info["category"],
                       "count": info["count"], "exists": name in existing})
    result.sort(key=lambda x: -x["count"])
    return {"status": "ok", "candidates": result[:60]}


@router.get("/api/novels/{novel_id}/bible/relations/render")
async def bible_relations(novel_id: str, request: Request):
    """关系图数据（前端直接渲染）。"""
    _require_owner(request, novel_id)
    bible = _load_bible(novel_id)
    chars = bible.get("characters", {})
    relations = bible.get("relations", [])
    nodes = [{"id": name, "label": name} for name in chars.keys()]
    edges = []
    for r in relations:
        if r.get("from") and r.get("to"):
            edges.append({"from": r["from"], "to": r["to"], "type": r.get("type", "未知"), "note": r.get("note", "")})
    return {"nodes": nodes, "edges": edges}
