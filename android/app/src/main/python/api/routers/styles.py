"""NovelGenerator — 风格系统 / 种子 / 指纹 API Router"""
import json
import os
from typing import Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter()
from core.style_fingerprint import StyleFingerprint

from ..deps import engine, log, _validate_novel_id, _validate_chapter_range
from core.styles import CUSTOM_STYLE_PARAMS
from core.styles import build_parameterized_style
from core.styles import get_style_categories, STYLES

@router.get("/api/styles")
async def get_styles():
    """返回所有可用风格（分组）"""
    from core.styles import get_style_categories, STYLES
    categories = get_style_categories()
    result = {}
    for cat, names in categories.items():
        items = []
        for n in names:
            s = STYLES.get(n)
            if not s: continue
            items.append({
                "name": n,
                "author": s["author"],
                "desc": s.get("prose", "")[:80] + "…",
                "is_custom": s.get("is_custom", False),
            })
        if items:
            result[cat] = items
    return {"categories": result}


@router.get("/api/styles/params")
async def get_style_params():
    """返回自定义风格的参数化配置选项"""
    from core.styles import CUSTOM_STYLE_PARAMS
    return {"params": CUSTOM_STYLE_PARAMS}


@router.post("/api/styles/build-custom")
async def build_custom_style_api(req: dict):
    """从用户选择的参数构建自定义风格"""
    from core.styles import build_parameterized_style
    style = build_parameterized_style(req)
    return {"style": style}


STYLE_SEEDS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "style_seeds")


@router.get("/api/styles/seeds")
async def list_style_seeds():
    """列出所有保存的风格种子"""
    seeds = []
    if os.path.exists(STYLE_SEEDS_DIR):
        for fname in os.listdir(STYLE_SEEDS_DIR):
            if fname.endswith(".json"):
                path = os.path.join(STYLE_SEEDS_DIR, fname)
                with open(path, "r", encoding="utf-8") as f:
                    seed = json.load(f)
                    seeds.append({"name": seed.get("name", fname[:-5]), "author": seed.get("author", ""), "filename": fname})
    return {"seeds": seeds}


@router.post("/api/styles/seeds")
async def save_style_seed(seed: dict):
    """保存风格种子"""
    name = seed.get("name", "未命名").strip()
    if not name:
        raise HTTPException(status_code=400, detail="风格名称不能为空")
    safe_name = "".join(c for c in name if c.isalnum() or c in " _-") or "custom_style"
    path = os.path.join(STYLE_SEEDS_DIR, f"{safe_name}.json")
    seed["saved_at"] = __import__("datetime").datetime.now().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False, indent=2)
    return {"success": True, "name": safe_name}


@router.delete("/api/styles/seeds/{name}")
async def delete_style_seed(name: str):
    path = os.path.join(STYLE_SEEDS_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
    return {"success": True}


fingerprinter = StyleFingerprint()


@router.post("/api/styles/fingerprint")
async def style_fingerprint(req: dict):
    """分析文本的风格指纹（5维DNA）"""
    text = req.get("text", "")
    if not text or len(text) < 500:
        raise HTTPException(status_code=400, detail="至少需要500字")

    fp = fingerprinter.analyze(text)
    return {"fingerprint": fp}


@router.post("/api/styles/compare")
async def compare_styles(req: dict):
    """对比两个文本的风格差异"""
    text_a = req.get("text_a", "")
    text_b = req.get("text_b", "")
    if not text_a or not text_b:
        raise HTTPException(status_code=400, detail="需要两个文本")

    comparison = fingerprinter.compare(text_a, text_b)
    return {"comparison": comparison}

