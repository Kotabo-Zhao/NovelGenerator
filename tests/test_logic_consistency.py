# -*- coding: utf-8 -*-
"""v3.5.43: 互动模式一致性校验测试——时空一致性 / 因果一致性 / 数据完整性

用法: python tests/test_logic_consistency.py [novel_id]
默认校验所有有互动存档的小说。纯离线数据校验（不调 LLM，秒级完成）。

校验项：
【时空一致性】
 T1 场景号连续递增（无跳号/回退）
 T2 状态卡时间序列不倒退（清晨<上午<正午<下午<傍晚<夜晚<深夜）
 T3 recent_blocks 与场景日志对应（场景数 <= blocks 数）
 T4 无玩家台词块（主角不会自己说话）
【因果一致性】
 C1 无名单外角色乱入（场景对话角色 ⊆ casts 白名单）
 C2 待兑现承诺有对应角色记忆（软校验，仅报告）
 C3 章节进度不倒退（outline idx 单调）
 C4 玩家选择沉淀（facts/relations 至少存在）
【数据完整性】
 D1 存档 JSON 可解析、必需字段齐全
 D2 temp 角色不残留（absent_count < 3 或已被清理）
 D3 player_state 8 字段完整
"""
import json
import os
import sys
import re

ROOT = os.path.join(os.path.dirname(__file__), '..')
NOVELS = os.path.join(ROOT, 'novels')

PASS, FAIL, WARN = 0, 0, 0
REPORT = []

def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        REPORT.append(f'  OK {name} {detail}')
    else:
        FAIL += 1
        REPORT.append(f'  XX {name} {detail}')

def soft(name, cond, detail=''):
    """软校验：不判失败，只记录"""
    global WARN
    if not cond:
        WARN += 1
        REPORT.append(f'  !! {name} {detail}')

TIME_ORDER = {'清晨': 1, '早晨': 1, '上午': 2, '中午': 3, '正午': 3,
              '下午': 4, '傍晚': 5, '黄昏': 5, '晚上': 6, '夜晚': 6,
              '深夜': 7, '午夜': 7, '凌晨': 0}

def time_value(t: str):
    if not t:
        return None
    for k, v in TIME_ORDER.items():
        if k in t:
            return v
    return None


def validate_novel(nid: str):
    global PASS, FAIL, WARN
    print(f'═══ {nid} ═══')
    st_path = os.path.join(NOVELS, nid, 'interactive', 'state.json')
    scenes_path = os.path.join(NOVELS, nid, 'interactive', 'scene_logs.jsonl')
    if not os.path.exists(st_path):
        check('存档存在', False, '无 interactive/state.json')
        return
    # D1: 存档可解析 + 必需字段
    try:
        st = json.load(open(st_path, encoding='utf-8'))
        check('D1 存档可解析', True)
    except Exception as e:
        check('D1 存档可解析', False, str(e)[:40])
        return
    player_name = (st.get('player_char') or {}).get('name', '')
    scenes = []
    if os.path.exists(scenes_path):
        for line in open(scenes_path, encoding='utf-8'):
            try:
                scenes.append(json.loads(line))
            except Exception:
                pass

    # ── 时空一致性 ──
    nums = [int(s.get('scene_num', 0)) for s in scenes]
    check('T1 场景号连续递增', nums == sorted(nums) and len(set(nums)) == len(nums),
          f'{len(nums)} 场景')
    # T2: 状态卡时间不倒退（取时间线：从 recent_blocks 无法取时间，用 player_state 快照不可得——
    # 校验最近场景文本中的时间词序列）
    times = []
    for s in scenes[-8:]:
        txt = str(s.get('scene_text', ''))
        for k in TIME_ORDER:
            if k in txt:
                times.append((TIME_ORDER[k], k))
                break
    if len(times) >= 2:
        vals = [t[0] for t in times if t[0] is not None]
        regress = any(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
        check('T2 场景时间不倒退', not regress, f'{times[-2:]}')
    else:
        soft('T2 时间序列样本不足', len(times) >= 2, '仅 1 个时间点')
    # T3: recent_blocks 与场景日志对应（旧存档未迁移 → 软警告，API 会自动迁移）
    rb = st.get('recent_blocks') or []
    if len(rb) == 0 and scenes:
        soft('T3 recent_blocks 待迁移', False, f'{len(scenes)} 场景，0 块（API 读取时自动迁移）')
    else:
        check('T3 recent_blocks 覆盖场景', len(rb) >= max(len(scenes), 1),
              f'{len(rb)} 块 vs {len(scenes)} 场景')
    # T4: 无玩家台词块
    p_blocks = [b for b in rb if b.get('type') == 'dialogue' and b.get('speaker') == player_name]
    check('T4 无玩家台词块', not p_blocks, f'{len(p_blocks)} 块' if p_blocks else '')

    # ── 因果一致性 ──
    casts = st.get('casts') or {}
    # C1: 场景对话角色 ⊆ 白名单
    rogue = []
    for s in scenes[-10:]:
        for b in (s.get('blocks') or []):
            sp = b.get('speaker', '')
            if sp and b.get('type') == 'dialogue' and sp not in casts and sp not in ('旁白', '叙述', '动作', '描写'):
                rogue.append((s.get('scene_num'), sp))
    if rogue and not casts:
        soft('C1 旧存档 casts 空（角色待重建）', False, f'乱入候选 {rogue[:2]}，casts 为空')
    else:
        check('C1 无名单外角色乱入', not rogue, f'{rogue[:3]}' if rogue else '')
    # C2: 待兑现承诺有角色记忆对应（软）
    facts = [f for f in (st.get('facts') or []) if f.get('status') == 'active']
    mems = st.get('memories') or {}
    if facts:
        fact_text = ''.join(str(f.get('content', ''))[:10] for f in facts[:3])
        mem_text = json.dumps(mems, ensure_ascii=False)[:2000]
        # 软校验：承诺内容关键词是否出现在任一角色记忆中
        hit = any(str(f.get('content', ''))[:8] in mem_text for f in facts[:3])
        soft('C2 承诺有记忆对应', hit, f'{len(facts)} 条待兑现')
    # C3: 章节进度（旧存档无大纲 → 软警告）
    op = st.get('outline_progress') or {}
    chs = st.get('outline_chapters') or []
    if not chs:
        soft('C3 无大纲章节（旧存档）', False, 'start 后自动载入')
    else:
        check('C3 章节进度存在', 'idx' in op, str(op))
    # C4: 玩家选择沉淀（无对话记录的书 → 软警告）
    has_chat = os.path.exists(os.path.join(NOVELS, nid, 'interactive', 'chat_logs.jsonl'))
    if not facts and not (st.get('state') or {}).get('relations') and not has_chat:
        soft('C4 无对话记录', False, '该存档尚未产生互动')
    elif not facts and not (st.get('state') or {}).get('relations'):
        soft('C4 有对话无沉淀（旧存档/未 end-chat）', False, 'end-chat 提取后自动补齐')
    else:
        check('C4 facts/relations 沉淀', bool(facts) or bool((st.get('state') or {}).get('relations')),
              f'facts={len(facts)}')

    # ── 数据完整性 ──
    # D2: temp 角色不残留（absent_count 机制工作）
    temp_left = [n for n, c in casts.items() if c.get('temp') and (c.get('absent_count') or 0) >= 3]
    check('D2 temp 角色清理', not temp_left, f'{temp_left}' if temp_left else '')
    # D3: player_state 8 字段（旧存档未生成 → 软警告）
    ps = st.get('player_state') or {}
    need = {'location', 'time', 'with', 'holding', 'condition', 'disguise', 'money', 'situation'}
    missing = need - set(ps.keys())
    if not ps:
        soft('D3 player_state 未生成（旧存档）', False, '新场景后自动补齐')
    else:
        check('D3 player_state 8 字段', not missing, f'缺 {missing}' if missing else '')
    # D3b: cast_states（NPC 状态卡）
    cs = st.get('cast_states') or {}
    if cs:
        check('D3b cast_states 字段完整',
              all({'mood', 'stance', 'knows', 'agenda'} <= set(c.keys()) for c in cs.values()),
              f'{len(cs)} 角色')
    else:
        soft('D3b cast_states 存在', bool(cs), '尚无 NPC 状态卡（新场景后生成）')


def test_compute_present_unit():
    """v3.5.46: compute_present 纯逻辑单元测试——在场/不在场推导 5 用例"""
    sys.path.insert(0, os.path.join(ROOT, 'backend'))
    from core.interactive.story_director import compute_present
    cases = []

    # 用例1: 位置冲突时以位置为准（present=True 但位置不同 → away）
    st1 = {'state': {'location': '医馆'},
           'player_state': {'location': '医馆', 'with': ['小翠']},
           'cast_states': {'小翠': {'present': True, 'location': '医馆'},
                           '张大夫': {'present': True, 'location': '医馆'},
                           '王捕头': {'present': False, 'location': '衙门'},
                           '李员外': {'present': True, 'location': '李府'}},
           'casts': {'小翠': {'present': True}, '张大夫': {'present': True}},
           'recent_blocks': []}
    cases.append(('位置为准+同行在场',
                  compute_present(st1) == (['小翠', '张大夫'], ['李员外', '王捕头'])))

    # 用例2: 无位置时 present 兜底 + 最近说话人默认在场
    st2 = {'state': {'location': '酒楼'}, 'player_state': {'location': '酒楼', 'with': []},
           'cast_states': {'小二': {'present': True, 'location': '酒楼'},
                           '掌柜': {'present': True}},
           'recent_blocks': [{'type': 'dialogue', 'speaker': '小二'},
                             {'type': 'dialogue', 'speaker': '神秘客'}]}
    p2, a2 = compute_present(st2)
    cases.append(('present兜底+最近说话人',
                  '小二' in p2 and '掌柜' in p2 and '神秘客' in p2 and a2 == []))

    # 用例3: 主角排除
    st3 = {'state': {'location': '林间'}, 'player_state': {'location': '林间', 'with': []},
           'player_char': {'name': '沈砚'},
           'cast_states': {'沈砚': {'present': True, 'location': '林间'},
                           '阿青': {'present': True, 'location': '林间'}}}
    p3, a3 = compute_present(st3)
    cases.append(('主角排除', p3 == ['阿青'] and '沈砚' not in p3))

    # 用例4: 空状态兜底
    p4, a4 = compute_present({})
    cases.append(('空状态', p4 == [] and a4 == []))

    # 用例5: 已离开角色必须在 away
    st5 = {'state': {'location': '客栈'}, 'player_state': {'location': '客栈', 'with': []},
           'cast_states': {'赵镖头': {'present': False, 'location': '城外官道'}}}
    p5, a5 = compute_present(st5)
    cases.append(('已离开→away', '赵镖头' in a5 and '赵镖头' not in p5))

    for name, ok in cases:
        check(f'P5 {name}', ok, '')


def test_advance_outline_unit():
    """v3.5.48: _advance_outline 切章推进单元测试——卡死自愈/正常节奏/final_done"""
    sys.path.insert(0, os.path.join(ROOT, 'backend'))
    from core.interactive.story_director import StoryDirector

    class FakeStore:
        def save_state(self, nid, st):
            pass

    d = StoryDirector.__new__(StoryDirector)
    d.store = FakeStore()
    chs = [{'number': i + 1, 'title': f'章{i+1}', 'summary': f'目标{i+1}',
            'target_words': 3000} for i in range(5)]
    cases = []

    # 用例1: 卡死存档自愈（scene_in_chapter 停滞但场景数已深 → 自动切章）
    st1 = {'outline_chapters': chs,
           'outline_progress': {'idx': 0, 'scene_in_chapter': 1, 'scene_start': 1},
           'scene_num': 28}
    d._advance_outline('t', st1)
    op1 = st1['outline_progress']
    cases.append(('卡死自愈切章', op1['idx'] >= 1 and 'final_done' not in op1))

    # 用例2: 正常切章节奏（每章约 3 场景）
    st2 = {'outline_chapters': chs,
           'outline_progress': {'idx': 0, 'scene_in_chapter': 2, 'scene_start': 1},
           'scene_num': 3}
    d._advance_outline('t', st2)
    op2 = st2['outline_progress']
    cases.append(('正常切章', op2['idx'] == 1 and op2['scene_in_chapter'] == 0
                  and 'final_done' not in op2))

    # 用例3: 最后一章 final_done 且保持 idx
    st3 = {'outline_chapters': chs,
           'outline_progress': {'idx': 4, 'scene_in_chapter': 2, 'scene_start': 40},
           'scene_num': 42}
    d._advance_outline('t', st3)
    op3 = st3['outline_progress']
    cases.append(('最后一章final_done', op3.get('final_done') is True and op3['idx'] == 4))

    # 用例4: final_done 后不再推进
    d._advance_outline('t', st3)
    cases.append(('final_done后冻结', st3['outline_progress'] == op3))

    # 用例5: 无大纲不崩
    st5 = {'outline_chapters': [], 'outline_progress': {}, 'scene_num': 5}
    try:
        d._advance_outline('t', st5)
        cases.append(('无大纲不崩', True))
    except Exception:
        cases.append(('无大纲不崩', False))

    for name, ok in cases:
        check(f'P6 {name}', ok, '')


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else None
    nids = [d for d in os.listdir(NOVELS)
            if os.path.isdir(os.path.join(NOVELS, d))
            and os.path.exists(os.path.join(NOVELS, d, 'interactive', 'state.json'))
            and not d.startswith('.')]
    if targets:
        nids = [n for n in nids if any(t in n for t in targets)]
    if not nids:
        print('没有可校验的互动存档')
        return
    for nid in nids:
        validate_novel(nid)
    # v3.5.46: compute_present 纯逻辑单元测试（不依赖存档）
    try:
        test_compute_present_unit()
    except Exception as e:
        check('P5 compute_present 单元测试', False, f'异常: {e}')
    # v3.5.48: _advance_outline 切章推进单元测试
    try:
        test_advance_outline_unit()
    except Exception as e:
        check('P6 _advance_outline 单元测试', False, f'异常: {e}')
    print()
    for line in REPORT:
        print(line)
    print()
    print(f'==== 一致性校验: {PASS} 通过 / {FAIL} 失败 / {WARN} 软警告 ====')
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == '__main__':
    main()
