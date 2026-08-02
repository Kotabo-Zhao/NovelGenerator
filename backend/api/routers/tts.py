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

# 实测可用音色白名单（2026-08-02 实测：zh-CN 部分音色被微软限区域，
# 但 zh-HK/zh-TW 全部可用；失败时自动降级）
AVAILABLE_VOICES = [
    "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-XiaoxuanNeural",
    "zh-CN-YunxiNeural", "zh-CN-YunxiaNeural", "zh-CN-YunjianNeural",
    "zh-CN-YunyangNeural",
    "zh-HK-HiuGaaiNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural",
    "zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural", "zh-TW-YunJheNeural",
]

# 全量中文音色风格表（v3.2: 全部音色 + 风格标注）
VOICE_STYLES = {
    "zh-CN-XiaoxiaoNeural":   {"gender": "女", "style": "温暖知性，适合温柔女主/旁白"},
    "zh-CN-XiaoyiNeural":     {"gender": "女", "style": "活泼俏皮，适合元气少女"},
    "zh-CN-YunjianNeural":    {"gender": "男", "style": "浑厚低沉，适合大叔/将领"},
    "zh-CN-YunxiNeural":      {"gender": "男", "style": "清爽温和，适合阳光青年/男主"},
    "zh-CN-YunxiaNeural":     {"gender": "男", "style": "清亮少年感，适合弟弟/年轻角色"},
    "zh-CN-YunyangNeural":    {"gender": "男", "style": "沉稳磁性，适合反派/枭雄"},
    "zh-CN-XiaoxuanNeural":   {"gender": "女", "style": "清冷空灵，适合仙子/高冷御姐"},
    "zh-CN-XiaomoNeural":     {"gender": "女", "style": "成熟魅惑，适合御姐/妖女"},
    "zh-CN-XiaomengNeural":   {"gender": "女", "style": "萌软甜美，适合萝莉"},
    "zh-CN-XiaohanNeural":    {"gender": "女", "style": "温柔亲切，适合姐姐/老师"},
    "zh-CN-XiaoruiNeural":    {"gender": "女", "style": "沉稳知性，适合女强人/掌权者"},
    "zh-CN-XiaoshuangNeural": {"gender": "女", "style": "童声清脆，适合小女孩"},
    "zh-CN-XiaozhenNeural":   {"gender": "女", "style": "甜美温柔，适合邻家少女"},
    "zh-CN-YunhaoNeural":     {"gender": "男", "style": "活力阳光，适合热血青年"},
    "zh-CN-YunyeNeural":      {"gender": "男", "style": "邪魅低音，适合反派/神秘角色"},
    "zh-HK-HiuGaaiNeural":    {"gender": "女", "style": "粤语女声，港风"},
    "zh-HK-HiuMaanNeural":    {"gender": "女", "style": "粤语女声，成熟"},
    "zh-HK-WanLungNeural":    {"gender": "男", "style": "粤语男声，沉稳"},
    "zh-TW-HsiaoChenNeural":  {"gender": "女", "style": "台语女声，清新"},
    "zh-TW-HsiaoYuNeural":    {"gender": "女", "style": "台语女声，温柔"},
    "zh-TW-YunJheNeural":     {"gender": "男", "style": "台语男声，斯文"},
}


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
    """全部中文音色列表（含风格标注 + 可用性标记）

    v3.2: 以 VOICE_STYLES 风格表为准返回全量（zh-CN 全部 + zh-HK + zh-TW），
    edge_tts.list_voices 可能漏列部分音色，仅作为补充。
    available=true 表示实测可用（白名单）；false 表示可能受限（自动降级到近似音色）。
    """
    # 基础：风格表全量（保证不漏音色）
    result = []
    for name, info in VOICE_STYLES.items():
        result.append({
            "name": name,
            "gender": info.get("gender", ""),
            "locale": "zh-CN" if name.startswith("zh-CN") else name[:5],
            "style": info.get("style", ""),
            "available": name in AVAILABLE_VOICES,
        })
    # 补充：list_voices 里风格表外的（如方言音色）
    try:
        import edge_tts
        voices = await edge_tts.list_voices()
        known = {v["name"] for v in result}
        for v in voices:
            loc = v.get("Locale", "")
            if not (loc.startswith("zh-CN") or loc.startswith("zh-HK") or loc.startswith("zh-TW")):
                continue
            name = v["ShortName"]
            if name in known:
                continue
            result.append({
                "name": name,
                "gender": v.get("Gender", ""),
                "locale": loc,
                "style": "",
                "available": name in AVAILABLE_VOICES,
            })
    except Exception as e:
        log.warning(f"List voices failed: {e}")
    result.sort(key=lambda x: (x["locale"], x["name"]))
    return {"count": len(result), "voices": result}


# ── v3.5: 情感语音引擎（IndexTTS-2 本地子进程，免费离线）──
class EmotionTTSRequest(BaseModel):
    text: str
    char_name: str = "角色"
    emotion: str = "平静"
    voice: str = DEFAULT_VOICE
    rate: str = "+0%"
    pitch: str = "+0Hz"
    real_voice: str = ""   # v3.5.1: 真人音色库 id（voice_01~12），空则用 voice 合成参考


@router.post("/api/tts/emotion")
async def tts_emotion_synthesize(req: EmotionTTSRequest):
    """情感语音合成 → WAV（角色音色 + 情感参考，IndexTTS-2 本地推理）

    引擎不可用 → 503，前端自动降级普通 edge-tts 语音。
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "文本不能为空")
    if len(text) > 500:
        raise HTTPException(400, "文本过长（最多 500 字）")
    from core.interactive.emotion_tts import EmotionTTS, EmotionTTSUnavailable
    try:
        data = await asyncio.to_thread(
            EmotionTTS.get().synthesize, text, req.char_name, req.emotion,
            req.voice, req.rate, req.pitch, req.real_voice)
    except EmotionTTSUnavailable as e:
        raise HTTPException(503, f"情感引擎不可用: {e}")
    except Exception as e:
        log.error(f"Emotion TTS failed: {e}")
        raise HTTPException(503, f"情感合成失败: {type(e).__name__}")
    if not data:
        raise HTTPException(503, "情感合成失败（空结果）")
    # 修复：HTTP 头只允许 latin-1，中文情感名需 URL 编码
    from urllib.parse import quote
    return Response(content=data, media_type="audio/wav",
                    headers={"X-Emotion": quote(req.emotion), "X-TTS-Engine": "indextts"})


@router.get("/api/tts/emotion/status")
async def tts_emotion_status():
    """情感引擎状态 + 真人音色库（前端用于显示可用性和音色选择）"""
    from core.interactive.emotion_tts import EmotionTTS
    st = EmotionTTS.get().status
    st["real_voices"] = EmotionTTS.get().real_voices()
    return {"ok": True, **st}
