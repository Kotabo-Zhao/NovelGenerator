"""SillyTavern-inspired slash commands and player-action helpers.

ST exposes dozens of slash commands; this module implements the subset that is
useful inside a single-character roleplay chat, plus a lightweight action
detector so natural-language actions ("我推门进去，同时拔剑") can be rendered
with the same action semantics as ``/act``.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class Command:
    name: str
    args: str
    raw: str


_ALIASES = {
    "h": "help",
    "s": "say",
    "do": "act",
    "action": "act",
    "narrate": "nar",
    "sys": "nar",
    "out": "ooc",
    "dice": "roll",
    "r": "roll",
    "cont": "continue",
    "imp": "impersonate",
    "send": "say",
    "me": "act",
}

_COMMAND_NAMES = {
    "help", "say", "act", "nar", "ooc", "roll", "continue", "regen",
    "swipe", "impersonate", "note", "world",
}

HELP_TEXT = """可用指令：
/help                    查看指令列表
/say 文本  (/s)           普通对话
/act 动作  (/do)          动作：描写动作结果与角色反应
/nar 旁白  (/narrate)     插入旁白，不调用模型
/ooc 内容  (/out)         出戏发言，角色会回应
/roll 1d20+3 (/dice)      掷骰，不调用模型
/continue (/cont)         让角色继续上一条回复
/regen                    重新生成最后一条回复
/swipe [next|prev|new]    切换/生成回复版本
/impersonate (/imp)       让 AI 替你写一句台词（不发送）
/note 文本                 设置作者注释（author's note）
/world                    查看世界书状态

也可以不用指令：直接输入「我推门进去，同时拔剑」这类动作描述，
系统会自动按动作处理；输入框切换「动作」模式效果相同。"""

_DICE_RE = re.compile(r"^\s*(\d{0,3})d(\d{1,4})\s*(?:([+-])\s*(\d{1,4}))?\s*$", re.IGNORECASE)

_ACTION_VERBS = (
    "推门|开门|关门|上车|下车|上马|下马|上楼|下楼|进去|进来|出去|出来|走进|走出|离开",
    "拔剑|拔刀|出手|攻击|跪下|点头|摇头|鞠躬|坐下|站起|起身|转身|回头|跟上|追上",
    "拿起|拿出|掏出|交出|递给|接过|放下|放回|收起|举起|接住|抱住|拉住|抓住",
    "抱住|亲吻|拥抱|握手|拍肩|挡在|冲上|扑向|躲开|闪身|退后|停下|等待|看着",
    "答应|拒绝|同意|接受|成交|收下|归还|签字|发誓|承诺|威胁|坦白|隐瞒",
    "倒茶|斟酒|敬酒|服药|喝下|吃下|使用|启动|按下|拉下|搬开|踹开|回家|回府",
    "我推|我拉|我拔|我拿|我抓|我抱|我跪|我站|我坐|我转身|我走向|我冲",
)

_ACTION_RE = re.compile("|".join(_ACTION_VERBS))
_PAREN_ACTION_RE = re.compile(r"[（(][^）)]{0,120}[）)]")
_FIRST_PERSON_RE = re.compile(r"^(?:我|我们|咱)[^。！？!?]{0,60}")

_ACTION_DIRECTIVE = (
    "【动作规则】用户消息以 [动作] 开头时，表示玩家在描述自己的行动。"
    "请先描写动作带来的环境/对象反馈与合理结果（受力量、距离、场合限制），"
    "再让角色做出符合人设的自然反应；不要替玩家决定成功与否，也不要替玩家写台词。"
)


def parse_command(text: str) -> Optional[Command]:
    """Return a Command when text starts with a known/unknown slash command."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped.startswith("/") or len(stripped) < 2:
        return None
    head, _, rest = stripped[1:].partition(" ")
    name = head.strip().lower()
    if not name:
        return None
    name = _ALIASES.get(name, name)
    return Command(name=name, args=rest.strip(), raw=stripped)


def is_known_command(name: str) -> bool:
    return name in _COMMAND_NAMES


def roll_dice(expression: str) -> Dict[str, Any]:
    """Parse and roll NdM[+/-K]. Raises ValueError for invalid expressions."""
    match = _DICE_RE.match(expression or "")
    if not match:
        raise ValueError("骰子格式应为 NdM，例如 1d20、2d6+3")
    count_text, sides_text, sign, modifier_text = match.groups()
    count = int(count_text or 1)
    sides = int(sides_text)
    modifier = int(modifier_text or 0) * (-1 if sign == "-" else 1)
    if not 1 <= count <= 100:
        raise ValueError("骰子数量需在 1-100 之间")
    if not 2 <= sides <= 1000:
        raise ValueError("骰子面数需在 2-1000 之间")
    if abs(modifier) > 1000:
        raise ValueError("修正值过大")
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + modifier
    sign_text = f"{modifier:+d}" if modifier else ""
    text = f"🎲 {count}d{sides}{sign_text} = {total}（{', '.join(map(str, rolls))}）"
    return {
        "expression": f"{count}d{sides}{sign_text}",
        "rolls": rolls,
        "modifier": modifier,
        "total": total,
        "text": text,
    }


def looks_like_action(text: str) -> bool:
    """Heuristic for natural-language action input (ST-like /act autodetect)."""
    value = (text or "").strip()
    if not value or value.startswith("/"):
        return False
    if _PAREN_ACTION_RE.search(value):
        return True
    if _FIRST_PERSON_RE.match(value) and _ACTION_RE.search(value):
        return True
    # Short imperative actions such as "上车" / "推门" / "拔剑".
    return len(value) <= 12 and bool(_ACTION_RE.search(value))


ACTION_DIRECTIVE = _ACTION_DIRECTIVE
