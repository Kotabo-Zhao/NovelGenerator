#!/usr/bin/env python3
"""split_server.py — 将 server.py 的 71 个路由按域拆分为独立 router 文件（纯移动，零逻辑改动）

用法: python tools/split_server.py
生成: backend/api/routers/{novels,outline,quality,storygraph,requirements,styles,trends,xhs}.py
"""
import ast
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
SRC = os.path.join(BASE, "backend", "api", "server.py")
OUT_DIR = os.path.join(BASE, "backend", "api", "routers")

# ── 路由 → 域 ──
ROUTE_DOMAIN = {}
for name in ["list_novels", "get_novel", "delete_novel", "get_quality_dashboard",
             "get_chapter", "chapter_exists", "sync_novel_state", "update_novel_plan",
             "create_novel", "create_novel_stream", "generate_chapter",
             "generate_chapter_atomic", "generate_batch", "get_batch_checkpoint",
             "export_novel", "batch_export", "get_character_bible",
             "repair_all_states", "repair_state"]:
    ROUTE_DOMAIN[name] = "novels"
for name in ["regenerate_outline", "interactive_outline", "decompose_feedback", "chapter_feedback"]:
    ROUTE_DOMAIN[name] = "outline"
for name in ["logic_check_chapter", "logic_check_batch", "validate_chapter_consistency",
             "validate_outline_consistency", "analyze_opening", "opening_alternatives",
             "design_twists", "summarize_chapters", "get_token_budget",
             "design_chapter_twist", "check_pacing"]:
    ROUTE_DOMAIN[name] = "quality"
for name in ["get_storygraph", "get_arcs", "get_calibration", "get_storygraph_visualization",
             "update_thread", "delete_thread", "update_foreshadow", "create_foreshadow",
             "update_character", "quick_action"]:
    ROUTE_DOMAIN[name] = "storygraph"
for name in ["preview_decompose", "decompose_requirements", "update_requirements",
             "get_requirements", "supervise_requirements", "verify_and_fix_loop"]:
    ROUTE_DOMAIN[name] = "requirements"
for name in ["get_styles", "get_style_params", "build_custom_style_api",
             "list_style_seeds", "save_style_seed", "delete_style_seed",
             "style_fingerprint", "compare_styles"]:
    ROUTE_DOMAIN[name] = "styles"
for name in ["analyze_trends", "generate_bizarre", "quick_bizarre", "create_bizarre_novel"]:
    ROUTE_DOMAIN[name] = "trends"
for name in ["list_xhs_templates", "create_xhs_novel", "list_xhs_presets", "generate_titles_endpoint"]:
    ROUTE_DOMAIN[name] = "xhs"

# ── 辅助函数 → 域 ──
HELPER_DOMAIN = {
    "_generate_quality_recommendations": "novels",
    "_checkpoint_path": "novels", "_save_batch_checkpoint": "novels",
    "_read_batch_checkpoint": "novels", "_clear_batch_checkpoint": "novels",
    "_read_novel_file": "storygraph", "_filter_storygraph_to_chapter": "storygraph",
    "_compute_storygraph_stats": "storygraph", "_write_novel_file": "storygraph",
    "_validate_thread_fields": "storygraph", "_build_character_relation_graph": "storygraph",
    "_build_plot_timeline": "storygraph",
    # 未被子路由直接调用的校验工具 → novels（通用校验）
    "_validate_novel_id": "novels", "_validate_chapter_range": "novels",
}

# ── 模块级 Assign → 域 ──
ASSIGN_DOMAIN = {
    "STYLE_SEEDS_DIR": "styles",
    "fingerprinter": "styles",
}

def main():
    with open(SRC, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)

    domains = {d: [] for d in set(ROUTE_DOMAIN.values()) | set(HELPER_DOMAIN.values()) | set(ASSIGN_DOMAIN.values())}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 路由？
            is_route = False
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ("get", "post", "put", "delete", "patch"):
                    is_route = True
                    break
            if is_route:
                dom = ROUTE_DOMAIN.get(node.name)
                if not dom:
                    print(f"⚠️ 未分类路由 {node.name} @L{node.lineno} — 留在主文件")
                    continue
            else:
                dom = HELPER_DOMAIN.get(node.name)
                if not dom:
                    print(f"⚠️ 未分类辅助函数 {node.name} @L{node.lineno} — 留在主文件")
                    continue
            block_start = node.lineno
            if node.decorator_list:
                block_start = min(d.lineno for d in node.decorator_list)
            block = "".join(lines[block_start - 1:node.end_lineno])
            domains[dom].append((node.lineno, block))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ASSIGN_DOMAIN:
                    block = "".join(lines[node.lineno - 1:node.end_lineno])
                    domains[ASSIGN_DOMAIN[t.id]].append((node.lineno, block))

    os.makedirs(OUT_DIR, exist_ok=True)

    # 每个域需要的 core 导入（从块文本中提取 from core.xxx import）
    import re
    domain_imports = {}
    for dom, blocks in domains.items():
        imports = set()
        for _, block in blocks:
            for m in re.finditer(r"from\s+(core|\.\w+)\.(\w+)\s+import\s+([^\n]+)", block):
                imports.add(f"from core.{m.group(2)} import {m.group(3).strip()}")
            for m in re.finditer(r"from\s+core\s+import\s+([^\n]+)", block):
                imports.add(f"from core import {m.group(1).strip()}")
        domain_imports[dom] = sorted(imports)

    headers = {
        "novels": "小说 CRUD / 生成 / 导出 / 批次 / 状态修复",
        "outline": "大纲交互 / 反馈拆解 / 章节反馈",
        "quality": "逻辑校验 / 一致性 / 开局 / 反转 / 节奏 / 摘要",
        "storygraph": "剧情图谱 / 弧 / 校准 / 可视化 / 快捷操作",
        "requirements": "需求拆解与监督",
        "styles": "风格系统 / 种子 / 指纹",
        "trends": "爆火分析 / 逆天生成",
        "xhs": "小红书爆款短篇",
    }

    for dom, blocks in domains.items():
        blocks.sort(key=lambda x: x[0])
        body = "\n\n".join(b for _, b in blocks)
        header = (
            f'"""NovelGenerator — {headers.get(dom, dom)} API Router"""\n'
            f"import json\nimport os\n"
            f"from typing import Optional\n"
            f"from fastapi import APIRouter, HTTPException\n"
            f"from fastapi.responses import StreamingResponse, JSONResponse\n"
            f"from fastapi.staticfiles import StaticFiles\n"
        )
        deps_import = "from .deps import engine, log, _validate_novel_id, _validate_chapter_range\n"
        core_imports = "\n".join(domain_imports.get(dom, []))
        if core_imports:
            core_imports += "\n"
        extra = ""
        if dom == "trends":
            extra = 'import traceback\n'
        if dom == "xhs":
            extra = "from core.xiaohongshu import TEMPLATES, create_xhs_novel_pipeline, generate_titles, PRESETS, get_presets\n"
        content = f"{header}{extra}\nrouter = APIRouter()\n\n{deps_import}{core_imports}\n{body}\n"
        # 把 @app. 替换为 @router.
        content = content.replace("@app.", "@router.")
        out_path = os.path.join(OUT_DIR, f"{dom}.py")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ {dom}.py  ({len(blocks)} 个节点, {len(body.splitlines())} 行)")

    # 统计
    total = sum(len(b) for b in domains.values())
    print(f"\n共移动 {total} 个顶层节点到 8 个 router 文件")

if __name__ == "__main__":
    main()
