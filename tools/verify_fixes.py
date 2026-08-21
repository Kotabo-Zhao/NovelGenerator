# -*- coding: utf-8 -*-
"""验证 quality-dashboard 与 ideas 鉴权修复"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8010"

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))

def post(path, data=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))

novels = get("/api/novels")["novels"]
print("novels:", [(n["id"], n["title"]) for n in novels])
if novels:
    nid = novels[0]["id"]
    q = get(f"/api/novels/{urllib.request.quote(nid)}/quality-dashboard")
    print("quality keys:", list(q.get("quality", {}).keys()))
    fs = q.get("quality", {}).get("foreshadowing", {})
    print("foreshadowing:", json.dumps(fs, ensure_ascii=False)[:300])
    # ideas 鉴权（internal 模式应可访问）
    ideas = get("/api/ideas")
    print("ideas ok:", list(ideas.keys()))
    # 批量导出（恢复后应可用；无章节会报错但不应 410）
    try:
        b = post("/api/novels/export/batch", {"novel_ids": [nid], "fmt": "txt"})
        print("batch export:", json.dumps(b, ensure_ascii=False)[:200])
    except Exception as e:
        print("batch export error:", e)
