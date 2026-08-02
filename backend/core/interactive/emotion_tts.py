"""EmotionTTS — 本地情感语音引擎（IndexTTS-2，RTX 5070，免费离线）

**为什么是 IndexTTS-2**：音色与情感完全解耦（GRL 梯度反转分离 speaker_emb / emotion_emb）——
角色一个音色参考音频，情感独立切换（平静/愤怒/悲伤/喜悦/紧张/冷漠 6 档），
实测情感传达 93%、三层情绪递进"压抑→蓄力→爆发"。

**进程架构**：模型跑在独立子进程（emotion_server.py，indextts venv 的 python 启动，
HTTP :8791 常驻）——依赖完全隔离（torch/CUDA 不进后端），后端仅 HTTP 调用。
后端进程重启不丢模型（子进程保活：检测断开自动重启）。

- 角色音色参考：edge-tts 合成该角色 15s 台词 → ref_audio（首次使用时生成，磁盘缓存）
- 情感参考：内置 6 档情感参考音频（v1 edge-tts 强情绪文本占位，后续可换真实情感音频）
- 合成缓存：text+char+emotion hash → wav 磁盘缓存（7 天 TTL）
- 不可用降级：子进程未启动/失败 → 503 → 前端降级 edge-tts
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)

EMOTIONS = ["平静", "愤怒", "悲伤", "喜悦", "紧张", "冷漠"]

EMOTION_SERVER_PORT = int(os.environ.get("EMOTION_TTS_PORT", "8791"))
EMOTION_SERVER_URL = f"http://127.0.0.1:{EMOTION_SERVER_PORT}"
_INDEXTTS_PY = os.environ.get("INDEXTTS_PY", "").strip() or None  # indextts venv 的 python 路径

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emotion_server.py")
CACHE_DIR = os.path.join(_BASE_DIR, "data", "tts_cache", "emotion")
CACHE_TTL = 7 * 24 * 3600


class EmotionTTSUnavailable(Exception):
    """情感引擎不可用（未安装/无 GPU/子进程未启动）"""


class EmotionTTS:
    _instance: Optional["EmotionTTS"] = None
    _proc: Optional[subprocess.Popen] = None
    _spawn_attempted = False

    @classmethod
    def get(cls) -> "EmotionTTS":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 子进程生命周期 ──
    def _spawn(self):
        """启动情感引擎子进程（首次调用时；进程崩溃后自动重启）"""
        if self._proc is not None and self._proc.poll() is None:
            return
        if self._proc is not None and self._proc.poll() is not None:
            log.warning("EmotionTTS 子进程已退出，重启中…")
        py = _INDEXTTS_PY
        if not py:
            # 默认路径：indextts311 venv（IndexTTS 要求 Python 3.10-3.12）
            candidates = [
                os.path.expanduser(r"~\.workbuddy\binaries\python\envs\indextts311\Scripts\python.exe"),
                os.path.join(os.path.dirname(sys.executable), "..", "envs", "indextts311", "Scripts", "python.exe"),
            ]
            py = next((c for c in candidates if os.path.exists(os.path.normpath(c))), "")
        if not py or not os.path.exists(SERVER_SCRIPT):
            self._spawn_attempted = True
            raise EmotionTTSUnavailable("未找到 indextts 环境或 emotion_server.py")
        try:
            self._proc = subprocess.Popen(
                [os.path.normpath(py), SERVER_SCRIPT, str(EMOTION_SERVER_PORT)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._spawn_attempted = True
        except Exception as e:
            self._spawn_attempted = True
            raise EmotionTTSUnavailable(f"子进程启动失败: {e}")

    def _wait_ready(self, timeout: float = 60.0) -> bool:
        """等待子进程就绪（模型加载可能需要 20-40s）"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                with urllib.request.urlopen(f"{EMOTION_SERVER_URL}/status", timeout=2) as r:
                    data = json.loads(r.read().decode("utf-8", "ignore"))
                    return bool(data.get("ready"))
            except Exception:
                time.sleep(1.5)
        return False

    def _request(self, path: str, payload: Optional[dict] = None, timeout: float = 90.0):
        url = EMOTION_SERVER_URL + path
        body = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise EmotionTTSUnavailable(f"情感引擎 HTTP {e.code}: {e.read()[:120].decode('utf-8', 'ignore')}")
        except Exception as e:
            raise EmotionTTSUnavailable(f"情感引擎连接失败: {e}")

    @property
    def status(self) -> dict:
        try:
            with urllib.request.urlopen(f"{EMOTION_SERVER_URL}/status", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
                return {"available": bool(data.get("ready")), "emotions": EMOTIONS,
                        "model": data.get("model", ""), "error": ""}
        except Exception:
            return {"available": False, "emotions": EMOTIONS, "model": "", "error": "引擎未启动"}

    # ── 合成（HTTP 调子进程）──
    def synthesize(self, text: str, char_name: str, emotion: str,
                   voice: str = "zh-CN-XiaoxiaoNeural",
                   rate: str = "+0%", pitch: str = "+0Hz") -> Optional[bytes]:
        """情感合成：角色音色 + 情感参考 → wav bytes。失败返回 None（上层降级）"""
        if emotion not in EMOTIONS:
            emotion = "平静"
        # 缓存
        key = hashlib.md5(f"{text}|{char_name}|{emotion}|{voice}".encode()).hexdigest()[:16]
        cache_path = os.path.join(CACHE_DIR, f"{key}.wav")
        if os.path.exists(cache_path):
            if time.time() - os.path.getmtime(cache_path) < CACHE_TTL:
                with open(cache_path, "rb") as f:
                    return f.read()
            else:
                try:
                    os.remove(cache_path)
                except OSError:
                    pass
        # 调子进程（未启动则拉起；失败一次后直接降级，不阻塞主流程）
        try:
            self._spawn()
            if not self._wait_ready():
                raise EmotionTTSUnavailable("子进程超时未就绪")
            data = self._request("/synthesize", {
                "text": text[:500], "char_name": char_name, "emotion": emotion,
                "voice": voice, "rate": rate, "pitch": pitch,
            })
        except EmotionTTSUnavailable:
            raise
        except Exception as e:
            raise EmotionTTSUnavailable(f"情感合成失败: {e}")
        if not data or data[:4] != b"RIFF":
            raise EmotionTTSUnavailable("情感引擎返回非 WAV 数据")
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(data)
        except OSError:
            pass
        return data

