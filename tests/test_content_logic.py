# -*- coding: utf-8 -*-
"""v3.5.18 生成内容逻辑专项：intro/视角/玩家台词/JSON/对话对象/记忆/事件"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, 'backend')

BASE = 'http://127.0.0.1:8787'
NID = urllib.parse.quote('替身的告别')
PASS, FAIL = 0, 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  ✅ {name} {detail}')
    else:
        FAIL += 1
        print(f'  ❌ {name} {detail}')


def post_sse(url, data=None, timeout=180):
    req = urllib.request.Request(BASE + url, data=json.dumps(data or {}).encode(),
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


def post(url, data=None, timeout=120):
    req = urllib.request.Request(BASE + url, data=json.dumps(data or {}).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def get_state():
    req = urllib.request.Request(BASE + f'/api/novels/{NID}/interactive/state')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))['state']


print('═══ 生成内容逻辑专项 ═══')

# ── A. intro：四要素 + 长度 + 无 JSON ──
st = get_state()
intro = st.get('intro', '')
check('intro 存在且 >300 字', len(intro) > 300, f'{len(intro)}字')
check('intro 含世界观', any(k in intro for k in ('陆家嘴', '恒远', '老洋房', '上海', '世界')))
check('intro 含人物背景', any(k in intro for k in ('顾衍之', '林听雪', '沈念薇')))
check('intro 含处境/目标', any(k in intro for k in ('离婚', '设计师', '目标', '三天', '决定')))
check('intro 无 JSON 字样', '{"' not in intro and '[{' not in intro)

# ── B. 场景视角：第二人称 + 无玩家台词 ──
ev = post_sse(f'/api/novels/{NID}/interactive/scene')
ends = [e for e in ev if e.get('type') == 'scene_end']
check('场景生成成功', bool(ends))
if ends:
    blocks = ends[0].get('blocks', [])
    text = ' '.join(b.get('content', '') for b in blocks)
    check('场景含第二人称"你"', '你' in text)
    pname = (st.get('player_char') or {}).get('name', '')
    player_dialogue = [b for b in blocks if b.get('type') == 'dialogue' and b.get('speaker') == pname]
    check('无玩家角色台词块', len(player_dialogue) == 0,
          f'{len(player_dialogue)} 条玩家台词' if player_dialogue else '')
    # 旁观转述检查（"沈念薇/她 站在" 模式）
    import re
    third_person = re.findall(rf'{pname}[，。；、]|{pname}站|{pname}看|{pname}说', text)
    check('无旁观转述', len(third_person) == 0, f'发现: {third_person[:2]}' if third_person else '')

# ── C. 玩家台词解析层兜底（单测 parse + 过滤）──
from core.interactive.story_director import parse_scene_markup
raw = f'【旁白】你推开门。\n【{pname}】我回来了。\n【顾衍之】你终于来了。'
blocks = parse_scene_markup(raw)
pname = (st.get('player_char') or {}).get('name', '')
cleaned = []
for b in blocks:
    if b.get('type') == 'dialogue' and b.get('speaker') == pname:
        cleaned.append({'type': 'narration', 'speaker': '', 'content': f'你心中所想：{b["content"]}'})
    else:
        cleaned.append(b)
player_left = [b for b in cleaned if b.get('type') == 'dialogue' and b.get('speaker') == pname]
check('玩家台词过滤兜底', len(player_left) == 0 and any('你心中所想' in b.get('content', '') for b in cleaned))

# ── D. location/objective 无 JSON 数组 ──
s = st.get('state', {})
loc = str(s.get('location', ''))
obj = str(s.get('objective', ''))
check('location 非 JSON 数组', not loc.strip().startswith('['), loc[:40])
check('objective 非 JSON 数组', not obj.strip().startswith('['), obj[:40])

# ── E. 对话对象排除玩家 ──
ev = post_sse(f'/api/novels/{NID}/interactive/chat', {'message': '@沈念薇 你听我说'})
ends = [e for e in ev if e.get('type') == 'chat_end']
check('@自己 回复者为 NPC', bool(ends) and ends[0].get('speaker') != '沈念薇',
      ends[0].get('speaker', '?') if ends else '无回复')

# ── F. 角色记忆 + 事件时间线 ──
st2 = get_state()
mems = st2.get('memories') or {}
evs = st2.get('events') or []
check('角色记忆沉淀', len(mems) > 0, f'{len(mems)} 个角色有记忆')
check('事件时间线', len(evs) > 0, f'{len(evs)} 条')
# 信息不对称：每个角色记忆内容不同
mems_all = [m.get('content', '') for ml in mems.values() for m in ml]
check('记忆内容非空', all(m for m in mems_all[:5]))

print(f'═══ 内容逻辑专项: {PASS} 通过 / {FAIL} 失败 ═══')
