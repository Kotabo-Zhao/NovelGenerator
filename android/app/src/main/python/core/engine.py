"""NovelGenerator — Engine: 创作管线编排器"""
import json
import os
import sys
import time
import copy
import hashlib
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
from .beat_decomposer import BeatDecomposer, Beat
from .atomic_writer import AtomicWriter
from .beat_assembler import BeatAssembler
from .storygraph_interventions import analyze_and_inject
from .coherence_validator import validate_and_repair_outline

log = logging.getLogger(__name__)







from .mixins.generation import GenerationMixin
from .mixins.validation import ValidationMixin
from .mixins.analysis import AnalysisMixin
from .mixins.requirements import RequirementsMixin
from .mixins.export import ExportMixin
from .mixins.character_profile import CharacterProfileMixin, FeedbackMixin


class NovelEngine(GenerationMixin, ValidationMixin, AnalysisMixin,
                  RequirementsMixin, ExportMixin, CharacterProfileMixin, FeedbackMixin):
    """小说创作引擎 — 多智能体架构（2026-07-31 与实现对齐）:
    Create:    RequirementDecomposer → Planner → StoryGraph/ArcPlanner → CharacterProfiler
    Generate:  Writer(初稿+结尾) / AtomicWriter(逐beat) → PacingChecker质量门
               → AIDetector/HumanRewriter → ConsistencyValidator(L1) → Summarizer → StoryGraph更新
    Interactive: OutlineInteractive (反馈式大纲迭代) · CharacterProfiler (人设蒸馏)
    Support:   ContextUpdater → LogicSupervisor → TwistDesigner
    """

    def __init__(self):
        self.client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
        )
        self.model = config.DEEPSEEK_MODEL
        self.planner = Planner(self.client, self.model)
        self.writer = Writer(self.client, self.model)
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
        # v2.3.4: 角色人设蒸馏（女娲框架移植）
        from .character_profiler import CharacterProfiler
        self.character_profiler = CharacterProfiler(self.client, self.model)
        # v2.3.5: 反馈闭环（👍👎 偏好学习）
        from .feedback_store import FeedbackStore
        self.feedback_store = FeedbackStore(config.NOVELS_DIR)
        # v2.3.6: 角色声音卡（解决角色同质化）
        from .character_voices import CharacterVoices
        self.character_voices = CharacterVoices(self.client, self.model)
        # v2.3.3: 需求拆解结果持久化（SQLite，多进程安全，替代原进程内 dict）
        from .requirements_store import RequirementsStore
        self._req_store = RequirementsStore(config.NOVELS_DIR)
        # v2.3: 原子化生成引擎
        self.atomic_writer = AtomicWriter(self.client, self.model)
        self.beat_assembler = BeatAssembler()
        self._use_atomic = True  # 默认启用原子化生成

        # v2.46: 韧性客户端 — 用于桥接提取等辅助LLM调用
        try:
            from .resilient_client import ResilientLLMClient
            self._resilient = ResilientLLMClient(self.client, self.model)
            log.info("ResilientLLMClient initialized for bridge extraction")
        except ImportError:
            self._resilient = self.client  # 降级用普通客户端

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
                self._req_store.set(creative_input.get("title", inspiration[:20]), requirements)
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
            reqs = self._req_store.get(plan.get("title", ""))
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
        
        # v2.42: 初始化角色状态追踪系统
        try:
            from .character_state import CharacterStateTracker
            tracker = CharacterStateTracker(self.client, self.model, self.memory)
            tracker.init_from_plan(plan["title"])
        except Exception as e:
            log.warning(f"Character state init skipped: {e}")
        
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
                yield {"type": "warning", "message": f"⚠️ 需求深度分析跳过，将直接使用您的灵感描述生成。原因：{e}"}
        else:
            enhanced_input = dict(creative_input)
        
        # ── v2.11: 创意种子注入（架构级随机性）──
        try:
            from .creative_seeds import create_seed_engine
            seed_engine = create_seed_engine(self.memory.novels_dir)
            temp_id = hashlib.md5((inspiration or enhanced_input.get("title","") or "untitled").encode()).hexdigest()[:12]
            seed_text, seeds = seed_engine.inject_into_planning_context(
                temp_id, creative_input.get("genre", ""), ""
            )
            if seeds:
                # 将创意约束作为第一个系统指令注入
                enhanced_input["_creative_seeds"] = seeds
                enhanced_input["_creative_seeds_text"] = seed_text
                enhanced_input["_creative_seeds_temp_id"] = temp_id
                yield {"type": "progress", "phase": "creative_seeds", "pct": 1,
                       "label": f"已注入 {len(seeds)} 个创意约束"}
                yield {"type": "creative_seeds_injected", "count": len(seeds), "temp_id": temp_id}
        except Exception as e:
            log.warning(f"Creative seed injection failed: {e}")

        # ── Phase 1-3: 标准流式规划（使用增强版输入）──
        async for event in self.planner.plan_stream(enhanced_input):
            if event["type"] == "done":
                plan = event.get("plan")
                if not isinstance(plan, dict):
                    log.error(f"create_novel_stream: plan is {type(plan).__name__}, not dict")
                    yield {"type": "error", "message": "❌ 大纲生成失败：数据结构异常。建议检查灵感描述是否清晰，或稍后重试。"}
                    return
                if "title" not in plan or not plan["title"]:
                    log.error(f"create_novel_stream: plan missing title, keys={list(plan.keys())[:10]}")
                    yield {"type": "error", "message": "❌ 大纲生成失败：未检测到书名。建议在灵感中明确书名，或稍后重试。"}
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
                    
                    # 保存到 RequirementsStore (用 title 做 key, SQLite 持久化)
                    self._req_store.set(plan["title"], self._pending_requirements)
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
                
                # ── v2.10: 大纲因果链验证 ──
                try:
                    result = await asyncio.to_thread(
                        validate_and_repair_outline,
                        plan.get("outline", {}), auto_fix=True
                    )
                    report = result["report"]
                    plan["outline"] = result["outline"]
                    # 如果有修复，重新保存 plan.json
                    if report.get("fixes_applied"):
                        atomic_write_json(os.path.join(novel_dir, "plan.json"), plan)
                        log.info(f"CoherenceValidator: {len(report['fixes_applied'])} fixes applied, "
                                f"score={report['score']}")
                    if not report.get("passed"):
                        yield {"type": "warning", "message": 
                              f"🔧 大纲逻辑检查发现 {sum(1 for i in report.get('issues',[]) if i['severity']=='P0')} 个断裂点，"
                              f"已自动修补 {len(report.get('fixes_applied',[]))} 处。生成质量不受影响。"}
                except Exception as e:
                    log.warning(f"CoherenceValidator skipped (non-fatal): {e}")
                
                log.info(f"Novel created (streamed): {plan['title']} ({total_chapters} chapters)"
                        f" — requirements: {self._req_store.get(plan['title']).get('total_count', 0)} subtasks")

                # ── v2.3.5: 创建即蒸馏 — 完成前自动蒸馏所有出场角色（确保人设不崩）──
                try:
                    # 从大纲提取出场角色（主角优先，去重，上限 8 个）
                    char_names = []
                    bible = {}
                    bible_path = os.path.join(novel_dir, "character_bible.json")
                    if os.path.exists(bible_path):
                        with open(bible_path, "r", encoding="utf-8") as _bf:
                            bible = json.load(_bf) or {}
                    protagonist = (bible.get("protagonist") or {}).get("name", "")
                    if protagonist:
                        char_names.append(protagonist)
                    seen = set(char_names)
                    for vol in (plan.get("outline", {}) or {}).get("volumes", []) or []:
                        for ch in vol.get("chapters", []) or []:
                            for c in ch.get("characters", []) or []:
                                if c and c not in seen:
                                    seen.add(c)
                                    char_names.append(c)
                    char_names = char_names[:8]
                    if char_names:
                        yield {"type": "progress", "phase": "character_profiling", "pct": 97,
                               "label": f"正在蒸馏角色人设（0/{len(char_names)}）…"}
                        wb = plan.get("worldbuilding") or {}
                        wb_summary = (f"时代: {wb.get('era', '')}\n力量体系: {wb.get('power_system', '')}\n"
                                      f"核心冲突: {wb.get('core_conflict', '')}")
                        for _ci, _cn in enumerate(char_names):
                            try:
                                _result = await asyncio.to_thread(
                                    self.distill_character_profile, plan["title"], _cn
                                )
                                if "error" in _result:
                                    log.warning(f"Auto-distill {_cn}: {_result['error']}")
                                else:
                                    log.info(f"Auto-distilled profile: {_cn}")
                            except Exception as _pe:
                                log.warning(f"Auto-distill {_cn} failed: {_pe}")
                            yield {"type": "progress", "phase": "character_profiling", "pct": 97,
                                   "label": f"正在蒸馏角色人设（{_ci+1}/{len(char_names)}）：{_cn}…"}
                        yield {"type": "progress", "phase": "character_profiling", "pct": 98,
                               "label": f"角色人设就绪（{len(char_names)} 个角色）"}
                except Exception as _pd_e:
                    log.warning(f"Auto character profiling skipped: {_pd_e}")

            yield event

    async def regenerate_outline_stream(self, novel_id: str, feedback: str) -> AsyncIterator[dict]:
        """根据修改意见重新生成大纲（保留世界观和角色）"""
        plan = self.get_novel(novel_id)
        if not plan:
            yield {"type": "error", "message": f"❌ 小说 '{novel_id}' 不存在。请从书架选择有效的小说。"}
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

    def delete_novel(self, novel_id: str) -> bool:
        """删除小说及其所有章节、状态文件
        
        Args:
            novel_id: 小说目录名
            
        Returns:
            True 如果删除成功，False 如果小说不存在
        """
        import shutil
        novel_dir = self.memory.get_novel_dir(novel_id)
        if not os.path.exists(novel_dir):
            return False
        
        # v2.3: 输入校验 — 防止路径遍历攻击
        safe_name = os.path.basename(os.path.normpath(novel_id))
        if safe_name != novel_id or ".." in novel_id or "/" in novel_id or "\\" in novel_id:
            log.warning(f"Rejected unsafe novel_id: {novel_id}")
            return False
        
        # 清除缓存
        self.memory.invalidate_novel(novel_id)
        
        try:
            shutil.rmtree(novel_dir)
            log.info(f"Novel deleted: {novel_id}")
            return True
        except OSError as e:
            log.error(f"Failed to delete novel {novel_id}: {e}")
            return False

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




    @staticmethod
    def _detect_narrative_pov(text: str) -> str:
        """从章节正文自动检测叙事人称。
        
        策略：开篇前800字是POV信号最强的区域——主角自我介绍/内心独白必然用「我」。
        全文采样作为兜底。
        
        Returns:
            'first_person' | 'third_person' | '' (未确定)
        """
        if not text:
            return ''
        
        # 去掉 markdown 标题行（# / ## 开头），避免干扰计数
        import re
        body = re.sub(r'^#.*$', '', text, flags=re.MULTILINE)
        body = body.strip()
        
        if len(body) < 50:
            return ''
        
        # 开篇 800 字 — 最强信号
        opening = body[:800]
        first_open = opening.count('我')
        third_open = opening.count('他') + opening.count('她')
        
        if first_open > third_open * 2 and first_open >= 3:
            return 'first_person'
        if third_open > first_open * 2 and third_open >= 3:
            return 'third_person'
        
        # 全量采样 3000 字 — 兜底
        sample = body[:3000]
        first_all = sample.count('我')
        third_all = sample.count('他') + sample.count('她')
        
        if first_all > third_all * 2 and first_all > 5:
            return 'first_person'
        if third_all > first_all * 1.5 and third_all > 5:
            return 'third_person'
        
        return ''

    def _save_narrative_pov(self, novel_id: str, text: str):
        """第一章生成后，从正文检测人称并存储到 plan 中。"""
        try:
            pov = self._detect_narrative_pov(text)
            if pov:
                plan = self.memory.read("plan", novel_id)
                if plan.get('narrative_pov') != pov:
                    plan['narrative_pov'] = pov
                    self.memory.write("plan", novel_id, plan)
                    log.info(f"POV auto-detected from chapter 1: {pov}")
        except Exception as e:
            log.warning(f"POV detection failed (non-fatal): {e}")

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



    # ═══════════════════════════════════════════════════════
    # Phase 4: 交互式大纲 (v2 — FeedbackDecomposer驱动)
    # ═══════════════════════════════════════════════════════

    async def interactive_outline_stream(self, novel_id: str, feedback: str) -> AsyncIterator[dict]:
        """v2 交互式大纲: FeedbackDecomposer 语义拆解 → 逐条精确执行 → diff输出"""
        plan = self.get_novel(novel_id)
        if not plan:
            yield {"type": "error", "message": f"❌ 小说 '{novel_id}' 不存在。请从书架选择有效的小说。"}
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
                
                # ── v2.10: 大纲因果链验证 ──
                try:
                    cv_result = await asyncio.to_thread(
                        validate_and_repair_outline,
                        new_plan.get("outline", {}), auto_fix=True
                    )
                    new_plan["outline"] = cv_result["outline"]
                    if cv_result["report"].get("fixes_applied"):
                        log.info(f"CoherenceValidator (interactive): {len(cv_result['report']['fixes_applied'])} fixes")
                except Exception as e:
                    log.warning(f"CoherenceValidator skipped in interactive path: {e}")
                
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





    # ═══════════════════════════════════════════════════════
    # Phase 6: 开头分析 (新)
    # ═══════════════════════════════════════════════════════



    # ═══════════════════════════════════════════════════════
    # Phase 7: 反转设计 (新)
    # ═══════════════════════════════════════════════════════



    # ═══════════════════════════════════════════════════════
    # Phase 8: 多Agent需求拆解与监督系统
    # ═══════════════════════════════════════════════════════





