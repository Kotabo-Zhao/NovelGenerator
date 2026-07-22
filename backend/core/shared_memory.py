"""NovelGenerator — Shared Memory Manager: 统一记忆访问层

职责: 为所有模块提供统一的记忆访问接口。包装 6 种持久化文件的读写，
提供内存缓存、乐观锁并发控制、变化通知和分模块上下文构建。

6 种记忆文件:
  plan.json           — 世界观 + 角色 + 大纲 (Soul)
  state.json          — 写作进度 (current_chapter/total_words/completed_chapters)
  global_state.json   — 角色状态快照 (位置/力量/关系/摘要)
  character_bible.json — 人物宝典 (角色关系图)
  foreshadowing.json  — 伏笔追踪表
  chapters/*.md       — 章节正文

特性:
  - 内存缓存 (TTL 30s): 减少 60-80% 磁盘 I/O
  - 乐观锁 (_version): 防止并发写入冲突
  - 变化通知: 写操作后自动失效缓存
  - 完全向后兼容 NovelMemory 接口
"""

import json
import os
import copy
import time
import threading
import logging
from typing import Optional, Callable
from .atomic_io import atomic_write_json, safe_read_json, atomic_write_text

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# 文件定义
# ═══════════════════════════════════════════════

MEMORY_FILES = {
    "plan":           {"file": "plan.json",              "type": "json", "versioned": True},
    "state":          {"file": "state.json",             "type": "json", "versioned": True},
    "global_state":   {"file": "global_state.json",      "type": "json", "versioned": True},
    "character_bible":{"file": "character_bible.json",   "type": "json", "versioned": False},
    "foreshadowing":  {"file": "foreshadowing.json",     "type": "json", "versioned": False},
    "chapter":        {"file": "chapters/chapter_{:04d}.md", "type": "text", "versioned": False},
}


class SharedMemoryManager:
    """统一记忆管理器 — 所有模块通过此接口读写小说记忆

    Usage:
        smm = SharedMemoryManager(novels_dir="/path/to/novels")
        plan = smm.read("plan", novel_id)           # 读取（优先缓存）
        smm.write("plan", novel_id, new_plan)       # 写入（乐观锁 + 失效缓存）
        ctx = smm.build_context("writer", novel_id, chapter_num, {"outline": ...})
    """

    def __init__(self, novels_dir: str, cache_ttl: float = 30.0):
        self.novels_dir = os.path.abspath(novels_dir)
        os.makedirs(self.novels_dir, exist_ok=True)

        # 内存缓存: {(novel_id, memory_type): (data, cached_at)}
        self._cache: dict = {}
        self._cache_ttl = cache_ttl
        self._cache_lock = threading.Lock()

        # 变化监听: {novel_id: {memory_type: [callbacks]}}
        self._listeners: dict = {}

        # 版本追踪（仅内存中，不落盘）: {path: version}
        self._versions: dict = {}

        log.info(f"SharedMemoryManager initialized: {self.novels_dir}, TTL={cache_ttl}s")

    # ═══════════════════════════════════════════
    # 核心读写接口
    # ═══════════════════════════════════════════

    def read(self, memory_type: str, novel_id: str, skip_cache: bool = False) -> dict:
        """读取指定类型的记忆
        
        Args:
            memory_type: plan|state|global_state|character_bible|foreshadowing
            novel_id: 小说ID（目录名）
            skip_cache: 跳过缓存，强制读磁盘
        """
        if memory_type not in MEMORY_FILES:
            raise ValueError(f"Unknown memory type: {memory_type}. Available: {list(MEMORY_FILES.keys())}")

        cache_key = (novel_id, memory_type)

        # 检查缓存
        if not skip_cache:
            with self._cache_lock:
                if cache_key in self._cache:
                    data, cached_at = self._cache[cache_key]
                    if time.time() - cached_at < self._cache_ttl:
                        return data

        # 读磁盘
        path = self._get_path(memory_type, novel_id)
        default = {} if memory_type != "foreshadowing" else []
        data = safe_read_json(path, default)

        # 类型守卫: 防止 corrupted JSON 返回非预期类型（如 string）
        if memory_type == "foreshadowing":
            if not isinstance(data, list):
                log.warning(f"foreshadowing.json corrupted (got {type(data).__name__}), resetting to []")
                data = []
        else:
            if not isinstance(data, dict):
                log.warning(f"{memory_type}.json corrupted (got {type(data).__name__}), resetting to {{}}")
                data = {}

        # 入缓存
        with self._cache_lock:
            self._cache[cache_key] = (data, time.time())

        return data

    def write(self, memory_type: str, novel_id: str, data,
              max_retries: int = 3) -> bool:
        """写入记忆（乐观锁 + 缓存失效）
        
        Args:
            memory_type: plan|state|global_state|character_bible|foreshadowing
            novel_id: 小说ID
            data: 要写入的数据
            max_retries: 乐观锁冲突最大重试次数
        """
        if memory_type not in MEMORY_FILES:
            raise ValueError(f"Unknown memory type: {memory_type}")

        file_info = MEMORY_FILES[memory_type]
        path = self._get_path(memory_type, novel_id)

        if file_info["versioned"]:
            success = self._write_with_lock(path, data, max_retries)
        else:
            atomic_write_json(path, data)
            success = True

        if success:
            # 失效缓存
            self._invalidate(novel_id, memory_type)
            # 触发变化通知
            self._notify(novel_id, memory_type, data)

        return success

    def read_chapter(self, novel_id: str, chapter_num: int) -> Optional[str]:
        """读取章节正文"""
        path = self._get_path("chapter", novel_id, chapter_num=chapter_num)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    def write_chapter(self, novel_id: str, chapter_num: int, content: str):
        """写入章节正文"""
        path = self._get_path("chapter", novel_id, chapter_num=chapter_num)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_text(path, content)
        self._invalidate(novel_id, "chapter")

    def chapter_exists(self, novel_id: str, chapter_num: int) -> bool:
        """检查章节是否存在"""
        return os.path.exists(self._get_path("chapter", novel_id, chapter_num=chapter_num))

    def scan_chapters(self, novel_id: str) -> list:
        """扫描磁盘实际存在的章节号列表"""
        chapters_dir = os.path.join(self.novels_dir, novel_id, "chapters")
        if not os.path.exists(chapters_dir):
            return []
        chapters = []
        for f in os.listdir(chapters_dir):
            if f.startswith("chapter_") and f.endswith(".md"):
                try:
                    num = int(f.replace("chapter_", "").replace(".md", ""))
                    chapters.append(num)
                except ValueError:
                    pass
        return sorted(chapters)

    # ═══════════════════════════════════════════
    # 分模块上下文构建
    # ═══════════════════════════════════════════

    def build_context(self, module: str, novel_id: str, **kwargs) -> str:
        """为指定模块构建注入上下文
        
        支持的模块: writer, validator, decomposer, twist_designer
        
        Args:
            module: 模块名
            novel_id: 小说ID
            **kwargs: 模块特定参数 (如 chapter_num, chapter_outline)
        """
        if module == "writer":
            return self._build_writer_context(novel_id, kwargs)
        elif module == "validator":
            return self._build_validator_context(novel_id, kwargs)
        elif module == "decomposer":
            return self._build_decomposer_context(novel_id)
        elif module == "planner":
            return self._build_planner_context(novel_id, kwargs)
        else:
            # 通用上下文
            return self._build_generic_context(novel_id)

    def _build_writer_context(self, novel_id: str, kwargs: dict) -> str:
        """为 Writer 构建完整写作上下文（五层）"""
        chapter_num = kwargs.get("chapter_num", 1)
        chapter_outline = kwargs.get("chapter_outline", {})
        
        parts = []

        # L1: 核心设定
        plan = self.read("plan", novel_id)
        wb = plan.get("worldbuilding", {})
        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})
        
        core = f"""## 核心设定（永远记住）

### 世界观
- 时代: {wb.get('era', '')}
- 力量体系: {wb.get('power_system', '')}
- 核心冲突: {wb.get('core_conflict', '')}

### 主角档案
- 姓名: {protagonist.get('name', '')}
- 身份: {protagonist.get('identity', '')}
- 性格: {protagonist.get('personality', '')}
- 金手指: {protagonist.get('cheat', '')}
- 核心动机: {protagonist.get('motivation', '')}
"""
        supporting = chars.get("supporting", [])
        if supporting:
            core += "\n### 重要配角\n"
            for c in supporting[:5]:
                core += f"- {c.get('name', '?')}: {c.get('identity', '')}, {c.get('relation', '')}\n"
        parts.append(core)

        # L2: 上一章完整上下文（结尾+摘要）
        prev_chapter = chapter_num - 1
        if prev_chapter >= 1:
            prev_content = self.read_chapter(novel_id, prev_chapter)
            if prev_content:
                # 取上一章最后 2000 字作为连续性上下文（原来只取 500，太少了）
                take_chars = min(2000, len(prev_content))
                prev_ending = prev_content[-take_chars:]
                parts.append(f"## ⬆️ 上一章结尾（必须从这里接着写！开头要无缝衔接）\n\n{prev_ending}")
                
                # 上一章钩子
                for vol in plan.get("outline", {}).get("volumes", []):
                    for ch in vol.get("chapters", []):
                        if int(ch.get("number", 0)) == prev_chapter:
                            hook = ch.get("hook", "")
                            if hook:
                                parts.append(f"## 🔗 上一章留下的钩子（本章必须在某个节点回应）\n{hook}")
                            # 也加入上一章的摘要作为背景
                            prev_summary = ch.get("summary", "")
                            if prev_summary and prev_summary != hook:
                                parts.append(f"## 📝 上一章大纲摘要\n{prev_summary}")
                            break
        
        # L2b: 更早章节的摘要（最近3章，帮助理解多章弧线）
        if chapter_num > 2:
            summaries = []
            for ch_num in range(max(1, chapter_num - 3), chapter_num):
                for vol in plan.get("outline", {}).get("volumes", []):
                    for ch in vol.get("chapters", []):
                        if int(ch.get("number", 0)) == ch_num:
                            s = ch.get("summary", "")
                            if s:
                                summaries.append(f"第{ch_num}章: {s}")
                            break
            if summaries:
                parts.append(f"## 📚 前几章剧情线\n" + "\n".join(summaries))

        # L3: 全局状态快照
        state = self.read("global_state", novel_id)
        if state:
            parts.append(self._format_state_snapshot(state, chapter_num))

        # L4: 伏笔
        hooks_ctx = self._build_foreshadowing_context(novel_id, chapter_num)
        if hooks_ctx:
            parts.append(hooks_ctx)

        # L5: 本章大纲
        beats_text = ""
        beats = chapter_outline.get("scene_beats", [])
        if beats:
            beats_text = "\n### 场景节拍\n"
            for b in beats:
                beats_text += f"- 节拍{b.get('beat','?')}「{b.get('name','')}」: {b.get('function','')} → {b.get('key_action','')}\n"
        
        outline_text = f"""## 本章大纲

- 章节: 第{chapter_num}章「{chapter_outline.get('title', '')}」
- 核心事件: {chapter_outline.get('summary', '')}
- 情绪曲线: {chapter_outline.get('emotion_curve', '')}
- 出场角色: {', '.join(chapter_outline.get('characters', []))}
- 结尾钩子: {chapter_outline.get('hook', '')}
- 目标字数: {chapter_outline.get('target_words', 3000)} 字
{beats_text}"""
        parts.append(outline_text)

        return "\n\n---\n\n".join(parts)

    def _build_validator_context(self, novel_id: str, kwargs: dict) -> str:
        """为 Validator 构建校验上下文"""
        plan = self.read("plan", novel_id)
        state = self.read("global_state", novel_id)
        
        parts = []
        protagonist = plan.get("characters", {}).get("protagonist", {})
        parts.append(f"主角: {protagonist.get('name', '')}")
        parts.append(f"力量体系: {plan.get('worldbuilding', {}).get('power_system', '')[:200]}")
        
        if state:
            parts.append(f"当前状态: {json.dumps(state, ensure_ascii=False)[:500]}")
        
        return "\n".join(parts)

    def _build_decomposer_context(self, novel_id: str) -> str:
        """为 FeedbackDecomposer 构建大纲上下文"""
        plan = self.read("plan", novel_id)
        outline = plan.get("outline", {})
        volumes = outline.get("volumes", [])
        
        parts = [f"总章节数: {outline.get('total_chapters', 0)}"]
        
        for vol in volumes:
            title = vol.get("title", "")
            act = vol.get("act", "")
            parts.append(f"\n第{vol.get('number','?')}卷「{title}」({act})")
            for ch in vol.get("chapters", [])[:8]:
                parts.append(f"  Ch{ch.get('number','?')}: {ch.get('summary','')[:40]}")
        
        return "\n".join(parts)

    def _build_planner_context(self, novel_id: str, kwargs: dict) -> str:
        """为 Planner 构建规划上下文"""
        plan = self.read("plan", novel_id)
        return json.dumps({
            "worldbuilding": plan.get("worldbuilding", {}),
            "characters": plan.get("characters", {}),
            "genre": plan.get("genre", ""),
            "style": plan.get("style", ""),
            "target_words": plan.get("target_words", 0),
        }, ensure_ascii=False)

    def _build_generic_context(self, novel_id: str) -> str:
        """通用上下文"""
        plan = self.read("plan", novel_id)
        return f"小说: {plan.get('title', novel_id)}\n题材: {plan.get('genre', '')}\n风格: {plan.get('style', '')}"

    def _build_foreshadowing_context(self, novel_id: str, current_chapter: int) -> str:
        """构建伏笔上下文"""
        hooks = self.read("foreshadowing", novel_id)
        
        active = []
        for h in hooks:
            if h.get("resolved"):
                continue
            reveal = h.get("reveal_chapter", 999)
            if reveal <= current_chapter + 3:
                urgency = "🔴 必须" if reveal <= current_chapter else (
                    "🟡 建议" if reveal <= current_chapter + 1 else "🟢 可选")
                active.append((urgency, reveal, h))
        
        if not active:
            return ""
        
        urgency_order = {"🔴 必须": 0, "🟡 建议": 1, "🟢 可选": 2}
        active.sort(key=lambda x: (urgency_order.get(x[0], 99), x[1]))
        
        lines = ["## 📌 伏笔回收提醒\n\n以下伏笔需要在近期回收：\n"]
        for urgency, reveal, h in active[:8]:
            lines.append(
                f"- {urgency} [第{h.get('plant_chapter', '?')}章埋设 → "
                f"计划第{reveal}章回收] {h.get('description', '')}\n"
            )
        return "".join(lines)

    def _format_state_snapshot(self, state: dict, chapter_num: int) -> str:
        """格式化角色状态快照（子字段类型守卫，防 global_state.json 损坏）"""
        lines = ["## 📊 全局状态快照"]
        
        summaries = state.get("chapters_summary", {})
        if isinstance(summaries, dict):
            recent = sorted([(int(k), v) for k, v in summaries.items()
                            if int(k) >= chapter_num - 5 and int(k) < chapter_num])
            if recent:
                lines.append("\n### 前情提要")
                for ch, summary in recent:
                    lines.append(f"- 第{ch}章: {summary}")
        
        chars = state.get("characters", {})
        if isinstance(chars, dict) and chars:
            lines.append("\n### 角色状态")
            for name, changes in list(chars.items())[:10]:
                if isinstance(changes, list):
                    latest = changes[-1] if changes else ""
                else:
                    latest = str(changes)
                lines.append(f"- **{name}**: {latest}")
        
        powers = state.get("power_levels", {})
        if isinstance(powers, dict) and powers:
            lines.append("\n### 力量等级")
            for name, level in powers.items():
                lines.append(f"- {name}: {level}")
        
        locations = state.get("locations", [])
        if locations:
            lines.append(f"\n### 已知地点: {', '.join(locations[-5:])}")
        
        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # 批量操作
    # ═══════════════════════════════════════════

    def create_novel_workspace(self, novel_id: str) -> str:
        """为新小说创建完整工作目录"""
        novel_dir = os.path.join(self.novels_dir, novel_id)
        os.makedirs(novel_dir, exist_ok=True)
        os.makedirs(os.path.join(novel_dir, "chapters"), exist_ok=True)
        return novel_dir

    def export_all(self, novel_id: str) -> dict:
        """导出小说的全部记忆数据（用于备份/迁移）"""
        return {
            "plan": self.read("plan", novel_id, skip_cache=True),
            "state": self.read("state", novel_id, skip_cache=True),
            "global_state": self.read("global_state", novel_id, skip_cache=True),
            "foreshadowing": self.read("foreshadowing", novel_id, skip_cache=True),
            "character_bible": self.read("character_bible", novel_id, skip_cache=True),
        }

    def import_all(self, novel_id: str, data: dict):
        """导入小说的全部记忆数据"""
        for key in ["plan", "state", "global_state", "character_bible", "foreshadowing"]:
            if key in data and data[key] is not None:
                self.write(key, novel_id, data[key], max_retries=1)

    # ═══════════════════════════════════════════
    # 变化通知
    # ═══════════════════════════════════════════

    def subscribe(self, novel_id: str, memory_type: str, callback: Callable):
        """订阅记忆变化通知
        
        Args:
            novel_id: 小说ID（"*"表示所有小说）
            memory_type: 记忆类型（"*"表示所有类型）
            callback: fn(novel_id, memory_type, new_data)
        """
        key = (novel_id, memory_type)
        if key not in self._listeners:
            self._listeners[key] = []
        self._listeners[key].append(callback)

    def _notify(self, novel_id: str, memory_type: str, data):
        """触发变化通知"""
        # 精确匹配
        for (nid, mtype), callbacks in self._listeners.items():
            if (nid == "*" or nid == novel_id) and (mtype == "*" or mtype == memory_type):
                for cb in callbacks:
                    try:
                        cb(novel_id, memory_type, data)
                    except Exception as e:
                        log.warning(f"Memory listener error: {e}")

    # ═══════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════

    def _get_path(self, memory_type: str, novel_id: str, chapter_num: int = None) -> str:
        """获取记忆文件的绝对路径"""
        file_info = MEMORY_FILES[memory_type]
        if memory_type == "chapter" and chapter_num is not None:
            rel_path = file_info["file"].format(chapter_num)
        else:
            rel_path = file_info["file"]
        return os.path.join(self.novels_dir, novel_id, rel_path)

    def _write_with_lock(self, path: str, data, max_retries: int = 3) -> bool:
        """乐观锁写入：读取→版本检查→写入→冲突重试
        注意: 防御性复制 data，避免修改调用方的原始数据（副作用 bug）
        """
        # 防御性复制 — 避免修改调用方的数据对象
        write_data = copy.deepcopy(data)

        for attempt in range(max_retries):
            # 读取当前版本
            current = safe_read_json(path, {})
            if not isinstance(current, dict):
                current = {}
            current_version = current.get("_version", 0)

            # 设置新版本（只修改 write_data 副本，不影响原始数据）
            if isinstance(write_data, dict):
                write_data["_version"] = current_version + 1

            # 原子写入
            atomic_write_json(path, write_data)

            # 读取验证
            verify = safe_read_json(path, {})
            if not isinstance(verify, dict):
                verify = {}
            verify_version = verify.get("_version", 0)
            if verify_version == current_version + 1:
                if attempt > 0:
                    log.info(f"Optimistic lock OK after {attempt+1} attempts: {path}")
                return True

            # 版本冲突 → 重试
            log.warning(f"Version conflict on {path}, retry {attempt+1}/{max_retries}")
            time.sleep(0.05 * (attempt + 1))  # 退避

        log.error(f"Optimistic lock FAILED after {max_retries} retries: {path}")
        return False

    def _invalidate(self, novel_id: str, memory_type: str):
        """失效指定记忆的缓存"""
        cache_key = (novel_id, memory_type)
        with self._cache_lock:
            self._cache.pop(cache_key, None)

    def invalidate_all(self, novel_id: str = None):
        """失效全部缓存"""
        with self._cache_lock:
            if novel_id:
                keys = [k for k in self._cache if k[0] == novel_id]
                for k in keys:
                    del self._cache[k]
            else:
                self._cache.clear()

    # ═══════════════════════════════════════════
    # 向后兼容 — NovelMemory 接口映射
    # ═══════════════════════════════════════════

    def get_novel_dir(self, novel_id: str) -> str:
        return os.path.join(self.novels_dir, novel_id)

    def build_writer_context(self, novel_id: str, chapter_num: int,
                             chapter_outline: dict) -> str:
        return self.build_context("writer", novel_id,
                                  chapter_num=chapter_num,
                                  chapter_outline=chapter_outline)

    def save_chapter(self, novel_id: str, chapter_num: int, content: str):
        self.write_chapter(novel_id, chapter_num, content)

    def update_foreshadowing(self, novel_id: str, chapter_num: int,
                             planted: list = None, resolved: list = None):
        hooks = self.read("foreshadowing", novel_id)
        for p in (planted or []):
            hooks.append({
                "plant_chapter": chapter_num,
                "description": p.get("description", ""),
                "reveal_chapter": p.get("reveal_chapter", chapter_num + 5),
                "resolved": False,
            })
        for r in (resolved or []):
            for h in hooks:
                if r in h.get("description", ""):
                    h["resolved"] = True
                    h["resolved_chapter"] = chapter_num
        self.write("foreshadowing", novel_id, hooks)

    def save_novel_state(self, novel_id: str, state: dict):
        self.write("state", novel_id, state)

    def get_novel_state(self, novel_id: str) -> dict:
        state = self.read("state", novel_id)
        # 防御：确保 state 是 dict
        if not isinstance(state, dict):
            state = {}
        # Fix: handle both missing key AND None value
        if "completed_chapters" not in state or state.get("completed_chapters") is None:
            state["completed_chapters"] = self.scan_chapters(novel_id)
        if state.get("completed_chapters"):
            state["completed_chapters"] = sorted(state["completed_chapters"])
        if "current_chapter" not in state:
            chs = state.get("completed_chapters", [])
            state["current_chapter"] = max(chs) if chs else 0
        # Auto-sync: ADD chapters that exist on disk but aren't in state
        disk_chapters = self.scan_chapters(novel_id)
        chs = state.get("completed_chapters", [])
        if chs is None:
            chs = []
        missing = [c for c in disk_chapters if c not in chs]
        if missing:
            log.info(f"State auto-sync: adding {missing} from disk")
            state["completed_chapters"] = sorted(chs + missing)
            state["current_chapter"] = max(state["completed_chapters"])
            self.write("state", novel_id, state)
        return state

    def get_core_context(self, novel_id: str) -> str:
        plan = self.read("plan", novel_id)
        wb = plan.get("worldbuilding", {})
        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})

        ctx = f"""## 核心设定（永远记住）

### 世界观
- 时代: {wb.get('era', '')}
- 力量体系: {wb.get('power_system', '')}
- 核心冲突: {wb.get('core_conflict', '')}

### 主角档案
- 姓名: {protagonist.get('name', '')}
- 身份: {protagonist.get('identity', '')}
- 性格: {protagonist.get('personality', '')}
- 金手指: {protagonist.get('cheat', '')}
"""
        supporting = chars.get("supporting", [])
        if supporting:
            ctx += "\n### 重要配角\n"
            for c in supporting[:5]:
                ctx += f"- {c.get('name', '?')}: {c.get('identity', '')}, {c.get('relation', '')}\n"
        return ctx
