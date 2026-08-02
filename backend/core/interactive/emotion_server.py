"""EmotionTTS 子进程服务 — IndexTTS-2 情感语音引擎（独立进程，HTTP :8791）

由后端 EmotionTTS 自动拉起，跑在 indextts venv（torch/CUDA 与后端隔离）。
首次启动加载模型 20-40s，之后常驻。

端点：
GET  /status        → {"ready": bool, "model": str}
POST /synthesize    → wav bytes（body: text/char_name/emotion/voice/rate/pitch）

用法：python emotion_server.py [port]
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [emotion-tts] %(message)s")
log = logging.getLogger("emotion_server")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# index-tts 源码目录（Claw/index-tts，未 pip 安装时直接走源码）
_INDEXTTS_SRC = os.path.normpath(os.path.join(_BASE_DIR, "..", "..", "index-tts"))
if os.path.isdir(os.path.join(_INDEXTTS_SRC, "indextts")) and _INDEXTTS_SRC not in sys.path:
    sys.path.insert(0, _INDEXTTS_SRC)

MODEL_ROOT = os.environ.get("INDEXTTS_MODEL_ROOT", "").strip() or os.path.normpath(
    os.path.join(_INDEXTTS_SRC, "models", "index-tts-v2"))

# 情感 → 情感文本提示（use_emo_text 模式：qwen_emo 从文本检测情感向量）
EMOTION_TEXT_PROMPTS = {
    "平静": None,  # 无情感参考 → 角色自身中性情感
    "愤怒": "他压抑着怒火，一字一顿地说道",
    "悲伤": "她的声音有些哽咽，带着哭腔",
    "喜悦": "她眉眼弯弯，语气轻快地说",
    "紧张": "他声音发紧，带着明显的不安",
    "冷漠": "她语气冰冷，不带一丝感情",
}

REF_DIR = os.path.join(_BASE_DIR, "data", "tts_refs")

_model = None
_model_error = ""


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
        from indextts.infer_v2 import IndexTTS2
        cfg = os.path.join(MODEL_ROOT, "config.yaml")
        if not os.path.exists(cfg):
            _model_error = f"模型目录缺失: {MODEL_ROOT}（config.yaml 不存在，需先下载模型）"
            log.warning(_model_error)
            return
        _model = IndexTTS2(cfg_path=cfg, model_dir=MODEL_ROOT,
                           device="cuda", use_fp16=True)
        log.info(f"IndexTTS2 模型加载完成 ({time.time()-t0:.1f}s)")
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
                voice: str, rate: str, pitch: str) -> bytes:
    """IndexTTS2 合成：角色音色 spk_audio_prompt + 情感文本提示（qwen_emo）→ wav bytes"""
    _load_model()
    if _model is None:
        raise RuntimeError(_model_error or "模型未加载")
    ref = _char_ref(char_name, voice, rate, pitch)
    if not ref:
        raise RuntimeError(f"角色参考音频缺失: {char_name}")
    emo_text = EMOTION_TEXT_PROMPTS.get(emotion)
    # 返回 (sampling_rate, wav_data int16 numpy)；output_path=None 走内存返回
    result = _model.infer(
        spk_audio_prompt=ref,
        text=text,
        output_path=None,
        use_emo_text=emo_text is not None,
        emo_text=emo_text or text,
        temperature=0.3,
        top_k=30,
        interval_silence=200,
    )
    if result is None:
        raise RuntimeError("IndexTTS2 返回空结果")
    sr, wav = result
    import io
    import numpy as np
    wav = np.asarray(wav)
    if wav.dtype != np.int16:
        wav = np.clip(wav * 32767, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    _write_wav(buf, int(sr), wav)
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
            if not text.strip():
                self._send_json({"error": "text 为空"}, 400)
                return
            t0 = time.time()
            wav = _synthesize(text, char_name, emotion, voice, rate, pitch)
            log.info(f"synthesize {char_name}/{emotion} {len(text)}字 ({time.time()-t0:.1f}s)")
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
    # 预热：后台加载模型（首个请求到达时可能已在加载）
    import threading
    threading.Thread(target=_load_model, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
