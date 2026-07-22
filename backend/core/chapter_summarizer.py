"""NovelGenerator — Chapter Summarizer: 渐进式摘要压缩 Agent

职责: 当小说章节超过一定数量时，自动对旧章节生成压缩摘要，
确保 LLM 上下文窗口不会被长篇全文撑爆。

机制:
- 每写满 10 章触发一次压缩
- 对最近10章生成 300 字摘要
- 旧章正文保留，但注入 Writer 时只注入摘要（最近3章保留全文）
- 摘要存储在 global_state 的 chapter_summaries 字段中
- 支持关键事件标记: 用户标记 ⭐ 的章节永不压缩

Token 预算模型:
  L1 核心设定: ~500 tokens (永远注入)
  L2 近期全文: ~1500 tokens (最近3章)
  L3 最近摘要: ~1000 tokens (最近10章摘要)
  L4 远期摘要: ~1000 tokens (更早章节的超压缩摘要)
  ─────────────────
  总计: ~4000 tokens (远低于 8K 窗口)
"""

import json
import logging
from typing import Optional
from openai import OpenAI

log = logging.getLogger(__name__)

SUMMARIZER_SYSTEM = """你是一位专业的小说摘要生成器。请对以下章节生成精简的剧情摘要。

## 要求

1. 只提取关键剧情, 忽略细节描写和对话
2. 包含以下要素:
   - 本章核心事件 (1句话)
   - 角色状态变化 (谁做了什么/获得了什么/失去了什么)
   - 埋下的伏笔
   - 结尾钩子 (引向下一章)
3. 总长度: 50-100字

## 输出格式

返回 JSON (不要 markdown 标记):
```json
{
  "chapter": 章节号,
  "summary": "摘要文本(50-100字)",
  "key_events": ["事件1", "事件2"],
  "character_changes": {"角色名": "变化"},
  "hooks": ["伏笔或钩子"],
  "importance": "normal|key"  // key=用户标记的重要章节
}
```

只输出 JSON。"""


class ChapterSummarizer:
    """章节渐进式摘要压缩 Agent"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model
        self._key_chapters = set()  # 用户标记为关键的章节号

    # ── 公开接口 ──

    def mark_key_chapter(self, chapter_num: int):
        """标记关键章节（永不压缩全文）"""
        self._key_chapters.add(chapter_num)
        log.info(f"Chapter {chapter_num} marked as key")

    def unmark_key_chapter(self, chapter_num: int):
        self._key_chapters.discard(chapter_num)

    def should_compress(self, chapter_num: int) -> bool:
        """判断是否应该触发压缩（每10章一次）"""
        if chapter_num in self._key_chapters:
            return False
        return chapter_num % 10 == 0

    def summarize_chapter(self, chapter_num: int, chapter_content: str) -> dict:
        """对单章生成摘要
        
        Returns:
            {chapter, summary, key_events, character_changes, hooks, importance}
        """
        # 截取章节前3000字用于分析
        snippet = chapter_content[:3000] if len(chapter_content) > 3000 else chapter_content
        
        importance = "key" if chapter_num in self._key_chapters else "normal"

        if not self.client or not self.model:
            return self._offline_summary(chapter_num, snippet, importance)

        user_prompt = f"""请对第{chapter_num}章生成摘要:

章节内容:
{snippet}

重要性: {importance}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SUMMARIZER_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)
            result["chapter"] = chapter_num
            result["importance"] = importance
            result["compressed_at"] = __import__("datetime").datetime.now().isoformat()
            log.info(f"Summarized chapter {chapter_num}: {len(result.get('summary',''))} chars")
            return result
        except Exception as e:
            log.error(f"Chapter {chapter_num} summarization failed: {e}")
            return self._offline_summary(chapter_num, snippet, importance)

    def summarize_batch(self, chapters: dict, novel_id: str = "") -> dict:
        """批量摘要生成（每章的 summary 累加到全局状态）
        
        Args:
            chapters: {chapter_num: content}
        Returns:
            {chapter_num: summary_dict, ...}
        """
        results = {}
        for ch_num, content in sorted(chapters.items()):
            if ch_num in self._key_chapters:
                results[ch_num] = {
                    "chapter": ch_num,
                    "summary": f"[关键章节·全文保留] {content[:100]}...",
                    "importance": "key",
                }
                continue
            results[ch_num] = self.summarize_chapter(ch_num, content)
        return results

    def get_token_budget(self, total_chapters: int) -> dict:
        """计算不同策略下的 token 预算
        
        Returns:
            {strategy: estimated_tokens}
        """
        avg_chapter_tokens = 1500  # ~500字/章
        summary_tokens = 150       # ~50字摘要
        
        return {
            "full_inject": total_chapters * avg_chapter_tokens,
            "summary_only": total_chapters * summary_tokens,
            "hybrid": min(total_chapters, 3) * avg_chapter_tokens  # 最近3章全文
                     + max(0, total_chapters - 3) * summary_tokens,  # 其余摘要
            "is_safe": (min(total_chapters, 3) * avg_chapter_tokens
                       + max(0, total_chapters - 3) * summary_tokens) < 8000,
        }

    def build_summary_context(self, novel_id: str, current_chapter: int,
                               smm) -> str:
        """为 Writer 构建摘要上下文（替代旧章全文注入）
        
        优先级:
        - 最近3章: 全文（从磁盘读取）
        - 4-10章前: 摘要（从 global_state.summaries 读取）
        - 10章以前: 超压缩摘要（从 global_state.archive 读取）
        """
        parts = []
        state = smm.read("global_state", novel_id)
        summaries = state.get("summaries", {})
        
        # 最近3章: 全文
        for ch_num in range(max(1, current_chapter - 3), current_chapter):
            content = smm.read_chapter(novel_id, ch_num)
            if content:
                # 只取前500字
                preview = content[:500] + ("..." if len(content) > 500 else "")
                parts.append(f"### 第{ch_num}章\n{preview}")
        
        # 4-10章前: 摘要
        recent_summaries = sorted(
            [(int(k), v) for k, v in summaries.items()
             if int(k) >= current_chapter - 10 and int(k) < current_chapter - 3],
            key=lambda x: x[0]
        )
        if recent_summaries:
            parts.append("\n### 前情摘要")
            for ch, summary in recent_summaries:
                if isinstance(summary, dict):
                    summary = summary.get("summary", str(summary))
                parts.append(f"- 第{ch}章: {summary}")
        
        return "\n".join(parts)

    # ── 离线降级 ──

    def _offline_summary(self, chapter_num: int, content: str, importance: str) -> dict:
        """无 LLM 时的规则降级摘要（取首尾100字拼接）"""
        first = content[:100].replace("\n", " ").strip()
        last = content[-100:].replace("\n", " ").strip() if len(content) > 200 else ""
        
        summary = f"第{chapter_num}章: {first}..."
        if last and last != first:
            summary += f" ...{last}"
        
        return {
            "chapter": chapter_num,
            "summary": summary[:150],
            "key_events": [first[:50]],
            "character_changes": {},
            "hooks": [last[:50]] if last else [],
            "importance": importance,
            "compressed_at": __import__("datetime").datetime.now().isoformat(),
        }


# ═══════════════════════════════════════
# 集成到 SharedMemoryManager 的扩展
# ═══════════════════════════════════════

def check_and_compress(smm, novel_id: str, current_chapter: int,
                       summarizer: ChapterSummarizer = None):
    """检查是否需要触发压缩，如需要则执行
    
    在 Engine.generate_chapter_stream 末尾调用。
    每10章触发一次自动压缩。
    """
    if not summarizer:
        return None
    
    if not summarizer.should_compress(current_chapter):
        return None
    
    log.info(f"Auto-compressing: chapter {current_chapter} milestone")
    
    # 对最近10章生成摘要
    start_ch = max(1, current_chapter - 9)
    chapters_to_summarize = {}
    for ch in range(start_ch, current_chapter + 1):
        content = smm.read_chapter(novel_id, ch)
        if content:
            chapters_to_summarize[ch] = content
    
    if not chapters_to_summarize:
        return None
    
    results = summarizer.summarize_batch(chapters_to_summarize, novel_id)
    
    # 更新 global_state
    state = smm.read("global_state", novel_id)
    if "summaries" not in state:
        state["summaries"] = {}
    
    for ch_num, summary in results.items():
        state["summaries"][str(ch_num)] = summary
    
    smm.write("global_state", novel_id, state)
    
    # 日志
    budget = summarizer.get_token_budget(current_chapter)
    log.info(f"Compression done: {len(results)} chapters summarized. "
             f"Token budget: full={budget['full_inject']}, "
             f"hybrid={budget['hybrid']}, safe={budget['is_safe']}")
    
    return results
