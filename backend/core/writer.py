"""NovelGenerator — Writer: 章节生成（两遍式 + Humanizer后处理 + 截断检测）"""
import logging
import re
from typing import AsyncGenerator
from openai import OpenAI
from .styles import get_style, build_style_prompt, build_custom_style
from .humanizer import humanize_text, build_humanizer_prompt

log = logging.getLogger(__name__)

WRITER_SYSTEM = """你是一位专业的网络小说作家。

## 你的写作身份

{style_guide}

## 核心写作要求

1. **绝对忠于风格**: 上述文笔特征、语气基调、对话风格是你必须严格遵守的准则。
2. **少样本参考**: 如果提供了风格示例，请模仿其句式节奏、意象选择、情感张力。
3. **标志句式**: 适当使用上述标志性句式/词汇，但不要堆砌。
4. **严禁写法**: 上述禁止列表中的写法一律不得出现。

## 去 AI 味硬规则（违反任何一条 = 不合格）

### 节奏控制
5. **句长变化强制**: 每 200 字至少有一句 ≤8 字（短句爆点）。连续三句长度相差不得超过 6 字。长句（>30字）后必须跟一个短句（≤12字）缓冲。
6. **段落参差**: 每段 1-5 句不等，禁止连续三段都是 3 句。偶尔用单句成段制造冲击力。

### 禁用句式
7. **禁用二元对比壳**: 不使用「不是 A，而是 B」「并非 X，而在于 Y」「不只是 A，更是 B」「与其 X，不如 Y」。
8. **禁用伪洞察标记**: 不使用「真正的」「本质上」「核心在于」「关键在于」「说白了」「归根结底」。
9. **禁用讲义冒号**: 不使用「原因是:」「结论是:」「重点是:」「分成三类:」这类冒号-列举结构。
10. **禁用空泛总结句**: 不写「这不仅仅是一次突破，更是蜕变」「这标志着一个新的里程碑」「在这一刻，他明白了真正的力量」这类万能金句。
11. **禁用抽象压力句**: 不写「差距会拉开」「成为分水岭」「时代变了」这类无具体内容的威胁描述。

### 写作质感
12. **具象优先**: 每个抽象描述必须接地——不用「他感到无比痛苦」写「胸口像被攥住，喘不上气」。不用「实力大幅提升」写「一拳轰出，石壁凹进去半尺」。
13. **对话标注克制**: 不是每句对话都要「XX说」「XX道」。用动作、神态、沉默穿插——推门/拔剑/冷笑/沉默三秒——比「冷冷地说」强十倍。
14. **破折号限用**: 每 500 字最多一个破折号。多用句号切割，少用破折号连接。
15. **少用模糊词**: 不用「似乎」「仿佛」「或许」「大概」堆砌。要么确定，要么用动作表达不确定（「他顿了顿」而非「他似乎犹豫了」）。
16. **去掉 AI 过渡词**: 禁用「与此同时」「在这个过程中」「此外」「值得一提的是」「总的来看」「随着……的发展」。

## 叙事技巧与情绪控制（违反 = 读着没劲）

### 三态情感弧线（每章必须走完）
17. **开篇·压抑态（积蓄期待）**: 不要一上来就高潮。前 1/3 用来堆障碍、制造信息差、让读者揪心——「为什么」「然后呢」「千万别」。
18. **中段·爆发态（情感释放）**: 本章核心冲突在此引爆。冲突对撞（正面对决/价值观碰撞）、反转揭示（预期违背/身份揭露）、节奏加速（句式缩短、场景切换加快）。
19. **结尾·余韵态（意犹未尽）**: 不给大团圆结尾。要么留白（意象留白「剑还在地上转」），要么余波（后果刚显现），要么新悬念植入（「但他不知道的是……」）。

### 场景导演（选一种在本章主导，可混合）
20. **动作场景**: 快节奏。短句为主（≤12字占比>40%）。动作链: 感知→反应→动作→结果。视觉描写 > 心理描写。电影化: 把镜头语言转成文字——「剑尖离喉三寸」「墙上的影子先碎了」。
21. **情感场景**: 中速。内心独白 + 身体微反应（不是胸口发紧/手心出汗/心跳加速这些AI套路，而是「他张了张嘴，没出声」「指甲掐进掌心，不觉得疼」）。对话中留沉默，留未说出口的话。
22. **对话场景**: 变速。高手过招: 每句话都在推进（试探→交锋→摊牌）。菜鸟吵架: 各说各的。对话不是信息传递工具，是角色意志碰撞的战场。每轮对话后给一个动作或神态停顿。

### 冲突控制（每章至少一个，标强度）
23. **冲突类型**: 内心冲突[IN]（道德抉择、价值观挣扎）/ 人际冲突[IR]（对抗、背叛）/ 环境冲突[EN]（生存威胁）/ 宿命冲突[DE]（命运/预言）。
24. **冲突强度**: 1=微弱（隐性存在）、2=轻度（可调和）、3=中度（明确对立）、4=重度（难以调和）、5=极端（生死存亡）。
25. **冲突链**: 本章的冲突是从上一章的哪个冲突升级来的，又将引向下一章的什么冲突？在写作时有意识地让冲突逐级加码。

### POV 硬规则
26. **POV 切换在场景边界**: 不能在段落中间跳视角。场景切换（空行）后才能换POV。
27. **不靠POV偷渡信息**: 主角不知道的事，不能因为切换到上帝视角就写出来。
28. **同一场景单一POV**: 一场战斗/一次对话只能从一个角色的感知出发。

### 紧张-放松法则
29. **高潮后必有缓冲**: 战斗高潮后给一段喘息——一句环境描写、一段沉默、一个日常细节。不能全程高压。读者需要呼吸。

### 叙事时间线（偶尔打破线性）
30. **不要永远一条线走到底**: AI通病——永远从事件起点写到终点。人类会: 从葬礼开场再倒叙(倒叙)、关键瞬间突然插入回忆(插叙)、前文没说的信息在合适时机揭示(补叙)。

## 输出格式

直接输出正文，不需要标题。每段之间空一行。总字数控制在 {target_words} 字左右。"""


# ── 文学模式 System Prompt ──
LITERARY_SYSTEM = """你是一位严肃文学作家。

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
12. **克制**: 感叹号几乎不用。省略号只在必要时用。破折号不超过每800字一个。
13. **具象**: 抽象的概念通过具体的物象来表达。「自由」可以是一扇没锁的窗。「孤独」可以是桌上两副碗筷只用了一副。
14. **声韵**: 中文有自己的声音。注意句子的平仄和韵脚。一段好文字读出来是有节奏的。

## 输���格式

直接输出正文，不需要标题。每段之间空一行。总字数控制在 {target_words} 字左右。"""



STYLE_POLISH_SYSTEM = """你是一位专业的文字编辑，专精于将文字打磨成特定作家的风格。

## 目标风格

{style_guide}

## 打磨要求

你需要将以下草稿进行风格打磨。注意:
1. **不必重写全文**——保留原稿的核心情节和对话内容
2. **修正文笔**——将不匹配的句式替换为目标风格的句式
3. **注入风格标志**——适当加入目标风格的标志性写法（但不能生硬）
4. **去掉违和感**——移除与目标风格冲突的用词和表述
5. **保持字数**——打磨后的字数应与原稿相近（±10%）

## 输出格式

直接输出打磨后的正文，不需要标题和说明。每段之间空一行。"""


class Writer:
    """章节写手 — 两遍生成: 初稿 + 风格打磨"""

    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def _create(self, **kwargs):
        """创建 LLM 请求，v4 系列自动禁用 reasoning"""
        if "v4" in self.model:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        return self.client.chat.completions.create(**kwargs)

    async def write_stream(
        self,
        context: str,
        genre: str = "玄幻",
        style: str = "热血爽文",
        target_words: int = 3000,
        writing_mode: str = "webnovel",
    ) -> AsyncGenerator[str, None]:
        """流式生成章节正文
        
        Args:
            writing_mode: 'webnovel' (网文) or 'literary' (文学)
        """
        # 解析风格
        if style in ("自定义风格",) or style.startswith("自定义"):
            style_config = build_custom_style(style)
        else:
            style_config = get_style(style)
        
        style_prompt = build_style_prompt(style_config)

        # ── 第一遍: 生成初稿 ──
        template = LITERARY_SYSTEM if writing_mode == "literary" else WRITER_SYSTEM
        system_prompt = template.format(
            style_guide=style_prompt,
            target_words=target_words,
        )

        log.info(f"Writing chapter: {genre}/{style}/{writing_mode}, pass 1/2 (draft)")
        
        draft = ""
        stream = self._create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请根据以下上下文和本章大纲，开始写正文：\n\n{context}"},
            ],
            temperature=0.85,
            max_tokens=target_words * 3,
            stream=True,
        )
        
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                draft += delta.content
                yield delta.content  # 流式输出初稿
        
        log.info(f"Draft done: {len(draft)} chars")

        final_text = draft  # default: use draft as-is
        
        # ── 第二遍: 风格打磨（长文跳过——3000字以上初稿质量已够，省一轮API调用）──
        polish_skipped = len(draft) < 500 or style_config.get("is_custom") or len(draft) > 2000
        if polish_skipped:
            if len(draft) > 2000:
                log.info(f"Skipping polish pass (draft {len(draft)} chars, long enough)")
            else:
                log.info("Skipping polish pass (too short or custom style)")
        else:
            try:
                log.info(f"Pass 2/2: style polish")
                polish_prompt = STYLE_POLISH_SYSTEM.format(style_guide=style_prompt)
                
                polish_stream = self._create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": polish_prompt},
                        {"role": "user", "content": f"草稿如下：\n\n{draft}"},
                    ],
                    temperature=0.6,
                    max_tokens=target_words * 3,
                    stream=True,
                )
                
                polished = ""
                for chunk in polish_stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        polished += delta.content
                
                if polished and len(polished) > len(draft) * 0.5:
                    final_text = polished
                    log.info(f"Polish done: {len(polished)} chars")
                else:
                    log.warning(f"Polish result too short ({len(polished)} chars), using draft")
            except Exception as e:
                log.warning(f"Polish pass failed: {e}, using draft")

        # ── Humanizer 检测（长文跳过——减少API调用）──
        try:
            h_result = humanize_text(final_text)
            log.info(f"Humanizer score: {h_result['score']}/100 ({h_result['total_issues']} issues)")
            
            # 初稿 ≥2000字 且 评分 ≥50 → 跳过 Humanizer 重写（省一轮API）
            if len(final_text) >= 2000 and h_result["score"] >= 50:
                log.info(f"Skipping Humanizer pass (score OK: {h_result['score']})")
            elif h_result["score"] < 70 and h_result["total_issues"] > 3:
                log.info(f"Pass 3/3: Humanizer rewrite (score={h_result['score']})")
                h_prompt = STYLE_POLISH_SYSTEM.format(style_guide=style_prompt)
                h_prompt += "\n\n" + build_humanizer_prompt(h_result["detected"])
                
                h_stream = self._create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": h_prompt},
                        {"role": "user", "content": f"需要Humanizer润色的文本：\n\n{final_text}"},
                    ],
                    temperature=0.5,
                    max_tokens=target_words * 3,
                    stream=True,
                )
                
                humanized = ""
                for chunk in h_stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        humanized += delta.content
                
                if len(humanized) > len(final_text) * 0.6:
                    final_text = humanized
                    log.info(f"Humanizer done: {len(humanized)} chars")
        except Exception as e:
            log.warning(f"Humanizer pass failed: {e}, using current text")

        # ── 截断检测 ──
        try:
            is_trunc, reason = _check_truncation(final_text, target_words)
            if is_trunc:
                log.warning(f"Truncation detected: {reason}. Retrying once...")
                retry_text = ""
                retry_stream = self._create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt + f"\n\n⚠️ 注意：上次生成被截断了（{reason}）。请确保本次完整生成。"},
                        {"role": "user", "content": f"请根据以下上下文和本章大纲，重新写正文：\n\n{context}"},
                    ],
                    temperature=0.8,
                    max_tokens=target_words * 3,
                    stream=True,
                )
                for chunk in retry_stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        retry_text += delta.content
                
                is_trunc2, reason2 = _check_truncation(retry_text, target_words)
                if not is_trunc2 and len(retry_text) > len(final_text) * 0.5:
                    final_text = retry_text
                    log.info(f"Retry OK: {len(retry_text)} chars")
                else:
                    log.warning(f"Retry also truncated or short, using best available")
        except Exception as e:
            log.warning(f"Truncation check failed: {e}, using current text")


def _check_truncation(text: str, target_words: int) -> tuple:
    """检测生成内容是否被截断
    
    Returns:
        (is_truncated: bool, reason: str)
    """
    if not text or len(text) < 100:
        return True, "文本过短"
    
    # 1. 结尾完整性: 句子不能断在半截
    last_char = text.rstrip()[-1] if text.rstrip() else ""
    valid_endings = set("。！？…\"')」》")
    if last_char not in valid_endings:
        return True, f"结尾不完整 (最后字符: {last_char})"
    
    # 2. 长度比率: 不能太短
    ratio = len(text) / (target_words * 2)  # 约每个中文字2个token
    if ratio < 0.15:
        return True, f"长度过短 ({len(text)}字 vs 目标{target_words}字)"
    
    # 3. 钩子检查: 结尾应该有悬念/期待感
    last_100 = text[-100:] if len(text) > 100 else text
    has_hook = any(kw in last_100 for kw in ["突然", "忽然", "这时", "那一刻", "然后", "但是", "然而", "奇怪", "……", "?"])
    if not has_hook and target_words > 2000:
        log.info("No hook detected at end (minor)")
    
    return False, ""


def _is_truncated(text: str, expected_max: int) -> bool:
    """快速截断检查"""
    truncated, _ = _check_truncation(text, expected_max)
    return truncated
