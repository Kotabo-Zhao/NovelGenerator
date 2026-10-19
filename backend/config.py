"""NovelGenerator — Configuration"""
import os
import json
from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
load_dotenv(os.path.join(_ROOT, ".env"))

# LLM
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# Storage — 绝对路径，环境变量覆盖
_DEFAULT_NOVELS = os.path.join(os.path.dirname(_HERE), "novels")
_novels = os.getenv("NOVELS_DIR", _DEFAULT_NOVELS)
NOVELS_DIR = os.path.abspath(_novels if os.path.isabs(_novels) else os.path.join(_ROOT, _novels))
PROVIDER_FILE = os.path.join(os.path.dirname(NOVELS_DIR), "_settings", "provider.json")
try:
    with open(PROVIDER_FILE, encoding="utf-8") as _file:
        _provider = json.load(_file)
    DEEPSEEK_API_KEY = _provider.get("api_key", DEEPSEEK_API_KEY)
    DEEPSEEK_BASE_URL = _provider.get("base_url", DEEPSEEK_BASE_URL)
    DEEPSEEK_MODEL = _provider.get("model", DEEPSEEK_MODEL)
except (OSError, ValueError, AttributeError):
    pass

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
SUPPORTED_EXPORT_FORMATS = ("txt", "pdf", "epub")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

# 写作参数
MAX_CONTEXT_TOKENS = 8000
DEFAULT_CHAPTER_WORDS = 1500        # 默认每章字数（降低以加快生成速度）
MAX_CHAPTER_TOKENS = 4000           # 单章 max_tokens 硬上限（Writer 自动 min(target*3, 4000)）
