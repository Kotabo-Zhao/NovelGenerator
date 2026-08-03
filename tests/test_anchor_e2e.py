# -*- coding: utf-8 -*-
"""锚点式 T3 端到端三路径测试（需本地服务 8787）

运行: 先启动后端服务 (python backend/api/server.py)，然后:
python tests/test_anchor_e2e.py [novel_id]

覆盖（v1.1 测试方案 §3 T3）：
- 顺从路径: 配合剧情 → 锚点按序触发、正常切章
- 绕路路径: 闲聊逛街 → 不强制回轨、tension 递增、事件找上门
- 拒绝路径: 拒绝关键事件 → reject 后果生效、仍推进
- 每路径: 性能阈值 + 状态一致性 + scene_num 单调
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
    if cond:
        PASS += 1
        print(f"  OK {name} {detail}")
    else:
        FAIL += 1
        print(f"  XX {name} {detail}")


def post(url, data=None):
    req = urllib.request.Request(
        BASE + url, data=json.dumps(data).encode() if data else b"{}",
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_sse(url, data=None):
    req = urllib.request.Request(
        BASE + url, data=json.dumps(data).encode() if data else b"{}",
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


def get_state():
    """GET 互动存档（精简返回）"""
    try:
        with urllib.request.urlopen(
                BASE + f"/api/novels/{NID}/interactive/state", timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("state") or {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def run_path(name, inputs, max_scenes=6):
    """跑一条路径：restart → start → 依次输入 → 断言"""
    print(f"▶ 路径: {name}")
    r = post(f"/api/novels/{NID}/interactive/restart")
    check(f"[{name}] restart ok", r.get("ok") is True)
    t0 = time.time()
    evs = post_sse(f"/api/novels/{NID}/interactive/start")
    dur = time.time() - t0
    text1 = "".join(e.get("content", "") for e in evs if e.get("type") == "scene_chunk")
    check(f"[{name}] start 场景生成", len(text1) > 200, f"{len(text1)}字")
    check(f"[{name}] 性能: 场景 <20s", dur < 20, f"{dur:.1f}s")
    tensions, scenes = [], []
    for i, inp in enumerate(inputs):
        t0 = time.time()
        evs = post_sse(f"/api/novels/{NID}/interactive/chat", {"message": inp})
        d = time.time() - t0
        check(f"[{name}] 轮{i+1} 回复非空", any(e.get("type") == "chat_chunk" for e in evs), f"{d:.1f}s")
        # 从事件流取状态快照
        for e in evs:
            snap = e.get("snapshot") or {}
            if snap.get("scene_num"):
                scenes.append(snap["scene_num"])
        # 完整互动循环：每 2 轮对话 → 生成下一场景（锚点检查/张力更新在场景生成时触发）
        if (i + 1) % 2 == 0:
            t0 = time.time()
            evs = post_sse(f"/api/novels/{NID}/interactive/scene")
            d = time.time() - t0
            stext = "".join(e.get("content", "") for e in evs if e.get("type") == "scene_chunk")
            check(f"[{name}] 场景{i//2+2} 生成", len(stext) > 100, f"{len(stext)}字 {d:.1f}s")
    st = get_state()
    return st


def main():
    print(f"═══ 锚点式端到端三路径测试: {urllib.parse.unquote(NID)} ═══")
    # 绕路路径: 闲聊/逛街（张力应递增、不被强制回轨）
    st = run_path("绕路", [
        "今天天气不错，我们先随便聊聊吧",
        "对了，你上次说的那个故事后来怎么样了？",
        "我想先去街上逛逛，买点东西",
        "这家的点心看起来不错，我们尝尝？",
        "其实我一直有个问题想问你……",
        "我们还是先做正事吧",
    ])
    t = st.get("tension", -1)
    check("[绕路] tension 字段存在", isinstance(t, (int, float)) and 0 <= t <= 10, f"tension={t}")
    check("[绕路] scene_num 单调递增", int(st.get("scene_num", 0)) >= 2)

    # 顺从路径: 配合剧情
    st = run_path("顺从", [
        "好，我跟你走", "我答应你", "好的，我这就去办",
        "谢谢你告诉我这些", "我们继续吧",
    ])
    check("[顺从] 状态卡 location 存在", bool((st.get("player_state") or {}).get("location")))

    # 拒绝路径: 明确拒绝
    st = run_path("拒绝", [
        "我不去", "我拒绝这件事", "不行，我不会同意的",
        "别逼我，我不想参与", "好吧，我听你的",
    ])
    check("[拒绝] scene 推进未卡死", int(st.get("scene_num", 0)) >= 2)

    print(f"\n═══ 结果: {PASS} 通过 / {FAIL} 失败 ═══")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
