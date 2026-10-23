import asyncio
import base64
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.roleplay.card_codec import apply_macros, normalize_card
from core.roleplay.engine import RoleplayEngine
from core.roleplay.identity import character_to_persona, identity_instruction, role_mode
from core.roleplay.prompt import build_messages, build_system_prompt
from core.roleplay.regex_rules import transform
from core.roleplay.store import RoleplayStore
from core.roleplay.worldinfo import normalize_entry, scan_world_info


def test_lorebook_import_collisions_repeat_and_capacity_never_lose_entries(monkeypatch):
    from core.roleplay import worldinfo as wi
    first = wi.parse_worldinfo_payload({'entries': {'0': {'key':['城门'], 'content':'旧城'}}})
    other = wi.parse_worldinfo_payload({'entries': {'0': {'key':['村落'], 'content':'新村'}}})
    merged = wi.merge_entries(first, other)
    assert len(merged) == 2 and len({e['id'] for e in merged}) == 2
    assert len(wi.merge_entries(merged, other)) == 2
    monkeypatch.setattr(wi, 'MAX_ENTRIES', 1)
    with pytest.raises(ValueError, match='本次未导入'):
        wi.merge_entries(first, other)
    with pytest.raises(ValueError, match='未导入任何'):
        wi.parse_worldinfo_payload({'entries': [first[0], other[0]]})
    assert first[0]['content'] == '旧城'


def test_worldinfo_st_aliases_do_not_revert_edits_or_duplicate_ids():
    from core.roleplay import worldinfo as wi
    entries = wi.import_worldinfo_bytes(json.dumps({'entries': {'7': {
        'key':['旧关键词'], 'keysecondary':['旧次词'], 'content':'旧城', 'disable':True, 'position':'4'
    }}}).encode('utf-16'))
    entry = entries[0]
    assert entry['id'] == '7' and entry['position'] == 'at_depth'
    entry.update(id='local-renamed', keys=['新关键词'], secondary_keys=['新次词'], enabled=True)
    loaded = wi.normalize_entry(entry)
    assert loaded['id'] == 'local-renamed' and loaded['enabled']
    exported = wi.export_entries([loaded])[0]
    assert isinstance(exported['id'], int) and exported['key'] == ['新关键词'] and not exported['disable']
    again = wi.parse_worldinfo_payload({'entries':[exported]})[0]
    assert again['keys'] == ['新关键词'] and again['enabled'] and again['position'] == 'at_depth'


def test_export_empty_book_does_not_restore_deleted_entries(client_store):
    client, store = client_store
    card = store.get_character('builtin-suwan')
    card['character_book'] = {'name':'旧城','entries':[{'keys':['门'], 'content':'已删除'}]}
    store.upsert_character(card)
    store.save_worldinfo(card['id'], [])
    book = client.get('/api/roleplay/characters/'+card['id']+'/export').json()['data']['character_book']
    assert book['name'] == '旧城' and book['entries'] == []


def test_worldinfo_api_repeated_import_different_books_and_export_reimport(client_store):
    client, store = client_store
    cid = 'builtin-suwan'
    url = '/api/roleplay/characters/'+cid
    for content in ('北方的旧城', '南方的港口', '南方的港口'):
        response = client.post(url+'/worldinfo/import', json={'json_text':json.dumps({
            'entries':{'0':{'uid':0, 'key':['地点'], 'content':content, 'disable':True}}
        })})
        assert response.status_code == 200, response.text
    entries = store.load_worldinfo(cid)
    assert len(entries) == 2
    assert client.patch(url+'/worldinfo/'+entries[1]['id'], json={'enabled':True}).status_code == 200
    assert store.load_worldinfo(cid)[1]['enabled']
    exported = client.get(url+'/export').content
    result = client.post('/api/roleplay/characters/import',json={
        'filename':'shop-export.json','data_base64':base64.b64encode(exported).decode()})
    assert result.status_code == 200, result.text
    imported = store.load_worldinfo(result.json()['character']['id'])
    assert len(imported) == 2 and imported[1]['enabled']


def test_worldinfo_regex_keys_activate_and_import_keeps_book_scan_rules(client_store):
    client, store = client_store
    entry = normalize_entry({'keys':[r'/north\s+gate/i'], 'content':'北门守卫'} )
    assert scan_world_info([entry], [], 'NORTH gate')[0]['content'] == '北门守卫'
    assert scan_world_info([entry], [], 'south gate') == []
    url = '/api/roleplay/characters/builtin-suwan/worldinfo/import'
    body = {'json_text':json.dumps({'name':'旧城','scan_depth':32,'recursive_scanning':True,
                                    'token_budget':800,'entries':[entry]})}
    response = client.post(url, json=body)
    assert response.status_code == 200 and response.json()['imported'] == 1
    repeat = client.post(url, json=body).json()
    assert repeat['imported'] == 0 and repeat['skipped'] == 1
    book = store.get_character('builtin-suwan')['character_book']
    assert book['scan_depth'] == 32 and book['recursive_scanning'] and book['token_budget'] == 800


def test_imported_st_secondary_logic_uses_official_enum():
    not_all = normalize_entry({'key':['城门'], 'keysecondary':['白天','守卫'],
        'selective':True,'selectiveLogic':1,'content':'夜间通道'})
    and_all = normalize_entry({'key':['城门'], 'keysecondary':['白天','守卫'],
        'selective':True,'selectiveLogic':3,'content':'岗哨检查'})
    assert scan_world_info([not_all], [], '城门 白天')
    assert not scan_world_info([not_all], [], '城门 白天 守卫')
    assert not scan_world_info([and_all], [], '城门 白天')
    assert scan_world_info([and_all], [], '城门 白天 守卫')


def test_player_identity_is_bound_to_character_and_does_not_relabel_history(tmp_path):
    store = RoleplayStore(str(tmp_path))
    store.upsert_character({'id':'other', 'name':'旅店店主'})
    store.update_identity('builtin-suwan', {'user_name':'林舟', 'player_pronouns':'她'})
    store.save_chat('builtin-suwan', [{'role':'user', 'speaker':'旧名字', 'content':'旧对话'}])
    store.update_identity('builtin-suwan', {'user_name':'青禾'})
    assert store.chat_messages('builtin-suwan')[0]['speaker'] == '旧名字'
    assert store.effective_settings('other')['user_name'] == '旅行者'
    assert RoleplayStore(str(tmp_path)).identity('builtin-suwan')['user_name'] == '青禾'
    with pytest.raises(ValueError, match='不能使用相同名字'):
        store.update_identity('other', {'user_name':'旅店店主'})


def test_character_conversion_swaps_only_persona_macros():
    result = character_to_persona({'name':'林舟','description':'{{char}} 是 {{user}} 的旧识。', 'scenario':'不要复制场景'})
    assert result == {'user_name':'林舟', 'user_persona':'{{user}} 是 {{char}} 的旧识。'}


def test_world_card_is_a_host_instead_of_a_person_and_respects_authored_pov():
    card = {'name':'旧城漫游', 'description':'{{char}} 不是一个特定的角色，而是一个RPG系统。'}
    settings = {'user_name':'青禾','player_pronouns':'她','narration_pov':'third'}
    assert role_mode(card, settings) == 'narrator'
    result = identity_instruction(card, settings)
    assert '世界主持' in result and '第三人称' in result and '「她」' in result
    assert '不替玩家决定' in result
    assert role_mode(card, {'role_mode':'character'}) == 'character'


def test_macro_original_and_case_insensitive_names():
    assert apply_macros('{{ USER }} / {{CHAR}} / {{original}} / {{unknown}}', '店主','青禾', original='默认提示') == '青禾 / 店主 / 默认提示 / {{unknown}}'
    system = build_system_prompt({'name':'店主', 'system_prompt':'{{original}}\n[卡片规则]'}, {'user_name':'青禾','system_prompt':'[我的默认]'})
    assert '[我的默认]\n[卡片规则]' in system


def test_multiline_examples_and_post_history_depth_zero():
    card = {'name':'店主','mes_example':'<START>\n{{user}}: 第一行\n第二行\n\n{{char}}: 回答\n动作描写',
            'post_history_instructions':'保持店主身份', 'extensions':{'depth_prompt':{'depth':0,'role':'system','prompt':'最后检查 {{user}}'}}}
    settings={'user_name':'青禾','authors_note':'作者注释','authors_note_depth':0}
    messages=build_messages(card,settings,[{'role':'user','content':'现在的问题'}])
    assert any(m['content']=='第一行\n第二行\n' for m in messages)
    assert any(m['content']=='回答\n动作描写' for m in messages)
    assert messages[-1]['content']=='[补充指示]\n保持店主身份'
    assert messages[-2]['content']=='最后检查 青禾'
    assert messages[-3]['content']=='[作者注释]\n作者注释'
    assert '保持店主身份' not in messages[0]['content']


def test_world_book_position_case_order_probability_and_recursion():
    raw={'id':1,'keys':['Gate'],'content':'unlock','insertion_order':0,'case_sensitive':True,
         'position':'after_char','extensions':{'position':4,'depth':0,'role':1,'vendor':'preserve'}}
    entry=normalize_entry(raw)
    assert entry['order']==0 and entry['depth']==0 and entry['role']=='user' and entry['position']=='at_depth'
    assert entry['extensions']['vendor']=='preserve'
    assert not scan_world_info([entry],[],'gate')
    assert scan_world_info([entry],[],'Gate')[0]['id']=='1'
    following=normalize_entry({'id':2,'keys':['unlock'],'content':'门已经开启'})
    assert len(scan_world_info([entry,following],[],'Gate',recursive=True))==2
    impossible=normalize_entry({'id':3,'constant':True,'content':'never','probability':0})
    assert not scan_world_info([impossible],[],'')


def test_lorebook_injections_are_not_all_collapsed_into_first_system_message():
    entries=[normalize_entry({'id':1,'content':'before','constant':True,'position':'before_char'}),
             normalize_entry({'id':2,'content':'after','constant':True,'position':'after_char'}),
             normalize_entry({'id':3,'content':'tail {{user}}','constant':True,'position':'after_char','extensions':{'position':4,'depth':0}})]
    messages=build_messages({'name':'店主','description':'description'},{'user_name':'青禾'},[{'role':'user','content':'问候'}],entries)
    assert messages[0]['content'].index('before')<messages[0]['content'].index('description')<messages[0]['content'].index('after')
    assert messages[-1]=={'role':'system','content':'tail 青禾'}


def test_regex_source_display_prompt_named_groups_and_trim():
    card={'name':'店主','extensions':{'regex_scripts':[
        {'findRegex':'/hello/gi','replaceString':'你好','placement':[1]},
        {'findRegex':r'/<status>([\s\S]*?)<\/status>/g','replaceString':'<details>$1</details>','placement':[2],'markdownOnly':True},
        {'findRegex':r'/<status>[\s\S]*?<\/status>/g','replaceString':'','placement':[2],'promptOnly':True},
        {'findRegex':'/(?<item>青禾!)/g','replaceString':'{{char}} says $<item>','trimStrings':['!'],'placement':[2]}
    ]}}
    settings={'user_name':'青禾'}
    text='正文<status>生命: 10</status>'
    assert transform('HELLO hello',card,settings,1)=='你好 你好'
    assert transform(text,card,settings,2,mode='display')=='正文<details>生命: 10</details>'
    assert transform(text,card,settings,2,mode='prompt')=='正文'
    assert transform('青禾!',card,settings,2)=='店主 says 青禾'
    assert transform(text,card,settings,2)==text


def test_regex_timeout_does_not_hang_generation():
    card={'extensions':{'regex_scripts':[{'findRegex':'/(a|aa)+$/','replaceString':'x','placement':[2]}]}}
    text='a'*20000+'!';warnings=[];started=time.monotonic()
    assert transform(text,card,{},2,warnings=warnings)==text
    assert time.monotonic()-started<1
    assert warnings


@pytest.fixture
def client_store(tmp_path,monkeypatch):
    from api.routers import roleplay as rp
    store=RoleplayStore(str(tmp_path/'roleplay'))
    monkeypatch.setenv('NOVEL_GENERATOR_INTERNAL','true')
    monkeypatch.setattr(rp,'_store',store)
    monkeypatch.setattr(rp,'_engine',RoleplayEngine(SimpleNamespace(client=None,model='mock'),store))
    monkeypatch.setattr(rp,'NOVELS_DIR',str(tmp_path/'novels'))
    app=FastAPI();app.include_router(rp.router)
    with TestClient(app) as client:
        yield client,store


def test_identity_api_saves_player_and_rejects_actor_collision_or_busy(client_store):
    client,store=client_store
    url='/api/roleplay/characters/builtin-suwan/identity'
    assert client.put(url,json={'user_name':'青禾','player_pronouns':'她'}).status_code==200
    assert client.get(url).json()['identity']['user_name']=='青禾'
    assert client.put(url,json={'user_name':'苏晚'}).status_code==400
    store.begin_generation('builtin-suwan')
    try:
        assert client.put(url,json={'user_name':'another'}).status_code==409
    finally:
        store.end_generation('builtin-suwan')


def test_card_export_preserves_v3_vendor_fields_and_book_metadata(client_store):
    client,store=client_store
    data={'name':'店主','description':'D'*25000,'first_mes':'欢迎 {{user}}',
          'nickname':'老板','assets':[{'type':'icon','uri':'ccdefault:','name':'main'}],
          'extensions':{'depth_prompt':{'prompt':'角色提醒','depth':0},'vendor':{'x':1}},
          'character_book':{'name':'旧城','scan_depth':50,'token_budget':4000,'recursive_scanning':True,
                            'extensions':{'custom':'keep'},'entries':[{'keys':['门'],'content':'开门','extensions':{'position':4,'depth':0}}]}}
    raw={'spec':'chara_card_v3','spec_version':'3.0','data':data}
    payload=base64.b64encode(json.dumps(raw).encode()).decode()
    response=client.post('/api/roleplay/characters/import',json={'filename':'card.json','data_base64':payload})
    assert response.status_code==200
    card=response.json()['character']
    assert card['description']==data['description']
    exported=client.get('/api/roleplay/characters/'+card['id']+'/export').json()
    assert exported['spec']=='chara_card_v3'
    assert exported['data']['assets']==data['assets']
    assert exported['data']['nickname']=='老板'
    book=exported['data']['character_book']
    assert book['scan_depth']==50 and book['recursive_scanning'] and book['extensions']['custom']=='keep'


def test_generation_uses_bound_identity_in_prompt_and_speaker(client_store):
    client,store=client_store
    from api.routers import roleplay as rp
    store.update_identity('builtin-suwan',{'user_name':'青禾','player_pronouns':'她','narration_pov':'third'})
    captured=[]
    class Fake:
        async def create_stream(self,**kwargs):
            captured.extend(kwargs['messages'])
            yield '店主向青禾点头。'
    rp._engine._resilient=Fake()
    async def run():
        return [e async for e in rp._engine.stream_reply('builtin-suwan','你好')]
    events=asyncio.run(run())
    assert events[0]['user']=='青禾'
    assert '玩家（user 消息）是「青禾」' in captured[0]['content']
    assert '「她」' in captured[0]['content']
    assert store.chat_messages('builtin-suwan')[0]['speaker']=='青禾'


def test_novel_character_can_be_chosen_as_player_without_ai_identity_collision(client_store,tmp_path):
    client,store=client_store
    folder=tmp_path/'novels'/'book1';folder.mkdir(parents=True)
    (folder/'plan.json').write_text(json.dumps({'title':'旧城', 'characters':{'protagonist':{'name':'顾川','identity':'来自北方的商人'}}}),encoding='utf-8')
    store.upsert_character({'id':'keeper','name':'林舟','novel_id':'book1'})
    url='/api/roleplay/characters/keeper/identity'
    assert any(c['id']=='novel:顾川' for c in client.get(url).json()['choices'])
    response=client.put(url,json={'source_character':'novel:顾川'})
    assert response.status_code==200 and response.json()['identity']['user_name']=='顾川'
    assert '商人' in response.json()['identity']['user_persona']


def test_legacy_metadata_sync_preserves_edits_chat_and_disabled_entries(client_store,monkeypatch):
    from api.routers import roleplay as rp
    client,store=client_store
    store.upsert_character({'id':'legacy','name':'店主','description':'已编辑的设定','source':'marketplace', 'market_source':'chub','market_reference':'author/card'})
    store.save_worldinfo('legacy',[{'id':1,'content':'已编辑的世界书','comment':'自己的标题','enabled':False,'position':'after_char'}])
    store.save_chat('legacy',[{'role':'user','speaker':'旧玩家','content':'以前聊过'}])
    incoming=normalize_card({'name':'店主','description':'原作设定', 'character_book':{'scan_depth':30,'entries':[{'id':1,'content':'原作内容','constant':True,'position':'after_char','extensions':{'position':4,'depth':0}}]}})
    async def card(*args):return incoming,None
    monkeypatch.setattr(rp._market,'card',card)
    response=client.post('/api/roleplay/characters/legacy/sync-metadata')
    assert response.status_code==200
    assert store.get_character('legacy')['description']=='已编辑的设定'
    entry=store.load_worldinfo('legacy')[0]
    assert entry['content']=='已编辑的世界书' and entry['enabled'] is False
    assert entry['position']=='at_depth' and entry['depth']==0
    assert store.chat_messages('legacy')[0]['speaker']=='旧玩家'
    assert list((__import__('pathlib').Path(store.root)/'backups/card-metadata/legacy').glob('*/characters.json'))


def test_first_open_uses_default_greeting_not_random_alternate(tmp_path,monkeypatch):
    store=RoleplayStore(str(tmp_path))
    card=store.get_character('builtin-suwan');card.update(first_mes='默认 {{user}}',alternate_greetings=['其他开场'])
    store.upsert_character(card)
    monkeypatch.setattr('core.roleplay.engine.random.choice',lambda values:values[-1])
    engine=RoleplayEngine(SimpleNamespace(client=None,model='mock'),store)
    assert engine.ensure_chat('builtin-suwan')[0]['content']=='默认 旅行者'
    assert normalize_card({'name':'卡','alternate_greetings':['开场']})['first_mes']==''


def test_unbound_chat_does_not_claim_player_identity_is_confirmed(client_store):
    client,store=client_store
    url='/api/roleplay/characters/builtin-suwan/chat'
    assert client.get(url).json()['identity_bound'] is False
    store.update_identity('builtin-suwan',{'user_name':'青禾'})
    assert client.get(url).json()['identity_bound'] is True


def test_png_original_fields_can_be_previewed_and_restored(client_store):
    import struct,zlib
    client,store=client_store
    def chunk(kind,payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
    raw={'name':'Guard','description':'Original background','personality':'Patient'}
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'tEXt',b'chara\x00'+base64.b64encode(json.dumps(raw).encode()))+chunk(b'IEND',b'')
    store.upsert_character({'id':'png-repair','name':'Guard','description':'Wrong','source':'png'})
    store.save_avatar('png-repair',png)
    url='/api/roleplay/characters/png-repair/sync-metadata'
    response=client.get(url)
    assert response.status_code==200,response.text
    assert any(f['incoming']=='Original background' for f in response.json()['fields'])
    response=client.post(url,json={'fields':['description'],'expected':{'description':'Wrong'},'proposed':{'description':'Original background'}})
    assert response.status_code==200,response.text
    assert store.get_character('png-repair')['description']=='Original background'
