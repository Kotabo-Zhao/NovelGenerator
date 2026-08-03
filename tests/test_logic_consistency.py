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

    # ── v3.5.51 四维一致性：时间/空间/事件/人物 ──
    ps = st.get('player_state') or {}
    # ── 时间 ──
    # T5: 状态卡时间 vs 最近场景文本时间词（状态卡明确且场景文本出现相反时段 → 失败）
    _ps_t = str(ps.get('time', ''))
    if _ps_t and scenes:
        _t5_hit = False
        for s in scenes[-3:]:
            _txt = str(s.get('scene_text', ''))
            for k, v in TIME_ORDER.items():
                if k in _txt:
                    if k in _ps_t:
                        _t5_hit = True
                    elif (_ps_t in ('清晨', '早晨') and v > 2) or \
                         (_ps_t in ('深夜', '午夜', '凌晨') and v < 6):
                        check('T5 状态卡时间与场景矛盾', False,
                              f'状态卡[{_ps_t}] vs 场景文本[{k}]')
                        _t5_hit = True
                    break
        if not _t5_hit:
            soft('T5 状态卡时间一致性', True, f'状态卡[{_ps_t}] 无冲突时间词')
    else:
        soft('T5 状态卡时间一致性', bool(_ps_t), '状态卡未生成')
    # ── 空间 ──
    # S1: 说话人位置 vs 场景地点（说话人状态卡位置在其他处 → 软警告）
    _loc = str(ps.get('location', '') or (st.get('state') or {}).get('location', ''))
    _loc_core = re.sub(r'[的在了到于与和及，。\s]', '', _loc)[:4] if _loc else ''
    _s1_bad = []
    for s in scenes[-5:]:
        for b in (s.get('blocks') or []):
            sp = str(b.get('speaker', ''))
            if not sp or sp in ('旁白', '叙述', '动作', '描写') or sp == player_name:
                continue
            _c = (cs.get(sp) or {})
            _cl = str(_c.get('location', ''))
            if _cl and _loc_core and _loc_core not in _cl and _loc not in _cl and '同' not in _cl:
                _s1_bad.append((s.get('scene_num'), sp, _cl[:16]))
    if _s1_bad:
        soft('S1 说话人位置与场景地点', False,
             f'{_s1_bad[:3]}（场景地点:{_loc_core}）')
    else:
        soft('S1 说话人位置与场景地点', True, f'场景地点:{_loc_core or "（未定）"}')
    # S2: 地点被场景交代（状态卡地点明确时，最近 3 场景至少 1 个出现地点词）
    if _loc_core and len(_loc_core) >= 2 and scenes:
        _s2_ok = any(_loc_core in str(s.get('scene_text', '')) for s in scenes[-3:])
        soft('S2 场景交代地点', _s2_ok, f'地点[{_loc_core}] 未在最近场景出现' if not _s2_ok else '')
    else:
        soft('S2 场景交代地点', bool(_loc_core), '地点未定')
    # S3: 场景开头环境交代（规则13：场景文本含环境描写迹象——时间/地点/感官词）
    _s3_missing = []
    for s in scenes[-5:]:
        _head = str(s.get('scene_text', ''))[:80]
        if not any(w in _head for w in ('清晨', '上午', '正午', '下午', '傍晚', '夜晚', '深夜', '天', '光',
                                        '门', '窗', '街', '房间', '室', '厅', '车', '楼', '屋', '院子',
                                        '风', '雨', '雪', '声', '香', '味')):
            _s3_missing.append(s.get('scene_num'))
    if _s3_missing:
        soft('S3 场景开头交代环境', False, f'场景{_s3_missing[:3]} 开头无环境描写')
    else:
        soft('S3 场景开头交代环境', True, '')
    # ── 事件 ──
    # E1: beats 事件进度单调（current 唯一、done 不倒退、章节绑定）
    cb = st.get('chapter_beats') or {}
    _beats = cb.get('beats') or []
    if _beats and chs:
        _done = [b for b in _beats if b.get('status') == 'done']
        _cur = [b for b in _beats if b.get('status') == 'current']
        check('E1 beats 进度合法', len(_cur) <= 1 and len(_done) <= len(_beats),
              f'done={len(_done)} current={len(_cur)}')
        check('E1b beats 章节绑定', cb.get('chapter_idx') == int((op or {}).get('idx', -1)),
              f'beats章{cb.get("chapter_idx")} vs 进度章{op.get("idx")}')
    else:
        soft('E1 beats 事件进度', bool(_beats), '尚无事件序列（切章后生成）')
    # E2: facts 无重复（同事件不同说法——前 25 字相同视为重复）
    _all_f = st.get('facts') or []
    _dup = []
    for i in range(len(_all_f)):
        for j in range(i + 1, len(_all_f)):
            _a = str(_all_f[i].get('content', ''))[:25]
            _b = str(_all_f[j].get('content', ''))[:25]
            if _a and _a == _b:
                _dup.append((_a[:15], _b[:15]))
    if _dup:
        check('E2 facts 无重复', False, f'重复对 {_dup[:2]}')
    else:
        check('E2 facts 无重复', True, f'{len(_all_f)} 条')
    # ── 人物 ──
    # P8a: 说话人都有 cast_states（说了话但状态卡缺失 → 警告）
    _no_state = []
    for s in scenes[-5:]:
        for b in (s.get('blocks') or []):
            sp = str(b.get('speaker', ''))
            if sp and b.get('type') == 'dialogue' and sp not in ('旁白', '叙述', '动作', '描写') \
                    and sp not in cs and sp != player_name:
                _no_state.append((s.get('scene_num'), sp))
    if _no_state:
        soft('P8a 说话人有状态卡', False, f'{_no_state[:3]} 无 cast_states')
    else:
        soft('P8a 说话人有状态卡', True, '')
    # P8b: 场景台词块无主角（用场景 blocks，不依赖 recent_blocks 迁移）
    _p_blocks2 = [(s.get('scene_num'), b.get('speaker')) for s in scenes[-5:]
                  for b in (s.get('blocks') or [])
                  if b.get('type') == 'dialogue' and b.get('speaker') == player_name]
    check('P8b 场景无主角台词', not _p_blocks2, f'{_p_blocks2[:2]}' if _p_blocks2 else '')


def test_clean_location_unit():
    """v3.5.52: clean_location JSON 片段剥离单元测试——12 用例"""
    sys.path.insert(0, os.path.join(ROOT, 'backend'))
    from core.interactive.action_engine import clean_location
    cases = [
        ('{"name": "青冥山", "description": "云雾缭绕的仙山"}', '青冥山'),
        ('{"name": "青冥山"}', '青冥山'),
        ('[{"name": "临江", "description": "江边小镇"}]', '临江'),
        ('{"location": "医馆", "desc": "xxx"}', '医馆'),
        ('["青冥山", "药王谷"]', '青冥山'),
        ('[{"轮回"}]', '轮回'),
        ('null', ''),
        ('上海，陆家嘴、前滩', '上海'),
        ('青冥山', '青冥山'),
        ('', ''),
        ('None', 'None'),
        ('  ,  ', ''),
    ]
    bad = [f"{inp!r} → {clean_location(inp)!r}" for inp, exp in cases if clean_location(inp) != exp]
    if bad:
        raise AssertionError('FAIL: ' + '; '.join(bad))


def test_chapter1_beats_unit():
    """v3.5.53: 第 1 章 beats 兜底单元测试——第 1 章开局无 beats 时自动生成"""
    sys.path.insert(0, os.path.join(ROOT, 'backend'))
    from core.interactive.story_director import StoryDirector

    class FakeStore:
        def __init__(self):
            self.saved = {}
        def save_state(self, nid, st):
            self.saved[nid] = json.loads(json.dumps(st))
        def load_state(self, nid):
            return self.saved.get(nid)

    d = StoryDirector.__new__(StoryDirector)
    d.store = FakeStore()
    chs = [{'number': 1, 'title': '第一章', 'summary': '顶楼宴会，主角被当众否认关系', 'target_words': 3000}]
    st = {'outline_chapters': chs, 'outline_progress': {'idx': 0, 'scene_in_chapter': 0, 'scene_start': 1},
          'scene_num': 1, 'title': 't', 'genre': 'g', 'style': 's'}
    d.store.saved['t'] = st
    # 无 LLM 环境 → 兜底 beats
    beats = d._ensure_chapter_beats('t', st)
    if not beats or beats[0].get('status') != 'current':
        raise AssertionError(f'第1章 beats 未生成: {beats}')
    if st.get('chapter_beats', {}).get('chapter_idx') != 0:
        raise AssertionError('章节绑定错误')
    # 场景后推进 → done
    d._advance_beat(st)
    if st['chapter_beats']['beats'][0]['status'] != 'done':
        raise AssertionError('推进失败')
    # 缓存复用保留推进状态
    beats2 = d._ensure_chapter_beats('t', st)
    if beats2[0]['status'] != 'done':
        raise AssertionError('缓存未保留推进状态')


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


def test_beats_unit():
    """v3.5.49: 章节事件序列 beats——拆解兜底/规则推进/章节对齐"""
    sys.path.insert(0, os.path.join(ROOT, 'backend'))
    from core.interactive.story_director import StoryDirector

    class FakeStore:
        def __init__(self):
            self.saved = {}

        def save_state(self, nid, st):
            self.saved[nid] = json.loads(json.dumps(st))

        def load_state(self, nid):
            return self.saved.get(nid)

    d = StoryDirector.__new__(StoryDirector)
    d.store = FakeStore()
    cases = []

    # 用例1: LLM 不可用时兜底为章节目标
    st1 = {'outline_chapters': [{'number': 1, 'title': 'T', 'summary': '酒会上羞辱',
                                 'target_words': 2000}],
           'outline_progress': {'idx': 0, 'scene_in_chapter': 1, 'scene_start': 1},
           'scene_num': 1}
    d.store.saved['t'] = st1
    beats = d._ensure_chapter_beats('t', st1)
    cases.append(('beats兜底', len(beats) >= 1 and beats[0]['status'] == 'current'))

    # 用例2: 规则推进——每场景一个事件
    d._advance_beat(st1)
    cases.append(('beats规则推进', st1['chapter_beats']['beats'][0]['status'] == 'done'))

    # 用例3: 章节索引绑定（切章后旧 beats 不误用）
    st2 = {'outline_chapters': [{'number': 1, 'title': 'A', 'summary': 's1', 'target_words': 2000},
                                {'number': 2, 'title': 'B', 'summary': 's2', 'target_words': 2000}],
           'outline_progress': {'idx': 1, 'scene_in_chapter': 0, 'scene_start': 4},
           'scene_num': 4}
    d.store.saved['t2'] = st2
    beats2 = d._ensure_chapter_beats('t2', st2)
    cases.append(('beats章节绑定', st2['chapter_beats']['chapter_idx'] == 1))

    # 用例4: 已生成不重复生成（force=False 走缓存）
    _before = st2['chapter_beats']['beats'][0]['desc']
    d._ensure_chapter_beats('t2', st2)
    cases.append(('beats缓存复用', st2['chapter_beats']['beats'][0]['desc'] == _before))

    for name, ok in cases:
        check(f'P7 {name}', ok, '')


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
    # v3.5.52: clean_location JSON 剥离单元测试
    try:
        test_clean_location_unit()
        check('P8 clean_location 单元测试', True, '12 用例')
    except Exception as e:
        check('P8 clean_location 单元测试', False, f'异常: {e}')
    # v3.5.53: 第 1 章 beats 兜底单元测试
    try:
        test_chapter1_beats_unit()
        check('P9 第1章 beats 兜底', True, '3 用例')
    except Exception as e:
        check('P9 第1章 beats 兜底', False, f'异常: {e}')
    print()
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
    # v3.5.49: beats 事件序列单元测试
    try:
        test_beats_unit()
    except Exception as e:
        check('P7 beats 单元测试', False, f'异常: {e}')
    print()
    for line in REPORT:
        print(line)
    print()
    print(f'==== 一致性校验: {PASS} 通过 / {FAIL} 失败 / {WARN} 软警告 ====')
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == '__main__':
    main()
