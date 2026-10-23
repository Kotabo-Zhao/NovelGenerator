"""Novel participation contract, cited canon context and validated action outcomes.

Intent recognition and prose generation do not commit outcomes. Only the resolver
below may apply a typed action; chapter events require a successful resolution.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import time
import uuid


def _natural(value):
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', str(value))]


def chapter_catalog(novel_dir, plan):
    chapters = []
    for volume in (plan.get('outline') or {}).get('volumes') or []:
        for chapter in volume.get('chapters') or []:
            if isinstance(chapter, dict):
                chapters.append({**chapter, 'volume': volume.get('title', '')})
    base = Path(novel_dir).resolve()
    files = sorted((base/'chapters').glob('*'), key=lambda p: _natural(p.name))
    files = [p for p in files if p.suffix.lower() in ('.md', '.txt') and p.is_file()
             and p.resolve().is_relative_to(base)]
    numbered = {}
    for path in files:
        match = re.search(r'\d+', path.stem)
        if match:
            numbered.setdefault(int(match[0]), path)
    result, used = [], set()
    for index, chapter in enumerate(chapters):
        number = chapter.get('number', index+1)
        try:
            path = numbered.get(int(number)) if numbered else (files[index] if index < len(files) else None)
        except (ValueError, TypeError):
            path = None
        if path:
            used.add(path)
        result.append({'number':number,'title':str(chapter.get('title') or f'第{number}章'),
                       'has_text':bool(path),'file':path.name if path else '',
                       'summary':str(chapter.get('summary') or '')[:600]})
    for path in files:
        if path in used:
            continue
        match = re.search(r'\d+',path.stem)
        number = int(match[0]) if match else len(result)+1
        # Multiple file formats for the same numbered chapter are alternatives,
        # not additional chapters which would move the canon boundary forward.
        if any(str(entry['number']) == str(number) for entry in result):
            continue
        result.append({'number':number,'title':path.stem,'has_text':True,'file':path.name,'summary':''})
    result.sort(key=lambda entry:_natural(entry['number']))
    for index, entry in enumerate(result):
        entry['index'] = index
    return result or [{'index':0,'number':1,'title':'设定开局','has_text':False,'file':'','summary':''}]


def _read_chapter(novel_dir, entry):
    if not entry.get('file'):
        return ''
    base = Path(novel_dir).resolve()
    path = (base/'chapters'/entry['file']).resolve()
    if not path.is_relative_to(base):
        raise ValueError('章节路径无效')
    with path.open(encoding='utf-8-sig') as stream:
        return stream.read(200000)


def initialize_participation(state, novel_dir, plan, chapter_index=0, mode='branch', goal=''):
    catalog = chapter_catalog(novel_dir, plan)
    if chapter_index < 0 or chapter_index >= len(catalog):
        raise ValueError('进入章节不存在，请重新选择')
    if mode not in ('branch', 'canon'):
        raise ValueError('剧情规则必须是 branch 或 canon')
    selected = catalog[chapter_index]
    # Past prose is evidence, never future lore. Keep a bounded paragraph index,
    # retrieved by current query rather than concatenating a whole novel per turn.
    evidence = []
    for entry in catalog[:chapter_index]:
        body = _read_chapter(novel_dir, entry)
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n|\n', body) if p.strip()]
        for paragraph in paragraphs[:200]:
            evidence.append({'chapter':entry['number'], 'source':entry['file'], 'text':paragraph[:1000]})
    body = _read_chapter(novel_dir, selected)
    profile = state.get('player_char') or {}
    personal = str(goal or profile.get('motivation') or profile.get('initial_attitude') or '先观察当前事件，决定自己如何参与')[:200]
    state['participation'] = {
        'version':1, 'session_id':uuid.uuid4().hex, 'mode':mode,
        'entry_index':chapter_index, 'chapter_index':chapter_index,
        'chapter_number':selected['number'], 'chapter_title':selected['title'],
        'source': 'chapter_text' if body else 'outline', 'source_file':selected['file'],
        'reference':body if body else selected['summary'],
        'opening':body[:900] if body else selected['summary'],
        'past_evidence':evidence[-1600:], 'goal':personal,
        'actions':[], 'completed_events':[], 'divergences':[],
        'knowledge_rule':'原著客观事实不等于每个角色都知道；玩家只知道自身经历与已获线索。',
        'entry_seed':{'status':'unconfirmed','note':'起始位置、随身物品及人物在场需由正文证据确认；没有依据的属性不凭空添加'},
    }
    state['state']['objective'] = personal
    existing = {str(ch.get('number',i+1)):ch for i,ch in enumerate(state.get('outline_chapters') or [])}
    state['outline_chapters'] = [existing.get(str(entry['number']),
        {'number':entry['number'],'title':entry['title'],'summary':entry['summary']}) for entry in catalog]
    state['outline_progress'] = {'idx':chapter_index,'scene_in_chapter':0,'scene_start':1,'final_done':False}
    state.pop('chapter_beats', None)
    state.pop('anchor_triggered', None)
    return state['participation']


def canon_context(state, query='', limit=9000, observer=None):
    contract = state.get('participation') or {}
    if not contract:
        return ''
    query = str(query) + ' ' + str((current_event(state) or {}).get('desc',''))
    terms = set(re.findall(r'[\u4e00-\u9fff]{2}|[a-zA-Z]{3,}', query))
    evidence = contract.get('past_evidence') or []
    ranked = sorted(enumerate(evidence), key=lambda pair: (
        sum(term in pair[1]['text'] for term in terms), pair[0]), reverse=True)
    facts = '\n'.join(f"[原著第{item['chapter']}章/{item['source']}] {item['text']}" for _,item in ranked[:7])[:max(600,limit//3)]
    previous = sorted(enumerate(contract.get('reference_history') or []), key=lambda pair: (
        sum(term in pair[1]['text'] for term in terms), pair[0]), reverse=True)[:3]
    previous_reference = '\n'.join(f"[已游玩章节的原著参考/第{item['chapter']}章] {item['text']}" for _, item in previous)[:1400]
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n|\n',contract.get('reference','')) if p.strip()]
    passages = sorted(enumerate(paragraphs), key=lambda pair:(
        sum(term in pair[1] for term in terms), -pair[0]),reverse=True)[:5]
    current_reference = '\n'.join(f"[本章参考段{index+1}] {text[:1000]}" for index,text in sorted(passages))
    mode = '遵循原著主要事件，拒绝会改写结局的执行并说明原因；普通行动仍可自由进行' if contract.get('mode') == 'canon' else '允许玩家改变当前及后续事件；原著后续走向只是参考，不能强行回轨'
    actions = contract.get('actions', [])
    divergences = contract.get('divergences', [])
    if observer:
        actions = [a for a in actions if observer in a.get('witnesses', [])]
        known = {a['id'] for a in actions}
        divergences = [d for d in divergences if d.get('action_id') in known]
    return (f"## 小说参与契约\n玩家是{(state.get('player_char') or {}).get('name','你')}；AI只负责环境与NPC，不能替玩家决定或完成关键行动。\n"
            f"进入第{contract.get('chapter_number')}章《{contract.get('chapter_title')}》，依据：{contract.get('source_file') or '仅大纲，未提供正文'}。\n"
            f"规则：{mode}\n玩家目标：{contract.get('goal')}\n{contract.get('knowledge_rule')}\n"
            f"本局已发生变化（优先于原著参考）：{json.dumps(divergences[-8:],ensure_ascii=False)[:800]}\n"
            f"最近执行结果（优先于参考叙事）：{json.dumps(actions[-3:],ensure_ascii=False)[:1600]}\n"
            f"已发生的原著证据（未检索到不等于不存在）：\n{facts or '无前章正文证据，不能编造前史'}\n"
            f"已游玩章节的原著参考（不代表本局发生；本局以执行记录为准）：\n{previous_reference}\n"
            f"当前章节参考（尚未发生的玩家行为不能当成既成事实）：\n{current_reference[:max(800,limit//3)]}\n"
            "以已提交的行动结果为权威：失败、拒绝、未决不得叙述成成功；未来章节内容不在本轮依据内。")[:limit]


def request_kind(text):
    """A refusal/conditional clause must never become an affirmative action."""
    clauses = [c.strip() for c in re.split(r'[，,。；;！!\n]', str(text)) if c.strip()]
    if re.search(r'^(?:如果|假如|假设|要是)|(?:能不能|是否可以|可不可以|要不要)|[?？]', str(text).strip()):
        return 'conditional', str(text)
    positive = []
    negative = []
    for clause in clauses:
        if re.match(r'^(?:我)?(?:没有|并未|从未|未曾|不会|不打算|不想|不愿|不要)', clause) or re.search(r'拒绝|取消|放弃|不(?:去|要|想|会|愿|打算|交|给|使用|拿|杀|救|出发|能|攻击)|(?:没有|没|未曾|并未)(?:交|给|杀|救|去|使用|拿|攻击)|别(?:去|动|交)', clause):
            negative.append(clause)
        else:
            positive.append(clause)
    return ('affirmative', positive[-1]) if positive else ('refused' if negative else 'unknown', str(text))


def conditions_met(state, conditions):
    """Only recognized, explicit state predicates authorize event completion."""
    from .story_director import _cond_match
    for condition in conditions or []:
        if not isinstance(condition, dict) or not _cond_match(
            condition.get('field'), condition.get('op'), condition.get('value'), state,
            state.get('state') or {}, int(state.get('tension',0)), condition.get('target')):
            return False
    return True


def current_event(state):
    return next((b for b in (state.get('chapter_beats') or {}).get('beats') or []
                 if b.get('status') == 'current'), None)


def _typed(text, state, action):
    names = list((state.get('casts') or {}).keys())
    item_names = [str(x) for x in (state.get('state') or {}).get('inventory') or []]
    locations = list((state.get('world') or {}).get('locations') or {})
    target = next((n for n in sorted(names,key=len,reverse=True) if n in text), '')
    if not target and re.search(r'你|他|她',text):
        target = str(action.get('recipient') or '')
    item = next((n for n in sorted(item_names,key=len,reverse=True) if n in text), '')
    if re.search(r'交出|交给|递给|递上|归还|还给|赠送|送给|(?:把|将).+给', text):
        missing = re.search(r'(?:把|将)(.{1,24}?)(?:交给|递给|归还|还给|送给|给)|(?:交出|递上|归还)(.{1,24})', text)
        item = item or (next((g for g in missing.groups() if g), '').strip() if missing else '')
        return {'verb':'give','item':item,'target':target}
    if re.search(r'捡起|拾起|拿起|取走|拿走',text):
        here = (state.get('world') or {}).get('location','')
        available = (((state.get('world') or {}).get('locations') or {}).get(here) or {}).get('items') or []
        found = next((str(n) for n in sorted(available,key=lambda n:len(str(n)),reverse=True) if str(n) in text),'')
        return {'verb':'take','item':found,'target':''}
    if re.search(r'使用|服下|喝下|吃下|服用|用.+(?:打开|治疗)', text):
        return {'verb':'use','item':item or str(action.get('target') or ''),'target':target}
    if re.search(r'救下|救出|救治|营救|救人|治疗', text):
        return {'verb':'rescue','item':item,'target':target}
    if re.search(r'杀|攻击|刺向|拔剑|打倒|击败|战斗', text):
        return {'verb':'combat','item':item,'target':target}
    if re.search(r'观察|查看|调查|搜查|搜寻|检查|翻阅|寻找', text):
        return {'verb':'observe','item':item,'target':target}
    if action.get('intent') == 'travel':
        return {'verb':'travel','item':'','target':str(action.get('target') or '')}
    if re.search(r'前往|走到|走进|出发|回家|^(?:我)?(?:要|想|现在|马上|改)?去', text):
        from .world_state import resolve_travel_target
        place, _ = resolve_travel_target(text, state)
        return {'verb':'travel','item':'','target':place or next((n for n in locations if n in text), '')}
    return {'verb':'interact','item':item,'target':target}


def resolve_action(state, action, adjudicate=None):
    """Resolve to success/failure/refused/pending, returning a bounded audit record.

    Model verdicts cannot add arbitrary flags/items/relationships. Concrete effects
    are applied by typed rules; authored event effects are committed separately.
    """
    original = str(action.get('input') or action.get('summary') or '')[:500]
    polarity, text = request_kind(original)
    typed = _typed(text,state,action)
    result = {'id':uuid.uuid4().hex, 'input':original, **typed, 'status':'pending',
              'reason':'需要更多依据，尚未确认执行成功', 'changes':[], 'event_completed':False,
              'time':time.strftime('%Y-%m-%d %H:%M:%S')}
    s = state.setdefault('state', {})
    inv = s.setdefault('inventory', [])
    event = current_event(state)
    if event:
        result['event_id'] = event.get('id')
        result['event_chapter'] = (state.get('outline_progress') or {}).get('idx', 0)
    event_text = str((event or {}).get('desc',''))
    event_related = (typed['verb'] == 'give' and typed['item'] and typed['item'] in event_text) or (typed['verb'] in ('rescue','combat') and typed['target'] and typed['target'] in event_text)
    result['conditions_satisfied'] = conditions_met(state,(event.get('trigger') or {}).get('conditions')) if event else True
    if action.get('blocked'):
        result.update(status='failure', reason=str(action.get('reason') or '不符合当前世界规则'))
    elif polarity != 'affirmative':
        result.update(status='refused' if polarity == 'refused' else 'pending',
                      reason='玩家拒绝执行，未改变事件结果' if polarity == 'refused' else '这是询问或条件设想，尚未执行')
        if (polarity == 'refused' and '拒绝当前事件' in original and event
                and (state.get('participation') or {}).get('mode') == 'branch'):
            event['status'] = 'declined'
            beats = state['chapter_beats']['beats']
            index = beats.index(event)
            if index + 1 < len(beats):
                beats[index+1]['status'] = 'current'
            change = '事件分支：拒绝「' + str(event.get('desc','')) + '」，未领取完成奖励'
            result.update(reason='已选择拒绝该事件，后续剧情必须承接拒绝的后果', changes=[change])
    elif typed['verb'] == 'travel':
        from .world_state import ensure_world, execute_travel, fulfill_promises_at
        world = ensure_world(state)
        target = typed['target']
        known = target in (world.get('locations') or {})
        if not target or (not known and not action.get('confirmed')):
            result.update(reason='目标地点不明确或尚未确认新地点')
        elif target == world.get('location'):
            result.update(status='success',reason='你已经在这里，无需再次移动')
        else:
            changes, ok = execute_travel(state,target,register_new=bool(action.get('confirmed')))
            if ok:
                if not state.get('participation'):
                    changes += fulfill_promises_at(state,target)
                else:
                    for promise in state.get('pending_promises') or []:
                        if promise.get('status') == 'pending' and promise.get('location') == target:
                            promise['arrived'] = True
                            changes.append('已抵达约定地点，履约仍需实际行动')
                result.update(status='success',reason='移动已执行',changes=changes)
            else:
                result.update(status='failure',reason='未能到达目标地点')
    elif typed['verb'] in ('give','use') and (not typed['item'] or typed['item'] not in inv):
        result.update(status='failure',reason='没有持有要交付或使用的物品')
    else:
        from .story_director import compute_present
        present, _ = compute_present(state)
        authoritative = (state.get('world') or {}).get('chars') or {}
        here = (state.get('world') or {}).get('location') or s.get('location')
        present = [name for name in present if (state.get('casts') or {}).get(name,{}).get('alive') is not False
                   and authoritative.get(name,{}).get('present') is not False
                   and (not authoritative.get(name,{}).get('location') or authoritative[name]['location'] == here)]
        if typed['verb'] in ('give','rescue','combat') and not typed['target']:
            result.update(reason='请明确行动对象；尚未对任何角色执行')
        elif typed['target'] and typed['target'] not in present:
            result.update(status='failure',reason='目标角色不在场，不能执行该行动')
        elif typed['verb'] == 'rescue' and typed['target'] and (
                (state.get('cast_states') or {}).get(typed['target'],{}).get('condition') in ('死亡','已死')
                or (state.get('casts') or {}).get(typed['target'],{}).get('alive') is False):
            result.update(status='failure',reason='目标已经死亡，普通救援不能使其复活')
        elif event_related and not result['conditions_satisfied']:
            result.update(reason='当前事件前置条件尚未满足；可以先观察或采取其他行动')
            if typed['verb'] == 'observe':
                result.update(status='success',reason='观察当前环境，不代替完成事件')
        elif typed['verb'] == 'observe':
            result.update(status='success',reason='观察当前环境，不代替完成事件')
        elif typed['verb'] == 'take' and not typed['item']:
            result.update(status='failure',reason='当前地点没有已确认的这件物品，不能凭空获得')
        elif typed['verb'] == 'give' and typed['target']:
            inv.remove(typed['item'])
            recipient = state['casts'][typed['target']].setdefault('inventory',[])
            if typed['item'] not in recipient:
                recipient.append(typed['item'])
            result.update(status='success',reason='已将持有物品交付给在场角色',
                          changes=[f"交付：{typed['item']} → {typed['target']}"])
        elif adjudicate:
            try:
                verdict = adjudicate(state, {**result,'event':event})
            except Exception:
                verdict = {}
            if isinstance(verdict, dict) and verdict.get('status') in ('success','failure','pending') and verdict.get('reason'):
                result.update(status=verdict['status'],reason=str(verdict['reason'])[:300],
                              event_completed=verdict.get('event_completed') is True)
            if result['status'] == 'success':
                contract = state.get('participation') or {}
                disruptive = typed['verb'] in ('rescue','combat')
                quote = str(verdict.get('canon_quote') or '')
                compatible = verdict.get('canon_compatible') is True and len(quote) >= 4 and quote in contract.get('reference','')
                if disruptive and contract.get('mode') == 'canon' and not compatible:
                    result.update(status='pending',reason='遵循原著模式下，尚无正文证据证明该结果符合原著；未改变人物状态',event_completed=False)
                elif typed['verb'] == 'give':
                    if not typed['target']:
                        result.update(status='pending',reason='请明确交付给哪位在场角色',event_completed=False)
                    else:
                        inv.remove(typed['item'])
                        recipient = state['casts'][typed['target']].setdefault('inventory',[])
                        if typed['item'] not in recipient:
                            recipient.append(typed['item'])
                        result['changes'].append(f"交付：{typed['item']} → {typed['target']}")
                elif typed['verb'] == 'rescue' and typed['target']:
                    char = state['casts'][typed['target']]
                    char['condition'] = '获救'
                    state.setdefault('cast_states',{}).setdefault(typed['target'],{})['condition'] = '获救'
                    result['changes'].append(f"{typed['target']}已获救")
                elif typed['verb'] == 'take':
                    here = (state.get('world') or {}).get('location','')
                    available = state['world']['locations'][here]['items']
                    available.remove(typed['item'])
                    if typed['item'] not in inv:
                        inv.append(typed['item'])
                    result['changes'].append('取得物品：'+typed['item'])
                elif typed['verb'] == 'use' and re.search(r'服下|喝下|吃下|服用|饮下',text):
                    inv.remove(typed['item'])
                    result['changes'].append('消耗物品：'+typed['item'])
                elif typed['verb'] == 'combat' and typed['target']:
                    effect = verdict.get('effect')
                    if effect not in ('injured','defeated','dead'):
                        result.update(status='pending',reason='战斗结果缺少明确裁定，尚未改变目标状态',event_completed=False)
                    else:
                        condition = {'injured':'受伤','defeated':'失去战斗能力','dead':'死亡'}[effect]
                        state['casts'][typed['target']]['condition'] = condition
                        state.setdefault('cast_states',{}).setdefault(typed['target'],{})['condition'] = condition
                        if effect == 'dead':
                            state['casts'][typed['target']]['alive'] = False
                            state['casts'][typed['target']]['present'] = False
                            world_char = (state.get('world') or {}).get('chars',{}).get(typed['target'])
                            if world_char:
                                world_char.update(present=False,condition=condition)
                        result['changes'].append(f"{typed['target']}：{condition}")
    result['event_completed'] = result['event_completed'] and result['status'] == 'success'
    action['resolution'] = copy.deepcopy(result)
    action['status'] = result['status']
    if result['status'] != 'success':
        action['end_chat'] = False
    # Preserve original input, rather than a classifier's affirmative paraphrase.
    state['last_action'] = {**copy.deepcopy(result),'type':typed['verb'],'summary':original}
    ledger = state.setdefault('action_ledger',[])
    ledger.append(copy.deepcopy(result)); del ledger[:-200]
    contract = state.get('participation')
    if contract:
        contract.setdefault('actions',[]).append(copy.deepcopy(result)); del contract['actions'][:-30]
        if result['changes'] and typed['verb'] != 'travel':
            contract.setdefault('divergences',[]).append({'action_id':result['id'],'changes':result['changes']})
            del contract['divergences'][:-100]
    return result


def event_matches(state, action):
    event = current_event(state)
    resolution = action.get('resolution') or {}
    if not event or resolution.get('status') != 'success':
        return False
    if resolution.get('event_id') != event.get('id') or resolution.get('event_chapter') != (state.get('outline_progress') or {}).get('idx', 0):
        return False
    if any(e.get('action_id') == resolution.get('id') for e in
           (state.get('participation') or {}).get('completed_events',[])):
        return False
    if not resolution.get('conditions_satisfied', False):
        return False
    if request_kind(resolution.get('input',''))[0] != 'affirmative':
        return False
    text = str(event.get('desc') or '')
    # Mentioning a destination or another recipient is not performing this event.
    for place in ((state.get('world') or {}).get('locations') or {}):
        if place in text and (state.get('world') or {}).get('location') != place:
            return False
    for name in state.get('casts') or {}:
        if name in text and resolution.get('target') and resolution['target'] != name:
            return False
    verb = resolution.get('verb')
    if re.search(r'交出|交给|递给|递上|归还|交付|送信', text):
        return verb == 'give' and bool(resolution.get('item')) and resolution['item'] in text and bool(resolution.get('changes'))
    if re.search(r'救下|救出|救治|营救|救人', text):
        return verb == 'rescue' and bool(resolution.get('target')) and resolution['target'] in text and bool(resolution.get('changes'))
    if re.search(r'前往|到达|进入|抵达', text) and not re.search(r'接头|交|救|战|击败',text):
        return verb == 'travel' and bool(resolution.get('target')) and resolution['target'] in text
    return bool(resolution.get('event_completed')) and verb != 'observe'


def advance_canon_chapter(state, novels_dir, novel_id):
    contract = state.get('participation') or {}
    base = Path(novels_dir)/novel_id
    try:
        plan = json.loads((base/'plan.json').read_text(encoding='utf-8-sig'))
        catalog = chapter_catalog(base,plan)
        index = int((state.get('outline_progress') or {}).get('idx',0)) + 1
        if index >= len(catalog):
            return True
        entry = catalog[index]
        body = _read_chapter(base,entry)
        # Keep the original entry history separate from reference-only chapters
        # traversed in this playthrough: their canonical endings may have changed.
        history = contract.setdefault('reference_history', [])
        if contract.get('reference'):
            for paragraph in re.split(r'\n\s*\n|\n', contract['reference']):
                if paragraph.strip():
                    history.append({'chapter':contract.get('chapter_number'), 'source':contract.get('source_file',''),
                                    'text':paragraph.strip()[:1000], 'reference_only':True})
            del history[:-800]
        contract.update(chapter_index=index,chapter_number=entry['number'],chapter_title=entry['title'],
                        source='chapter_text' if body else 'outline',source_file=entry['file'],
                        reference=body if body else entry['summary'],
                        opening=body[:900] if body else entry['summary'])
        contract.pop('source_warning', None)
        return True
    except (OSError, ValueError, TypeError):
        # Retain the last verified context on missing/corrupt source, never invent it.
        contract['source_warning'] = '下一章原著依据暂不可读，保留已确认的本局事实'
        return False


def participation_view(state):
    contract = state.get('participation') or {}
    event = current_event(state)
    return {'enabled':bool(contract), 'chapter':contract.get('chapter_number'),
            'title':contract.get('chapter_title',''), 'source':contract.get('source',''),
            'mode':contract.get('mode','branch'), 'goal':contract.get('goal',''),
            'opening':contract.get('opening',''), 'event':event.get('desc','') if event else '',
            'event_state':'current' if event else ('resolved' if (state.get('chapter_beats') or {}).get('beats') else 'preparing'),
            'entry_seed':contract.get('entry_seed',{}),
            'source_warning':contract.get('source_warning',''),
            'event_warning':contract.get('event_warning',''),
            'event_source':event.get('source_quote','') if event else '',
            'memories':[{'name':name, 'count':len(entries) + len((state.get('memory_archive') or {}).get(name,[])),
                         'recent':entries[-3:][::-1]} for name,entries in (state.get('memories') or {}).items()],
            'last_action':(state.get('action_ledger') or [{}])[-1],
            'completed_events':contract.get('completed_events',[]) [-10:],
            'divergences':contract.get('divergences',[])[-10:]}


def apply_entry_seed(state, candidate):
    """Accept a model's entry-state proposal only with literal source quotations.

    Inventory needs a quotation tying ownership to this player. Unsupported fields
    (money, powers, rewards, future knowledge) are never applied here.
    """
    contract = state['participation']
    player = (state.get('player_char') or {}).get('name','')
    reference = contract.get('opening','') + '\n' + '\n'.join(
        e['text'] for e in (contract.get('past_evidence') or [])[-8:])
    def supported(item, value, owner=False):
        quote = str(item.get('quote') or '') if isinstance(item,dict) else ''
        return bool(value and 4 <= len(quote) <= 500 and quote in reference
                    and value in quote and (not owner or (player and player in quote)))
    changes = []
    location = candidate.get('location') or {}
    value = str(location.get('value') or '')[:40] if isinstance(location,dict) else ''
    if supported(location,value,True):
        from .world_state import ensure_world, recompute_presence
        w = ensure_world(state)
        w.setdefault('locations',{}).setdefault(value,{'desc':'原著开局地点','connected':[],'chars':[]})
        w['location'] = value
        state['state']['location'] = value
        state.setdefault('player_state',{})['location'] = value
        recompute_presence(state,w)
        changes.append('位置依据：'+value)
    for item in (candidate.get('inventory') or [])[:20]:
        value = str(item.get('value') or '')[:60] if isinstance(item,dict) else ''
        if supported(item,value,True) and value not in state['state']['inventory']:
            state['state']['inventory'].append(value)
            changes.append('持有依据：'+value)
    for item in (candidate.get('present') or [])[:20]:
        name = str(item.get('name') or '') if isinstance(item,dict) else ''
        if supported(item,name) and name in state.get('casts',{}):
            state['casts'][name]['present'] = True
            here = state['state']['location']
            state.setdefault('world',{}).setdefault('chars',{}).setdefault(name,{}).update(present=True,location=here)
            state.setdefault('cast_states',{}).setdefault(name,{}).update(present=True,location=here)
            changes.append('在场依据：'+name)
    here = (state.get('world') or {}).get('location','')
    for item in (candidate.get('items') or [])[:20]:
        value = str(item.get('value') or '')[:60] if isinstance(item,dict) else ''
        if supported(item,value) and here in ((state.get('world') or {}).get('locations') or {}):
            items = state['world']['locations'][here].setdefault('items',[])
            if value not in items:
                items.append(value)
                changes.append('场景物品依据：'+value)
    contract['entry_seed'] = {'status':'verified' if changes else 'unconfirmed',
        'note':'；'.join(changes) if changes else '未找到可验证的起始状态证据，沿用已有状态；可先观察场景确认处境'}
    return changes
