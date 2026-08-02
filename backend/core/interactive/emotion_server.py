"""EmotionTTS 子进程服务 — CosyVoice2 情感语音引擎（独立进程，HTTP :8791）

由后端 EmotionTTS 自动拉起，跑在 indextts venv（torch/CUDA 与后端隔离）。
首次启动加载模型 ~10s，之后常驻。

v3.5.6: 引擎从 IndexTTS-2 切换到 CosyVoice2-0.5B
- 显存 2.4GB（IndexTTS-2 6.5GB+，12GB 卡 OOM 的根因）
- 加载 9s（IndexTTS-2 110s）
- 真人克隆 7-12s/句（IndexTTS-2 17-30s，且并发必挂）
- 真人音色 → inference_zero_shot 克隆；情感 → inference_instruct2 指令

端点：
GET  /status        → {"ready": bool, "model": str}
POST /synthesize    → wav bytes（body: text/char_name/emotion/voice/rate/pitch/real_voice）

用法：python emotion_server.py [port]
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [emotion-tts] %(message)s")
log = logging.getLogger("emotion_server")

# v3.5.5: 全局推理锁——模型单实例非线程安全，并发推理会互相污染缓存/状态
# （预合成+播放并发 → 偶发失败）。锁内串行化，排队而非并行。
_INFER_LOCK = threading.Lock()

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# cosyvoice 源码目录（Claw/cosyvoice_tmp，未 pip 安装时直接走源码）
_COSY_SRC = os.path.normpath(os.path.join(_BASE_DIR, "..", "..", "cosyvoice_tmp"))
if os.path.isdir(os.path.join(_COSY_SRC, "cosyvoice")):
    if _COSY_SRC not in sys.path:
        sys.path.insert(0, _COSY_SRC)
    _MATCHA_DIR = os.path.join(_COSY_SRC, "third_party", "matcha")
    if os.path.isdir(_MATCHA_DIR) and _MATCHA_DIR not in sys.path:
        sys.path.insert(0, _MATCHA_DIR)

MODEL_ROOT = os.environ.get("COSYVOICE_MODEL_ROOT", "").strip() or os.path.normpath(
    os.path.join(_COSY_SRC, "pretrained_models", "CosyVoice2-0.5B"))

# 情感 → 情感指令文本（instruct2 模式）
EMOTION_TEXT_PROMPTS = {
    "平静": None,  # 无情感指令 → 零样本克隆（角色自身声音特质）
    "愤怒": "用愤怒的语气，声音颤抖地说",
    "悲伤": "用悲伤哽咽的语气，带着哭腔说",
    "喜悦": "用喜悦轻快的语气，眉眼弯弯地说",
    "紧张": "用紧张不安的语气，声音发紧地说",
    "冷漠": "用冷漠平淡的语气，不带一丝感情地说",
}

# zero_shot 参考文本（CosyVoice2 需要 prompt_text；真人音色无真实文本，用占位）
ZERO_SHOT_REF_TEXT = "嗯，我在听。你说吧。"

REF_DIR = os.path.join(_BASE_DIR, "data", "tts_refs")
VOICES_DIR = os.path.join(REF_DIR, "voices")

# ── 真人音色库（IndexTTS-2 官方 Demo 参考音频，真人录音）──
# 性格标签根据官方 cases.jsonl 示例文本归纳
REAL_VOICES = {
    "voice_01": {"file": "voice_01.wav", "gender": "男", "style": "少年音，翻译腔，清爽"},
    "voice_02": {"file": "voice_02.wav", "gender": "女", "style": "古风宫廷，清冷御姐"},
    "voice_03": {"file": "voice_03.wav", "gender": "女", "style": "亲切主播，带货感"},
    "voice_04": {"file": "voice_04.wav", "gender": "男", "style": "沉稳专业，可靠大叔"},
    "voice_05": {"file": "voice_05.wav", "gender": "男", "style": "解说腔，冷静叙述"},
    "voice_06": {"file": "voice_06.wav", "gender": "男", "style": "播客主播，活力阳光"},
    "voice_07": {"file": "voice_07.wav", "gender": "男", "style": "说书人，江湖气"},
    "voice_08": {"file": "voice_08.wav", "gender": "男", "style": "中年父亲，威严沙哑"},
    "voice_09": {"file": "voice_09.wav", "gender": "女", "style": "软萌少女，撒娇"},
    "voice_11": {"file": "voice_11.wav", "gender": "女", "style": "古风女主，悲情婉转"},
    "voice_12": {"file": "voice_12.wav", "gender": "女", "style": "清亮少女，紧张感"},
}


def list_real_voices() -> list:
    """真人音色列表（带文件存在性）"""
    out = []
    for vid, meta in REAL_VOICES.items():
        path = os.path.join(VOICES_DIR, meta["file"])
        out.append({"id": vid, "gender": meta["gender"], "style": meta["style"],
                    "available": os.path.exists(path)})
    return out

_model = None
_model_error = ""


def _patch_audio_backend():
    """torchaudio 2.11 已移除 soundfile 后端（只支持 torchcodec）→ 重写 load_wav"""
    import torch
    import torchaudio
    import soundfile as sf

    def load_wav_sf(wav, target_sr, min_sr=16000):
        data, sample_rate = sf.read(wav, dtype="float32")
        speech = torch.from_numpy(data)
        if speech.dim() > 1:
            speech = speech.mean(dim=1)
        speech = speech.unsqueeze(0)
        if sample_rate != target_sr:
            assert sample_rate >= min_sr
            speech = torchaudio.transforms.Resample(sample_rate, target_sr)(speech)
        return speech

    from cosyvoice.utils import file_utils
    file_utils.load_wav = load_wav_sf


def _load_model():
    global _model, _model_error
    if _model is not None or _model_error:
        return
    t0 = time.time()
    try:
        import torch
        if not torch.cuda.is_available():
            _model_error = "无可用 CUDA GPU"
            log.warning(_model_error)
            return
        if not os.path.exists(os.path.join(MODEL_ROOT, "llm.pt")):
            _model_error = f"模型目录缺失: {MODEL_ROOT}（llm.pt 不存在，需先下载 CosyVoice2-0.5B）"
            log.warning(_model_error)
            return
        _patch_audio_backend()
        from cosyvoice.cli.cosyvoice import CosyVoice2
        _model = CosyVoice2(MODEL_ROOT, load_jit=False, load_trt=False, fp16=True)
        log.info(f"CosyVoice2 模型加载完成 ({time.time()-t0:.1f}s)")
    except Exception as e:
        _model_error = f"{type(e).__name__}: {str(e)[:150]}"
        log.warning(f"模型加载失败: {_model_error}")


async def _edge_tts(text: str, out_path: str, voice: str, rate: str, pitch: str):
    import edge_tts
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(out_path)


def _emotion_ref(emotion: str) -> str:
    """情感参考音频（v2 起用 use_emo_text 模式，不再需要情感音频——保留占位逻辑）"""
    return ""


def _char_ref(char_name: str, voice: str, rate: str, pitch: str) -> str:
    """角色音色参考音频（edge-tts 合成 15s 台词）"""
    key = hashlib.md5(f"{char_name}|{voice}|{rate}|{pitch}".encode()).hexdigest()[:10]
    path = os.path.join(REF_DIR, f"char_{key}.mp3")
    if not os.path.exists(path):
        try:
            text = (f"{char_name}。很久不见了。"
                    "我一直在想，如果当初我们能再坦诚一些，"
                    "是不是一切都会不一样。可事到如今，说这些又有什么用呢。"
                    "罢了。你愿意听，我就说给你听。")
            asyncio.run(_edge_tts(text, path, voice, rate, pitch))
            log.info(f"角色音色参考生成: {char_name} ({voice})")
        except Exception as e:
            log.warning(f"角色参考失败 {char_name}: {e}")
    return path if os.path.exists(path) else ""


def _synthesize(text: str, char_name: str, emotion: str,
                voice: str, rate: str, pitch: str, real_voice: str = "") -> bytes:
    """IndexTTS2 合成：角色音色 spk_audio_prompt + 情感文本提示（qwen_emo）→ wav bytes

    real_voice 指定时用真人音色库参考音频（人味），否则用 edge-tts 合成角色参考。
    v3.5.5: 全局锁串行化（模型非线程安全）+ OOM 容错（清缓存重试一次）。
    """
    with _INFER_LOCK:
        try:
            return _synthesize_locked(text, char_name, emotion, voice, rate, pitch, real_voice)
        except RuntimeError as e:
            if "out of memory" in str(e).lower() or "cuda" in str(e).lower() and "memory" in str(e).lower():
                log.warning("CUDA OOM，清缓存后重试一次…")
                import torch
                torch.cuda.empty_cache()
                time.sleep(1)
                return _synthesize_locked(text, char_name, emotion, voice, rate, pitch, real_voice)
            raise


def _synthesize_locked(text: str, char_name: str, emotion: str,
                       voice: str, rate: str, pitch: str, real_voice: str = "") -> bytes:
    _load_model()
    if _model is None:
        raise RuntimeError(_model_error or "模型未加载")
    # v3.5.1: 真人音色库优先
    if real_voice in REAL_VOICES:
        ref = os.path.join(VOICES_DIR, REAL_VOICES[real_voice]["file"])
        if not os.path.exists(ref):
            raise RuntimeError(f"真人音色文件缺失: {real_voice}")
    else:
        ref = _char_ref(char_name, voice, rate, pitch)
        if not ref:
            raise RuntimeError(f"角色参考音频缺失: {char_name}")
    emo_text = EMOTION_TEXT_PROMPTS.get(emotion)
    if emo_text:
        # 情感指令模式：真人音色/角色音色 + 情感文本（instruct2）
        chunks = [j["tts_speech"] for j in _model.inference_instruct2(
            text, emo_text, ref, stream=False)]
    else:
        # 平静：零样本克隆参考音频（人味）
        chunks = [j["tts_speech"] for j in _model.inference_zero_shot(
            text, ZERO_SHOT_REF_TEXT, ref, stream=False)]
    if not chunks:
        raise RuntimeError("CosyVoice2 返回空结果")
    import torch
    import numpy as np
    wav = torch.cat(chunks, dim=1).squeeze(0).cpu().numpy()
    wav = np.clip(wav * 32767, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    _write_wav(buf, 24000, wav)
    return buf.getvalue()


def _write_wav(buf, sr: int, samples: np.ndarray):
    import struct
    data = samples.tobytes()
    n = len(data)
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + n))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", n))
    buf.write(data)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/status"):
            self._send_json({"ready": _model is not None, "model": MODEL_ROOT,
                             "error": _model_error})
        elif self.path.startswith("/voices"):
            self._send_json({"voices": list_real_voices()})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/synthesize":
            self._send_json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8", "ignore"))
            text = str(payload.get("text", ""))[:500]
            char_name = str(payload.get("char_name", "角色"))[:30]
            emotion = str(payload.get("emotion", "平静"))[:10]
            voice = str(payload.get("voice", "zh-CN-XiaoxiaoNeural"))[:50]
            rate = str(payload.get("rate", "+0%"))[:10]
            pitch = str(payload.get("pitch", "+0Hz"))[:10]
            real_voice = str(payload.get("real_voice", ""))[:30]
            if not text.strip():
                self._send_json({"error": "text 为空"}, 400)
                return
            t0 = time.time()
            wav = _synthesize(text, char_name, emotion, voice, rate, pitch, real_voice)
            log.info(f"synthesize {char_name}/{emotion}/{real_voice or voice} {len(text)}字 ({time.time()-t0:.1f}s)")
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)
        except Exception as e:
            log.warning(f"synthesize 失败: {e}")
            self._send_json({"error": str(e)[:200]}, 500)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
    log.info(f"EmotionTTS server listening on {port} (model: {MODEL_ROOT})")
    # 预热：后台加载模型 + 跑一次 dummy 合成（触发 cuDNN autotune，首次请求不再慢）
    import threading

    def _warmup():
        try:
            _load_model()
            if _model is not None:
                ref = os.path.join(VOICES_DIR, "voice_11.wav")
                if os.path.exists(ref):
                    t0 = time.time()
                    for _ in _model.inference_zero_shot(
                            "嗯，我知道了。", ZERO_SHOT_REF_TEXT, ref, stream=False):
                        pass
                    log.info(f"预热完成 ({time.time()-t0:.1f}s)")
        except Exception as e:
            log.warning(f"预热失败（不影响使用）: {type(e).__name__}: {str(e)[:100]}")

    threading.Thread(target=_warmup, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
