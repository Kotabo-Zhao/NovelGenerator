import asyncio
import copy
import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.interactive.action_engine import ActionEngine
from core.interactive.interact_store import InteractStore, new_state
from core.interactive.participation import (
    initialize_participation, canon_context, resolve_action, event_matches,
    advance_canon_chapter, chapter_catalog, apply_entry_seed,
)
from core.interactive.story_director import (
    StoryDirector, beat_advance_by_action, chapter_complete, anchor_trigger_check,
)


def world():
    state = new_state('book','旧城','','')
    state['state'].update(location='码头', inventory=['密信'])
    state['player_char'] = {'name':'林舟','motivation':'寻找同伴'}
    state['casts'] = {'掌柜':{'present':True,'profile':{}},'顾川':{'present':True,'profile':{}}}
    state['world'].update(location='码头', locations={
        '码头':{'connected':['城门'],'chars':['掌柜','顾川']},
        '城门':{'connected':['码头'],'chars':[]}},
        chars={'掌柜':{'present':True,'location':'码头'},'顾川':{'present':True,'location':'码头'}})
    state['chapter_beats'] = {'chapter_idx':0,'beats':[{'id':1,'desc':'在码头交出密信给掌柜',
        'status':'current','trigger':{'state_output':{'flags':['密信已交付']}}}]}
    state['participation'] = {'version':1,'mode':'branch','chapter_number':1,'actions':[],'divergences':[]}
    return state


def yes(state, request):
    return {'status':'success','reason':'目标在场且证据支持本次尝试成功','event_completed':True,'effect':'defeated'}


@pytest.mark.parametrize('text', ['我拒绝去码头，也不交出密信','我不交出密信','如果交出密信会怎么样','能不能交出密信'])
def test_refusal_or_question_cannot_complete_or_award(text):
    state = world(); action={'input':text,'summary':'交出密信','intent':'travel','target':'城门'}
    result=resolve_action(state,action,yes)
    assert result['status'] in ('refused','pending')
    assert not beat_advance_by_action(state,action)
    assert state['chapter_beats']['beats'][0]['status']=='current'
    assert state['state']['inventory']==['密信'] and state['state']['flags']==[]
    assert state['world']['location']=='码头'


def test_no_item_means_no_transfer_even_if_model_claims_success():
    state=world();state['state']['inventory']=[]
    action={'input':'我把密信交给掌柜'}
    assert resolve_action(state,action,yes)['status']=='failure'
    assert not event_matches(state,action)


def test_destination_alone_is_not_delivery():
    state=world();state['world']['location']='城门';state['state']['location']='城门'
    action={'intent':'travel','target':'码头','input':'我去码头'}
    assert resolve_action(state,action,yes)['status']=='success'
    assert not beat_advance_by_action(state,action)
    assert state['chapter_beats']['beats'][0]['status']=='current'


def test_actual_transfer_consumes_item_has_causal_event_id_and_cannot_repeat():
    state=world();action={'input':'我把密信交给掌柜'}
    result=resolve_action(state,action,yes)
    assert result['status']=='success' and state['state']['inventory']==[]
    assert state['casts']['掌柜']['inventory']==['密信']
    assert beat_advance_by_action(state,action)
    assert state['chapter_beats']['beats'][0]['status']=='done'
    assert state['state']['flags']==['密信已交付']
    assert state['participation']['completed_events'][0]['action_id']==result['id']
    assert not beat_advance_by_action(state,action)
    assert resolve_action(state,{'input':'我把密信交给掌柜'},yes)['status']=='failure'


def test_wrong_recipient_does_not_complete_named_event():
    state=world();action={'input':'我把密信交给顾川'}
    assert resolve_action(state,action,yes)['status']=='success'
    assert not beat_advance_by_action(state,action)


def test_absent_target_cannot_receive_item():
    state=world();state['casts']['掌柜']['present']=False
    state['world']['chars']['掌柜'].update(present=False,location='城门')
    action={'input':'我把密信交给掌柜'}
    assert resolve_action(state,action,yes)['status']=='failure'
    assert state['state']['inventory']==['密信']


def test_unmet_precondition_does_not_commit_model_verdict():
    state=world();state['chapter_beats']['beats'][0]['trigger']['conditions']=[{'field':'flag','op':'has','value':'核验身份'}]
    action={'input':'我把密信交给掌柜'}
    assert resolve_action(state,action,yes)['status']=='pending'
    assert state['state']['inventory']==['密信'] and not beat_advance_by_action(state,action)


def test_condition_checked_before_item_is_consumed():
    state=world();state['chapter_beats']['beats'][0]['trigger']['conditions']=[{'field':'inventory','op':'has','value':'密信'}]
    action={'input':'我把密信交给掌柜'}
    assert resolve_action(state,action,yes)['status']=='success'
    assert beat_advance_by_action(state,action)


def test_failed_move_never_fulfills_a_promise_or_completes_beat():
    state=world();state['pending_promises']=[{'status':'pending','location':'北山','what':'交信','who':'掌柜'}]
    action={'intent':'travel','target':'北山','input':'去北山'}
    assert resolve_action(state,action,yes)['status']=='pending'
    assert state['pending_promises'][0]['status']=='pending'
    assert not beat_advance_by_action(state,action)


def test_arrival_marks_presence_not_fulfillment():
    state=world();state['pending_promises']=[{'status':'pending','location':'城门','what':'交信','who':'掌柜'}]
    action={'intent':'travel','target':'城门','input':'去城门'}
    assert resolve_action(state,action,yes)['status']=='success'
    assert state['pending_promises'][0]['status']=='pending'
    assert state['pending_promises'][0]['arrived']


def test_branch_rescue_survives_save_reload_and_appears_in_future_context(tmp_path):
    state=world();state['chapter_beats']['beats'][0]['desc']='救下顾川'
    action={'input':'我救下顾川'}
    assert resolve_action(state,action,yes)['status']=='success'
    assert beat_advance_by_action(state,action)
    store=InteractStore(str(tmp_path));store.save_state('book',state)
    loaded=store.load_state('book')
    assert loaded['cast_states']['顾川']['condition']=='获救'
    assert '顾川已获救' in canon_context(loaded)


def test_dead_character_not_resurrected_by_ordinary_rescue():
    state=world();state['casts']['顾川']['alive']=False
    assert resolve_action(state,{'input':'我救下顾川'},yes)['status']=='failure'


def test_conservative_mode_blocks_key_rewrite_and_model_outage_stays_pending():
    state=world();state['participation']['mode']='canon'
    assert resolve_action(state,{'input':'我救下顾川'},yes)['status']=='failure'
    assert 'condition' not in state['casts']['顾川']
    state['participation']['mode']='branch'
    assert resolve_action(state,{'input':'我说服顾川'},lambda *_:{} )['status']=='pending'


def test_branch_decline_is_not_success_or_reward_but_can_move_to_next_event():
    state=world()
    result=resolve_action(state,{'input':'我拒绝当前事件'},yes)
    assert result['status']=='refused' and result['changes']
    assert state['chapter_beats']['beats'][0]['status']=='declined'
    assert state['state']['flags']==[] and state['state']['inventory']==['密信']
    state['outline_progress']={'idx':0,'scene_in_chapter':2}
    assert chapter_complete(state)
    assert state['participation']['divergences']


def test_canon_events_do_not_finish_on_timeout_or_scene_count():
    state=world();state['outline_progress']={'idx':0,'scene_in_chapter':40,'scene_start':1};state['scene_num']=40
    state['chapter_beats']['beats'][0]['trigger'].update(conditions=[],timeout_scenes=1)
    assert anchor_trigger_check(state) is None
    sd=StoryDirector(None,'mock',MagicMock());sd._advance_beat(state)
    assert state['chapter_beats']['beats'][0]['status']=='current'
    assert not chapter_complete(state)


def test_cited_canon_reads_past_and_current_but_never_future(tmp_path):
    base=tmp_path/'book';(base/'chapters').mkdir(parents=True)
    for index,text in enumerate(['前章已发生：顾川持有旧钥匙。','当前事件：码头有人求救。','未来秘密：幕后真凶是掌柜。'],1):
        (base/'chapters'/f'{index:03}.md').write_text(text,encoding='utf-8')
    plan={'outline':{'volumes':[{'chapters':[{'number':i,'title':f'章节{i}','summary':'章目标'} for i in range(1,4)]}]}}
    state=world();initialize_participation(state,base,plan,1,'branch','找到顾川')
    context=canon_context(state,'顾川')
    assert '前章已发生' in context and '当前事件' in context
    assert '未来秘密' not in context
    assert '001.md' in context and '002.md' in context
    assert state['outline_progress']['idx']==1
    assert state['state']['objective']=='找到顾川'
    state['outline_progress']['idx']=1
    advance_canon_chapter(state,tmp_path,'book')  # Missing plan retains verified context.
    assert state['participation']['source_warning']


def test_outline_only_is_labeled_and_bad_entry_is_rejected(tmp_path):
    state=world();initialize_participation(state,tmp_path,{},0)
    assert state['participation']['source']=='outline'
    assert '仅大纲' in canon_context(state)
    with pytest.raises(ValueError):
        initialize_participation(state,tmp_path,{},8)


def test_real_action_engine_uses_new_resolver_and_persists_audit(tmp_path):
    store=InteractStore(str(tmp_path));store.save_state('book',world())
    action_engine=ActionEngine(None,'mock',store)
    action_engine._llm=lambda *a,**k: (_ for _ in ()).throw(AssertionError('simple actions must not call model'))
    action=action_engine.detect_action('我把密信交给掌柜',store.load_state('book'))
    applied=action_engine.apply_action('book',action)
    assert applied['resolution']['status']=='success'
    assert store.load_state('book')['action_ledger'][-1]['input']=='我把密信交给掌柜'


def test_api_exposes_entry_options_and_upgrade_preserves_existing_progress(tmp_path,monkeypatch):
    import config
    from api.routers import interactive as api
    base=tmp_path/'book';(base/'chapters').mkdir(parents=True)
    (base/'plan.json').write_text(json.dumps({'title':'旧城'}),encoding='utf-8')
    (base/'chapters/001.md').write_text('原著正文依据',encoding='utf-8')
    store=InteractStore(str(tmp_path));state=world();state['scene_num']=5
    state.pop('participation');store.save_state('book',state)
    monkeypatch.setattr(api,'_store',store)
    monkeypatch.setattr(api,'NOVELS_DIR',str(tmp_path));monkeypatch.setattr(config,'NOVELS_DIR',str(tmp_path))
    monkeypatch.setattr(api.engine,'get_novel',lambda *_:{'id':'book'})
    monkeypatch.setattr(api,'_story',StoryDirector(None,'mock',store))
    app=FastAPI();app.include_router(api.router);client=TestClient(app)
    assert client.get('/api/novels/book/interactive/entry-options').json()['chapters'][0]['has_text']
    response=client.post('/api/novels/book/interactive/participation/upgrade',json={'canon_mode':'branch'})
    assert response.status_code==200,response.text
    loaded=store.load_state('book')
    assert loaded['scene_num']==5 and loaded['state']['inventory']==['密信']
    assert loaded['participation']['source']=='chapter_text'
    view=client.get('/api/novels/book/interactive/state').json()['state']
    assert view['participation_view']['enabled']
    assert 'past_evidence' not in view['participation'] and 'reference' not in view['participation']


def test_entry_seed_requires_literal_quote_and_player_ownership():
    state=world();state['state']['inventory']=[]
    state['participation']['opening']='林舟站在码头，林舟手中握着密信。掌柜在旁等候。'
    apply_entry_seed(state,{'inventory':[{'value':'神器','quote':'林舟凭空获得神器。'},
        {'value':'密信','quote':'林舟手中握着密信。'}]})
    assert state['state']['inventory']==['密信']
    assert state['participation']['entry_seed']['status']=='verified'


def test_take_requires_scene_item_and_moves_it_without_creating_duplicates():
    state=world();state['world']['locations']['码头']['items']=['钥匙']
    action={'input':'我捡起钥匙'}
    assert resolve_action(state,action,yes)['status']=='success'
    assert '钥匙' in state['state']['inventory']
    assert state['world']['locations']['码头']['items']==[]
    assert resolve_action(state,{'input':'我捡起神器'},yes)['status']=='failure'


def test_narrative_extraction_cannot_overwrite_committed_outcome(tmp_path):
    store=InteractStore(str(tmp_path));state=world()
    state['cast_states']={'顾川':{'condition':'获救'}};store.save_state('book',state)
    director=StoryDirector(None,'mock',store)
    director._llm=lambda *a,**k: (_ for _ in ()).throw(AssertionError('prose must not adjudicate state'))
    director._extract_player_state('book','顾川又死了，林舟得到了一件神器。')
    result=store.load_state('book')
    assert result['cast_states']['顾川']['condition']=='获救'
    assert result['state']['inventory']==['密信']


def test_action_mode_works_when_no_npc_is_present(tmp_path):
    from core.interactive.dialogue_engine import DialogueEngine
    store=InteractStore(str(tmp_path));state=world();state['casts']={};state['world']['chars']={}
    store.save_state('book',state)
    engine=DialogueEngine(None,'mock',store)
    async def narrative(*args,**kwargs):
        yield '【旁白】你检查了码头的环境。'
    engine.action._llm_stream=narrative
    async def run():
        return [event async for event in engine.chat_stream('book','观察四周',force_action=True)]
    events=asyncio.run(run())
    assert not any(e['type']=='error' for e in events)
    assert store.load_state('book')['action_ledger'][-1]['status']=='success'


def test_start_binds_selected_chapter_before_any_generated_scene(tmp_path,monkeypatch):
    import config
    from api.routers import interactive as api
    from core.interactive import novel_assets
    base=tmp_path/'book';(base/'chapters').mkdir(parents=True)
    plan={'title':'旧城','characters':{'protagonist':{'name':'林舟'}},
          'outline':{'volumes':[{'chapters':[{'number':1,'title':'旧事'},{'number':2,'title':'码头'}]}]}}
    (base/'plan.json').write_text(json.dumps(plan,ensure_ascii=False),encoding='utf-8')
    (base/'chapters/001.md').write_text('第一章已发生的独有事实。',encoding='utf-8')
    (base/'chapters/002.md').write_text('第二章开端在码头。',encoding='utf-8')
    store=InteractStore(str(tmp_path));director=StoryDirector(None,'mock',store)
    director._llm=lambda *a,**k:'{}';director.attach_cast_profiles=lambda *a:None
    director.generate_intro=lambda *a,**k:''
    captured=[]
    async def scene(novel_id):
        captured.append(copy.deepcopy(store.load_state(novel_id)))
        yield {'type':'done'}
    director.generate_scene_stream=scene
    monkeypatch.setattr(config,'NOVELS_DIR',str(tmp_path));monkeypatch.setattr(api,'NOVELS_DIR',str(tmp_path))
    monkeypatch.setattr(novel_assets,'NOVELS_DIR',str(tmp_path));monkeypatch.setattr(novel_assets,'_roleplay_cards',lambda *_:{})
    monkeypatch.setattr(api,'_store',store);monkeypatch.setattr(api,'_story',director)
    monkeypatch.setattr(api,'enqueue_background',lambda *a,**k:None)
    monkeypatch.setattr(api.engine,'get_novel',lambda *_:{'id':'book'})
    app=FastAPI();app.include_router(api.router);client=TestClient(app)
    response=client.post('/api/novels/book/interactive/start',json={
        'character':'林舟','chapter_index':1,'canon_mode':'branch','player_goal':'寻找同伴'})
    assert response.status_code==200,response.text
    assert captured[0]['participation']['chapter_number']==2
    assert captured[0]['outline_progress']['idx']==1
    assert '第一章已发生' in canon_context(captured[0])


def test_missing_chapter_does_not_bind_a_future_file_to_earlier_entry(tmp_path):
    (tmp_path/'chapters').mkdir()
    (tmp_path/'chapters/001.md').write_text('第一章',encoding='utf-8')
    (tmp_path/'chapters/003.md').write_text('第三章未来秘密',encoding='utf-8')
    plan={'outline':{'volumes':[{'chapters':[{'number':i,'title':str(i)} for i in (1,2,3)]}]}}
    catalog=chapter_catalog(tmp_path,plan)
    assert not catalog[1]['has_text'] and catalog[2]['file']=='003.md'
    state=world();initialize_participation(state,tmp_path,plan,1)
    assert '第三章未来秘密' not in canon_context(state)


def test_current_chapter_retrieval_finds_event_beyond_opening_excerpt():
    state=world();state['participation']['reference']='无关环境。\n'*800 + '掌柜在码头等待交出密信的林舟。'
    assert '掌柜在码头等待' in canon_context(state,'密信')


def test_selected_recipient_resolves_you_without_changing_player_identity():
    state=world();action={'input':'我把密信交给你','recipient':'掌柜'}
    assert resolve_action(state,action,yes)['status']=='success'
    assert state['casts']['掌柜']['inventory']==['密信']
    assert state['player_char']['name']=='林舟'


def test_failed_action_and_player_input_are_restorable_after_refresh(tmp_path):
    from core.interactive.dialogue_engine import DialogueEngine
    store=InteractStore(str(tmp_path));state=world();state['state']['inventory']=[]
    store.save_state('book',state);engine=DialogueEngine(None,'mock',store)
    async def run():
        return [e async for e in engine.chat_stream('book','我把密信交给掌柜',force_action=True)]
    events=asyncio.run(run())
    assert not any(e['type']=='error' for e in events)
    loaded=InteractStore(str(tmp_path)).load_state('book')
    assert any(b['type']=='user' and b['content']=='我把密信交给掌柜' for b in loaded['recent_blocks'])
    assert any('未成功' in b['content'] for b in loaded['recent_blocks'])


def test_successful_consumable_use_deducts_inventory_without_completing_delivery():
    state=world();state['state']['inventory'].append('药丸');action={'input':'我服下药丸'}
    assert resolve_action(state,action,yes)['status']=='success'
    assert '药丸' not in state['state']['inventory']
    assert not beat_advance_by_action(state,action)
