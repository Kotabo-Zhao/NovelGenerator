"""Live check: exercise the real FastAPI + OpenAI client against the mock LLM.

Prereqs: mock server on :8124 and NovelGenerator server on :8123.
Run:
    .venv/Scripts/python.exe backend/tests/roleplay_live_check.py
"""
from __future__ import annotations

import base64
import json
import os
import sys

import httpx

API = os.getenv("NOVELGEN_API", "http://127.0.0.1:8123")
CARD = {
    "spec": "chara_card_v2",
    "data": {
        "name": "顾尘",
        "description": "一位沉默的{{char}}，剑术精湛。",
        "personality": "冷淡但重情",
        "scenario": "深夜的客栈",
        "first_mes": "「坐。」{{char}}把剑放在桌上。",
        "mes_example": "<START>\n{{user}}: 你是谁？\n{{char}}: 一个路过的人。",
        "alternate_greetings": ["「又是你。」"],
        "tags": ["测试"],
    },
}

failures = []


def check(name: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name} {detail}")
    if not condition:
        failures.append(name)


def sse(client: httpx.Client, url: str, body: dict | None = None) -> list:
    events = []
    with client.stream("POST", url, json=body or {}) as stream:
        for line in stream.iter_lines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
    return events


def main() -> int:
    with httpx.Client(timeout=60.0) as client:
        health = client.get(f"{API}/api/health")
        check("health", health.status_code == 200, f"status={health.status_code}")

        response = client.get(f"{API}/api/roleplay/characters")
        characters = response.json().get("characters") or []
        check("builtin character seeded", response.status_code == 200 and len(characters) >= 1,
              f"count={len(characters)}")

        payload = base64.b64encode(json.dumps(CARD, ensure_ascii=False).encode()).decode()
        imported = client.post(
            f"{API}/api/roleplay/characters/import",
            json={"filename": "card.json", "data_base64": payload},
        )
        imported_body = imported.json() if imported.status_code == 200 else {}
        card = imported_body.get("character") or {}
        check("import JSON card", imported.status_code == 200 and card.get("name") == "顾尘",
              str(imported_body)[:160])
        char_id = card.get("id")

        if char_id:
            start = client.post(f"{API}/api/roleplay/characters/{char_id}/chat/start")
            messages = (start.json() or {}).get("messages") or []
            check("chat start greeting", start.status_code == 200 and len(messages) == 1,
                  f"messages={len(messages)}")

            events = []
            with client.stream(
                "POST",
                f"{API}/api/roleplay/characters/{char_id}/chat/send",
                json={"message": "你好"},
            ) as stream:
                check("send SSE status", stream.status_code == 200, f"status={stream.status_code}")
                for line in stream.iter_lines():
                    if line.startswith("data: "):
                        try:
                            events.append(json.loads(line[6:]))
                        except json.JSONDecodeError:
                            pass
            types = [e.get("type") for e in events]
            text = "".join(e.get("content", "") for e in events if e.get("type") == "delta")
            check("SSE meta/delta/done", "meta" in types and "delta" in types and "done" in types,
                  f"types={types}")
            check("mock reply streamed", "mock 模型" in text, f"text={text!r}")

            chat = client.get(f"{API}/api/roleplay/characters/{char_id}/chat").json()
            messages = chat.get("messages") or []
            check("assistant reply persisted",
                  messages and messages[-1].get("role") == "assistant"
                  and "mock 模型" in messages[-1].get("content", ""),
                  f"last={messages[-1] if messages else None}")

            regen_events = []
            with client.stream(
                "POST", f"{API}/api/roleplay/characters/{char_id}/chat/regenerate"
            ) as stream:
                for line in stream.iter_lines():
                    if line.startswith("data: "):
                        try:
                            regen_events.append(json.loads(line[6:]))
                        except json.JSONDecodeError:
                            pass
            regen_types = [e.get("type") for e in regen_events]
            check("regenerate SSE", "done" in regen_types, f"types={regen_types}")

            chat = client.get(f"{API}/api/roleplay/characters/{char_id}/chat").json()
            messages = chat.get("messages") or []
            assistants = [m for m in messages if m.get("role") == "assistant"]
            check("regenerate does not duplicate greeting", len(assistants) == 2,
                  f"assistants={len(assistants)}")

            # ── v2 slash commands / worldinfo ─────────────────────────────
            help_events = sse(client, f"{API}/api/roleplay/characters/{char_id}/chat/send",
                              {"message": "/help"})
            check("command /help", any(e.get("type") == "command_result" and "/swipe" in e.get("text", "")
                                       for e in help_events),
                  f"types={[e.get('type') for e in help_events]}")

            roll_events = sse(client, f"{API}/api/roleplay/characters/{char_id}/chat/send",
                              {"message": "/roll 1d20+3"})
            roll_message = next((e.get("message") for e in roll_events if e.get("type") == "message"), {})
            check("command /roll", roll_message.get("kind") == "roll"
                  and any(e.get("type") == "command_result" for e in roll_events),
                  f"kind={roll_message.get('kind')}")

            nar_events = sse(client, f"{API}/api/roleplay/characters/{char_id}/chat/send",
                             {"message": "/nar 夜色更深了。"})
            nar_message = next((e.get("message") for e in nar_events if e.get("type") == "message"), {})
            check("command /nar", nar_message.get("kind") == "narration"
                  and "夜色更深" in nar_message.get("content", ""),
                  f"message={str(nar_message)[:120]}")

            act_events = sse(client, f"{API}/api/roleplay/characters/{char_id}/chat/send",
                             {"message": "/act 我推门进去，同时拔剑"})
            act_types = [e.get("type") for e in act_events]
            check("command /act streams", "delta" in act_types and "done" in act_types,
                  f"types={act_types}")
            chat = client.get(f"{API}/api/roleplay/characters/{char_id}/chat").json()
            user_messages = [m for m in chat.get("messages", []) if m.get("role") == "user"]
            check("action kind persisted",
                  bool(user_messages) and user_messages[-1].get("kind") == "action",
                  f"last_user_kind={user_messages[-1].get('kind') if user_messages else None}")

            world_payload = json.dumps({"entries": [
                {"uid": 1, "key": ["客栈"], "content": "客栈是江湖消息集散地。", "comment": "客栈"},
            ]}, ensure_ascii=False).encode()
            world_import = client.post(
                f"{API}/api/roleplay/characters/{char_id}/worldinfo/import",
                json={"filename": "world.json",
                      "data_base64": base64.b64encode(world_payload).decode()},
            )
            world_body = world_import.json() if world_import.status_code == 200 else {}
            check("worldinfo import", world_import.status_code == 200 and world_body.get("imported") == 1,
                  f"status={world_import.status_code}")
            world_list = client.get(f"{API}/api/roleplay/characters/{char_id}/worldinfo").json()
            check("worldinfo list", world_list.get("total") == 1, f"total={world_list.get('total')}")

            note = client.post(f"{API}/api/roleplay/characters/{char_id}/chat/send",
                               json={"message": "/note 保持克制"})
            check("command /note", note.status_code == 200, f"status={note.status_code}")
            settings_after = client.get(f"{API}/api/roleplay/settings").json()["settings"]
            check("authors note saved", settings_after.get("authors_note") == "保持克制",
                  f"note={settings_after.get('authors_note')!r}")

            swipe_events = sse(client, f"{API}/api/roleplay/characters/{char_id}/chat/swipe",
                               {"direction": "new"})
            check("swipe new", any(e.get("type") == "swipe" and e.get("total", 0) >= 2
                                   for e in swipe_events),
                  f"swipe={[e for e in swipe_events if e.get('type') == 'swipe']}")

            imp_events = sse(client, f"{API}/api/roleplay/characters/{char_id}/chat/send",
                             {"message": "/impersonate"})
            imp_text = next((e.get("text") for e in imp_events if e.get("type") == "impersonate"), "")
            check("command /impersonate", bool(imp_text), f"text={imp_text!r}")

        settings = client.put(
            f"{API}/api/roleplay/settings",
            json={"user_name": "阿明", "temperature": 1.1, "context_messages": 20},
        )
        body = settings.json()
        check("settings update",
              settings.status_code == 200 and body["settings"]["user_name"] == "阿明",
              str(body)[:120])

        # Optional: verify AI card export from a novel when the server-side store
        # directory is known (local verification only).
        novels_dir = os.getenv("NOVELGEN_NOVELS_DIR")
        if novels_dir:
            novel_dir = os.path.join(novels_dir, "novel-ai-live")
            os.makedirs(novel_dir, exist_ok=True)
            with open(os.path.join(novel_dir, "plan.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "title": "剑气长歌",
                    "genre": "玄幻",
                    "worldbuilding": {"era": "古代", "core_conflict": "人族与妖族之争"},
                    "characters": {
                        "protagonist": {
                            "name": "顾尘", "identity": "落魄剑客", "appearance": "白衣",
                            "backstory": "师门覆灭", "personality": {"surface": "冷淡", "true_self": "重情"},
                            "motivation": {"want": "复仇", "need": "放下"},
                        },
                        "supporting": [{"name": "苏晚", "identity": "医女", "personality": "温柔"}],
                    },
                }, f, ensure_ascii=False)
            with open(os.path.join(novel_dir, "character_bible.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "protagonist": {"name": "顾尘", "identity": "落魄剑客", "catchphrase": "剑在人在"},
                    "supporting": [{"name": "苏晚", "identity": "医女"}],
                    "relationship_map": {"顾尘": ["苏晚：旧识"]},
                }, f, ensure_ascii=False)
            # Remove artifacts from earlier runs so the export really creates cards.
            for existing in client.get(f"{API}/api/roleplay/characters").json().get("characters", []):
                if existing.get("source") == "novel" and existing.get("novel_id") == "novel-ai-live":
                    client.delete(f"{API}/api/roleplay/characters/{existing['id']}")
            response = client.post(
                f"{API}/api/roleplay/characters/from-novel/novel-ai-live",
                json={"mode": "ai"},
            )
            data = response.json() if response.status_code == 200 else {}
            cards = data.get("characters") or []
            check("AI export from novel", response.status_code == 200 and data.get("ai_filled", 0) >= 1,
                  f"status={response.status_code} created={data.get('created')} ai_filled={data.get('ai_filled')}")
            ai_card = next((c for c in cards if c.get("ai_filled")), None)
            check("AI card has generated fields",
                  bool(ai_card) and "AI 根据小说设定" in ai_card.get("description", "")
                  and bool(ai_card.get("first_mes")),
                  str(ai_card)[:180] if ai_card else "no ai card")

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {failures}")
        return 1
    print("ALL LIVE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
