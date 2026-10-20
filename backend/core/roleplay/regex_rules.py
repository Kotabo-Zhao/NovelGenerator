"""ST scoped regex data: source/display/prompt paths, bounded execution time.

Semantics reference: SillyTavern public/scripts/extensions/regex/engine.js.
This implements regex replacement data, not JavaScript or STscript execution.
"""
import re
import time
import regex

from .card_codec import apply_macros, macro_context

MAX_TEXT = 500000


def scripts(card):
    value = (card.get('extensions') or {}).get('regex_scripts', [])
    return [s for s in value[:100] if isinstance(s, dict)] if isinstance(value, list) else []


def transform(text, card, settings, placement, *, mode='source', depth=None, is_edit=False, warnings=None):
    text = str(text or '')[:MAX_TEXT]
    context = macro_context(card, settings)
    expand = lambda value: apply_macros(str(value or ''), card.get('name', ''), settings.get('user_name', '旅行者'), context)
    started = time.monotonic()
    for script in scripts(card):
        if time.monotonic() - started > .15:
            if warnings is not None:
                warnings.append('正则处理超过时间预算，已保留剩余原文')
            break
        placements = script.get('placement')
        if script.get('disabled') or not isinstance(placements, list) or placement not in placements:
            continue
        markdown = bool(script.get('markdownOnly'))
        prompt = bool(script.get('promptOnly'))
        if not ((mode == 'display' and markdown) or (mode == 'prompt' and prompt)
                or (mode == 'source' and not markdown and not prompt)):
            continue
        if is_edit and not script.get('runOnEdit'):
            continue
        if depth is not None:
            low, high = script.get('minDepth'), script.get('maxDepth')
            if isinstance(low, (int, float)) and low >= -1 and depth < low:
                continue
            if isinstance(high, (int, float)) and high >= 0 and depth > high:
                continue
        pattern = str(script.get('findRegex') or '')
        if not pattern or len(pattern) > 12000:
            continue
        substitute = script.get('substituteRegex', 0)
        if substitute == 1:
            pattern = expand(pattern)
        elif substitute == 2:
            values = {**context, 'char': card.get('name', ''), 'user': settings.get('user_name', '旅行者')}
            for key, value in values.items():
                pattern = re.sub(r'\{\{\s*' + re.escape(key) + r'\s*\}\}', lambda _: re.escape(str(value or '')), pattern, flags=re.I)
        flags = ''
        if pattern.startswith('/') and pattern.rfind('/') > 0:
            end = pattern.rfind('/')
            pattern, flags = pattern[1:end], pattern[end + 1:]
        count = 0 if 'g' in flags else 1
        if set(flags) - set('gimsud'):
            if warnings is not None:
                warnings.append('正则标志暂不兼容：' + str(script.get('scriptName', '未命名')))
            continue
        pattern = re.sub(r'\(\?<([A-Za-z_]\w*)>', r'(?P<\1>', pattern)
        pattern = re.sub(r'\\k<([A-Za-z_]\w*)>', r'(?P=\1)', pattern)
        options = regex.VERSION1
        for flag, option in [('i', regex.I), ('m', regex.M), ('s', regex.S)]:
            if flag in flags:
                options |= option
        replacement = re.sub(r'\{\{match\}\}', '$0', str(script.get('replaceString') or ''), flags=re.I)
        def replace(match):
            def group(m):
                try:
                    value = match.group(int(m[1]) if m[1] is not None else m[2]) or ''
                except (IndexError, KeyError):
                    value = ''
                for trim in script.get('trimStrings') or []:
                    value = value.replace(expand(trim), '')
                return value
            return expand(re.sub(r'\$(\d+)|\$<([^>]+)>', group, replacement))[:MAX_TEXT]
        try:
            text = regex.sub(pattern, replace, text, count=count, flags=options, timeout=.025)[:MAX_TEXT]
        except (regex.error, TimeoutError, ValueError) as exc:
            if warnings is not None:
                warnings.append(str(script.get('scriptName') or '未命名正则') + '：' + type(exc).__name__)
    return text


def display_messages(messages, card, settings):
    return [{**message, 'display_content': transform(message.get('content', ''), card, settings,
             1 if message.get('role') == 'user' else 2, mode='display', depth=len(messages)-index-1)}
            for index, message in enumerate(messages)]
