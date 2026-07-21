"""NovelGenerator — Configuration"""
import os
from dotenv import load_dotenv

load_dotenv()

# LLM
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# Storage
NOVELS_DIR = os.getenv("NOVELS_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "novels"))

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

# 写作参数
MAX_CONTEXT_TOKENS = 8000
DEFAULT_CHAPTER_WORDS = 3000
