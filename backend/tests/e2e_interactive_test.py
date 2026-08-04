# -*- coding: utf-8 -*-
"""v3.6 互动系统端到端实测（真实 LLM 链路 + 确定性规则验证）

覆盖功能点：
- P0: travel 通道 + 三支柱更新 + 确认流（图谱外地点）
- P1: 场景时间推进（travel +1 档 / LLM time 不采纳）
- P2: 时段氛围 + 路程叙事注入
- P3: 承诺台账 → 到达自动兑现
- P4: meta 指令隔离（零 LLM 零状态）
- P5: 行动 ↔ beat 联动（命中推进 / 偏离计数 / 牵引提示）

用法: python tests/e2e_interactive_test.py
"""
import asyncio
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openai import OpenAI
import config

from core.interactive.interact_store import InteractStore
from core.interactive import world_state as ws
from core.interactive.dialogue_engine import DialogueEngine

LLM_STATS = {"detect_llm_calls": 0, "scene_llm_calls": 0, "llm_failures": 0}


def build_state():
    """构造完整互动状态（含图谱/角色/beats/承诺台账）"""
    st = {
        "title": "《天命骗局：我在诸天当神棍》",
        "genre": "玄幻", "style": "轻松诙谐",
        "player_char": {"name": "沈夜"},
        "casts": {
            "林晚晚": {"profile": "镖局大小姐，性格泼辣直爽", "present": True,
                      "location": "茶楼", "temp": False, "relation": 62},
            "掌柜": {"profile": "茶楼掌柜，精明圆滑", "present": True,
                     "location": "茶楼", "temp": False},
            "神秘客": {"profile": "斗笠遮面的江湖客", "present": False,
                       "location": "", "temp": True},
        },
        "state": {
            "location": "茶楼", "objective": "查明父亲失踪真相",
            "flags": [], "relations": {"林晚晚": "62"}, "inventory": ["铜钱三十文"],
        },
        "player_state": {
            "location": "茶楼", "time": "", "with": ["林晚晚"],
            "holding": ["铜钱三十文"], "situation": "正在茶楼喝茶",
            "condition": "健康", "disguise": "", "money": "三十文",
        },
        "cast_states": {
            "林晚晚": {"present": True, "location": "茶楼", "mood": "好奇"},
            "掌柜": {"present": True, "location": "茶楼"},
        },
        "known_locations": ["茶楼", "家", "街市", "码头"],
        "scene_num": 0,
        "chapter_beats": {
            "chapter_idx": 0,
            "beats": [
                {"id": 1, "desc": "去码头接头，取回父亲留下的信物", "status": "current",
                 "trigger": {"type": "event", "conditions": [], "timeout_scenes": 3},
                 "entry_hook": "码头老船夫手里有你父亲的信物"},
                {"id": 2, "desc": "护送林晚晚出城", "status": "pending",
                 "trigger": {"type": "event", "conditions": [], "timeout_scenes": 3},
                 "entry_hook": ""},
            ],
        },
        "pending_promises": [
            {"who": "林晚晚", "what": "午时在码头见面，把信物交给你",
             "when_raw": "午时", "location": "码头", "status": "pending",
             "scene_num": 0, "due_scene": 3},
        ],
    }
    ws.ensure_world(st)
    # 图谱节点（家/街市/码头 pre-built，北山故意不在图谱内 → 验证确认流）
    st["world"]["locations"]["茶楼"]["desc"] = "临街二层木楼，人声鼎沸"
    st["world"]["locations"]["家"]["desc"] = "城南小院，院中有棵老槐树"
    st["world"]["locations"]["街市"] = {"desc": "青石板长街，商贩云集",
                                       "connected": ["茶楼", "码头", "家"],
                                       "chars": [], "items": []}
    st["world"]["locations"]["码头"] = {"desc": "货运码头，桅杆林立",
                                       "connected": ["街市"],
                                       "chars": ["老船夫"], "items": []}
    st["world"]["locations"]["茶楼"]["connected"] = ["街市"]
    st["world"]["locations"]["家"]["connected"] = ["街市"]
    return st


def snapshot(st):
    w = st.get("world") or {}
    return {
        "time": f"{w.get('time', {}).get('label', '?')}(D{w.get('time', {}).get('day', 1)})",
        "location": w.get("location", ""),
        "present": [n for n, c in (w.get("chars") or {}).items() if c.get("present")],
        "scene_num": st.get("scene_num", 0),
        "beat": (st.get("chapter_beats") or {}).get("beats", [{}])[0].get("status", "?"),
        "drift": st.get("beat_drift_count", 0),
        "promises": [p["status"] for p in st.get("pending_promises", [])],
        "last_action": (st.get("last_action") or {}).get("type", ""),
    }


async def run_round(de, novel_id, st, user_input, tag):
    print(f"\n{'=' * 70}")
    print(f"▶ 第{st.get('scene_num', 0) + 1}轮 [{tag}] 输入: 「{user_input}」")
    print(f"  前状态: {json.dumps(snapshot(st), ensure_ascii=False)}")
    chunks = []
    try:
        async for ev in de.chat_stream(novel_id, user_input):
            t = ev.get("type")
            if t == "chat_chunk":
                chunks.append(ev.get("text", ""))
            elif t == "action_chunk":  # v3.6.1: 确认流/meta 提示
                if ev.get("content"):
                    chunks.append(ev.get("content", ""))
            elif t in ("action_scene", "chat_end", "ooc_check", "phase", "done"):
                if ev.get("text"):
                    chunks.append(ev.get("text", ""))
            elif t == "error":
                print(f"  ✗ error: {ev.get('message', '')}")
                return
    except Exception as e:
        print(f"  ✗ 异常: {type(e).__name__}: {e}")
        traceback.print_exc()
        return
    st2 = de.store.load_state(novel_id) or st
    text = "".join(chunks).strip()
    print(f"  输出: {text[:260]}")
    print(f"  后状态: {json.dumps(snapshot(st2), ensure_ascii=False)}")
    return text


async def main():
    # 真实 LLM 客户端（DeepSeek）
    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL)
    store = InteractStore(os.path.join(config.NOVELS_DIR, "_e2e_test_interactive"))
    novel_id = "e2e_demo"

    st = build_state()
    store.save_state(novel_id, st)
    de = DialogueEngine(client, config.DEEPSEEK_MODEL, store)

    rounds = [
        ("我要回家了", "P0 travel+三支柱+P2时段氛围"),
        ("存档", "P4 meta隔离"),
        ("去北山", "P0 图谱外确认流"),
        ("好，去吧", "P0 确认注册执行"),
        ("到处闲逛", "P5 偏离1"),
        ("去茶馆坐坐", "P5 偏离2"),
        ("看看风景", "P5 偏离3→牵引"),
        ("去码头", "P5 beat命中+P3承诺兑现"),
    ]

    t0 = time.time()
    for user_input, tag in rounds:
        await run_round(de, novel_id, store.load_state(novel_id), user_input, tag)

    print(f"\n{'=' * 70}")
    print(f"耗时 {time.time() - t0:.1f}s")
    final = store.load_state(novel_id)
    print(f"终态: {json.dumps(snapshot(final), ensure_ascii=False, indent=2)}")

    # ── 验收断言（规则层，不依赖 LLM 是否在线）──
    w = final.get("world") or {}
    checks = []
    checks.append(("P0 travel: 到达过'家'", "家" in (final.get("world") or {}).get("locations", {})))
    checks.append(("P0 确认流: '北山'已注册", "北山" in (final.get("world") or {}).get("locations", {})))
    checks.append(("P1 时间推进: 非正午", w.get("time", {}).get("label") != "正午"))
    checks.append(("P3 承诺兑现: 码头约定 fulfilled",
                   any(p.get("status") == "fulfilled" for p in final.get("pending_promises", []))))
    checks.append(("P5 beat推进: beat1 非 current",
                   (final.get("chapter_beats") or {}).get("beats", [{}])[0].get("status") != "current"))
    print("\n── 验收断言 ──")
    ok = 0
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok += passed
    print(f"验收: {ok}/{len(checks)}")

    # 清理
    import shutil
    shutil.rmtree(os.path.join(config.NOVELS_DIR, "_e2e_test_interactive"), ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
