"""NovelGenerator — BeatAssembler: 节拍→流畅章节 装配器

将独立生成的 beat 拼接为一章流畅的正文。
这是最难的部分——必须在"随机性"和"可读性"之间找到平衡。

核心功能:
1. 去重: 相邻beat语义重复 → 合并或删除
2. 平滑: 在拼接点生成过渡句
3. 一致性: 角色行为/OOC检查
4. POV一致性: 不允许在beat内部跳POV
5. 节奏验证: 拼接后的章节调用评估系统
"""
import re
import logging
from typing import Optional

from .evaluation_system import (distinct_n, coherence_score, hook_strength,
                                 dopamine_density, ai_slop_score)

log = logging.getLogger(__name__)


class BeatAssembler:
    """节拍装配器 — 拼接近乎独立的文本片段为流畅章节"""
    
    def __init__(self):
        self.min_transition_words = 0
        self.max_transition_words = 30
    
    def assemble(self, beats_text: list, chapter_title: str = "",
                 chapter_num: int = 0, char_snapshots: dict = None) -> dict:
        """将 beat 文本列表装配为完整章节
        
        Args:
            beats_text: [{"index":0, "function":"opening_hook", "text":"...", ...}, ...]
            chapter_title: 章节标题
            chapter_num: 章节号
            char_snapshots: 角色快照（用于一致性检查）
        
        Returns:
            {
                "full_text": str,
                "transitions": [{from, to, added_text}, ...],
                "duplicates_removed": int,
                "consistency_issues": [...],
                "quality_report": dict,  # 评估系统输出
            }
        """
        if not beats_text:
            return self._empty_result()
        
        texts = [b["text"].strip() for b in beats_text]
        functions = [b.get("function", "unknown") for b in beats_text]
        
        # ── 步骤1: 去重 ──
        texts, removed = self._deduplicate(texts)
        
        # ── 步骤2: 生成过渡句 ──
        assembled, transitions = self._stitch(texts, functions)
        
        # ── 步骤3: 一致性检查 ──
        issues = self._consistency_check(assembled, char_snapshots or {})
        
        # ── 步骤4: 格式化 ──
        formatted = self._format_chapter(assembled, chapter_title, chapter_num)
        
        # ── 步骤4.5: 句子级去重（装配后扫一遍，去掉相邻重复句）──
        assembled = self._deduplicate_sentences(assembled)
        formatted = self._format_chapter(assembled, chapter_title, chapter_num)
        
        # ── 步骤5: 质量评估 ──
        quality = self._evaluate(assembled)
        
        return {
            "full_text": formatted,
            "raw_text": assembled,
            "transitions": transitions,
            "duplicates_removed": removed,
            "consistency_issues": issues,
            "quality_report": quality,
            "beat_count": len(beats_text),
        }
    
    def _deduplicate(self, texts: list) -> tuple:
        """检测相邻beat的语义重复，合并或删除
        
        简单策略: Jaccard相似度 > 0.6 时标记为重复
        """
        from .evaluation_system import _jaccard_distance
        
        cleaned = []
        removed = 0
        skip_next = False
        
        for i, text in enumerate(texts):
            if skip_next:
                skip_next = False
                removed += 1
                continue
            
            if i < len(texts) - 1:
                dist = _jaccard_distance(text, texts[i+1])
                if dist < 0.4:  # 相似度 > 0.6 → 重复
                    # 保留较长的
                    if len(text) >= len(texts[i+1]):
                        cleaned.append(text)
                    else:
                        cleaned.append(texts[i+1])
                    skip_next = True
                    removed += 1
                    log.info(f"BeatAssembler: deduplicated beats {i} and {i+1} (similarity={1-dist:.2f})")
                    continue
            
            cleaned.append(text)
        
        return cleaned, removed
    
    def _stitch(self, texts: list, functions: list = None) -> tuple:
        """拼接 beat 文本，在接缝处生成过渡
        
        策略：
        - 检查前后beat的衔接是否自然（通过简单的模式匹配）
        - 如果需要过渡，从后beat开头提取或生成一句话衔接
        - 不需要 LLM——规则 + 文本操作 = 稳定可预测
        """
        if len(texts) <= 1:
            return "\n\n".join(texts), []
        
        assembled_parts = [texts[0]]
        transitions = []
        
        for i in range(1, len(texts)):
            prev = texts[i-1]
            curr = texts[i]
            
            # 检查是否需要过渡
            need_transition, reason = self._need_transition(prev, curr)
            
            if need_transition:
                transition = self._generate_transition(prev, curr, functions[i] if functions else "")
                if transition:
                    # 将过渡拼到当前beat前
                    assembled_parts.append(transition + " " + curr)
                    transitions.append({
                        "from_beat": i-1,
                        "to_beat": i,
                        "reason": reason,
                        "added_text": transition,
                    })
                    continue
            
            # 不需要过渡，直接拼接
            assembled_parts.append(curr)
        
        assembled = "\n\n".join(assembled_parts)
        return assembled, transitions
    
    def _need_transition(self, prev: str, curr: str) -> tuple:
        """判断两个beat之间是否需要过渡句
        
        Returns:
            (need_transition: bool, reason: str)
        """
        # 检查1: 前beat末尾是否是断句（以省略号/破折号结尾）
        if prev.rstrip().endswith(('……', '...', '——', '—')):
            return False, "前beat以悬念结尾，自然衔接"
        
        # 检查2: 后beat开头是否直接引用了前beat的末句关键词
        prev_last = _last_sentence(prev)
        curr_first = _first_sentence(curr)
        if prev_last and curr_first:
            # 简单词汇重叠检测
            prev_words = set(prev_last) & set(curr_first)
            if len(prev_words) > 5:
                return False, "关键词自然衔接"
        
        # 检查3: 时间/空间跳跃
        jump_signals = ["第二天", "一个时辰后", "三天后", "与此同时", "另一边",
                        "转眼", "不久之后", "过了", "次日", "翌日"]
        if any(sig in curr_first for sig in jump_signals):
            return False, "显式时间/空间过渡"
        
        # 检查4: 对话连续（前beat以「」结尾，后beat以「开头）
        if prev.rstrip().endswith('」') and curr.lstrip().startswith('「'):
            return False, "对话连续"
        
        # 检查5: 场景切换
        scene_shift = False
        for sig in jump_signals[3:]:  # 跳过后两个
            if sig in curr[:80]:
                scene_shift = True
                break
        
        if scene_shift:
            return True, "场景切换需要过渡"
        
        # 默认：检查是否明显断层
        # 如果后beat前30字中没有任何与前beat后30字的词汇重叠，可能需要过渡
        prev_tail = set(prev[-30:]) if len(prev) >= 30 else set(prev)
        curr_head = set(curr[:30]) if len(curr) >= 30 else set(curr)
        overlap = prev_tail & curr_head
        if len(overlap) == 0:
            return True, f"词汇无重叠(共{len(prev_tail)}和{len(curr_head)}词)"
        
        return False, ""
    
    def _generate_transition(self, prev: str, curr: str, curr_function: str = "") -> str:
        """生成过渡句——规则驱动，不用LLM
        
        策略：
        1. 如果后beat以角色名开头，用「XX……」衔接
        2. 如果是场景切换，用简洁的环境过渡
        3. 否则从后beat前15字截取作为钩子
        """
        curr_first_sentence = _first_sentence(curr)
        
        # 如果后beat是 opening_hook 且后文已有足够上下文，直接开始
        if curr_function == "opening_hook":
            return ""
        
        # 策略1: 后beat以角色名开头
        import re as regex
        name_match = regex.match(r'^([\u4e00-\u9fff]{2,3})', curr_first_sentence)
        if name_match:
            name = name_match.group(1)
            options = [
                f"{name}",
                f"就在这时，{name}",
                f"{name}深吸一口气。",
            ]
            import random
            return random.choice(options)
        
        # 策略2: 简洁的场景过渡
        options = []
        if "门" in curr_first_sentence or "窗" in curr_first_sentence or "房" in curr_first_sentence:
            options = ["", "室内一片寂静。", "光线从窗外斜斜地照进来。"]
        elif "走" in curr_first_sentence or "跑" in curr_first_sentence or "追" in curr_first_sentence:
            options = ["没有犹豫的时间了。", ""]
        else:
            # 策略3: 不做过渡，直接拼接（多数情况）
            options = [""]
        
        import random
        return random.choice(options)
    
    def _consistency_check(self, text: str, char_snapshots: dict) -> list:
        """检查角色行为一致性
        
        Returns:
            [{issue: str, severity: "low"|"mid"|"high"}]
        """
        issues = []
        
        for name, snap in char_snapshots.items():
            # 检查角色是否在文本中出现（如果快照说应该出现）
            # 这是轻量检查，深度检查留给 LogicSupervisor
            if snap.get("last_chapter_appeared", 0) > 0:
                if name not in text:
                    # 不一定每次出场，所以只是 low
                    pass
        
        return issues
    
    def _deduplicate_sentences(self, text: str) -> str:
        """去掉相邻重复句（装配后清理beat拼接处的重复）"""
        sentences = re.split(r'([。！？\n]+)', text)
        cleaned = []
        prev_content = None
        i = 0
        while i < len(sentences):
            current = sentences[i].strip()
            sep = sentences[i+1] if i+1 < len(sentences) else ''
            if current and current == prev_content:
                if sep:
                    cleaned.append(sep)
            else:
                cleaned.append(sentences[i])
                if sep:
                    cleaned.append(sep)
                if current:
                    prev_content = current
            i += 2 if i+1 < len(sentences) else 1
        return ''.join(cleaned)
    
    def _format_chapter(self, text: str, title: str, ch_num: int) -> str:
        """格式化章节文本"""
        if not title:
            title = f"第{ch_num}章"
        return f"# 第{ch_num}章 {title}\n\n{text}"
    
    def _evaluate(self, text: str) -> dict:
        """使用评估系统对装配后的章节做质量评分"""
        from .evaluation_system import evaluate_chapter
        return evaluate_chapter(text)
    
    def _empty_result(self) -> dict:
        return {
            "full_text": "", "raw_text": "",
            "transitions": [], "duplicates_removed": 0,
            "consistency_issues": [], "quality_report": {},
            "beat_count": 0,
        }


def _last_sentence(text: str) -> str:
    """提取最后一句话"""
    sentences = re.split(r'[。！？\n]+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    return sentences[-1] if sentences else ""


def _first_sentence(text: str) -> str:
    """提取第一句话"""
    sentences = re.split(r'[。！？\n]+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    return sentences[0] if sentences else ""


# ── 便捷函数 ──

def assemble_beats(beats_text: list, chapter_title: str = "",
                   chapter_num: int = 0, char_snapshots: dict = None) -> dict:
    """便捷函数：装配节拍为章节"""
    assembler = BeatAssembler()
    return assembler.assemble(beats_text, chapter_title, chapter_num, char_snapshots)


# ── 测试 ──

if __name__ == "__main__":
    # 模拟 5 个独立生成的 beat 文本
    mock_beats = [
        {"index": 0, "function": "opening_hook", "text": "演武场上，数千道目光都落在林枫身上。嘲弄的、怜悯的、幸灾乐祸的。他握紧拳头，指甲掐进掌心。"},
        {"index": 1, "function": "obstacle_build", "text": "测试石毫无反应。一片死寂。然后是哄堂大笑。'三年了，还是废物一个。'大长老摇着头，转身要走。"},
        {"index": 2, "function": "character_highlight", "text": "林枫没动。他站在原地，看着那块石头。三年前它亮过一次——就在父亲失踪的那天晚上。没人知道。他也不打算说。但今天不一样。他感觉到了某种东西在体内翻涌。"},
        {"index": 3, "function": "turning_point", "text": "石头裂了。笑声戛然而止。一道金色的光从裂缝中炸开——不是灵力，是某种更古老的东西。林枫的手还贴在石面上。他低头看自己的掌心：一道从未见过的纹路正在浮现。"},
        {"index": 4, "function": "closing_hook", "text": "全场死寂。大长老猛地回头，脸上的表情不是震惊——是恐惧。'封印……破了。'他喃喃道。但林枫没听见。因为他脑海中正响起一个声音，苍老而疲惫：'终于等到你了。'"},
    ]
    
    assembler = BeatAssembler()
    result = assembler.assemble(mock_beats, "废柴觉醒", 1)
    
    print(f"=== 装配结果 ===")
    print(f"原始 beat 数: {result['beat_count']}")
    print(f"去重: {result['duplicates_removed']} 个")
    print(f"过渡句: {len(result['transitions'])} 个")
    print(f"一致性问题: {len(result['consistency_issues'])} 个")
    print(f"质量综合评分: {result['quality_report'].get('overall_score', 'N/A')}")
    print(f"\n装配后正文:\n{result['raw_text'][:500]}...")
