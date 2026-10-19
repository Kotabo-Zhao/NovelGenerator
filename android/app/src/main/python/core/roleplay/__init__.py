"""Roleplay mode (SillyTavern-style) package."""
from .card_codec import CardError, apply_macros, parse_card_bytes, pick_greeting
from .engine import RoleplayEngine
from .store import RoleplayStore

__all__ = [
    "CardError",
    "RoleplayEngine",
    "RoleplayStore",
    "apply_macros",
    "parse_card_bytes",
    "pick_greeting",
]
