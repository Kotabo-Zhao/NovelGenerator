# -*- coding: utf-8 -*-
"""估算互动存档 prompt 注入膨胀量级"""
import json, os

novels_dir = os.path.join(os.path.dirname(__file__), '..', 'novels')
cands = []
for d in os.listdir(novels_dir):
    p = os.path.join(novels_dir, d, 'interactive', 'state.json')
    if os.path.exists(p):
        try:
            st = json.load(open(p, encoding='utf-8'))
            cands.append((st.get('scene_num', 0), d, st))
        except Exception:
            pass
cands.sort(reverse=True)
for sn, nid, st in cands[:5]:
    cs = st.get('cast_states') or {}
    casts = st.get('casts') or {}
    facts = st.get('facts') or []
    rb = st.get('recent_blocks') or []
    cs_chars = 0
    for n, c in cs.items():
        cs_chars += len('{}[位置{} 情绪{} 立场{} 身体{} 知道[{}] 想[{}]]'.format(
            n, c.get('location', ''), c.get('mood', ''), c.get('stance', ''),
            c.get('condition', ''), ','.join(c.get('knows') or []), c.get('agenda', '')))
    cast_chars = 0
    for n, c in casts.items():
        prof = (c or {}).get('profile', {})
        brief = []
        for d in prof.get('expression_dna', [])[:2]:
            brief.append(str(d.get('name', d))[:60] if isinstance(d, dict) else str(d)[:60])
        for a in prof.get('anti_patterns', [])[:2]:
            brief.append('禁:' + str(a.get('pattern', a) if isinstance(a, dict) else a)[:60])
        cast_chars += len(n) + 5 + len('；'.join(brief))
    print('{:24s} 场景{:3d} | cast_states {:2d}角色≈{:5d}字 | casts人设 {:2d}角色≈{:5d}字 | facts {:2d} | blocks {:3d}'.format(
        nid[:22], sn, len(cs), cs_chars, len(casts), cast_chars, len(facts), len(rb)))
