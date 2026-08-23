"""NovelGenerator — Android configuration"""
import os
import sys

# LLM
# LLM_* 为统一供应商配置；DEEPSEEK_* 保留为旧版 .env 兼容别名。
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").strip().lower() or "deepseek"
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
LLM_MODEL = os.getenv("LLM_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))
DEEPSEEK_API_KEY = LLM_API_KEY
DEEPSEEK_BASE_URL = LLM_BASE_URL
DEEPSEEK_MODEL = LLM_MODEL

# Storage — Android: use app external storage
_HERE = os.path.dirname(os.path.abspath(__file__))
_EXTERNAL = os.getenv("EXTERNAL_STORAGE", _HERE)
_DEFAULT_NOVELS = os.path.join(_EXTERNAL, "NovelGenerator", "novels")
NOVELS_DIR = os.path.abspath(os.getenv("NOVELGEN_NOVELS_DIR", _DEFAULT_NOVELS))
LLM_CONFIG_FILE = os.path.join(os.path.dirname(NOVELS_DIR), "llm_config.json")

# Web dir
WEB_DIR = os.getenv("NOVELGEN_WEB_DIR", os.path.join(_HERE, "web"))

# Server
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8899"))
# 默认不启用跨域；需要跨域调试时显式配置来源。
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "")

# Writing
MAX_CONTEXT_TOKENS = 8000
DEFAULT_CHAPTER_WORDS = 1500
MAX_CHAPTER_TOKENS = 4000
