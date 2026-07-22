"""NovelGenerator — Configuration"""
import os
from dotenv import load_dotenv

load_dotenv()

# LLM
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# Storage — 绝对路径，环境变量覆盖
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_NOVELS = os.path.join(os.path.dirname(_HERE), "novels")
NOVELS_DIR = os.path.abspath(os.getenv("NOVELS_DIR", _DEFAULT_NOVELS))

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")

# 写作参数
MAX_CONTEXT_TOKENS = 8000
DEFAULT_CHAPTER_WORDS = 3000        # 默认每章字数
MAX_CHAPTER_TOKENS = 6000           # 单章 max_tokens 硬上限（Writer 自动 min(target*3, 6000)）
