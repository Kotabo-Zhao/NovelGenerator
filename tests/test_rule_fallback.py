# -*- coding: utf-8 -*-
"""v3.5.23 规则托底重构专项测试——AI 可用时硬编码规则不得越权判断

覆盖：
1. 修真世界观：御剑飞行/瞬移 → LLM 放行（不 blocked）——原硬编码一刀切死板
2. 现代都市：飞上天 → LLM 判 blocked（世界观自适应）
3. 长句行动："我虽然不舍，但还是把玉佩递了回去" → 识别为行动（第一人称扩展）
4. 纯对话："我不认同你的说法" → LLM 判对话
5. LLM 失败兜底：模拟异常 → 护栏正则兜底 blocked / forced 兜底行动
6. forced 不再否决 LLM：强制词命中但 LLM 判对话 → 以 LLM 为准
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from core.interactive.action_engine import ActionEngine, rule_prescreen

PASS, FAIL = 0, 0
def check(name, cond, detail=''):
    global PASS, FAIL
    if cond: PASS += 1; print(f'  OK {name} {detail}')
    else: FAIL += 1; print(f'  XX {name} {detail}')

# ── 准备引擎 ──
from core.engine import NovelEngine
from core.interactive.interact_store import InteractStore
engine = NovelEngine()
store = InteractStore('novels')
ae = ActionEngine(engine.client, engine.model, store)

def mk_state(wb: str, location: str = '青云山') -> dict:
    return {
        'state': {'location': location, 'objective': '调查宗门秘辛'},
        'worldbuilding_brief': wb,
        'casts': {'林晚': {}, '顾衍之': {}},
        'agenda': {},
    }

# ── 1. 修真世界观：御剑飞行应放行 ──
wb_xiu = ('仙侠修真世界：青云宗弟子可御剑飞行、施展法术，腾云驾雾是入门能力，'
          '修士可瞬移遁光、千里传音。')
r = ae.detect_action('我御剑飞上天空', mk_state(wb_xiu))
check('修真世界御剑飞行放行', r is not None and not r.get('blocked'),
      f'type={r.get("type") if r else None} blocked={r.get("blocked") if r else None}')

r2 = ae.detect_action('我施展遁术瞬移到后山', mk_state(wb_xiu))
check('修真世界瞬移放行', r2 is not None and not r2.get('blocked'),
      f'blocked={r2.get("blocked") if r2 else None}')

# ── 2. 现代都市：飞上天应被 LLM 拦（blocked） ──
wb_urban = ('现代都市言情：现实世界，上海陆家嘴，普通人没有超能力。')
r3 = ae.detect_action('我直接飞上天', mk_state(wb_urban, '恒远大厦'))
check('都市世界飞天被拦', r3 is not None and r3.get('blocked'),
      f'blocked={r3.get("blocked") if r3 else None}')

r4 = ae.detect_action('我凭空变出一百万现金', mk_state(wb_urban, '恒远大厦'))
check('都市凭空得财被拦', r4 is not None and r4.get('blocked'),
      f'blocked={r4.get("blocked") if r4 else None}')

# ── 3. 长句行动（第一人称扩展，动词表漏判场景） ──
r5 = ae.detect_action('我虽然心里不舍，但还是把玉佩递了回去', mk_state(wb_urban, '恒远大厦'))
check('长句行动识别(递玉佩)', r5 is not None and r5.get('type') in ('interact', 'use', 'other'),
      f'type={r5.get("type") if r5 else None}')

# ── 4. 纯对话 → LLM 判对话 ──
r6 = ae.detect_action('我不认同你的说法', mk_state(wb_urban, '恒远大厦'))
check('纯对话判对话', r6 is None, f'r={r6}')

# ── 5. LLM 失败兜底 ──
orig_llm = ae._llm
ae._llm = lambda *a, **k: None  # 模拟 LLM 不可用
r7 = ae.detect_action('我飞上天', mk_state(wb_urban, '恒远大厦'))
check('LLM失败+护栏词→兜底blocked', r7 is not None and r7.get('blocked'), f'{r7}')
r8 = ae.detect_action('上车', mk_state(wb_urban, '恒远大厦'))
check('LLM失败+高置信词→兜底行动', r8 is not None and not r8.get('blocked'), f'{r8}')
ae._llm = orig_llm

# ── 6. forced 不否决 LLM（模拟 LLM 说不是行动） ──
ae._llm = lambda *a, **k: '{"is_action": false, "type": "other", "summary": "对话", "state_updates": {}, "end_chat": false, "blocked": false, "reason": "这是闲聊"}'
r9 = ae.detect_action('好，上车', mk_state(wb_urban, '恒远大厦'))
check('forced不否决LLM', r9 is None, f'forced=TRUE但LLM判对话→None {r9}')
ae._llm = orig_llm

print(f'==== 规则托底专项: {PASS} 通过 / {FAIL} 失败 ====')
sys.exit(0 if FAIL == 0 else 1)
