#!/usr/bin/env python3
"""split_engine.py — 将 NovelEngine 上帝类按职责拆为 Mixin

拆分为 5 个 Mixin（生成/校验/分析/需求/导出），NovelEngine 继承之，
对外接口（路由/测试调用方式）零变化。

用法: python tools/split_engine.py
生成: backend/core/mixins/{generation,validation,analysis,requirements,export}.py
"""
import ast
import os

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "core")
SRC = os.path.join(BASE, "engine.py")
OUT_DIR = os.path.join(BASE, "mixins")

# 方法 → Mixin 归属
METHOD_MAP = {
    # GenerationMixin: 章节生成（普通 + 原子化）
    "generate_chapter_stream": "generation",
    "atomic_generate_chapter_stream": "generation",
    "_find_chapter_outline": "generation",
    # ValidationMixin: 一致性/逻辑校验
    "validate_chapter_consistency": "validation",
    "validate_outline_consistency": "validation",
    "validate_chapter_full": "validation",
    "build_logic_fix_prompt": "validation",
    # AnalysisMixin: 开局/反转/节奏分析
    "analyze_opening": "analysis",
    "generate_opening_alternatives": "analysis",
    "design_twists": "analysis",
    "design_chapter_twist": "analysis",
    # RequirementsMixin: 需求拆解/更新/监督/闭环
    "decompose_requirements": "requirements",
    "update_requirements": "requirements",
    "supervise_requirements": "requirements",
    "verify_and_fix_loop": "requirements",
    # ExportMixin: 导出
    "export_novel": "export",
    "_export_epub": "export",
}

# 保留在 NovelEngine 的方法（核心编排 + 状态 + 创建流程）
KEEP_METHODS = {
    "__init__", "create_novel", "_init_storygraph_and_arcs", "create_novel_stream",
    "regenerate_outline_stream", "get_novel", "update_plan", "list_novels",
    "delete_novel", "get_chapter", "_detect_narrative_pov", "_save_narrative_pov",
    "_save_character_bible", "save_character_bible", "_format_char_entry",
    "_build_relationship_map", "interactive_outline_stream", "decompose_feedback",
}

# 模块级函数 → 归属（随生成 Mixin 走）
MODULE_FN_MAP = {
    "_extract_key_ending": "generation",
    "_protect_ending_semantic": "generation",
}

MIXIN_DOCS = {
    "generation": "章节生成 — 普通路径（Writer 两遍式）与原子化路径（逐 beat）",
    "validation": "质量校验 — 逻辑监督 / 大纲一致性 / 章节一致性",
    "analysis": "创作分析 — 开局评估 / 开局备选 / 反转设计",
    "requirements": "需求拆解与监督 — 拆解 / 更新 / 监督 / 闭环修复",
    "export": "导出 — TXT / EPUB",
}


def main():
    with open(SRC, encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)

    # 1. 提取方法块（含装饰器，按归属分组）
    mixin_blocks = {k: [] for k in METHOD_MAP.values()}
    class_node = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "NovelEngine":
            class_node = node
            break
    assert class_node, "NovelEngine class not found"

    remove_spans = []  # (start_line, end_line) 1-based，含装饰器
    for m in class_node.body:
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
            dom = METHOD_MAP.get(m.name)
            if dom:
                start = m.lineno
                if m.decorator_list:
                    start = min(d.lineno for d in m.decorator_list)
                block = "".join(lines[start - 1:m.end_lineno])
                mixin_blocks[dom].append((m.name, block))
                remove_spans.append((start, m.end_lineno))

    # 2. 提取模块级函数（_extract_key_ending 等）
    module_fn_blocks = {k: [] for k in MODULE_FN_MAP.values()}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            dom = MODULE_FN_MAP.get(node.name)
            if dom:
                block = "".join(lines[node.lineno - 1:node.end_lineno])
                module_fn_blocks[dom].append((node.name, block))
                remove_spans.append((node.lineno, node.end_lineno))

    # 3. 生成 mixin 文件
    os.makedirs(OUT_DIR, exist_ok=True)
    for dom, blocks in mixin_blocks.items():
        blocks.sort(key=lambda x: x[0])
        body = "\n\n".join(b for _, b in blocks)
        doc = MIXIN_DOCS[dom]
        content = (
            f'"""NovelEngine {dom.capitalize()}Mixin — {doc}\n\n'
            f"由 tools/split_engine.py 从 engine.py 自动拆分。\n"
            f'依赖 NovelEngine 提供的 self.client/self.model/self.memory 等属性。\n"""\n'
            f"from typing import AsyncGenerator, Optional, AsyncIterator\n\n\n"
        )
        # 模块级函数（目前只有 generation 有）
        for fn_name, fn_block in module_fn_blocks.get(dom, []):
            content += f"\n\n# ===== {fn_name} (从 engine.py 迁移) =====\n{fn_block}\n"
        content += f"\n\nclass {dom.capitalize()}Mixin:\n"
        content += body + "\n"
        # 类内方法已有 4 空格缩进（原类内），可直接放
        out_path = os.path.join(OUT_DIR, f"{dom}.py")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ mixins/{dom}.py  ({len(blocks)} 个方法)")

    # 4. 重写 engine.py：删除被移走的方法与模块级函数，改类继承
    remove_spans.sort()
    new_lines = []
    skip = False
    skip_until = -1
    idx = 0
    spans = list(remove_spans)
    for i, line in enumerate(lines):
        # 跳过被移除的行
        while spans and i + 1 > spans[0][1]:
            spans.pop(0)
        if spans and i + 1 >= spans[0][0] and i + 1 <= spans[0][1]:
            continue
        new_lines.append(line)

    new_src = "".join(new_lines)
    # 类声明加 Mixin 继承
    new_src = new_src.replace(
        "class NovelEngine:",
        "from .mixins.generation import GenerationMixin\n"
        "from .mixins.validation import ValidationMixin\n"
        "from .mixins.analysis import AnalysisMixin\n"
        "from .mixins.requirements import RequirementsMixin\n"
        "from .mixins.export import ExportMixin\n"
        "\n\n"
        "class NovelEngine(GenerationMixin, ValidationMixin, AnalysisMixin,\n"
        "                  RequirementsMixin, ExportMixin):",
        1,
    )
    # 移除文件顶部原有的 Humanizer 结尾保护注释块（函数已移走）
    new_src = new_src.replace(
        '# ═══════════════════════════════════════════\n# v2.13: Humanizer 语义结尾保护\n# ═══════════════════════════════════════════\n\n\n',
        "",
    )
    with open(SRC, "w", encoding="utf-8") as f:
        f.write(new_src)
    print(f"\n✅ engine.py 重写完成（保留方法 + {len(MIXIN_DOCS)} 个 Mixin 继承）")


if __name__ == "__main__":
    main()
