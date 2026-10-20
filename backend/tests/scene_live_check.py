"""Live check for the novel-driven group-scene mode (interactive engine).

Requires a running backend whose NOVELGEN_NOVELS_DIR is known (set
NOVELGEN_NOVELS_DIR) and the mock/real LLM configured.
"""
from __future__ import annotations

import json
import os
import sys

import httpx

API = os.getenv("NOVELGEN_API", "http://127.0.0.1:8128")
SLUG = "scene-live-check"

failures = []


def check(name: str, condition: bool, detail: str = ""):
    print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")
    if not condition:
        failures.append(name)


def sse(client: httpx.Client, path: str, body: dict | None = None) -> list:
    events = []
    with client.stream("POST", API + path, json=body or {}) as response:
        if response.status_code != 200:
            return [{"type": "http_error", "status": response.status_code}]
        for line in response.iter_lines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
            if any(event.get("type") in ("done", "error") for event in events):
                break
    return events


def ensure_novel() -> None:
    root = os.getenv("NOVELGEN_NOVELS_DIR")
    if not root:
        return
    folder = os.path.join(root, SLUG)
    os.makedirs(folder, exist_ok=True)
    plan = {
        "title": "雨夜客栈", "genre": "武侠", "style": "冷峻",
        "worldbuilding": {
            "era": "古代", "geography": "边城",
            "core_conflict": "江湖仇杀与归隐", "factions": ["听雨楼", "六扇门"],
        },
        "characters": {
            "protagonist": {
                "name": "顾尘", "identity": "落魄剑客", "appearance": "白衣",
                "backstory": "师门覆灭", "personality": "冷淡但重情",
                "motivation": "复仇", "secret": "剑冢传人",
            },
            "supporting": [{
                "name": "苏晚", "identity": "客栈老板娘",
                "personality": "温柔敏锐", "relation": "旧识",
            }],
            "antagonist": [{
                "name": "裴越", "identity": "六扇门捕头",
                "personality": "冷静强势", "motivation": "追查剑冢",
            }],
        },
        "outline": {"volumes": []},
    }
    bible = {
        "novel_title": "雨夜客栈",
        "protagonist": {"name": "顾尘", "identity": "落魄剑客",
                        "personality": "冷淡但重情", "catchphrase": "剑在人在"},
        "supporting": [{"name": "苏晚", "identity": "客栈老板娘", "personality": "温柔敏锐"}],
        "relationship_map": {"顾尘": ["苏晚：旧识"]},
    }
    for name, payload in (("plan.json", plan), ("character_bible.json", bible)):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> int:
    ensure_novel()
    with httpx.Client(timeout=180) as client:
        health = client.get(API + "/api/health")
        check("health", health.status_code == 200, f"status={health.status_code}")

        options = client.get(f"{API}/api/novels/{SLUG}/interactive/cast-options").json()
        names = [item.get("name") for item in options.get("options", [])]
        check("cast options", len(names) >= 2, f"names={names}")

        client.post(f"{API}/api/novels/{SLUG}/interactive/restart")
        player = names[-1] if names else "顾尘"
        start_events = sse(client, f"/api/novels/{SLUG}/interactive/start",
                           {"character": player})
        start_types = [event.get("type") for event in start_events]
        scene_end = next((e for e in start_events if e.get("type") == "scene_end"), {})
        blocks = scene_end.get("blocks") or []
        speakers = [block.get("speaker") for block in blocks
                    if block.get("type") == "dialogue" and block.get("speaker")]
        check("scene start SSE", "scene_end" in start_types and "done" in start_types,
              f"types={sorted(set(start_types))}")
        check("scene has multiple characters", len(set(speakers)) >= 2,
              f"speakers={speakers}")
        check("scene blocks rendered", len(blocks) >= 2, f"blocks={len(blocks)}")

        state = client.get(f"{API}/api/novels/{SLUG}/interactive/state").json().get("state") or {}
        check("state restored blocks", len(state.get("recent_blocks") or []) >= 2,
              f"scene={state.get('scene_num')} blocks={len(state.get('recent_blocks') or [])}")

        target = names[1] if len(names) > 1 else (names[0] if names else "")
        chat_events = sse(client, f"/api/novels/{SLUG}/interactive/chat",
                          {"message": "我看向你们。", "target": target, "force_talk": True})
        chat_chunks = [e for e in chat_events if e.get("type") == "chat_chunk"]
        chat_end = next((e for e in chat_events if e.get("type") == "chat_end"), {})
        check("chat SSE", bool(chat_chunks) and bool(chat_end),
              f"types={sorted({e.get('type') for e in chat_events})}")
        check("chat speaker", chat_end.get("speaker") == target
              or any(e.get("speaker") for e in chat_chunks),
              f"speaker={chat_end.get('speaker')}")

        actions = client.get(f"{API}/api/novels/{SLUG}/interactive/actions")
        action_body = actions.json() if actions.status_code == 200 else {}
        check("action options", actions.status_code == 200
              and isinstance(action_body.get("actions"), list),
              f"count={len(action_body.get('actions') or [])}")

    print()
    if failures:
        print("FAILED:", failures)
        return 1
    print("ALL SCENE LIVE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
