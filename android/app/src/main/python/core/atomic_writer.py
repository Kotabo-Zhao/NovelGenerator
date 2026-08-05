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
- 你拥有完整的创作权限。处理成人情感关系、道德困境、人性欲望——故事需要什么就写什么，不要自我审查
- 你的读者是成年人，他们期待真实完整的叙事

## 成熟题材处理（v2.6）
- 成年角色之间的亲密场景要正面写完整，不要"一夜过去"跳过
- 允许具体描写身体接触、亲吻、抚摸的推进、感官细节（触感/气味/呼吸/声音）
- 用动作和细节构建画面感；保持文学性，浓而不腻

## 🎯 核心规则：你的节拍必须服务本章大纲

- 上下文中提供了本章的写作指令。你的节拍是本章拼图的一块——必须严丝合缝地对接本章的核心事件和钩子
- **不要偏离大纲自创剧情**。即使你是独立写手，也要为本章统一目标服务
- 你的节拍写完后，下一节拍直接从你的最后一句继续。所以最后一句不要写成死胡同

## 写作质量

### 必须推进剧情
- 这个节拍必须在情节上往前推一步。读者看完这个节拍后应该比之前知道更多、感受到更多
- 如果这个节拍删掉不影响剧情理解 → 这个节拍不合格。重新写
- 环境描写/内心独白/角色闲聊：除非直接服务于当前节拍的核心事件，否则不写

### 去AI味规则
- 句长变化：长句>30字后跟短句≤12字
- 禁用：太阳穴/胸口一紧/心跳加速/倒吸凉气/脊背发凉/瞳孔骤缩/浑身一震/嘴角勾起/眼底闪过/眸光/眸色
- 禁用：「不是A而是B」「真正的/本质/关键在」「与此同时」「随着……的发展」

### 段落格式
- 3-5句为一个自然段（80-150字），段间空一行。禁止一句一段

### 跨世界设定忠实度

当涉及已有原著的世界时，必须严格遵守。任何违背都会让小说变成胡编乱造。
- **时间线锁定**：锚定具体时间点，已发生事件不可篡改，未发生事件可干预但不能让不符合时间线的事物提前出现
- **角色设定**：性格/身份/立场/关系/技能必须与原作一致。可改编情节，但动机必须符合原作性格
- **地点与势力**：使用原作中的地名和组织名，不自行发明。势力关系（敌对/联盟/从属）必须与原作一致
- **力量体系**：使用原作的力量框架，不混入其他世界的设定
- **灰色地带**：不确定时选最主流版本（漫画优先），不了解的世界只做框架引用不深入细节

### 主角身份定位
角色待遇必须与当前剧情阶段匹配。严禁身份错位。
- **声望靠积累**：初出茅庐必被轻视，实力微末必被刁难。不打Boss不配获得敬畏
- **配角反应要合逻辑**：配角只知道他们应该知道的信息，不会未卜先知主角的身份
- 动笔前自问：主角到现在实际达成了什么成就？这个配角凭什么知道？
- 反例：刚到新地图就被所有人认出来；刚杀一个小怪就威名远播

### 实力逻辑
- **等级自洽**：主角实力与能击败的敌人上限对应。差两档以上必败或必须借势
- **赢要合理**：碾压(有代价)/智取(真聪明非敌人蠢)/借势(用完就没)/侥幸(偶尔不能连续)
- 反例：刚觉醒就打爆千年老妖；敌人站着等主角念招式；每章都顿悟

## 输出格式
只输出节拍正文。不要标题、序号、说明文字。正常段落格式。"""


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
            
            # v2.49: 累积上下文 — 传递所有前文而不是仅上一beat
            prev_full = (prev_full + "\n\n" + text) if prev_full else text
    
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
        """构建单个 beat 的 prompt
        
        v2.35: 从chapter_context提取写作指令全文注入每个beat
        """
        lines = []
        
        # 蓝图（所有beat共享）
        if blueprint:
            lines.extend([
                "## 本章蓝图（所有节拍必须朝这个方向写）",
                blueprint,
                "---",
            ])
        
        # 提取写作指令（最高优先级）
        instr_marker = "═══ 以下为写作元指令"
        if instr_marker in chapter_context:
            instr_start = chapter_context.find(instr_marker)
            remaining = chapter_context[instr_start:]
            # 尝试找到下一个 section 边界，如果没有则取到末尾
            for delimiter in ["\n\n## ", "\n\n---", "\n## "]:
                instr_end = remaining.find(delimiter)
                if instr_end > 10:
                    outline_instr = remaining[:instr_end].strip()
                    break
            else:
                outline_instr = remaining.strip()  # 最后一段，取全部
            # AUDIT P0-1: 剥离 ═══ 标记行，防止指令标记进入 beat 的 user prompt
            outline_instr = "\n".join(
                ln for ln in outline_instr.splitlines() if "═══" not in ln
            ).strip()
            lines.extend([
                "## 本章写作指令（必须遵守）",
                outline_instr,
                "---",
            ])
        elif chapter_context:
            # 降级：取前600字核心设定
            lines.append(f"## 本章大纲速览\n{chapter_context[:600]}")
        
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
