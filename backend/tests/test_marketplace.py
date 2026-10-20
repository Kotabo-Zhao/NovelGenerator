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
