"""Compatibility imports; the audio implementation is shared."""
from core.audio.emotion_tts import EmotionTTS, EmotionTTSUnavailable

__all__ = ["EmotionTTS", "EmotionTTSUnavailable"]
