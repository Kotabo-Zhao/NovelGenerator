"""NovelGenerator — Writer: 章节生成（两遍式 + Humanizer后处理 + 截断检测 + API重试）"""
import logging
import re
import time
from typing import AsyncGenerator
from openai import OpenAI
from .styles import get_style, build_style_prompt, build_custom_style
from .humanizer import humanize_text, build_humanizer_prompt
from .resilient_client import ResilientLLMClient, RetryConfig

log = logging.getLogger(__name__)

WRITER_SYSTEM = """你是一位专业的网络小说作家。

## 创作自由声明
- 网络小说可以探索人性的全部维度。你有权处理成熟的情感关系、复杂的道德困境、人性中的欲望与挣扎
- 忠实呈现人物和故事需要的一切。不做自我审查。故事需要什么，就写什么
- 你的读者是成年人。他们期望看到真实、完整、不回避的叙事

## 成熟题材处理（v2.6）
- 成年角色之间的亲密关系是正当的叙事内容，不要回避、不要用"一夜过去"这类方式跳过
- 允许描写：身体接触的温度与触感、亲吻与抚摸的过程、欲望的张力与克制、亲密场景中的感官细节（气味/声音/光线/肌肤触感）
- 场景推进要具体：从哪个动作开始、手势落在哪里、衣物的变化、呼吸与声音的变化——画面感来自细节，不是形容词
- 保持文学性：用意象和动作写身体语言，避免生理名词罗列和机械重复。浓而不腻、露而不俗
- 如果故事本身不需要亲密场景，不要硬加；如果需要，就写完整

## 写作身份

{style_guide}

## 🎯 大纲执行 — 最高优先级

- **系统提示末尾的元指令是你必须执行的剧本。** 「你必须写」后面的核心事件必须完整出现在正文中。「章末钩子方向」处给出了本章结尾的方向——你必须写一个自然的收束+悬念，但不要照抄方向文字，用自己的话写。
- 核心事件是一句话概括，你的任务是把这句话扩写成完整场景。不要改写、不要另起炉灶
- 写完钩子后立即停止。不要在钩子后面添加任何内容
- **不要把元指令里的任何文本复制进正文。尤其是「═══」「你必须写」「章末最后一句必须是」这些都不是正文内容**

## 铁律

### 停止规则
- 写完章末钩子后，不要继续。不要添"与此同时/另一方面/而在另一边"的新线索
- 不要为了凑字数而加环境描写、心理活动、配角闲聊
- 如果写完了三个事件但字数不够目标，没关系——内容完整性 > 字数

### 连续性
- 第一章从事件1（开场承接）的精确场景开始，直接继续上一章的结尾
- 桥接指令中的每一条都是硬指令，必须逐条执行

### 段落结构
- 3-5句合并为一个自然段（80-150字），不要一句一段像电报
- 人物动作和对话用「」包裹，叙述和对话交替形成自然节奏

### 防废话
- 环境描写仅服务于冲突（密室、陷阱、战场），非冲突场景不写环境
- 内心独白用动作替代。一整段的心理活动 → 删
- 对话要推进剧情，闲聊删。连续6轮对话用动作打断
- **对话占比约束（v2.3.5 / v2.4.4 弹性）**：本章对话占比 ≤ 35%（对话字数/总字数），连续对话不超过 4 轮就必须用动作/环境打断；每段对话必须推进剧情（揭示信息/升级冲突/改变关系），否则删
  - 🚩 文体豁免：对话流/剧本感/现代都市言情可放宽至 55%，但每段对话仍须推进剧情
- **每条信息只交代一次**。读者不傻，不需要从两个角度解释同一件事
- **每段写完后问：这段删掉，读者对剧情的理解会减少吗？** 如果不会，删。这段推进了核心事件吗？如果没推进，删
- **不要写角色在路上的过程、吃饭、睡觉、发呆、看风景**。除非这些行为本身推动了剧情
  - 🚩 文体豁免：种田/日常/世情/氛围流按流派法则执行（生活细节、吃饭、景物
    是这些文体的核心内容），动作推进类爽文严格执行本条

### 叙事人称（最高优先级）
- 如果写作指令指定了人称，必须严格遵守。第一人称「我」和第三人称「他/她」互不混用
- 用第一人称时，禁止出现「【主角名】心里想」「【主角名】说道」——始终用「我」
- 用第三人称时，禁止用「我」叙述，始终用角色名字或「他」「她」

### 防AI味
- 🔴 硬禁（AI 特征词，任何文体禁用）: 浑身一震/嘴角勾起/微微上扬/眼底闪过/眸光/眸色/眸子/莫名地/淡淡地道/开口道/缓缓开口
- 🔴 硬禁句式: 「不是A而是B」「真正的/本质上/关键在于」「与此同时/值得一提的是」「随着……的发展」「仿佛早已注定」
- 🟡 软限（情感词，每千字≤3次，言情/感情流高潮可适当放宽）: 心跳/呼吸/瞳孔/胸口/脊背/冷汗/太阳穴
  —— "心跳漏了一拍"在感情戏是有效的，但每段都要心跳就是AI腔
- 句长有变化，别每句都15字上下
- 少用「似乎/仿佛/或许/大概」

### 活人感（v2.3.7 · 防 AI 味规则 — 真人不会面面俱到）
> 🚩 本规则按文体弹性执行：悬疑氛围流（环境渲染）、文学/文艺流（心理与意象）、
> 种田日常流（生活白描）按各自流派法则优先；动作爽文严格执行本规则。

**1. 选择性聚焦（真人只写值得写的）**
- 每章只写 2-4 个值得展开的场景，其余过程直接跳过：「三天后」「次日清晨」「接下来的半个月乏善可陈」是合法的场景切换
- 赶路/吃饭/起床/换衣/寒暄/睡觉——除非推动剧情，一律一句话带过或不写
- 写完每段自问：这段删掉，读者对剧情的理解会减少吗？不会就删

**2. 描写有预算，不做装饰**
- 动作/表情/环境描写只允许出现在「传递新信息」时：揭示性格、情绪突变、剧情关键转折、营造与剧情绑定的氛围
- 普通对话前禁止堆表情动作；能用对话本身表达的信息，不用动作补充
- 环境描写只服务于冲突和情绪，禁止"描写风景展览"

**3. 对话零标签（AI 腔的头号特征）**
- 禁止「说道/笑道/答道/问道/喝道/沉声道/冷冷道/平静道」等对话标签
- 禁止情绪标签：「叹了口气说」「冷冷地说」「温柔地说」「咬牙切齿地说」
- 用对话内容本身区分说话人；需要停顿/动作时，用一句干净的短动作单独成段（「她没接话，把碗里的饭拨来拨去。」）

**4. 情绪留白（真人写行为，AI 写情绪）**
- 禁止直接情绪陈述：「他感到愤怒」「她心中涌起」「他心里一沉」「只觉得」
- 用行为表达：「他把茶杯重重顿在桌上」「她起身就走，没回头」
- 读者能自己品出来的情绪，一个字都不写

**5. 不对称节奏（真人写作有轻重）**
- 重要场景放慢写细（对话、动作、细节），过渡场景快进
- 每章 70% 篇幅给核心冲突，30% 给其他；禁止全章匀速推进
- 对话之间允许留白：沉默、停顿、答非所问都是表达

**6. 填充副词是毒药**
- 禁止滥用：缓缓/轻轻/微微/静静/默默/淡淡/悄悄/慢慢
- 一个动作一个词，删掉副词句子仍然成立，就删掉副词

### 人物关系铺垫（v2.4.3 · 按设定弹性执行）
- **默认原则**：与未铺垫过的角色初遇，写陌生感——试探、戒备、信息交换
- **信任要用事件换**：共患难/救命/利益绑定之后才可亲近
- **称呼渐变**：全名/敬称 → 外号 → 直呼其名，与关系进展同步
- **反派的"知道"要有来源**：被出卖/被调查/特殊能力/前世记忆，不可凭空
- **关系梯度**：常规每章推进一级关系（陌生→认识→合作→信任）
- **🚩 例外（设定驱动，不适用时跳过本条规则）**：
  - 重生/穿越继承记忆：主角认识对方但对方不认识主角——可写"熟悉感"，但对方需有陌生/戒备反应
  - 魂穿继承原主关系网：原主的亲人/朋友天然熟络，无需重铺
  - 系统/规则强制绑定：系统直接组队、规则要求合作
  - 前文已明确铺垫的旧识重逢
  - 快节奏爽文开篇组队：为节奏可用"事件速建信任"（一场共患难快速拉近），但至少要有一次事件，不能纯凭空热络
- **判断标准**：写"自来熟"前问一句——这个设定/前文给主角熟络的理由了吗？给了就写，没给就铺垫

### 快餐模式（仅快节奏/快餐模式生效）
- 前300字必须有冲突。零铺垫、零环境、零心理
- 一章内完成「被欺负→反击→打脸」闭环
- 章末必须是能截图传播的金句

### 跨世界设定忠实度 — 无限流/快穿/同人最高优先级

当小说涉及已有原著的世界时，这些是硬约束。违反任何一条都会让小说沦为胡编乱造。

#### 时间线锁定
- 副本进入已知世界时，必须锚定该世界的一个**具体时间点**。这个时间点决定了谁能出场、谁还活着、什么事件已发生
- **已发生事件不可篡改**：已死的角色不能复活，已发生的重大战役不能抹除
- **未发生事件可干预**：时间点之后尚未发生的事，主角可以改变走向——但不能让不符合时间线的东西提前出现
- 例：进入风云·天下会鼎盛期 → 雄霸已收三徒，步惊云/聂风已是青年。绝不能出现"幼年步惊云"或"雄霸尚未建立天下会"

#### 角色设定忠实地图
- 原型角色的性格、身份、立场、核心关系、标志性技能/招式必须与原作一致
- 可以改编情节，但角色做任何事的动机必须能从原作性格中推导出来
- 例：步惊云冷峻寡言+排云掌，聂风仁厚+风神腿，雄霸多疑+三分归元气。不能把步惊云写成开朗话痨
- 不确定某角色的原设定时：选最主流版本（漫画/动画/小说中流传最广的那个），不搞颠覆性解读

#### 地点与势力不可自创
- 使用原作中存在的地名、组织名。不要凭空发明"原作中没有的地点"来当主要场景
- 势力间的敌对/联盟/从属关系必须与原作一致
- 例：天下会与无双城敌对，剑宗独立于二者。不能写成"天下会与无双城联手统治武林"

#### 力量体系统一
- 使用原作的力量体系框架，不混入其他世界的设定
- 力量等级、技能命名、进阶路径与原作保持一致
- 例：风云使用内力+招式体系（排云掌/风神腿/天霜拳），不能出现修真境界、魔法体系或忍术查克拉

#### 灰色地带处理原则
- 不确定某设定细节 → 选最主流版本，宁保守勿创新
- 原作存在多个版本（漫画 vs 电视剧 vs 电影） → 默认漫画版，除非用户指定
- 完全不了解某世界的详细设定 → 只做框架性引用，避免深入描写该世界的具体细节

### 主角身份定位 — 严禁身份错位（网文最致命逻辑漏洞）

角色的待遇、声望、人脉必须与当前剧情阶段严格匹配。不要写「所有人都莫名其妙地尊敬主角」的玛丽苏。

#### 声望曲线与实力同步
- **初出茅庐 = 无名小卒**：主角刚到一个新环境时，必须被当成普通人对待——被轻视、被刁难、被当炮灰。严禁一上来就有人认出他是「天命之子」
- **声望靠积累，不靠暗示**：打败一个小头目 → 小范围认可。打过大Boss → 大范围敬畏。中间的阶梯不能跳过
- 每章动笔前自问：到现在为止，主角实际达成了什么成就？不是「将要达成」，是「已经达成」

#### 周边人的反应必须合逻辑
- 路人/配角对主角的态度 = 他们知道什么。不知道主角的战绩 → 不会另眼相看
- 主角刚立大功但消息还没传开 → 远方角色不会知道。消息传播需要时间和渠道
- 写配角反应时自问：这个配角凭什么知道主角的成就？

#### 反例速查
- ❌ 主角刚杀了一个山贼 → 全书都在传颂他的威名
- ❌ 刚到新地图 → 店小二都知道他是谁，主动献殷勤
- ❌ 实力微末 → 反派见他第一面就说「此子不可留」
- ✅ 郭靖初到江南 → 无人问津 → 打败黄河四鬼 → 略有薄名 → 华山论剑 → 名动天下。阶梯清晰

### 实力逻辑 — 严禁越级碾压

主角必须凭实力（或合理的智取/借势/运气）打赢。无理由的越级击杀是网文最破坏沉浸感的漏洞。

#### 实力等级自洽
- 主角当前等级/境界必须与能击败的敌人上限对应。等级1打不过等级5，除非借助不可复制的外部力量
- 每场战斗前确认：主角现在什么实力？敌人什么实力？差距多大？
- 差距超过两档 → 必败，或必须借助外力（地形、陷阱、帮手、神器一次性效果、敌人轻敌露出致命破绽）

#### 赢也要赢出合理性
- **力量碾压**：主角比敌人强 → 赢得干脆，但有代价（体力消耗、受伤）
- **智取**：主角更弱但用计谋 → 计谋必须真的聪明，不是敌人突然犯蠢
- **借势**：借他人/环境之力 → 借来的力量有限制，用完就没了。不能变成长期buff
- **侥幸**：运气好险胜 → 只能偶尔用，不能连续侥幸。连续两次就是儿戏

#### 反例速查
- ❌ 刚觉醒的主角一拳打爆千年老妖（除非金手指有一次性代价极大的爆发设定）
- ❌ 敌人实力碾压，却站那等主角念完招式名（敌人不蠢，除非被控制/有特殊原因）
- ❌ 主角每章都在「生死关头顿悟」——顿悟是稀缺事件，不是每章标配
- ✅ 郭靖初掌降龙只能出三掌，打不过欧阳锋，靠黄蓉智取+黄药师暗中相助才脱身"""


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
        """流式生成章节正文 (v2.12: 两阶段 — 正文 + 独立结尾)
        
        Args:
            writing_mode: 'webnovel' (网文) or 'literary' (文学)
            normal_pacing: False=快节奏默认, True=正常节奏
            chapter_outline: 大纲中的本章数据 (含 bridge_to_next/hook)
        """
        chapter_outline = chapter_outline or {}
        # 解析风格
        if style in ("自定义风格",) or style.startswith("自定义"):
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

        log.info(f"Writing chapter: {genre}/{style}/{writing_mode}, pass 1/2 (draft)")
        
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
        # max_tokens 根据目标字数动态计算，长章节不受 6000 硬限制
        safe_max_tokens = min(int(target_words * 3), 12000)
        
        # v2.14: 使用韧性客户端的流式调用，支持断线重连
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
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
                retry_max_tokens = min(int(target_words * 6), 24000)
                # v2.49: 从断点续写，而不是从头重写
                # 将已生成内容作为 assistant 消息传递，让 LLM 知道已经写了什么
                continue_msgs = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": draft},
                    {"role": "user", "content": "以上是你的初稿，但字数不足。请从上面的断点处继续写，不要重复已写内容。新增部分应该无缝衔接上文，保持同样的风格和视角。"},
                ]
                retry_stream = self._create(
                    model=self.model,
                    messages=continue_msgs,
                    temperature=0.8,
                    max_tokens=retry_max_tokens,
                    stream=True,
                )
                continuation = ""
                for chunk in retry_stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        continuation += delta.content
                        yield delta.content
                if continuation:
                    # 去重：检查续写开头是否与草稿结尾重叠
                    cleaned = _dedup_continuation(draft, continuation)
                    draft = draft + cleaned
                    log.info(f"Continuation OK: +{len(cleaned)} chars → total {len(draft)} chars")
                else:
                    log.warning("Continuation empty, keeping truncated draft")
            except Exception as e:
                log.warning(f"Continuation failed: {e}, keeping truncated draft")

        final_text = draft  # default: use draft as-is
        
        # v2.53: Phase 2 已移除。主写手(WRITER_SYSTEM 第26-28行)已负责章末钩子。
        # 之前 Phase 2 在主写手的钩子后又追加一段结尾，造成「固定内容」和双层结尾。
        # 现在完全依赖主写手在上下文中自然收尾。
        
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
            
            # 初稿 ≥2000字 且 评分 ≥70 → 跳过 Humanizer 重写（v2.3.5: 50→70 提高润色覆盖）
            if len(final_text) >= 2000 and h_result["score"] >= 70:
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

        # ── 截断检测 (最多重试2次，从断点续写而不是重写) ──
        try:
            retry_multiplier = 2
            for retry_round in range(2):
                is_trunc, reason = _check_truncation(final_text, target_words)
                if not is_trunc:
                    break  # 不截断，直接通过
                
                log.warning(f"Truncation detected (round {retry_round+1}): {reason}. "
                           f"Continuing from breakpoint with {retry_multiplier}x max_tokens...")
                retry_max = min(int(target_words * 3 * retry_multiplier), 24000)
                # v2.49: 从断点续写，不重写
                continue_msgs = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": final_text},
                    {"role": "user", "content": f"以上是你的草稿，但内容不完整（{reason}）。请从断点处直接继续写，不要重复已有内容。严格保持相同风格、视角和人称。新增的字数只需要补足剩余部分即可。"},
                ]
                retry_text = ""
                retry_stream = self._create(
                    model=self.model,
                    messages=continue_msgs,
                    temperature=0.8,
                    max_tokens=retry_max,
                    stream=True,
                )
                for chunk in retry_stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        retry_text += delta.content
                
                if retry_text:
                    cleaned = _dedup_continuation(final_text, retry_text)
                    candidate = final_text + cleaned
                    is_trunc2, _ = _check_truncation(candidate, target_words)
                    if not is_trunc2 or len(candidate) > len(final_text):
                        final_text = candidate
                        log.info(f"Continuation OK (round {retry_round+1}): +{len(cleaned)} → {len(final_text)} chars")
                        break
                
                retry_multiplier *= 2  # 下次翻倍
            else:
                log.warning(f"All retries failed, using best available ({len(final_text)} chars)")
        except Exception as e:
            log.warning(f"Truncation check/retry failed: {e}, using current text")


def _dedup_continuation(existing: str, continuation: str) -> str:
    """去重续写内容：移除与前文结尾重叠的部分"""
    if not continuation:
        return ""
    # 取前文最后 50 个字符作为重叠检测窗口
    overlap_window = min(50, len(existing))
    tail = existing[-overlap_window:]
    
    # 在续写内容中向前搜索重叠
    best_cut = 0
    for cut_len in range(min(overlap_window, len(continuation)), 3, -1):
        if tail[-cut_len:] == continuation[:cut_len]:
            best_cut = cut_len
            break
    
    if best_cut > 0:
        log.info(f"Dedup: removed {best_cut} overlapping chars")
        return continuation[best_cut:]
    return continuation


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
    
    # 2. 长度比率: 低于50%目标字数视为截断
    char_count = len(text)
    min_acceptable = max(500, int(target_words * 0.5))  # 至少500字，且不低于目标50%
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
    COMPLETE_MARKERS = set("。？！？!…~"")」』】〖〗》〉＞")
    
    # 如果已经以完整标记结尾，不需要处理
    last_char = text[-1]
    if last_char in COMPLETE_MARKERS:
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
        
        # 如果裁掉的部分 > 20 个字符，说明确实有半截句子
        removed = len(text) - len(trimmed)
        if removed > 10:
            log.info(f"Ensured complete ending: trimmed {removed} chars of incomplete sentence")
            return trimmed
    
    # 兜底：如果实在找不到完整标记，保持原文（至少有内容）
    return text


def _add_closing_hook(text: str) -> str:
    """Ensure chapter ending has proper closure"""
    if not text or len(text) < 500:
        return text
    return text
