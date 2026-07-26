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
from .beat_decomposer import BeatDecomposer, Beat
from .atomic_writer import AtomicWriter
from .beat_assembler import BeatAssembler
from .storygraph_interventions import analyze_and_inject
from .coherence_validator import validate_and_repair_outline

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════
# v2.13: Humanizer 语义结尾保护
# ═══════════════════════════════════════════

def _extract_key_ending(text: str, window: int = 600) -> dict | None:
    """提取原文关键结尾信息，用于 Humanizer 后恢复保护。
    
    返回 {sentences, ngrams, has_hook} 或 None。
    """
    import re
    if len(text) < 100:
        return None
    
    # 按句子边界切分
    raw = re.split(r'(?<=[。！？……])', text[-window:])
    sentences = [s.strip() for s in raw if s.strip() and len(s.strip()) > 2]
    
    if len(sentences) < 2:
        return None
    
    # 最后 2-3 句为关键结尾
    key = sentences[-3:] if len(sentences) >= 3 else sentences[-2:]
    ending_text = ''.join(key)
    
    # 3-gram 特征集
    chars = ending_text.replace(' ', '').replace('\n', '')
    ngrams = {chars[i:i+3] for i in range(len(chars) - 2)} if len(chars) >= 3 else set()
    
    # 勾子检测
    hook_markers = ['突然', '忽然', '猛地', '即将', '等待', '然而', '可是',
                    '但', '只见', '……', '—', '未完', '待续', '下一章']
    has_hook = any(m in ending_text for m in hook_markers)
    
    return {
        "text": ending_text,
        "ngrams": ngrams,
        "has_hook": has_hook,
        "sentence_count": len(key),
    }


def _protect_ending_semantic(original: str, rewritten: str, ending_info: dict) -> str:
    """v2.13: 语义结尾保护 — 三重检测决定是否恢复原文结尾。
    
    1. n-gram 重叠率 < 40% → 结尾已被大改
    2. 原文结尾长度 > 改写结尾 1.5x → 被截断
    3. 原文有勾子改写版没有 → 悬念被抹掉
    
    满足任一条件即恢复原文结尾。
    """
    import re
    
    # 提取改写版结尾
    raw = re.split(r'(?<=[。！？……])', rewritten[-600:])
    r_sentences = [s.strip() for s in raw if s.strip() and len(s.strip()) > 2]
    if len(r_sentences) < 2:
        # 改写版结尾太短 — 直接用原文结尾替换
        if len(ending_info["text"]) > 80:
            cutoff = max(0, len(rewritten) - len(ending_info["text"]))
            protected = rewritten[:cutoff] + ending_info["text"]
            log.info(f"Humanizer: ending protected (rewrite too short, {len(ending_info['text'])} chars kept)")
            return protected
        return rewritten
    
    r_ending_text = ''.join(r_sentences[-3:] if len(r_sentences) >= 3 else r_sentences[-2:])
    
    # 指标1: n-gram 重叠
    if ending_info["ngrams"]:
        r_chars = r_ending_text.replace(' ', '').replace('\n', '')
        r_ngrams = {r_chars[i:i+3] for i in range(len(r_chars) - 2)} if len(r_chars) >= 3 else set()
        if ending_info["ngrams"]:
            overlap = len(ending_info["ngrams"] & r_ngrams) / len(ending_info["ngrams"])
        else:
            overlap = 1.0
    else:
        overlap = 1.0
    
    # 指标2: 长度剧烈缩短
    length_shrink = len(ending_info["text"]) > len(r_ending_text) * 1.5
    
    # 指标3: 勾子丢失
    hook_markers = ['突然', '忽然', '猛地', '即将', '等待', '然而', '可是',
                    '但', '只见', '……', '—', '未完', '待续', '下一章']
    r_has_hook = any(m in r_ending_text for m in hook_markers)
    hook_lost = ending_info["has_hook"] and not r_has_hook
    
    needs_protection = overlap < 0.4 or length_shrink or hook_lost
    
    if needs_protection:
        # 用原文结尾替换改写版结尾
        ending_len = len(ending_info["text"])
        cutoff = max(0, len(rewritten) - len(r_ending_text))
        protected = rewritten[:cutoff] + ending_info["text"]
        
        reasons = []
        if overlap < 0.4: reasons.append(f"overlap={overlap:.0%}")
        if length_shrink: reasons.append("length_shrink")
        if hook_lost: reasons.append("hook_lost")
        
        log.info(f"Humanizer: ending semantically protected ({', '.join(reasons)})")
        return protected
    
    return rewritten


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
        # v2.3: 原子化生成引擎
        self.atomic_writer = AtomicWriter(self.client, self.model)
        self.beat_assembler = BeatAssembler()
        self._use_atomic = True  # 默认启用原子化生成

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
                yield {"type": "warning", "message": f"⚠️ 需求深度分析跳过，将直接使用您的灵感描述生成。原因：{e}"}
        else:
            enhanced_input = dict(creative_input)
        
        # ── v2.11: 创意种子注入（架构级随机性）──
        try:
            from .creative_seeds import create_seed_engine
            seed_engine = create_seed_engine(self.memory.storage_dir)
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
                        f" — requirements: {self._requirements.get(plan['title'], {}).get('total_count', 0)} subtasks")
            
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
                    yield {"type": "error", "message": f"⏳ 第{chapter_num}章正在生成中（已运行{int(lock_age)}秒），请等待完成后再试"}
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
                yield {"type": "error", "message": f"❌ 小说 '{novel_id}' 不存在。请从书架选择有效的小说。"}
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

            # ── v2.3.1: 剧情图谱反哺 — 主动干预指令注入 ──
            try:
                novel_dir = self.memory.get_novel_dir(novel_id)
                intervention_ctx = analyze_and_inject(
                    novel_dir, chapter_num, chapter_outline
                )
                if intervention_ctx:
                    context += "\n\n" + intervention_ctx
                    log.info(f"StoryGraph interventions injected for Ch{chapter_num}")
            except Exception as e:
                log.warning(f"Intervention injection skipped: {e}")

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
            # v2.7: 快餐模式字数自适应 — 2500字/章(短剧化节奏)
            if plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False):
                target_words = 2500

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
                normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False), fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
                chapter_outline=chapter_outline,  # v2.12: 传递大纲用于两阶段结尾生成
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
                # v2.13: 保存结尾 (Humanizer会覆盖全文 → 语义保护Phase 2结尾)
                # 不再仅靠长度——改用句子拆分 + n-gram重叠 + 勾子检测三重保护
                _ending_saved = _extract_key_ending(full_text)
                result = await asyncio.to_thread(
                    humanize_pipeline, full_text, detector, rewriter,
                    scene_desc=chapter_summary,
                    target_length=target_words,
                    min_score_threshold=30,
                )
                if result["rewritten"]:
                    ai_report = result
                    rewritten = result["text"]
                    if len(rewritten) > 500 and _ending_saved:
                        protected = _protect_ending_semantic(full_text, rewritten, _ending_saved)
                        if protected != rewritten:
                            full_text = protected
                        else:
                            full_text = rewritten
                    else:
                        full_text = rewritten
                    log.info(f"AI Humanizer: score {ai_report['ai_score_before']}→{ai_report.get('ai_score_after','?')}, rewritten")
                    yield {"type": "ai_report", 
                           "score_before": ai_report["ai_score_before"],
                           "score_after": ai_report.get("ai_score_after", ai_report["ai_score_before"]),
                           "rewritten": True}
            except Exception as e:
                log.warning(f"AI Humanizer skipped: {e}")

            # ── v2.4.1: 段落规范化安全网 ──
            # 在所有后处理后，强制合并短行碎片，确保输出是正常段落格式
            try:
                from core.shared_memory import normalize_chapter_paragraphs
                before_lines = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                full_text = normalize_chapter_paragraphs(full_text)
                after_lines = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                if before_lines != after_lines:
                    log.info(f"Paragraph normalize: ≤10char lines reduced {before_lines}→{after_lines}")
            except Exception as e:
                log.warning(f"Paragraph normalize skipped: {e}")

            # ── v2.6: 质量门 — 对话占比/爽点密度/碎片化自动检测 ──
            quality_report = None
            try:
                is_fast_food = plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False)
                checker = PacingChecker(self.client, self.model)
                qr = checker.quick_quality_check(full_text, fast_food=is_fast_food)
                quality_report = qr
                log.info(f"Quality gate: score={qr['score']}, pass={qr['pass']}, issues={len(qr['issues'])}")
                
                if not qr["pass"] and qr["issues"]:
                    issues_text = "; ".join(qr["issues"])
                    log.warning(f"Quality gate FAILED (score={qr['score']}): {issues_text}")
                    
                    # 构建重生成反馈
                    regenerate_feedback = f"【质量门自动反馈】以下问题需要修正：{issues_text}。请减少无意义对话，增加动作描写和冲突推进，确保每段都有实质性剧情推进。"
                    
                    # 重试一次
                    yield {"type": "quality_warning", "score": qr["score"], "issues": qr["issues"],
                           "message": f"📝 质量检查发现 {len(qr['issues'])} 个可改进点（评分 {qr['score']}），正在自动优化重写..."}
                    
                    retry_text = ""
                    async for text in self.writer.write_stream(
                        context=context + f"\n\n⚠️ 上一版质量不合格（评分{qr['score']}）。{regenerate_feedback}",
                        genre=genre, style=style,
                        target_words=target_words,
                        writing_mode=writing_mode,
                        normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False), fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
                        chapter_outline=chapter_outline,
                        skip_ending=True,  # v2.12: 重试不重复生成结尾
                    ):
                        retry_text += text
                    
                    if retry_text and len(retry_text) > len(full_text) * 0.5:
                        # 再检查一次
                        qr2 = checker.quick_quality_check(retry_text, fast_food=is_fast_food)
                        if qr2["score"] > qr["score"]:
                            full_text = retry_text
                            quality_report = qr2
                            log.info(f"Quality gate retry PASSED: score {qr['score']} → {qr2['score']}")
                            yield {"type": "quality_retry", "score_before": qr["score"], "score_after": qr2["score"]}
                        else:
                            log.warning(f"Quality gate retry still low: {qr2['score']}, keeping better version")
                            if qr2["score"] >= qr["score"]:
                                full_text = retry_text
                                quality_report = qr2
                    else:
                        log.warning("Quality gate retry text too short, keeping original")
            except Exception as e:
                log.warning(f"Quality gate skipped: {e}")

            # ── v2.11: 确保结尾是完整句子 ──
            from .writer import _ensure_complete_ending
            original_len = len(full_text)
            full_text = _ensure_complete_ending(full_text)
            if len(full_text) != original_len:
                log.info(f"Ending trimmed from {original_len} to {len(full_text)} chars (removed incomplete sentence)")

            # 最终保存章节（覆盖增量保存的临时文件）
            formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{full_text}"
            self.memory.save_chapter(novel_id, chapter_num, formatted)

            # ── v2.10: 提取章节桥接数据 → 保证下章接续 ──
            try:
                bridge = await asyncio.to_thread(
                    self.memory.extract_bridge_from_chapter,
                    full_text, chapter_num, chapter_outline,
                    client=self.client, model=self.model,
                )
                if bridge:
                    self.memory.save_bridge(novel_id, chapter_num, bridge)
                    log.info(f"ChapterBridge saved for chapter {chapter_num}: "
                            f"next_beat={bridge.get('next_beat','')[:60]}...")
                else:
                    log.warning(f"ChapterBridge extraction returned None for chapter {chapter_num}")
            except Exception as e:
                log.warning(f"ChapterBridge extraction failed (non-fatal): {e}")

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
                log.warning(f"Chapter {chapter_num} incomplete after Writer retries: {reason}. "
                           f"Engine fallback: retrying generation...")
                yield {"type": "warning", "message": f"⏳ 本章内容不完整（{reason}），正在自动重新生成以补全内容..."}
                
                # Engine-level retry: 用更大的 max_tokens 重新调用 Writer
                try:
                    retry_context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)
                    retry_text = ""
                    async for text in self.writer.write_stream(
                        context=retry_context,
                        genre=genre,
                        style=style,
                        target_words=max(target_words, int(len(full_text) * 1.5 / 2)),  # 调高目标
                        writing_mode=writing_mode,
                        normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False),
                        fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
                        chapter_outline=chapter_outline,
                        skip_ending=True,  # v2.12: 重试不重复生成结尾
                    ):
                        retry_text += text
                        yield {"type": "text", "content": text}
                    
                    is_trunc2, reason2 = _check_truncation(retry_text, target_words)
                    if not is_trunc2 and len(retry_text) > len(full_text):
                        full_text = retry_text
                        log.info(f"Engine retry OK: {len(full_text)} chars")
                        yield {"type": "status", "message": "重新生成完成，内容已补全"}
                    else:
                        log.warning(f"Engine retry also short ({len(retry_text)} chars), using best")
                        if len(retry_text) > len(full_text):
                            full_text = retry_text
                except Exception as re:
                    log.warning(f"Engine retry failed: {re}, keeping original")

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
                self.memory.invalidate_all(novel_id)
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
                            normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False), fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
                            chapter_outline=chapter_outline,
                            skip_ending=True,  # v2.12: 修复重试不重复生成结尾
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

            # ── v2.14: 每章自动摘要生成（注入后续章节上下文）──
            try:
                chapter_text = self.get_chapter(novel_id, chapter_num) or full_text
                if chapter_text and len(chapter_text) > 200:
                    summary_result = await asyncio.to_thread(
                        self.chapter_summarizer.summarize_chapter,
                        chapter_num, chapter_text
                    )
                    if summary_result:
                        state = self.memory.get_novel_state(novel_id)
                        if "summaries" not in state:
                            state["summaries"] = {}
                        state["summaries"][str(chapter_num)] = summary_result
                        self.memory.save_novel_state(novel_id, state)
                        log.info(f"Auto-summary for Ch{chapter_num}: {len(summary_result.get('summary',''))} chars")
            except Exception as e:
                log.warning(f"Auto-summary for Ch{chapter_num} skipped: {e}")

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
            yield {"type": "error", "message": f"❌ 章节生成失败：{e}。请检查网络连接或稍后重试。"}
        finally:
            # 清理并发锁
            try:
                if os.path.exists(lock_file):
                    os.remove(lock_file)
            except OSError:
                pass

    async def atomic_generate_chapter_stream(
        self, novel_id: str, chapter_num: int, writing_mode: str = "webnovel",
        feedback: str = None,
    ) -> AsyncGenerator[dict, None]:
        """原子化生成章节：逐beat独立LLM调用 → 装配 → 评估
        
        与 generate_chapter_stream 的区别：
        - 传统: 1次LLM调用 → 整章2000字（趋同）
        - 原子化: 5-7次独立LLM调用 → 每beat 200-400字 → 装配（多样性爆炸）
        """
        novel_dir = self.memory.get_novel_dir(novel_id)
        lock_file = os.path.join(novel_dir, f".generating_{chapter_num:04d}.lock")
        
        # 并发锁（与generate_chapter_stream共享）
        if os.path.exists(lock_file):
            try:
                lock_age = time.time() - os.path.getmtime(lock_file)
                if lock_age < 300:
                    yield {"type": "error", "message": f"第{chapter_num}章正在生成中"}
                    return
                else:
                    os.remove(lock_file)
            except OSError:
                pass
        
        os.makedirs(novel_dir, exist_ok=True)
        try:
            with open(lock_file, "w") as lf:
                lf.write(str(time.time()))
        except IOError:
            pass
        
        try:
            plan = self.get_novel(novel_id)
            if not plan:
                yield {"type": "error", "message": f"❌ 小说 '{novel_id}' 不存在。请从书架选择有效的小说。"}
                return
            
            chapter_outline = self._find_chapter_outline(plan, chapter_num)
            if not chapter_outline:
                chapter_outline = {
                    "number": chapter_num, "title": f"第{chapter_num}章",
                    "summary": "继续推进主线", "emotion_curve": "平稳→紧张→悬念",
                    "characters": ["主角"], "hook": "留下悬念",
                    "target_words": config.DEFAULT_CHAPTER_WORDS,
                }
            
            # ── v2.9 Phase 0: 生成章节蓝图 ──
            yield {"type": "status", "message": "生成章节蓝图..."}
            
            # 构建完整写作上下文（常规Writer用的五层上下文）
            chapter_context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)
            
            # 用LLM生成300字叙事蓝图
            blueprint = ""
            try:
                blueprint_prompt = f"""根据以下大纲和设定，写出本章的叙事蓝图。蓝图是一段200-300字的连贯叙事概要，描述本章从头到尾具体发生了什么。不是大纲条目，是用叙述语言把本章过一遍。

{chapter_context[:2000]}

只输出蓝图正文，不要标题。"""
                
                bp_resp = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "你是一位小说策划。把大纲转化为叙事蓝图。"},
                        {"role": "user", "content": blueprint_prompt},
                    ],
                    temperature=0.7,
                    max_tokens=600,
                )
                blueprint = bp_resp.choices[0].message.content.strip()
                log.info(f"Blueprint generated for Ch{chapter_num}: {len(blueprint)} chars")
            except Exception as e:
                log.warning(f"Blueprint generation failed, using summary: {e}")
                blueprint = chapter_outline.get("summary", "继续推进主线剧情")
            
            yield {"type": "blueprint", "text": blueprint[:100] + "..."}
            
            # ── Phase 1: 拆解为 beat ──
            yield {"type": "status", "message": f"拆解第{chapter_num}章为节拍..."}
            
            is_first = (chapter_num == 1)
            
            # 判断是否高潮章（检查 arcplan）
            is_climax = False
            try:
                from .arcplanner import is_arc_climax
                sg_path = os.path.join(novel_dir, "storygraph.json")
                if os.path.exists(sg_path):
                    sg_data = safe_read_json(sg_path)
                    if sg_data and sg_data.get("arcs"):
                        for arc in sg_data["arcs"]:
                            if is_arc_climax(arc, chapter_num):
                                is_climax = True
                                break
            except Exception:
                pass
            
            # 获取角色快照
            chars = chapter_outline.get("characters", [])
            try:
                sg_path = os.path.join(novel_dir, "storygraph.json")
                if os.path.exists(sg_path):
                    sg_data = safe_read_json(sg_path)
                    if sg_data:
                        snaps = sg_data.get("char_snapshots", {})
                        char_context = "\n".join(
                            f"{n}: {s.get('current_emotion','?')} @{s.get('current_location','?')}"
                            for n, s in snaps.items() if n in chars
                        )
                    else:
                        char_context = ""
                else:
                    char_context = ""
            except Exception:
                char_context = ""
            
            decomposer = BeatDecomposer(seed=chapter_num * 100 + int(time.time()) % 100)
            beats = decomposer.decompose(
                chapter_outline, chapter_num,
                is_first_chapter=is_first,
                is_climax_chapter=is_climax,
                available_characters=chars,
            )
            
            yield {"type": "beats_decomposed", "count": len(beats),
                   "functions": [b.function for b in beats]}
            
            # ── Phase 2: 逐 beat 独立生成 ──
            style = plan.get("style", "热血爽文")
            genre = plan.get("genre", "玄幻")
            style_guide = _get_style_guide(style, genre)
            
            # v2.3.1: 剧情图谱反哺 + 活人感注入
            try:
                intervention_ctx = analyze_and_inject(novel_dir, chapter_num, chapter_outline)
                if intervention_ctx:
                    char_context += "\n\n" + intervention_ctx
            except Exception as e:
                log.warning(f"Intervention injection skipped in atomic: {e}")
            
            beats_text = []
            async for beat_result in self.atomic_writer.write_beats_stream(
                beats, char_context, style_guide, chapter_num,
                blueprint=blueprint, chapter_context=chapter_context[:1200]
            ):
                beats_text.append({
                    "index": beat_result["beat_index"],
                    "function": beat_result["beat_function"],
                    "text": beat_result["text"],
                    "temperature": beat_result["temperature"],
                })
                yield {
                    "type": "beat_progress",
                    "beat_index": beat_result["beat_index"],
                    "total_beats": len(beats),
                    "function": beat_result["beat_function"],
                }
            
            # ── Phase 3: 装配章节 ──
            yield {"type": "status", "message": "装配节拍..."}
            
            assembly = await asyncio.to_thread(
                self.beat_assembler.assemble,
                beats_text,
                chapter_outline.get("title", f"第{chapter_num}章"),
                chapter_num,
            )
            
            full_text = assembly["raw_text"]
            formatted = assembly["full_text"]
            
            # ── v2.4.1: 段落规范化安全网 ──
            try:
                from core.shared_memory import normalize_chapter_paragraphs
                before_lines = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                full_text = normalize_chapter_paragraphs(full_text)
                # v2.11: 确保结尾完整句子
                from .writer import _ensure_complete_ending
                full_text = _ensure_complete_ending(full_text)
                formatted = f"# 第{chapter_num}章 {chapter_outline.get('title', f'第{chapter_num}章')}\n\n{full_text}"
                after_lines = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                if before_lines != after_lines:
                    log.info(f"Atomic paragraph normalize: ≤10char lines reduced {before_lines}→{after_lines}")
            except Exception as e:
                log.warning(f"Atomic paragraph normalize skipped: {e}")
            
            # ── Phase 4: 保存 ──
            self.memory.save_chapter(novel_id, chapter_num, formatted)
            
            # ── 完整度验证 ──
            from .writer import _check_truncation
            is_trunc, reason = _check_truncation(full_text, chapter_outline.get("target_words", 2000))
            if is_trunc:
                log.warning(f"Atomic chapter {chapter_num} may be incomplete: {reason}")
                yield {"type": "warning", "message": f"⚠️ 原子模式生成的本章可能不完整（{reason}），建议使用常规模式重新生成此章"}
            
            # ── v2.10: 提取章节桥接数据 ──
            try:
                bridge = await asyncio.to_thread(
                    self.memory.extract_bridge_from_chapter,
                    full_text, chapter_num, chapter_outline,
                    client=self.client, model=self.model,
                )
                if bridge:
                    self.memory.save_bridge(novel_id, chapter_num, bridge)
                    log.info(f"ChapterBridge (atomic) saved for chapter {chapter_num}")
            except Exception as e:
                log.warning(f"ChapterBridge extraction failed in atomic (non-fatal): {e}")
            
            # 更新状态
            state = self.memory.get_novel_state(novel_id)
            completed = state.get("completed_chapters", [])
            if chapter_num not in completed:
                completed.append(chapter_num)
                completed.sort()
            state["completed_chapters"] = completed
            self.memory.save_novel_state(novel_id, state)
            
            # ── Phase 5: 质量评估 ──
            yield {"type": "status", "message": "质量评估..."}
            
            evaluation = assembly["quality_report"]
            
            yield {
                "type": "evaluation",
                "overall_score": evaluation.get("overall_score", 0),
                "verdict": evaluation.get("verdict", ""),
                "distinct_1": evaluation.get("distinct_1", 0),
                "hook_strength": evaluation.get("hook", {}).get("strength", 0),
                "dopamine_density": evaluation.get("dopamine", {}).get("density_per_2000", 0),
                "ai_slop_score": evaluation.get("ai_slop", {}).get("score", 100),
                "coherence": evaluation.get("coherence", {}).get("verdict", ""),
                "duplicates_removed": assembly["duplicates_removed"],
                "transitions_added": len(assembly["transitions"]),
            }
            
            # ── 流式输出正文 ──
            yield {"type": "text", "content": full_text}
            yield {"type": "done", "content": formatted, "chapter_num": chapter_num,
                   "atomic": True, "beat_count": len(beats)}
            
            # 更新校验
            try:
                from .storygraph import StoryGraph, extract_storygraph_from_chapter, apply_extraction
                sg_path = os.path.join(novel_dir, "storygraph.json")
                sg_data = safe_read_json(sg_path) or {}
                sg = StoryGraph.from_dict(sg_data)
                extract_result = await asyncio.to_thread(
                    extract_storygraph_from_chapter,
                    chapter_text=full_text, current_graph=sg_data,
                    chapter_num=chapter_num, chapter_outline=chapter_outline,
                    client=self.client, model=self.model,
                )
                apply_extraction(sg, extract_result, chapter_num)
                atomic_write_json(sg_path, sg.to_dict())
                self.memory.invalidate_all(novel_id)
            except Exception as e:
                log.warning(f"StoryGraph update skipped in atomic: {e}")
            
        except Exception as e:
            log.exception(f"Atomic chapter generation failed: {e}")
            yield {"type": "error", "message": str(e)}
        finally:
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


def _get_style_guide(style: str, genre: str) -> str:
    """获取简化的风格指南（用于 AtomicWriter）"""
    from .styles import get_style, build_style_prompt
    try:
        style_config = get_style(style)
        return build_style_prompt(style_config)
    except Exception:
        return f"写作风格：{style}。题材：{genre}。"
