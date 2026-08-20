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
  - 保留迁移期旧方法映射，业务统一直接依赖本类
"""

import json
import os
import copy
import time
import threading
import logging
from typing import Optional, Callable
from .atomic_io import atomic_write_json, safe_read_json, atomic_write_text
from .path_security import safe_novel_dir, safe_novel_file

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
    "chapter_bridge": {"file": "chapter_bridges.json",   "type": "json", "versioned": False},
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
        
        # v2.2.1: 为不同类型设置不同的 TTL
        # state 变化频繁且关键，TTL 需极短
        # plan/worldbuilding 相对稳定，可用默认
        self._cache_ttl_map = {
            "state": 2.0,          # state 变化最频繁，2s TTL
            "global_state": 10.0,  # 全局状态中等频率
            "plan": 30.0,          # plan 很少变化
            "character_bible": 60.0,  # 人物宝典几乎不变
            "foreshadowing": 10.0,
            "chapter_bridge": 5.0, # 桥接数据频繁读写，5s TTL
        }
        self._cache_lock = threading.Lock()

        # 变化监听: {novel_id: {memory_type: [callbacks]}}
        self._listeners: dict = {}

        # 版本追踪（仅内存中，不落盘）: {path: version}
        self._versions: dict = {}

        # per-path 可重入锁：串行化同一文件的读-改-写，替代"写后读校验"的伪乐观锁。
        self._write_locks: dict = {}
        self._write_locks_guard = threading.Lock()

        log.info(f"SharedMemoryManager initialized: {self.novels_dir}, TTL={cache_ttl}s")

    def _path_lock(self, path: str) -> threading.Lock:
        with self._write_locks_guard:
            return self._write_locks.setdefault(os.path.abspath(path), threading.RLock())

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
        effective_ttl = self._cache_ttl_map.get(memory_type, self._cache_ttl)

        # 检查缓存
        if not skip_cache:
            with self._cache_lock:
                if cache_key in self._cache:
                    data, cached_at = self._cache[cache_key]
                    if time.time() - cached_at < effective_ttl:
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
        """写入记忆（per-path 锁串行化 + 缓存失效）

        Args:
            memory_type: plan|state|global_state|character_bible|foreshadowing
            novel_id: 小说ID
            data: 要写入的数据
            max_retries: 保留参数（兼容旧签名，串行化后不再需要乐观锁重试）
        """
        if memory_type not in MEMORY_FILES:
            raise ValueError(f"Unknown memory type: {memory_type}")

        file_info = MEMORY_FILES[memory_type]
        path = self._get_path(memory_type, novel_id)

        with self._path_lock(path):
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

    def update(self, memory_type: str, novel_id: str, fn):
        """在 per-path 锁内执行 load→fn(mutate)→save，消除并发读-改-写丢失更新。

        fn 接收最新数据副本，返回新数据（或原地修改后返回该对象）；返回 None 表示
        不写回。这是并发场景下修改 state/global_state/plan 的唯一正确入口。
        """
        if memory_type not in MEMORY_FILES:
            raise ValueError(f"Unknown memory type: {memory_type}")
        path = self._get_path(memory_type, novel_id)
        default = [] if memory_type == "foreshadowing" else {}
        with self._path_lock(path):
            current = safe_read_json(path, default)
            if not isinstance(current, (dict, list)):
                current = default
            current = copy.deepcopy(current)
            updated = fn(current)
            if updated is None:
                return current
            atomic_write_json(path, updated)
            self._invalidate(novel_id, memory_type)
            self._notify(novel_id, memory_type, updated)
            return updated

    def read_chapter(self, novel_id: str, chapter_num: int) -> Optional[str]:
        """读取章节正文"""
        path = self._get_path("chapter", novel_id, chapter_num=chapter_num)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                if self._is_incomplete_chapter(content):
                    return None
                return content
        except Exception:
            return None

    def write_chapter(self, novel_id: str, chapter_num: int, content: str):
        """写入章节正文"""
        path = self._get_path("chapter", novel_id, chapter_num=chapter_num)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_text(path, content)
        self._invalidate(novel_id, "chapter")

    def read_chapter_draft(self, novel_id: str, chapter_num: int) -> Optional[str]:
        """读取用户草稿；草稿与已发布章节分离，不能污染生成结果。"""
        path = safe_novel_file(self.novels_dir, novel_id, f"drafts/chapter_{int(chapter_num)}.md")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as file:
                return file.read()
        except (OSError, UnicodeDecodeError):
            return None

    def write_chapter_draft(self, novel_id: str, chapter_num: int, content: str) -> None:
        """原子保存用户草稿，不改变章节完成状态或统计。"""
        if not isinstance(content, str) or len(content) > 50_000:
            raise ValueError("草稿长度无效")
        path = safe_novel_file(self.novels_dir, novel_id, f"drafts/chapter_{int(chapter_num)}.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_text(path, content)

    def delete_chapter_draft(self, novel_id: str, chapter_num: int) -> bool:
        """删除指定草稿；不存在时保持幂等。"""
        path = safe_novel_file(self.novels_dir, novel_id, f"drafts/chapter_{int(chapter_num)}.md")
        try:
            if not os.path.exists(path):
                return False
            os.remove(path)
            return True
        except OSError:
            return False

    def chapter_exists(self, novel_id: str, chapter_num: int) -> bool:
        """检查章节是否存在"""
        return self.read_chapter(novel_id, chapter_num) is not None

    @staticmethod
    def _is_incomplete_chapter(content: str) -> bool:
        return "<!-- GENERATION_IN_PROGRESS -->" in content or (
            "<!--" in content and "生成中" in content
        )

    def remove_incomplete_chapter(self, novel_id: str, chapter_num: int) -> bool:
        """Remove only a generation marker file; completed text is untouched."""

        path = self._get_path("chapter", novel_id, chapter_num=chapter_num)
        try:
            if not os.path.exists(path):
                return False
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            if not self._is_incomplete_chapter(content):
                return False
            os.remove(path)
            self._invalidate(novel_id, "chapter")
            return True
        except OSError:
            return False

    def scan_chapters(self, novel_id: str) -> list:
        """扫描磁盘实际存在的章节号列表"""
        chapters_dir = os.path.join(safe_novel_dir(self.novels_dir, novel_id), "chapters")
        if not os.path.exists(chapters_dir):
            return []
        chapters = []
        for f in os.listdir(chapters_dir):
            if f.startswith("chapter_") and f.endswith(".md"):
                try:
                    num = int(f.replace("chapter_", "").replace(".md", ""))
                    with open(os.path.join(chapters_dir, f), "r", encoding="utf-8") as chapter_file:
                        if self._is_incomplete_chapter(chapter_file.read()):
                            continue
                    chapters.append(num)
                except (ValueError, OSError, UnicodeDecodeError):
                    pass
        return sorted(chapters)

    # ═══════════════════════════════════════════
    # 章节桥接 (ChapterBridge) — v2.10 防止章节间逻辑断裂
    # ═══════════════════════════════════════════

    def get_bridge(self, novel_id: str, chapter_num: int) -> Optional[dict]:
        """获取指定章节的桥接数据（上一章结尾状态 → 下一章起始指令）"""
        bridges = self.read("chapter_bridge", novel_id) or {}
        if not isinstance(bridges, dict):
            bridges = {}
        return bridges.get(str(chapter_num))

    def save_bridge(self, novel_id: str, chapter_num: int, bridge_data: dict):
        """保存章节桥接数据"""
        bridges = self.read("chapter_bridge", novel_id) or {}
        if not isinstance(bridges, dict):
            bridges = {}
        bridges[str(chapter_num)] = bridge_data
        # 清理旧桥接（v2.4.5: 保留最近10章，便于审计早期桥接；原5章太激进）
        all_keys = sorted(int(k) for k in bridges.keys())
        for old_key in all_keys[:-10]:
            bridges.pop(str(old_key), None)
        self.write("chapter_bridge", novel_id, bridges)

    def extract_bridge_from_chapter(
        self, chapter_text: str, chapter_num: int, chapter_outline: dict,
        client=None, model: str = None,
    ) -> dict:
        """从已生成的章节中提取结构化桥接数据

        用轻量 LLM 调用分析章节结尾，提取：
        - end_scene: 结尾场景描述（在哪、谁在、正在做什么）
        - character_states: 关键角色当前状态（位置/情绪/伤势）
        - unresolved_actions: 未完成的动作（战斗中断/对话未完/事件待续）
        - next_beat: 接下来必须推进的叙事节拍
        - hook_to_resolve: 本章留下的钩子（下章必须回应）

        Args:
            chapter_text: 章节全文
            chapter_num: 章节号
            chapter_outline: 本章大纲
            client: OpenAI client（外部注入）
            model: 模型名

        Returns:
            bridge dict，可供下章 Writer 作为强制接续指令
        """
        if not client:
            log.warning("No LLM client for bridge extraction, using fallback")
            return self._fallback_bridge(chapter_text, chapter_num, chapter_outline)

        # 只分析最后 2000 字（结尾部分最关键）
        end_text = chapter_text[-2000:] if len(chapter_text) > 2000 else chapter_text
        # 也取开头 300 字帮助理解全貌
        opening_text = chapter_text[:300]
        # v2.4.5: 中段关键状态扫描 — 防止"关键事件发生在章节中段"时被桥接漏掉
        mid_states = self._extract_mid_chapter_states(chapter_text)

        prompt = f"""分析以下小说章节，提取结构化的"章节桥接数据"。

## 本章大纲
- 标题: {chapter_outline.get('title', '')}
- 核心事件: {chapter_outline.get('summary', '')}
- 计划钩子: {chapter_outline.get('hook', '')}

## 章节开头（供参考）
{opening_text}

## 章节结尾（重点分析）
{end_text}

## 章节中段的关键状态变化（发生在结尾之前，但可能影响下一章）
{mid_states if mid_states else "（无显著状态变化）"}

## 任务
提取以下信息。**只输出 JSON，不要任何其他内容**：

```json
{{
  "end_scene": "结尾场景：描述章节最后一幕发生什么。在哪？谁在场？正在进行什么动作？",
  "character_states": [
    {{"name": "角色名", "status": "该角色在本章结尾的状态：位置/情绪/伤势/关系变化"}}
  ],
  "unresolved_actions": ["任何未完成的动作或事件：战斗是否结束？对话是否说完？事件是否等待结果？"],
  "next_beat": "根据本章结尾，下一章必须推进的叙事节拍（用一句话描述必须发生什么）",
  "hook_to_resolve": "本章结尾留下的钩子——下一章必须在某个节点回应的悬念或期待（如果没有则填'无'）"
}}
```

**重要**：
- end_scene 要具体到"角色A在XX地点刚做了YY，正要ZZ"
- next_beat 必须是 actionable 的指令，不是模糊的"继续推进"
- character_states 只列出状态有变化的角色
- **如果"章节中段的关键状态变化"里有持续的伤势/身体状态（如摔伤、磕破、流血、断骨），即使它发生在中段而不是结尾，也必须体现在 character_states 的 status 中**——因为下一章需要延续这些状态"""

        try:
            # v2.15: 使用韧性客户端（如果传入的是ResilientClient则直接用，否则包装）
            resilient = None
            try:
                from .resilient_client import ResilientLLMClient
                if isinstance(client, ResilientLLMClient):
                    resilient = client
                else:
                    resilient = ResilientLLMClient(client, model or "deepseek-v4-flash")
            except ImportError:
                resilient = None
            
            if resilient:
                response = resilient.create(
                    messages=[
                        {"role": "system", "content": "你是一位小说编辑，擅长分析叙事结构和章节衔接。只输出JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=800,
                )
            else:
                response = client.chat.completions.create(
                    model=model or "deepseek-v4-flash",
                    messages=[
                        {"role": "system", "content": "你是一位小说编辑，擅长分析叙事结构和章节衔接。只输出JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    max_tokens=800,
                )
            content = response.choices[0].message.content.strip()
            # 去掉可能的 markdown 代码块
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:]) if lines[0].startswith("```") else content
                if content.endswith("```"):
                    content = content[:-3].strip()
            bridge = json.loads(content)
            bridge["extracted_at"] = time.time()
            bridge["chapter_num"] = chapter_num
            log.info(f"Bridge extracted for chapter {chapter_num}: {json.dumps(bridge, ensure_ascii=False)[:200]}")
            return bridge
        except Exception as e:
            log.warning(f"Bridge extraction failed for chapter {chapter_num}: {e}, using fallback")
            return self._fallback_bridge(chapter_text, chapter_num, chapter_outline)

    def _extract_mid_chapter_states(self, chapter_text: str, max_items: int = 8) -> str:
        """v2.4.5: 提取章节中段的关键状态变化句（供桥接提取补充）

        问题：桥接提取只分析结尾 2000 字，若关键身体状态（摔伤/受伤/获得/
        失去）发生在章节中段，会被漏掉 → 下一章无法延续。
        解法：用关键词启发式从全文（排除结尾 2000 字）提取"状态句"，
        连同结尾一起交给桥接 LLM 分析。

        Args:
            chapter_text: 章节全文
            max_items: 最多返回多少条状态句

        Returns:
            状态句列表的字符串（每行一条），无显著状态时返回空字符串
        """
        import re
        # 排除结尾 2000 字（那是重点分析区），扫描其余全文
        scan_text = chapter_text[:-2000] if len(chapter_text) > 2000 else ""
        if len(scan_text) < 50:
            return ""

        # 状态关键词：身体伤害 / 情绪冲击 / 获得失去 / 关系变化
        state_kw = (
            r'摔|磕|撞|流血|血|伤|破皮|擦伤|刮伤|扭|折|断|疼|痛|烧|烫|'
            r'获得|得到|捡到|拿起|抢到|失去|丢掉|没了|消失|收下|戴上|拔出|'
            r'哭|笑|震惊|震怒|惊恐|绝望|喜悦|悲愤|'
            r'成为|晋升|突破|觉醒|签订|结盟|决裂|背叛'
        )
        sentences = re.split(r'[。！？\n]', scan_text)
        hits = []
        for s in sentences:
            s = s.strip()
            # 句子过短/过长都跳过（过长多半是场景描述而非状态）
            if not (4 <= len(s) <= 80):
                continue
            # 跳过对话句（引号内多为闲聊，状态词不可靠），优先叙述句
            if ('「' in s or '」' in s or '“' in s or '”' in s or s.startswith('"')):
                continue
            if re.search(state_kw, s):
                # 去重（按前 12 字）
                key = s[:12]
                if key not in [h[0] for h in hits]:
                    hits.append((key, s))
            if len(hits) >= max_items:
                break
        if not hits:
            return ""
        # 按原顺序返回
        return "\n".join(f"- {s}" for _, s in hits[:max_items])

    def _fallback_bridge(self, chapter_text: str, chapter_num: int, chapter_outline: dict) -> dict:
        """v2.24: 本地提取桥接 — 不依赖 LLM，从正文中直接抽取关键信息
        
        策略：
        - 取结尾 800 字作为 end_scene（比之前 300 字多，提供足够上下文）
        - 从结尾中检测角色名（引号/冒号前的名字 = 正在说话/行动的人）
        - 检测未完成动作（短句 + 省略号/破折号结尾 = 被打断的动作）
        - 大纲钩子作为兜底钩子
        """
        import re
        end_text = chapter_text[-800:] if len(chapter_text) > 800 else chapter_text
        
        # 本地提取角色名（从结尾+大纲）
        char_names = []
        # 方法1: 从大纲提取角色（最准确）
        outline_chars = chapter_outline.get("characters", [])
        if outline_chars:
            char_names = [c for c in outline_chars if isinstance(c, str) and len(c) >= 1]
        
        # 方法2: 从结尾检测这些角色是否实际出现
        present_chars = []
        for name in char_names:
            if name in end_text:
                present_chars.append(name)
        
        # 如果大纲角色不足，从结尾文本中检测未列出的名字
        if len(present_chars) < 2:
            blacklist = {'自己','心中','心里','暗暗','不由','不禁','忽然','突然','然后','只是','他','她','我','你','它','他们','她们','我们'}
            speaking_verbs = r'(?:说|道|问|喊|叫|喝|怒喝|冷笑|沉声|低语|叹|吼|厉喝)'
            for m in re.finditer(r'[」』](.{1,4})' + speaking_verbs, end_text[-500:]):
                name = m.group(1).strip()
                if name and name not in blacklist and name not in present_chars and not re.match(r'^[\d\W_]+$', name):
                    present_chars.append(name)
        char_names = present_chars[:5]
        
        # 检测未完成动作（省略号结尾的短句）
        unresolved = []
        sentences = re.split(r'[。！？\n]', end_text[-400:])
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if s.endswith('…') or s.endswith('...') or s.endswith('——'):
                unresolved.append(f"未完成的动作: {s[:60]}")
            # 问句结尾 = 可能是在等待回答
            if s.endswith('？') and len(s) < 30:
                unresolved.append(f"待回答的问题: {s[:60]}")
        unresolved = unresolved[-3:] if unresolved else []
        
        # 结尾关键词检测（情绪/状态）
        end_keywords = []
        emotional = {'愤怒':'处于愤怒状态', '恐惧':'处于恐惧中', '震惊':'刚受到震撼', 
                     '杀意':'充满杀意', '绝望':'陷入绝望', '兴奋':'情绪激动',
                     '重伤':'身受重伤', '昏迷':'昏迷不醒', '逃走':'正在逃离'}
        for kw, desc in emotional.items():
            if kw in end_text[-300:]:
                end_keywords.append(desc)
        
        character_states = []
        if char_names and end_keywords:
            for name in char_names[:3]:
                character_states.append({"name": name, "status": "; ".join(end_keywords[:2])})
        
        return {
            "end_scene": end_text[:400],  # 直接给结尾原文（比LLM总结更准确）
            "character_states": character_states,
            "unresolved_actions": unresolved,
            "next_beat": chapter_outline.get("summary", ""),
            "hook_to_resolve": chapter_outline.get("hook", ""),
            "extracted_at": time.time(),
            "chapter_num": chapter_num,
            "fallback": False,  # v2.24: 本地提取足够强，不再标记为fallback
        }

    def format_bridge_for_writer(self, bridge: dict, chapter_num: int) -> str:
        """将桥接数据格式化为 Writer 上下文中的高优先级指令"""
        if not bridge:
            return ""
        
        parts = [f"## 🔗 第{chapter_num}章 → 第{chapter_num+1}章 桥接指令（最高优先级·必须遵守）\n"]

        end_scene = bridge.get("end_scene", "")
        if end_scene:
            parts.append(
                f"### 上一章结尾（第{chapter_num}章）\n"
                f"**{end_scene}**\n\n"
                f"⚠️ 第{chapter_num+1}章必须从这个精确场景继续。开头不能跳时间、不能换地点、不能忽略上一章最后正在进行的动作。"
            )

        char_states = bridge.get("character_states", [])
        if char_states:
            parts.append("\n### 角色当前状态（必须保持一致性）")
            for cs in char_states:
                parts.append(f"- **{cs.get('name', '?')}**: {cs.get('status', '')}")

        unresolved = bridge.get("unresolved_actions", [])
        if unresolved:
            parts.append("\n### 未完成事件（必须在本章处理）")
            for ua in unresolved:
                parts.append(f"- {ua}")

        next_beat = bridge.get("next_beat", "")
        if next_beat:
            parts.append(
                f"\n### 🎯 强制叙事节拍\n"
                f"**{next_beat}**\n\n"
                f"这是本章必须推进的核心事件。可以在推进中加入创意，但不能跳过或替换。"
            )

        hook = bridge.get("hook_to_resolve", "")
        if hook and hook != "无":
            parts.append(
                f"\n### 🔗 待回应钩子\n"
                f"**{hook}**\n\n"
                f"本章必须在某个节点回应这个钩子——可以是揭晓、延续、或用更大的悬念替代，但不能无视。"
            )

        if bridge.get("fallback"):
            # v2.24: fallback bridge 已足够强，不需要警告
            pass

        return "\n".join(parts)

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
        try:
            return self._build_writer_context_impl(novel_id, kwargs)
        except Exception as e:
            import traceback
            log.error(f"FATAL: _build_writer_context crashed for novel={novel_id}, kwargs_keys={list(kwargs.keys())}: {e}\n{traceback.format_exc()}")
            # 最小降级上下文
            chapter_num = kwargs.get("chapter_num", 1)
            chapter_outline = kwargs.get("chapter_outline", {})
            return f"""## 本章大纲（上下文构建降级）

- 章节: 第{chapter_num}章「{str(chapter_outline.get('title', ''))}」
- 核心事件: {str(chapter_outline.get('summary', ''))}
- 目标字数: {chapter_outline.get('target_words', 3000)} 字

（注意：高级上下文构建失败，已降级为最小化提示，请继续写作）"""

    def _build_writer_context_impl(self, novel_id: str, kwargs: dict) -> str:
        """_build_writer_context 的实际实现（含异常保护）"""
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
- 性别/代词: {protagonist.get('gender') or protagonist.get('pronouns') or '未设定，禁止自行推断'}
- 年龄: {protagonist.get('age') or '未设定，禁止自行编造具体年龄或出生年份'}
- 性格: {protagonist.get('personality', '')}
- 金手指: {protagonist.get('cheat', '')}
- 核心动机: {protagonist.get('motivation', '')}
"""
        supporting = chars.get("supporting", [])
        if supporting:
            core += "\n### 重要配角\n"
            for c in supporting[:5]:
                core += (
                    f"- {c.get('name', '?')}: {c.get('identity', '')}, "
                    f"{c.get('relation', '')}, 年龄={c.get('age') or '未设定'}, "
                    f"性别/代词={c.get('gender') or c.get('pronouns') or '未设定'}\n"
                )
        parts.append(core)

        # L1b: 世界档案 — 当小说涉及已知原著世界时注入
        world_canon = wb.get("world_canon", {})
        if world_canon and isinstance(world_canon, dict):
            canon_parts = []
            for world_name, canon in world_canon.items():
                if isinstance(canon, dict) and canon:
                    lines = [f"### {world_name} 世界档案\n"]
                    if canon.get("source"):
                        lines.append(f"- 原著: {canon['source']}")
                    if canon.get("timeline"):
                        lines.append(f"- 时间线: {canon['timeline']}")
                    if canon.get("key_characters"):
                        chars_list = canon['key_characters']
                        if isinstance(chars_list, list):
                            lines.append(f"- 核心角色: {', '.join(chars_list)}")
                        else:
                            lines.append(f"- 核心角色: {chars_list}")
                    if canon.get("key_locations"):
                        locs = canon['key_locations']
                        if isinstance(locs, list):
                            lines.append(f"- 关键地点: {', '.join(locs)}")
                        else:
                            lines.append(f"- 关键地点: {locs}")
                    if canon.get("power_system"):
                        lines.append(f"- 力量体系: {canon['power_system']}")
                    canon_parts.append('\n'.join(lines))
            if canon_parts:
                canon_text = f"## 📖 原著世界档案（写作时严格遵守以下设定）\n\n" + '\n\n'.join(canon_parts)
                parts.append(canon_text)

        # L2: 上一章完整上下文（桥接指令 + 结尾 + 钩子）
        # v2.24: 重新排序 — 连续性信息放在上下文最前面（LLM对开头最敏感）
        prev_chapter = chapter_num - 1
        bridge_inserted = False
        if prev_chapter >= 1:
            # L2a: 章节桥接指令（最高优先级——结构化接续指令）
            prev_bridge = self.get_bridge(novel_id, prev_chapter)
            if prev_bridge:
                bridge_text = self.format_bridge_for_writer(prev_bridge, prev_chapter)
                if bridge_text:
                    parts.append(bridge_text)
                    bridge_inserted = True
                    log.info(f"Bridge injected for chapter {chapter_num}: from Ch{prev_chapter} bridge")
            else:
                log.warning(f"No bridge found for chapter {prev_chapter}, falling back to direct ending injection")

            # L2b: 上一章结尾原文（兜底 + 桥接补充）
            prev_content = self.read_chapter(novel_id, prev_chapter)
            if prev_content:
                # v2.24: 取结尾 1000 字（从1500减少，因为桥接已提供结构化指令）
                take_chars = min(1000, len(prev_content))
                prev_ending = prev_content[-take_chars:]
                prev_ending = _normalize_context_paragraphs(prev_ending)
                
                # v2.46: 强制桥接：提取上一章结尾的最后一段作为强连续性锚点
                if not bridge_inserted:
                    last_paragraphs = prev_ending.strip().split('\n')[-5:]  # 最后5行
                    last_line = last_paragraphs[-1].strip() if last_paragraphs else prev_ending[-100:]
                    parts.insert(0, 
                        f"## 🔗 强制连续性指令 — 从上一章精确继续（最高优先级）\n\n"
                        f"⚠️ 以下是第{prev_chapter}章的结尾原文。第{chapter_num}章必须：\n"
                        f"1. **第一段就从下面的场景开始**——不能跳时间、不能换地点、不能忽略上一章最后正在进行的动作\n"
                        f"2. **角色状态保持**——位置/情绪/伤势 = 本章起始状态\n"
                        f"3. **未完成的动作继续**——如果结尾在战斗中/对话中，直接从那里继续\n"
                        f"4. **上一章最后一句作为本章第一句的出发点**\n\n"
                        f"### 第{prev_chapter}章结尾（完整场景）：\n\n{prev_ending}\n\n"
                        f"### ⚡ 强制起始句\n上一章结尾是：「{last_line}」\n"
                        f"第{chapter_num}章的第一句话必须紧接着这个场景写。"
                    )
                    log.info(f"Direct ending injected for chapter {chapter_num} (no bridge available)")
                else:
                    parts.append(f"## 📖 上一章结尾原文（参考）\n\n{prev_ending}")
                    # v2.4.5: 上一章中段的关键状态变化（桥接补充保险——防止
                    # 关键事件发生在章节中段、不在结尾1000字内时被 writer 遗漏）
                    mid_states = self._extract_mid_chapter_states(prev_content)
                    if mid_states:
                        parts.append(
                            f"## 📌 上一章中段的关键状态（发生在结尾之前，本章需延续）\n{mid_states}\n\n"
                            f"⚠️ 若这些状态在本章仍有影响（伤势未愈、物品未还、关系未解），本章必须延续或交代。"
                        )
                        log.info(f"Mid-chapter states injected for chapter {chapter_num} from Ch{prev_chapter}")
                
                # 上一章大纲钩子
                for vol in plan.get("outline", {}).get("volumes", []):
                    for ch in vol.get("chapters", []):
                        if int(ch.get("number", 0)) == prev_chapter:
                            hook = ch.get("hook", "")
                            if hook:
                                parts.append(f"## 🔗 上一章大纲计划钩子\n{hook}")
                            break
            else:
                log.warning(f"Previous chapter {prev_chapter} content not found on disk!")
        
        # L2c: 已发生事件清单（v2.0.1：全书已写章节摘要，防长文重复）
        # 长文时 LLM 只看到最近3章容易重复更早情节 → 提供全书紧凑事件线，
        # 明确"这些已发生，不得重演"（配合 WRITER_SYSTEM 防重复铁律）。
        if chapter_num > 2:
            state = self.read("global_state", novel_id)
            generated_summaries = state.get("summaries", {}) if isinstance(state, dict) else {}
            disk_chapters = self.scan_chapters(novel_id)
            
            summaries = []
            # 最近 3 章：完整摘要（关键衔接）
            for ch_num in range(max(1, chapter_num - 3), chapter_num):
                gen_summary = generated_summaries.get(str(ch_num))
                if gen_summary:
                    if isinstance(gen_summary, dict):
                        s = gen_summary.get("summary", "")
                        hooks = gen_summary.get("hooks", [])
                        if hooks:
                            s += f" | 伏笔: {', '.join(str(h)[:30] for h in hooks[:2])}"
                    else:
                        s = str(gen_summary)
                else:
                    s = ""
                    for vol in plan.get("outline", {}).get("volumes", []):
                        for ch in vol.get("chapters", []):
                            if int(ch.get("number", 0)) == ch_num:
                                s = ch.get("summary", "")
                                break
                        if s:
                            break
                    # v2.7.5: 缺真实摘要时 fallback 的是大纲计划，必须标注
                    # 「未确认发生」，不得冒充已发生事件（防重复清单依赖真实性）
                    if s:
                        s = f"[大纲计划·未确认] {s}"
                if s:
                    summaries.append(f"第{ch_num}章: {str(s)[:100]}")
            
            # 更早章节：只列一句话摘要（防重复清单），最多 15 章
            early_chs = [c for c in sorted(disk_chapters) if c < chapter_num - 3][-15:]
            for ch_num in early_chs:
                s = ""
                gen_summary = generated_summaries.get(str(ch_num))
                if gen_summary:
                    s = (gen_summary.get("summary", "") if isinstance(gen_summary, dict) else str(gen_summary))
                if not s:
                    for vol in plan.get("outline", {}).get("volumes", []):
                        for ch in vol.get("chapters", []):
                            if int(ch.get("number", 0)) == ch_num:
                                s = ch.get("summary", "")
                                break
                        if s:
                            break
                    # v2.7.5: 同上——大纲计划不得标注「已发生」
                    if s:
                        s = f"[大纲计划·未确认] {s}"
                if s:
                    summaries.append(f"第{ch_num}章(已发生): {str(s)[:60]}")
            
            if summaries:
                parts.append(
                    "## 📚 已发生事件清单（硬约束——本章严禁重演/复述清单中的任何事件）\n"
                    + "\n".join(summaries)
                )

        # L2d: 后续章节反向连续性（如果后一章已存在，必须与之衔接）
        next_chapter = chapter_num + 1
        next_content = self.read_chapter(novel_id, next_chapter)
        next_bridge = self.get_bridge(novel_id, chapter_num)  # 本章→下章的桥
        has_prev = prev_chapter >= 1 and self.read_chapter(novel_id, prev_chapter) is not None
        
        if next_content:
            next_opening = next_content[:800]  # 下一章开头800字
            next_opening = _normalize_context_paragraphs(next_opening)
            next_first_line = next_opening.strip().split('\n')[0] if next_opening else next_content[:100]
            
            backward_ctx = (
                f"## 🔙 强制反向连续性指令 — 必须衔接到下一章（最高优先级）\n\n"
                f"⚠️ 第{next_chapter}章已经存在！第{chapter_num}章必须：\n"
                f"1. **结尾场景精确衔接到下一章开头**——下一章第一句写的是什么、在哪里、谁在场\n"
                f"2. **角色状态自然过渡**——本章结尾的角色位置/情绪 = 下一章起始状态\n"
                f"3. **不要创造新剧情导致下一章失效**——本章是为衔接第{next_chapter}章而写的\n\n"
                f"### 第{next_chapter}章开头（必须衔接到这里）：\n\n{next_opening}"
            )
            
            if next_bridge:
                nbeat = next_bridge.get("next_beat", "")
                hook_r = next_bridge.get("hook_to_resolve", "")
                if nbeat or hook_r:
                    backward_ctx += "\n\n### 本章→下一章桥接指令\n"
                    if nbeat:
                        backward_ctx += f"- 本章结尾必须配合的叙事节拍: **{nbeat}**\n"
                    if hook_r:
                        backward_ctx += f"- 本章结尾必须埋下的钩子: **{hook_r}**\n"
                backward_ctx += (
                    f"\n\n⚡ **本章最后一句话必须自然过渡到下一章的第一句话：**「{next_first_line}」"
                )
            
            # 插入到高优先级位置
            parts.insert(0, backward_ctx)
            log.info(f"Backward continuity injected for chapter {chapter_num} → Ch{next_chapter}")
        
        # L2e: 可靠性评级
        if not has_prev and not next_content:
            parts.insert(0, (
                f"## ⚠️ 连续性警告\n\n"
                f"本章前后均无已生成的章节内容。生成的内容可能在上下文上不可靠。\n"
                f"建议：生成前后章节后再回来审视本章衔接是否自然。\n"
            ))
            log.warning(f"Chapter {chapter_num}: no prev or next chapter — reliability warning")
        elif not has_prev:
            parts.insert(0, (
                f"## ⚠️ 注意：这是第一章\n\n"
                f"没有前情章节，请自由展开剧情。但需注意本章结尾应为后续章节留好钩子。\n"
            ))

        # L3: 全局状态快照
        state = self.read("global_state", novel_id)
        if state:
            parts.append(self._format_state_snapshot(state, chapter_num))
            # v2.42: 注入主角状态（从 global_state.json 的 protagonist_state 字段读取）
            char_ctx = self._build_character_state_context(state)
            if char_ctx:
                parts.append(char_ctx)

        # L4: 伏笔
        hooks_ctx = self._build_foreshadowing_context(novel_id, chapter_num)
        if hooks_ctx:
            parts.append(hooks_ctx)

        # L5: 本章大纲
        beats_text = ""
        beats = chapter_outline.get("scene_beats", [])
        if beats:
            beats_text = "\n### 场景节拍\n"
            for index, b in enumerate(beats, 1):
                if isinstance(b, dict):
                    beats_text += (
                        f"- 节拍{b.get('beat', index)}「{b.get('name', '')}」: "
                        f"{b.get('function', '')} → {b.get('key_action', '')}\n"
                    )
                elif b is not None and str(b).strip():
                    # 兼容旧版/手工编辑大纲中的 scene_beats: list[str]，
                    # 不让一条格式问题拖垮整层写作上下文。
                    beats_text += f"- 节拍{index}: {str(b).strip()}\n"
        
        cause = chapter_outline.get("cause_from_prev", "")
        bridge = chapter_outline.get("bridge_to_next", "")
        opening = chapter_outline.get("opening_scene", "")
        intensity = chapter_outline.get("conflict_intensity", "")
        
        # v2.40: 检测叙事人称
        narrative_pov = plan.get('narrative_pov', '')
        if not narrative_pov and chapter_num > 1:
            # 从第一章文本推断人称
            try:
                ch1 = self.read_chapter(novel_id, 1)
                if ch1:
                    sample = ch1[:2000]
                    first_count = sample.count('我') + sample.count('我们')
                    third_count = sample.count('他') + sample.count('她')
                    if first_count > third_count * 2 and first_count > 3:
                        narrative_pov = 'first_person'
                    elif third_count > first_count * 1.5 and third_count > 3:
                        narrative_pov = 'third_person'
            except Exception:
                pass
        
        pov_instruction = ""
        if narrative_pov == 'first_person':
            pov_instruction = "\n叙事人称：第一人称「我」视角，全文用「我」叙述，禁止切换成「他」或角色名字。\n"
        elif narrative_pov == 'third_person':
            pov_instruction = "\n叙事人称：第三人称，始终用角色名字或「他」「她」叙述，不要用「我」。\n"

        outline_text = f"""## 本章大纲

═══ 以下为写作元指令 ═══

本章必须覆盖以下核心事件，不可偏离：
{chapter_outline.get('summary', '')}

章末钩子方向（请根据本章实际剧情，用你自己的话写出自然的悬念收尾，
不要照抄下面这句话，它是方向不是模板）：
→ {chapter_outline.get('hook', '无')}
{pov_instruction}
接续状态：从上章结尾继续。{cause + ' ' if cause else ''}{opening if opening else ''}
出场角色：{', '.join(chapter_outline.get('characters', ['主角']))}
情绪曲线：{chapter_outline.get('emotion_curve', '未设定')}
{beats_text}

═══ 结束 ═══"""

        parts.append(outline_text)
        parts.append(
            "## 本章逻辑硬约束（写前自检）\n"
            "- 时间点、等待时长和角色移动必须能同时成立；不要用新时间点覆盖前文而不解释。\n"
            "- 被外部反锁、断电或隔离的地点，角色不能无铺垫地进出；设备工作必须先交代电源或替代能源。\n"
            "- 角色只能按身份和关系字段行动；‘提供线索但不越权’不等于身份矛盾。\n"
            "- 本章摘要、场景节拍和核心设定是硬约束；需要新增事实时先补因果铺垫，不要直接凭空补写。"
        )

        # ── v2.3.5: 上一章一致性提醒注入（ConsistencyValidator 发现的问题）──
        try:
            _st = self.get_novel_state(novel_id)
            _issues_map = _st.get("consistency_issues", {}) if isinstance(_st, dict) else {}
            _prev_issues = _issues_map.get(str(chapter_num - 1), [])
            if _prev_issues:
                _nl = chr(10)
                parts.append("## ⚠️ 上一章一致性提醒（本章写作必须修正）" + _nl
                             + _nl.join(f"- {_i}" for _i in _prev_issues[:4]))
        except Exception:
            pass

        # ── v2.3.4: 角色人设约束注入（女娲框架蒸馏结果，仅本角色出场时生效）──
        try:
            _profiles_path = os.path.join(self.get_novel_dir(novel_id), "character_profiles.json")
            if os.path.exists(_profiles_path):
                with open(_profiles_path, "r", encoding="utf-8") as _pf:
                    _profiles = json.load(_pf) or {}
                _chars = chapter_outline.get("characters", [])
                _rule_parts = []
                for _cn in _chars:
                    _prof = _profiles.get(_cn)
                    if not _prof:
                        continue
                    _he = _prof.get("decision_heuristics", [])[:4]
                    _dna = _prof.get("expression_dna", [])[:4]
                    _anti = _prof.get("anti_patterns", [])[:3]
                    _boundary = _prof.get("boundary", {}) or {}
                    _rules = (_boundary.get("rules") or _boundary.get("anti_collapse_checks") or [])[:3]
                    _lines = []
                    if _he:
                        _lines.append("**决策启发式**（出场必须遵守）：")
                        for _h in _he:
                            if isinstance(_h, dict):
                                _lines.append(f"- {('' if str(_h.get('trigger', '')).startswith('当') else '当')}{_h.get('trigger', '')} → {_h.get('action', '')}"[:120])
                            else:
                                _lines.append(f"- {_h}"[:120])
                    if _dna:
                        _lines.append("**表达DNA**：")
                        for _d in _dna:
                            if isinstance(_d, dict):
                                _lines.append(f"- {_d.get('name', '')}：{_d.get('example', '')}"[:120])
                            else:
                                _lines.append(f"- {_d}"[:120])
                    if _anti:
                        _lines.append("**反模式**（绝对禁止）：")
                        for _a in _anti:
                            _lines.append(f"- {_a.get('pattern', _a) if isinstance(_a, dict) else _a}"[:120])
                    if _rules:
                        _lines.append("**防崩校验**：")
                        _lines += [f"- {_r}" for _r in _rules]
                    if _lines:
                        _rule_parts.append(f"## 🎭 角色人设约束（{_cn}）\n" + "\n".join(_lines))
                if _rule_parts:
                    parts.append("\n\n".join(_rule_parts))
        except Exception as _e:
            log.warning(f"Character rules injection skipped: {_e}")

        # ── v2.6: 对话密度告警 — 检测前几章是否对话过多 ──
        try:
            import re
            recent_chapters = range(max(1, chapter_num - 3), chapter_num)
            high_dialogue_count = 0
            for ch in recent_chapters:
                ch_text = self.read_chapter(novel_id, ch)
                if ch_text:
                    dialogue_chars = len(re.findall(r'[「「""][^」」""'']*[」」""'']', ch_text))
                    ratio = dialogue_chars / max(len(ch_text), 1) * 100
                    if ratio > 40:
                        high_dialogue_count += 1
            
            if high_dialogue_count >= 2:
                # AUDIT P0-4: 对话约束已硬编码进 WRITER_SYSTEM（≤35%、连续≤4轮），
                # 这里只保留一行自适应提示，避免重复的长篇告警挤占 prompt
                parts.append(
                    "## ⚠️ 对话密度提示\n\n"
                    f"最近 {high_dialogue_count} 章对话占比>40%。本章对话占比硬约束 ≤30%，"
                    "连续对话不超过 4 轮就必须用动作/环境打断。"
                )
        except Exception:
            pass

        # ── v2.7: 快餐模式上下文告警 ──
        try:
            plan_data = self.read("plan", novel_id)
            is_fast_food = plan_data.get("_meta", {}).get("creative_input", {}).get("fast_food", False) if isinstance(plan_data, dict) else False
            if is_fast_food:
                parts.append(
                    "## ⚡ 快餐模式告警\n\n"
                    "本小说处于快餐模式。每章必须遵循爆款网文标准：\n"
                    "- 前300字必须有冲突（被欺负/身份暴露/生死危机）\n"
                    "- 300-1000字反转觉醒，1000-2200字打脸碾压，2200-3000字新危机\n"
                    "- 每300字一个看点，一章内完成\"被欺负→反击→打脸\"闭环\n"
                    "- 章末必须是金句钩子（能截图传播的短句）\n"
                    "- 零心理描写，零环境描写（冲突场景除外），零铺垫\n"
                    "- 对话不超过3轮就必须用动作打断\n"
                )
        except Exception:
            pass
        
        # ── v2.3.5: 上下文预算保护 — 总长超限时从尾部（低优先级段）截断 ──
        # v2.8: 保护关键段（本章大纲/逻辑硬约束）——正文生成的事件契约绝不能被截断，
        # 否则大纲→正文丢信息。预算提高到 11000，并为大纲段设置保护标记。
        # v2.7.1: 预算提到 12000，并把「已发生事件清单」「全局状态快照」也纳入保护
        # （长文后期这两段是防重复/连贯性的关键，被截断会导致重复或脱节）。
        _ctx = "\n\n---\n\n".join(parts)
        _MAX_CTX = 12000  # 保留至少 50% token 给生成
        _PROTECTED_MARKERS = ("## 本章大纲", "## 本章逻辑硬约束",
                              "## 📚 已发生事件清单", "## 📊 全局状态快照")
        if len(_ctx) > _MAX_CTX:
            _over = len(_ctx) - _MAX_CTX
            # 从最后一个 section 开始逐段移除（保护 L1 核心设定 + 本章大纲在最前/关键位）
            _ctx_parts = _ctx.split("\n\n---\n\n")
            # 标记哪些段是受保护的（不可截断）
            protected_idx = set()
            for pi, seg in enumerate(_ctx_parts):
                if any(seg.startswith(m) for m in _PROTECTED_MARKERS):
                    protected_idx.add(pi)
            while (len(_ctx_parts) - len(protected_idx)) > 3 \
                    and len("\n\n---\n\n".join(_ctx_parts)) > _MAX_CTX:
                # 从尾部找第一个非保护段删除
                removed = False
                for pi in range(len(_ctx_parts) - 1, -1, -1):
                    if pi not in protected_idx:
                        _ctx_parts.pop(pi)
                        removed = True
                        break
                if not removed:
                    break
            _ctx = "\n\n---\n\n".join(_ctx_parts)
            log.warning(f"Writer context budget: truncated {_over} chars (kept {len(_ctx)})")
        return _ctx

    def _build_validator_context(self, novel_id: str, kwargs: dict) -> str:
        """为 Validator 构建校验上下文"""
        plan = self.read("plan", novel_id)
        state = self.read("global_state", novel_id)
        
        parts = []
        protagonist = plan.get("characters", {}).get("protagonist", {})
        parts.append(f"主角: {protagonist.get('name', '')}")
        parts.append(f"力量体系: {_s(plan.get('worldbuilding', {}).get('power_system', ''))[:200]}")
        
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

    def _build_character_state_context(self, state: dict) -> str:
        """从 global_state.json 构建主角状态上下文（v2.42 新增字段）"""
        proto = state.get("protagonist_state")
        if not proto:
            return ""

        lines = ["## 👤 主角状态"]
        lines.append(f"- 姓名: {proto.get('name', '未知')}")
        lines.append(f"- 身份: {proto.get('identity', '未知')}")
        lines.append(f"- 修为: {proto.get('cultivation', '未知')}")
        lines.append(f"- 声望: {proto.get('reputation', '无名小卒')}")
        lines.append(f"- 位置: {proto.get('location', '未知')}")
        equip = proto.get("equipment", [])
        lines.append(f"- 装备: {'、'.join(equip) if equip else '无特殊装备'}")
        lines.append(f"- 健康: {proto.get('health', '良好')}")
        achievements = proto.get("achievements", [])
        if achievements:
            ach_text = "、".join([a.get("event", str(a)) for a in achievements[-5:]])
            lines.append(f"- 成就: {ach_text}")

        # AUDIT P1-4: 活跃配角状态不再丢弃 — 保留最近 5 章出场角色的最新状态
        active = state.get("active_characters", {})
        if isinstance(active, dict) and active:
            _recent = sorted(
                [c for c in active.values() if isinstance(c, dict)],
                key=lambda c: c.get("last_appeared", 0),
                reverse=True,
            )[:5]
            if _recent:
                lines.append("\n### 👥 活跃配角（最近出场）")
                for c in _recent:
                    _name = c.get("name", "?")
                    _status = c.get("status", "活")
                    _loc = c.get("location", "未知")
                    _rel = c.get("relationship", "")
                    _last = c.get("last_appeared", 0)
                    _extra = f"，关系: {_rel}" if _rel else ""
                    if _last:
                        lines.append(f"- {_name}: {_status} @{_loc}{_extra}（第{_last}章出场）")
                    else:
                        lines.append(f"- {_name}: {_status} @{_loc}{_extra}（尚未出场）")

        # 信息传播（最近5条）
        spreads = state.get("information_spread", [])
        if spreads:
            lines.append("\n### 📡 信息传播")
            for s in spreads[-5:]:
                known = "、".join(s.get("new_known_by", s.get("known_by", [])))
                regions = "、".join(s.get("spread_to", []))
                spread_text = f"「{s.get('event', '?')}」→ {known}"
                if regions:
                    spread_text += f" ({regions})"
                lines.append(f"- {spread_text}")

        # 故事线定位
        storyline = state.get("storyline_position", {})
        if storyline:
            lines.append("\n### 📖 故事线")
            lines.append(f"- 当前弧: {storyline.get('current_arc', '未命名')}")
            lines.append(f"- 进度: {storyline.get('arc_progress', '前期')}")
            if storyline.get("timeline_days", 0) > 0:
                lines.append(f"- 时间线: 约第{storyline['timeline_days']}天")

        return "\n".join(lines)

    # ═══════════════════════════════════════════
    # 批量操作
    # ═══════════════════════════════════════════

    def create_novel_workspace(self, novel_id: str) -> str:
        """为新小说创建完整工作目录"""
        novel_dir = safe_novel_dir(self.novels_dir, novel_id)
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
        return safe_novel_file(self.novels_dir, novel_id, rel_path)

    def _write_with_lock(self, path: str, data, max_retries: int = 3) -> bool:
        """版本化写入（调用方必须已持有 _path_lock）。

        保留 _version 自增以兼容旧数据/乐观锁元数据，但并发正确性由
        write()/update() 的 per-path 锁保证——这里不再依赖"写后读校验"
        （那种校验在并发写者版本号碰撞时无法检测覆盖）。
        """
        # 防御性复制 — 避免修改调用方的数据对象
        write_data = copy.deepcopy(data)

        current = safe_read_json(path, {})
        if not isinstance(current, dict):
            current = {}
        current_version = current.get("_version", 0)

        if isinstance(write_data, dict):
            write_data["_version"] = current_version + 1

        atomic_write_json(path, write_data)
        return True

    def _invalidate(self, novel_id: str, memory_type: str):
        """失效指定记忆的缓存"""
        cache_key = (novel_id, memory_type)
        with self._cache_lock:
            self._cache.pop(cache_key, None)

    def invalidate(self, memory_type: str, novel_id: str):
        """公开失效单项记忆缓存，供生成管线在落盘后刷新状态。"""
        if memory_type not in MEMORY_FILES:
            raise ValueError(f"Unknown memory type: {memory_type}")
        self._invalidate(novel_id, memory_type)

    def invalidate_all(self, novel_id: str = None):
        """失效全部缓存"""
        with self._cache_lock:
            if novel_id:
                keys = [k for k in self._cache if k[0] == novel_id]
                for k in keys:
                    del self._cache[k]
            else:
                self._cache.clear()

    def invalidate_novel(self, novel_id: str):
        """失效指定小说的所有缓存（invalidate_all 别名）"""
        self.invalidate_all(novel_id=novel_id)

    # ═══════════════════════════════════════════
    # v2.2.1: 状态修复与容灾
    # ═══════════════════════════════════════════

    def repair_state(self, novel_id: str) -> dict:
        """修复 state.json 一致性：以磁盘章节文件为准
        
        当 state.json 与磁盘实际章节不一致时，以磁盘为准重建 state。
        这是最终的容灾手段，确保"只要章节在磁盘上，就永远不会丢失"。
        
        Returns:
            {"repaired": bool, "added": [...], "removed": [], "state": dict}
        """
        state_path = safe_novel_file(self.novels_dir, novel_id, "state.json")
        
        # 读取当前 state（可能已损坏）
        current_state = safe_read_json(state_path, {})
        if not isinstance(current_state, dict):
            current_state = {}
        
        # 扫描磁盘实际章节
        disk_chapters = self.scan_chapters(novel_id)
        state_chapters = current_state.get("completed_chapters", [])
        if state_chapters is None:
            state_chapters = []
        
        missing = [c for c in disk_chapters if c not in state_chapters]
        # 也检查 state 中有但磁盘没有的（可能是误判，只 warn 不删除）
        phantom = [c for c in state_chapters if c not in disk_chapters]
        disk_total_words = self._published_total_words(novel_id, disk_chapters)
        try:
            state_total_words = int(current_state.get("total_words") or 0)
        except (TypeError, ValueError):
            state_total_words = 0
        total_words_mismatch = state_total_words != disk_total_words
        
        if not missing and not phantom and not total_words_mismatch:
            return {"repaired": False, "added": [], "removed": [], "state": current_state}
        
        # 以磁盘为准重建
        repaired_state = dict(current_state)
        repaired_state["completed_chapters"] = sorted(disk_chapters)
        repaired_state["current_chapter"] = max(disk_chapters) if disk_chapters else 0
        repaired_state["total_words"] = disk_total_words
        
        # 写入
        try:
            atomic_write_json(state_path, repaired_state)
            self._invalidate(novel_id, "state")
            log.warning(
                f"repair_state [{novel_id}]: added {missing}, "
                f"phantom chapters in state: {phantom}, "
                f"total_words {state_total_words}->{disk_total_words}"
            )
        except Exception as e:
            log.error(f"repair_state [{novel_id}]: write failed: {e}")
            return {"repaired": False, "added": [], "removed": [], "state": current_state, "error": str(e)}
        
        return {
            "repaired": True,
            "added": missing,
            "removed": [],  # 不自动删除，phantom 章节可能只是文件名不匹配
            "phantom_warnings": phantom,
            "state": repaired_state,
        }

    def _published_total_words(self, novel_id: str, chapter_numbers: list[int]) -> int:
        """Count published body characters without Markdown chapter headings."""

        total = 0
        for chapter_num in chapter_numbers:
            content = self.read_chapter(novel_id, chapter_num)
            if not content:
                continue
            lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if lines and lines[0].lstrip().startswith("#"):
                lines.pop(0)
                while lines and not lines[0].strip():
                    lines.pop(0)
            total += len("\n".join(lines).strip())
        return total

    def repair_all_states(self) -> list:
        """扫描所有小说的 state.json 并修复不一致"""
        results = []
        if not os.path.exists(self.novels_dir):
            return results
        for novel_dir in sorted(os.listdir(self.novels_dir)):
            novel_path = os.path.join(self.novels_dir, novel_dir)
            if not os.path.isdir(novel_path):
                continue
            if not os.path.exists(os.path.join(novel_path, "plan.json")):
                continue
            result = self.repair_state(novel_dir)
            if result["repaired"]:
                results.append({"novel_id": novel_dir, **result})
        if results:
            log.info(f"repair_all_states: repaired {len(results)} novels")
        return results

    # ═══════════════════════════════════════════
    # 迁移期兼容方法：统一由本类承载，避免再维护包装器
    # ═══════════════════════════════════════════

    def get_novel_dir(self, novel_id: str) -> str:
        return safe_novel_dir(self.novels_dir, novel_id)

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
        """获取小说写作状态（v2.2.1: 强制跳缓存保证数据最新）
        
        状态文件是批量生成中最关键的数据，必须保证读取到最新版本。
        skip_cache=True 确保每次调用都从磁盘读取，避免缓存返回过期数据。
        """
        state = self.read("state", novel_id, skip_cache=True)
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
            # v2.2.1: 使用 simplified write (no optimistic lock) to avoid conflict failures
            # during auto-sync. Auto-sync only adds missing chapters, never removes.
            state_path = safe_novel_file(self.novels_dir, novel_id, "state.json")
            try:
                atomic_write_json(state_path, state)
                self._invalidate(novel_id, "state")
            except Exception as e:
                log.warning(f"State auto-sync write failed (non-fatal): {e}")
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
- 性别/代词: {protagonist.get('gender') or protagonist.get('pronouns') or '未设定，禁止自行推断'}
- 年龄: {protagonist.get('age') or '未设定，禁止自行编造具体年龄或出生年份'}
- 性格: {protagonist.get('personality', '')}
- 金手指: {protagonist.get('cheat', '')}
"""
        supporting = chars.get("supporting", [])
        if supporting:
            ctx += "\n### 重要配角\n"
            for c in supporting[:5]:
                ctx += (
                    f"- {c.get('name', '?')}: {c.get('identity', '')}, "
                    f"{c.get('relation', '')}, 年龄={c.get('age') or '未设定'}, "
                    f"性别/代词={c.get('gender') or c.get('pronouns') or '未设定'}\n"
                )
        return ctx


# ═══════════════════════════════════════════
# v2.4.1: 上下文清洗 — 合并短行成正常段落
# ═══════════════════════════════════════════

# v2.10: 安全字符串切片 — 防止非字符串类型调用 [:] 产生 slice 错误
def _s(text, default=""):
    """将任意值安全转为字符串（防御 dict/list/None 等非预期的 .get() 返回值）"""
    if isinstance(text, str):
        return text
    if text is None:
        return default
    return str(text)

def _normalize_context_paragraphs(text: str) -> str:
    """轻量清洗：合并相邻短行，防止短行风格自我毒化循环。
    
    规则：
    - 保留所有空行和标题
    - 连续 ≤15字 的行合并为一句（≥40字时输出为段落）
    - 对话行（「」「」或引号）特殊保留结构
    """
    lines = text.split('\n')
    result = []
    i = 0
    last_was_blank = False
    
    def append_line(s):
        nonlocal last_was_blank
        if not s.strip():
            if not last_was_blank:
                result.append("")
                last_was_blank = True
        else:
            result.append(s)
            last_was_blank = False
    
    while i < len(lines):
        stripped = lines[i].strip()
        
        # 空行/标题 → 保留
        if not stripped or stripped.startswith('#'):
            append_line(lines[i])
            i += 1
            continue
        
        # 短行 → 收集并合并
        if len(stripped) <= 15:
            merged = stripped
            i += 1
            # 跳过空行，收集后续短行
            skipped_blanks = False
            while i < len(lines):
                next_line = lines[i].strip()
                if not next_line:
                    skipped_blanks = True
                    i += 1
                    continue
                if len(next_line) <= 15:
                    merged += next_line
                    skipped_blanks = False
                    i += 1
                else:
                    # 遇到长行，如果前面跳了空行 → 合并长行
                    if skipped_blanks and i < len(lines):
                        merged += next_line
                        i += 1
                    break
            
            # 输出合并后的文本
            if len(merged) >= 40:
                # 拆为多个句号段
                if len(merged) > 120:
                    chunks = []
                    pos = 0
                    while pos < len(merged):
                        end = min(pos + 100, len(merged))
                        if end < len(merged):
                            last_period = merged.rfind('。', pos, end)
                            if last_period > pos + 30:
                                end = last_period + 1
                        chunks.append(merged[pos:end])
                        pos = end
                    for c in chunks:
                        append_line(c)
                else:
                    append_line(merged)
            else:
                append_line(merged)
        else:
            # 正常长的行 → 保留
            append_line(lines[i])
            i += 1
    
    return '\n'.join(result)


def normalize_chapter_paragraphs(text: str) -> str:
    """Clean whitespace without rewriting the author's semantic paragraphs.

    Earlier versions concatenated the whole chapter and cut it every ~100
    characters.  That could split a dialogue pair, erase a scene break, and
    give every book the same paragraph fingerprint.  Formatting cleanup is
    intentionally conservative: normalize line endings, trim line tails, and
    collapse only repeated blank lines.
    """
    if not text:
        return ""

    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    result: list[str] = []
    for raw_line in normalized.split("\n"):
        line = raw_line.rstrip()
        if line.strip():
            result.append(line)
        elif result and result[-1] != "":
            result.append("")

    while result and result[-1] == "":
        result.pop()
    return "\n".join(result)
