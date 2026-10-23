import asyncio
import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.roleplay.marketplace import Marketplace, MarketError, OFFICIAL_PREFIX
from core.roleplay.store import RoleplayStore


@pytest.mark.parametrize('source,reference', [('chub','http://127.0.0.1/private'), ('chub','https://evil.example/card'),
    ('chub','a/../private'), ('official','../secret.png'), ('official','https://example.com/card'), ('invalid','a/b')])
def test_market_references_cannot_escape_source(source,reference):
    with pytest.raises(MarketError):
        Marketplace.reference(source,reference)


def test_chub_adapter_preserves_prompt_fields_and_lorebook(monkeypatch):
    market=Marketplace()
    async def fetch(url,**kwargs):
        return json.dumps({'node':{'name':'Test','topics':['story'],'definition':{
            'name':'角色','personality':'完整描述','tavern_personality':'克制','first_message':'你好',
            'embedded_lorebook':{'entries':[{'keys':['古城'],'content':'城门在北'}]},
            'system_prompt':'{{char}} stays in character'}}}).encode()
    monkeypatch.setattr(market,'_fetch',fetch)
    card,avatar=asyncio.run(market.card('chub','author/story'))
    assert card['description']=='完整描述'
    assert card['personality']=='克制'
    assert card['character_book']['entries'][0]['content']=='城门在北'
    assert avatar is None


@pytest.mark.parametrize('envelope', [False, True])
def test_shop_standard_card_survives_install_export_and_import(monkeypatch, client, envelope):
    from api.routers import roleplay
    market = Marketplace()
    data = {'name':'旧城店主', 'description':'角色描述', 'personality':'沉稳', 'first_mes':'欢迎', 'first_message':'过期的旧别名',
            'mes_example':'<START>\n{{char}}: 你好', 'assets':[{'type':'icon','uri':'ccdefault:'}],
            'extensions':{'vendor':{'keep':True}}, 'character_book':{'name':'旧城', 'entries':[
                {'id':0,'keys':['门'],'content':'北门','enabled':True}]}}
    definition = {'spec':'chara_card_v3','spec_version':'3.0','data':data} if envelope else data
    async def fetch(url, **kwargs):
        return json.dumps({'node':{'definition':definition}}).encode()
    monkeypatch.setattr(market, '_fetch', fetch)
    monkeypatch.setattr(roleplay, '_market', market)
    http, store = client
    installed = http.post('/api/roleplay/marketplace/install', json={'source':'chub','reference':'author/card'}).json()['character']
    assert installed['description'] == '角色描述' and installed['first_mes'] == '欢迎'
    assert installed['personality'] == '沉稳'
    exported = http.get('/api/roleplay/characters/'+installed['id']+'/export').json()
    assert exported['data']['assets'] == data['assets']
    assert exported['data']['extensions'] == data['extensions']
    response = http.post('/api/roleplay/characters/import',json={'filename':'export.json',
        'data_base64':base64.b64encode(json.dumps(exported).encode()).decode()})
    assert response.status_code == 200, response.text
    imported = response.json()['character']
    assert imported['first_mes'] == '欢迎' and imported['description'] == '角色描述'
    assert store.load_worldinfo(imported['id'])[0]['content'] == '北门'


def test_catalog_cache_coalesces_parallel_requests_without_sharing_mutable_results(monkeypatch):
    market=Marketplace();calls=[]
    async def fetch(*args,**kwargs):
        calls.append(1);await asyncio.sleep(.01)
        return json.dumps([{'type':'character','id':'card.png','name':'Card','url':OFFICIAL_PREFIX+'card.png'}]).encode()
    monkeypatch.setattr(market,'_fetch',fetch)
    async def scenario():
        a,b=await asyncio.gather(market.catalog(),market.catalog())
        a['items'][0]['name']='changed'
        assert b['items'][0]['name']=='Card'
        assert (await market.catalog())['items'][0]['name']=='Card'
    asyncio.run(scenario());assert len(calls)==1


@pytest.fixture
def client(tmp_path,monkeypatch):
    from api.routers import roleplay
    monkeypatch.setenv('NOVEL_GENERATOR_INTERNAL','true')
    store=RoleplayStore(str(tmp_path/'cards'))
    monkeypatch.setattr(roleplay,'_store',store)
    monkeypatch.setattr(roleplay.engine,'get_novel',lambda ident:{'id':ident} if ident in {'book-a','book-b'} else None)
    async def card(source,reference):
        return {'name':'商店角色','description':'原描述','market_source':source,'market_reference':reference,
                'character_book':{'entries':[{'keys':['城门'],'content':'北门通向荒原','enabled':True}]}},None
    monkeypatch.setattr(roleplay._market,'card',card)
    app=FastAPI();app.include_router(roleplay.router)
    return TestClient(app),store


def test_install_is_idempotent_per_novel_and_never_overwrites_existing_edits(client):
    http,store=client
    def install(novel):
        r=http.post('/api/roleplay/marketplace/install',json={'source':'chub','reference':'author/card','novel_id':novel})
        assert r.status_code==200,r.text
        return r.json()
    first=install('book-a');cid=first['character']['id']
    store.save_chat(cid,[{'role':'user','content':'原聊天'}])
    card=store.get_character(cid);card['description']='用户改稿';store.upsert_character(card)
    repeat=install('book-a');other=install('book-b')
    assert repeat['reused'] and repeat['character']['description']=='用户改稿'
    assert other['character']['id']!=cid
    assert store.chat_messages(cid)[0]['content']=='原聊天'
    assert len(store.load_worldinfo(cid))==1
    listed=http.get('/api/roleplay/characters?novel_id=book-b&summary=true').json()['characters']
    assert len(listed)==1 and listed[0]['id']==other['character']['id']


def test_move_keeps_chat_and_copy_preserves_lorebook_without_copying_chat(client):
    http,store=client
    created=http.post('/api/roleplay/characters',json={'name':'原角色','novel_id':'book-a'}).json()['character']
    cid=created['id'];store.save_chat(cid,[{'role':'user','content':'私人对话'}])
    store.save_worldinfo(cid,[{'keys':['古城'],'content':'旧世界'}])
    moved=http.put('/api/roleplay/characters/'+cid,json={'novel_id':'book-b'})
    assert moved.status_code==200 and store.chat_messages(cid)[0]['content']=='私人对话'
    copied=http.post('/api/roleplay/characters/'+cid+'/copy',json={'novel_id':'book-a'}).json()['character']
    assert copied['id']!=cid and store.chat_messages(copied['id'])==[]
    assert store.load_worldinfo(copied['id'])[0]['content']=='旧世界'
    exported=http.get('/api/roleplay/characters/'+copied['id']+'/export')
    from core.roleplay.card_codec import parse_card_bytes
    parsed,_=parse_card_bytes(exported.content,'export.json')
    assert parsed['name']=='原角色' and parsed['character_book']['entries'][0]['content']=='旧世界'
    assert http.post('/api/roleplay/characters',json={'name':'bad','novel_id':'missing'}).status_code==404


def test_local_import_retains_selected_novel(client):
    http,store=client
    payload=base64.b64encode(json.dumps({'name':'本地卡','description':'设置'}).encode()).decode()
    result=http.post('/api/roleplay/characters/import',json={'filename':'card.json','data_base64':payload,'novel_id':'book-a'})
    assert result.status_code==200,result.text
    assert result.json()['character']['novel_id']=='book-a'


def test_character_cache_is_defensive_and_reloads_external_edits(tmp_path):
    from core.atomic_io import atomic_write_json
    store=RoleplayStore(str(tmp_path/'cache'))
    store.upsert_character({'id':'test','name':'原角色','description':'完整描述','novel_id':'book-a','tags':['原标签']})
    returned=store.get_character('test');returned['tags'].append('未保存')
    summary=store.list_characters(novel_id='book-a',summary=True)
    summary[0]['tags'].clear()
    assert store.get_character('test')['tags']==['原标签']
    atomic_write_json(store.characters_file,[{'id':'test','name':'外部编辑后的名字'}])
    assert store.get_character('test')['name']=='外部编辑后的名字'


def test_edit_delete_and_clear_are_rejected_during_a_reply(client):
    http,store=client
    cid='builtin-suwan'
    assert store.begin_generation(cid)
    try:
        assert http.put('/api/roleplay/characters/'+cid,json={'name':'误修改'}).status_code==409
        assert http.delete('/api/roleplay/characters/'+cid).status_code==409
        assert http.post('/api/roleplay/characters/'+cid+'/chat/clear',json={}).status_code==409
        assert store.get_character(cid)
    finally:
        store.end_generation(cid)
    assert http.put('/api/roleplay/characters/'+cid,json={'name':'可修改'}).status_code==200


def test_world_assets_never_pick_a_different_novels_same_named_card(tmp_path,monkeypatch):
    from core.interactive.novel_assets import _roleplay_cards
    monkeypatch.setenv('ROLEPLAY_DIR',str(tmp_path/'scoped-cards'))
    store=RoleplayStore()
    for cid,novel,text in [('shared','','通用'),('a','book-a','本书人物'),('b','book-b','别本小说的秘密')]:
        store.upsert_character({'id':cid,'name':'同名角色','novel_id':novel,'description':text})
    assert _roleplay_cards('book-a')['同名角色']['description']=='本书人物'
    assert _roleplay_cards('book-b')['同名角色']['description']=='别本小说的秘密'
    assert _roleplay_cards('book-c')['同名角色']['description']=='通用'

@pytest.mark.parametrize('wrapped', [False, True])
def test_shop_hybrid_aliases_never_swap_description_and_personality(wrapped):
    from core.roleplay.card_codec import normalize_card
    data = {'name':'城门守卫','description':'负责北门的老兵','personality':'谨慎友善',
            'first_message':'请出示通行证', 'embedded_lorebook':json.dumps({'entries':[{'keys':['北门'],'content':'日落关门'}]}),
            'extensions':{'vendor':{'preserve':True}}}
    definition = {'spec':'chara_card_v3','spec_version':'3.0','data':data} if wrapped else data
    card = normalize_card({'node':{'definition':definition}})
    assert card['description'] == data['description']
    assert card['personality'] == data['personality']
    assert card['first_mes'] == data['first_message']
    assert card['character_book']['entries'][0]['content'] == '日落关门'
    assert card['extensions'] == data['extensions']
    if wrapped:
        assert card['card_spec'] == 'chara_card_v3'


def test_shop_legacy_listing_notes_are_distinct_from_character_definition():
    from core.roleplay.card_codec import normalize_card
    card = normalize_card({'definition':{'name':'守卫','description':'作者的介绍',
        'personality':'守卫的背景设定','tavern_personality':'沉着','first_message':'你好'}})
    assert card['description'] == '守卫的背景设定'
    assert card['personality'] == '沉着'
    assert card['creator_notes'] == '作者的介绍'


def test_editor_save_preserves_long_imported_fields_and_multiline_greetings(client):
    http,store=client
    text='角色设定'*7000
    greetings=['第一段\n\n第二段','另一条\n动作']
    store.upsert_character({'id':'long-card','name':'守卫','description':text,'personality':text,
                            'alternate_greetings':greetings})
    response=http.put('/api/roleplay/characters/long-card',json={'name':'守卫改名','description':text,
        'personality':text,'alternate_greetings':greetings})
    assert response.status_code==200, response.text
    assert store.get_character('long-card')['description']==text
    assert store.get_character('long-card')['personality']==text
    assert store.get_character('long-card')['alternate_greetings']==greetings


def test_original_card_repair_is_previewed_selective_and_rejects_stale_edits(client,monkeypatch):
    from api.routers import roleplay
    http,store=client
    store.upsert_character({'id':'repair','name':'守卫','description':'错误的解析','personality':'手动修改的性格',
        'source':'marketplace','market_source':'chub','market_reference':'author/card'})
    store.save_chat('repair',[{'role':'user','content':'旧对话'}])
    async def card(*args):
        return {'name':'守卫','description':'正确的完整设定','personality':'原卡性格','card_data':{'name':'守卫'}},None
    monkeypatch.setattr(roleplay._market,'card',card)
    url='/api/roleplay/characters/repair/sync-metadata'
    preview=http.get(url).json()
    assert any(f['field']=='description' and f['incoming']=='正确的完整设定' for f in preview['fields'])
    assert store.get_character('repair')['description']=='错误的解析'
    payload={'fields':['description'],'expected':{'description':'错误的解析'},'proposed':{'description':'正确的完整设定'}}
    response=http.post(url,json=payload)
    assert response.status_code==200,response.text
    assert store.get_character('repair')['description']=='正确的完整设定'
    assert store.get_character('repair')['personality']=='手动修改的性格'
    assert store.chat_messages('repair')[0]['content']=='旧对话'
    assert http.post(url,json=payload).status_code==409


def test_market_cache_is_bounded_by_bytes_and_cancellation_does_not_duplicate(monkeypatch):
    import core.roleplay.marketplace as module
    monkeypatch.setattr(module,'MAX_CACHE_BYTES',100)
    market=Marketplace()
    async def scenario():
        started=asyncio.Event();release=asyncio.Event();calls=[]
        async def load():
            calls.append(1);started.set();await release.wait();return b'x'*80
        first=asyncio.create_task(market._cached('shared',load))
        await started.wait()
        second=asyncio.create_task(market._cached('shared',load))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        release.set()
        assert await second==b'x'*80 and len(calls)==1
        async def other():return b'y'*80
        await market._cached('other',other)
        assert market._cache_bytes<=100
        assert 'shared' not in market._cache
        assert not market._inflight
        async def large():return b'z'*101
        assert len(await market._cached('large',large))==101
        assert 'large' not in market._cache
    asyncio.run(scenario())


def test_failed_shop_load_is_shared_and_retryable():
    market=Marketplace();calls=[]
    async def fail():
        calls.append(1);await asyncio.sleep(.01);raise MarketError('offline')
    async def scenario():
        results=await asyncio.gather(*(market._cached('failed',fail) for _ in range(4)),return_exceptions=True)
        assert all(isinstance(result,MarketError) for result in results)
        assert len(calls)==1 and not market._inflight
        with pytest.raises(MarketError):await market._cached('failed',fail)
        assert len(calls)==2
    asyncio.run(scenario())


def test_summary_is_small_and_full_card_and_persona_remain_available(client):
    http,store=client
    text='guard background '*3000
    store.upsert_character({'id':'large','name':'Guard','description':text,'card_data':{'description':text},'alternate_greetings':[text]})
    summary=http.get('/api/roleplay/characters?summary=true').json()['characters']
    card=next(c for c in summary if c['id']=='large')
    assert card['_summary'] and len(card['description'])<=240
    assert 'card_data' not in card and 'alternate_greetings' not in card
    assert http.get('/api/roleplay/characters/large').json()['character']['description']==text
    choices=store.persona_choices(store.get_character('builtin-suwan'))
    persona=next(c for c in choices if c['id']=='card:large')
    assert persona['user_persona']==text[:16000]
