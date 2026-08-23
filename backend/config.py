"""NovelGenerator — Configuration"""
import os
from dotenv import load_dotenv

load_dotenv()

# LLM
#
# LLM_* 是新的统一配置；DEEPSEEK_* 保留为兼容旧版 .env、脚本和第三方
# 集成。未设置 LLM_* 时自动回退到旧变量，因此升级不需要立即重写 .env。
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek").strip().lower() or "deepseek"
LLM_API_KEY = os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
LLM_BASE_URL = os.getenv("LLM_BASE_URL", os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
LLM_MODEL = os.getenv("LLM_MODEL", os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))

# 兼容旧代码：业务模块仍可读取 DEEPSEEK_*，运行时切换供应商时由 Engine
# 同步这些别名，避免一次性修改几十个历史调用点。
DEEPSEEK_API_KEY = LLM_API_KEY
DEEPSEEK_BASE_URL = LLM_BASE_URL
DEEPSEEK_MODEL = LLM_MODEL

# Storage — 环境变量允许覆盖；相对路径固定相对于项目根目录，
# 不能随着 uvicorn 的启动目录漂移到 backend/novels。
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
_DEFAULT_NOVELS = os.path.join(_PROJECT_ROOT, "novels")


def _resolve_project_path(value: str) -> str:
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        expanded = os.path.join(_PROJECT_ROOT, expanded)
    return os.path.abspath(expanded)


NOVELS_DIR = _resolve_project_path(os.getenv("NOVELS_DIR", _DEFAULT_NOVELS))
LLM_CONFIG_FILE = os.path.join(os.path.dirname(NOVELS_DIR), "llm_config.json")

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
# 默认不启用跨域；浏览器同源页面不需要 CORS，小程序使用合法业务域名。
# 如需本地跨域调试，请显式配置 CORS_ORIGINS，不要把 * 带入生产。
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "")

# 写作参数
MAX_CONTEXT_TOKENS = 8000
DEFAULT_CHAPTER_WORDS = 1500        # 默认每章字数（降低以加快生成速度）
MAX_CHAPTER_TOKENS = 4000           # 单章 max_tokens 硬上限（Writer 自动 min(target*3, 4000)）
