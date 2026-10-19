"""NovelGenerator — Android configuration"""
import os
import sys
import json

# LLM
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# Storage — Android: use app external storage
_HERE = os.path.dirname(os.path.abspath(__file__))
_EXTERNAL = os.getenv("EXTERNAL_STORAGE", _HERE)
_DEFAULT_NOVELS = os.path.join(_EXTERNAL, "NovelGenerator", "novels")
NOVELS_DIR = os.path.abspath(os.getenv("NOVELGEN_NOVELS_DIR", _DEFAULT_NOVELS))

PROVIDER_FILE = os.path.join(os.path.dirname(NOVELS_DIR), "_settings", "provider.json")
try:
    with open(PROVIDER_FILE, encoding="utf-8") as _file:
        _provider = json.load(_file)
    DEEPSEEK_API_KEY = _provider.get("api_key", DEEPSEEK_API_KEY)
    DEEPSEEK_BASE_URL = _provider.get("base_url", DEEPSEEK_BASE_URL)
    DEEPSEEK_MODEL = _provider.get("model", DEEPSEEK_MODEL)
except (OSError, ValueError, AttributeError):
    pass

# Web dir
WEB_DIR = os.getenv("NOVELGEN_WEB_DIR", os.path.join(_HERE, "web"))

# Server
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8899"))
SUPPORTED_EXPORT_FORMATS = ("txt",)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

# Writing
MAX_CONTEXT_TOKENS = 8000
DEFAULT_CHAPTER_WORDS = 1500
MAX_CHAPTER_TOKENS = 4000
