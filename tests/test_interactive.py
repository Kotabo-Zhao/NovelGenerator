"""互动模式端到端回归测试（v3.0）

运行: python tests/test_interactive.py [novel_id]
覆盖: restart → start → chat ×2 → end-chat(PACT) → scene → 性能计时
"""
import json
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8787"
NID = urllib.parse.quote(sys.argv[1] if len(sys.argv) > 1 else "替身的告别")

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    mark = "✅" if cond else "❌"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"  {mark} {name} {detail}")


def post(url, data=None):
    req = urllib.request.Request(
        BASE + url,
        data=json.dumps(data).encode() if data else b"{}",
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_sse(url, data=None):
    req = urllib.request.Request(
        BASE + url,
        data=json.dumps(data).encode() if data else b"{}",
        headers={"Content-Type": "application/json"})
    events = []
    with urllib.request.urlopen(req, timeout=180) as resp:
        for line in resp:
            line = line.decode("utf-8", "replace").strip()
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except Exception:
                    pass
    return events


def get(url):
    with urllib.request.urlopen(BASE + url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print(f"═══ 互动模式回归测试: {urllib.parse.unquote(NID)} ═══")
    t_all = time.time()

    # 0. restart
    print("▶ restart")
    r = post(f"/api/novels/{NID}/interactive/restart")
    check("restart ok", r.get("ok") is True)

    # 1. start
    print("▶ start")
    t0 = time.time()
    evs = post_sse(f"/api/novels/{NID}/interactive/start")
    dur = time.time() - t0
    text1 = "".join(e.get("content", "") for e in evs if e.get("type") == "scene_chunk")
    node1 = [e for e in evs if e.get("type") == "node_check"]
    check("start 场景生成", len(text1) > 200, f"({len(text1)}字, {dur:.1f}s)")
    check("开场必触发节点", node1 and node1[-1].get("is_node") is True)
    check("性能: 场景生成 <20s(含冷启动)", dur < 20, f"{dur:.1f}s")

    # 2. chat ×2
    print("▶ chat")
    t0 = time.time()
    evs = post_sse(f"/api/novels/{NID}/interactive/chat", {"message": "我们重新开始，好不好？"})
    r1 = "".join(e.get("content", "") for e in evs if e.get("type") == "chat_chunk")
    d1 = time.time() - t0
    check("chat 回复非空", len(r1) > 20, f"({d1:.1f}s)")
    check("性能: 对话 <10s", d1 < 10, f"{d1:.1f}s")
    t0 = time.time()
    evs = post_sse(f"/api/novels/{NID}/interactive/chat", {"message": "当年的事，我可以解释。"})
    r2 = "".join(e.get("content", "") for e in evs if e.get("type") == "chat_chunk")
    d2 = time.time() - t0
    check("chat 回复2 非空", len(r2) > 20, f"({d2:.1f}s)")

    # 3. end-chat PACT
    print("▶ end-chat (PACT)")
    t0 = time.time()
    r = post(f"/api/novels/{NID}/interactive/end-chat")
    facts = r.get("facts", [])
    check("PACT 提取 facts", len(facts) >= 1, f"{len(facts)}条")
    check("性能: PACT <10s", time.time() - t0 < 10, f"{time.time()-t0:.1f}s")
    # 去重校验
    contents = [f.get("content", "")[:40] for f in facts]
    check("facts 无重复", len(contents) == len(set(contents)))

    # 4. scene
    print("▶ scene")
    t0 = time.time()
    evs = post_sse(f"/api/novels/{NID}/interactive/scene")
    dur = time.time() - t0
    text2 = "".join(e.get("content", "") for e in evs if e.get("type") == "scene_chunk")
    node2 = [e for e in evs if e.get("type") == "node_check"]
    check("场景2 生成", len(text2) > 200, f"({len(text2)}字, {dur:.1f}s)")
    check("性能: 场景 <15s", dur < 15, f"{dur:.1f}s")
    check("节点判定返回", len(node2) > 0)

    # 5. state
    print("▶ state")
    st = get(f"/api/novels/{NID}/interactive/state")
    check("state 可读", st.get("ok") is True)
    s = st.get("state", {})
    check("scene_num 已推进", s.get("scene_num", 0) >= 2)
    check("casts 有角色", len(s.get("casts") or {}) > 0)

    # 6. voices
    print("▶ voices")
    v = get(f"/api/novels/{NID}/interactive/voices")
    check("voices 可读", v.get("ok") is True and len(v.get("voices") or {}) > 0)

    print(f"\n═══ 结果: {PASS} 通过 / {FAIL} 失败 · 总耗时 {time.time()-t_all:.1f}s ═══")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
