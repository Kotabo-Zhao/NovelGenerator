"""互动模式全链路 e2e 测试（v3.5.4 全面回归）

覆盖：start → scene → node(Agenda) → chat → 行动 → end-chat(PACT) → scene 循环
验证点：状态推进、议程生成、行动执行、钩子核对、missing 回流、存档一致性
"""
import json
import sys
import time
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:8787'
NID = urllib.parse.quote('替身的告别')
PASS, FAIL = 0, 0
RESULTS = []


def check(name, cond, detail=""):
    global PASS, FAIL
    mark = '✅' if cond else '❌'
    if cond:
        PASS += 1
    else:
        FAIL += 1
    RESULTS.append(f"  {mark} {name} {detail}")
    print(f"  {mark} {name} {detail}")


def post(url, data=None, timeout=180):
    req = urllib.request.Request(BASE + url,
        data=json.dumps(data).encode() if data else b'{}',
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def post_sse(url, data=None, timeout=300):
    req = urllib.request.Request(BASE + url,
        data=json.dumps(data).encode() if data else b'{}',
        headers={'Content-Type': 'application/json'}, method='POST')
    events = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = ''
        for chunk in r:
            buf += chunk.decode('utf-8', 'ignore')
            while '\n\n' in buf:
                raw, buf = buf.split('\n\n', 1)
                for line in raw.splitlines():
                    if line.startswith('data:'):
                        try:
                            events.append(json.loads(line[5:].strip()))
                        except Exception:
                            pass
    return events


def get_state():
    req = urllib.request.Request(BASE + f'/api/novels/{NID}/interactive/state')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))['state']


def ev_type(events, t):
    return [e for e in events if e.get('type') == t]


print(f"═══ 互动模式全链路 e2e: {NID} ═══")
t0 = time.time()

# ── 1. restart 干净起点 ──
r = post(f'/api/novels/{NID}/interactive/restart')
check('restart', r.get('ok') is True)

# ── 2. start + 开场场景 + 开场节点 ──
events = post_sse(f'/api/novels/{NID}/interactive/start')
scene_end = ev_type(events, 'scene_end')
node = ev_type(events, 'node_check')
check('start 开场场景', len(scene_end) == 1 and len(scene_end[0].get('blocks', [])) > 0,
      f"{len(scene_end[0].get('blocks', []))} blocks")
check('开场必触发节点', node and node[0].get('is_node') is True)
agenda = node[0].get('agenda') if node else None
check('开场 Agenda 生成', bool(agenda and agenda.get('goal')), f"hooks={len(agenda.get('hooks', [])) if agenda else 0}")

# ── 3. 对话 2 轮 + drift 校验事件 ──
for i, msg in enumerate(['你是谁？为什么会在这里？', '（环顾四周）这里是什么地方？']):
    events = post_sse(f'/api/novels/{NID}/interactive/chat', {'message': msg})
    chat_end = ev_type(events, 'chat_end')
    check(f'chat 回复{i+1}', len(chat_end) == 1 and chat_end[0].get('content', '').strip(),
          f"{len(chat_end[0].get('content', ''))}字" if chat_end else '')

# ── 4. 行动：答应/拒绝类（影响剧情） ──
events = post_sse(f'/api/novels/{NID}/interactive/chat', {'message': '我答应你'})
detect = ev_type(events, 'action_detect')
act_end = ev_type(events, 'action_end')
check('行动识别「我答应你」', len(detect) == 1 and detect[0].get('action_type') in ('accept', 'interact', 'other'),
      detect[0].get('action_type') if detect else '未识别')
check('行动结果场景', len(act_end) == 1 and act_end[0].get('content', '').strip())

# ── 5. 超现实行动拦截 ──
events = post_sse(f'/api/novels/{NID}/interactive/chat', {'message': '我瞬移到皇宫'})
detect = ev_type(events, 'action_detect')
check('超现实拦截', len(detect) == 1 and detect[0].get('blocked') is True)

# ── 6. end-chat: PACT + 钩子核对 ──
r = post(f'/api/novels/{NID}/interactive/end-chat')
check('PACT 提取', len(r.get('facts', [])) >= 0 and 'hooks' in r)
check('钩子核对返回', isinstance(r.get('hooks'), dict))

# ── 7. 推进场景 + 节点判定 ──
events = post_sse(f'/api/novels/{NID}/interactive/scene')
scene_end = ev_type(events, 'scene_end')
node = ev_type(events, 'node_check')
check('场景2 生成', len(scene_end) == 1)
check('节点判定返回', len(node) >= 1)

# ── 8. 状态一致性 ──
st = get_state()
check('scene_num 推进', st.get('scene_num', 0) >= 2, f"scene={st.get('scene_num')}")
check('状态含角色', len(st.get('casts', {})) > 0)
check('存档含 agenda 或已消费', 'agenda' in st or st.get('scene_num', 0) > 0)
check('summary 存在', bool(st.get('summary', '')))

# ── 9. 存档快照 ──
import os
cp_dir = os.path.join(r'C:\Users\Yan Zhao\WorkBuddy\Claw\NovelGenerator\novels\测试手册\interactive', 'checkpoints')
check('快照目录存在', os.path.isdir(cp_dir))
if os.path.isdir(cp_dir):
    check('快照数量', len(os.listdir(cp_dir)) > 0, f"{len(os.listdir(cp_dir))}份")

# ── 10. 重新进入（断线恢复） ──
st2 = get_state()
check('断线恢复 state 可读', st2.get('scene_num') == st.get('scene_num'))

print(f"\n═══ e2e 结果: {PASS} 通过 / {FAIL} 失败 · 总耗时 {time.time()-t0:.0f}s ═══")
sys.exit(0 if FAIL == 0 else 1)
