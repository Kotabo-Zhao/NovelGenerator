#!/usr/bin/env python3
"""post_process_routers.py — 拆分后的 router 文件自动修复脚本

在 split_server.py 生成后运行，完成：
1. 相对导入修正（.deps → ..deps）
2. deps import 补充 _sse_with_heartbeat（outline/requirements）
3. novels: 补全 import（asyncio/urllib/PlainTextResponse/Response/config）+ 请求模型 + 移走 _sse_with_heartbeat
4. quality: 补 check_and_compress
5. styles: 补 StyleFingerprint（安全插入到模块顶层）
6. trends: _get_engine → deps engine
"""
import ast
import os

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "api", "routers")

def read(name):
    with open(os.path.join(BASE, name), encoding="utf-8") as f:
        return f.read()

def write(name, s):
    with open(os.path.join(BASE, name), "w", encoding="utf-8") as f:
        f.write(s)

# 1. 相对导入 + deps 补充
for name in ["novels", "outline", "quality", "storygraph", "requirements", "styles", "trends", "xhs"]:
    s = read(f"{name}.py")
    s = s.replace("from .deps import", "from ..deps import")
    if name in ("outline", "requirements"):
        s = s.replace(
            "from ..deps import engine, log, _validate_novel_id, _validate_chapter_range\n",
            "from ..deps import engine, log, _validate_novel_id, _validate_chapter_range, _sse_with_heartbeat\n",
        )
    write(f"{name}.py", s)
print("1️⃣ 相对导入修正 ✅")

# 2. novels.py: 头部 import + 模型 + 删除 _sse_with_heartbeat
s = read("novels.py")
# 头部 import
s = s.replace(
    '"""NovelGenerator — 小说 CRUD / 生成 / 导出 / 批次 / 状态修复 API Router"""\nimport json\nimport os\nfrom typing import Optional\n',
    '"""NovelGenerator — 小说 CRUD / 生成 / 导出 / 批次 / 状态修复 API Router"""\nimport asyncio\nimport json\nimport os\nimport urllib.parse\nfrom typing import Optional\n',
)
s = s.replace(
    "from fastapi.responses import StreamingResponse, JSONResponse\n",
    "from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse, Response\n",
)
s = s.replace(
    "from pydantic import BaseModel\n",
    "from pydantic import BaseModel\n\nfrom config import NOVELS_DIR, DEFAULT_CHAPTER_WORDS\n",
)
s = s.replace(
    "from ..deps import engine, log, _validate_novel_id, _validate_chapter_range\n",
    "from ..deps import engine, log, _validate_novel_id, _validate_chapter_range, _sse_with_heartbeat\n",
)
# 追加请求模型（在 router = APIRouter() 之后）
model_block = '''

class CreateNovelRequest(BaseModel):
    genre: str = "玄幻"
    style: str = "热血爽文"
    inspiration: str = ""
    target_words: int = 500000
    title: str = ""
    natural_names: bool = True  # 自然命名，去AI味
    normal_pacing: bool = False  # v2.2: 默认快节奏
    fast_food: bool = False  # v2.7: 快餐模式


class GenerateChapterRequest(BaseModel):
    novel_id: str
    chapter_num: int
    writing_mode: str = "webnovel"  # "webnovel" | "literary"
    feedback: Optional[str] = None  # 用户修改意见（重生成场景）

'''
s = s.replace("router = APIRouter()\n", "router = APIRouter()\n" + model_block, 1)
# 删除 _sse_with_heartbeat 函数（保留调用）
tree = ast.parse(s)
lines = s.splitlines(keepends=True)
removed = False
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_sse_with_heartbeat":
        start = node.lineno
        if node.decorator_list:
            start = min(d.lineno for d in node.decorator_list)
        del lines[start - 1:node.end_lineno]
        removed = True
        break
if removed:
    s = "".join(lines).rstrip() + "\n"
    print("2️⃣ _sse_with_heartbeat 已从 novels.py 移除")
write("novels.py", s)

# 3. quality.py: check_and_compress
s = read("quality.py")
if "check_and_compress" in s and "from core.chapter_summarizer import check_and_compress" not in s:
    s = s.replace(
        "from ..deps import engine, log, _validate_novel_id, _validate_chapter_range\n",
        "from ..deps import engine, log, _validate_novel_id, _validate_chapter_range\nfrom core.chapter_summarizer import check_and_compress\n",
    )
    write("quality.py", s)
print("3️⃣ quality.py ✅")

# 4. styles.py: StyleFingerprint 安全插入（模块顶层，用 AST 找 router 定义行后）
s = read("styles.py")
if "StyleFingerprint" in s and "from core.style_fingerprint import StyleFingerprint" not in s:
    tree = ast.parse(s)
    router_line = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "router" for t in node.targets):
            router_line = node.end_lineno
            break
    lines = s.splitlines(keepends=True)
    lines.insert(router_line, "from core.style_fingerprint import StyleFingerprint\n")
    s = "".join(lines)
    write("styles.py", s)
print("4️⃣ styles.py ✅")

# 5. trends.py: _get_engine → engine
s = read("trends.py")
s = s.replace("        engine = _get_engine()\n", "        # engine 来自 deps 单例\n")
write("trends.py", s)
print("5️⃣ trends.py ✅")

# 6. deps.py: 追加 _sse_with_heartbeat（从 git 原始 server.py 提取）
deps_path = os.path.join(os.path.dirname(BASE), "deps.py")
with open(deps_path, encoding="utf-8") as f:
    deps_src = f.read()
if "_sse_with_heartbeat" not in deps_src:
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    orig = subprocess.run(
        ["git", "show", "HEAD:backend/api/server.py"],
        capture_output=True, text=True, cwd=root,
    ).stdout
    tree = ast.parse(orig)
    lines = orig.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_sse_with_heartbeat":
            start = node.lineno
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            block = "".join(lines[start - 1:node.end_lineno])
            with open(deps_path, "a", encoding="utf-8") as f:
                f.write(f"\n\n# ── SSE 心跳包装（novels/outline/requirements 共用）──\n{block}")
            print("6️⃣ deps.py 已追加 _sse_with_heartbeat")
            break
    else:
        print("⚠️ 原始 server.py 中未找到 _sse_with_heartbeat")
else:
    print("6️⃣ deps.py 已含 _sse_with_heartbeat")

print("\n全部后处理完成")
