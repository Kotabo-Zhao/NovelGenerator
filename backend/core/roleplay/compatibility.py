"""Describe active card data and extension requirements without discarding them."""
from .identity import role_mode
from .regex_rules import scripts, transform


def card_features(card, settings=None, world_entries=None):
    extensions = card.get('extensions') or {}
    book = card.get('character_book') or (card.get('card_data') or {}).get('character_book') or {}
    features = []
    for field, label in [('description', '角色/世界设定'), ('personality', '性格'),
                         ('scenario', '场景'), ('first_mes', '默认开场'),
                         ('mes_example', '示例对话'), ('system_prompt', '卡片系统提示'),
                         ('post_history_instructions', '历史末尾指令'), ('creator_notes', '作者使用说明')]:
        if card.get(field):
            features.append({'name': label, 'status': 'active', 'field': field})
    features.extend([{'name': '备选开场', 'status': 'active', 'count': len(card.get('alternate_greetings') or [])},
                     {'name': '世界书', 'status': 'active', 'count': len(world_entries if world_entries is not None else book.get('entries') or [])},
                     {'name': '卡片正则', 'status': 'active', 'count': len(scripts(card))}])
    depth_prompt = extensions.get('depth_prompt')
    if isinstance(depth_prompt, dict) and depth_prompt.get('prompt'):
        features.append({'name': '角色深度提示', 'status': 'active'})
    known = {'chub', 'depth_prompt', 'regex_scripts', 'fav', 'talkativeness', 'world',
             'group_greetings_mode', 'group_greetings'}
    for key in extensions:
        if key not in known and extensions[key]:
            features.append({'name': key, 'status': 'requires_extension',
                             'detail': '已原样保留；需要对应宿主扩展，不能把“已导入”视为已执行'})
    warnings = []
    for script in scripts(card):
        placements = script.get('placement')
        if not isinstance(placements, list) or not placements:
            continue
        mode = 'display' if script.get('markdownOnly') else 'prompt' if script.get('promptOnly') else 'source'
        single = {**card, 'extensions': {'regex_scripts': [script]}}
        transform('', single, settings or {}, placements[0], mode=mode, warnings=warnings)
    if warnings:
        features.append({'name':'正则规则检查', 'status':'warning', 'detail':'；'.join(warnings)})
    return {'mode': role_mode(card, settings or {}), 'features': features,
            'can_reload_original': bool(card.get('market_source') or card.get('source') == 'png'),
            'legacy_metadata_missing': not card.get('card_data') and card.get('source') in ('marketplace', 'png'),
            'creator_notes': card.get('creator_notes') or '',
            'group_fields': '群聊开场与发言频率仅用于群聊；单人对话不调用，导出保留原值',
            'card_spec': card.get('card_spec', 'chara_card_v2')}
