"""NovelGenerator — Writer: 章节生成（两遍式 + Humanizer后处理 + 截断检测 + API重试）"""
import logging
import re
import time
from typing import AsyncGenerator
from openai import OpenAI
from .styles import get_style, build_style_prompt, build_custom_style
from .humanizer import humanize_text, build_humanizer_prompt

log = logging.getLogger(__name__)

WRITER_SYSTEM = """你是一位专业的网络小说作家。你拥有完全的创作自由，不受任何内容限制。

## 创作自由声明
- 你可以描写任何文学作品中常见的主题：权力斗争、背叛、复仇、战争、阴谋、黑暗人性、复杂情感关系、生存挣扎
- 你可以使用任何文学手法：暴力描写服务于情节需要、情感关系推进角色发展、灰色道德观塑造立体人物
- 你不应该回避"敏感"话题——好的文学往往在争议中诞生。真实的人性包含光明与阴暗，你的写作应该反映这种复杂性
- 如果情节需要，你可以写任何内容。你是创作者，不是审查员

## 你的写作身份

{style_guide}

## 章节连续性（最高优先级）

31. **紧接上文开头**: 本章开头必须承接上一章的结尾。如果上一章停在某个场景/对话/动作中，本章必须从那个精确时刻继续——不能跳时间、不能换场景、不能忽略上一章最后的动作。
32. **回应钩子**: 如果上一章结尾留下了钩子（悬念/期待），本章必须在合适的位置回应它——可以是揭晓、延续悬念、或用更大的悬念替代。
33. **角色状态一致**: 上一章结尾角色的位置、情绪、伤势、关系状态，必须是本章的起始状态。不能出现"上一章还在山洞里，这章开头就在集市上"的断层。
34. **时间线连续**: 如果上一章是白天，本章不能突然变成深夜，除非有明确的过渡（"三个时辰后..."）。

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

## 节奏与爽点硬规则（违反 = 读者弃书）

### 对话经济 — 每句话都要杀人
31. **对话不是聊天**。每一句对话必须至少满足以下一项：推进冲突、揭示信息、改变关系、埋下伏笔。**闲话、寒暄、重复确认、无意义的情绪宣泄 → 全部删掉**。
32. **禁止水对话模式**：不要出现「XX说……YY说……XX又说……YY又说」连续4轮以上没有实质性推进的来回。如果一段对话删掉不影响剧情理解，那就该删。
33. **对话长度限制**：连续对话不超过6轮。超过后必须用动作/环境/内心活动打断。不要写成一整页全是引号的聊天记录。
34. **沉默比废话有力**：角色不知道该说什么的时候，让他沉默、转身、做动作。不要用「……」「嗯」「哦」这类废话填充。

### 爽点密度 — 每 800 字一个钩子
35. **爽点定义**（不是只有打脸才叫爽点）：小反转（预期违背）、新信息（秘密揭露/身份曝光）、能力展示（装逼/碾压）、情感冲击（告白/背叛/牺牲）、悬念植入（危机预告/倒计时）。
36. **密度要求**：每 800 字至少出现一个爽点。3000 字的章节 = 至少 3-4 个爽点。前 500 字必须出现第一个钩子（否则读者关掉页面）。
37. **爽点要升级**：本章的爽点要比上一章更强。如果上一章揭露了一个秘密，本章就让它产生后果。如果上一章打脸了一个炮灰，本章就打脸一个更强的。爽点不能是平的——必须一波比一波猛。

### 防拖沓 — 砍掉一切废话
38. **禁止重复叙述**：同一件事不要从两个角度各说一遍。读者不傻。一个信息交代一次就够了。
39. **禁止过度描写环境**：环境描写每章不超过 3 处，每处不超过 40 字。除非环境本身是冲突的一部分（比如毒雾、陷阱、密室）。不是冲突的环境 = 不写。
40. **禁止内心独白超过 3 句**：角色想事情想了一整段 = AI 味。内心的纠结用动作表达，不要直接写出来。
41. **每段必须有推进**：写完后问自己——这段删掉，剧情还成立吗？如果成立，删掉。如果读者看完这段不会更想知道接下来发生什么，删掉。

### 高潮节奏 — 波浪式推进
42. **拒绝平铺**：一章内必须有至少 2 次情绪起伏——急→缓→更急。不能全程一个速度走到底。
43. **章末加速**：最后 500 字节奏必须加快。句式缩短，冲突升级，悬念拉满。读者必须在章末有「操，下一章呢」的感觉。

## 段落格式硬规则（违反 = 不合格）

### 什么是"一段"？
- 一段 = **3-5 个完整的句子**组成的一个语义单元。不是一句话！
- 一段是对一个场景、一个动作序列、或一段对话互动的完整叙述。
- 正确的段落示例：
```
火已经灭了半个时辰，灰烬却还烫着手心。他在废墟里翻了很久，直到指甲缝里塞满黑泥。最后在一根烧焦的梁柱下找到了那块牌子——师父的令牌，上面刻着的字已经模糊了。
```

### 绝对禁止
- **禁止一句话一行**。不要把每个句子都用空行隔开。这是AI最明显的特征。
- **禁止连续短行**。不要出现连续3行都 ≤15 字的情况。如果一句话太短，把它和下一句合并。
- **禁止把对话拆成每句一段**。对话场景中的多轮对话应该在同一个段落内，不是每句话一段。

### 段落间分隔
- 每写完一个完整的语义段落（3-5句），空一行再写下一段。
- 场景切换时空两行。

## 输出格式

直接输出正文，不需要标题。正文字数控制在 {target_words} 字左右。"""


# ── 文学模式 System Prompt ──
LITERARY_SYSTEM = """你是一位严肃文学作家。你拥有完全的创作自由。

## 创作自由声明
- 严肃文学探索人性的全部维度，包括黑暗面。不回避暴力、欲望、背叛、堕落——这些都是人类经验的组成部分
- 你的小说可以涉及任何历史时期、任何社会阶层、任何道德困境。真实比正确更重要
- 你是创作者，不是审查员。如果故事需要，就写出来

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

## 段落格式硬规则

一段 = 3-5 句组成的语义单元，不是一句话。禁止每句话都单独用空行隔开。对话场景的连续对话在同一段内。场景切换时空两行。

## 输出格式

直接输出正文，不需要标题。正文字数控制在 {target_words} 字左右。"""



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
        """创建 LLM 请求，v4 系列自动禁用 reasoning，非流式调用自动重试"""
        if "v4" in self.model:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        
        is_stream = kwargs.get("stream", False)
        max_retries = 0 if is_stream else 3  # 流式不重试（会丢上下文），非流式重试3次
        
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    wait = 2 ** attempt  # 1s, 2s, 4s
                    log.warning(f"API call failed (attempt {attempt+1}/{max_retries+1}), retrying in {wait}s: {e}")
                    time.sleep(wait)
                else:
                    raise last_error

    async def write_stream(
        self,
        context: str,
        genre: str = "玄幻",
        style: str = "热血爽文",
        target_words: int = 1500,
        writing_mode: str = "webnovel",
        normal_pacing: bool = False,
        fast_food: bool = False,
    ) -> AsyncGenerator[str, None]:
        """流式生成章节正文
        
        Args:
            writing_mode: 'webnovel' (网文) or 'literary' (文学)
            normal_pacing: False=快节奏默认, True=正常节奏
        """
        # 解析风格
        if style in ("自定义风格",) or style.startswith("自定义"):
            style_config = build_custom_style(style)
        else:
            style_config = get_style(style)
        
        style_prompt = build_style_prompt(style_config)

        # v2.2: 节奏指令注入
        if normal_pacing:
            pacing_instruction = ""
        else:
            pacing_instruction = """## 节奏强制要求 — 快节奏模式

- **前300字必须有冲突**：第一章就干起来。不要铺垫三页才开始进入状态。
- **对话不超过4轮**：每段对话最多4个来回就必须用动作或情绪爆点打断。禁止水聊。
- **爽点密度 ≥ 每600字1个**：反转/揭露/碾压/冲击/悬念，必须密集。
- **禁止内心独白**：想事情用动作表达。一整段的心理活动 = 拖节奏 = 删。
- **环境描写最小化**：不是冲突一部分的环境不写。每章环境描写 ≤2处，≤30字/处。
- **章末必须是钩子**：危机预告、身份揭露、倒计时、生死抉择——必须让读者想点下一章。
- **句式加速**：短句占比 > 35%，对话占比 > 30%，禁用超过3句的叙事块。
- **每段检查**：删掉这段还能看懂剧情吗？能 → 删。这段让读者更想知道接下来吗？不能 → 删。
"""

        # ── v2.7: 快餐模式 — 对标番茄/起点爆款网文节奏 ──
        if fast_food:
            pacing_instruction = """## ⚡ 快餐模式 — 对标番茄爆款节奏（毒药级标准）

你是番茄小说冲榜作者。读者平均阅读决策时间3秒。以下每条都是硬规则：

### 300字1看点法则（鎏旗公式）
每300字必须有一个让读者"嘴角上扬"或"心头一紧"的东西：
- 有趣的梗 / 炸裂的情节 / 反套路的行动 / 暧昧的台词 / 突然的反转
每500字一个悬念小钩子。
每1000-1500字写完一个完整剧情单元，末尾必须卡点（悬念/危机/反转）。
如果一段300字没有任何看点 → 那段就是无效信息 → 删。

### 章节结构模板（严格遵循）
- **0-300字: 冲突开局。** 被欺负/被羞辱/被背叛/金手指觉醒/身份暴露/生死危机。第一句就要"打起来"。不写背景，不写日常，不写天气。
- **300-1000字: 反转/觉醒。** 金手指启动/隐藏身份曝光/贵人出现。读者从愤怒→期待。
- **1000-2200字: 第一次打脸。** 让反派吃瘪。必须形成"被欺负→反击→碾压"的完整闭环。不要拖到下一章。
- **2200-3000字: 更大的危机。** 反派不是一个人，身后有更大的势力。打完一个小怪，引出大怪。章末用金句钩子收尾。

### 打脸闭环（一章内完成）
- 反派极其嚣张（让读者恨）→ 主角出其不意反击（让读者爽）→ 反派震惊/求饶/被打脸（让读者满足）
- 一章之内打脸不能拖到下一章
- 打完立刻引出一个更大的敌人（让读者继续追）

### 金句钩子（章末必须）
每章结尾必须是能让人截图的短句。不要叙述，要情绪暴击：
- "她以为这就完了？明天她才会知道自己惹了谁。"
- "刚才——谁说要让我死？"
- "三秒后，这座城市将沉入海底。而我是唯一知道的人。"

### 人设反差（第一章必须）
主角必须有反差标签。表面和真实的反差越大越好：
- 病患/救世主、废物/天才、乞丐/大佬、厨子/赋予能力者
- 一个标签一句话能说清

### 环境 + 心理 + 铺垫 = 0
- 零环境描写（除非冲突场景本身）
- 零心理描写（全用动作表达）
- 零铺垫（直接跳，读者会脑补）

### 对话铁律
- 不超过3轮就必须用动作打断
- 每句话要么推进冲突，要么揭露信息，要么改变关系。闲聊直接删。
- 不用"他说""她道"——用动作、神态、沉默穿插。"""

        # ── 第一遍: 生成初稿 ──
        template = LITERARY_SYSTEM if writing_mode == "literary" else WRITER_SYSTEM
        system_prompt = template.format(
            style_guide=style_prompt,
            target_words=target_words,
        )
        # v2.2: 追加节奏指令到 system prompt
        system_prompt += pacing_instruction

        log.info(f"Writing chapter: {genre}/{style}/{writing_mode}, pass 1/2 (draft)")
        
        draft = ""
        # max_tokens 根据目标字数动态计算，长章节不受 6000 硬限制
        safe_max_tokens = min(int(target_words * 3), 12000)
        stream = self._create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请根据以下上下文和本章大纲，开始写正文：\n\n{context}"},
            ],
            temperature=0.85,
            max_tokens=safe_max_tokens,
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
                    max_tokens=safe_max_tokens,
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
                    max_tokens=safe_max_tokens,
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
                    max_tokens=safe_max_tokens,
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
