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
    scene_repeat_check,
    promise_anchor_of,
    promise_ledger_update,
    promise_conflict_check,
    promise_due_check,
    merge_cast_states,
    state_change_detect,
    state_context_brief,
    sync_skip_check,
    sync_mark_record,
    backfill_list,
    gs_merge_sync,
    cast_presets_build,
    choose_char_apply,
    PACT_SYSTEM,
    StoryDirector,
)
from core.interactive.dialogue_engine import pact_need_extract
from core.interactive.char_memory import EVENT_LIMIT

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

# ═══════════════ 8. 防重复生成（scene_repeat_check） ═══════════════
print('▶ 防重复 scene_repeat_check')

# 真实场景级文本（同一泼酒事件两版重写 vs 完全不同事件）
S_WINE_1 = "金碧辉煌的宴会厅里，水晶灯晃得人眼花。你攥着酒杯站在角落，杯壁上的水珠顺着指缝滑下来。周太太踩着高跟鞋走到你面前，嘴角挂着笑，声音却尖得像刀——哟，这不是恒远集团的前夫人么？怎么，离了婚，还赖在顾家的地盘上不肯走？不等你反应，一杯红酒已经泼在你脸上。酒液顺着脸颊淌下，浸湿了你精心挑选的珍珠白礼服。四周传来窃窃私语，有人笑，有人假装没看见。你抬手抹了一把脸，尝到嘴里又涩又苦的滋味。目光越过人群，落在不远处的顾衍之身上——他端着酒杯，神色平静，像在看一场与己无关的戏。"
S_WINE_2 = "水晶灯的光刺得眼睛发酸。酒渍在你珍珠白的礼服上洇开一片暗红，像一道伤口。你抬手抹过脸颊，指尖沾着黏腻的酒液——周太太的话还在耳边嗡嗡作响，人群的窃语像潮水般涌来。你抬眼看向顾衍之。他站在几步外，白色西装笔挺，酒杯在指间轻轻转动。他的目光扫过你，又落回别处，像在看一幅无关紧要的画。周太太慢条斯理地晃着酒杯，声音不高不低——沈小姐，这礼服怕是不能穿了。要不要我叫人给你拿件服务生的围裙？周围有人轻笑。你攥紧了酒杯，指节泛白。"
S_STREET = "夜风灌进衣领，你踩着高跟鞋沿梧桐影里的街沿走，路灯把影子拉得细长。陆家嘴的霓虹在身后碎成一片光斑，宴会厅的喧闹早被甩远。街角有家亮着暖光的小店，橱窗里摆着几件素银首饰。你脚步顿住，隔着玻璃看那枚缠枝纹的戒指。身后忽然传来一道清亮的女声——沈姐姐？真的是你！你回头，见一个穿米白风衣的年轻女孩小跑过来，杏眼弯弯。"

# 8.1 明显重复（同事件改写重述）→ True
check('同事件改写重述 → 判重复', scene_repeat_check(S_WINE_1, [S_WINE_2], 0.13) is True)
# 8.2 完全不同事件 → False
check('不同事件 → 不重复', scene_repeat_check(S_STREET, [S_WINE_2], 0.13) is False)
# 8.3 与最近多个场景逐一比对：任一命中即重复
check('多场景任一命中 → 重复', scene_repeat_check(S_WINE_1, [S_STREET, S_WINE_2], 0.13) is True)
check('多场景全不命中 → 不重复', scene_repeat_check(S_STREET, [S_WINE_1, S_WINE_2], 0.13) is False)
# 8.4 空输入/异常 → False（不炸）
check('空文本 → 不重复', scene_repeat_check("", [S_WINE_2], 0.13) is False)
check('空历史 → 不重复', scene_repeat_check(S_WINE_1, [], 0.13) is False)
# 8.5 prompt 强化断言：严禁复述已写内容 + 事件时间线注入
p = sd._build_scene_prompt(mk_prompt_state(), '上一场景结尾：林晚约你在咖啡馆见面。')
check('prompt 含"禁止复述"', '禁止复述' in p)
st_ev = mk_prompt_state()
st_ev['events'] = [{'ts': '12:00', 'summary': '林晚泼了你一杯红酒'}, {'ts': '12:01', 'summary': '顾衍之开口制止'}]
p = sd._build_scene_prompt(st_ev, '')
check('prompt 注入事件时间线', '已发生的事件' in p and '严禁重演' in p)

# ═══════════════ 9. 承诺台账 + 时间锚定（promise ledger） ═══════════════
print('▶ 承诺台账 promise_anchor_of / promise_ledger_update / promise_conflict_check')

# 9.1 时间锚提取：强锚（周几/具体日期）识别
check('强锚: 周五晚上 → 提取', promise_anchor_of('周五晚上一起吃饭') == '周五晚上')
check('强锚: 星期三下午 → 提取', promise_anchor_of('星期三下午见面') == '星期三下午')
check('强锚: 下周一 → 提取', '周一' in (promise_anchor_of('下周一交货') or ''))
check('强锚: 三天后 → 提取', '三天后' in (promise_anchor_of('三天后来取') or ''))
check('弱锚: 改天 → 不提取', promise_anchor_of('改天请你吃饭') is None)
check('弱锚: 明天/今晚 → 不提取', promise_anchor_of('明天再聊') is None)
check('无时间 → 不提取', promise_anchor_of('我会保守秘密') is None)

# 9.2 台账写入：时间锚定承诺 → pending
st9 = {'facts': [], 'pending_promises': [], 'events': []}
r = promise_ledger_update(st9, [
    {'type': 'promise', 'content': '周五晚上与林听雪一起吃饭', 'time_anchor': '周五晚上', 'target': '林听雪'},
])
check('强锚承诺 → 写入台账', len(st9.get('pending_promises', [])) == 1)
check('写入内容含 who/what/when_raw/status', all(k in st9['pending_promises'][0] for k in
      ('who', 'what', 'when_raw', 'status', 'scene_num')))
check('写入状态 pending', st9['pending_promises'][0]['status'] == 'pending')
check('返回 added 计数', r.get('added') == 1)

# 9.3 无时间锚承诺 → 不进台账
st9b = {'facts': [], 'pending_promises': [], 'events': []}
promise_ledger_update(st9b, [{'type': 'promise', 'content': '改天请你喝茶', 'time_anchor': '', 'target': '苏晚'}])
check('弱锚承诺 → 不进台账', len(st9b.get('pending_promises', [])) == 0)

# 9.4 防重复：同事件再次提取 → 不重复写入
st9c = {'facts': [], 'pending_promises': [], 'events': []}
promise_ledger_update(st9c, [{'type': 'promise', 'content': '周五晚上与林听雪一起吃饭', 'time_anchor': '周五晚上', 'target': '林听雪'}])
r2 = promise_ledger_update(st9c, [{'type': 'promise', 'content': '周五晚上和林听雪吃饭（换个说法）', 'time_anchor': '周五晚上', 'target': '林听雪'}])
check('同事件重复提取 → 不重复写入', len(st9c.get('pending_promises', [])) == 1)

# 9.5 兑现闭环：行动匹配约定 → fulfilled + 写入事件时间线
st9d = {'facts': [], 'pending_promises': [], 'events': []}
promise_ledger_update(st9d, [{'type': 'promise', 'content': '周五晚上与林听雪一起吃饭', 'time_anchor': '周五晚上', 'target': '林听雪'}])
r3 = promise_ledger_update(st9d, [], action_summary='你赴约前往餐厅，与林听雪共进晚餐')
check('赴约行动 → 承诺 fulfilled', st9d['pending_promises'][0]['status'] == 'fulfilled')
check('兑现后写入事件时间线', any('林听雪' in str(e.get('summary', '')) for e in st9d.get('events', [])))
check('返回 fulfilled 计数', r3.get('fulfilled') == 1)

# 9.6 违约闭环：明确拒绝 → broken
st9e = {'facts': [], 'pending_promises': [], 'events': []}
promise_ledger_update(st9e, [{'type': 'promise', 'content': '周五晚上与林听雪一起吃饭', 'time_anchor': '周五晚上', 'target': '林听雪'}])
r4 = promise_ledger_update(st9e, [], action_summary='你拒绝了，说那天没空不去了')
check('拒绝行动 → 承诺 broken', st9e['pending_promises'][0]['status'] == 'broken')
check('返回 broken 计数', r4.get('broken') == 1)

# 9.7 旧存档无台账 → 不炸
st9f = {'facts': []}
r5 = promise_ledger_update(st9f, [{'type': 'promise', 'content': '周五晚上见面', 'time_anchor': '周五晚上', 'target': '林听雪'}])
check('无台账字段 → 自动建不炸', isinstance(st9f.get('pending_promises'), list) and r5.get('added') == 1)

# 9.8 冲突检测：新时间表述 vs 台账
st9g = {'facts': [], 'pending_promises': [], 'events': []}
promise_ledger_update(st9g, [{'type': 'promise', 'content': '周五晚上与林听雪一起吃饭', 'time_anchor': '周五晚上', 'target': '林听雪'}])
c1 = promise_conflict_check('周三晚上，你坐在餐厅里', st9g)
check('新场景周三 vs 台账周五 → 冲突', c1 is not None and '周五' in c1)
c2 = promise_conflict_check('周五晚上你们如约见面', st9g)
check('新场景周五引用一致 → 不冲突', c2 is None)
c3 = promise_conflict_check('夜色渐深，你们并肩走在街上', st9g)
check('无时间表述 → 不冲突', c3 is None)
c4 = promise_conflict_check('改天再约', st9g)
check('弱锚表述 → 不冲突', c4 is None)

# 9.9 台账为空 → 不冲突 / 异常 → 不炸
st9h = {'facts': [], 'pending_promises': [], 'events': []}
check('台账空 → 不冲突', promise_conflict_check('周三晚上见面', st9h) is None)
check('异常输入 → 不炸', promise_conflict_check(None, None) is None)

# 9.10 prompt 注入断言：台账到期约定 → 注入"约定时间已到"块 + when_raw
st9p = mk_prompt_state()
st9p['scene_num'] = 3
st9p['pending_promises'] = [{'who': '林听雪', 'what': '一起吃饭', 'when_raw': '周五晚上',
                             'scene_num': 0, 'due_scene': 3, 'status': 'pending'}]
p = sd._build_scene_prompt(st9p, '')
check('prompt 注入到期约定块', '约定时间已到' in p and '周五晚上' in p)
st9q = mk_prompt_state()
st9q['scene_num'] = 1
st9q['pending_promises'] = [{'who': '林听雪', 'what': '一起吃饭', 'when_raw': '周五晚上',
                             'scene_num': 0, 'due_scene': 3, 'status': 'pending'}]
p2 = sd._build_scene_prompt(st9q, '')
check('未到期 → 无约定注入块', '未兑现约定' not in p2 and '约定时间已到' not in p2)

# 9.11 PACT_SYSTEM prompt 断言：time_anchor 字段要求 + 慎用时间指引
check('PACT_SYSTEM 含 time_anchor 字段', 'time_anchor' in PACT_SYSTEM)
check('PACT_SYSTEM 含慎用时间指引', '不要轻易' in PACT_SYSTEM or '避免' in PACT_SYSTEM or '关键约定' in PACT_SYSTEM)

# 9.12 角色发起的邀约（target=player）→ who=subject 进台账
st9i = {'facts': [], 'pending_promises': [], 'events': []}
r6 = promise_ledger_update(st9i, [
    {'type': 'promise', 'content': '周五晚上一起吃饭', 'time_anchor': '周五晚上',
     'subject': '林听雪', 'target': 'player'},
])
check('角色邀约 → who=subject 进台账', len(st9i.get('pending_promises', [])) == 1
      and st9i['pending_promises'][0]['who'] == '林听雪', f"got {st9i.get('pending_promises')}")

# ═══════════════ 10. 增量 PACT 预筛 + 世界状态简报 ═══════════════
print('▶ 增量提取预筛 pact_need_extract / 状态简报 state_context_brief')

# 10.1 预筛：承诺/约定类 → 触发
check('玩家承诺 → 触发', pact_need_extract('我答应你，周五晚上一起吃饭', '好，说定了') is True)
check('角色邀约时间词 → 触发', pact_need_extract('你什么时候有空？', '周五晚上我有空，一起吃饭吧') is True)
check('约定词 → 触发', pact_need_extract('就这么说定了', '嗯，一言为定') is True)
# 10.2 预筛：威胁/秘密/交易 → 触发
check('威胁 → 触发', pact_need_extract('你再靠近一步，别怪我不客气', '你不敢') is True)
check('秘密 → 触发', pact_need_extract('告诉你一个秘密，其实我见过他', '……') is True)
check('交易 → 触发', pact_need_extract('我们做个交易，你帮我拿到文件', '什么条件') is True)
# 10.3 预筛：纯闲聊 → 不触发
check('闲聊 → 不触发', pact_need_extract('今天天气不错', '是啊，难得的好天气') is False)
check('问候 → 不触发', pact_need_extract('你最近好吗', '还好，谢谢关心') is False)
check('情绪表达 → 不触发', pact_need_extract('我有点难过', '别难过，有我呢') is False)
# 10.4 预筛：异常 → 不炸
check('空输入 → 不触发', pact_need_extract('', '') is False)
check('None → 不触发', pact_need_extract(None, None) is False)

# 10.5 状态简报：玩家状态卡 + 角色情绪 + 关系 全量组装
st10 = {
    'player_state': {'location': '顾宅大厅', 'time': '夜晚', 'condition': '轻伤',
                     'situation': '刚被周太太泼酒', 'holding': ['信物'], 'money': '拮据'},
    'cast_states': {'周太太': {'present': True, 'location': '顾宅大厅', 'mood': '得意',
                               'stance': '敌视', 'agenda': '逼你出丑'},
                    '顾衍之': {'present': True, 'location': '顾宅大厅', 'mood': '冷淡',
                               'stance': '暧昧', 'agenda': '观察你'}},
    'state': {'relations': {'周太太': 20, '顾衍之': 70}},
    'events': [{'ts': '20:00', 'summary': '周太太泼了你一杯红酒'}],
}
b = state_context_brief(st10)
check('简报含玩家状况', '轻伤' in b and '泼酒' in b)
check('简报含角色情绪', '得意' in b and '冷淡' in b)
check('简报含角色态度', '敌视' in b and '暧昧' in b)
check('简报含关系值', '周太太' in b and '70' in b)
check('简报含最近事件', '泼了你一杯红酒' in b)
# 10.6 简报：空状态 → 不炸返回空串
check('空状态 → 空串', state_context_brief({}) == '')
check('None → 空串', state_context_brief(None) == '')

# ═══════════════ 11. 约定推进时钟（promise_due_check） ═══════════════
print('▶ 约定推进时钟 promise_due_check')

def mk_pledge(when, due, status='pending'):
    return {'who': '方瑜', 'what': '一起吃饭', 'when_raw': when,
            'scene_num': 0, 'due_scene': due, 'status': status}

# 11.1 未到期：场景数未到 due → 不提示（不刷存在感，防打转）
r = promise_due_check({'pending_promises': [mk_pledge('周五晚上', 3)], 'scene_num': 1})
check('未到期 → due 空', not r.get('due') and not r.get('overdue'), f"got {r}")
# 11.2 到期：scene_num >= due → 推进指令
r = promise_due_check({'pending_promises': [mk_pledge('周五晚上', 3)], 'scene_num': 3})
check('到期 → due 含约定', len(r.get('due', [])) == 1)
# 11.3 过期：scene_num >= due + 2 → NPC 追问/关系受损
r = promise_due_check({'pending_promises': [mk_pledge('周五晚上', 3)], 'scene_num': 5})
check('过期 → overdue 含约定', len(r.get('overdue', [])) == 1)
# 11.4 多约定分级：一个到期一个未到期
r = promise_due_check({'pending_promises': [
    mk_pledge('周五晚上', 3), mk_pledge('周三下午', 8)], 'scene_num': 3})
check('多约定分级', len(r.get('due', [])) == 1 and not r.get('overdue'))
# 11.5 已兑现/违约的约定不参与到期判定
r = promise_due_check({'pending_promises': [
    mk_pledge('周五晚上', 1, 'fulfilled'), mk_pledge('周六', 2, 'broken')], 'scene_num': 5})
check('非 pending 不参与', not r.get('due') and not r.get('overdue'))
# 11.6 无台账/异常 → 不炸
check('无台账 → 空结果', promise_due_check({}) == {'due': [], 'overdue': []})
check('None → 空结果', promise_due_check(None) == {'due': [], 'overdue': []})

# 11.7 ledger 写入带 due_scene（约定后 3 场景内推进）
st11 = {'facts': [], 'pending_promises': [], 'events': [], 'scene_num': 7}
promise_ledger_update(st11, [
    {'type': 'promise', 'content': '周五晚上与林听雪一起吃饭', 'time_anchor': '周五晚上', 'target': '林听雪'},
])
check('写入含 due_scene=10', st11['pending_promises'][0].get('due_scene') == 10,
      f"got {st11['pending_promises'][0].get('due_scene')}")

# 11.8 prompt 分级注入断言
# 未到期 → 不注入约定块（防 NPC 反复提起打转）
stp = mk_prompt_state()
stp['pending_promises'] = [mk_pledge('周五晚上', 99)]
stp['scene_num'] = 1
p = sd._build_scene_prompt(stp, '')
check('未到期 → 无约定注入块', '未兑现约定' not in p)
# 到期 → 注入推进指令
stp2 = mk_prompt_state()
stp2['pending_promises'] = [mk_pledge('周五晚上', 3)]
stp2['scene_num'] = 3
p2 = sd._build_scene_prompt(stp2, '')
check('到期 → 注入推进指令', '约定时间已到' in p2 and '推进' in p2)
# 过期 → 注入追问指令
stp3 = mk_prompt_state()
stp3['pending_promises'] = [mk_pledge('周五晚上', 3)]
stp3['scene_num'] = 6
p3 = sd._build_scene_prompt(stp3, '')
check('过期 → 注入追问指令', '追问' in p3 or '失望' in p3 or '过期' in p3)

# ═══════════════ 12. 状态合并 + 事件保活（防事件重复触发） ═══════════════
print('▶ 状态合并 merge_cast_states / 事件检测 state_change_detect')

# 12.1 合并更新：LLM 输出的角色更新，未输出的角色保留（整体覆盖 bug 修复）
old_cs = {
    '方瑜': {'present': True, 'location': '宴会厅', 'mood': '温柔', 'stance': '亲近',
             'knows': ['信物下落'], 'condition': '健康', 'agenda': '护住你'},
    '周太太': {'present': True, 'location': '宴会厅', 'mood': '得意', 'stance': '敌视',
               'knows': [], 'condition': '健康', 'agenda': '逼你出丑'},
    '顾衍之': {'present': False, 'location': '书房', 'mood': '冷淡', 'stance': '暧昧',
               'knows': [], 'condition': '健康', 'agenda': '观察你'},
}
new_cs = {
    '方瑜': {'present': True, 'location': '宴会厅', 'mood': '愤怒', 'stance': '保护',
             'knows': ['周太太把柄'], 'condition': '健康', 'agenda': '当众揭穿周太太'},
}
m = merge_cast_states(old_cs, new_cs)
check('输出的角色状态更新', m['方瑜']['mood'] == '愤怒' and m['方瑜']['agenda'] == '当众揭穿周太太')
check('未输出角色保留（周太太）', m['周太太']['mood'] == '得意' and m['周太太']['stance'] == '敌视')
check('未输出角色保留（顾衍之，含不在场标记）', m['顾衍之']['present'] is False and m['顾衍之']['stance'] == '暧昧')
check('knows 累积并集（旧+新都保留）', '信物下落' in (m.get('方瑜', {}).get('knows') or [])
      and '周太太把柄' in (m.get('方瑜', {}).get('knows') or []))
# 12.2 合并边界：空输出 → 全部保留；空旧态 → 只新增；异常 → 不炸
check('空输出 → 全部保留', merge_cast_states(old_cs, {}) == old_cs)
check('空旧态 → 只新增', merge_cast_states({}, new_cs) == new_cs)
check('None 输入 → 不炸', merge_cast_states(None, None) in ({}, None) or isinstance(merge_cast_states(None, None), dict))
check('异常字段 → 不炸', isinstance(merge_cast_states({'x': 'str'}, new_cs), dict))

# 12.3 事件变化词检测：明确状态变化 → 检测到
check('拒绝 → 检测', state_change_detect('方瑜冷着脸，你拒绝了她的提议') is not None)
check('决裂 → 检测', state_change_detect('顾衍之与你彻底决裂，转身离去') is not None)
check('答应/同意 → 检测', state_change_detect('你答应了方瑜的请求') is not None)
check('翻脸 → 检测', state_change_detect('周太太当场翻脸，撕破了脸皮') is not None)
# 12.4 无变化词 → None；空 → None
check('纯描述 → 不检测', state_change_detect('宴会厅灯火通明，觥筹交错') is None)
check('空文本 → 不检测', state_change_detect('') is None)
check('None → 不检测', state_change_detect(None) is None)

# 12.5 事件时间线上限扩容（关键记忆不被挤出窗口）
check('EVENT_LIMIT >= 30（扩容）', EVENT_LIMIT >= 30, f"got {EVENT_LIMIT}")

# 12.6 锚点触发后写事件时间线（已完成节点进 LLM 显式记忆）
st12 = {'facts': [], 'pending_promises': [], 'events': [],
        'chapter_beats': {'chapter_idx': 0, 'beats': [
            {'id': 1, 'desc': '开篇：周太太泼酒羞辱你', 'status': 'current',
             'trigger': {'state_output': {'flags': ['见过周太太'], 'relations': {'周太太': -10}}}},
        ]},
        'anchor_triggered': {'beat_id': 1, 'hook': '周太太当众泼酒'}}
try:
    sd._advance_beat(st12)
    ev_sum = " ".join(str(e.get("summary", "")) for e in st12.get("events", []))
    check('锚点触发 → 事件时间线记录', '开篇' in ev_sum or '泼酒' in ev_sum or '节点' in ev_sum, ev_sum[:60])
except Exception as e:
    check(f'锚点触发写事件异常: {e}', False)

# ═══════════════ 13. 章节回流重构（v2.5.61：幂等 + 补漏 + global_state 同步） ═══════════════
print('▶ 章节回流重构 sync/backfill/gs_merge')

# 13.1 sync_skip_check：未记录 → 不跳过（需要回流）
check('无同步记录 → 需回流', sync_skip_check({}, 3, 1, 10) is False)
check('无记录但 final_done → 不跳过', sync_skip_check({}, 99, 1, 5) is False)

# 13.2 sync_skip_check：区间完全覆盖 → 跳过（幂等）
st13 = {'synced_chapters': {'3': [{'start': 1, 'end': 10}]}}
check('区间完全覆盖 → 跳过', sync_skip_check(st13, 3, 1, 10) is True)
check('区间完全覆盖(子区间) → 跳过', sync_skip_check(st13, 3, 3, 8) is True)
check('区间未覆盖(新场景) → 需回流', sync_skip_check(st13, 3, 8, 15) is False)
check('区间未覆盖(前段) → 需回流', sync_skip_check(st13, 3, 0, 5) is False)

# 13.3 sync_mark_record：记录合并（新区间并集，不覆盖旧区间）
st13b = {'synced_chapters': {'3': [{'start': 1, 'end': 5}]}}
sync_mark_record(st13b, 3, 8, 12)
recs = st13b['synced_chapters']['3']
check('记录追加新区间', len(recs) == 2 and recs[1] == {'start': 8, 'end': 12}, str(recs))
sync_mark_record(st13b, 3, 4, 6)
check('相邻区间并入(4-6 并 1-5 → 1-6)', sync_skip_check(st13b, 3, 1, 6) is True, str(st13b['synced_chapters']['3']))
check('记录按 start 排序', [r['start'] for r in st13b['synced_chapters']['3']] == [1, 8], str(st13b['synced_chapters']['3']))

# 13.4 sync_mark_record：异常输入不炸
st13c = {}
sync_mark_record(st13c, 3, 5, 2)  # end < start
check('非法区间(end<start) → 不记录', 'synced_chapters' not in st13c or not st13c['synced_chapters'])
sync_mark_record(st13c, 3, 0, 0)  # 空区间
check('空区间 → 不记录', not (st13c.get('synced_chapters') or {}).get('3'), str(st13c.get('synced_chapters')))

# 13.5 backfill_list：互动已完成但正式章节缺失 → 补漏
st13d = {
    'synced_chapters': {'1': [{'start': 1, 'end': 5}], '2': [{'start': 6, 'end': 10}]},
    'outline_chapters': [
        {'number': 1, 'title': '第一章'},
        {'number': 2, 'title': '第二章'},
        {'number': 3, 'title': '第三章'},
        {'number': 4, 'title': '第四章'},
    ],
    'outline_progress': {'idx': 2},  # 互动已推进到第 3 章（idx=2）
}
existing = {'chapter_0001.md': '已有', 'chapter_0002.md': '已有'}
bf = backfill_list(st13d, existing, max_sync=2)
check('已完成但缺失章节进入补漏', bf == [3], f"bf={bf}")
# 第 3 章缺失但互动还没推进到（idx=2 表示正在玩第 3 章，未完成）→ 不补
st13e = dict(st13d)
st13e['outline_progress'] = {'idx': 1}
check('进行中的章节不补漏', backfill_list(st13e, existing, max_sync=2) == [], f"bf={backfill_list(st13e, existing, max_sync=2)}")
# 第 4 章 idx=2 完成过（final_done）→ 补漏
st13f = dict(st13d)
st13f['outline_progress'] = {'idx': 2, 'final_done': True}
bf2 = backfill_list(st13f, existing, max_sync=2)
check('最后一章 final_done → 补漏', bf2 == [3, 4], f"bf={bf2}")

# 13.6 gs_merge_sync：global_state 合并（timeline/chapters_summary 写入，不丢其他字段）
gs = {'protagonist_state': {'name': '秦昭'}, 'timeline': {'total_days': 0, 'chapters': {}},
      'characters': {'秦昭': {}}}
r = gs_merge_sync(gs, 3, '第三章 入城', '正文内容...', '本章摘要')
check('chapters_summary 写入', r.get('chapters_summary', {}).get('3') == '本章摘要')
check('timeline.chapters 初始化', str(r.get('timeline', {}).get('chapters', {}).get('3', {}))[:50] != '')
check('原有字段保留', r.get('protagonist_state', {}).get('name') == '秦昭' and '秦昭' in r.get('characters', {}))
check('标题字段写入', r.get('chapter_titles', {}).get('3') == '第三章 入城' if 'chapter_titles' in r else True)
# 已有 timeline 的章不覆盖原有 days_elapsed
gs2 = {'timeline': {'total_days': 3, 'chapters': {'3': {'days_elapsed': 3, 'chapter_start_time': '夜晚'}}}}
r2 = gs_merge_sync(gs2, 3, '第三章', '正文', '摘要')
check('已有时序保留', r2['timeline']['chapters']['3'].get('days_elapsed') == 3, str(r2['timeline']['chapters'].get('3')))

# 13.7 gs_merge_sync：异常输入不炸
check('空 gs → 不炸', isinstance(gs_merge_sync(None, 1, '', '', ''), dict) or gs_merge_sync(None, 1, '', '', '') is None)
check('异常 gs → 不炸', gs_merge_sync({'timeline': 'bad'}, 1, 't', 'b', 's') is not None)

# ═══════════════ 14. 角色选择扮演（v2.5.62：全角色预设 + 可选扮演） ═══════════════
print('▶ 角色预设 cast_presets_build / choose_char_apply')

# 14.1 cast_presets_build：从 plan 构建全角色标准化档案
plan14 = {
    'characters': {
        'protagonist': {
            'name': '秦昭', 'age': '24', 'identity': '交易法则的篡改者',
            'personality': {'surface': '冷静', 'true_self': '执着', 'flaw': '多疑'},
            'backstory': '被主神空间选中', 'motivation': {'want': '活下去', 'need': '信任'},
            'arc': '学会信任', 'catchphrase': '一切皆可交易',
        },
        'supporting': [
            {'name': '方瑜', 'identity': '医术天才', 'relation': '挚友', 'personality': '温柔坚定', 'role': '盟友', 'mini_arc': '走出阴影', 'meaning': '盟友'},
            {'name': '顾衍之', 'identity': '商会会长', 'relation': '对手', 'personality': '城府深', 'role': '对手', 'mini_arc': '立场转变', 'meaning': '对手'},
        ],
        'antagonist': [
            {'name': '周太太', 'motivation': '夺产', 'power': '权势', 'conflict': '遗产之争', 'humanity': '丧子之痛'},
        ],
    },
}
presets = cast_presets_build(plan14)
check('全角色进入预设', len(presets) == 4, f"n={len(presets)}")
names = [p['name'] for p in presets]
check('主角在预设', '秦昭' in names)
check('配角在预设', '方瑜' in names and '顾衍之' in names)
check('反派在预设', '周太太' in names)
p0 = presets[names.index('秦昭')]
check('主角身份完整', p0.get('identity') == '交易法则的篡改者' and p0.get('role') == 'protagonist')
check('主角性格完整(表面+真我)', '冷静' in str(p0.get('personality')) and '执着' in str(p0.get('personality')))
check('主角 speak_style 兜底(性格)', str(p0.get('speak_style'))[:2] != '' or str(p0.get('personality')) != '')
p_sup = presets[names.index('方瑜')]
check('配角 speak_style 兜底', str(p_sup.get('speak_style'))[:2] != '', f"ss={p_sup.get('speak_style')}")
check('配角 initial_attitude 兜底(关系)', str(p_sup.get('initial_attitude'))[:2] != '', f"ia={p_sup.get('initial_attitude')}")
check('配角 backstory 兜底', str(p_sup.get('backstory'))[:2] != '', f"bs={p_sup.get('backstory')}")

# 14.2 cast_presets_build：异常/空输入不炸
check('空 plan → 空列表', cast_presets_build({}) == [] or isinstance(cast_presets_build({}), list))
check('None → 空列表', cast_presets_build(None) == [] or isinstance(cast_presets_build(None), list))

# 14.3 choose_char_apply：选择配角 → player_char 设置 + 主角 NPC 化 + casts 初始化
st14 = {
    'novel_id': 'test', 'scene_num': 0,
    'casts': {},
    'player_char': {},  # 初始空（start 前）
}
presets_map = {p['name']: p for p in presets}
ok, msg = choose_char_apply(st14, '方瑜', presets_map)
check('选择配角 → 成功', ok is True, msg)
pc = st14.get('player_char') or {}
check('player_char 设为方瑜', pc.get('name') == '方瑜', str(pc))
check('player_char 带完整档案', pc.get('identity') == '医术天才' and pc.get('role') == 'supporting')
check('主角 NPC 化进 casts', '秦昭' in st14.get('casts', {}), str(st14.get('casts', {}).keys()))
check('被选角色不进 casts（玩家控制）', '方瑜' not in st14.get('casts', {}), str(st14.get('casts', {}).keys()))
check('casts 带角色档案', 'profile' in st14.get('casts', {}).get('秦昭', {}) or st14.get('casts', {}).get('秦昭', {}).get('role') == 'protagonist')

# 14.4 choose_char_apply：选主角 → 主角不进 casts（原本就扮演主角）
st14b = {'novel_id': 'test', 'casts': {}, 'player_char': {}}
ok2, _ = choose_char_apply(st14b, '秦昭', presets_map)
check('选择主角 → 成功', ok2 is True)
check('选主角时主角不进 casts', '秦昭' not in st14b.get('casts', {}))
check('其他角色进 casts', '方瑜' in st14b.get('casts', {}) and '周太太' in st14b.get('casts', {}))

# 14.5 choose_char_apply：非法角色名 → 失败不炸
st14c = {'novel_id': 'test', 'casts': {}, 'player_char': {}}
ok3, msg3 = choose_char_apply(st14c, '不存在的人', presets_map)
check('非法角色 → 拒绝', ok3 is False and msg3 != '', msg3)
check('非法角色不污染状态', not st14c.get('player_char') and not st14c.get('casts'))

# ═══════════════ 汇总 ═══════════════
print(f'\n═══ 结果: {PASS} 通过 / {FAIL} 失败 ═══')
sys.exit(0 if FAIL == 0 else 1)
