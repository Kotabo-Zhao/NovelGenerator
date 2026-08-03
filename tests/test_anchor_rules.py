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

# ═══════════════ 汇总 ═══════════════
print(f'\n═══ 结果: {PASS} 通过 / {FAIL} 失败 ═══')
sys.exit(0 if FAIL == 0 else 1)
