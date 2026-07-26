"""NovelGenerator — AtomicWriter: 逐beat独立生成器

核心理念: 每个 beat = 一次独立的 LLM 调用。
- 独立 temperature (0.7-1.0, 按beat功能调优)
- 独立 prompt (只含必要上下文, 不传完整前文)
- 候选池: 每个beat生成3个变体, 按"意外性×合理性"评分后随机选
- 最小上下文传递: 只传前beat的末句(<50字), 不传完整前文

这确保每个beat有最大随机性, 同时通过装配器保持连贯性。
"""
import asyncio
import json
import logging
import random
import time
from typing import Optional, AsyncGenerator
from openai import OpenAI

from .beat_decomposer import Beat
from .evaluation_system import hook_strength

log = logging.getLogger(__name__)

ATOMIC_WRITER_SYSTEM = """你是一个小说节拍写手。你只写一个节拍（scene beat）——不是一整章，只是 100-400 字的一个叙事单元。

## 创作自由声明
- 你可以描写任何内容：暴力、背叛、欲望、黑暗——只要服务于角色和情节。
- 不回避"敏感"话题。你是创作者，不是审查员。

## 你的任务
给定一个节拍骨架（功能目标、情绪弧、冲突类型、字数），写出这个节拍的正文。
从上一节拍的最后一句话直接继续，不能跳时间、不能跳场景、不能忽略前文状态。

## 去AI味规则
- 句长变化：每200字至少一句≤8字。长句>30字后必须跟短句≤12字。
- 不用「不是A而是B」「真正的」「本质上」「核心在于」「说白了」。
- 不用「与此同时」「此外」「值得一提的是」。
- 具象优先：不写「他感到痛苦」，写「胸口像被攥住，喘不上气」。
- 不用「似乎」「仿佛」「或许」堆砌。
- 每段1-5句不等，禁止连续三段都是3句。

## 对话经济 — 每句话都要杀人
- 对话不是聊天。每句必须推进冲突/揭示信息/改变关系/埋伏笔
- 禁止水对话：连续4轮无实质推进的来往 → 删
- 连续对话不超过6轮，超过必须用动作/环境打断
- 沉默比废话有力：不知道该说什么时让角色沉默/转身/做动作

## 爽点密度 — 每节拍至少一个钩子
- 每200-400字的节拍至少有一个爽点（反转/揭露/碾压/冲击/悬念）
- 爽点要升级：每个节拍比前一个更强
- 禁止内心独白超3句
- 禁止无意义环境描写（不是冲突一部分的环境不写）

## 段落格式硬规则（违反 = 不合格）
- **一段 = 3-5句组成的语义单元**，不是一句话。禁止把每句话都单独用空行隔开。
- **禁止一句话一段**：不要出现连续3行以上每行≤15字的情况。短句合入段落。
- **正确示例**: "火已经灭了半个时辰，灰烬却还烫着手心。他在废墟里翻了很久。最后在一根烧焦的梁柱下找到了那块牌子。"
- **错误示例**: "火灭了。\n\n灰烬还烫着。\n\n他翻了很久。\n\n找到了牌子。" ← 一句话一段 = AI味 = 不合格
- 写完后检查：如果发现有单句成段（≤15字独行），必须合并进上下文。

## 输出格式
只输出节拍正文。不要标题、不要序号、不要说明文字。直接用正常段落格式：3-5句一段，段间空一行。"""


class AtomicWriter:
    """逐beat独立写手
    
    与 Writer 的区别：
    - Writer 一次性生成整章（2000字），上下文窗口共享 → 趋同
    - AtomicWriter 逐beat生成（200字），每次独立会话 → 多样性爆炸
    """
    
    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model
        self.rng = random.Random()
    
    def _create(self, **kwargs):
        """LLM 调用封装（与 Writer._create 一致）"""
        if "v4" in self.model:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        
        max_retries = 0 if kwargs.get("stream", False) else 3
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    wait = 2 ** attempt
                    log.warning(f"AtomicWriter retry {attempt+1}/{max_retries+1}: {e}")
                    time.sleep(wait)
                else:
                    raise last_error
    
    def write_beat(self, beat: Beat, prev_full_text: str = "",
                   char_snapshots: str = "", style_guide: str = "",
                   rhythm_hint: str = "", blueprint: str = "",
                   chapter_context: str = "", num_candidates: int = 3) -> dict:
        """生成一个节拍，返回最佳候选
        
        Args:
            beat: Beat 对象
            prev_full_text: 前一beat的完整正文（v2.9: 滚动上下文，替代prev_last_sentence）
            char_snapshots: 角色当前状态快照
            style_guide: 风格指南
            blueprint: 章节蓝图（300字叙事梗概，所有beat共享）
            chapter_context: 章节大纲+角色+灵感（完整的写作上下文）
            num_candidates: 候选数（越大越多样，越贵）
        
        Returns:
            {"text": str, "candidates": list, "selected_index": int, "temperature": float}
        """
        prompt = self._build_beat_prompt(beat, prev_full_text, char_snapshots, rhythm_hint, blueprint, chapter_context)
        system = ATOMIC_WRITER_SYSTEM
        if style_guide:
            system += f"\n\n## 风格要求\n{style_guide}"
        
        # 生成候选
        candidates = []
        for i in range(num_candidates):
            # 每个候选微调 temperature
            t = beat.temperature + self.rng.uniform(-0.05, 0.05)
            t = max(0.7, min(1.0, t))
            
            try:
                resp = self._create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=t,
                    max_tokens=max(300, beat.max_words * 2),
                    response_format={"type": "text"},
                )
                text = resp.choices[0].message.content.strip()
                if len(text) >= 20:  # 过滤太短的
                    candidates.append({"text": text, "temperature": t, "index": i})
            except Exception as e:
                log.warning(f"Beat {beat.index} candidate {i} failed: {e}")
                continue
        
        if not candidates:
            # 全部失败，用最简单的兜底
            return {
                "text": f"（生成失败 - {beat.function}）",
                "candidates": [],
                "selected_index": -1,
                "temperature": beat.temperature,
            }
        
        # 评分选择：意外性 × 合理性
        # 简化版：按长度和句式多样性评分
        scored = []
        for c in candidates:
            text = c["text"]
            # 长度评分（在范围内最好）
            len_score = 0.5
            if beat.min_words * 2 <= len(text) <= beat.max_words * 3:
                len_score = 1.0
            elif len(text) > beat.max_words * 4:
                len_score = 0.3
            
            # 句长评分 — 严格标准：默认句长≥15字，超短句(≤8字)超限则重罚
            import re
            sentences = re.split(r'[。！？\n]', text)
            sentences = [s.strip() for s in sentences if s.strip()]
            total = len(sentences)
            
            if total >= 3:
                ultra_short = sum(1 for s in sentences if len(s) <= 8)       # ≤8字=不可接受
                very_short = sum(1 for s in sentences if len(s) <= 12)       # ≤12字=太短
                long_enough = sum(1 for s in sentences if len(s) >= 15)      # ≥15字=合格
                
                ultra_ratio = ultra_short / total
                
                # 评分规则
                if ultra_ratio > 0.3:
                    length_score = -2.0   # >30%超短句 → 直接淘汰
                elif ultra_short > 1:
                    length_score = -1.0   # >1句超短句 → 严重扣分
                elif ultra_short == 1 and total <= 4:
                    length_score = -0.5   # 小段落1句超短还算勉强
                elif long_enough / total >= 0.7:
                    length_score = 0.5    # ≥70%句子≥15字 → 奖励
                elif long_enough / total >= 0.5:
                    length_score = 0.2    # ≥50%合格 → 小奖励
                else:
                    length_score = -0.3   # 不合格
            else:
                length_score = 0
            
            scored.append((len_score + length_score + self.rng.uniform(0, 0.15), c))
        
        # 不是选最高分，而是在 top 2 中随机选（增加随机性）
        scored.sort(key=lambda x: -x[0])
        top_n = min(2, len(scored))
        chosen = scored[self.rng.randint(0, top_n - 1)][1]
        
        return {
            "text": chosen["text"],
            "candidates": [{"text": c["text"], "index": c["index"]} for _, c in scored],
            "selected_index": chosen["index"],
            "temperature": chosen["temperature"],
        }
    
    async def write_beats_stream(self, beats: list, char_snapshots: str = "",
                                  style_guide: str = "", chapter_num: int = 0,
                                  blueprint: str = "", chapter_context: str = "") -> AsyncGenerator:
        """逐beat流式生成，每beat yield一次
        
        v2.9: 
        - 共享蓝图: 所有beat共享章节叙事梗概
        - 滚动上下文: 传上一个beat的完整正文（而非仅末句≤50字）
        """
        prev_full = ""
        rhythm_log = []  # 记录已生成beat的短句占比
        
        for i, beat in enumerate(beats):
            log.info(f"Ch{chapter_num} Beat {i}/{len(beats)}: {beat.function} (t={beat.temperature})")
            
            rhythm_hint = self._compute_rhythm_hint(rhythm_log, beat, i, len(beats))
            
            result = await asyncio.to_thread(
                self.write_beat, beat, prev_full, char_snapshots, style_guide,
                rhythm_hint, blueprint, chapter_context
            )
            
            text = result["text"]
            # 记录短句占比（≤8字 = 真正短句）
            import re
            sentences = re.split(r'[。！？\n]+', text)
            sentences = [s.strip() for s in sentences if s.strip()]
            if sentences:
                total = len(sentences)
                short8 = sum(1 for s in sentences if len(s) <= 8)
                short_ratio = short8 / total
                rhythm_log.append({
                    "function": beat.function,
                    "short_ratio": short_ratio,
                    "sentence_count": total,
                    "avg_len": sum(len(s) for s in sentences) / total,
                })
            
            yield {
                "type": "beat",
                "beat_index": i,
                "beat_function": beat.function,
                "text": text,
                "temperature": result["temperature"],
                "num_candidates": len(result["candidates"]),
                "selected_candidate": result["selected_index"],
            }
            
            # v2.9: 滚动上下文 — 传完整前文
            prev_full = text
    
    def _compute_rhythm_hint(self, log: list, current_beat, index: int, total: int) -> str:
        """计算节奏反平衡提示
        
        短句判定: ≤8字（中文网文标准）
        """
        if not log or index == 0:
            return ""
        recent = log[-3:]
        avg_short = sum(r["short_ratio"] for r in recent) / len(recent)
        avg_len = sum(r["avg_len"] for r in recent) / len(recent)
        hints = []
        if avg_short > 0.25 and current_beat.function not in ("closing_hook", "conflict_ignition"):
            hints.append(f"前几个节拍≤8字超短句占比{avg_short:.0%}过高。你的节拍默认句长必须≥15字，禁止≤8字超短句，用≥20字长句写环境和感受。")
        elif avg_len < 14 and current_beat.function in ("emotion_settle", "character_highlight", "info_reveal"):
            hints.append("当前句长不足(均<14字)。用≥20字的长句写，每段3-4句。禁止≤12字的句子。")
        action_funcs = {"conflict_ignition", "climax_release", "turning_point", "obstacle_build"}
        recent_actions = sum(1 for r in recent if r["function"] in action_funcs)
        if recent_actions >= 2 and current_beat.function not in action_funcs:
            hints.append("前面连续动作节拍导致节奏过紧，放慢——多用描写少用动作，每段至少2句。")
        if index >= total - 2 and current_beat.function != "closing_hook":
            hints.append("接近章末，不要用≤8字的碎片短句，用≥20字的完整长句铺陈。")
        return " | ".join(hints) if hints else ""
    
    def _build_beat_prompt(self, beat: Beat, prev_full: str, char_snapshots: str,
                           rhythm_hint: str = "", blueprint: str = "",
                           chapter_context: str = "") -> str:
        """构建单个 beat 的 prompt（v2.9: 蓝图 + 滚动上下文）
        
        核心改进：
        - 蓝图注入：所有beat共享章节叙事梗概，保证方向一致
        - 滚动上下文：传上一个beat完整正文，保证段落衔接
        - 章节上下文：世界观/角色/大纲，保证内容不跑偏
        """
        lines = []
        
        # v2.9: 章节蓝图（最高优先级 — 所有beat共享的目标）
        if blueprint:
            lines.extend([
                f"## 📖 本章蓝图（所有节拍必须朝这个方向写）",
                blueprint,
                "---",
            ])
        
        # v2.9: 章节上下文（核心设定速览）
        if chapter_context:
            lines.append(f"## 📋 本章大纲速览\n{chapter_context[:600]}")
        
        lines.extend([
            f"## 当前节拍",
            f"功能: {beat.function} — {beat.goal}",
            f"情绪: {beat.emotion_start} → {beat.emotion_end}",
            f"冲突: {beat.conflict_type} {beat.conflict_intensity}/5",
            f"目标字数: {beat.min_words}-{beat.max_words}字",
        ])
        if beat.paragraph_style:
            lines.append(f"段落节奏: {beat.paragraph_style}")
        if rhythm_hint:
            lines.append(f"⚠️ 全章节奏提示: {rhythm_hint}")
        if beat.key_event:
            lines.append(f"核心事件: {beat.key_event}")
        if beat.character_focus:
            lines.append(f"聚焦角色: {beat.character_focus}")
        
        if prev_full:
            lines.append(f"\n## ⬆️ 上一节拍的完整正文（从这里直接接续）\n{prev_full}")
            lines.append("必须从此处毫无痕迹地接着写。场景、情感、节奏不能断层。")
        
        if char_snapshots:
            lines.append(f"\n## 角色参考\n{char_snapshots}")
        
        if beat.hook_type and beat.function == "closing_hook":
            hook_guide = {
                "crisis_suspense": "在最高危险点截断——武器抵喉、偷袭将至、倒计时归零。",
                "reversal_tease": "揭示一个颠覆认知的信息——但不说完整，留一半。",
                "face_slap_preview": "反派放狠话/要搞事——下一章将被打脸。",
                "secret_reveal": "出现关键线索/神秘人物/隐藏真相的一角。",
            }
            lines.append(f"\n## 章末钩子要求\n{hook_guide.get(beat.hook_type, '')}")
            lines.append(f"钩子类型: {beat.hook_type}")
        
        return "\n".join(lines)


def _extract_last_sentence(text: str, max_chars: int = 50) -> str:
    """提取文本的最后一句话（用于节拍衔接）"""
    import re
    sentences = re.split(r'[。！？\n]+', text)
    # 过滤空白
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return ""
    last = sentences[-1]
    if len(last) > max_chars:
        last = last[-max_chars:]
    return last


# ── 测试 ──

if __name__ == "__main__":
    from .beat_decomposer import BeatDecomposer
    
    # 测试拆解+生成流程（不调LLM，只测骨架）
    test_outline = {
        "title": "第1章 废柴觉醒",
        "summary": "家族测试中被人当众羞辱，意外激活隐藏血脉",
        "emotion_curve": "压抑→绝望→爆发→期待",
        "characters": ["林枫", "林婉儿", "大长老"],
        "hook": "林枫不知道的是，他体内的封印，正裂开第一道缝隙",
    }
    
    decomposer = BeatDecomposer(seed=42)
    beats = decomposer.decompose(test_outline, 1, is_first_chapter=True,
                                  characters=["林枫", "林婉儿", "大长老"])
    
    print(f"拆解 {test_outline['title']} → {len(beats)} 个节拍:")
    for b in beats:
        prompt = b.to_prompt("上一句...")
        print(f"\n{'='*40}")
        print(prompt[:200])
