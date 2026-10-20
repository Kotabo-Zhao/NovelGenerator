"""Explicit player identity, separate from the AI character or world host."""
import re

IDENTITY_DEFAULTS = {
    'user_name': '旅行者', 'user_persona': '', 'player_pronouns': 'auto',
    'narration_pov': 'card', 'role_mode': 'auto', 'source_character': '',
}


def normalize_identity(data):
    result = dict(IDENTITY_DEFAULTS)
    result.update({k: v for k, v in data.items() if k in result})
    result['user_name'] = str(result['user_name'] or '').strip()[:60] or '旅行者'
    result['user_persona'] = str(result['user_persona'] or '')[:16000]
    result['source_character'] = str(result['source_character'] or '')[:160]
    for key, allowed in [('player_pronouns', {'auto', '他', '她', 'TA'}),
                         ('narration_pov', {'card', 'second', 'third'}),
                         ('role_mode', {'auto', 'character', 'narrator'})]:
        if not isinstance(result[key], str) or result[key] not in allowed:
            result[key] = IDENTITY_DEFAULTS[key]
    return result


def role_mode(card, settings):
    selected = settings.get('role_mode', 'auto')
    if selected in {'character', 'narrator'}:
        return selected
    description = '\n'.join(str(card.get(k) or '') for k in ('description', 'system_prompt', 'personality'))
    patterns = r'不是一个特定的角色|世界主持|担任旁白|扮演.*(?:RPG系统|模拟器)|you (?:are|play as).*?(?:simulator|narrator|game master)|\{\{char\}\}.*?(?:RPG系统|模拟器)'
    return 'narrator' if re.search(patterns, description, re.I) else 'character'


def identity_instruction(card, settings):
    player = settings.get('user_name') or '旅行者'
    name = card.get('name') or '角色'
    mode = role_mode(card, settings)
    text = (f'[身份与人称边界]\n玩家（user 消息）是「{player}」，由真人玩家控制。'
            f'AI（assistant 消息）负责「{name}」。{{{{user}}}} 永远指玩家，{{{{char}}}} 永远指本卡。\n')
    text += ('本卡是世界主持/旁白卡：AI 负责环境、事件和其他人物，遵循卡片原定叙事人称；'
             '不要把世界卡名称当作场内人物，也不要把玩家等同于主持者。\n' if mode == 'narrator' else
             '本卡是对话角色卡：AI 扮演卡面人物，玩家是与其互动的另一人；不要互换双方身份。\n')
    text += '除明确的代写玩家指令外，不替玩家决定下一步行动、台词、想法或感受。作者开场中的既定背景不代表可以持续代演玩家。\n'
    pronoun = settings.get('player_pronouns', 'auto')
    text += (f'玩家的第三人称代词使用「{pronoun}」，不要根据卡面人物的性别推断玩家性别。\n'
             if pronoun != 'auto' else '玩家性别和称谓以玩家设定及卡片明确背景为准；未说明时不要自行推断。\n')
    pov = settings.get('narration_pov', 'card')
    text += {'card': '叙事人称遵循原卡和开场；对话里的“我”属于正在说话的那个人。',
             'second': '叙述玩家时使用第二人称“你”；角色台词中的“我”仍指该说话角色。',
             'third': f'叙述玩家时使用第三人称或名字「{player}」；角色台词中的“我”仍指说话角色。'}.get(pov, '')
    return text


def character_to_persona(card):
    """Like ST Convert to Persona: swap char/user macros in description only."""
    description = str(card.get('description') or '')
    description = re.sub(r'\{\{\s*(char|user)\s*\}\}',
                         lambda m: '{{user}}' if m[1].lower() == 'char' else '{{char}}',
                         description, flags=re.I)
    return {'user_name': str(card.get('name') or '旅行者'), 'user_persona': description}
