"""NovelGenerator — Humanizer 规则引擎

基于 blader/humanizer (7200★) + AI_Gen_Novel Humanizer-zh
24种中文AI写作痕迹检测 + 自动重写

来源:
- https://github.com/blader/humanizer
- https://github.com/idao-cube/humanizer-zh
- https://github.com/cs2764/AI_Gen_Novel
"""

import re
import logging

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# 24 种 AI 写作痕迹 (中文适配版)
# ═══════════════════════════════════════════

AI_PATTERNS = [
    # ── 内容模式 (6种) ──
    {
        "id": 1,
        "name": "意义夸大",
        "category": "内容",
        "pattern": r"标志着.{0,20}(?:演进|发展|新时代|新纪元|里程碑|新篇章)",
        "fix": "删除夸大修饰，直接陈述事实。如'标志着XX演进中的关键里程碑' → 'XX成立于1989年'"
    },
    {
        "id": 2,
        "name": "靠名头抬高",
        "category": "内容",
        "pattern": r"被(?:众多|无数|大量)(?:媒体|专家|学者|机构).{0,30}(?:报道|评价|认可|赞誉)",
        "fix": "如有具体来源则保留具体来源，否则删除模糊引用"
    },
    {
        "id": 3,
        "name": "肤浅分析",
        "category": "内容",
        "pattern": r"(?:象征着|体现了|反映了|折射出|展现了|代表着).{0,20}(?:深刻|重要|关键|本质)",
        "fix": "删除空话分析，或补上真实的推理过程"
    },
    {
        "id": 4,
        "name": "宣传腔",
        "category": "内容",
        "pattern": r"(?:坐落于|位于).{0,20}(?:风景如画|令人惊叹|美不胜收|壮观|宏伟)(?:的|之)",
        "fix": "用具体描写替代广告式修饰语"
    },
    {
        "id": 5,
        "name": "模糊归因",
        "category": "内容",
        "pattern": r"(?:专家认为|据分析|研究表明|众所周知|人们常说).{0,30}(?:发挥着|起到了|扮演了)",
        "fix": "给出具体来源，或改为角色自身的判断"
    },
    {
        "id": 6,
        "name": "模板展望",
        "category": "内容",
        "pattern": r"尽管.{0,30}(?:面临|存在).{0,30}(?:挑战|困难|问题).{0,20}(?:但|然而|不过).{0,20}(?:依然|仍然|依旧|持续).{0,15}(?:发展|前进|努力|奋斗)",
        "fix": "写出实际的挑战和应对，而非模板句式"
    },

    # ── 语言模式 (6种) ──
    {
        "id": 7,
        "name": "AI高频词汇",
        "category": "语言",
        "pattern": r"(?:此外|与此同时|总而言之|综上所述|换言之|值得一提的是|不容忽视的是|不可否认)",
        "fix": "删除过渡词，用自然的段落衔接"
    },
    {
        "id": 8,
        "name": "系动词回避",
        "category": "语言",
        "pattern": r"(?:作为|充当|具备|拥有|享有|占据)(?!.*(?:了|着|过))",
        "fix": "大胆用'是'和'有'。'作为XX的XX' → '是XX的XX'"
    },
    {
        "id": 9,
        "name": "否定排比",
        "category": "语言",
        "pattern": r"这不仅仅是.{0,30}(?:而是|更是).{0,30}",
        "fix": "直接陈述观点，删除'不仅仅是...更是...'句式"
    },
    {
        "id": 10,
        "name": "三段式执念",
        "category": "语言",
        "pattern": r"(?:.{1,10}(?:、.{1,10}){2,3}(?:和.{1,10}))(?=.*(?:的|之|等))",
        "fix": "不要刻意凑三个并列词。两个或四个也可以"
    },
    {
        "id": 11,
        "name": "同义词轮换",
        "category": "语言",
        "pattern": r"(?:主角|主人公|核心人物|关键角色|主要人物)(?!.{0,5}(?:是|的|在|了|着|过))(?=.*(?:主角|主人公|核心人物|关键角色|主要人物))",
        "fix": "同一人物/事物用同一个称呼，不要怕重复"
    },
    {
        "id": 12,
        "name": "虚假范围",
        "category": "语言",
        "pattern": r"从.{0,20}到.{0,20}(?:再到|乃至|以至于).{0,20}",
        "fix": "只在确实需要表达范围时使用，否则去掉"
    },

    # ── 风格模式 (6种) ──
    {
        "id": 13,
        "name": "破折号滥用",
        "category": "风格",
        "pattern": r"——.{10,60}——",
        "fix": "破折号每章不超过3处。用句号分句替代"
    },
    {
        "id": 14,
        "name": "环境描写开章",
        "category": "风格",
        "pattern": r"^(?:阳光|月光|天空|大地|风|雨|雪|雾|云).{0,50}(?:之下|之中|之上).{0,20}$",
        "fix": "不要每章开头都用环境描写。用动作/对话/冲突开章"
    },
    {
        "id": 15,
        "name": "每段结尾感叹号",
        "category": "风格",
        "pattern": r"！\s*$",
        "fix": "感叹号密度不超过每3段1个。陈述句用句号"
    },
    {
        "id": 16,
        "name": "整齐段落",
        "category": "风格",
        "fix": "段落长度要参差不齐。2句→6句→3句→8句 交替"
    },
    {
        "id": 17,
        "name": "结尾总结句",
        "category": "风格",
        "pattern": r"(?:这就是|这便是|这就是所谓|这正是).{0,30}(?:真谛|意义|本质|所在)",
        "fix": "不要替读者总结。让事件自己说话"
    },
    {
        "id": 18,
        "name": "说教腔",
        "category": "风格",
        "pattern": r"(?:真正的|真正的.{0,10}在于|最重要的.{0,10}是).{0,40}",
        "fix": "叙述中不要跳出作者身份说教"
    },

    # ── 交流模式 (6种) ──
    {
        "id": 19,
        "name": "上帝视角评价",
        "category": "交流",
        "pattern": r"(?:殊不知|然而.{0,5}不知道的是|他.{0,5}并不知道).{0,40}",
        "fix": "减少全知叙述者的评价，让角色自己发现"
    },
    {
        "id": 20,
        "name": "过度解释",
        "category": "交流",
        "pattern": r"(?:换句话说|也就是说|这意味着|这表示).{0,30}",
        "fix": "说一遍就够了。如果读者没懂，那是写得不够好，不是解释不够多"
    },
    {
        "id": 21,
        "name": "填充短语",
        "category": "交流",
        "pattern": r"(?:在某种(?:程度|意义)上|从某种(?:角度|层面)来说|某种程度上)",
        "fix": "全部删除。这些词没有任何信息量"
    },
    {
        "id": 22,
        "name": "过度限定",
        "category": "交流",
        "pattern": r"(?:似乎|仿佛|好像|犹如|宛如).{0,5}(?=是|在|有|会|能)",
        "fix": "减少模糊词。'似乎是' → '是'。不确定的事用角色视角而非叙述者视角表达"
    },
    {
        "id": 23,
        "name": "万能结论",
        "category": "交流",
        "pattern": r"(?:或许|也许|可能).{0,30}(?:这才是|这就是|那便是).{0,20}(?:真正|最终|最好|唯一)",
        "fix": "结尾不要给万能结论。开放式结尾比大团圆总结更有力"
    },
    {
        "id": 24,
        "name": "AI对话标注",
        "category": "交流",
        "pattern": r"(.{2,10})(?:说|道|问|答|喊|叫|吼|喝)(?:道|着)?(?!.{0,3}(?:。|！|？|……|\"|'|」))",
        "fix": "不要每句对话都用'XX说/道'标注。用动作和神态穿插"
    },

    # ── 第5阶段: 中文小说特有问题 (来自 rn-renhua + stop-slop + humanize 9 levers) ──
    {
        "id": 25,
        "name": "二元对比壳",
        "category": "内容",
        "pattern": r"(?:不是|并非|不在于)(?:.{0,15})(?:而是|而在于)|不只是.{0,15}(?:更是|更是……{0,5})|与其.{0,15}不如",
        "fix": "直接陈述判断，不要用'不是A而是B'的对比结构"
    },
    {
        "id": 26,
        "name": "伪洞察标记",
        "category": "内容",
        "pattern": r"(?:真正的|本质上|核心在于|关键在于|说白了|归根结底|更重要的是|这背后|这说明)(?!.{0,3}(?:问题|原因|事实|情况))",
        "fix": "删除这些提示词，直接进入判断或事实"
    },
    {
        "id": 27,
        "name": "讲义冒号",
        "category": "风格",
        "pattern": r"(?:原因(?:很简单|是)|结论是|重点是|分成.{1,4}类)[：:]",
        "fix": "改成普通叙述句，不用冒号引出答案"
    },
    {
        "id": 28,
        "name": "空洞金句结尾",
        "category": "风格",
        "pattern": r"(?:这不仅仅是.{0,10}更是|这标志.{0,10}里程碑|真正的.{0,5}从来都不|在这一刻.{0,10}(?:明白|懂得|知道))",
        "fix": "结尾用具体动作或悬念收束，不要万能金句"
    },
    {
        "id": 29,
        "name": "抽象压力叙事",
        "category": "内容",
        "pattern": r"(?:差距.{0,6}(?:拉开|扩大)|成为.{0,4}分水岭|时代.{0,4}(?:变|不同)|格局.{0,4}(?:改变|重塑))",
        "fix": "不写空泛的威胁描述。写具体的失败场景或损失"
    },
    {
        "id": 30,
        "name": "AI高频过渡词",
        "category": "语言",
        "pattern": r"(?:与此同时|在这个过程中|此外|值得一提的是|总的来看|随着.{0,8}的发展|值得注意的是|从某种意义上说|在当今时代)",
        "fix": "删除这些过渡词。分段直接开始新内容"
    },
    {
        "id": 31,
        "name": "模糊感悟句",
        "category": "语言",
        "pattern": r"(?:似乎|仿佛|或许|大概|也许).{0,10}(?:明白了|懂得了|知道|感受到|意识到|触动了)",
        "fix": "用具体动作代替模糊感悟。'他顿了顿'比'他仿佛明白了'有力"
    },
    {
        "id": 32,
        "name": "破折号过量",
        "category": "风格",
        "pattern": r"—",
        "fix": "每500字最多一个破折号。多余的全部改为句号或逗号"
    },
    {
        "id": 33,
        "name": "三段式执念",
        "category": "风格",
        "pattern": r"^(.+)(?:。|！|？|……)\n\n(.+)(?:。|！|？|……)\n\n(.+)(?:。|！|？|……)$",
        "fix": "段落长度要有变化。偶尔用单句成段打破节奏"
    },
    {
        "id": 34,
        "name": "的的不绝",
        "category": "语言",
        "pattern": r"(?:的.{0,8}){4,}",
        "fix": "减少'的'字密度。长定语拆成短句"
    },
    {
        "id": 35,
        "name": "说教腔",
        "category": "交流",
        "pattern": r"(?:你要知道|要记住|别忘了|记住|你要明白|必须承认|不得不承认)",
        "fix": "不要对读者说教。用人物内心独白或行动展示"
    },
    {
        "id": 36,
        "name": "每个段落感叹号",
        "category": "风格",
        "pattern": r"！.{0,50}\n\n.{0,50}！",
        "fix": "感叹号是调料不是主菜。克制使用，让动作和沉默说话"
    },
    {
        "id": 37,
        "name": "AI句式平行",
        "category": "语言",
        "pattern": r"(.{5,15})(?:的|地|得)(.{5,15})(?:，{0,2}|、)(.{5,15})(?:的|地|得)(.{5,15})",
        "fix": "打破平行句式。长短交错，不要两句一样节奏"
    },

    # ── 第6阶段: 中文网络小说特有AI痕迹 (v2.14新增) ──
    {
        "id": 38,
        "name": "万能身体反应",
        "category": "内容",
        "pattern": r"(?:太阳穴突突|胸口一紧|心口一紧|手心出汗|心跳加速|心跳漏了一拍|呼吸急促|呼吸一滞|倒吸一口凉气|脊背发凉|后背一凉|胃里翻江倒海|瞳孔骤缩|瞳孔猛地一缩|额角渗出冷汗|冷汗直流|浑身一震|虎躯一震)",
        "fix": "用角色独有的动作表达情绪。'太阳穴突突跳' → '他把茶杯转了四圈才端起来喝'"
    },
    {
        "id": 39,
        "name": "陈词意象",
        "category": "风格",
        "pattern": r"(?:嘴角勾起一抹|眼底闪过一丝|目光如炬|目光如电|空气仿佛凝固|时间仿佛静止|这一刻.{0,5}明白了)",
        "fix": "用具体的、个人化的动作替代。'嘴角勾起一抹冷笑' → '他看着那张脸看了三秒，转身就走'"
    },
    {
        "id": 40,
        "name": "重复确认对话",
        "category": "交流",
        "pattern": r"[「」""].{2,8}[」」""].{0,10}[「「""].{2,8}[」」""].{0,10}[「「""].{2,8}[」」""]",
        "fix": "对话不超过4轮必须用动作打断。删掉确认性废话（'你说什么？''你确定？'）"
    },
    {
        "id": 41,
        "name": "空洞升级描写",
        "category": "内容",
        "pattern": r"(?:实力暴涨|修为大增|境界突破|力量飙升|气势暴涨|实力突飞猛进)(?!.{0,10}(?:的|了|，|。))",
        "fix": "不要用'实力暴涨'这种空泛词。写具体变化：能举起多重的石头？速度快了多少？能看到多远的敌人？"
    },
    {
        "id": 42,
        "name": "万能反派嘲讽",
        "category": "内容",
        "pattern": r"(?:就凭你|不自量力|螳臂当车|蚍蜉撼树|区区|雕虫小技|班门弄斧|不知死活|找死)",
        "fix": "反派不要只会说成语嘲讽。让反派用行动展示实力差距，而非嘴上说说"
    },
    {
        "id": 43,
        "name": "内心独白过长",
        "category": "风格",
        "pattern": r"(?:他|她|它)想[，。:：].{80,}",
        "fix": "内心独白不超过3句。用动作表达纠结：'他顿了顿'比'他心想...'有力"
    },
    {
        "id": 44,
        "name": "场景切换生硬",
        "category": "风格",
        "pattern": r"\n\n(?:此时|与此同时|另一边|在.{2,8}那边|话说)",
        "fix": "场景切换用空行+环境细节过渡，不要用'另一边'这种AI过渡词"
    },
    {
        "id": 45,
        "name": "过度铺垫",
        "category": "内容",
        "pattern": r"(?:众所周知|在这片大陆上|在这个世界里|自古以来|传说中)(?!.{0,5}(?:的|是|，))",
        "fix": "世界观融入剧情，不要用旁白式说明。读者通过角色行动理解世界规则"
    },
]


def detect_ai_patterns(text: str) -> list:
    """检测文本中的 AI 写作痕迹
    
    Returns:
        [{id, name, category, count, examples: [匹配到的文本片段]}]
    """
    results = []
    for pattern in AI_PATTERNS:
        if "pattern" not in pattern:
            continue
        matches = re.findall(pattern["pattern"], text)
        if matches:
            results.append({
                "id": pattern["id"],
                "name": pattern["name"],
                "category": pattern["category"],
                "count": len(matches),
                "examples": list(set(matches))[:3],
            })
    return results


def _get_fix(pattern: dict) -> str:
    """获取修复建议，兼容 AI_PATTERNS 内外"""
    if pattern["id"] == 99:
        return f"句长单调: {pattern['examples'][0] if pattern.get('examples') else '句长变化不够大'}。让短句更短(≤8字)，长句更长，交替出现"
    idx = pattern["id"] - 1
    if 0 <= idx < len(AI_PATTERNS):
        return AI_PATTERNS[idx]["fix"]
    return f"减少{pattern['name']}的出现"


def build_humanizer_prompt(detected: list) -> str:
    """根据检测结果构建 Humanizer 润色提示"""
    if not detected:
        return ""

    summary = "\n".join(
        f"- [{p['name']}] 出现{p['count']}次 (类别: {p['category']})"
        for p in detected[:10]
    )

    # 取前6个最严重的模式的具体 fix 建议
    fixes = "\n".join(
        f"{i+1}. {_get_fix(p)}"
        for i, p in enumerate(detected[:6])
    )

    return f"""## Humanizer 检测结果

发现以下 AI 写作痕迹:

{summary}

## 修改要求

请重写以下文本，修复上述问题:

{fixes}

## 重要原则

- 有观点——不要只报告事实，要对事件做出反应
- 变化节奏——长短句交替，不要所有段落都一样长
- 承认复杂性——真实的人有复杂的感受和矛盾的想法
- 允许一些混乱——完美的结构反而显得机械
- 对感受要具体——用具体的感官细节替代抽象的情绪概括

## 故事连贯性保护（最高优先级）

- **不得删除任何剧情事件、角色对话、关键转折**
- **不得改变角色性格、关系、动机**
- **不得新增原文没有的情节或角色**
- **只改写表达方式，不改写故事内容**
- **保留原文的伏笔、钩子、悬念结构**
- **对话内容可以调整语气但不能改变信息量**
- **如果一段文字已经很自然，不要为了改而改**

直接输出修改后的文本，不需要标注修改了哪里。"""


def humanize_text(text: str) -> dict:
    """分析文本并生成 Humanizer 提示
    
    Returns:
        {"detected": [...], "prompt": "...", "score": 0-100 (越低AI味越重)}
    """
    detected = detect_ai_patterns(text)
    
    # ── 句长分析 (burstiness) ──
    burst_issues = _analyze_burstiness(text)
    if burst_issues["violations"]:
        detected.append({
            "id": 99,
            "name": "句长单调",
            "category": "风格",
            "count": burst_issues["violations"],
            "examples": burst_issues["examples"][:3],
        })
    
    total_issues = sum(p["count"] for p in detected)
    
    # ── v2.14: 分类别统计 ──
    category_stats = {}
    for p in detected:
        cat = p["category"]
        category_stats[cat] = category_stats.get(cat, 0) + p["count"]
    
    # 综合评分: pattern detections + burstiness + 类别权重
    word_count = len(text)
    issue_density = total_issues / max(word_count, 1) * 100
    score = max(0, 100 - int(issue_density * 20))
    
    # 破折号惩罚: 每500字超过1个扣3分
    dash_count = text.count("—")
    dash_budget = max(1, word_count // 500)
    if dash_count > dash_budget:
        score = max(0, score - (dash_count - dash_budget) * 2)
    
    # v2.14: 高严重度模式额外扣分
    HIGH_SEVERITY_IDS = {38, 39, 43, 44}  # 万能身体反应、陈词意象、内心独白过长、场景切换生硬
    MEDIUM_SEVERITY_IDS = {7, 9, 25, 26, 28, 30, 41, 42}  # AI高频词、否定排比、二元对比壳等
    
    for p in detected:
        if p["id"] in HIGH_SEVERITY_IDS:
            score = max(0, score - p["count"] * 3)
        elif p["id"] in MEDIUM_SEVERITY_IDS:
            score = max(0, score - p["count"] * 1)
    
    # v2.14: 评分等级
    if score >= 80:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 40:
        grade = "C"
    elif score >= 20:
        grade = "D"
    else:
        grade = "F"
    
    return {
        "detected": detected,
        "prompt": build_humanizer_prompt(detected),
        "score": score,
        "grade": grade,
        "total_issues": total_issues,
        "word_count": word_count,
        "burstiness": burst_issues,
        "category_stats": category_stats,
    }


def _analyze_burstiness(text: str) -> dict:
    """分析句长变化（突发性检查）
    
    每150字至少一个短句(≤8字)
    连续三句长度差不超过6字
    """
    # 按句号/感叹号/问号分句
    sentences = re.split(r'[。！？……\n]', text)
    sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 1]
    
    violations = 0
    examples = []
    
    # 检查1: 连续三句长度过于接近
    for i in range(len(sentences) - 2):
        s1, s2, s3 = len(sentences[i]), len(sentences[i+1]), len(sentences[i+2])
        if abs(s1 - s2) <= 3 and abs(s2 - s3) <= 3:
            violations += 1
            if len(examples) < 3:
                examples.append(f"连续三句长度相近({s1},{s2},{s3}字)")
    
    # 检查2: 每150字是否有短句
    char_count = 0
    has_short = False
    block_start = 0
    for s in sentences:
        char_count += len(s)
        if len(s) <= 8:
            has_short = True
        if char_count >= 150:
            if not has_short:
                violations += 1
                if len(examples) < 3:
                    examples.append(f"150字内无短句(≤8字)")
            char_count = 0
            has_short = False
    
    return {"violations": violations, "examples": examples}
