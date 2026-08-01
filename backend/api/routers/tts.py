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
    """调用 edge-tts 合成（重试 2 次）"""
    import edge_tts

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
    """可用音色列表（中文优先）"""
    try:
        import edge_tts
        voices = await edge_tts.list_voices()
        cn = sorted(
            [{"name": v["ShortName"], "gender": v.get("Gender", ""),
              "locale": v.get("Locale", "")}
             for v in voices if v.get("Locale", "").startswith("zh-CN")],
            key=lambda x: x["name"],
        )
        return {"count": len(cn), "voices": cn}
    except Exception as e:
        log.warning(f"List voices failed: {e}")
        return {"count": 0, "voices": []}
