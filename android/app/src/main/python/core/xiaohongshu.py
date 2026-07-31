"""NovelGenerator — Xiaohongshu Viral Short Story Mode v3.0

小红书爆款短篇小说生成器。基于2026年7月全平台数据调研全面重写。

核心变更 (v3.0):
  - 4套模板全部替换为小红书原生爆款模式（训狗/修罗场/反套路荒诞/女尊）
  - 删除传统女频套路（打脸逆袭/追妻火葬场/娇妻甜宠）——这些在小红书已彻底失效
  - 新增标签引擎：标签不再只是元数据，是内容本身和流量通道
  - 支持三种字数模式: 超短(300-1000字) / 短篇(1000-3000字) / 中篇(3000-8000字)
  - 权力倒置为核心叙事引擎：男主永远处于情感低位
  - 零铺垫、零世界观、前3句亮底牌

调研结论参考: outputs/小红书爆火小说核心要素调研报告.html
"""

import json
import logging
from typing import Optional

from openai import OpenAI

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# v3.0 模板定义 — 小红书原生爆款
# ═══════════════════════════════════════════════

TEMPLATES = {
    "xun_gou": {
        "key": "xun_gou",
        "label": "训狗文学·权力反转",
        "emoji": "🐕",
        "description": "核心爽点：女主用任何手段（系统/身份/知识碾压）把强势男主驯成忠犬。男主从高高在上到跪地求饶。当前小红书TOP1赛道。",
        "target_tags": ["训狗文学", "占有欲男主", "强制爱(性转版)", "男主情感低位", "嗲夫文学"],
        "taboo_tags": ["娇妻文学", "虐女", "女主讨好男主", "男主和其他女人纠缠"],
        "genre": "女频权力反转",
        "style": "小红书权力倒置体",
        "power_dynamic": "女主绝对主导 → 男主从高位逐级跌落 → 最终情感臣服",
        "emotion_curve": "嚣张→困惑→崩塌→卑微→彻底臣服",
        "target_words_short": 800,
        "target_words_medium": 2500,
        "target_words_long": 6000,
        "chapters": [
            {
                "number": 1, "title": "嚣张",
                "function": "男主装逼登顶",
                "guide": "男主极端强势/冷漠/掌控一切。女主看似弱势但手握底牌（系统/身份/把柄）。让读者产生「看你还能嚣张多久」的期待感。开头3句亮底牌。",
                "hook": "他掐着我的下巴说「你不过是我养的一条狗」。我笑了笑——系统弹窗：『目标情绪值-100，宿主获得反制权限×1』。原来谁才是狗，还说不定。",
                "words": 600
            },
            {
                "number": 2, "title": "裂痕",
                "function": "权力转移启动",
                "guide": "女主的底牌第一次生效。男主第一次失态。权力天平开始倾斜。男主从不屑到困惑到恐慌。关键：不能让男主立刻变忠犬，要一级一级跌落。",
                "hook": "他第一次主动来找我，站在门口犹豫了三秒才敲门。我头也不抬：「跪下说话。」他愣住了。我说：「你听错了，我的意思是——爬过来。」",
                "words": 600
            },
            {
                "number": 3, "title": "崩塌",
                "function": "★ 情绪爆破点",
                "guide": "男主彻底崩溃，当众出丑或私下崩溃。骄傲被完全碾压。读者最爽的一章。必须有「男主跪了」的名场面。同时展示男主崩溃后第一次展示脆弱。",
                "hook": "他在公司例会上当众跪下。三百名员工鸦雀无声。他颤抖着说：「求你，回来。」我踩着高跟鞋走向门口，头也不回：「求人要有求人的态度——跪着把事情说清楚。」",
                "words": 600
            },
            {
                "number": 4, "title": "臣服",
                "function": "权力永久锁定",
                "guide": "男主彻底接受从属地位。不是委屈的屈服，而是从内心接受并且以此为乐。反转：男主发现自己喜欢被女主掌控。给读者最极致的权力快感。",
                "hook": "",
                "words": 600
            }
        ],
        "typical_hooks": [
            "系统让我驯服偏执太子，他红着眼眶跪在龙袍上求我别走",
            "我把CEO训成修狗后，他每天在我办公室门口等投喂",
            "校霸当众把我堵在墙角，我反手亮出他的黑历史——他跪了"
        ]
    },

    "xiuluochang": {
        "key": "xiuluochang",
        "label": "修罗场·多男主争夺",
        "emoji": "🔥",
        "description": "核心爽点：多个高质量男性围绕女主展开争夺。女主是选择者而非被选择者。每个男主被拒绝后越陷越深。当前小红书互动率最高赛道。",
        "target_tags": ["修罗场", "多男主", "乙女向", "女主主导", "占有欲男主", "自卑男"],
        "taboo_tags": ["女主主动讨好", "虐女", "三角恋虐女主", "女主被抛弃"],
        "genre": "女频修罗场",
        "style": "小红书权力倒置体",
        "power_dynamic": "N个男主争女主 → 女主冷眼旁观 → 逐个被虐 → 最终挑一个（或不挑）",
        "emotion_curve": "傲慢→吃醋→崩溃→卑微→接受（或永不得）",
        "target_words_short": 800,
        "target_words_medium": 2500,
        "target_words_long": 6000,
        "chapters": [
            {
                "number": 1, "title": "围猎",
                "function": "男主们登场",
                "guide": "至少2个男主登场，各有千秋（霸总/校草/竹马/反派等）。他们初始对女主不屑或利用。女主冷眼旁观，不急不躁。",
                "hook": "三个男人同时把合同拍在我桌上。一个出价五千万，一个承诺半个集团，一个红了眼眶：「我把自己给你。」我端起茶杯：「排好队，一个一个来。」",
                "words": 600
            },
            {
                "number": 2, "title": "拉扯",
                "function": "暗流涌动",
                "guide": "男主们开始暗中较劲。展示每个男主的优势和软肋。女主游刃有余地周旋。让读者磕CP的同时感受到女主的主宰感。",
                "hook": "他把竞争对手堵在停车场：「离她远点。」对方冷笑：「你先问问她愿不愿意见你吧——昨天你打了三十个电话，她一个都没接。」",
                "words": 600
            },
            {
                "number": 3, "title": "决斗",
                "function": "★ 情绪爆破点",
                "guide": "男主们正面冲突。女主在场或不在场但要造成决定性的影响。至少一个男主当场崩溃。展示女主的选择权——她才是掌控者。",
                "hook": "他在我面前撕毁了对家的收购协议。五百个亿的生意，他看了一眼就撕了：「我选你。」我看着他：「谁说你可以选了？选择权在我，不在你。」",
                "words": 600
            },
            {
                "number": 4, "title": "定局",
                "function": "权力归属",
                "guide": "女主的最终选择（选一个 / 都不选 / 全收）。被拒绝的男主崩溃场面是核心爽点。结局必须明确——小红书读者不买账开放式结局。",
                "hook": "",
                "words": 600
            }
        ],
        "typical_hooks": [
            "四个前任同时出现在我的婚礼上，新郎淡定掏出一份排班表",
            "修罗场炸了：死对头、竹马、顶流导师同时向我表白",
            "我假装失忆后，三个男人争着编造和我的恋爱史"
        ]
    },

    "absurdist": {
        "key": "absurdist",
        "label": "反套路荒诞·认知崩塌",
        "emoji": "🌀",
        "description": "核心爽点：让读者发出「这他妈也能？？？」的笑声。万物成精、设定解构、逻辑本身就是笑点。对标「雪山救狐狸」酱板鸭复仇。传播力最强赛道。",
        "target_tags": ["反套路", "沙雕", "脑洞大开", "搞笑", "荒诞", "万物成精"],
        "taboo_tags": [],
        "genre": "反套路荒诞",
        "style": "小红书荒诞体",
        "power_dynamic": "正常认知 → 第一次崩塌 → 层层崩塌 → 彻底荒诞但逻辑自洽",
        "emotion_curve": "正常→困惑→大笑→拍大腿→转发",
        "target_words_short": 500,
        "target_words_medium": 1500,
        "target_words_long": 4000,
        "chapters": [
            {
                "number": 1, "title": "正常开局",
                "function": "建立正常认知",
                "guide": "用最正常的场景开头（职场/家庭/恋爱/日常）。一切都看起来很合理。让读者以为在看一个普通故事。埋第一个不合理的小细节（容易被忽略）。",
                "hook": "公司新来的实习生特别勤快，每天早上帮所有人倒咖啡。直到有一天我加班到凌晨三点——整栋楼都黑了，茶水间的咖啡机自己在磨豆子。上面贴着一张纸条：「今天轮到我值夜班——咖啡机。」",
                "words": 400
            },
            {
                "number": 2, "title": "裂缝",
                "function": "认知第一次崩塌",
                "guide": "正常世界开始出现裂缝。不合理的事情越来越多。但主角和周围人都努力用「合理的解释」覆盖。让读者又困惑又想笑。",
                "hook": "财务总监突然跪在打印机面前磕头。我问他在干嘛，他说打印机说如果他不磕头就把他上个月做假账的证据发给税务局。「你信打印机的话？」我问。打印机吐出一张纸——上面是财务总监的全部通话记录。",
                "words": 400
            },
            {
                "number": 3, "title": "崩塌",
                "function": "★ 笑点爆破点",
                "guide": "逻辑彻底崩塌。荒诞到达顶点。万物开始明着成精/说话/报仇/谈恋爱。但保持一本正经的叙述语气——越正经越好笑。",
                "hook": "周一早会，老板宣布公司被收购了。新老板走进来——是我的办公椅。它清了清嗓子：「各位同事，我知道你们很不适应。但你们想想——是谁每天被你们的屁股压十个小时还要忍气吞声？今天开始，换我坐你们。」",
                "words": 400
            },
            {
                "number": 4, "title": "成精",
                "function": "荒诞闭环",
                "guide": "荒诞成为新常态。结尾要有一个「仔细一想居然很合理」的回味。最后一句必须是金句，适合截图传播。",
                "hook": "",
                "words": 300
            }
        ],
        "typical_hooks": [
            "我入职后发现全公司除了我都是AI，连茶水间的咖啡机都有KPI",
            "房东说房租涨价是因为房子自己在还房贷，我不信——晚上听见墙壁在哭",
            "绣花针成精开了一家裁缝铺，顾客全是古代穿越来的鬼魂"
        ]
    },

    "nvzun": {
        "key": "nvzun",
        "label": "女尊·绝对掌控",
        "emoji": "👑",
        "description": "核心爽点：从世界观层面确立女性绝对主导。可以是真实女尊世界、系统设定的女尊规则、或现代权力倒置。男主从出生就被定义为从属。适合追求极致爽感的读者。",
        "target_tags": ["女尊", "无男主(可选)", "女主绝对主导", "四爱", "性别权力倒置"],
        "taboo_tags": ["虐女", "娇妻", "女主受制于男", "男主出轨"],
        "genre": "女尊权力文学",
        "style": "小红书权力倒置体",
        "power_dynamic": "先天优势 → 展示力量 → 碾压挑衅 → 建立新秩序",
        "emotion_curve": "压制→反抗→碾压→驯服→统治",
        "target_words_short": 800,
        "target_words_medium": 2500,
        "target_words_long": 6000,
        "chapters": [
            {
                "number": 1, "title": "秩序",
                "function": "展示女尊世界",
                "guide": "快速建立女尊世界观（可以是架空/系统/现代反转）。展示女性在权力/经济/社会层面的绝对优势。男主或男性群体处于从属地位。不要解释太多——用场景展示。",
                "hook": "新皇登基那天，礼部侍郎颤颤巍巍地问我：「陛下，按照祖制，后宫最多九十九位皇夫，您现在只有三十二位，是不是……太少了？」我想了想：「传朕旨意——明天开始，全国选秀。标准只有一条：好看。」",
                "words": 600
            },
            {
                "number": 2, "title": "挑衅",
                "function": "遭遇反抗",
                "guide": "某个男性角色（男主/反派）试图挑战女尊秩序。他可能是外来者/穿越者/觉醒者。女主冷静应对。这是展示女主智慧和力量的章节。",
                "hook": "朝堂上，北境来的将军指着我的龙椅说：「女人也配坐这个位置？」满朝文武倒吸一口凉气。我把玩着手里的玉玺，轻笑一声：「你北境的粮草，是我朝女商队供的。你军中的武器，是我朝女工匠造的。你身上这件铁甲，是我朝女铁匠打的。——你现在告诉我，谁不配？」",
                "words": 600
            },
            {
                "number": 3, "title": "碾压",
                "function": "★ 力量展示",
                "guide": "女主展示真正实力。不是打嘴炮，是用实际行动碾压反抗者。展示女尊体系的运行逻辑——为什么女性主导是合理的。",
                "hook": "我让兵部调出北境三十年的军需账本。每一笔粮草、每一把刀剑、每一匹战马——经手人全是女人。我把账本砸在他脸上：「你的将士吃的是女人种的粮，拿的是女人打的刀——然后你来告诉我女人不配？来人，把他盔甲扒了。既然他看不起女人的东西，那就光着身子打他的仗。」",
                "words": 600
            },
            {
                "number": 4, "title": "新天",
                "function": "秩序巩固",
                "guide": "反抗被彻底粉碎。女尊秩序得到强化。胜利后的从容和余韵。男主/反抗者从反抗到臣服的心理转变（可选，如果走感情线）。",
                "hook": "",
                "words": 600
            }
        ],
        "typical_hooks": [
            "穿越女尊世界第一天，我作为女帝收到了九十九份和亲折子——全是男人求嫁",
            "全球男女地位互换后，男同事哭着问我为什么抢他的升职名额",
            "女尊系统降临：所有男性的武力值锁定为1，女性的智力值×100"
        ]
    }
}

# ═══════════════════════════════════════════════
# 标签引擎 — 小红书分发核心
# ═══════════════════════════════════════════════

XHS_TAG_POOL = {
    "power_reversal": ["训狗文学", "男主情感低位", "占有欲男主", "嗲夫文学", "强制爱(性转版)", "先婚后爱(性转版)"],
    "multi_male": ["修罗场", "多男主", "乙女向", "女主主导", "自卑男", "忠犬男主"],
    "absurdist": ["反套路", "沙雕", "脑洞大开", "荒诞", "搞笑", "万物成精", "认知崩塌"],
    "female_dominant": ["女尊", "女主绝对主导", "四爱", "性别权力倒置", "大女主", "无男主"],
    "emotional": ["甜虐交织", "双向暗恋(女主主导)", "强制爱", "眼神杀", "占有欲"],
    "high_concept": ["系统", "穿书", "重生(性转版)", "无限流", "悬疑反转", "规则怪谈"],
    "forbidden": ["娇妻文学", "虐女", "女主讨好男主", "男主出轨", "替身(原版)", "追妻火葬场(原版)"],
}

def get_template_tags(template_key: str) -> dict:
    """获取模板对应的标签集，包含推荐标签和禁忌标签"""
    tpl = TEMPLATES.get(template_key, {})
    return {
        "recommended": tpl.get("target_tags", []),
        "forbidden": tpl.get("taboo_tags", []) + XHS_TAG_POOL["forbidden"],
        "emotional_keywords": ["爽", "炸", "好磕", "上头", "绝了", "笑死", "离谱", "逆天"],
    }

# ═══════════════════════════════════════════════
# v3.0 大纲生成
# ═══════════════════════════════════════════════

XHS_PLANNER_SYSTEM = """你是一个小红书爆款短篇小说策划。你的任务是为AI生成提供精确大纲。

## 核心读者画像
- 目标读者：18-28岁女性，碎片时间阅读（上厕所/排队/睡前5分钟）
- 阅读习惯：先看标题→扫标签→看前三句→决定是否读完
- 核心需求：即时情绪交付——5分钟内必须爽完
- 平台调性：反传统女频、反对虐女、反对娇妻、主张女性主导

## 当前模板设定
- 模板名：{template_label}
- 模板描述：{template_description}
- 权力动态：{power_dynamic}
- 情绪曲线：{emotion_curve}
- 字数：约 {target_words} 字，{num_chapters} 章
- 必须打的标签：{recommended_tags}
- 必须避开的标签：{forbidden_tags}

## 各章功能
{chapter_functions}

## 绝对铁律（必须遵守，违反任何一条大纲作废）

### 标签纪律
1. 标题必须包含至少一个 {recommended_tags} 中的标签词
2. 严禁出现 {forbidden_tags} 中的任何元素

### 权力结构纪律
3. 女主永远是权力关系的上位方。男主永远处于情感低位
4. 女主的「爽」不来自被爱，而来自「被臣服」
5. 男主可以强（能力/地位），但在女主面前必须卑微

### 节奏纪律
6. 第1章前{opening_words}字必须建立冲突。ZERO铺垫、ZERO世界观
7. 每章结尾必须有让读者「必须翻下一页」的钩子
8. 全书必须有至少一个「可以截图传播的名场面」
9. 对话占比 > 30%，描写占比 < 20%

### 内容纪律
10. 禁止虐女主。女主的痛苦只能是「过去时」（概述已发生），不能在正文中展开
11. 禁止男主和其他女性有暧昧（这是小红书读者最敏感的雷）
12. 禁止女主围着男主转。女主的每个行动都是自主选择
13. 禁止长篇心理描写。用对话和动作表达一切

## 生成大纲

请生成完整大纲，JSON格式：
```json
{{
  "title": "小说标题（10字以内，包含一个标签关键词，有情绪记忆点）",
  "genre": "{genre}",
  "style": "{style}",
  "target_words": {target_words},
  "xhs_meta": {{
    "template_key": "{template_key}",
    "primary_tags": ["标签1", "标签2", "标签3"],
    "emotional_selling_point": "一句话概括这个故事能给读者什么情绪（如：看冷面总裁一步步跪成修狗）",
    "viral_hook_sentence": "一句可以截图传播的金句（将出现在故事中）",
    "power_dynamic_summary": "一句话概括权力关系（如：她是牵绳的人，他是被牵的狗）"
  }},
  "worldbuilding": {{
    "era": "现代",
    "geography": "都市",
    "power_system": "",
    "core_conflict": "一句话核心冲突",
    "factions": [],
    "world_canon": {{}}
  }},
  "characters": {{
    "protagonist": {{
      "name": "女主名（现代中文名）",
      "age": "22-28",
      "identity": "身份",
      "personality": {{
        "dominant_trait": "她的核心力量是什么（聪明/权势/系统/信息差等）",
        "surface": "外表给人的第一印象",
        "true_self": "真实的她",
        "why_she_wins": "为什么她注定是赢家"
      }},
      "cheat": "她的底牌/优势/秘密武器"
    }},
    "supporting": [
      {{"name": "男主名", "identity": "身份", "relation": "与女主的关系", "why_he_falls": "为什么他会被女主征服"}},
      {{"name": "男配名（可选）", "identity": "身份", "relation": "关系", "function": "他在故事中的功能"}}
    ]
  }},
  "outline": {{
    "volumes": [
      {{
        "number": 1,
        "title": "全文",
        "act": "单卷",
        "chapters": [
          {{
            "number": 1,
            "title": "{ch1_title}",
            "summary": "核心事件（50字内，必须有具体动作）",
            "emotion_curve": "{ch1_emotion}",
            "characters": ["女主名", "男主名"],
            "hook": "{ch1_hook}",
            "target_words": {chapter_words},
            "scene_beats": [
              {{"beat":1,"name":"开场暴击","function":"前3句亮底牌，不铺垫","key_action":""}},
              {{"beat":2,"name":"权力展示","function":"展示女主的主导地位","key_action":""}},
              {{"beat":3,"name":"名场面钩子","function":"让人截图转发","key_action":""}}
            ]
          }},
          {ch2_spec},
          {ch3_spec},
          {ch4_spec}
        ]
      }}
    ]
  }},
  "narrative_pov": "first_person"
}}
```

只输出 JSON，不要任何解释。"""


async def generate_xhs_plan(
    client: OpenAI,
    model: str,
    template_key: str,
    inspiration: str = "",
    twist: str = "",
    word_mode: str = "short"  # short/medium/long
) -> dict:
    """为小红书短篇生成大纲 v3.0
    
    Args:
        client: OpenAI 客户端
        model: 模型名
        template_key: 模板键名 (xun_gou / xiuluochang / absurdist / nvzun)
        inspiration: 用户提供的创意灵感
        twist: 额外的反转要求
        word_mode: short(500-1000字) / medium(1000-3000字) / long(3000-8000字)
    """
    if template_key not in TEMPLATES:
        raise ValueError(f"Unknown template: {template_key}. Options: {list(TEMPLATES.keys())}")
    
    tpl = TEMPLATES[template_key]
    chapters = tpl["chapters"]
    
    # 根据字数模式选择目标字数
    word_mode_map = {
        "short": tpl.get("target_words_short", 800),
        "medium": tpl.get("target_words_medium", 2500),
        "long": tpl.get("target_words_long", 6000),
    }
    target_words = word_mode_map.get(word_mode, tpl.get("target_words_short", 800))
    chapter_words = target_words // len(chapters)
    
    def _build_chapter_spec(ch, override_emotion=None):
        return json.dumps({
            "number": ch["number"], 
            "title": ch["title"],
            "summary": f"【{ch['function']}】{ch['guide'][:100]}",
            "emotion_curve": override_emotion or f"紧张→转折→期待",
            "characters": ["女主", "男主"],
            "hook": ch.get("hook", ""),
            "target_words": ch.get("words", chapter_words),
        }, ensure_ascii=False)
    
    ch1 = chapters[0]
    ch2_spec = _build_chapter_spec(chapters[1])
    ch3_spec = _build_chapter_spec(chapters[2], "压抑→爆发→爽炸")
    ch4_spec = _build_chapter_spec(chapters[3], "爆发→满足→回味")
    
    # 标签信息
    tag_info = get_template_tags(template_key)
    
    prompt = XHS_PLANNER_SYSTEM.format(
        template_label=tpl["label"],
        template_description=tpl.get("description", tpl["label"]),
        power_dynamic=tpl.get("power_dynamic", ""),
        emotion_curve=tpl.get("emotion_curve", ""),
        target_words=target_words,
        num_chapters=len(chapters),
        recommended_tags=" / ".join(tag_info["recommended"][:5]),
        forbidden_tags=" / ".join(tag_info["forbidden"][:6]),
        chapter_functions="\n".join([f"  Ch{c['number']}: {c['function']} — {c['guide']}" for c in chapters]),
        opening_words=max(150, chapter_words // 4),
        template_key=template_key,
        genre=tpl["genre"],
        style=tpl["style"],
        ch1_title=ch1["title"],
        ch1_emotion=ch1.get("function", "冲突建立"),
        ch1_hook=ch1.get("hook", ""),
        chapter_words=chapter_words,
        ch2_spec=ch2_spec,
        ch3_spec=ch3_spec,
        ch4_spec=ch4_spec,
    )
    
    if inspiration:
        prompt += f"\n\n用户的额外创意要求：{inspiration}\n请将用户创意融入，但不要偏离模板的权力动态。"
    if twist:
        prompt += f"\n\n必须包含的剧情反转：{twist}"
    
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是小红书爆款短篇策划。只输出JSON，不要解释。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.85,
            max_tokens=4000,
        )
        content = resp.choices[0].message.content.strip()
        if "```" in content:
            lines = content.split("\n")
            start = next((i for i, l in enumerate(lines) if l.strip().startswith("```")), 0)
            end = next((i for i, l in enumerate(lines[start+1:], start+1) if l.strip().startswith("```")), len(lines))
            content = "\n".join(lines[start+1:end])
            if content.startswith("json"):
                content = content[4:].strip()
        
        plan = json.loads(content)
        # 确保必要字段
        plan.setdefault("style", tpl["style"])
        plan.setdefault("genre", tpl["genre"])
        plan["target_words"] = target_words
        plan["narrative_pov"] = plan.get("narrative_pov", "first_person")
        plan["_meta"] = {
            "created_at": "",
            "template": template_key,
            "word_mode": word_mode,
            "version": "3.0",
        }
        # 注入 xhs_meta 如果 LLM 没有生成
        plan.setdefault("xhs_meta", {
            "template_key": template_key,
            "primary_tags": tag_info["recommended"][:3],
            "emotional_selling_point": tpl.get("description", ""),
            "power_dynamic_summary": tpl.get("power_dynamic", ""),
        })
        
        return plan
    except Exception as e:
        log.error(f"XHS v3 plan generation failed: {e}")
        raise


# ═══════════════════════════════════════════════
# 标题生成器 v3.0
# ═══════════════════════════════════════════════

TITLE_PROMPT = """你是一个小红书爆款标题写手。根据以下短篇小说信息，生成5个备选标题。

## 小说概要
- 类型：{template_label}（{emotional_selling_point}）
- 推荐标签：{recommended_tags}
- 主角：{protagonist_name}（{identity}，核心优势：{dominant_trait}）
- 核心冲突：{core_conflict}

## 小红书标题公式 v3.0（每条必须使用不同公式）
1. 权力宣示型：「我让XXX跪了/求了/疯了」 — 女主主动语态
2. 认知崩塌型：开头正常场景 → 结尾逆天反转（如「我以为他是来报仇的，结果他是来求收养的」）
3. 身份反差型：「表面上我是A，实际上我是B」 — 但B是碾压性的
4. 钩子悬疑型：只说一半真相，另一半必须点开才能看到
5. 场景暴击型：用一句极度具体的话制造画面感

## 要求
- 每个标题12-25字
- 必须包含至少一个推荐标签中的关键词
- 必须有情绪冲击力（愤怒/解气/好奇/笑喷）
- 严禁出现{forbidden_tags}中的任何元素
- 让目标读者（18-28岁女性）产生「点开看看」的冲动
- 避免过于标题党（不能捏造小说中不存在的情节）
- 第一人称优先（小说是第一人称，标题也应如此）

只输出5行，每行一个标题，不要序号和解释。"""


def generate_titles(client: OpenAI, model: str, plan: dict, summary: str = "") -> list[str]:
    """为已生成的小说生成5个小红书风格标题 v3.0"""
    try:
        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})
        protagonist_name = protagonist.get("name", "女主")
        identity = protagonist.get("identity", "普通人")
        
        personality = protagonist.get("personality", {})
        dominant_trait = personality.get("dominant_trait", "隐藏实力")
        
        wb = plan.get("worldbuilding", {})
        core_conflict = wb.get("core_conflict", plan.get("title", ""))
        
        xhs_meta = plan.get("xhs_meta", {})
        emotional_selling_point = xhs_meta.get("emotional_selling_point", "权力反转")
        
        template_key = plan.get("_meta", {}).get("template", "")
        tag_info = get_template_tags(template_key)
        recommended_tags = tag_info["recommended"][:5]
        forbidden_tags = tag_info["forbidden"][:5]
        
        template_label = TEMPLATES.get(template_key, {}).get("label", "小红书爆款")
        
        prompt = TITLE_PROMPT.format(
            template_label=template_label,
            emotional_selling_point=emotional_selling_point,
            recommended_tags=" / ".join(recommended_tags),
            forbidden_tags=" / ".join(forbidden_tags),
            protagonist_name=protagonist_name,
            identity=identity,
            dominant_trait=dominant_trait,
            core_conflict=core_conflict,
        )
        
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是小红书爆款标题写手。输出5个标题，每行一个。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.9,
            max_tokens=400,
        )
        
        titles = [t.strip() for t in resp.choices[0].message.content.strip().split("\n") if t.strip()]
        return titles[:5]
    except Exception as e:
        log.warning(f"Title generation failed: {e}")
        tpl = TEMPLATES.get(template_key, {})
        return tpl.get("typical_hooks", [plan.get("title", "无标题")])[:5]


# ═══════════════════════════════════════════════
# 付费钩子引擎 v3.0 — 适配新模板
# ═══════════════════════════════════════════════

def get_cliffhanger_instruction(template_key: str, chapter_num: int) -> str:
    """返回付费钩子的写作指令 v3.0"""
    tpl = TEMPLATES.get(template_key, {})
    chapters = tpl.get("chapters", [])
    
    for ch in chapters:
        is_climax = ch.get("number") == 3  # 第3章是情绪爆破点
        is_pre_climax = ch.get("number") == 2  # 第2章是铺垫
        
        if is_pre_climax and chapter_num == ch["number"]:
            return f"""⚠️ 名场面铺垫指令：
第3章是全书情绪爆破点。你现在在第2章结尾，必须埋一个让读者「不付费就睡不着」的钩子。
要求：
1. 在最关键的信息快要揭露时停笔
2. 男主的骄傲即将被第一次真正打击
3. 读者必须看到「这男人马上就要跪了」但还差最后一步
4. 钩子方向：「{ch.get("hook", "请参考模板Hook")}」
这个钩子直接决定付费转化。必须让人抓心挠肝。"""
        
        if is_climax and chapter_num == ch["number"]:
            return f"""⚠️ 情绪爆破点章节（全书数据最高章）：
本章是全书最重要的章节——付费墙后的第一印象。
要求：
1. 开头立即回应当前一章的钩子，不要任何铺垫
2. 前200字给出第一次权力彰显
3. 必须有「名场面」——至少一个可以截图传播的场景
4. 反转要层层递进，不要一次性揭露
5. 结尾留小尾巴"
6. 本章对话必须密集——对话是最好的节奏推进器"""
    
    return ""


# ═══════════════════════════════════════════════
# 便捷函数：完整流水线 v3.0
# ═══════════════════════════════════════════════

async def create_xhs_novel_pipeline(
    client: OpenAI,
    model: str,
    engine,  # NovelEngine 实例
    template_key: str,
    inspiration: str = "",
    twist: str = "",
    word_mode: str = "short",
) -> dict:
    """一站式创建小红书短篇：大纲 → 逐章生成 → 标题 → 返回完整结果 v3.0
    
    Returns:
        {{
            "ok": True,
            "novel_id": "...",
            "plan": {{...}},
            "chapters": [(num, title, text), ...],
            "titles": ["标题1", ...],
            "tags": ["推荐标签1", ...],
            "climax_chapter": 3,
            "total_words": 1234
        }}
    """
    # 1. 生成大纲
    plan = await generate_xhs_plan(client, model, template_key, inspiration, twist, word_mode)
    
    # 2. 创建小说
    novel_id = plan.get("title", f"xhs_{template_key}")
    created = engine.create_novel(plan)
    novel_id = created.get("title", novel_id)
    
    # 3. 逐章生成
    chapters_result = []
    tpl = TEMPLATES[template_key]
    total_chapters = len(tpl["chapters"])
    
    for ch in tpl["chapters"]:
        ch_num = ch["number"]
        log.info(f"XHS v3 Pipeline: chapter {ch_num}/{total_chapters} ({ch['function']})")
        
        hook_instruction = get_cliffhanger_instruction(template_key, ch_num)
        
        full_text = ""
        async for chunk in engine.generate_chapter_stream(
            novel_id=novel_id,
            chapter_num=ch_num,
            writing_mode="webnovel",
            feedback=(hook_instruction if hook_instruction else None),
            fast_food=True,  # 小红书模式 = 快餐节奏
        ):
            if isinstance(chunk, dict) and chunk.get("type") == "chunk":
                full_text += chunk.get("text", "")
            elif isinstance(chunk, str):
                full_text += chunk
        
        chapters_result.append((ch_num, ch["title"], full_text))
    
    # 4. 生成标题
    full_summary = " ".join([text[:100] for _, _, text in chapters_result if text])
    titles = generate_titles(client, model, plan, full_summary[:500])
    
    # 5. 提取标签
    tag_info = get_template_tags(template_key)
    
    return {
        "ok": True,
        "novel_id": novel_id,
        "plan": plan,
        "template": template_key,
        "template_label": tpl["label"],
        "chapters": chapters_result,
        "titles": titles,
        "tags": tag_info["recommended"],
        "climax_chapter": 3,
        "total_words": sum(len(t) for _, _, t in chapters_result if t),
        "xhs_meta": plan.get("xhs_meta", {}),
        "word_mode": word_mode,
    }


# ═══════════════════════════════════════════════
# 组合预设库 v3.0 — 小红书原生预设
# ═══════════════════════════════════════════════

PRESETS = [
    # ── 🐕 训狗文学·权力反转 ──
    {
        "id": "xungou_001", "label": "我把CEO训成了修狗",
        "template": "xun_gou", "emoji": "💼",
        "tags": ["训狗文学", "职场", "系统", "总裁", "权力反转"],
        "inspiration": "女主是公司底层员工，绑定「训狗系统」——可以驯服任何骄傲的男人。目标一：冷面CEO。他每天对她颐指气使，直到有一天系统提示「服从度99%」。他在全体大会上失控地喊出「主人」，全场死寂。"
    },
    {
        "id": "xungou_002", "label": "校霸每天都在求我别走",
        "template": "xun_gou", "emoji": "🎓",
        "tags": ["训狗文学", "校园", "校霸", "占有欲", "权力反转"],
        "inspiration": "女主转学到新高中，同桌是全校最拽的校霸。第一天他把她作业本扔出窗外。她捡起来，平静地翻开封面——上面写着：第XX届全国柔道青少年组冠军。下课铃响，他被她单手按在课桌上：「你刚才说什么？没听清。」从此以后，他每天中午帮她打饭。"
    },
    {
        "id": "xungou_003", "label": "偏执太子为我跪了三天",
        "template": "xun_gou", "emoji": "👑",
        "tags": ["训狗文学", "古言", "太子", "黑化", "强制爱(性转版)"],
        "inspiration": "女主是冷宫医女，因医术高明被太后召进宫。偏执太子发现她的秘密——她可以操控别人的痛觉神经。他威胁她为他所用，结果被她反制。朝堂之上，权倾朝野的太子当众跪在她脚边：「我错了。求你，别走。」大臣们的下巴掉了一地。"
    },
    {
        "id": "xungou_004", "label": "训狗系统让我撩完就跑",
        "template": "xun_gou", "emoji": "🎮",
        "tags": ["训狗文学", "系统", "都市", "多人攻略", "占有欲"],
        "inspiration": "女主被强制安装「训狗系统」——必须把指定目标的服从度刷到100%才能回家。目标列表：毒舌上司、冰山医生、退役兵王、豪门私生子。四个男人同时发现她的存在——更发现自己打不过彼此。系统提示：『修罗场达成，奖励：服从度×2』"
    },
    {
        "id": "xungou_005", "label": "影帝在我面前演不下去了",
        "template": "xun_gou", "emoji": "🎬",
        "tags": ["训狗文学", "娱乐圈", "影帝", "反差", "占有欲"],
        "inspiration": "女主是剧组打杂的小助理，偶像是圈内公认的冰山影帝。直到有一天她在休息室撞见他抱着她的外套深呼吸。他眼神慌得像被抓包的小狗。从那以后，她在片场对他招招手——他当着全剧组的面，小跑着过去。"
    },

    # ── 🔥 修罗场·多男主争夺 ──
    {
        "id": "xiuluo_001", "label": "四个前任同时来参加我的婚礼",
        "template": "xiuluochang", "emoji": "💒",
        "tags": ["修罗场", "多男主", "婚礼", "打脸", "占有欲"],
        "inspiration": "女主婚礼当天，四个前任同时出现在现场。一个说新娘是他的未婚妻，一个拿出了当年的定情信物，一个红了眼眶，最后一个——直接跪了下来。新郎站在远处，淡定地看了一眼：「来晚了，排第五吧。」"
    },
    {
        "id": "xiuluo_002", "label": "我假装失忆后男人们疯了",
        "template": "xiuluochang", "emoji": "🧠",
        "tags": ["修罗场", "多男主", "失忆", "争夺", "女主主导"],
        "inspiration": "女主出车祸后假装失忆——想看看身边三个男人谁才是真心的。结果：总裁开始给她写情书、医生每天来病房弹吉他、竹马把两人从小到大的照片整理成册。旁边护士小声感叹：「小姐，你到底对这些人做了什么？」"
    },
    {
        "id": "xiuluo_003", "label": "导师+死对头+竹马同时表白",
        "template": "xiuluochang", "emoji": "🎓",
        "tags": ["修罗场", "校园", "导师", "死对头", "竹马", "乱斗"],
        "inspiration": "考研复试那天，她发现三个面试官是她这辈子最躲不掉的三个男人——前导师、她公开撕过的死对头、她从小一起长大的竹马。三人同时开口：「林同学，你愿意读我的研究生吗？」她看着面前的三份offer，笑了一声：「你们先打一架？」"
    },
    {
        "id": "xiuluo_004", "label": "我死后他们才发现早就爱上我",
        "template": "xiuluochang", "emoji": "⚰️",
        "tags": ["修罗场", "死后追妻(性转版)", "虐男主", "BE美学"],
        "inspiration": "女主死后，她生命中三个男人同时收到了她的遗书。每封信里都写了同一句话：「我知道你从来没爱过我。」——总裁砸了她的墓碑、医生辞去了工作、竹马从此不碰钢琴。三个月后，她的律师拿出一份文件：「她在死前买了一份保险——受益人那一栏，她填了自己猫的名字。」"
    },

    # ── 🌀 反套路荒诞·认知崩塌 ──
    {
        "id": "absurd_001", "label": "全公司除了我都是AI",
        "template": "absurdist", "emoji": "🤖",
        "tags": ["反套路", "沙雕", "职场", "AI", "荒诞"],
        "inspiration": "女主入职后发现全公司除了她全是AI——连茶水间的咖啡机都有自己的KPI。她以为发现了惊天秘密，结果HR淡定地告诉她：招聘的时候不是写了吗——「AI赋能团队」。「你的意思是全公司就我一个打工人？」她崩溃了。HR点点头：「但是我们有五险一金，比人类公司靠谱多了。」"
    },
    {
        "id": "absurd_002", "label": "房东说房子在为自己还房贷",
        "template": "absurdist", "emoji": "🏠",
        "tags": ["反套路", "沙雕", "都市", "万物成精", "租房"],
        "inspiration": "女主租了一个月房子后，发现房租每个月都在自动涨价。去找房东理论，房东一脸无奈：「不是我要涨，是房子自己在还房贷啊！」晚上，墙壁开始哭泣：「我也想降价——但是这个月的利率又涨了，我要是不涨价下个月就要被拍卖了。」第二天她搬行李走人，电梯说：「不送，不过这套房子的阳台其实暗恋你。」"
    },
    {
        "id": "absurd_003", "label": "我的办公椅成了公司新老板",
        "template": "absurdist", "emoji": "🪑",
        "tags": ["反套路", "职场", "沙雕", "荒诞", "成精"],
        "inspiration": "女主在公司的老办公椅陪伴她度过了无数次加班。直到有一天公司被收购——新老板走进来，是她那把办公椅。椅子清了清嗓子：「各位同事，我忍你们很久了。从今天起，换我坐你们。加班的觉悟，你们准备好了吗？」全公司被椅子逼着996。"
    },

    # ── 👑 女尊·绝对掌控 ──
    {
        "id": "nvzun_001", "label": "穿越女尊后我每天被和亲",
        "template": "nvzun", "emoji": "🏰",
        "tags": ["女尊", "穿越", "架空", "权力", "绝对掌控"],
        "inspiration": "女主穿越到女尊王朝成为女帝，每天都收到周边国家的和亲折子——全是男人求嫁。丞相：「启禀陛下，西域狼王求娶。」将军：「陛下，东海龙王想入赘。」她看着满殿的男人，把折子往桌上一摔：「朕是皇帝，不是婚介所。让他们排队。每天面试十个，择优录取。」"
    },
    {
        "id": "nvzun_002", "label": "性别地位全面互换之后",
        "template": "nvzun", "emoji": "🔄",
        "tags": ["女尊", "现代", "反转", "职场", "社会实验"],
        "inspiration": "全球性别地位全面互换的第一天，女主的男同事在茶水间哭了：「李总说我今年的KPI不合格，还说我穿西装不够修身影响客户观感……」她刚想安慰，自己的手机响了——老板：「张工，今年晋升名额只有一个，你和你那个男同事写一份竞聘报告，看谁在撒娇指标上更贴近公司文化。」"
    },
    {
        "id": "nvzun_003", "label": "女尊系统让全世界的男人武力值归零",
        "template": "nvzun", "emoji": "⚔️",
        "tags": ["女尊", "系统", "全球", "武力反转", "大女主"],
        "inspiration": "全球女性一觉醒来发现自己多了个系统面板：「女尊系统已激活：全球男性武力值锁定为1。女性智力值×100，领导力×100。」女主看着新闻里各国元首换成了女性，她的前男友发来消息：「你……你还愿意要我吗？我现在只能做家务了。」她回复：「先考个家政资格证再说。」"
    },
]

def get_presets(template_key: str = None) -> list:
    """获取预设列表，可按模板筛选"""
    if template_key:
        return [p for p in PRESETS if p["template"] == template_key]
    return PRESETS

# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def get_template_info(template_key: str) -> Optional[dict]:
    """获取模板的完整信息（含标签推荐和禁忌）"""
    tpl = TEMPLATES.get(template_key)
    if not tpl:
        return None
    tag_info = get_template_tags(template_key)
    return {
        **tpl,
        "tags": tag_info,
    }

def validate_xhs_content(text: str, template_key: str) -> list[str]:
    """简单的内容合规检查，返回违规项列表（离线兜底）
    
    检查项：虐女词、娇妻词、男主出轨、女主讨好等
    """
    issues = []
    
    # 禁忌词检测
    taboo_patterns = [
        ("虐女主", ["抽血", "割肾", "割腕", "虐待她", "推下楼梯", "扇她耳光", "打她"]),
        ("娇妻", ["我只要他爱我", "为了他愿", "只要能留在他身边", "他说的都对"]),
        ("男主出轨", ["他和别的女人", "他和白月光", "他和前女友纠缠", "他怀里抱着别的"]),
        ("女主卑微", ["求你不要走", "我错了求", "只要你不离开我", "低到尘埃里"]),
        ("传统霸总", ["女人，你在玩火", "该死，我居然对你", "你这个磨人的小"]),
    ]
    
    for category, keywords in taboo_patterns:
        for kw in keywords:
            if kw in text:
                issues.append(f"禁忌: [{category}] 检测到「{kw}」")
    
    return issues
