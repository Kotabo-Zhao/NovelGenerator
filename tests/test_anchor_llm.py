# -*- coding: utf-8 -*-
"""锚点式 T2 LLM 集成测试（手动运行，需真实 API key）

运行: python tests/test_anchor_llm.py

覆盖：
1. scene_beats 生成（planner 模板）→ trigger/entry_hook/reject_outcome/state_output 字段完整
2. trigger 条件字段合法性（field/op 枚举）
3. _ensure_chapter_beats 消费 scene_beats 的 trigger/entry_hook（离线部分）
"""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from core.engine import NovelEngine
from core.interactive.story_director import StoryDirector

PASS, FAIL = 0, 0
def check(name, cond, detail=''):
    global PASS, FAIL
    if cond: PASS += 1; print(f'  OK {name} {detail}')
    else: FAIL += 1; print(f'  XX {name} {detail}')

VALID_FIELDS = {'tension', 'location', 'flag', 'relations', 'inventory'}
VALID_OPS = {'>=', '<=', '==', '!=', 'has', 'not_has'}

# ═══════════════ 1. scene_beats 生成（真实 LLM） ═══════════════
print('▶ T2.1 scene_beats 生成（真实 LLM，约 30-60s）')
engine = NovelEngine()
try:
    from core.resilient_client import ResilientLLMClient
    rl = ResilientLLMClient(engine.client, engine.model)
    scene_beats_schema = (
        '[{"beat":1,"name":"开篇钩子","function":"立即抓住读者","key_action":"具体事件",'
        '"trigger":{"type":"event/scene","conditions":[{"field":"tension/location/flag/relations/inventory",'
        '"op":">=/<=/==/!=/has/not_has","value":"阈值或关键词","target":"可选"}],"timeout_scenes":3-5},'
        '"reject_outcome":"玩家拒绝时后果","state_output":{"flags":[],"relations":{}},"entry_hook":"事件如何找上门"}]×5'
    )
    user = (
        f"生成一部都市悬疑互动小说的【第一章】5 个 scene_beats（Galgame 节点）。\n"
        f"章节目标: 第一章《深夜咖啡馆》——主角收到匿名信，赴约后卷入一起失踪案调查。\n"
        f"输出 JSON 数组，严格按此结构（每个 beat 必填所有字段）:\n{scene_beats_schema}\n"
        f"要求: trigger.conditions 至少 1 条真实状态条件（tension 阈值/地点/flag/关系/物品）；"
        f"entry_hook 写清本节点事件如何找上门；reject_outcome 写玩家拒绝时的后果；"
        f"state_output 写触发后的 flag/关系变化。只输出 JSON。"
    )
    t0 = time.time()
    resp = rl.create(messages=[
        {"role": "system", "content": "你是互动小说剧情拆解师，输出严格 JSON。"},
        {"role": "user", "content": user},
    ], temperature=0.4, max_tokens=2000)
    content = resp.choices[0].message.content if hasattr(resp, 'choices') else resp
    text = str(content).strip()
    start, end = text.find('['), text.rfind(']')
    data = json.loads(text[start:end + 1]) if start >= 0 else []
    dur = time.time() - t0
    check('LLM 返回 5 个 beats', len(data) == 5, f'got {len(data)} ({dur:.0f}s)')
    if data:
        for i, b in enumerate(data):
            if not isinstance(b, dict):
                check(f'beat{i+1} 是 dict', False)
                continue
            check(f'beat{i+1} key_action 非空', bool(str(b.get('key_action', '')).strip()))
            trig = b.get('trigger')
            check(f'beat{i+1} trigger 存在', isinstance(trig, dict))
            if isinstance(trig, dict):
                conds = trig.get('conditions') or []
                check(f'beat{i+1} conditions ≥1 条', len(conds) >= 1, f'{len(conds)}条')
                ok_fields = True
                for c in conds:
                    if isinstance(c, dict) and c.get('field') not in VALID_FIELDS:
                        ok_fields = False
                    if isinstance(c, dict) and c.get('op') not in VALID_OPS:
                        ok_fields = False
                check(f'beat{i+1} 条件字段合法', ok_fields)
                ts = int(trig.get('timeout_scenes', 0) or 0)
                check(f'beat{i+1} timeout 3-5', 3 <= ts <= 5, f'{ts}')
            check(f'beat{i+1} entry_hook 非空', bool(str(b.get('entry_hook', '')).strip()))
            check(f'beat{i+1} reject_outcome 非空', bool(str(b.get('reject_outcome', '')).strip()))
            check(f'beat{i+1} state_output 是 dict', isinstance(b.get('state_output'), dict))
except Exception as e:
    check(f'LLM 调用失败: {type(e).__name__}: {str(e)[:80]}', False)

# ═══════════════ 2. _ensure_chapter_beats 消费 trigger（离线） ═══════════════
print('▶ T2.2 _ensure_chapter_beats 消费 trigger（离线）')
from unittest.mock import MagicMock
sd = StoryDirector(MagicMock(), 'test-model', MagicMock())
state = {
    'outline_progress': {'idx': 0},
    'outline_chapters': [{'number': 1, 'title': '第一章', 'summary': '咖啡馆相遇',
                          'target_words': 3000, 'scene_beats': [
        {'beat': 1, 'name': '开篇', 'key_action': '收到匿名信',
         'trigger': {'type': 'event', 'conditions': [{'field': 'tension', 'op': '>=', 'value': 3}],
                     'timeout_scenes': 4},
         'entry_hook': '服务员递来一封信', 'reject_outcome': '拒绝→信被收走',
         'state_output': {'flags': ['已收信'], 'relations': {}}},
        {'beat': 2, 'name': '冲突', 'key_action': '赴约',
         'trigger': {'type': 'scene', 'conditions': [{'field': 'location', 'op': '==', 'value': '咖啡馆'}],
                     'timeout_scenes': 4},
         'entry_hook': '', 'reject_outcome': '', 'state_output': {}},
        ]},
    ],
}
beats = sd._ensure_chapter_beats('test', state, force=True)
check('beats 生成 2 个', len(beats) == 2)
if beats:
    check('beat1 trigger 保留', beats[0].get('trigger', {}).get('conditions') == [{'field': 'tension', 'op': '>=', 'value': 3}])
    check('beat1 entry_hook 保留', beats[0].get('entry_hook') == '服务员递来一封信')
    check('beat1 status=current', beats[0].get('status') == 'current')
    check('beat2 空 entry_hook 兜底为 key_action', '赴约' in str(beats[1].get('entry_hook', '')))

# ═══════════════ 3. _adapt_outline 动态大纲微调（真实 LLM） ═══════════════
print('▶ T2.3 _adapt_outline 动态大纲微调（真实 LLM，约 30-60s）')
try:
    sd_real = StoryDirector(engine.client, engine.model, MagicMock())
    state2 = {
        'outline_progress': {'idx': 0},
        'outline_chapters': [
            {'number': 1, 'title': '第一章', 'summary': '收到信物'},
            {'number': 2, 'title': '第二章', 'summary': '凭信物进入地下城',
             'scene_beats': [
                 {'beat': 1, 'name': '开篇', 'key_action': '用信物打开地下城大门',
                  'trigger': {'type': 'scene', 'conditions': [
                      {'field': 'inventory', 'op': 'has', 'value': '信物'}],
                      'timeout_scenes': 4}},
                 {'beat': 2, 'name': '冲突', 'key_action': '遭遇守卫', 'trigger': {
                     'type': 'event', 'conditions': [{'field': 'tension', 'op': '>=', 'value': 3}],
                     'timeout_scenes': 4}},
             ]},
        ],
        'state': {'location': '废墟', 'flags': ['信物已毁'], 'inventory': []},
        'player_state': {'location': '废墟', 'situation': '信物被烧毁，陷入困境'},
        'last_action': {'summary': '你烧毁了信物'},
        'mainline': {'required_flags': ['信物'], 'acquired': [], 'expected_by_chapter': 1},
    }
    r = sd_real._adapt_outline('test', state2)
    check('adapt 返回 dict', isinstance(r, dict))
    if isinstance(r, dict):
        if r.get('appropriate') is False:
            repl = r.get('replacement')
            check('替换锚点非空', isinstance(repl, list) and len(repl) >= 1)
            if isinstance(repl, list) and repl:
                b0 = repl[0]
                check('替换锚点含 key_action', bool(str(b0.get('key_action', '')).strip()))
                check('替换锚点触发条件不再依赖已毁信物',
                      '信物' not in json.dumps(b0.get('trigger', {}), ensure_ascii=False))
        else:
            check('LLM 判定合适（玩家信物已毁场景应判不合适）', False, 'appropriate=True')
except Exception as e:
    check(f'adapt LLM 失败: {type(e).__name__}: {str(e)[:80]}', False)

print(f'\n═══ 结果: {PASS} 通过 / {FAIL} 失败 ═══')
sys.exit(0 if FAIL == 0 else 1)
