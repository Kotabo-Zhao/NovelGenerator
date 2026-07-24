"""NovelGenerator — Engine: 创作管线编排器"""
import json
import os
import sys
import time
import copy
import logging
import asyncio
from typing import AsyncGenerator, Optional, AsyncIterator
from openai import OpenAI

# Allow importing from parent dir (works both as package and standalone)
try:
    from backend import config
except ImportError:
    import config

from .planner import Planner
from .writer import Writer
from .shared_memory import SharedMemoryManager
from .embellisher import Embellisher
from .foreshadowing_designer import ForeshadowingDesigner
from .context_updater import ContextUpdater
from .pacing_checker import PacingChecker
from .consistency_validator import ConsistencyValidator
from .logic_supervisor import LogicSupervisor
from .opening_optimizer import OpeningOptimizer
from .twist_designer import TwistDesigner
from .feedback_decomposer import FeedbackDecomposer
from .outline_interactive import OutlineInteractive
from .outline_interactive import FEEDBACK_CATEGORIES
from .chapter_summarizer import ChapterSummarizer, check_and_compress
from .requirement_decomposer import RequirementDecomposer
from .requirement_supervisor import RequirementSupervisor
from .atomic_io import atomic_write_json, safe_read_json, atomic_write_text

log = logging.getLogger(__name__)


class NovelEngine:
    """小说创作引擎 — 多智能体架构:
    Pipeline: Planner → Writer → ConsistencyValidator → OpeningOptimizer → TwistDesigner
    Support: Embellisher → ContextUpdater → PacingChecker
    Interactive: OutlineInteractive (反馈式大纲迭代)
    """

    def __init__(self):
        self.client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
        )
        self.model = config.DEEPSEEK_MODEL
        self.planner = Planner(self.client, self.model)
        self.writer = Writer(self.client, self.model)
        self.embellisher = Embellisher(self.client, self.model)
        self.fd_designer = ForeshadowingDesigner(self.client, self.model)
        self.context_updater = ContextUpdater(self.client, self.model)
        self.pacing_checker = PacingChecker(self.client, self.model)
        self.consistency_validator = ConsistencyValidator(self.client, self.model)
        self.logic_supervisor = LogicSupervisor(self.client, self.model)
        self.opening_optimizer = OpeningOptimizer(self.client, self.model)
        self.twist_designer = TwistDesigner(self.client, self.model)
        self.feedback_decomposer = FeedbackDecomposer(self.client, self.model)
        self.outline_interactive = OutlineInteractive(
            self.client, self.model,
            decomposer=self.feedback_decomposer,
        )
        # v2.1: 统一记忆管理层 + 渐进式摘要压缩
        self.memory = SharedMemoryManager(config.NOVELS_DIR)
        self.chapter_summarizer = ChapterSummarizer(self.client, self.model)
        # v2.2: 需求拆解与监督系统
        self.requirement_decomposer = RequirementDecomposer(self.client, self.model)
        self.requirement_supervisor = RequirementSupervisor(self.client, self.model)
        self._requirements = {}  # novel_id → requirements dict

    # ── Phase 1: 规划 ──

    def create_novel(self, creative_input: dict) -> dict:
        """创建新小说：灵感 → 需求拆解 → 世界观 + 角色 + 大纲
        
        v2.2: 先生成大纲前先拆解灵感为详细需求
        
        Args:
            creative_input: {genre, style, inspiration, target_words, title?}
        Returns:
            plan dict (结构化设定文档)
        """
        inspiration = creative_input.get("inspiration", "")
        
        # v2.2: 需求拆解
        if inspiration.strip():
            try:
                enhanced_input = self.requirement_decomposer.decompose_and_inject(
                    inspiration, creative_input
                )
                requirements = enhanced_input.get("_requirements", {})
                self._requirements[creative_input.get("title", inspiration[:20])] = requirements
                log.info(f"Requirements decomposed: {requirements.get('total_count', 0)} subtasks")
            except Exception as e:
                log.warning(f"Requirement decomposition failed: {e}")
                enhanced_input = dict(creative_input)
        else:
            enhanced_input = dict(creative_input)
        
        plan = self.planner.plan(enhanced_input)
        if not plan:
            raise RuntimeError("规划生成失败，请重试")
        
        # v2.2: 附加需求拆解元数据
        if inspiration.strip():
            reqs = self._requirements.get(plan.get("title", ""), {})
            if reqs and not isinstance(plan.get("_meta"), dict):
                plan["_meta"] = {}
            if isinstance(plan.get("_meta"), dict) and reqs:
                plan["_meta"]["requirements"] = {
                    "summary": reqs.get("summary", ""),
                    "core_theme": reqs.get("core_theme", ""),
                    "subtask_count": reqs.get("total_count", 0),
                    "decomposed_at": reqs.get("created_at", ""),
                }
        
        # 保存规划
        novel_dir = self.memory.get_novel_dir(plan["title"])
        os.makedirs(novel_dir, exist_ok=True)
        
        atomic_write_json(os.path.join(novel_dir, "plan.json"), plan)
        
        # 生成人物宝典 — 独立的角色wiki文件
        self._save_character_bible(plan, novel_dir)
        
        # 初始化状态
        total_chapters = plan.get("outline", {}).get("total_chapters", 0)
        self.memory.save_novel_state(plan["title"], {
            "current_chapter": 0,
            "total_chapters": total_chapters,
            "total_words": 0,
            "status": "planning_done",
            "created_at": plan.get("_meta", {}).get("created_at", ""),
        })
        
        # 初始化伏笔文件
        hooks_path = os.path.join(novel_dir, "foreshadowing.json")
        atomic_write_json(hooks_path, [])
        
        # 初始化剧情图谱 + 弧规划（不阻断大纲生成）
        try:
            self._init_storygraph_and_arcs(plan, novel_dir)
        except Exception as e:
            log.warning(f"StoryGraph init skipped: {e}")
        
        log.info(f"Novel created: {plan['title']} ({total_chapters} chapters)")
        return plan

    def _init_storygraph_and_arcs(self, plan: dict, novel_dir: str):
        """初始化剧情图谱 + 弧规划"""
        from .storygraph import StoryGraph
        
        sg_path = os.path.join(novel_dir, "storygraph.json")
        sg = StoryGraph()
        
        # 从大纲初始化角色快照
        volumes = plan.get("outline", {}).get("volumes", [])
        all_chapters = []
        for vol in volumes:
            for ch in vol.get("chapters", []):
                ch_chars = ch.get("characters", [])
                for c_name in ch_chars:
                    sg.ensure_char(c_name)
                all_chapters.append(ch)
        
        # 根据核心冲突初始化主线
        main_thread_desc = plan.get("worldbuilding", {}).get("core_conflict", "")
        if main_thread_desc:
            sg.add_thread("thread_main", "主线", "main_plot", 5, main_thread_desc[:100])
        
        # 运行弧规划器
        try:
            from .arcplanner import plan_arcs
            arcs = plan_arcs(
                all_chapters,
                plan.get("outline", {}).get("total_chapters", 0),
                main_thread_desc,
                plan.get("_meta", {}).get("requirements", {}).get("arcs_hint"),
            )
            if arcs:
                sg.data["arcs"] = arcs
                sg.data["current_arc"] = arcs[0] if arcs else {}
                log.info(f"ArcPlanner: {len(arcs)} arcs planned")
        except Exception as e:
            log.warning(f"ArcPlanner skipped: {e}")

        # 方案B: 全局伏笔规划 — 用 foreshadowing_designer 设计伏笔分布
        try:
            total_chs = plan.get("outline", {}).get("total_chapters", 0)
            if total_chs >= 10:  # 至少10章才值得做全局伏笔规划
                fds_result = self.fd_designer.design(plan, target_count=5)
                if fds_result:
                    # 保存到 storygraph 的伏笔账本
                    for fs in fds_result:
                        plant_ch = fs.get("plant_chapter", 1)
                        reveal_ch = fs.get("reveal_chapter", total_chs)
                        fs_id = f"fs_plan_{plant_ch}_{reveal_ch}"
                        importance = {"high": 5, "medium": 3, "low": 1}.get(
                            fs.get("importance", "medium"), 3)
                        sg.add_foreshadow(
                            fs_id=fs_id,
                            description=fs.get("description", ""),
                            planted_chapter=plant_ch,
                            planned_payoff=reveal_ch,
                            importance=importance,
                        )
                    log.info(f"Foreshadowing plan: {len(fds_result)} foreshadows designed")
        except Exception as e:
            log.warning(f"Foreshadowing planner skipped: {e}")

        atomic_write_json(sg_path, sg.to_dict())
        log.info(f"StoryGraph initialized for {plan['title']}")

    async def create_novel_stream(self, creative_input: dict) -> AsyncIterator[dict]:
        """流式创建小说 — 前端可显示分阶段进度条
        
        v2.2: 新增需求拆解阶段。
        在生成大纲之前，先用 RequirementDecomposer 深度分析用户灵感，
        将拆解后的需求注入到各生成阶段的 prompt 中。
        
        Yields progress events from:
        1. RequirementDecomposer.decompose_and_inject()
        2. Planner.plan_stream(enhanced_input)
        then saves plan + bible on 'done'.
        """
        inspiration = creative_input.get("inspiration", "")
        
        # ── v2.2 Phase 0: 需求深度拆解 ──
        if inspiration.strip():
            yield {"type": "progress", "phase": "decompose_requirements", "pct": 2,
                   "label": "正在深度分析您的创作需求…"}
            
            try:
                # 在线程池中运行（避免阻塞事件循环）
                enhanced_input = await asyncio.to_thread(
                    self.requirement_decomposer.decompose_and_inject,
                    inspiration, creative_input
                )
                
                requirements = enhanced_input.get("_requirements", {})
                subtask_count = requirements.get("total_count", 0)
                subtasks = requirements.get("subtasks", [])
                
                yield {"type": "progress", "phase": "decompose_requirements", "pct": 4,
                       "label": f"已拆解出 {subtask_count} 条创作需求"}
                
                # 输出拆解摘要给前端展示（含子任务详情）
                yield {
                    "type": "requirements_decomposed",
                    "summary": requirements.get("summary", ""),
                    "core_theme": requirements.get("core_theme", ""),
                    "subtask_count": subtask_count,
                    "p0_count": sum(1 for t in subtasks if t.get("priority") == "P0"),
                    "offline_mode": requirements.get("offline_mode", False),
                    "target_audience": requirements.get("target_audience", ""),
                    # 子任务列表（前端展示用）
                    "subtasks": [{
                        "id": t.get("id", ""),
                        "title": t.get("title", ""),
                        "category": t.get("category", ""),
                        "sub_category": t.get("sub_category", ""),
                        "priority": t.get("priority", ""),
                        "description": t.get("description", t.get("generation_hint", ""))[:200],
                        "must_include": t.get("must_include", [])[:3],
                        "must_avoid": t.get("must_avoid", [])[:3],
                    } for t in subtasks],
                }
                
                # 保存需求到内存
                # novel_id 还没生成，先用临时 key
                self._pending_requirements = requirements
                
            except Exception as e:
                log.warning(f"Requirement decomposition failed, proceeding without it: {e}")
                enhanced_input = dict(creative_input)
                yield {"type": "warning", "message": f"需求深度拆解跳过: {e}，使用原始灵感生成"}
        else:
            enhanced_input = dict(creative_input)
        
        # ── Phase 1-3: 标准流式规划（使用增强版输入）──
        async for event in self.planner.plan_stream(enhanced_input):
            if event["type"] == "done":
                plan = event.get("plan")
                if not isinstance(plan, dict):
                    log.error(f"create_novel_stream: plan is {type(plan).__name__}, not dict")
                    yield {"type": "error", "message": "大纲数据结构异常，请重试"}
                    return
                if "title" not in plan or not plan["title"]:
                    log.error(f"create_novel_stream: plan missing title, keys={list(plan.keys())[:10]}")
                    yield {"type": "error", "message": "大纲缺少书名，请重试"}
                    return
                novel_dir = self.memory.get_novel_dir(plan["title"])
                os.makedirs(novel_dir, exist_ok=True)
                
                # v2.2: 将需求拆解结果附加到 plan._meta 中
                if hasattr(self, '_pending_requirements') and self._pending_requirements:
                    if not isinstance(plan.get("_meta"), dict):
                        plan["_meta"] = {}
                    plan["_meta"]["requirements"] = {
                        "summary": self._pending_requirements.get("summary", ""),
                        "core_theme": self._pending_requirements.get("core_theme", ""),
                        "subtask_count": self._pending_requirements.get("total_count", 0),
                        "decomposed_at": self._pending_requirements.get("created_at", ""),
                    }
                    
                    # 保存到 _requirements dict (用 title 做 key)
                    self._requirements[plan["title"]] = self._pending_requirements
                    del self._pending_requirements
                
                atomic_write_json(os.path.join(novel_dir, "plan.json"), plan)
                
                # 人物宝典在线程池中执行（包含文件IO）
                await asyncio.to_thread(self._save_character_bible, plan, novel_dir)
                
                total_chapters = plan.get("outline", {}).get("total_chapters", 0)
                self.memory.save_novel_state(plan["title"], {
                    "current_chapter": 0,
                    "total_chapters": total_chapters,
                    "total_words": 0,
                    "status": "planning_done",
                    "created_at": plan.get("_meta", {}).get("created_at", ""),
                })
                
                hooks_path = os.path.join(novel_dir, "foreshadowing.json")
                atomic_write_json(hooks_path, [])
                
                # 初始化剧情图谱 + 弧规划（不阻断大纲生成）
                try:
                    await asyncio.to_thread(self._init_storygraph_and_arcs, plan, novel_dir)
                except Exception as e:
                    log.warning(f"StoryGraph init skipped in stream: {e}")
                
                log.info(f"Novel created (streamed): {plan['title']} ({total_chapters} chapters)"
                        f" — requirements: {self._requirements.get(plan['title'], {}).get('total_count', 0)} subtasks")
            
            yield event

    async def regenerate_outline_stream(self, novel_id: str, feedback: str) -> AsyncIterator[dict]:
        """根据修改意见重新生成大纲（保留世界观和角色）"""
        plan = self.get_novel(novel_id)
        if not plan:
            yield {"type": "error", "message": f"小说 '{novel_id}' 不存在"}
            return

        genre = plan.get("genre", "玄幻")
        style_name = plan.get("style", "热血爽文")
        
        yield {"type": "progress", "phase": "outline", "pct": 5, "label": "分析修改意见…"}
        
        outline_prompt = f"""你是小说大纲规划师。根据以下设定和用户修改意见，重新生成章节大纲。

已有世界观: {json.dumps(plan.get('worldbuilding',{}), ensure_ascii=False)[:400]}
已有主角: {json.dumps(plan.get('characters',{}).get('protagonist',{}).get('name',''), ensure_ascii=False)}
题材: {genre}  风格: {style_name}  目标: {plan.get('target_words',0)}字

用户修改意见: {feedback}

【重要】只修改大纲，保留世界观和角色不变。只输出JSON，且只包含"outline"字段。每章摘要控制在30字内。
```json
{{"outline":{{"volumes":[{{"number":1,"title":"","act":"第一幕·建置","theme":"","act_function":"","chapters":[{{"number":1,"title":"","summary":"","emotion_curve":"","conflict":"","characters":[""],"hook":"","target_words":1500}}]}}],"total_chapters":0,"three_act_map":"","rhythm_notes":""}}}}
```"""
        
        yield {"type": "progress", "phase": "outline", "pct": 30, "label": "重新规划章节…"}
        
        outline = await self.planner._call_llm(outline_prompt, "outline", max_tokens=16384)
        if not outline:
            yield {"type": "error", "message": "大纲生成失败"}
            return
        
        yield {"type": "progress", "phase": "outline", "pct": 80, "label": "保存新大纲…"}
        
        # 更新 plan
        plan["outline"] = outline.get("outline", {})
        if not isinstance(plan.get("_meta"), dict):
            plan["_meta"] = {}
        plan["_meta"]["regenerated_at"] = __import__("datetime").datetime.now().isoformat()
        plan["_meta"]["regeneration_feedback"] = feedback
        
        novel_dir = self.memory.get_novel_dir(novel_id)
        atomic_write_json(os.path.join(novel_dir, "plan.json"), plan)
        
        # 同步更新主角设定卡片
        self.save_character_bible(novel_id, plan, novel_dir)
        
        # 重置状态
        self.memory.save_novel_state(novel_id, {
            "current_chapter": 0,
            "total_chapters": plan["outline"].get("total_chapters", 0),
            "total_words": 0,
            "status": "outline_regenerated",
            "created_at": plan.get("_meta", {}).get("created_at", ""),
        })
        
        yield {"type": "progress", "phase": "done", "pct": 100, "label": "大纲已更新！"}
        yield {"type": "done", "plan": plan}

    def get_novel(self, novel_id: str) -> Optional[dict]:
        """获取已有小说的规划数据（支持 URL 编码和多种匹配方式）"""
        # 尝试 URL 解码
        from urllib.parse import unquote
        decoded_id = unquote(novel_id)
        
        # 按顺序尝试匹配
        for try_id in [novel_id, decoded_id]:
            novel_dir = self.memory.get_novel_dir(try_id)
            plan_path = os.path.join(novel_dir, "plan.json")
            if os.path.exists(plan_path):
                plan = safe_read_json(plan_path)
                if not isinstance(plan, dict):
                    plan = {}
                plan["state"] = self.memory.get_novel_state(try_id)
                return plan
        
        # 智能匹配：在 novels 目录中查找名称相近的
        if os.path.exists(self.memory.novels_dir):
            for d in sorted(os.listdir(self.memory.novels_dir)):
                dir_path = os.path.join(self.memory.novels_dir, d)
                if os.path.isdir(dir_path):
                    plan_file = os.path.join(dir_path, "plan.json")
                    if os.path.exists(plan_file):
                        plan_data = safe_read_json(plan_file)
                        if isinstance(plan_data, dict) and plan_data.get("title", "") == novel_id:
                            log.info(f"get_novel: matched by title '{novel_id}' → dir '{d}'")
                            plan_data["state"] = self.memory.get_novel_state(d)
                            return plan_data
        
        log.warning(f"get_novel: plan.json not found for novel_id='{novel_id}' (decoded='{decoded_id}')")
        if os.path.exists(self.memory.novels_dir):
            dirs = [d for d in os.listdir(self.memory.novels_dir) 
                   if os.path.isdir(os.path.join(self.memory.novels_dir, d))]
            log.warning(f"get_novel: available novel dirs: {dirs[:10]}")
        return None

    def update_plan(self, novel_id: str, plan_data: dict) -> bool:
        """保存用户修改后的大纲
        
        Args:
            novel_id: 小说ID（目录名）
            plan_data: 修改后的完整 plan 字典
        Returns:
            True 表示保存成功
        """
        plan_path = os.path.join(self.memory.get_novel_dir(novel_id), "plan.json")
        if not os.path.exists(plan_path):
            return False
        
        # 保留 _meta 原始信息
        existing = self.get_novel(novel_id)
        if existing and "_meta" in existing:
            plan_data["_meta"] = existing["_meta"]
        
        # 标准化章节号
        for vol in plan_data.get("outline", {}).get("volumes", []):
            if not isinstance(vol, dict):
                continue
            vol["number"] = int(vol.get("number", 1))
            for ch in vol.get("chapters", []):
                if isinstance(ch, dict):
                    ch["number"] = int(ch.get("number", 1))
                    ch["target_words"] = int(ch.get("target_words", config.DEFAULT_CHAPTER_WORDS))
        if isinstance(plan_data.get("outline"), dict):
            plan_data["outline"]["total_chapters"] = int(plan_data.get("outline", {}).get("total_chapters", 0))
        else:
            log.warning(f"update_plan: plan_data['outline'] is {type(plan_data.get('outline')).__name__}, not dict")
        
        atomic_write_json(plan_path, plan_data)
        
        # 更新 state 中的 total_chapters
        state = self.memory.get_novel_state(novel_id)
        state["total_chapters"] = plan_data.get("outline", {}).get("total_chapters", state.get("total_chapters", 0))
        self.memory.save_novel_state(novel_id, state)
        
        log.info(f"Plan updated: {novel_id}")
        return True

    def list_novels(self) -> list:
        """列出所有小说"""
        novels = []
        if not os.path.exists(config.NOVELS_DIR):
            return novels
        for name in os.listdir(config.NOVELS_DIR):
            plan_path = os.path.join(config.NOVELS_DIR, name, "plan.json")
            if os.path.exists(plan_path):
                plan = safe_read_json(plan_path)
                if not isinstance(plan, dict):
                    continue
                # v2.2.1: skip_cache=True 确保批量生成后书架状态最新
                state = self.memory.get_novel_state(name)
                novels.append({
                    "id": name,
                    "title": plan.get("title", name),
                    "genre": plan.get("genre", ""),
                    "style": plan.get("style", ""),
                    "target_words": plan.get("target_words", 0),
                    "state": state,
                    # v2.2.1: 附加上磁盘实际章节数，前端可交叉验证
                    "disk_chapters": len(state.get("completed_chapters", [])),
                })
        return sorted(novels, key=lambda n: n["state"].get("created_at", ""), reverse=True)

    # ── Phase 2: 写作 ──

    def get_chapter(self, novel_id: str, chapter_num: int) -> Optional[str]:
        """读取已生成的章节正文"""
        chapters_dir = os.path.join(self.memory.get_novel_dir(novel_id), "chapters")
        ch_file = os.path.join(chapters_dir, f"chapter_{chapter_num:04d}.md")
        if not os.path.exists(ch_file):
            return None
        try:
            with open(ch_file, "r", encoding="utf-8") as f:
                return f.read()
        except (IOError, UnicodeDecodeError) as e:
            log.error(f"Failed to read chapter {chapter_num}: {e}")
            return None

    async def generate_chapter_stream(
        self, novel_id: str, chapter_num: int, writing_mode: str = "webnovel",
        feedback: str = None,
    ) -> AsyncGenerator[dict, None]:
        """流式生成章节 — 前端可实时显示打字效果
        
        Args:
            feedback: 用户修改意见（用于重生成，不改大纲结构）
        """
        # ── 并发锁：防止同一章被两个Tab同时生成 ──
        novel_dir = self.memory.get_novel_dir(novel_id)
        lock_file = os.path.join(novel_dir, f".generating_{chapter_num:04d}.lock")
        if os.path.exists(lock_file):
            # 检查锁是否过期（超过300秒视为僵尸锁）
            try:
                lock_age = time.time() - os.path.getmtime(lock_file)
                if lock_age < 300:
                    yield {"type": "error", "message": f"第{chapter_num}章正在生成中，请等待完成（已运行{int(lock_age)}秒）"}
                    return
                else:
                    log.warning(f"Stale lock file for chapter {chapter_num}, removing")
                    os.remove(lock_file)
            except OSError:
                pass
        
        # 写入锁文件
        os.makedirs(novel_dir, exist_ok=True)
        try:
            with open(lock_file, "w") as lf:
                lf.write(str(time.time()))
        except IOError:
            pass  # 锁写入失败不阻塞，继续生成
        
        try:
            plan = self.get_novel(novel_id)
            if not plan:
                yield {"type": "error", "message": f"小说 '{novel_id}' 不存在"}
                return

            # 找到本章大纲
            chapter_outline = self._find_chapter_outline(plan, chapter_num)
            if not chapter_outline:
                # 兜底：构造一个基础大纲（防止 DeepSeek JSON 结构异常导致全流程挂掉）
                log.warning(f"Chapter {chapter_num} outline not found in plan, using fallback")
                chapter_outline = {
                    "number": chapter_num,
                    "title": f"第{chapter_num}章",
                    "summary": f"继续推进主线剧情发展",
                    "emotion_curve": "平稳→紧张→悬念",
                    "characters": ["主角"],
                    "hook": "留下悬念引导下一章",
                    "target_words": config.DEFAULT_CHAPTER_WORDS,
                }

            # 组装上下文
            context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)

            # 方案C: 在弧高潮章自动注入反转设计
            try:
                sg_path = os.path.join(self.memory.get_novel_dir(novel_id), "storygraph.json")
                if os.path.exists(sg_path):
                    sg_data = safe_read_json(sg_path)
                    if sg_data and sg_data.get("arcs"):
                        from .arcplanner import is_arc_climax
                        for arc in sg_data["arcs"]:
                            if is_arc_climax(arc, chapter_num):
                                twist = self.twist_designer.design_chapter_twist(
                                    chapter_num=chapter_num,
                                    plan=plan,
                                    chapter_outline=chapter_outline,
                                )
                                if twist and twist.get("suggestion"):
                                    context += f"\n\n【建议反转】{twist['suggestion']}\n"
                                    log.info(f"Twist injected for arc climax Ch{chapter_num}")
                                break
            except Exception as e:
                log.warning(f"Twist injection skipped: {e}")

            # 注入修改意见（重生成场景）
            if feedback and feedback.strip():
                context = (
                    f"【重写指令】以下是上一版存在的问题，请在重写时修正。\n"
                    f"注意：章节大纲、核心事件、出场角色、scene_beats 和结局钩子不变！\n"
                    f"只改进行文质量和具体表达，不改变叙事结构。\n\n"
                    f"用户修改意见：{feedback.strip()}\n\n"
                    f"---\n\n{context}"
                )

            # 获取创作参数
            genre = plan.get("genre", "玄幻")
            style = plan.get("style", "热血爽文")
            target_words = chapter_outline.get("target_words", config.DEFAULT_CHAPTER_WORDS)

            # 流式生成 + 增量保存（每500字写盘，防断线丢内容）
            full_text = ""
            last_save_len = 0
            chapter_title = chapter_outline.get("title", f"第{chapter_num}章")
            async for text in self.writer.write_stream(
                context=context,
                genre=genre,
                style=style,
                target_words=target_words,
                writing_mode=writing_mode,
                normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False),
            ):
                full_text += text
                # 每500字增量保存一次
                if len(full_text) - last_save_len >= 500:
                    try:
                        formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{full_text}\n\n<!-- 生成中，尚未完成 -->"
                        self.memory.save_chapter(novel_id, chapter_num, formatted)
                        last_save_len = len(full_text)
                    except Exception as e:
                        log.warning(f"Incremental save failed (non-fatal): {e}")
                yield {"type": "text", "content": text}

            # ── AI 检测 & 人类化改写 (架构层去AI味) ──
            ai_report = None
            try:
                from .ai_detector import AIDetector, HumanRewriter, humanize_pipeline
                detector = AIDetector(self.client, self.model)
                rewriter = HumanRewriter(self.client, self.model)
                
                chapter_summary = chapter_outline.get("summary", "") or chapter_outline.get("title", "")
                result = await asyncio.to_thread(
                    humanize_pipeline, full_text, detector, rewriter,
                    scene_desc=chapter_summary,
                    target_length=target_words,
                    min_score_threshold=30,
                )
                if result["rewritten"]:
                    ai_report = result
                    full_text = result["text"]
                    log.info(f"AI Humanizer: score {ai_report['ai_score_before']}→{ai_report.get('ai_score_after','?')}, rewritten")
                    yield {"type": "ai_report", 
                           "score_before": ai_report["ai_score_before"],
                           "score_after": ai_report.get("ai_score_after", ai_report["ai_score_before"]),
                           "rewritten": True}
            except Exception as e:
                log.warning(f"AI Humanizer skipped: {e}")

            # 最终保存章节（覆盖增量保存的临时文件）
            formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{full_text}"
            self.memory.save_chapter(novel_id, chapter_num, formatted)

            # 更新状态（v2.2.1: 加重试+验证）
            state = self.memory.get_novel_state(novel_id)
            completed = state.get("completed_chapters", [])
            if chapter_num not in completed:
                completed.append(chapter_num)
            state["completed_chapters"] = sorted(completed)
            state["current_chapter"] = max(completed) if completed else 0
            state["total_words"] = state.get("total_words", 0) + len(full_text)
            
            # 保存 state（最多重试3次，每次验证）
            state_saved = False
            for retry in range(3):
                self.memory.save_novel_state(novel_id, state)
                # 验证：重新读取确认写入了最新数据
                verify_state = self.memory.read("state", novel_id, skip_cache=True)
                if isinstance(verify_state, dict):
                    verify_completed = verify_state.get("completed_chapters", [])
                    if chapter_num in verify_completed:
                        state_saved = True
                        break
                log.warning(f"State write verification failed for chapter {chapter_num}, retry {retry+1}/3")
                time.sleep(0.1 * (retry + 1))
            
            if not state_saved:
                log.error(f"CRITICAL: Failed to persist state for chapter {chapter_num} after 3 retries!")
                # 降级：直接用 atomic_write_json 写入
                state_path = os.path.join(novel_dir, "state.json")
                try:
                    state["_version"] = (state.get("_version", 0) or 0) + 1
                    atomic_write_json(state_path, state)
                    self.memory.invalidate_all(novel_id)
                    log.info(f"State repaired via fallback direct write for chapter {chapter_num}")
                except Exception as fe:
                    log.error(f"State fallback write also failed: {fe}")

            log.info(f"Chapter {chapter_num} saved: {len(full_text)} chars")

            # ── 完整度验证 ──
            from .writer import _check_truncation
            is_trunc, reason = _check_truncation(full_text, target_words)
            if is_trunc:
                log.warning(f"Chapter {chapter_num} may be incomplete: {reason}")
                yield {"type": "warning", "message": f"本章可能不完整（{reason}），建议查看后重生成"}

            # ── 自动执行 ContextUpdater: 更新全局角色状态 ──
            try:
                novel_dir = self.memory.get_novel_dir(novel_id)
                state_path = os.path.join(novel_dir, "global_state.json")
                current_state = {}
                if os.path.exists(state_path):
                    current_state = safe_read_json(state_path)
                
                new_state = self.context_updater.update(novel_id, chapter_num, full_text, current_state)
                atomic_write_json(state_path, new_state)
                log.info(f"ContextUpdater: state updated after chapter {chapter_num}")
            except Exception as e:
                log.warning(f"ContextUpdater skipped: {e}")

            # ── 自动更新剧情图谱（storygraph）──
            try:
                novel_dir = self.memory.get_novel_dir(novel_id)
                sg_path = os.path.join(novel_dir, "storygraph.json")
                sg_data = safe_read_json(sg_path) or {}
                
                from .storygraph import StoryGraph, extract_storygraph_from_chapter, apply_extraction
                sg = StoryGraph.from_dict(sg_data)
                
                # 用轻量模型提取
                extract_result = extract_storygraph_from_chapter(
                    chapter_text=full_text,
                    current_graph=sg_data,
                    chapter_num=chapter_num,
                    chapter_outline=chapter_outline,
                    client=self.client,
                    model=self.model,  # 可换成更便宜的模型
                )
                
                # 应用到图谱
                apply_extraction(sg, extract_result, chapter_num)
                
                # 保存
                atomic_write_json(sg_path, sg.to_dict())
                self.memory.invalidate("storygraph", novel_id)
                log.info(f"StoryGraph updated after chapter {chapter_num}")
            except Exception as e:
                log.warning(f"StoryGraph update skipped: {e}")

            # ── 方案A: 逻辑监督自动校验（自动修复P0违规）──
            try:
                # 获取前文章节
                prev_chapters = {}
                for ch in completed:
                    if ch < chapter_num:
                        ch_content = self.get_chapter(novel_id, ch)
                        if ch_content:
                            prev_chapters[ch] = ch_content
                
                # 获取全局状态
                novel_dir_gs = self.memory.get_novel_dir(novel_id)
                gs_path = os.path.join(novel_dir_gs, "global_state.json")
                global_state = safe_read_json(gs_path, {}) if os.path.exists(gs_path) else {}
                
                # 执行校验
                validation = self.logic_supervisor.validate_chapter(
                    chapter_text=full_text,
                    chapter_num=chapter_num,
                    plan=plan,
                    prev_chapters=prev_chapters,
                    global_state=global_state,
                    run_deep=(chapter_num % 3 == 0),  # 每3章做一次深度校验
                )
                
                violations = validation.get("violations", [])
                if violations:
                    p0_count = sum(1 for v in violations if v.get("severity") == "P0")
                    score = validation.get("score", 100)
                    
                    log.warning(f"LogicSupervisor: {len(violations)} violations "
                               f"(P0:{p0_count}) score={score}")
                    
                    yield {"type": "consistency_check",
                           "violations": violations,
                           "score": score}
                    
                    # P0违规 ≥1 → 自动修复
                    if p0_count >= 1:
                        fix_prompt = self.logic_supervisor.build_fix_prompt(violations)
                        log.info(f"Auto-fix triggered: {p0_count} P0 violations")
                        
                        # 构造修复上下文
                        fix_context = f"{context}\n\n{fix_prompt}"
                        fixed_text = ""
                        
                        async for fix_chunk in self.writer.write_stream(
                            context=fix_context,
                            genre=genre,
                            style=style,
                            target_words=target_words,
                            writing_mode=writing_mode,
                            normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False),
                        ):
                            fixed_text += fix_chunk
                        
                        if fixed_text and len(fixed_text) > len(full_text) * 0.5:
                            full_text = fixed_text
                            # 重新保存
                            formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{fixed_text}"
                            self.memory.save_chapter(novel_id, chapter_num, formatted)
                            log.info(f"Auto-fix completed for chapter {chapter_num}")
                            yield {"type": "auto_fix", "applied": True, "violations_fixed": p0_count}
            except Exception as e:
                log.warning(f"LogicSupervisor auto-validation skipped: {e}")

            # ── 自动校准（每10章）──
            try:
                from .autocalibrator import should_calibrate, calibrate
                if should_calibrate(chapter_num):
                    plan = self.get_novel(novel_id)
                    sg_data = safe_read_json(sg_path) or {}
                    report = calibrate(chapter_num, plan, sg_data,
                                       completed_chapters=completed)
                    
                    # 将校准报告注入 storygraph
                    if not sg.is_healthy():
                        calib_ctx = report.to_context_block()
                        if calib_ctx and sg_data:
                            sg_data["_last_calibration"] = {
                                "chapter": chapter_num,
                                "report": report.to_context_block(),
                                "score": report.score,
                            }
                            atomic_write_json(sg_path, sg_data)
                            self.memory.invalidate("storygraph", novel_id)
                        
                        if not report.is_healthy():
                            log.warning(f"Calibration issues found: "
                                       f"{len(report.plot_drift_items)} drifts, "
                                       f"{len(report.overdue_foreshadows)} overdue "
                                       f"foreshadows, score={report.score}")
                            yield {"type": "calibration", "report": report.to_context_block(),
                                   "score": report.score}
                    log.info(f"AutoCalibration done at chapter {chapter_num}: score={report.score}")
            except Exception as e:
                log.warning(f"AutoCalibration skipped: {e}")

            # ── 渐进式摘要压缩（每10章）──
            try:
                compress_result = check_and_compress(
                    self.memory, novel_id, chapter_num, self.chapter_summarizer
                )
                if compress_result:
                    yield {"type": "compression", "chapters_summarized": len(compress_result)}
            except Exception as e:
                log.warning(f"Auto-compression skipped: {e}")
            
            yield {"type": "text", "content": "\n\n"}
            yield {"type": "done", "content": formatted, "chapter_num": chapter_num}

        except Exception as e:
            log.exception(f"Chapter generation failed: {e}")
            yield {"type": "error", "message": str(e)}
        finally:
            # 清理并发锁
            try:
                if os.path.exists(lock_file):
                    os.remove(lock_file)
            except OSError:
                pass

    def _find_chapter_outline(self, plan: dict, chapter_num: int) -> Optional[dict]:
        """在大纲中查找指定章节（兼容字符串/整数章节号，防御脏数据）"""
        volumes = plan.get("outline", {}).get("volumes", [])
        for vol in volumes:
            if not isinstance(vol, dict):
                continue
            for ch in vol.get("chapters", []):
                if not isinstance(ch, dict):
                    continue
                if int(ch.get("number", 0)) == chapter_num:
                    return ch
        return None

    def _save_character_bible(self, plan: dict, novel_dir: str):
        """生成人物宝典 — 独立的角色wiki文件"""
        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})
        supporting = chars.get("supporting", [])
        antagonist = chars.get("antagonist", [])
        
        bible = {
            "novel_title": plan.get("title", ""),
            "generated_at": plan.get("_meta", {}).get("created_at", ""),
            "bible_summary": chars.get("bible_summary", ""),
            "protagonist": self._format_char_entry(protagonist, "主角"),
            "supporting": [self._format_char_entry(c, f"配角{i+1}") for i, c in enumerate(supporting)],
            "antagonist": [self._format_char_entry(c, f"反派{i+1}") for i, c in enumerate(antagonist)],
            "relationship_map": self._build_relationship_map(protagonist, supporting, antagonist),
        }
        
        bible_path = os.path.join(novel_dir, "character_bible.json")
        atomic_write_json(bible_path, bible)
        
        log.info(f"Character bible saved: {len(supporting)} supporting + {len(antagonist)} antagonist")

    def save_character_bible(self, novel_id: str, plan: dict, novel_dir: str = None):
        """重新生成并保存人物宝典（用于主角修改后同步）
        
        Args:
            novel_id: 小说ID
            plan: 最新的 plan 数据（含新的 characters）
            novel_dir: 可选，自动推断
        """
        if novel_dir is None:
            novel_dir = self.memory.get_novel_dir(novel_id)
        self._save_character_bible(plan, novel_dir)
        log.info(f"Character bible regenerated for {novel_id}")
    
    def _format_char_entry(self, char: dict, default_role: str) -> dict:
        """格式化单个人物条目（展平嵌套字段）"""
        personality = char.get("personality", "")
        if isinstance(personality, dict):
            personality = f"表层: {personality.get('surface','')}; 真实: {personality.get('true_self','')}; 缺陷: {personality.get('flaw','')}"
        
        motivation = char.get("motivation", "")
        if isinstance(motivation, dict):
            motivation = f"想要: {motivation.get('want','')}; 需要: {motivation.get('need','')}"
        
        return {
            "name": char.get("name", ""),
            "role": char.get("role", default_role),
            "identity": char.get("identity", ""),
            "personality": str(personality),
            "motivation": str(motivation),
            "secret": char.get("secret", ""),
            "arc": char.get("arc", char.get("mini_arc", "")),
            "catchphrase": char.get("catchphrase", ""),
            "meaning": char.get("meaning", char.get("relation", "")),
        }
    
    def _build_relationship_map(self, protagonist: dict, supporting: list, antagonist: list) -> list:
        """构建角色关系图"""
        edges = []
        pname = protagonist.get("name", "主角")
        
        # 主角 → 配角
        for c in supporting:
            edges.append({
                "from": pname,
                "to": c.get("name", ""),
                "type": c.get("meaning", c.get("relation", "")),
            })
        
        # 主角 → 反派
        for c in antagonist:
            edges.append({
                "from": pname,
                "to": c.get("name", ""),
                "type": "对抗: " + c.get("conflict", ""),
            })
        
        return edges

    # ── Phase 3: 导出 ──

    def export_novel(self, novel_id: str, fmt: str = "txt") -> tuple:
        """导出小说全文
        
        Returns:
            (content: str|bytes|None, error: str|None)
        """
        plan = self.get_novel(novel_id)
        if not plan:
            return None, f"小说 '{novel_id}' 不存在"

        chapters_dir = os.path.join(self.memory.get_novel_dir(novel_id), "chapters")
        if not os.path.exists(chapters_dir):
            return None, "尚未生成任何章节，请先在写作页面生成至少一章"

        title = plan.get("title", novel_id)
        chapters = sorted(
            [f for f in os.listdir(chapters_dir) if f.endswith(".md")],
            key=lambda x: int(x.split("_")[1].split(".")[0]) if "_" in x else 0
        )

        if not chapters:
            return None, "暂无章节内容，请先生成章节"

        if fmt == "epub":
            return self._export_epub(title, plan, chapters_dir, chapters)

        if fmt == "txt":
            lines = [f"{title}\n{'=' * 40}\n"]
            for ch_file in chapters:
                try:
                    with open(os.path.join(chapters_dir, ch_file), "r", encoding="utf-8") as f:
                        lines.append(f.read())
                        lines.append("\n\n" + "—" * 40 + "\n\n")
                except (IOError, UnicodeDecodeError) as e:
                    log.warning(f"Failed to read {ch_file} for export: {e}")
                    lines.append(f"[无法读取: {ch_file}]\n\n")
            return "\n".join(lines), None

        return None, f"暂不支持 {fmt} 格式"

    def _export_epub(self, title: str, plan: dict, chapters_dir: str, chapters: list) -> tuple:
        """生成 EPUB 电子书"""
        try:
            from ebooklib import epub
        except ImportError:
            return None, "EPUB 导出需要 ebooklib: pip install ebooklib"
        
        book = epub.EpubBook()
        book.set_identifier(f"novelgen-{title}")
        book.set_title(title)
        book.set_language("zh-CN")
        
        author = plan.get("characters", {}).get("protagonist", {}).get("name", "AI Writer")
        book.add_author(author)
        
        # 样式
        style = epub.EpubItem(
            uid="style",
            file_name="style/default.css",
            media_type="text/css",
            content="body{font-family:serif;line-height:1.8;margin:2em}p{text-indent:2em;margin:.5em 0}h1{text-align:center;margin:2em 0}h2{font-size:1.2em;margin:1em 0}",
        )
        book.add_item(style)
        
        spine = ["nav"]
        toc = []
        
        # 书名页
        intro = epub.EpubHtml(title="书名页", file_name="intro.xhtml", lang="zh-CN")
        intro.content = f"""<html><head><link rel="stylesheet" href="style/default.css"/></head>
        <body><h1>{title}</h1>
        <p style="text-align:center">题材: {plan.get('genre','')} | 风格: {plan.get('style','')}</p>
        </body></html>"""
        book.add_item(intro)
        spine.append(intro)
        toc.append(epub.Link("intro.xhtml", "书名页", "intro"))
        
        # 逐章
        for ch_file in chapters:
            try:
                with open(os.path.join(chapters_dir, ch_file), "r", encoding="utf-8") as f:
                    content = f.read()
            except (IOError, UnicodeDecodeError) as e:
                log.warning(f"Failed to read {ch_file} for EPUB: {e}")
                continue
            ch_num = int(ch_file.split("_")[1].split(".")[0]) if "_" in ch_file else 0
            ch_title = f"第{ch_num}章"
            
            c = epub.EpubHtml(title=ch_title, file_name=f"ch{ch_num:04d}.xhtml", lang="zh-CN")
            html_content = content.replace("\n\n", "</p><p>").replace("\n", "<br/>")
            c.content = f'<html><head><link rel="stylesheet" href="style/default.css"/></head><body><p>{html_content}</p></body></html>'
            book.add_item(c)
            spine.append(c)
            toc.append(epub.Link(f"ch{ch_num:04d}.xhtml", ch_title, f"ch{ch_num}"))
        
        book.toc = toc
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = spine
        
        import io
        buf = io.BytesIO()
        epub.write_epub(buf, book)
        return buf.getvalue(), None

    # ═══════════════════════════════════════════════════════
    # Phase 4: 交互式大纲 (v2 — FeedbackDecomposer驱动)
    # ═══════════════════════════════════════════════════════

    async def interactive_outline_stream(self, novel_id: str, feedback: str) -> AsyncIterator[dict]:
        """v2 交互式大纲: FeedbackDecomposer 语义拆解 → 逐条精确执行 → diff输出"""
        plan = self.get_novel(novel_id)
        if not plan:
            yield {"type": "error", "message": f"小说 '{novel_id}' 不存在"}
            return

        # 保存旧版本用于 diff
        old_plan = copy.deepcopy(plan)

        # 使用 v2 process_feedback（内部含 decomposer.decompose + 逐条执行）
        async for event in self.outline_interactive.process_feedback(
            feedback, plan, self.planner
        ):
            if event["type"] == "done":
                new_plan = event["plan"]
                # 验证并修复
                new_plan["outline"] = self.planner.repair_outline(new_plan.get("outline", {}))
                
                # 保存
                novel_dir = self.memory.get_novel_dir(novel_id)
                if not isinstance(new_plan.get("_meta"), dict):
                    new_plan["_meta"] = {}
                new_plan["_meta"]["last_interactive_edit"] = __import__("datetime").datetime.now().isoformat()
                new_plan["_meta"]["last_feedback"] = feedback
                # merge old _meta fields to preserve created_at etc.
                old_meta = plan.get("_meta", {})
                if isinstance(old_meta, dict):
                    for k in ("created_at", "model", "creative_input"):
                        if k in old_meta and k not in new_plan["_meta"]:
                            new_plan["_meta"][k] = old_meta[k]
                atomic_write_json(os.path.join(novel_dir, "plan.json"), new_plan)

                # 同步更新主角设定卡片（character_bible.json）
                self.save_character_bible(novel_id, new_plan, novel_dir)

                # 更新状态
                total = new_plan.get("outline", {}).get("total_chapters", 0)
                self.memory.save_novel_state(novel_id, {
                    "current_chapter": 0,
                    "total_chapters": total,
                    "total_words": 0,
                    "status": "outline_regenerated",
                })

                # diff
                diff = self.outline_interactive.get_diff_summary(old_plan, new_plan)
                if diff:
                    yield {"type": "diff", "changes": diff}
                yield event
            else:
                yield event

    def decompose_feedback(self, novel_id: str, feedback: str) -> dict:
        """仅拆解反馈，不执行修改（供前端预览修改计划）"""
        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}
        return self.feedback_decomposer.decompose(feedback, plan)

    # ═══════════════════════════════════════════════════════
    # Phase 5: 一致性校验 (新)
    # ═══════════════════════════════════════════════════════

    def validate_chapter_consistency(
        self, novel_id: str, chapter_num: int, run_deep: bool = True
    ) -> dict:
        """对已生成章节执行逻辑一致性校验"""
        content = self.get_chapter(novel_id, chapter_num)
        if not content:
            return {"error": f"第{chapter_num}章不存在"}

        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}

        # 获取前文
        prev_chapters = {}
        state = self.memory.get_novel_state(novel_id)
        for ch in state.get("completed_chapters", []):
            if ch < chapter_num:
                ch_content = self.get_chapter(novel_id, ch)
                if ch_content:
                    prev_chapters[ch] = ch_content

        # 获取全局状态
        novel_dir = self.memory.get_novel_dir(novel_id)
        state_path = os.path.join(novel_dir, "global_state.json")
        global_state = {}
        if os.path.exists(state_path):
            global_state = safe_read_json(state_path, {})

        # 执行校验 — 使用增强版 LogicSupervisor
        result = self.logic_supervisor.validate_chapter(
            chapter_text=content,
            chapter_num=chapter_num,
            plan=plan,
            prev_chapters=prev_chapters,
            global_state=global_state,
            run_deep=run_deep,
        )
        return result

    def validate_outline_consistency(self, novel_id: str) -> dict:
        """校验大纲逻辑一致性"""
        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}
        return self.logic_supervisor.validate_outline(plan)

    def validate_chapter_full(self, novel_id: str, chapter_num: int, run_deep: bool = True) -> dict:
        """全维度逻辑监督（增强版，含 12 大类 + 分类得分 + 修复提示）"""
        return self.validate_chapter_consistency(novel_id, chapter_num, run_deep)

    def build_logic_fix_prompt(self, result: dict) -> str:
        """根据监督结果生成 Writer 修复提示"""
        return self.logic_supervisor.build_fix_prompt(
            result.get("violations", []),
            result.get("warnings", []),
        )

    # ═══════════════════════════════════════════════════════
    # Phase 6: 开头分析 (新)
    # ═══════════════════════════════════════════════════════

    def analyze_opening(self, novel_id: str, chapter_num: int = 1) -> dict:
        """分析章节开头吸引力"""
        content = self.get_chapter(novel_id, chapter_num)
        if not content:
            return {"error": f"第{chapter_num}章不存在"}

        plan = self.get_novel(novel_id)
        style = plan.get("style", "热血爽文") if plan else "热血爽文"

        return self.opening_optimizer.analyze_opening(
            chapter_text=content,
            chapter_num=chapter_num,
            style=style,
            is_first_chapter=(chapter_num == 1),
        )

    async def generate_opening_alternatives(
        self, novel_id: str, chapter_num: int = 1, count: int = 3
    ) -> list:
        """生成替代开头方案"""
        content = self.get_chapter(novel_id, chapter_num)
        if not content:
            return [{"error": f"第{chapter_num}章不存在"}]

        plan = self.get_novel(novel_id)
        style = plan.get("style", "热血爽文") if plan else "热血爽文"

        return self.opening_optimizer.generate_alternatives(
            chapter_text=content,
            chapter_num=chapter_num,
            plan=plan or {},
            style=style,
            count=count,
        )

    # ═══════════════════════════════════════════════════════
    # Phase 7: 反转设计 (新)
    # ═══════════════════════════════════════════════════════

    def design_twists(self, novel_id: str) -> dict:
        """为整部小说规划反转点"""
        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}
        return self.twist_designer.design_twists(plan)

    def design_chapter_twist(self, novel_id: str, chapter_num: int) -> dict:
        """为单章设计反转钩子"""
        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}

        chapter_outline = self._find_chapter_outline(plan, chapter_num)
        if not chapter_outline:
            return {"error": f"第{chapter_num}章大纲不存在"}

        # 获取前情摘要
        prev_summary = ""
        state = self.memory.get_novel_state(novel_id)
        for ch in sorted(state.get("completed_chapters", []))[-3:]:
            prev_summary += f"第{ch}章已完成\n"

        return self.twist_designer.design_chapter_twist(
            chapter_num=chapter_num,
            plan=plan,
            chapter_outline=chapter_outline,
            prev_chapters_summary=prev_summary,
        )

    # ═══════════════════════════════════════════════════════
    # Phase 8: 多Agent需求拆解与监督系统
    # ═══════════════════════════════════════════════════════

    def decompose_requirements(self, novel_id: str, inspiration: str) -> dict:
        """拆解用户灵感为可执行子任务
        
        Args:
            novel_id: 小说ID（已保存在plan中的）
            inspiration: 用户输入的核心灵感
        Returns:
            requirements dict with subtasks
        """
        plan = self.get_novel(novel_id)
        existing = self._requirements.get(novel_id)

        result = self.requirement_decomposer.decompose(
            inspiration, plan=plan, existing_requirements=existing
        )
        self._requirements[novel_id] = result
        return result

    def update_requirements(self, novel_id: str, new_inspiration: str) -> dict:
        """追加/修改需求"""
        existing = self._requirements.get(novel_id, {})
        if existing:
            result = self.requirement_decomposer.update_requirements(
                existing, new_inspiration
            )
        else:
            result = self.decompose_requirements(novel_id, new_inspiration)
        self._requirements[novel_id] = result
        return result

    def supervise_requirements(self, novel_id: str) -> dict:
        """监督当前 plan 是否满足需求
        
        Returns:
            supervision report with overall_score, results, etc.
        """
        requirements = self._requirements.get(novel_id)
        if not requirements:
            return {"error": "尚未拆解需求，请先提交灵感"}

        plan = self.get_novel(novel_id)
        if not plan:
            return {"error": f"小说 '{novel_id}' 不存在"}

        return self.requirement_supervisor.supervise(requirements, plan)

    async def verify_and_fix_loop(self, novel_id: str, max_iterations: int = 3
                                   ) -> dict:
        """循环校验：监督→修正→再监督，直至全部通过或达到最大迭代
        
        Args:
            novel_id: 小说ID
            max_iterations: 最大重试次数
            
        Yields progress events + final report.
        """
        requirements = self._requirements.get(novel_id)
        if not requirements:
            yield {"type": "error", "message": "尚未拆解需求"}
            return

        for iteration in range(1, max_iterations + 1):
            yield {"type": "progress", "phase": "verify",
                   "pct": int(100 * iteration / max_iterations),
                   "label": f"第 {iteration}/{max_iterations} 轮校验…"}

            # 监督
            report = self.supervise_requirements(novel_id)
            yield {"type": "supervision", "report": report}

            if report.get("overall_status") == "passed":
                yield {"type": "progress", "phase": "done", "pct": 100,
                       "label": "全部通过!"}
                yield {"type": "done", "iterations": iteration, "result": "passed"}
                return

            # 收集失败反馈
            failed_feedback = self.requirement_supervisor.get_failed_feedback(
                requirements
            )
            if not failed_feedback:
                yield {"type": "done", "iterations": iteration, "result": "no_feedback"}
                return

            yield {"type": "progress", "phase": "fix",
                   "pct": int(100 * iteration / max_iterations),
                   "label": f"修正 {report.get('failed_count', 0)} 项…"}

            # 使用 outline_interactive 机制重新生成
            plan = self.get_novel(novel_id)
            async for event in self.outline_interactive.process_feedback(
                failed_feedback, plan, self.planner
            ):
                if event["type"] == "done":
                    new_plan = event["plan"]
                    new_plan["outline"] = self.planner.repair_outline(
                        new_plan.get("outline", {})
                    )
                    novel_dir = self.memory.get_novel_dir(novel_id)
                    atomic_write_json(os.path.join(novel_dir, "plan.json"), new_plan)
                    self.save_character_bible(novel_id, new_plan, novel_dir)

        yield {"type": "done", "iterations": max_iterations,
               "result": "max_iterations_reached",
               "message": f"已达最大迭代次数 {max_iterations}，仍有未达标项"}
