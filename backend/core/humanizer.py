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


def build_humanizer_prompt(detected: list) -> str:
    """根据检测结果构建 Humanizer 润色提示"""
    if not detected:
        return ""

    summary = "\n".join(
        f"- [{p['name']}] 出现{p['count']}次 (类别: {p['category']})"
        for p in detected[:8]
    )

    # 取前5个最严重的模式的具体 fix 建议
    fixes = "\n".join(
        f"{i+1}. {AI_PATTERNS[p['id']-1]['fix']}"
        for i, p in enumerate(detected[:5])
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

直接输出修改后的文本，不需要标注修改了哪里。"""


def humanize_text(text: str) -> dict:
    """分析文本并生成 Humanizer 提示
    
    Returns:
        {"detected": [...], "prompt": "...", "score": 0-100 (越低越好)}
    """
    detected = detect_ai_patterns(text)
    total_issues = sum(p["count"] for p in detected)
    
    # 评分: 100 = 完全干净, 0 = 重灾区
    # 按万分比计算: 每100字出现1个问题扣1分，封顶100
    word_count = len(text)
    issue_density = total_issues / max(word_count, 1) * 100
    score = max(0, 100 - int(issue_density * 20))
    
    return {
        "detected": detected,
        "prompt": build_humanizer_prompt(detected),
        "score": score,
        "total_issues": total_issues,
        "word_count": word_count,
    }
