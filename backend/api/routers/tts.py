"""NovelGenerator — 免费 TTS 语音合成端点（edge-tts）

微软 Azure 神经语音引擎（Edge 浏览器同款），完全免费、零 API Key。
角色扮演/章节朗读共用：每个角色一套 {voice, rate, pitch} 声线配置。

设计:
- 文本 hash 磁盘缓存（重复内容零成本，7 天过期）
- 失败自动重试 2 次（2s/4s 退避），仍失败返回 503 → 前端降级 Web Speech API
- 中文音色 10+，rate(语速) + pitch(音调) 可调 → 差异化角色声线
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter()

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "tts_cache")
CACHE_TTL = 7 * 24 * 3600  # 7 天

# 常用中文音色（完整列表 GET /api/tts/voices）
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# 实测可用音色白名单（2026-08-02 验证：微软对部分音色限区域，失败自动降级）
AVAILABLE_VOICES = [
    "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-XiaoxuanNeural",
    "zh-CN-YunxiNeural", "zh-CN-YunxiaNeural", "zh-CN-YunjianNeural",
    "zh-CN-YunyangNeural",
]


def _sanitize_voice(voice: str) -> str:
    """不可用音色 → 白名单最接近替代"""
    if voice in AVAILABLE_VOICES:
        return voice
    if voice.lower().startswith("zh-cn-yun"):
        return "zh-CN-YunxiNeural"
    return DEFAULT_VOICE


class TTSRequest(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE
    rate: str = "+0%"      # 语速: -50% ~ +100%
    pitch: str = "+0Hz"    # 音调: -50Hz ~ +50Hz


def _cache_key(text: str, voice: str, rate: str, pitch: str) -> str:
    raw = f"{voice}|{rate}|{pitch}|{text}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _cached_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.mp3")


async def _synthesize(text: str, voice: str, rate: str, pitch: str) -> bytes:
    """调用 edge-tts 合成（重试 2 次 + 音色降级 1 次）"""
    import edge_tts

    # 优先使用白名单音色（不可用音色直接替换，避免浪费 3 次重试）
    voice = _sanitize_voice(voice)

    last_err = None
    for attempt in range(3):
        try:
            com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            buf = bytearray()
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            if not buf:
                raise RuntimeError("empty audio")
            return bytes(buf)
        except Exception as e:
            last_err = e
            log.warning(f"TTS attempt {attempt+1} failed: {type(e).__name__}: {str(e)[:80]}")
            if attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))

    # 音色降级：请求音色在白名单内仍失败 → 换默认音色兜底（应对微软服务波动）
    if voice != DEFAULT_VOICE:
        log.warning(f"TTS voice {voice} failed, falling back to {DEFAULT_VOICE}")
        try:
            com = edge_tts.Communicate(text, DEFAULT_VOICE, rate="+0%", pitch="+0Hz")
            buf = bytearray()
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    buf.extend(chunk["data"])
            if buf:
                return bytes(buf)
        except Exception as e:
            last_err = e
            log.warning(f"TTS fallback failed: {type(e).__name__}: {str(e)[:80]}")

    raise last_err


@router.post("/api/tts")
async def tts_synthesize(req: TTSRequest):
    """文本转语音 → MP3（角色扮演 / 章节朗读）

    前端降级策略: 503 时自动切换 Web Speech API（系统 TTS）。
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "文本不能为空")
    if len(text) > 2000:
        raise HTTPException(400, "文本过长（最多 2000 字）")

    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _cache_key(text, req.voice, req.rate, req.pitch)
    path = _cached_path(key)

    # 缓存命中
    if os.path.exists(path):
        if time.time() - os.path.getmtime(path) < CACHE_TTL:
            with open(path, "rb") as f:
                data = f.read()
            return Response(data, media_type="audio/mpeg",
                            headers={"X-TTS-Cache": "hit"})
        os.remove(path)

    try:
        data = await _synthesize(text, req.voice, req.rate, req.pitch)
    except Exception as e:
        log.error(f"TTS synthesis failed: {e}")
        raise HTTPException(503, f"语音合成暂不可用：{type(e).__name__}")

    # 写缓存
    try:
        with open(path, "wb") as f:
            f.write(data)
    except Exception as e:
        log.warning(f"TTS cache write failed: {e}")

    return Response(data, media_type="audio/mpeg",
                    headers={"X-TTS-Cache": "miss"})


@router.get("/api/tts/voices")
async def tts_voices():
    """可用音色列表（中文优先，只返回实测可用白名单）"""
    try:
        import edge_tts
        voices = await edge_tts.list_voices()
        cn = sorted(
            [{"name": v["ShortName"], "gender": v.get("Gender", ""),
              "locale": v.get("Locale", "")}
             for v in voices
             if v.get("Locale", "").startswith("zh-CN")
             and v["ShortName"] in AVAILABLE_VOICES],
            key=lambda x: x["name"],
        )
        return {"count": len(cn), "voices": cn}
    except Exception as e:
        log.warning(f"List voices failed: {e}")
        # 兜底：返回白名单
        return {"count": len(AVAILABLE_VOICES),
                "voices": [{"name": v, "gender": "Female" if "Xiao" in v else "Male",
                            "locale": "zh-CN"} for v in AVAILABLE_VOICES]}
