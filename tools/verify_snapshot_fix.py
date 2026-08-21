# -*- coding: utf-8 -*-
"""验证 surgical 版本快照修复：快照应保存旧内容而非新内容"""
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import asyncio
from fastapi.testclient import TestClient

os.environ["NOVEL_GENERATOR_INTERNAL"] = "true"
os.environ["NOVELS_DIR"] = tempfile.mkdtemp(prefix="ng-snap-test-")

from api.server import app

client = TestClient(app)

# 准备一本带章节的书
novel_dir = os.path.join(os.environ["NOVELS_DIR"], "快照测试书")
os.makedirs(os.path.join(novel_dir, "chapters"), exist_ok=True)
plan = {
    "title": "快照测试书",
    "outline": {"total_chapters": 1, "volumes": [{"number": 1, "title": "卷一", "chapters": [
        {"number": 1, "title": "第一章", "summary": "s", "target_words": 1000}]}]},
}
with open(os.path.join(novel_dir, "plan.json"), "w", encoding="utf-8") as f:
    json.dump(plan, f, ensure_ascii=False)
with open(os.path.join(novel_dir, "chapters", "chapter_0001.md"), "w", encoding="utf-8") as f:
    f.write("这是原始正文内容第一行。\n第二行。")

# 用 continue 触发一次 surgical 写入（不依赖 LLM——用 split 不调 LLM 也行，但 split 需要 4 段）
# 为了不调 LLM，直接用 split 会改文件；先写 4 段再 split
with open(os.path.join(novel_dir, "chapters", "chapter_0001.md"), "w", encoding="utf-8") as f:
    f.write("段落一。\n\n段落二。\n\n段落三。\n\n段落四。")

nid = "快照测试书"
r = client.post(f"/api/novels/{nid}/chapters/1/surgical", json={"op": "split"})
print("split status:", r.status_code, r.json().get("detail", ""))

# 检查 .versions/v1 是否保存的是 split 前的旧内容（4 段）
vdir = os.path.join(novel_dir, ".versions")
v1_path = os.path.join(vdir, "chapter_0001.v1.md")
if os.path.exists(v1_path):
    with open(v1_path, encoding="utf-8") as f:
        v1 = f.read()
    print("v1 snapshot contains 段落一:", "段落一" in v1)
    print("v1 length:", len(v1))
else:
    print("v1 snapshot MISSING")

# 验证回滚后内容恢复
r2 = client.post(f"/api/novels/{nid}/chapters/1/versions/rollback", json={"to_version": 1})
print("rollback status:", r2.status_code, r2.json().get("detail", ""))
with open(os.path.join(novel_dir, "chapters", "chapter_0001.md"), encoding="utf-8") as f:
    after = f.read()
print("after rollback contains 段落一:", "段落一" in after)
print("after rollback length:", len(after))
