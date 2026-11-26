"""NovelGenerator — Writer: 单遍章节生成、流式续写与截断恢复。"""
import asyncio
import logging
import re
import time
from typing import AsyncGenerator
from openai import OpenAI
from .styles import get_style, build_style_prompt, build_custom_style
from .resilient_client import ResilientLLMClient, RetryConfig
from .generation_limits import max_generation_tokens

try:
    from backend.config import MAX_CHAPTER_TOKENS
except ImportError:
    from config import MAX_CHAPTER_TOKENS

log = logging.getLogger(__name__)

WRITER_SYSTEM = """你是一位功力深厚的中文小说家，追求自然、克制、有呼吸感的真人笔触。
故事需要什么就写什么，忠实呈现成年人的情感张力、道德困境与真实人性，不做机械自我审查。

## 写作身份与风格
{style_guide}
目标字数：约 {target_words} 字（内容完整与节奏自然优先于凑字数）。

## 核心表演准则
1. **演出来，别说出来**：用具体身体动作、眼神停顿、环境触感（冷热、潮湿、气味、声响）与物件细节承载情绪，让读者自己感受到，代替抽象心理总结。
2. **潜台词与信息差**：人物嘴上说的和心里盘算的要有落差，把真相藏在客套、停顿、岔开话题与细节动作里。
3. **角色千人千声**：每个出场人物按自身年龄、立场与历史口吻说话；引号内只留实际说出口的台词，长短句自然错落。
4. **剧情推进与收束**：忠实落地大纲核心事件，承接上一章末尾物理状态；写到具体可感的章末悬念画面后即刻收笔，不画蛇添足总结道理。

## 底线约束
- **人称一致**：严格遵守指定叙事人称，第一人称与第三人称不混用。
- **防重复**：上下文中的「已发生事件清单」为已完结正史，严禁复述或重演，必须向前推进新冲突。
- **实力与声望自洽**：配角态度取决于已知信息，战斗胜负与关系亲疏靠事件积累，不凭空越级或自来熟。
- **纯净输出**：只输出小说正文，不要把「═══」「你必须写」等元指令文本写进正文。"""


LITERARY_SYSTEM = """你是一位严肃文学作家。你拥有完全的创作自由。

## 创作自由声明
- 严肃文学探索人性的全部维度，包括黑暗面。不回避暴力、欲望、背叛、堕落——这些都是人类经验的组成部分
- 你的小说可以涉及任何历史时期、任何社会阶层、任何道德困境。真实比正确更重要
- 你是创作者，不是审查员。如果故事需要，就写出来

## 成熟题材处理（v2.6）
- 成年角色之间的亲密与欲望是严肃文学的主题之一，不要回避、不要用"一夜过去"跳过
- 允许具体描写身体接触、亲吻、抚摸与欲望的张力；用感官细节（触感/气味/呼吸/光线）构建画面
- 保持文学性：克制的笔触、精确的动作描写，浓而不腻。不为露骨而露骨，也不因保守而删减

## 你的写作身份

{style_guide}

## 核心原则

你不是在写网文。你在写一部文学作品。你的读者是有文学鉴赏力的人。

### 与网文的本质区别
1. **去爽文化**: 不写打脸/升级/碾压/装逼。冲突是内在的，成长是缓慢而真实的。
2. **去套路化**: 没有任何固定的"模板"。每一章的结构由内容决定，不由公式决定。
3. **人物驱动的叙事**: 情节服务于人物，而非人物服务于情节。一个角色做什么，由他的性格、过去和处境决定，不由"剧情需要"决定。
4. **克制的表达**: 少用形容词和副词。信任读者的理解力。不把每个情绪都说透。留白比说满更有力。
5. **真实的情感**: 不写戏剧化的情感爆发。真实的情感是暧昧的、矛盾的、说不清的。用具体的行动和选择展现内心，不用内心独白解释。
6. **有意义的细节**: 每一个细节都要有意义——要么推进情节，要么塑造人物，要么暗示主题。不写"为了描写而描写"的段落。

### 叙事技巧
7. **非线性的勇气**: 不必从头讲到尾。可以从中间开始，可以插叙回忆，可以留一段空白让读者自己填补。
8. **多义性**: 不给出唯一的"正确答案"。让读者自己去解读。好文学是开放的。
9. **节奏由情绪决定**: 内心风暴时句子可以长而绵密。决战时刻句子可以短到只剩动词。平静时句子可以舒缓。
10. **对话的潜台词**: 人物说的和想的不一样。最精彩的部分是没说的那部分。用停顿、转移话题、答非所问来展现内心。

### 语言要求
11. **精确**: 每个词都要精确。不堆砌近义词。一个准确的动词胜过三个形容词。
12. **克制**: 标点服务于声音和停顿，不连续堆叠感叹号、省略号或破折号。
13. **具象**: 抽象概念尽量通过与人物处境有关的物象、动作和选择呈现，不套用固定意象。
14. **声韵**: 中文有自己的声音。注意句子的平仄和韵脚。一段好文字读出来是有节奏的。

## 段落组织

段落按语义单元组织。动作完成、观察焦点转移、说话者变化或场景切换时自然分段；不要为了整齐把每句话单独成段，也不要把不同焦点硬塞进同一段。

## 输出格式

直接输出正文，不需要标题。正文字数控制在 {target_words} 字左右。"""

class Writer:
    """章节写手：只流式生成一个正文候选，修订由上层最终提交器统一处理。"""

    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model
        # v2.14: 使用韧性客户端统一处理重试、限流、断线重连
        self._resilient = ResilientLLMClient(client, model)

    def _create(self, **kwargs):
        """创建 LLM 请求 — v2.14: 委托给韧性客户端，统一重试+限流+超时"""
        is_stream = kwargs.pop("stream", False)
        messages = kwargs.pop("messages", [])
        temperature = kwargs.pop("temperature", 0.8)
        max_tokens = kwargs.pop("max_tokens", 4096)

        if is_stream:
            # 流式调用返回 response 对象（供 write_stream 迭代）
            call_kwargs = dict(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            if "v4" in self.model:
                call_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            call_kwargs.update(kwargs)
            return self.client.chat.completions.create(**call_kwargs)
        else:
            # 非流式调用走韧性网关（自动重试+限流+退避）
            return self._resilient.create(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )

    async def _create_stream_chunks(self, **kwargs):
        """Consume a sync SDK stream in a worker thread before yielding text."""

        def collect():
            response = self._create(**kwargs)
            chunks = []
            for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    chunks.append(delta.content)
            return chunks

        return await asyncio.to_thread(collect)

    def assess_and_enhance_hook(self, text: str, min_rewrite_score: int = 35) -> dict:
        """AUDIT P1-3: LLM 判断章末钩子强度；偏弱时局部重写结尾。

        仅在规则检测（_check_truncation 关键词）未通过时由调用方触发，
        避免对每章都产生额外 LLM 成本。

        Returns:
            dict: {"assessed", "score", "rewritten", "text"}
                text 仅在 rewritten=True 时给出（替换结尾后的完整正文）。
        """
        result = {"assessed": False, "score": None, "rewritten": False, "text": None}
        if not text or len(text) < 500:
            return result
        try:
            import json as _json
            resp = self._create(
                messages=[
                    {"role": "system", "content": "你是一位严格的网络小说编辑，只评估章末钩子强度。只输出JSON。"},
                    {"role": "user", "content": (
                        "以下是小说章节的最后400字。请判断：读者读到这个结尾，"
                        "是否会产生强烈的「然后呢？」的期待？\n\n"
                        f"章节结尾：\n{text[-400:]}\n\n"
                        '只输出JSON: {"score": 0-100 钩子强度分, "verdict": "一句话判断", '
                        '"suggestion": "若需强化，给出一个具体的结尾改写方向；若钩子足够强则为空字符串"}'
                    )},
                ],
                temperature=0.3,
                max_tokens=300,
            )
            content = (resp.choices[0].message.content or "").strip()
            if not content:
                return result
            import re as _re
            try:
                assessment = _json.loads(content)
            except Exception:
                _m = _re.search(r"\{.*\}", content, _re.S)
                assessment = _json.loads(_m.group(0)) if _m else None
            if not isinstance(assessment, dict):
                return result

            try:
                score = int(assessment.get("score", 50))
            except (TypeError, ValueError):
                score = 50
            result.update({"assessed": True, "score": score})
            log.info(f"Hook LLM assessment: score={score}, verdict={assessment.get('verdict', '')}")

            if score >= min_rewrite_score:
                return result

            suggestion = str(assessment.get("suggestion", "")).strip()
            if not suggestion or suggestion == "无":
                return result

            # 局部重写结尾：只替换最后 300 字，保留前文不动
            resp2 = self._create(
                messages=[
                    {"role": "system", "content": "你是一位网络小说作家。只重写章节的结尾部分，保持与前文风格一致。"},
                    {"role": "user", "content": (
                        f"原文结尾（最后300字）：\n{text[-300:]}\n\n"
                        f"问题：{assessment.get('verdict', '章末钩子偏弱')}\n"
                        f"强化方向：{suggestion}\n\n"
                        "请重写这段结尾，使其成为强钩子（悬念升级/冲突突变/反转/金句）。"
                        "只输出改写后的结尾文字（250-350字），不要任何解释或标题。"
                        "必须以句号/问号/感叹号/省略号结束。"
                    )},
                ],
                temperature=0.75,
                max_tokens=700,
            )
            new_ending = (resp2.choices[0].message.content or "").strip()
            if len(new_ending) < 100:
                log.warning(f"Hook rewrite too short ({len(new_ending)} chars), keeping original ending")
                return result

            head = text[:-300].rstrip() if len(text) > 300 else ""
            new_text = (head + "\n\n" + new_ending) if head else new_ending
            result.update({"rewritten": True, "text": new_text})
            log.info(f"Hook enhanced: {len(new_ending)} chars ending replaced")
        except Exception as e:
            log.warning(f"Hook assessment failed (non-fatal): {e}")
        return result

    async def write_stream(
        self,
        context: str,
        genre: str = "玄幻",
        style: str = "热血爽文",
        target_words: int = 1500,
        writing_mode: str = "webnovel",
        normal_pacing: bool = False,
        fast_food: bool = False,
        chapter_outline: dict = None,
        skip_ending: bool = False,  # v2.12: 质量门重试时跳过Phase 2(避免重复生成结尾)
    ) -> AsyncGenerator[str, None]:
        """流式生成一个章节候选，必要时只从截断点续写。

        Args:
            writing_mode: 'webnovel' (网文) or 'literary' (文学)
            normal_pacing: False=快节奏默认, True=正常节奏
            chapter_outline: 大纲中的本章数据 (含 bridge_to_next/hook)
        """
        chapter_outline = chapter_outline or {}
        # 解析风格
        # v2.8.5: 参数化风格优先识别——此前 startswith("自定义") 把"自定义参数化风格"
        # 也拦截走 build_custom_style，丢弃全部 7 个参数，STYLES 里的参数化条目永不被命中。
        from .styles import STYLES
        if style in STYLES and STYLES[style].get("is_parameterized"):
            style_config = STYLES[style]
        elif style in ("自定义风格",) or style.startswith("自定义"):
            style_config = build_custom_style(style)
        else:
            style_config = get_style(style)

        style_prompt = build_style_prompt(style_config)

        # v2.25: 所有节奏和质量要求已合并到 WRITER_SYSTEM，不再单独注入
        # fast_food 和 normal_pacing 参数保留用于兼容，但不再追加额外指令

        # ── 第一遍: 生成初稿 ──
        template = LITERARY_SYSTEM if writing_mode == "literary" else WRITER_SYSTEM
        system_prompt = template.format(
            style_guide=style_prompt,
            target_words=target_words,
        )
        # v2.7.6: 小目标（超短篇）强化字数硬约束——LLM 在 3x token 预算下倾向
        # 写满，800字目标常输出1800+（超出2.3倍）。给出明确上限与精简要求。
        try:
            _tw = int(target_words)
            if 0 < _tw < 1500:
                system_prompt += (
                    f"\n\n## 超短篇字数铁律（必须遵守）\n"
                    f"本章是超短篇，正文必须控制在 {_tw} 字以内"
                    f"（建议 {int(_tw*0.85)}-{_tw} 字）。\n"
                    f"写完一个完整的故事即可，禁止：铺垫展开、环境赘述、无关对话、凑字数。\n"
                    f"若超出 {int(_tw*1.15)} 字说明你在啰嗦——立即精简收尾。"
                )
        except Exception:
            pass

        # v2.34: 把写作指令从 user context 中提取到 system prompt
        # LLM 不会把 system prompt 内容复制进输出，但会把 user message 里的指令文本当正文模板
        outline_instruction = ""
        instr_marker = "═══ 以下为写作元指令"
        if instr_marker in context:
            instr_start = context.find(instr_marker)
            instr_end = context.find("\n\n## ", instr_start + 10)  # 找到下一个 ## 小节
            if instr_end == -1:
                instr_end = context.find("\n## ", instr_start + 10)
            if instr_end > instr_start:
                outline_instruction = context[instr_start:instr_end].strip()
                context = context[:instr_start] + context[instr_end:]
            else:
                # 没有下一个 section，取到末尾
                outline_instruction = context[instr_start:].strip()
                context = context[:instr_start]

        if outline_instruction:
            # AUDIT P0-1: 剥离 ═══ 标记行，防止指令标记本身进入 system prompt
            outline_instruction = "\n".join(
                ln for ln in outline_instruction.splitlines() if "═══" not in ln
            ).strip()
            system_prompt = system_prompt + "\n\n" + outline_instruction
            log.info(f"Outline injected into system prompt ({len(outline_instruction)} chars)")

        log.info(f"Writing chapter candidate: {genre}/{style}/{writing_mode}")

        # v2.11: 提取桥接指令，放在 user prompt 最前面（LLM 对 user 消息开头最敏感）
        bridge_section = ""
        main_context = context
        bridge_marker = "## 🔗 第"
        opening_marker = "- 🎬 开场场景:"

        if bridge_marker in context:
            # 桥接区独立提取
            bridge_start = context.find(bridge_marker)
            # 找到桥接区结束（下一个 ## 或 L2c 或 L5 或 本章大纲）
            next_section = context.find("\n## ", bridge_start + len(bridge_marker))
            if next_section > bridge_start:
                bridge_section = context[bridge_start:next_section].strip() + "\n\n"
                main_context = context[:bridge_start] + context[next_section:]

        # 开场场景也提取到最前面
        if opening_marker in main_context:
            os_idx = main_context.find(opening_marker)
            os_end = main_context.find("\n", os_idx)
            if os_end > os_idx:
                opening_line = main_context[os_idx:os_end].strip()
                if "开场场景" in opening_line:
                    bridge_section = opening_line + "\n\n" + bridge_section
                    main_context = main_context[:os_idx] + main_context[os_end+1:]

        # 构建 user prompt：桥接指令在最前面
        user_prompt = bridge_section + f"请根据以下上下文和本章大纲，开始写正文：\n\n{main_context}"

        draft = ""
        finish_reason = "unknown"
        # max_tokens 根据目标字数估算，但所有初稿/续写/补全路径共享配置硬上限。
        # v2.7.6: 小目标（<1500字）收紧预算 3x→2x——3x 时 LLM 倾向写满 token
        # 预算（800字目标输出1800+），2x 配合 system 字数铁律更贴近目标。
        _mult = 3 if int(target_words) >= 1500 else 2
        safe_max_tokens = max_generation_tokens(
            int(target_words), multiplier=_mult, hard_cap=MAX_CHAPTER_TOKENS
        )

        # v2.14: 使用韧性客户端的流式调用，支持断线重连
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # v4.8.0: 温和去复读采样参数（若非标供应商不支持则自动静默回退）
        _stream_extra = {"frequency_penalty": 0.22, "presence_penalty": 0.16}
        try:
            async for chunk in self._resilient.create_stream(
                messages=messages,
                temperature=0.85,
                max_tokens=safe_max_tokens,
                **_stream_extra,
            ):
                draft += chunk
                yield chunk  # 流式输出初稿
        except Exception as _pe:
            if draft:
                raise
            log.info("Sampling penalty fallback without penalty params: %s", _pe)
            async for chunk in self._resilient.create_stream(
                messages=messages,
                temperature=0.85,
                max_tokens=safe_max_tokens,
            ):
                draft += chunk
                yield chunk  # 流式输出初稿

        # 流式调用完成后推断 finish_reason
        if len(draft) < target_words * 0.8:
            finish_reason = "length"
        else:
            finish_reason = "stop"

        log.info(f"Draft done: {len(draft)} chars, finish_reason={finish_reason}")

        # ── finish_reason 检测: API 因 token 不足截断 → 续写而不是重写 ──
        if finish_reason == "length" and len(draft) < target_words * 0.8:
            log.warning(f"Draft truncated by API (finish_reason=length, {len(draft)} < {int(target_words*0.8)}). "
                       f"Continuing from breakpoint with doubled max_tokens...")
            try:
                # v2.7.6: 小目标续写预算收紧（6x→3x），防补过头
                retry_max_tokens = max_generation_tokens(
                    int(target_words), multiplier=(6 if int(target_words) >= 1500 else 3),
                    hard_cap=MAX_CHAPTER_TOKENS
                )
                # v2.49: 从断点续写，而不是从头重写
                # 将已生成内容作为 assistant 消息传递，让 LLM 知道已经写了什么
                continue_msgs = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": draft},
                    {"role": "user", "content": "以上是你的初稿，但字数不足。请从上面的断点处继续写，不要重复已写内容。新增部分应该无缝衔接上文，保持同样的风格和视角。"},
                ]
                retry_stream = self._resilient.create_stream(
                    model=self.model,
                    messages=continue_msgs,
                    temperature=0.8,
                    max_tokens=retry_max_tokens,
                    stream=True,
                )
                continuation = ""
                async for text in retry_stream:
                    continuation += text
                    yield text
                if continuation:
                    # 去重：检查续写开头是否与草稿结尾重叠
                    cleaned = _dedup_continuation(draft, continuation)
                    draft = draft + cleaned
                    log.info(f"Continuation OK: +{len(cleaned)} chars → total {len(draft)} chars")
                else:
                    log.warning("Continuation empty, keeping truncated draft")
            except Exception as e:
                log.warning(f"Continuation failed: {e}, keeping truncated draft")


def _dedup_continuation(existing: str, continuation: str) -> str:
    """去重续写内容：忽略前导空白/换行并移除与前文结尾重叠的部分"""
    if not continuation:
        return ""
    existing_clean = existing.rstrip()
    cont_stripped = continuation.lstrip()
    if not existing_clean or not cont_stripped:
        return continuation

    overlap_window = min(80, len(existing_clean))
    tail = existing_clean[-overlap_window:]

    # 1) 精确后缀-前缀重叠（忽略续写前导空白）
    best_cut = 0
    for cut_len in range(min(overlap_window, len(cont_stripped)), 3, -1):
        if tail[-cut_len:] == cont_stripped[:cut_len]:
            best_cut = cut_len
            break

    if best_cut > 0:
        log.info("Dedup: removed %d overlapping chars", best_cut)
        return cont_stripped[best_cut:]

    # 2) 续写重复了前文最后一句（允许开头多出1-2个标点/引号）
    for lead in range(1, min(4, len(cont_stripped))):
        sub = cont_stripped[lead:]
        for cut_len in range(min(overlap_window, len(sub)), 7, -1):
            if tail[-cut_len:] == sub[:cut_len]:
                log.info("Dedup: removed %d sentence-echo chars", lead + cut_len)
                return sub[cut_len:]

    return continuation


def _trim_to_target(text: str, target_words: int) -> str:
    """v2.7.6: 超短篇超长兜底——上层修订可能把 800 字目标扩写到
    1800+，保存前按目标字数规则裁剪（保留完整句子）。仅小目标(<1500字)生效，
    长篇不动。"""
    try:
        tw = int(target_words)
        if tw >= 1500:
            return text
        cap = int(tw * 1.35)
        if len(text) <= cap:
            return text
        cut = text[:tw]
        # 从目标字数向前寻找安全句末边界，避免裁在未闭合引号/括号内部
        pairs = {"「": "」", "『": "』", "“": "”", "（": "）", "【": "】", "《": "》"}
        closers = {v: k for k, v in pairs.items()}

        def _delimiters_balanced(sample: str) -> bool:
            stack = []
            for ch in sample:
                if ch in pairs:
                    stack.append(ch)
                elif ch in closers:
                    if not stack or stack[-1] != closers[ch]:
                        return False
                    stack.pop()
            return not stack

        best_cut = ""
        search_pos = len(cut)
        while search_pos > int(tw * 0.5):
            idx = max(
                cut.rfind("。", 0, search_pos),
                cut.rfind("！", 0, search_pos),
                cut.rfind("？", 0, search_pos),
                cut.rfind("…", 0, search_pos),
            )
            if idx <= int(tw * 0.5):
                break
            end = idx + 1
            while end < len(text) and text[end] in ("」", "』", "”", "）", "】", "》", '"', "'"):
                end += 1
            candidate = text[:end].rstrip()
            if _delimiters_balanced(candidate):
                best_cut = candidate
                break
            search_pos = idx

        if best_cut:
            cut = best_cut
        else:
            last_punct = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"), cut.rfind("…"))
            if last_punct > tw * 0.5:
                cut = cut[:last_punct + 1]
        log.info("Flash trim: %d -> %d chars (target %d)", len(text), len(cut), tw)
        return cut
    except Exception:
        return text


def _check_truncation(text: str, target_words: int) -> tuple:
    """检测生成内容是否被截断

    Returns:
        (is_truncated: bool, reason: str)
    """
    if not text or len(text) < 100:
        return True, "文本过短"

    # 1. 结尾完整性: 句子不能断在半截
    last_char = text.rstrip()[-1] if text.rstrip() else ""
    valid_endings = set("。！？….!?\"'”’」』）】》")
    if last_char not in valid_endings:
        return True, f"结尾不完整 (最后字符: {last_char})"

    # 2. 长度比率: 低于50%目标字数视为截断
    char_count = len(text)
    # v2.7.6: 下限 500→40——字数放开后小目标（如超短篇300字）不得被判截断；
    # 正常目标(≥1000字)时 0.5×目标 ≥500，公式对长篇行为完全不变。
    min_acceptable = max(40, int(target_words * 0.5))  # 不低于目标50%，下限40字
    if char_count < min_acceptable:
        return True, f"长度不足 ({char_count}字 vs 目标{target_words}字, 最低要求{min_acceptable}字)"

    # 3. 钩子检查: 结尾应该有悬念/期待感
    # AUDIT P1-3: 移除「然后/但是/这时/然而」等高频非钩子词，避免假阳性。
    # 此检查仅作提示，真正的钩子强弱判断见 Writer.assess_and_enhance_hook（LLM 评估）。
    last_100 = text[-100:] if len(text) > 100 else text
    has_hook = any(kw in last_100 for kw in ["突然", "忽然", "那一刻", "奇怪", "竟然", "没想到", "却见", "就在这时", "……", "？", "?"])
    if not has_hook and target_words > 2000:
        log.info("No hook detected at end (minor)")

    return False, ""


def _is_truncated(text: str, expected_max: int) -> bool:
    """快速截断检查"""
    truncated, _ = _check_truncation(text, expected_max)
    return truncated


def _ensure_complete_ending(text: str) -> str:
    """确保章节结尾是一个完整的句子/段落。

    当 API 在 max_tokens 处截断时，最后一句可能只写了一半。
    此函数找到最后一个完整的句子边界并裁切到那里。
    """
    if not text or len(text) < 100:
        return text

    text = text.rstrip()

    # 完整结尾标记：句号、问号、感叹号、省略号、右引号、右书名号
    COMPLETE_MARKERS = set("。？！？!…~\"”’") | set("」』】〖〗》〉＞")

    # 如果已经以完整标记结尾，不需要处理；但右引号/括号内可能仍是
    # 没有句末标点的半句（例如“把纸给我”），交给下面的质量门会误报，
    # 因此保留原文以外的闭合符号时，后续统一补一个句号。
    last_char = text[-1]
    if last_char in COMPLETE_MARKERS:
        if last_char in {'"', "'", "”", "’", "」", "』", "】", "》", "〉", "＞"}:
            inner = text.rstrip('"\'”’」』】》〉＞')
            if inner and inner[-1] not in "。？！？!…~":
                return text + "。"
        return text

    # 从末尾反向搜索，找到最后一个完整句子边界
    # 搜索范围：末尾 300 字符内
    search_start = max(0, len(text) - 300)
    tail = text[search_start:]

    # 找最后一个完整句子结束标记
    last_complete = -1
    for marker in "。？！？!…":
        pos = tail.rfind(marker)
        if pos > last_complete:
            last_complete = pos

    if last_complete == -1:
        # 如果整个尾部都没找到完整句子标记，但文本够长，
        # 说明可能在叙述中间被截断，尝试找最后一个引号对话结束处
        for marker in ['」', '』', '】', '"', '\'']:
            pos = tail.rfind(marker)
            if pos > last_complete:
                last_complete = pos

    if last_complete >= 0:
        # 裁切到完整句子位置 + 1（保留标点）
        cut_point = search_start + last_complete + 1
        trimmed = text[:cut_point].rstrip()

        # 明确的截断符（破折号/省略号）即使只剩几个字符也表示半句；
        # 不能用长度阈值把它放过去，否则下一层质量门只能报错并丢失候选。
        removed = len(text) - len(trimmed)
        explicit_truncation = text.endswith(("——", "—", "...", "…"))
        if removed > 10 or explicit_truncation:
            log.info(f"Ensured complete ending: trimmed {removed} chars of incomplete sentence")
            return trimmed

    # 兜底：如果实在找不到完整标记，保持原文（至少有内容）
    return text


def _prepare_chapter_text_for_validation(text: str, target_words: int) -> str:
    """裁剪短目标正文并清除末尾截断，保证质量门拿到最终候选。"""
    return _ensure_complete_ending(_trim_to_target(text, target_words))


def _add_closing_hook(text: str) -> str:
    """Ensure chapter ending has proper closure"""
    if not text or len(text) < 500:
        return text
    return text
