"""NovelEngine GenerationMixin — 章节生成 — 普通路径（Writer 两遍式）与原子化路径（逐 beat）

由 tools/split_engine.py 从 engine.py 自动拆分。
依赖 NovelEngine 提供的 self.client/self.model/self.memory 等属性。
"""
import logging

log = logging.getLogger(__name__)

import asyncio
import json
import os
import re
import time
# Allow importing from parent dir (works both as package and standalone)
try:
    from backend import config
except ImportError:
    import config
from typing import AsyncGenerator, Optional, AsyncIterator
from ..atomic_io import atomic_write_json, safe_read_json
from ..beat_decomposer import BeatDecomposer, Beat
from ..pacing_checker import PacingChecker
from ..chapter_summarizer import check_and_compress
from ..storygraph_interventions import analyze_and_inject




# ===== _extract_key_ending (从 engine.py 迁移) =====
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



# ===== _protect_ending_semantic (从 engine.py 迁移) =====
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




# ===== _get_style_guide (从 engine.py 迁移) =====
def _get_style_guide(style: str, genre: str) -> str:
    """获取简化的风格指南（用于 AtomicWriter）"""
    from ..styles import get_style, build_style_prompt
    try:
        style_config = get_style(style)
        return build_style_prompt(style_config)
    except Exception:
        return f"写作风格：{style}。题材：{genre}。"


class GenerationMixin:
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

    def _decompose_chapter_beats(self, chapter_outline: dict, chapter_num: int,
                                 novel_dir: str, is_first: bool = False) -> list:
        """AUDIT P1-1: 统一的章节 beat 分解助手（普通/原子路径共用节奏骨架）"""
        is_climax = False
        try:
            from ..arcplanner import is_arc_climax
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
        chars = chapter_outline.get("characters", []) or []
        decomposer = BeatDecomposer(seed=chapter_num * 100 + int(time.time()) % 100)
        return decomposer.decompose(
            chapter_outline, chapter_num,
            is_first_chapter=is_first,
            is_climax_chapter=is_climax,
            available_characters=chars,
        )

    def _format_beats_instruction(self, beats: list) -> str:
        """AUDIT P1-1: 把 beat 列表渲染为紧凑节拍骨架，供普通 Writer 单次生成"""
        lines = ["## 🎬 本章节拍结构（严格按此节奏推进，节拍顺序不可跳跃、不可合并）"]
        for b in beats:
            lines.append(
                f"{b.index + 1}. {b.function}｜{b.goal}｜"
                f"情绪 {b.emotion_start}→{b.emotion_end}｜"
                f"冲突 {b.conflict_type}/{b.conflict_intensity}｜"
                f"字数 {b.min_words}-{b.max_words}"
            )
            if b.key_event:
                lines.append(f"   └ 核心事件: {b.key_event}")
            if b.character_focus:
                lines.append(f"   └ 聚焦角色: {b.character_focus}")
        return "\n".join(lines)

    def _strip_instruction_leaks(self, text: str) -> tuple:
        """AUDIT P0-1: 扫描正文中的元指令泄漏，返回 (清理后文本, 泄漏行数)"""
        markers = ("═══", "以下为写作元指令", "本章必须覆盖以下核心事件", "章末钩子方向")
        leaked = [ln for ln in text.splitlines() if any(m in ln for m in markers)]
        if not leaked:
            return text, 0
        clean = [ln for ln in text.splitlines() if not any(m in ln for m in markers)]
        return "\n".join(clean).strip(), len(leaked)

    def build_requirements_instruction(self, novel_id: str, chapter_num: int,
                                       plan: dict = None) -> str:
        """v2.4.7: 将用户需求注入章节生成上下文

        问题: 需求拆解在创建时执行、大纲阶段也注入了，但章节正文生成
        (writer) 时需求完全丢失 → 用户感觉"生成结果没反映需求"。
        修复: 从 RequirementsStore 读取需求，按章节范围过滤
        (global/opening/ending)，只注入 P0/P1 与当前章节相关的条目，
        压缩为轻量指令追加到 writer 上下文。

        Args:
            novel_id: 小说ID（目录名）
            chapter_num: 当前章节号
            plan: 已有 plan（用于解析 title 作为 req key，可空）

        Returns:
            需求指令文本（空字符串 = 无需求或读取失败）
        """
        try:
            req_store = getattr(self, "_req_store", None)
            if req_store is None:
                return ""

            # requirements.db 以 plan title 为 key；先解析 title
            title = ""
            if plan and plan.get("title"):
                title = plan["title"]
            if not title:
                try:
                    plan_data = self.memory.read("plan", novel_id)
                    if isinstance(plan_data, dict):
                        title = plan_data.get("title", "")
                except Exception:
                    pass
            if not title:
                return ""

            reqs = req_store.get(title)
            if not reqs:
                return ""

            subtasks = reqs.get("subtasks", [])
            if not subtasks:
                return ""

            # 判断本章在全书的位置（用于 scope 过滤）
            total = 0
            if plan and plan.get("outline", {}).get("total_chapters"):
                total = int(plan["outline"]["total_chapters"])
            if not total:
                try:
                    total = int(plan_data.get("outline", {}).get("total_chapters", 0))
                except Exception:
                    total = 0
            is_opening = chapter_num <= max(2, total // 10) if total else chapter_num <= 3
            is_ending = chapter_num >= total - max(1, total // 10) if total else False

            # 过滤：global 恒生效；opening 只在前 10% 章节；ending 只在最后 10%
            active = []
            for t in subtasks:
                scope = t.get("scope", "global")
                if scope == "opening" and not is_opening:
                    continue
                if scope == "ending" and not is_ending:
                    continue
                if t.get("status") == "resolved":
                    continue
                active.append(t)

            if not active:
                return ""

            # 压缩为轻量指令：P0 全部 + P1 只留 title/must_include 关键信息
            lines = ["## 🎯 用户需求（必须逐条满足，写入本章剧情）"]
            for t in active[:20]:
                pri = t.get("priority", "P1")
                title_t = t.get("title", "")
                must_inc = t.get("must_include", []) or []
                desc = t.get("description", "") or ""
                scope_label = {"opening": "【开篇】", "ending": "【结局】", "mid": "【中期】"}.get(
                    t.get("scope", "global"), "")
                if pri == "P0":
                    line = f"- [P0]{scope_label}{title_t}"
                    if must_inc:
                        line += "：包含 " + "、".join(str(m)[:24] for m in must_inc[:3])
                    lines.append(line)
                elif len(lines) < 12:  # P1 限量，防止上下文膨胀
                    line = f"- {scope_label}{title_t}"
                    if must_inc:
                        line += "（" + "、".join(str(m)[:18] for m in must_inc[:2]) + "）"
                    lines.append(line)
                del desc  # 描述不注入（大纲已体现），节省 token

            text = "\n".join(lines)
            log.info(f"Requirements injected for Ch{chapter_num}: {len(active)}/{len(subtasks)} active")
            return text
        except Exception as e:
            log.warning(f"Requirements instruction build failed (non-fatal): {e}")
            return ""

    # ── 尺度关键词（命中则注入成熟题材指令）──
    _SCALE_KEYWORDS = (
        "成人", "亲密", "情欲", "尺度", "暧昧", "激情", "欲望", "肉体",
        "性感", "擦边", "香艳", "露骨", "床戏", "缠绵", "荷尔蒙",
        "成年", "恋人", "身体接触", "亲热", "18+", "18禁", "R18",
    )

    def _build_scale_instruction(self, novel_id: str, plan: dict = None) -> str:
        """v2.6: 检测用户需求/灵感中的尺度关键词 → 注入成熟题材写作指令

        实测：DeepSeek 默认（创作自由声明）输出偏保守；显式授权后
        能写到"擦边文学"尺度（衣物/亲吻/身体接触/感官细节）。
        这里把用户的尺度诉求转成模型可执行的指令。
        """
        import re as _re
        haystacks = []
        # 1) creative_input（创建时的灵感/要求全文）
        try:
            ci = (plan or {}).get("_meta", {}).get("creative_input", {}) or {}
            for _k, _v in ci.items():
                if isinstance(_v, str) and _v.strip():
                    haystacks.append(_v)
                elif isinstance(_v, (list, tuple)):
                    haystacks.extend(str(x) for x in _v if isinstance(x, str))
        except Exception:
            pass
        # 2) RequirementsStore 子任务全量（title/description/must_include）
        try:
            req_store = getattr(self, "_req_store", None)
            title = ""
            if plan and plan.get("title"):
                title = plan["title"]
            if title and req_store:
                reqs = req_store.get(title)
                if reqs:
                    for t in (reqs.get("subtasks", []) or [])[:30]:
                        haystacks.append(str(t.get("title", "")))
                        haystacks.append(str(t.get("description", "")))
                        haystacks.extend(str(x) for x in (t.get("must_include", []) or [])[:6])
        except Exception:
            pass

        joined = "\n".join(x for x in haystacks if x)
        if not joined:
            return ""
        hit = [k for k in self._SCALE_KEYWORDS if k in joined]
        if not hit:
            return ""

        log.info(f"Scale instruction injected (keywords: {hit[:6]})")
        return (
            "## 🎯 成熟题材指令（用户需求，必须执行）\n"
            "- 本章若涉及成年角色之间的亲密场景，必须正面写、写完整：不要用'一夜过去''次日清晨'跳过，不要淡化处理\n"
            "- 允许并鼓励：身体接触的温度与触感、亲吻与抚摸的推进过程、衣物的变化、呼吸与声音、欲望的张力\n"
            "- 具体到动作和细节：从哪个动作开始、手落在哪里、感官如何变化——画面感来自细节\n"
            "- 保持文学性：用意象与动作写身体语言，浓而不腻、露而不俗\n"
            "- 所有角色均为成年人\n"
        )


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

            # 流派/风格提前定义（供 playbook/声音注入使用，v2.6: 修复 UnboundLocalError）
            genre = plan.get("genre", "玄幻")
            style = plan.get("style", "热血爽文")

            # 构建完整写作上下文（常规Writer用的五层上下文）
            chapter_context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)
            
            # v2.4.7: 注入用户需求（章节正文此前完全丢失需求传导）
            try:
                _req_ctx = self.build_requirements_instruction(novel_id, chapter_num, plan)
                if _req_ctx:
                    chapter_context = chapter_context + "\n\n" + _req_ctx
            except Exception as rce:
                log.warning(f"Requirements injection skipped (non-fatal): {rce}")

            # v2.6: 注入成熟题材指令（用户需求含尺度关键词时生效）
            try:
                _scale_ctx = self._build_scale_instruction(novel_id, plan)
                if _scale_ctx:
                    chapter_context = chapter_context + "\n\n" + _scale_ctx
            except Exception as _sce:
                log.warning(f"Scale instruction injection skipped (atomic, non-fatal): {_sce}")

            # v2.51: 注入角色当前状态
            char_ctx3 = self.context_updater.get_context_for_writer(novel_id, chapter_num, self.memory)
            if char_ctx3:
                chapter_context = chapter_context + "\n\n" + char_ctx3

            # v2.3.6: 注入流派黄金法则（与普通路径一致）
            try:
                from ..genre_playbooks import build_playbook_context
                _pb_ctx = build_playbook_context(genre)
                if _pb_ctx:
                    chapter_context = chapter_context + "\n\n" + _pb_ctx
            except Exception as _gpe:
                log.warning(f"Playbook injection skipped (atomic): {_gpe}")

            # v2.3.6: 注入角色声音表（对话辨识度，解决角色同质化）
            try:
                from ..character_voices import build_voices_context
                _vctx = build_voices_context(
                    self.get_character_voices(novel_id),
                    chapter_outline.get("characters", []) or None,
                )
                if _vctx:
                    chapter_context = chapter_context + "\n\n" + _vctx
            except Exception as _vce:
                log.warning(f"Voices injection skipped (atomic): {_vce}")
            
            # 用LLM生成300字叙事蓝图
            blueprint = ""
            try:
                # v2.36: 提取写作指令放到蓝图prompt最前面，否则被chapter_context[:2000]截断
                outline_instr = ""
                instr_marker = "═══ 以下为写作元指令"
                if instr_marker in chapter_context:
                    instr_start = chapter_context.find(instr_marker)
                    # 找到写作指令的结束（下一个 ## section 或文件末尾）
                    remaining = chapter_context[instr_start:]
                    instr_end_marker = remaining.find("\n\n## ")
                    if instr_end_marker > 0:
                        outline_instr = remaining[:instr_end_marker].strip()
                    else:
                        outline_instr = remaining[:800].strip()
                    # AUDIT P0-1: 剥离 ═══ 标记行
                    outline_instr = "\n".join(
                        ln for ln in outline_instr.splitlines() if "═══" not in ln
                    ).strip()
                
                blueprint_prompt = f"""根据以下写作指令和大纲，写出本章的叙事蓝图。蓝图是一段200-300字的连贯叙事概要，严格遵循写作指令中的核心事件和章末钩子。

{outline_instr}

---
{chapter_context[:1500]}

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
                from ..arcplanner import is_arc_climax
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
            # (genre/style 已在 Phase 0 定义)
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
                blueprint=blueprint, chapter_context=chapter_context  # v2.35: 全文传递，由beat prompt内部提取
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
                # v3.6.6: 逐 beat 流式输出正文——beat 完成即推送给前端（打字机效果），
                # 装配/规范化后由 done 事件触发前端拉取保存版替换，二者不冲突
                if beat_result.get("text"):
                    yield {"type": "text", "content": beat_result["text"] + "\n\n"}
            
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
            
            # AUDIT P0-1: 指令泄漏检测（原子路径）
            full_text, _leak_count = self._strip_instruction_leaks(full_text)
            if _leak_count:
                log.warning(f"Instruction leak stripped (atomic) Ch{chapter_num}: {_leak_count} lines")
                formatted = f"# 第{chapter_num}章 {chapter_outline.get('title', f'第{chapter_num}章')}\n\n{full_text}"

            # ── v2.4.1: 段落规范化安全网 ──
            try:
                from core.shared_memory import normalize_chapter_paragraphs
                before_lines = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                full_text = normalize_chapter_paragraphs(full_text)
                # v2.11: 确保结尾完整句子
                from ..writer import _ensure_complete_ending
                full_text = _ensure_complete_ending(full_text)
                formatted = f"# 第{chapter_num}章 {chapter_outline.get('title', f'第{chapter_num}章')}\n\n{full_text}"
                after_lines = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                if before_lines != after_lines:
                    log.info(f"Atomic paragraph normalize: ≤10char lines reduced {before_lines}→{after_lines}")
            except Exception as e:
                log.warning(f"Atomic paragraph normalize skipped: {e}")
            
            # ── Phase 4: 保存 ──
            self.memory.save_chapter(novel_id, chapter_num, formatted)
            
            # v2.40: 第一章生成后自动检测叙事人称
            if chapter_num == 1:
                self._save_narrative_pov(novel_id, full_text)

            # v2.42: 提取角色状态变化（原子路径）
            try:
                from ..character_state import CharacterStateTracker
                tracker = CharacterStateTracker(self.client, self.model, self.memory)
                asyncio.ensure_future(tracker.update_from_chapter(
                    novel_id, chapter_num, full_text
                ))
            except Exception as e:
                log.warning(f"Character state extraction failed (non-fatal): {e}")

            # ── 完整度验证 ──
            from ..writer import _check_truncation, _dedup_continuation
            is_trunc, reason = _check_truncation(full_text, chapter_outline.get("target_words", 2000))
            if is_trunc:
                log.warning(f"Atomic chapter {chapter_num} may be incomplete: {reason}")
                yield {"type": "warning", "message": f"⚠️ 原子模式生成的本章可能不完整（{reason}），建议使用常规模式重新生成此章"}
            
            # ── v2.10: 提取章节桥接数据（v2.15: 使用韧性客户端）──
            try:
                bridge = await asyncio.to_thread(
                    self.memory.extract_bridge_from_chapter,
                    full_text, chapter_num, chapter_outline,
                    client=self._resilient, model=self.model,
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
            
            # ── done 事件（正文已逐 beat 流式输出过，这里不再重复 yield full_text，
            #    避免前端 writing.content 收到「逐beat文本 + 全文」两遍重复）──
            yield {"type": "done", "content": formatted, "chapter_num": chapter_num,
                   "atomic": True, "beat_count": len(beats)}
            
            # 更新校验
            try:
                from ..storygraph import StoryGraph, extract_storygraph_from_chapter, apply_extraction
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


    async def generate_chapter_stream(
        self, novel_id: str, chapter_num: int, writing_mode: str = "webnovel",
        feedback: str = None, batch_mode: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """流式生成章节 — 前端可实时显示打字效果
        
        Args:
            feedback: 用户修改意见（用于重生成，不改大纲结构）
            batch_mode: v2.6 批量模式 — 速度优先：
                - Humanizer 质量阈值 70→60，改写后跳过再检测（省1-2次LLM）
                - 一致性校验只跑 L1 规则（省1次LLM）
                - 桥接/ContextUpdater/storygraph 提取后台化（省3次关键路径LLM）
                单章模式保持全量校验（质量优先）
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
                # AUDIT P1-5: 兜底基于前文自动推断，不再是「继续推进主线剧情发展」空壳
                _fb_summary = "推进主线剧情：主角直面新的挑战，冲突升级，并自然收束本章。"
                _fb_hook = "章末局势急转直下，留下新的悬念。"
                try:
                    if chapter_num > 1:
                        _prev_text = self.memory.read_chapter(novel_id, chapter_num - 1)
                        if _prev_text:
                            _prev_tail = _prev_text.strip()[-300:]
                            _prev_lines = [l for l in _prev_tail.splitlines() if l.strip()]
                            _prev_last = (_prev_lines[-1] if _prev_lines else _prev_tail)[:80]
                            _fb_summary = (f"紧接上一章结尾继续：{_prev_last}。"
                                           f"在此基础上推进核心矛盾、展开新冲突，并自然收束本章。")
                            _fb_hook = f"围绕上一章结尾「{_prev_last[-40:]}」的悬念展开，章末制造新的反转钩子。"
                except Exception:
                    pass
                chapter_outline = {
                    "number": chapter_num,
                    "title": f"第{chapter_num}章",
                    "summary": _fb_summary,
                    "emotion_curve": "平稳→紧张→悬念",
                    "characters": ["主角"],
                    "hook": _fb_hook,
                    "target_words": config.DEFAULT_CHAPTER_WORDS,
                }

            # 组装上下文
            context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)

            # v2.4.7: 注入用户需求（章节正文此前完全丢失需求传导）
            try:
                req_ctx = self.build_requirements_instruction(novel_id, chapter_num, plan)
                if req_ctx:
                    context = context + "\n\n" + req_ctx
            except Exception as rce:
                log.warning(f"Requirements injection skipped (non-fatal): {rce}")

            # v2.6: 注入成熟题材指令（用户需求含尺度关键词时生效）
            try:
                scale_ctx = self._build_scale_instruction(novel_id, plan)
                if scale_ctx:
                    context = context + "\n\n" + scale_ctx
            except Exception as sce:
                log.warning(f"Scale instruction injection skipped (non-fatal): {sce}")

            # v2.51: 注入角色当前状态 — 写作前读取每个角色的位置/情绪/健康/目标
            char_ctx = self.context_updater.get_context_for_writer(novel_id, chapter_num, self.memory)
            if char_ctx:
                context = context + "\n\n" + char_ctx

            # 获取创作参数（提前定义，供 playbook/角色声音等注入使用）
            genre = plan.get("genre", "玄幻")
            style = plan.get("style", "热血爽文")

            # v2.3.5: 注入用户偏好指令（反馈闭环，≥3 条反馈才生效）
            try:
                pref_ctx = self.build_preference_instruction(novel_id)
                if pref_ctx:
                    context = context + "\n\n" + pref_ctx
            except Exception as pe:
                log.warning(f"Preference injection skipped: {pe}")

            # v2.3.6: 注入流派黄金法则（网文技法研究提炼）
            try:
                from ..genre_playbooks import build_playbook_context
                pb_ctx = build_playbook_context(genre)
                if pb_ctx:
                    context = context + "\n\n" + pb_ctx
            except Exception as gpe:
                log.warning(f"Playbook injection skipped: {gpe}")

            # v2.3.6: 注入角色声音表（对话辨识度，解决角色同质化）
            try:
                from ..character_voices import build_voices_context
                vctx = build_voices_context(
                    self.get_character_voices(novel_id),
                    chapter_outline.get("characters", []) or None,
                )
                if vctx:
                    context = context + "\n\n" + vctx
            except Exception as vce:
                log.warning(f"Voices injection skipped: {vce}")
            log.info(f"Character state injected into writer context ({len(char_ctx)} chars)")

            # 方案C: 在弧高潮章自动注入反转设计
            try:
                sg_path = os.path.join(self.memory.get_novel_dir(novel_id), "storygraph.json")
                if os.path.exists(sg_path):
                    sg_data = safe_read_json(sg_path)
                    if sg_data and sg_data.get("arcs"):
                        from ..arcplanner import is_arc_climax
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

            # 获取创作参数（genre/style 已在函数前部定义）
            target_words = chapter_outline.get("target_words", config.DEFAULT_CHAPTER_WORDS)
            # v2.7: 快餐模式字数自适应 — 2500字/章(短剧化节奏)
            if plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False):
                target_words = 2500

            # AUDIT P1-1: 普通 Writer 注入节拍骨架 — 与原子化路径共享节奏，
            # 让单次生成也按 beat 推进，而非只靠 outline summary 一句话
            try:
                _beats = self._decompose_chapter_beats(
                    chapter_outline, chapter_num,
                    self.memory.get_novel_dir(novel_id),
                    is_first=(chapter_num == 1),
                )
                if _beats:
                    context += "\n\n" + self._format_beats_instruction(_beats)
                    log.info(f"Beats injected into writer context (Ch{chapter_num}, {len(_beats)} beats)")
            except Exception as _be:
                log.warning(f"Beat injection skipped (non-fatal): {_be}")

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

            # AUDIT P0-1: 指令泄漏检测 — 正文中不得出现元指令标记
            full_text, _leak_count = self._strip_instruction_leaks(full_text)
            if _leak_count:
                log.warning(f"Instruction leak stripped from Ch{chapter_num}: {_leak_count} lines")
                yield {"type": "warning", "message": "已清理正文中的指令泄漏文本"}

            # ── v2.27: 先跑质量门（本地规则，毫秒级），用结果控制后续处理 ──
            quality_report = None
            try:
                is_fast_food = plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False)
                checker = PacingChecker(self.client, self.model)
                qr = checker.quick_quality_check(full_text, fast_food=is_fast_food)
                quality_report = qr
                log.info(f"Quality gate: score={qr['score']}, issues={len(qr['issues'])}")

                if qr["score"] < 40 and qr["issues"]:
                    issues_text = "; ".join(qr["issues"])
                    log.warning(f"Quality gate CRITICAL (score={qr['score']}): {issues_text}")
                    _before_text = full_text

                    # AUDIT P0-2: LLM 复核防误杀 — 规则初筛命中后由 LLM 确认是否真需重写
                    llm_confirm = None
                    if not batch_mode:
                        try:
                            _resp = self.client.chat.completions.create(
                                model=self.model,
                                messages=[
                                    {"role": "system", "content": "你是一位严格的网络小说编辑。判断规则引擎的检测结果是否属实、文本是否真的需要重写。只输出JSON。"},
                                    {"role": "user", "content": (
                                        f"规则引擎认为本章存在以下问题：{issues_text}\n\n"
                                        f"章节开头1200字：\n{full_text[:1200]}\n\n"
                                        '请判断：这些问题是否真实存在且严重影响阅读？如果问题被夸大，'
                                        '或属于正常文风（例如爽文打脸场景节奏快、对话多是优点），请给高分并 needs_rewrite=false。'
                                        '只输出JSON: {"score": 0-100 可接受度评分, "needs_rewrite": true/false, "reason": "一句话理由"}'
                                    )},
                                ],
                                temperature=0.2,
                                max_tokens=250,
                            )
                            _content = (_resp.choices[0].message.content or "").strip()
                            try:
                                llm_confirm = json.loads(_content)
                            except Exception:
                                _mj = re.search(r"\{.*\}", _content, re.S)
                                llm_confirm = json.loads(_mj.group(0)) if _mj else None
                        except Exception as _ce:
                            log.warning(f"Quality gate LLM confirm failed, default to rewrite: {_ce}")

                    _llm_accept = False
                    _llm_score = 0
                    if llm_confirm and isinstance(llm_confirm, dict):
                        try:
                            _llm_score = float(llm_confirm.get("score", 0))
                        except (TypeError, ValueError):
                            _llm_score = 0
                        _llm_accept = (llm_confirm.get("needs_rewrite") is False and _llm_score >= 55)

                    if _llm_accept:
                        log.info(f"Quality gate LLM override: 保留原文 (llm_score={_llm_score})")
                        quality_report = qr
                        yield {"type": "quality_minor", "score": qr["score"], "issues": qr["issues"],
                               "note": f"LLM复核通过，保留原文（{llm_confirm.get('reason', '')}）"}
                    else:
                        yield {"type": "quality_warning", "score": qr["score"], "issues": qr["issues"],
                               "message": f"📝 严重质量瑕疵（评分 {qr['score']}），自动续写优化..."}
                        yield {"type": "status", "message": "✍️ 质量门未过，正在优化改写（约1分钟）…"}

                        # v2.49: 用 multi-turn history 续写修复，而不是从头重写
                        retry_text = ""
                        async for text in self.writer.write_stream(
                            context=context + f"\n\n⚠️ 上一版质量不合格（评分{qr['score']}）。以下问题必须修正：{issues_text}\n\n【已生成内容参考】\n{full_text[-500:]}",
                            genre=genre, style=style, target_words=target_words, writing_mode=writing_mode,
                            normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False),
                            fast_food=is_fast_food,
                            chapter_outline=chapter_outline, skip_ending=True,
                        ):
                            retry_text += text

                        if retry_text and len(retry_text) > len(full_text) * 0.6:
                            qr2 = checker.quick_quality_check(retry_text, fast_food=is_fast_food)
                            if qr2["score"] > qr["score"] + 10 or qr2["score"] >= 60:
                                full_text = retry_text
                                quality_report = qr2
                                log.info(f"Quality gate retry PASSED: {qr['score']} → {qr2['score']}")
                                yield {"type": "quality_retry", "score_before": qr["score"], "score_after": qr2["score"]}
                            else:
                                log.warning("Quality gate retry no improvement, keeping original")
                        else:
                            log.warning("Quality gate retry text too short, keeping original")

                    # AUDIT P0-2: 误杀率数据采集（积累 before/after 数据集供阈值调优）
                    try:
                        _qlog = os.path.join(self.memory.get_novel_dir(novel_id), "quality_gate_log.jsonl")
                        with open(_qlog, "a", encoding="utf-8") as _qf:
                            _qf.write(json.dumps({
                                "ts": time.time(),
                                "novel": novel_id,
                                "chapter": chapter_num,
                                "score_before": qr["score"],
                                "score_after": quality_report.get("score") if quality_report else None,
                                "rewritten": full_text != _before_text,
                                "llm_accept": _llm_accept,
                                "issues": qr["issues"][:5],
                            }, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                elif 40 <= qr["score"] < 60 and qr["issues"]:
                    log.info(f"Quality gate MILD (score={qr['score']}): {len(qr['issues'])} minor issues, accepting as-is")
                    yield {"type": "quality_minor", "score": qr["score"], "issues": qr["issues"]}
                    
            except Exception as e:
                log.warning(f"Quality gate skipped: {e}")

            # ── v2.27: Humanizer 移到质量门之后 — 用分数决定是否跑 ──
            ai_report = None
            # v2.6: 批量模式阈值 70→60（更多章节直接通过，跳过 humanizer 2-3次LLM）
            _quality_ok = quality_report and quality_report.get("score", 0) >= (60 if batch_mode else 70)  # v2.3.5: 50→70 提高润色覆盖
            if not _quality_ok:
                yield {"type": "status", "message": "🎨 正在消除 AI 痕迹、润色文笔…"}
                try:
                    from ..ai_detector import AIDetector, HumanRewriter, humanize_pipeline
                    detector = AIDetector(self.client, self.model)
                    rewriter = HumanRewriter(self.client, self.model)
                    
                    chapter_summary = chapter_outline.get("summary", "") or chapter_outline.get("title", "")
                    _ending_saved = _extract_key_ending(full_text)
                    result = await asyncio.to_thread(
                        humanize_pipeline, full_text, detector, rewriter,
                        scene_desc=chapter_summary,
                        target_length=target_words,
                        min_score_threshold=(50 if batch_mode else 40),  # v2.6: 批量模式更宽松，AI味<50不改写
                        skip_verify=batch_mode,  # v2.6: 批量模式跳过改写后再检测
                    )
                    if result["rewritten"]:
                        ai_report = result
                        rewritten = result["text"]
                        if len(rewritten) > 500 and _ending_saved:
                            protected = _protect_ending_semantic(full_text, rewritten, _ending_saved)
                            full_text = protected if protected != rewritten else rewritten
                        else:
                            full_text = rewritten
                        log.info(f"AI Humanizer: score {ai_report['ai_score_before']}→{ai_report.get('ai_score_after','?')}, rewritten")
                        yield {"type": "ai_report", 
                               "score_before": ai_report["ai_score_before"],
                               "score_after": ai_report.get("ai_score_after", ai_report["ai_score_before"]),
                               "rewritten": True}
                except Exception as e:
                    log.warning(f"AI Humanizer skipped: {e}")
            else:
                log.info(f"AI Humanizer skipped: quality score={quality_report['score']}≥50, text already human-like")

            # ── v2.11: 确保结尾是完整句子 ──
            from ..writer import _ensure_complete_ending
            original_len = len(full_text)
            full_text = _ensure_complete_ending(full_text)
            if len(full_text) != original_len:
                log.info(f"Ending trimmed from {original_len} to {len(full_text)} chars (removed incomplete sentence)")

            # ── v2.31: 后处理清除AI惯用语（prompt拦不住的硬过滤）──
            _ai_replacements = {
                '嘴角勾起一抹诡异的弧度': '冷冷一笑',
                '嘴角勾起一抹冷笑': '冷笑一声',
                '嘴角勾起一抹弧度': '笑了笑',
                '嘴角勾起': '嘴角微动',
                '瞳孔骤缩': '眼神一凝',
                '瞳孔猛地一缩': '目光一沉',
                '浑身一震': '身体一僵',
                '脊背发凉': '汗毛倒竖',
                '后背一凉': '汗毛倒竖',
                '倒吸一口凉气': '呼吸一紧',
                '眼底闪过一丝寒光': '眼神冷下来',
                '眼底闪过一丝': '眼神中透着',
                '眸光': '目光',
                '眸色': '眼神',
                '眸子': '眼睛',
            }
            _replaced_count = 0
            for old, new in _ai_replacements.items():
                if old in full_text:
                    full_text = full_text.replace(old, new)
                    _replaced_count += 1
            if _replaced_count:
                log.info(f"AI cliché filter: {_replaced_count} replacements")

            # ── v2.4.1: 段落规范化安全网（必须在 Humanizer 和质量门之后执行）──
            try:
                from core.shared_memory import normalize_chapter_paragraphs
                before_short = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                full_text = normalize_chapter_paragraphs(full_text)
                after_short = len([l for l in full_text.split('\n') if l.strip() and len(l.strip()) <= 10])
                if before_short != after_short:
                    log.info(f"Paragraph normalize: short fragments {before_short}→{after_short}")
            except Exception as e:
                log.warning(f"Paragraph normalize skipped: {e}")

            # ── v3.8 (AUDIT P1-3): 章末钩子 LLM 判断 — 规则初筛未通过时确认并局部强化 ──
            if not batch_mode and len(full_text) >= 1500:
                try:
                    _hook_kws = ["突然", "忽然", "那一刻", "奇怪", "竟然", "没想到", "却见", "就在这时", "……", "？", "?"]
                    if not any(k in full_text[-100:] for k in _hook_kws):
                        _hook_res = self.writer.assess_and_enhance_hook(full_text, min_rewrite_score=35)
                        if _hook_res.get("rewritten") and _hook_res.get("text"):
                            full_text = _hook_res["text"]
                            log.info(f"Hook enhanced for Ch{chapter_num} (score={_hook_res.get('score')})")
                            yield {"type": "hook_enhanced", "score": _hook_res.get("score"),
                                   "message": "🔗 章末钩子偏弱，已自动强化结尾悬念"}
                        elif _hook_res.get("assessed"):
                            log.info(f"Hook assessment: score={_hook_res.get('score')}, no rewrite needed")
                except Exception as _he:
                    log.warning(f"Hook assessment skipped (non-fatal): {_he}")

            # 最终保存章节（覆盖增量保存的临时文件）
            # v2.31: 去除 Writer 自动生成的所有标题行（# 开头且含章节号），防止多标题
            _text_to_save = full_text
            _lines = _text_to_save.split('\n')
            while _lines and _lines[0].strip().startswith('#') and (
                str(chapter_num) in _lines[0] or
                any(w in _lines[0] for w in ['第一章','第二章','第三章','第四章','第五章','第六章','第七章','第八章','第九章','第十章',
                                              '第一回','第二回','第三回','第四回','第五回','第六回','第七回','第八回','第九回','第十回'])
            ):
                _lines.pop(0)
                # 跳过标题后的空行
                while _lines and not _lines[0].strip():
                    _lines.pop(0)
            _text_to_save = '\n'.join(_lines)
            formatted = f"# 第{chapter_num}章 {chapter_title}\n\n{_text_to_save}"
            self.memory.save_chapter(novel_id, chapter_num, formatted)

            # v2.40: 第一章生成后自动检测叙事人称
            if chapter_num == 1:
                self._save_narrative_pov(novel_id, full_text)

            # v2.42: 提取角色状态变化
            try:
                from ..character_state import CharacterStateTracker
                tracker = CharacterStateTracker(self.client, self.model, self.memory)
                asyncio.ensure_future(tracker.update_from_chapter(
                    novel_id, chapter_num, full_text
                ))
            except Exception as e:
                log.warning(f"Character state extraction failed (non-fatal): {e}")

            # ── v2.10: 提取章节桥接数据 → 保证下章接续（v2.15: 使用韧性客户端）──
            # v2.6: 批量模式后台化（流水线：下一章启动前 await_pending_bridge 衔接）
            try:
                def _extract_bridge():
                    bridge = self.memory.extract_bridge_from_chapter(
                        full_text, chapter_num, chapter_outline,
                        client=self._resilient, model=self.model,
                    )
                    if bridge:
                        self.memory.save_bridge(novel_id, chapter_num, bridge)
                        log.info(f"ChapterBridge saved for chapter {chapter_num}: "
                                f"next_beat={bridge.get('next_beat','')[:60]}...")
                    else:
                        log.warning(f"ChapterBridge extraction returned None for chapter {chapter_num}")
                    return bridge

                if batch_mode:
                    _bkey = (novel_id, chapter_num)
                    _btask = asyncio.ensure_future(asyncio.to_thread(_extract_bridge))
                    self._pending_bridges[_bkey] = _btask
                    # 任务完成后自动清理（防止未 await 时残留）
                    _btask.add_done_callback(lambda _fut, _k=_bkey: self._pending_bridges.pop(_k, None))
                else:
                    _extract_bridge()
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

            # 一致性校验用上下文（前文章节 + 全局状态）
            prev_chapters_ctx = {}
            for _pc in completed:
                if _pc < chapter_num:
                    _pc_text = self.get_chapter(novel_id, _pc)
                    if _pc_text:
                        prev_chapters_ctx[_pc] = _pc_text
            _gs_path = os.path.join(novel_dir, "global_state.json")
            gs_ctx = safe_read_json(_gs_path, {}) if os.path.exists(_gs_path) else {}
            
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
                await asyncio.sleep(0.1 * (retry + 1))
            
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

            yield {"type": "status", "message": "💾 正在保存章节、更新伏笔与剧情图谱…"}
            log.info(f"Chapter {chapter_num} saved: {len(full_text)} chars")

            # ── v2.3.5: 一致性校验（L1 规则 + L2 LLM 语义，P0 自动重写）──
            try:
                yield {"type": "status", "message": "🔍 正在校验逻辑一致性（亲属关系/时空/设定）…"}
                cv_result = self.consistency_validator.validate_chapter(
                    chapter_text=full_text,
                    chapter_num=chapter_num,
                    plan=plan,
                    prev_chapters=prev_chapters_ctx,
                    global_state=gs_ctx,
                    run_deep=not batch_mode,  # v2.6: 批量模式只跑 L1 规则（省1次LLM），单章保持 L1+L2
                )
                all_v = cv_result.get("violations", [])
                p0_v = [v for v in all_v if v.get("severity") == "P0"]
                p1_v = [v for v in all_v if v.get("severity") == "P1"]

                # P0 致命错误 → 自动重写本章
                if p0_v:
                    yield {"type": "status", "message": f"🔧 检测到 {len(p0_v)} 处逻辑硬伤，正在自动重写…"}
                    log.warning(f"P0 issues Ch{chapter_num}: {[v.get('description','')[:50] for v in p0_v[:3]]}")
                    try:
                        fix_prompt = self.consistency_validator.build_fix_prompt(p0_v + p1_v[:3])
                        rewritten = ""
                        _nl2 = chr(10) + chr(10)
                        async for text in self.writer.write_stream(
                            context=context + _nl2 + fix_prompt,
                            genre=genre, style=style, target_words=target_words,
                            writing_mode=writing_mode, chapter_outline=chapter_outline,
                            skip_ending=True,
                        ):
                            rewritten += text
                        if rewritten and len(rewritten) > len(full_text) * 0.6:
                            full_text = rewritten
                            self.memory.save_chapter(novel_id, chapter_num, full_text)
                            self.memory.invalidate("state", novel_id)
                            yield {"type": "consistency_fixed", "chapter": chapter_num,
                                   "fixed": [v.get("description", "")[:80] for v in p0_v[:3]]}
                            log.info(f"Ch{chapter_num} auto-rewritten for {len(p0_v)} P0 issues")
                        else:
                            log.warning(f"Rewrite for Ch{chapter_num} too short, keeping original")
                    except Exception as rw_e:
                        log.warning(f"Auto-rewrite failed: {rw_e}")

                # P1 问题 → 注入下一章修正
                if p1_v:
                    issues = [v.get("description", "") for v in p1_v[:5]]
                    state["consistency_issues"] = state.get("consistency_issues", {})
                    state["consistency_issues"][str(chapter_num)] = issues
                    self.memory.save_novel_state(novel_id, state)
                    yield {"type": "consistency_warning", "chapter": chapter_num, "issues": issues}
                    log.warning(f"Consistency P1 issues Ch{chapter_num}: {len(issues)}")
            except Exception as cv_e:
                log.warning(f"Consistency check skipped: {cv_e}")

            # ── 完整度验证 ──
            from ..writer import _check_truncation, _dedup_continuation
            is_trunc, reason = _check_truncation(full_text, target_words)
            if is_trunc:
                log.warning(f"Chapter {chapter_num} incomplete after Writer retries: {reason}. "
                           f"Engine fallback: continuing from breakpoint...")
                yield {"type": "warning", "message": f"⏳ 本章内容不完整（{reason}），正在自动补全内容..."}
                
                # v2.49: Engine-level retry — 从断点续写，不重写
                try:
                    retry_context = self.memory.build_writer_context(novel_id, chapter_num, chapter_outline)
                    # v2.4.7: 注入用户需求（重写/续写同样需要）
                    try:
                        _rreq = self.build_requirements_instruction(novel_id, chapter_num, plan)
                        if _rreq:
                            retry_context = retry_context + "\n\n" + _rreq
                    except Exception as _rre:
                        pass
                    # v2.51: 注入角色状态
                    char_ctx2 = self.context_updater.get_context_for_writer(novel_id, chapter_num, self.memory)
                    if char_ctx2:
                        retry_context = retry_context + "\n\n" + char_ctx2
                    # 注入已生成内容作为参考
                    retry_context = (
                        f"## 📝 已生成草稿（请从断点继续，不要重复）\n\n"
                        f"以下是已经写完的内容，字数约{len(full_text)//2}字，请从断点处直接续写：\n\n"
                        f"{full_text[-800:]}\n\n"
                        f"---\n\n"
                        f"⚠️ 继续写的要求：1) 不要重复上述内容 2) 从断点直接衔接 3) 补足到{target_words}字\n\n"
                        f"{retry_context}"
                    )
                    retry_text = ""
                    async for text in self.writer.write_stream(
                        context=retry_context,
                        genre=genre,
                        style=style,
                        target_words=max(target_words, int(len(full_text) * 1.5 / 2)),
                        writing_mode=writing_mode,
                        normal_pacing=plan.get("_meta", {}).get("creative_input", {}).get("normal_pacing", False),
                        fast_food=plan.get("_meta", {}).get("creative_input", {}).get("fast_food", False),
                        chapter_outline=chapter_outline,
                        skip_ending=True,
                    ):
                        retry_text += text
                        yield {"type": "text", "content": text}
                    
                    if retry_text:
                        # v2.49: 智能去重拼接
                        cleaned = _dedup_continuation(full_text, retry_text)
                        candidate = full_text + cleaned
                        is_trunc2, reason2 = _check_truncation(candidate, target_words)
                        if not is_trunc2 or len(candidate) > len(full_text):
                            full_text = candidate
                            log.info(f"Engine continuation OK: +{len(cleaned)} → {len(full_text)} chars")
                            yield {"type": "status", "message": "补全完成"}
                        else:
                            log.warning(f"Engine continuation no improvement")
                    else:
                        log.warning(f"Engine continuation empty, keeping original")
                except Exception as re:
                    log.warning(f"Engine retry failed: {re}, keeping original")

            # ── 自动执行 ContextUpdater: 更新全局角色状态 ──
            # v2.6: 批量模式后台化（不阻塞正文流程）；单章模式保持同步
            try:
                novel_dir = self.memory.get_novel_dir(novel_id)
                state_path = os.path.join(novel_dir, "global_state.json")
                current_state = {}
                if os.path.exists(state_path):
                    current_state = safe_read_json(state_path)

                def _ctx_update():
                    new_state = self.context_updater.update(novel_id, chapter_num, full_text, current_state)
                    atomic_write_json(state_path, new_state)
                    log.info(f"ContextUpdater: state updated after chapter {chapter_num}")

                if batch_mode:
                    asyncio.ensure_future(asyncio.to_thread(_ctx_update))
                else:
                    _ctx_update()
            except Exception as e:
                log.warning(f"ContextUpdater skipped: {e}")

            # ── 自动更新剧情图谱（storygraph）──
            # v2.6: 批量模式后台化（不阻塞正文流程）；单章模式保持同步
            try:
                novel_dir = self.memory.get_novel_dir(novel_id)
                sg_path = os.path.join(novel_dir, "storygraph.json")
                sg_data = safe_read_json(sg_path) or {}

                from ..storygraph import StoryGraph, extract_storygraph_from_chapter, apply_extraction

                def _sg_update():
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

                if batch_mode:
                    asyncio.ensure_future(asyncio.to_thread(_sg_update))
                else:
                    _sg_update()
            except Exception as e:
                log.warning(f"StoryGraph update skipped: {e}")

            # ── 方案A: 一致性校验（ConsistencyValidator L1+L2 每章，P0 自动重写）──
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
                
                # 执行校验（v2.3.5: 统一 ConsistencyValidator，L2 每章深检，含亲属关系检测）
                # v2.6: 批量模式跳过 — 前面 v2.3.5 校验已跑 L1 规则，避免重复校验白烧 LLM
                if batch_mode:
                    validation = {"violations": [], "score": 100, "warnings": []}
                else:
                    validation = self.consistency_validator.validate_chapter(
                        chapter_text=full_text,
                        chapter_num=chapter_num,
                        plan=plan,
                        prev_chapters=prev_chapters,
                        global_state=global_state,
                        run_deep=True,  # 每章 L2 语义校验（v2.3.5: 原每3章→每章）
                    )
                
                violations = validation.get("violations", [])
                if violations:
                    p0_count = sum(1 for v in violations if v.get("severity") == "P0")
                    score = validation.get("score", 100)
                    
                    log.warning(f"ConsistencyValidator(atomic): {len(violations)} violations "
                               f"(P0:{p0_count}) score={score}")
                    
                    yield {"type": "consistency_check",
                           "violations": violations,
                           "score": score}
                    
                    # P0违规 ≥1 → 自动修复
                    if p0_count >= 1:
                        fix_prompt = self.consistency_validator.build_fix_prompt(violations)
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

                # P1 问题 → 注入下一章修正（与普通路径对齐）
                p1_issues = [v.get("description", "") for v in violations if v.get("severity") == "P1"]
                if p1_issues and not p0_count:
                    state = self.memory.get_novel_state(novel_id)
                    state["consistency_issues"] = state.get("consistency_issues", {})
                    state["consistency_issues"][str(chapter_num)] = p1_issues[:5]
                    self.memory.save_novel_state(novel_id, state)
                    yield {"type": "consistency_warning", "chapter": chapter_num, "issues": p1_issues[:5]}
            except Exception as e:
                log.warning(f"Consistency auto-validation skipped: {e}")

            # ── 自动校准（每10章）──
            try:
                from ..autocalibrator import should_calibrate, calibrate
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
            
            yield {"type": "status", "message": "💾 正在保存章节、更新伏笔与剧情图谱…"}
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

