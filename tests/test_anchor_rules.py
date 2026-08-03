# -*- coding: utf-8 -*-
"""锚点式剧情控制 P0 纯规则测试（零 LLM，离线秒级）

运行: python tests/test_anchor_rules.py

覆盖（v1.1 方案）：
1. 张力更新 tension_update —— 偏离 +2 / 中性 +1 / 推进 -1 / clamp 0-10
2. 切章判定 chapter_complete —— 锚点全 done 优先 / 场景数兜底 / 无 beats 兼容
3. L0 保障（里程碑链 flag 检测 + 跨章张力不清零 + 健康度对账）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from core.interactive.story_director import (
    tension_update,
    chapter_complete,
    mainline_check,
    anchor_trigger_check,
    location_valid,
    time_valid,
    consistency_repair,
    append_change,
    mainline_pressure,
    side_event_hint,
    StoryDirector,
)

PASS, FAIL = 0, 0
def check(name, cond, detail=''):
    global PASS, FAIL
    if cond: PASS += 1; print(f'  OK {name} {detail}')
    else: FAIL += 1; print(f'  XX {name} {detail}')

# ═══════════════ 1. 张力更新（tension_update） ═══════════════
print('▶ 张力更新 tension_update')
check('偏离 drift: 0→2', tension_update(0, 'drift') == 2, f'got {tension_update(0, "drift")}')
check('中性 neutral: 0→1', tension_update(0, 'neutral') == 1)
check('推进 progress: 3→2', tension_update(3, 'progress') == 2)
check('推进下限 clamp: 0 不为负', tension_update(0, 'progress') == 0)
check('偏离上限 clamp: 9→10', tension_update(9, 'drift') == 10)
check('偏离上限 clamp: 10 不越界', tension_update(10, 'drift') == 10)
check('未知模式按中性处理', tension_update(2, '???') == 3)
check('阈值判定: tension>=3 为高位', tension_update(2, 'drift') >= 3)

# ═══════════════ 2. 切章判定（chapter_complete） ═══════════════
print('▶ 切章判定 chapter_complete')

def mk_state(beats=None, scene_in_chapter=0):
    """构造最小 state fixture"""
    st = {'outline_progress': {'idx': 0, 'scene_in_chapter': scene_in_chapter}}
    if beats is not None:
        st['chapter_beats'] = {'chapter_idx': 0, 'beats': beats}
    return st

def beat(status, bid=1):
    return {'id': bid, 'desc': f'事件{bid}', 'status': status}

# 2.1 有 beats：全部 done → 切章
st = mk_state([beat('done'), beat('done'), beat('done')], scene_in_chapter=2)
check('beats 全 done → 切章', chapter_complete(st) is True)

# 2.2 有 beats：存在 current/pending → 不切（即使场景数达标）
st = mk_state([beat('done'), beat('current'), beat('pending')], scene_in_chapter=9)
check('beats 未全 done → 不切（场景数不越权）', chapter_complete(st) is False)

# 2.3 无 beats（旧存档）→ 场景数兜底
st = mk_state(None, scene_in_chapter=3)
check('无 beats 场景数兜底: 3 场景 → 切', chapter_complete(st) is True)
st = mk_state(None, scene_in_chapter=1)
check('无 beats 场景数不足 → 不切', chapter_complete(st) is False)

# 2.4 无 beats 且无 outline_progress（残缺存档）→ 不炸
st = {}
check('残缺存档 → 不切且不抛异常', chapter_complete(st) is False)

# 2.5 beats 与章节不符（chapter_idx 不同）→ 视为无 beats，场景数兜底
st = {'chapter_beats': {'chapter_idx': 1, 'beats': [beat('done')]},
      'outline_progress': {'idx': 0, 'scene_in_chapter': 3}}
check('beats 章节不符 → 场景数兜底', chapter_complete(st) is True)

# ═══════════════ 3. L0 保障（mainline_check） ═══════════════
print('▶ L0 保障 mainline_check')

def mk_mainline(required, acquired, chapter_idx=2, expected=2):
    return {'mainline': {'required_flags': required, 'acquired': acquired},
            'outline_progress': {'idx': chapter_idx, 'expected_by_chapter': expected}}

# 3.1 进度达标 → 无捷径
st = mk_mainline(['信物', '真相', '决战'], ['信物', '真相'], chapter_idx=2, expected=2)
r = mainline_check(st)
check('进度达标 → 不补捷径', r.get('shortcut') is False, f'gap={r.get("gap")}')

# 3.2 进度落后 → 补捷径
st = mk_mainline(['信物', '真相', '决战'], ['信物'], chapter_idx=2, expected=2)
r = mainline_check(st)
check('进度落后 → 注入捷径', r.get('shortcut') is True and r.get('gap') == 1)

# 3.3 落后多个 flag → gap 数正确
st = mk_mainline(['信物', '真相', '决战'], [], chapter_idx=3, expected=3)
r = mainline_check(st)
check('落后 3 flag → gap=3', r.get('gap') == 3 and r.get('shortcut') is True)

# 3.4 无 mainline 配置（旧存档）→ 不炸、不补捷径
st = {'outline_progress': {'idx': 1}}
r = mainline_check(st)
check('无 mainline 配置 → 不炸不补', r.get('shortcut') is False)

# ═══════════════ 4. P1 prompt 构造（_build_scene_prompt） ═══════════════
print('▶ P1 prompt 构造 _build_scene_prompt')

def mk_prompt_state(tension=0, beats_status=None, has_action=False):
    """构造场景 prompt 测试用最小 state"""
    st = {
        'title': '测试小说', 'genre': '都市', 'style': '细腻',
        'state': {'location': '咖啡馆', 'objective': '查明真相'},
        'outline_chapters': [{'number': 1, 'title': '第一章', 'summary': '咖啡馆相遇',
                              'volume': '', 'target_words': 3000}],
        'outline_progress': {'idx': 0},
        'tension': tension,
        'player_state': {'location': '咖啡馆', 'time': '夜晚'},
        'casts': {'林晚': {'profile': {}}},
        'cast_states': {'林晚': {'location': '咖啡馆', 'mood': '平静', 'stance': '合作'}},
        'facts': [], 'flags': [], 'npc_relations': {}, 'pending_missing_hooks': [],
        'summary': '上一场景结尾：林晚约你在咖啡馆见面。',
        'scene_num': 2,
    }
    if beats_status is not None:
        st['chapter_beats'] = {'chapter_idx': 0, 'beats': [
            {'id': i + 1, 'desc': f'事件{i + 1}', 'status': s}
            for i, s in enumerate(beats_status)]}
    if has_action:
        st['last_action'] = {'type': 'move', 'summary': '你起身走向窗边', 'ts': '12:00'}
    return st

from unittest.mock import MagicMock
sd = StoryDirector(MagicMock(), 'test-model', MagicMock())

# 4.1 已删除硬约束段
p = sd._build_scene_prompt(mk_prompt_state(), '')
check('prompt 不含"必须持续推进"', '必须持续推进' not in p)
check('prompt 不含"不改变主线方向"', '不改变主线方向' not in p)
check('prompt 不含"只占本段开头（1-2 句）"', '1-2 句' not in p)

# 4.2 已注入软引导段
p = sd._build_scene_prompt(mk_prompt_state(), '')
check('prompt 含"剧情引导"', '剧情引导' in p)
check('prompt 含"本章终点"', '本章终点' in p)
check('prompt 含引导原则"塑造过程"', '塑造过程' in p)

# 4.3 张力差异注入：高位含世界推力，低位不含
p_low = sd._build_scene_prompt(mk_prompt_state(tension=1), '')
p_high = sd._build_scene_prompt(mk_prompt_state(tension=5), '')
check('高位注入"剧情张力: 5/10"', '剧情张力: 5/10' in p_high)
check('高位注入世界推力', '世界推力' in p_high)
check('低位注入"剧情张力: 1/10"', '剧情张力: 1/10' in p_low)
check('低位无世界推力', '世界推力' not in p_low)

# 4.4 拒绝后果保留（拒绝也是推进）
check('prompt 保留拒绝后果机制', '拒绝' in p and ('后果' in p or '翻脸' in p))

# 4.5 SCENE_SYSTEM 硬回轨段已删除
from core.interactive.story_director import SCENE_SYSTEM
check('SCENE_SYSTEM 不含"回到主线轨道"', '回到主线轨道' not in SCENE_SYSTEM)
check('SCENE_SYSTEM 不含"严禁剧情跟着闲聊"', '严禁剧情跟着闲聊' not in SCENE_SYSTEM)

# ═══════════════ 5. P2 条件检查器（anchor_trigger_check） ═══════════════
print('▶ P2 条件检查器 anchor_trigger_check')

def mk_anchor_state(tension=0, location='青云山', flags=None, relations=None,
                    inventory=None, scene_num=3, timeout_scenes=0, trigger_type='event'):
    """构造锚点触发检查用最小 state"""
    beats = [{
        'id': 1, 'desc': '开篇钩子：老人试探',
        'status': 'current',
        'trigger': {
            'type': trigger_type,
            'conditions': [
                {'field': 'tension', 'op': '>=', 'value': 3},
                {'field': 'flag', 'op': 'has', 'value': '见过神秘老人'},
            ],
            'timeout_scenes': timeout_scenes,
        },
        'entry_hook': '深夜酒馆，老人坐到对面',
    }, {'id': 2, 'desc': '冲突升级', 'status': 'pending',
        'trigger': {'type': 'event', 'conditions': [], 'timeout_scenes': 0}}]
    return {
        'scene_num': scene_num,
        'tension': tension,
        'chapter_beats': {'chapter_idx': 0, 'beats': beats},
        'outline_progress': {'idx': 0, 'scene_start': 1},
        'state': {'location': location, 'flags': flags or [],
                  'relations': relations or {}, 'inventory': inventory or []},
    }

# 5.1 event 型：tension ≥ 阈值 且 flag 满足 → 触发
st = mk_anchor_state(tension=3, flags=['见过神秘老人'])
r = anchor_trigger_check(st)
check('event 型条件满足 → 触发', r is not None and r.get('reason') == 'condition')
check('触发返回 entry_hook', r is not None and '老人坐到对面' in str(r.get('hook', '')))

# 5.2 tension 不足 → 不触发
st = mk_anchor_state(tension=2, flags=['见过神秘老人'])
check('tension 不足 → 不触发', anchor_trigger_check(st) is None)

# 5.3 flag 缺失 → 不触发（AND 语义）
st = mk_anchor_state(tension=5, flags=[])
check('flag 缺失 → 不触发', anchor_trigger_check(st) is None)

# 5.4 scene 型：location 匹配 → 触发（替换默认条件）
st = mk_anchor_state()
st['chapter_beats']['beats'][0]['trigger'] = {
    'type': 'scene',
    'conditions': [{'field': 'location', 'op': '==', 'value': '青云山'}],
    'timeout_scenes': 0}
r = anchor_trigger_check(st)
check('scene 型 location 匹配 → 触发', r is not None)

# 5.5 relations 达标 → 触发
st = mk_anchor_state()
st['chapter_beats']['beats'][0]['trigger'] = {
    'type': 'scene',
    'conditions': [{'field': 'relations', 'op': '>=', 'value': 50, 'target': '老人'}],
    'timeout_scenes': 0}
st['state']['relations'] = {'老人': 80}
check('relations 达标 → 触发', anchor_trigger_check(st) is not None)
st['state']['relations'] = {'老人': 20}
check('relations 不达标 → 不触发', anchor_trigger_check(st) is None)

# 5.6 timeout 兜底：条件不满足但场景数超限 → 强制触发
st = mk_anchor_state(tension=1, flags=[], scene_num=8, timeout_scenes=5)
r = anchor_trigger_check(st)
check('timeout 强制触发', r is not None and r.get('reason') == 'timeout')

# 5.7 未到 timeout → 仍不触发
st = mk_anchor_state(tension=1, flags=[], scene_num=3, timeout_scenes=5)
check('timeout 未到 → 不触发', anchor_trigger_check(st) is None)

# 5.8 只有 current beat 被检查：pending 的条件再满足也不触发
st = mk_anchor_state(tension=3, flags=['见过神秘老人'])
st['chapter_beats']['beats'][0]['status'] = 'done'
st['chapter_beats']['beats'][1]['status'] = 'current'
r = anchor_trigger_check(st)  # current=冲突升级，conditions=[] → 无条件即触发
check('current 无条件 conditions → 视为满足触发', r is not None)

# 5.9 旧存档无 trigger → legacy 无条件触发（保持旧行为不卡死）
st = mk_anchor_state()
st['chapter_beats']['beats'][0].pop('trigger', None)
r = anchor_trigger_check(st)
check('无 trigger → legacy 无条件触发', r is not None and r.get('reason') == 'legacy')

# 5.10 无 beats / 章节不符 → None
st = {'outline_progress': {'idx': 0}, 'chapter_beats': {'chapter_idx': 1, 'beats': []}}
check('beats 章节不符 → None', anchor_trigger_check(st) is None)
check('残缺 state → 不抛异常', anchor_trigger_check({}) is None)

# ═══════════════ 6. P3 状态防线（L2/L3/L1） ═══════════════
print('▶ P3 状态防线')

# 6.1 地点可达性 location_valid
check('同地点 → 无变化(False)', location_valid('咖啡馆', '咖啡馆', ['咖啡馆']) is False)
check('已知地点 → 可达', location_valid('咖啡馆', '酒楼', ['咖啡馆', '酒楼']) is True)
check('新地点 → 允许探索', location_valid('咖啡馆', '码头', ['咖啡馆']) is True)
check('空 target → 拒绝', location_valid('咖啡馆', '', ['咖啡馆']) is False)
check('空 current → 拒绝', location_valid('', '码头', ['咖啡馆']) is False)

# 6.2 时间单调 time_valid
check('时间同档 → OK', time_valid('夜晚', '夜晚') is True)
check('时间顺延 → OK', time_valid('上午', '清晨') is True)
check('时间倒退 → 拒绝', time_valid('清晨', '夜晚') is False)
check('未知时间文本 → 放行', time_valid('午夜子时', '夜晚') is True)
check('空时间 → 放行', time_valid('', '夜晚') is True)

# 6.3 一致性修复 consistency_repair
st = {
    'state': {'location': '码头', 'flags': ['a', 'b', 'a']},
    'player_state': {'location': '酒楼'},
    'cast_states': {'林晚': {'present': True, 'location': '码头'},
                    '顾衍之': {'present': False, 'location': '码头'}},
    'tension': 5,
}
r = consistency_repair(st)
check('主状态 location 对齐 player_state', r['state']['location'] == '酒楼')
check('flags 去重', r['state']['flags'] == ['a', 'b'])
check('在场角色 location 对齐玩家', r['cast_states']['林晚']['location'] == '酒楼')
check('不在场角色 location 不动', r['cast_states']['顾衍之']['location'] == '码头')
check('不炸: 残缺 state', consistency_repair({}) is not None)

# 6.4 变更日志 append_change（L1 审计）
st = {'state': {'location': 'A'}}
append_change(st, {'field': 'location', 'old': 'A', 'new': 'B'}, 'action:move')
check('变更日志记录', len(st.get('change_log', [])) == 1)
check('日志带原因', st['change_log'][0].get('reason') == 'action:move')
append_change(st, {'field': 'tension', 'new': 3}, 'scene')
check('日志追加', len(st.get('change_log', [])) == 2)
append_change(st, {'field': 'x', 'new': 1}, 'scene')
check('日志上限截断(≤50)', len(st.get('change_log', [])) <= 50)

# ═══════════════ 7. P4 进化层（跨章张力/填充事件/捷径注入） ═══════════════
print('▶ P4 进化层')

# 7.1 跨章张力介入 mainline_pressure
check('drift 0 章 → 不介入', mainline_pressure({'tension_drift_chapters': 0}) is None)
check('drift 1 章 → 不介入', mainline_pressure({'tension_drift_chapters': 1}) is None)
r = mainline_pressure({'tension_drift_chapters': 2})
check('drift 2 章 → 介入', r is not None and '主线势力' in r)
check('drift 3 章 → 介入', mainline_pressure({'tension_drift_chapters': 3}) is not None)
check('无字段 → 不介入', mainline_pressure({}) is None)

# 7.2 填充事件 side_event_hint
check('张力 5 未触发 → 无填充', side_event_hint({'tension': 5}) is None)
r = side_event_hint({'tension': 6})
check('张力 6 未触发 → 填充事件', r is not None and '新的事件' in r)
check('张力 6 已触发锚点 → 无填充', side_event_hint({'tension': 6, 'anchor_triggered': {'beat_id': 1}}) is None)
check('张力 10 → 填充', side_event_hint({'tension': 10}) is not None)
check('无张力字段 → 无填充', side_event_hint({}) is None)

# 7.3 prompt 注入断言（跨章施压 + 捷径 + 填充事件）
def mk_p4_state(drift=0, tension=0, shortcut=False):
    st = {
        'title': '测试', 'genre': '都市', 'style': '细腻',
        'state': {'location': '咖啡馆', 'objective': '查明真相'},
        'outline_chapters': [{'number': 1, 'title': '第一章', 'summary': '咖啡馆',
                              'volume': '', 'target_words': 3000}],
        'outline_progress': {'idx': 0},
        'tension': tension,
        'tension_drift_chapters': drift,
        'player_state': {'location': '咖啡馆', 'time': '夜晚'},
        'casts': {'林晚': {'profile': {}}},
        'cast_states': {'林晚': {'location': '咖啡馆', 'mood': '平静', 'stance': '合作'}},
        'facts': [], 'flags': [], 'npc_relations': {}, 'pending_missing_hooks': [],
        'summary': '', 'scene_num': 2,
    }
    if shortcut:
        st['mainline_shortcut'] = True
    return st

p = sd._build_scene_prompt(mk_p4_state(drift=2), '')
check('drift≥2 prompt 注入"主线势力"', '主线势力' in p)
p = sd._build_scene_prompt(mk_p4_state(drift=1), '')
check('drift<2 prompt 无施压', '主线势力' not in p)
p = sd._build_scene_prompt(mk_p4_state(tension=6), '')
check('张力≥6 prompt 注入填充事件', '新的事件' in p)
p = sd._build_scene_prompt(mk_p4_state(tension=4), '')
check('张力<6 prompt 无填充', '新的事件' not in p)
p = sd._build_scene_prompt(mk_p4_state(shortcut=True), '')
check('捷径标记 prompt 注入捷径提示', '捷径' in p)
p = sd._build_scene_prompt(mk_p4_state(shortcut=False), '')
check('无捷径标记 prompt 无捷径', '捷径' not in p)

# ═══════════════ 汇总 ═══════════════
print(f'\n═══ 结果: {PASS} 通过 / {FAIL} 失败 ═══')
sys.exit(0 if FAIL == 0 else 1)
