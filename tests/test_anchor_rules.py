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

# ═══════════════ 汇总 ═══════════════
print(f'\n═══ 结果: {PASS} 通过 / {FAIL} 失败 ═══')
sys.exit(0 if FAIL == 0 else 1)
