"""NovelGenerator — Xiaohongshu Short Story Mode

小红书爆款短篇小说生成器。提供4种爆款模板、付费钩子引擎、标题生成器。

模板结构: 8000-12000字, 4章, 每章2000-3000字
  - Ch1 开局 (0-25%): 冲突建立, 快速代入
  - Ch2 升级 (25-50%): 冲突升级, 埋钩子
  - Ch3 反转 (50-75%): 核心反转, 付费卡点 ★
  - Ch4 结局 (75-100%): 终极打脸/撒糖/揭秘
"""

import json
import logging
from typing import Optional
from openai import OpenAI

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# 模板定义
# ═══════════════════════════════════════════════

TEMPLATES = {
    "爽文_打脸逆袭": {
        "label": "爽文·打脸逆袭",
        "emoji": "🔴",
        "genre": "女频爽文",
        "style": "热血爽文",
        "target_words": 10000,
        "chapters": [
            {
                "number": 1, "title": "深渊",
                "function": "受辱",
                "guide": "主角被背叛/陷害/抛弃，情绪拉满。让读者代入被欺负的愤怒。",
                "hook": "所有人都在等着看我笑话，却不知道我手里握着足以让他们跪下的东西。",
                "words": 2500
            },
            {
                "number": 2, "title": "暗涌",
                "function": "反击铺垫",
                "guide": "主角开始收集证据/积蓄力量。反派继续作妖增加仇恨值。主角展示隐藏实力的蛛丝马迹。",
                "hook": "他们以为我还是三年前那个任人宰割的废物——可惜，今天不一样了。",
                "words": 2500
            },
            {
                "number": 3, "title": "翻转",
                "function": "★ 付费卡点",
                "guide": "【关键章】主角当众打脸。亮出最终底牌/身份/证据。反派从嚣张到崩溃。读者必须看到这一章。",
                "hook": "我摘下口罩，全场死寂。三秒后，刚才还在嚣张的继妹扑通一声跪了下来——「姐，我错了。」",
                "words": 2500
            },
            {
                "number": 4, "title": "终局",
                "function": "终极打脸",
                "guide": "反派彻底崩溃，主角获得最终胜利。善有善报恶有恶报。给读者最大的满足感。",
                "hook": "",
                "words": 2500
            }
        ],
        "emotion_curve": "愤怒→期待→爽爆→满足",
        "typical_hooks": [
            "被退婚后，我亮出真实身份，前未婚夫跪着求我回来",
            "婆婆说我克夫，三年后我成了她儿子的老板",
            "全家等我死掉分遗产，我带着体检报告站在家族年会上"
        ]
    },
    "虐文_追妻火葬场": {
        "label": "虐文·追妻火葬场",
        "emoji": "🟣",
        "genre": "女频虐文",
        "style": "虐心深情",
        "target_words": 10000,
        "chapters": [
            {
                "number": 1, "title": "心碎",
                "function": "虐女主",
                "guide": "男主极度偏心/冷漠/误会女主。女主被虐待/冷落/伤害。细节描写让人心疼。",
                "hook": "他给白月光戴上婚戒的那一刻，我终于明白——在他心里，我连替代品都算不上。",
                "words": 2500
            },
            {
                "number": 2, "title": "决绝",
                "function": "崩溃",
                "guide": "女主彻底绝望，做出不可挽回的决定（离开/签字/消失）。男主仍然无动于衷或变本加厉。",
                "hook": "我把签好的离婚协议放在桌上，转身离开。身后传来他撕纸的声音——「闹够了就回来。」",
                "words": 2500
            },
            {
                "number": 3, "title": "裂痕",
                "function": "★ 付费卡点",
                "guide": "【关键章】女主真的消失了/出事了。男主终于发现真相，开始崩溃。追妻模式启动。",
                "hook": "三个月后，他在我的墓碑前跪了一整夜。而我站在远处，挽着另一个男人的手，头也不回地走了。",
                "words": 2500
            },
            {
                "number": 4, "title": "新生",
                "function": "追妻+结局",
                "guide": "男主疯狂寻找/后悔/弥补。女主已经move on。虐男主到极致。结局可HE可BE。",
                "hook": "",
                "words": 2500
            }
        ],
        "emotion_curve": "心疼→愤怒→揪心→痛快",
        "typical_hooks": [
            "他亲手把我推下楼梯那天，我肚子里还怀着他的孩子",
            "婚礼上他牵起白月光的手，我摘下戒指放在蛋糕上",
            "他说这辈子只爱她一个，可为什么在我失踪后他疯了三年"
        ]
    },
    "世情_家庭反转": {
        "label": "世情·家庭反转",
        "emoji": "🟢",
        "genre": "女频世情",
        "style": "轻松搞笑",
        "target_words": 10000,
        "chapters": [
            {
                "number": 1, "title": "寒心",
                "function": "憋屈",
                "guide": "全家冷漠/排挤/欺负主角。主角被孤立，但默默承受。让读者感受到强烈的不公。",
                "hook": "全家人都在给弟弟筹钱买房，而我——刚查出癌症，没人记得今天是我生日。",
                "words": 2500
            },
            {
                "number": 2, "title": "隐忍",
                "function": "积蓄",
                "guide": "主角在暗处收集证据/学习/成长。家人变本加厉。埋下翻盘的种子。",
                "hook": "我在医院化疗时接到妈的电话：「你弟要交首付了，你什么时候把钱打过来？」我挂断电话，打开了律师给我准备的文件。",
                "words": 2500
            },
            {
                "number": 3, "title": "摊牌",
                "function": "★ 付费卡点",
                "guide": "【关键章】主角在家庭聚会/节日/重要场合当众揭露真相。证据确凿。家人崩溃。",
                "hook": "我把亲子鉴定拍在桌上：「我不是你们亲生的——你们当年在医院抱错了。我亲爹是你们最怕的那个人。」",
                "words": 2500
            },
            {
                "number": 4, "title": "新生",
                "function": "终极翻盘",
                "guide": "家人后悔/求饶/被反噬。主角获得新生活。善有善报恶有恶报。温馨结局。",
                "hook": "",
                "words": 2500
            }
        ],
        "emotion_curve": "憋屈→期待→爽快→温馨",
        "typical_hooks": [
            "全家避他如瘟神，我接回个财神爷",
            "拆迁款全给了弟弟，我搬进桥洞住，第二天来了三辆劳斯莱斯",
            "继母说女孩子不配上大学，十年后我成了她的主治医生"
        ]
    },
    "甜宠_高糖轻虐": {
        "label": "甜宠·高糖轻虐",
        "emoji": "🟡",
        "genre": "女频甜宠",
        "style": "轻松搞笑",
        "target_words": 8000,
        "chapters": [
            {
                "number": 1, "title": "相遇",
                "function": "建立CP",
                "guide": "男女主以特殊方式相遇。制造CP感。要有甜有酸。让读者磕到。",
                "hook": "我撞进他怀里的那一秒，他手里的咖啡泼了我一身。他低头看我——「赔不起，就用你自己抵。」",
                "words": 2000
            },
            {
                "number": 2, "title": "拉扯",
                "function": "感情升级",
                "guide": "误会/情敌/身份差距制造张力。甜中有虐，虐中有甜。CP感持续升温。",
                "hook": "他的前女友突然出现，挽着他胳膊说：「这就是你说的那个替身？」我转身要走，他却一把拽住我——「替身？她是我未婚妻。」",
                "words": 2000
            },
            {
                "number": 3, "title": "告白",
                "function": "★ 付费卡点",
                "guide": "【关键章】高潮甜度。男主强势表白/追妻/公开关系。最甜最上头的一章。",
                "hook": "全公司都在传我要被开除。他推开会议室的门，单膝跪地：「嫁给我，这家公司送你当聘礼。」",
                "words": 2000
            },
            {
                "number": 4, "title": "甜蜜",
                "function": "完美结局",
                "guide": "婚后/在一起后的甜蜜生活。解决之前埋的冲突。给读者满满的幸福感。",
                "hook": "",
                "words": 2000
            }
        ],
        "emotion_curve": "甜→酸→甜→暖",
        "typical_hooks": [
            "替身新娘带球跑，暴戾总裁满城找",
            "我把相亲对象错认成了集团太子爷，第二天他堵在我公司门口",
            "新婚夜老公不肯碰我，三个月后他砸了我的画室——「画里的人为什么是他」"
        ]
    }
}


# ═══════════════════════════════════════════════
# 大纲生成
# ═══════════════════════════════════════════════

XHS_PLANNER_SYSTEM = """你是一个小红书爆款短篇小说策划。你的任务是为AI生成提供精确大纲。

## 核心原则
- 目标读者：18-35岁女性，碎片时间阅读
- 字数：{target_words} 字，分 {num_chapters} 章，每章 {chapter_words} 字
- 节奏：第一章必须在前500字内建立冲突。每章结尾必须有强钩子。
- 情感：女频爽文的核心是"情绪过山车"——让读者在愤怒、期待、爽感之间反复切换
- 人物：女主必须有让人共情的遭遇 + 让人期待的反击能力。反派必须让人讨厌到牙痒。

## 当前模板
- 类型：{template_label}
- 各章功能：{chapter_functions}

## 生成大纲

请生成完整大纲，JSON格式：
```json
{{
  "title": "小说标题（10字以内，有情绪钩子）",
  "genre": "{genre}",
  "style": "{style}",
  "target_words": {target_words},
  "worldbuilding": {{
    "era": "现代",
    "geography": "都市/豪门/职场",
    "power_system": "",
    "core_conflict": "一句话核心冲突",
    "factions": [],
    "world_canon": {{}}
  }},
  "characters": {{
    "protagonist": {{
      "name": "女主名（现代中文名，如苏晚晴/林安安/沈念）",
      "age": "22-28",
      "identity": "身份",
      "personality": {{"surface": "外表柔弱的/乖巧的","true_self": "内心强大的/聪明的","flaw": "太容易心软"}},
      "cheat": "隐藏的底牌/能力（如：真实身份是XX集团继承人、手握关键证据、有反转能力）"
    }},
    "supporting": [
      {{"name": "男主名", "identity": "身份", "relation": "关系"}},
      {{"name": "反派名", "identity": "身份", "relation": "对立关系"}}
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
            "summary": "核心事件（50字内）",
            "emotion_curve": "{ch1_emotion}",
            "characters": ["女主名", "反派名"],
            "hook": "{ch1_hook}",
            "target_words": {chapter_words},
            "scene_beats": [
              {{"beat":1,"name":"开场冲突","function":"立即抓住读者","key_action":""}},
              {{"beat":2,"name":"情绪拉扯","function":"加深代入感","key_action":""}},
              {{"beat":3,"name":"钩子","function":"让人想继续看","key_action":""}}
            ]
          }},
          {ch2_spec},
          {ch3_spec},
          {ch4_spec}
        ]
      }}
    ]
  }},
  "narrative_pov": "third_person"
}}
```

只输出 JSON。"""


async def generate_xhs_plan(client: OpenAI, model: str, template_key: str, inspiration: str = "", twist: str = "") -> dict:
    """为小红书短篇生成大纲
    
    Args:
        client: OpenAI 客户端
        model: 模型名
        template_key: 模板键名
        inspiration: 用户提供的创意灵感
        twist: 额外的反转要求
        
    Returns:
        plan dict，可直接用于 engine.create_novel()
    """
    tpl = TEMPLATES[template_key]
    chapters = tpl["chapters"]
    
    ch1 = chapters[0]
    ch2 = chapters[1]
    ch3 = chapters[2]
    ch4 = chapters[3]
    
    ch2_spec = json.dumps({
        "number": 2, "title": ch2["title"],
        "summary": f"【{ch2['function']}】{ch2['guide'][:80]}",
        "emotion_curve": "压抑→期待",
        "characters": ["女主", "反派"],
        "hook": ch2["hook"],
        "target_words": ch2["words"]
    }, ensure_ascii=False)
    
    ch3_spec = json.dumps({
        "number": 3, "title": ch3["title"],
        "summary": f"【★付费卡点★ {ch3['function']}】{ch3['guide'][:80]}",
        "emotion_curve": "紧张→爆发→爽快",
        "characters": ["女主", "反派"],
        "hook": ch3["hook"],
        "target_words": ch3["words"]
    }, ensure_ascii=False)
    
    ch4_spec = json.dumps({
        "number": 4, "title": ch4["title"],
        "summary": f"【{ch4['function']}】{ch4['guide'][:80]}",
        "emotion_curve": "爆发→满足→回味",
        "characters": ["女主", "反派"],
        "hook": "",
        "target_words": ch4["words"]
    }, ensure_ascii=False)
    
    prompt = XHS_PLANNER_SYSTEM.format(
        target_words=tpl["target_words"],
        num_chapters=4,
        chapter_words=tpl["chapters"][0]["words"],
        template_label=tpl["label"],
        chapter_functions="\n".join([f"  Ch{c['number']}: {c['function']} — {c['guide']}" for c in chapters]),
        genre=tpl["genre"],
        style=tpl["style"],
        ch1_title=ch1["title"],
        ch1_emotion=ch1.get("emotion_curve", "愤怒→期待"),
        ch1_hook=ch1["hook"],
        ch2_spec=ch2_spec,
        ch3_spec=ch3_spec,
        ch4_spec=ch4_spec,
    )
    
    if inspiration:
        prompt += f"\n\n用户的额外创意要求：{inspiration}"
    if twist:
        prompt += f"\n\n必须包含的剧情反转：{twist}"
    
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是小红书爆款短篇策划。只输出JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
            max_tokens=3000,
        )
        content = resp.choices[0].message.content.strip()
        # 清理可能的前后缀
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            if content.endswith("```"):
                content = content[:-3]
        
        plan = json.loads(content)
        # 确保必要字段
        plan.setdefault("style", tpl["style"])
        plan.setdefault("genre", tpl["genre"])
        plan["target_words"] = tpl["target_words"]
        plan["narrative_pov"] = "third_person"
        plan.setdefault("_meta", {"created_at": "", "template": template_key})
        
        return plan
    except Exception as e:
        log.error(f"XHS plan generation failed: {e}")
        raise


# ═══════════════════════════════════════════════
# 标题生成器
# ═══════════════════════════════════════════════

TITLE_PROMPT = """你是一个小红书爆款标题写手。根据以下短篇小说信息，生成5个备选标题。

## 小说概要
- 类型：{template_label}
- 主角：{protagonist_name}（{identity}）
- 核心冲突：{core_conflict}
- 故事梗概：{summary}

## 标题公式（必须使用）
1. 身份反转型：「被A看不起的人，其实是B」
2. 情绪爆点型：强烈的情绪关键词 + 悬念
3. 逆袭宣告型：「X年前我XX，X年后XXX」
4. 反差冲突型：两个极端身份/行为的碰撞
5. 道德困境型：让人产生"这太不公平了"的愤怒

## 要求
- 每个标题15-30字
- 必须有情绪冲击力
- 让目标读者（18-35岁女性）产生「想看后续」的冲动
- 避免过于标题党（不能捏造小说中不存在的情节）

只输出5行，每行一个标题，不要序号和解释。"""


def generate_titles(client: OpenAI, model: str, plan: dict, summary: str = "") -> list[str]:
    """为已生成的小说生成5个小红书风格标题"""
    try:
        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})
        protagonist_name = protagonist.get("name", "女主")
        identity = protagonist.get("identity", "普通人")
        
        wb = plan.get("worldbuilding", {})
        core_conflict = wb.get("core_conflict", plan.get("title", ""))
        
        # 找到模板
        template_key = plan.get("_meta", {}).get("template", "")
        template_label = TEMPLATES.get(template_key, {}).get("label", plan.get("style", "爽文"))
        
        prompt = TITLE_PROMPT.format(
            template_label=template_label,
            protagonist_name=protagonist_name,
            identity=identity,
            core_conflict=core_conflict,
            summary=summary or core_conflict,
        )
        
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是小红书爆款标题写手。输出5个标题，每行一个。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.9,
            max_tokens=300,
        )
        
        titles = [t.strip() for t in resp.choices[0].message.content.strip().split("\n") if t.strip()]
        return titles[:5]
    except Exception as e:
        log.warning(f"Title generation failed: {e}")
        # 降级：返回模板自带钩子
        tpl = TEMPLATES.get(template_key, {})
        return tpl.get("typical_hooks", [plan.get("title", "无标题")])[:5]


# ═══════════════════════════════════════════════
# 付费钩子引擎
# ═══════════════════════════════════════════════

def get_cliffhanger_instruction(template_key: str, chapter_num: int) -> str:
    """返回付费钩子的写作指令
    
    对于小红书短篇，第3章（50%位置）是付费卡点。
    在第2章结尾和第3章生成时注入强化钩子指令。
    """
    tpl = TEMPLATES.get(template_key, {})
    chapters = tpl.get("chapters", [])
    
    # 找到付费卡点章节（通常是第3章）
    for ch in chapters:
        if "★" in ch.get("function", "") or "付费" in ch.get("function", ""):
            if chapter_num == ch["number"] - 1:  # 前一章
                return f"""⚠️ 付费钩子强化指令：
这是付费卡点前的最后一章。结尾必须埋一个让读者「不付费就睡不着」的钩子。
具体要求：
1. 在最关键的信息快要揭露时停笔
2. 让主角处于「即将逆袭但还差一步」的状态
3. 反派在这一刻的嚣张达到顶点
4. 钩子模板：「{ch.get("hook", "请参考模板Hook")}」

这个钩子直接决定付费转化率。必须让人抓心挠肝。"""
            
            if chapter_num == ch["number"]:  # 付费卡点章
                return f"""⚠️ 付费卡点章节（本章是最高转化章节）：
本章是付费墙之后的第一章，必须给读者超预期的满足感。
具体要求：
1. 开头立即回应当前一章的钩子
2. 在500字内给出第一个爽点
3. 反转要层层递进，不要一次性揭露
4. 结尾留个小尾巴，让人想看完结局
5. 本章至少包含3次打脸/反转"""
    
    return ""


# ═══════════════════════════════════════════════
# 便捷函数：完整流水线
# ═══════════════════════════════════════════════

async def create_xhs_novel_pipeline(
    client: OpenAI,
    model: str,
    engine,  # NovelEngine 实例
    template_key: str,
    inspiration: str = "",
    twist: str = "",
) -> dict:
    """一站式创建小红书短篇：大纲 → 逐章生成 → 标题 → 返回完整结果
    
    Returns:
        {
            "ok": True,
            "novel_id": "...",
            "plan": {...},
            "chapters": [(num, title, text), ...],
            "titles": ["标题1", ...],
            "cliffhanger_chapter": 3
        }
    """
    # 1. 生成大纲
    plan = await generate_xhs_plan(client, model, template_key, inspiration, twist)
    
    # 2. 创建小说
    novel_id = plan.get("title", f"xhs_{template_key}")
    # 先把 plan 存进去
    import os
    from .shared_memory import SharedMemoryManager
    # engine.create_novel expects plan as dict
    created = engine.create_novel(plan)
    novel_id = created.get("title", novel_id)
    
    # 3. 逐章生成
    chapters_result = []
    tpl = TEMPLATES[template_key]
    total_chapters = len(tpl["chapters"])
    
    for ch in tpl["chapters"]:
        ch_num = ch["number"]
        log.info(f"XHS Pipeline: generating chapter {ch_num}/{total_chapters} ({ch['function']})")
        
        # 注入付费钩子指令
        hook_instruction = get_cliffhanger_instruction(template_key, ch_num)
        
        # 使用常规生成流程
        full_text = ""
        async for chunk in engine.generate_chapter_stream(
            novel_id=novel_id,
            chapter_num=ch_num,
            writing_mode="webnovel",
            feedback=(hook_instruction if hook_instruction else None),
        ):
            if isinstance(chunk, dict) and chunk.get("type") == "chunk":
                full_text += chunk.get("text", "")
            elif isinstance(chunk, str):
                full_text += chunk
        
        chapters_result.append((ch_num, ch["title"], full_text))
    
    # 4. 生成标题
    # 合并所有章节作为摘要
    full_summary = " ".join([text[:100] for _, _, text in chapters_result if text])
    titles = generate_titles(client, model, plan, full_summary[:500])
    
    return {
        "ok": True,
        "novel_id": novel_id,
        "plan": plan,
        "template": template_key,
        "chapters": chapters_result,
        "titles": titles,
        "cliffhanger_chapter": 3,  # 固定第3章
        "total_words": sum(len(t) for _, _, t in chapters_result if t),
    }


# ═══════════════════════════════════════════════
# 组合预设库 — 模板 × 角色对 × 冲突类型
# ═══════════════════════════════════════════════

PRESETS = [
    # ── 🔴 爽文·打脸逆袭 ──
    {"id":"slap_001","label":"被退婚后我成了首富","template":"爽文_打脸逆袭","emoji":"💔","tags":["退婚","逆袭","商战"],"inspiration":"女主被未婚夫退婚羞辱，全城都在看她笑话。她默默离开，三年后带着千亿身家归来——未婚夫的公司正面临破产，跪着求她注资。她冷笑着拿出当年被撕碎的婚书：「不好意思，我只捐给慈善机构，不施舍给垃圾。」"},
    {"id":"slap_002","label":"婆婆赶我出门后疯了","template":"爽文_打脸逆袭","emoji":"🏠","tags":["婆媳","逆袭","打脸"],"inspiration":"婆婆嫌女主生不出儿子，联合小姑子把怀着孕的女主赶出家门。五年后女主带着龙凤胎和霸道总裁老公回来买下了整条街。婆婆跪在门口哭诉：「儿媳妇，我错了！」女主微微一笑：「谁是您儿媳妇？我们认识吗？」"},
    {"id":"slap_003","label":"闺蜜偷我人生后崩溃了","template":"爽文_打脸逆袭","emoji":"👯","tags":["闺蜜背叛","身份反转","娱乐圈"],"inspiration":"女主是顶流女团成员，被最好的闺蜜下药毁容后踢出组合。五年后整形归来以素人身份参加选秀——评委席上坐着当年的闺蜜。当女主摘下口罩露出完美新面孔的那一刻，全场哗然。"},
    {"id":"slap_004","label":"全公司等我辞职后跪了","template":"爽文_打脸逆袭","emoji":"💼","tags":["职场","逆袭","身份反转"],"inspiration":"女主是公司最底层文员，被全部门排挤。辞职当天竞争对手公司总监亲自开车来接——那是她偷偷考下的offer。三个月后前公司被收购，新老板走进会议室：所有人看向门口——正是当年被看不起的那个小文员。"},
    {"id":"slap_005","label":"亲妈把我卖给傻子后","template":"爽文_打脸逆袭","emoji":"😈","tags":["家庭","逆袭","反转"],"inspiration":"亲妈为了五十万彩礼把女主卖给村里的傻子。新婚夜她逃了出去，十年后成了身家过亿的女企业家。亲妈带着傻子找上门要人，女主拿出当年的收据冷笑：「五十万是吧？我用五百万买你们全家滚出我的视线。」"},

    # ── 🟣 虐文·追妻火葬场 ──
    {"id":"chase_001","label":"替身新娘的致命报复","template":"虐文_追妻火葬场","emoji":"💍","tags":["替身","虐恋","复仇"],"inspiration":"女主嫁给总裁三年，却不知道自己只是白月光的替身。新婚夜他叫的是别人的名字，每次亲密他都闭着眼睛。直到白月光回国那天，他当众撕毁婚书。女主收拾行李离开——却在飞机上心脏病发作。三个月后，他在她的墓碑前跪了三天三夜。"},
    {"id":"chase_002","label":"我死后他才发现爱的是我","template":"虐文_追妻火葬场","emoji":"🪦","tags":["死后追妻","虐心","BE"],"inspiration":"女主患有先天性心脏病，一直暗恋青梅竹马的男主。她隐瞒病情嫁给他，每天偷偷吃药。五年无微不至的照顾，却换来他一句「我从来没爱过你」。她留下离婚协议独自离开，三个月后他收到医院的病危通知——赶到时已经晚了。"},
    {"id":"chase_003","label":"假千金被真千金碾压后","template":"虐文_追妻火葬场","emoji":"👑","tags":["真假千金","豪门","虐恋"],"inspiration":"女主是豪门假千金，真千金回来后她一夜之间从掌上明珠变成佣人。她默默承受虐待和羞辱，直到有一天真千金的未婚夫——她暗恋了十年的男人——亲手把她推下楼梯。「你不过是个赝品。」她躺在地上，手抚着微隆的小腹，笑了。"},
    {"id":"chase_004","label":"他娶我只是为了她的心脏","template":"虐文_追妻火葬场","emoji":"💔","tags":["器官移植","虐恋","反转"],"inspiration":"女主嫁给暗恋多年的学长江辰，婚后他温柔体贴是所有人眼中的模范丈夫。直到她无意间听到他和婆婆的对话——「等她的心脏匹配成功了就动手。」她浑身冰凉地逃出家门，身后传来他的声音：「老婆，你去哪儿？」"},

    # ── 🟢 世情·家庭反转 ──
    {"id":"family_001","label":"拆迁款全给弟弟后","template":"世情_家庭反转","emoji":"🏚️","tags":["重男轻女","家庭","逆袭"],"inspiration":"家里拆迁分八百万全给了弟弟买房买车，只给女主五百块路费让她滚。她带着病重的奶奶租住在城中村地下室。三年后女主成为知名律师，父母带着弟弟来认亲——「女儿，爸妈错了。」她拿出当年的录音：「需要我帮你们回忆一下当时说了什么吗？」"},
    {"id":"family_002","label":"继母毁我高考后我成了她领导","template":"世情_家庭反转","emoji":"📚","tags":["高考","继母","逆袭"],"inspiration":"高考前一天继母偷偷往女主饭菜里下药让她发烧错过考试。她哭着离开家靠着自学考上成人本科一路考上公务员。十年后她坐在面试官的位置上，门口走进来一个熟悉的身影——继母满脸堆笑：「领导好，我是来应聘的。」"},
    {"id":"family_003","label":"全家等我死后继承遗产","template":"世情_家庭反转","emoji":"💰","tags":["遗产","家庭","反转"],"inspiration":"外公留下遗嘱由长孙女继承全部遗产。全家人都盼着她早点死——车祸投毒陷害接连不断。她一忍再忍，直到有一天拿到了亲子鉴定报告。她把报告拍在家族年会上：「我确实不是亲生的——但我亲爹是你们所有人的大老板。现在，把你们吞的钱，一分不少地吐出来。」"},
    {"id":"family_004","label":"嫁出去的女儿泼出去的水","template":"世情_家庭反转","emoji":"🏡","tags":["重男轻女","家庭","打脸"],"inspiration":"女主结婚时父母一分嫁妆不给全留给弟弟。三年后弟弟赌博败光家产，父母被赶出家门。女主和老公已经住上别墅，父母跪在门口：「女儿，收留我们吧。」女主打开门淡淡一句：「嫁出去的女儿泼出去的水——这话是谁教我的来着？」"},

    # ── 🟡 甜宠·高糖轻虐 ──
    {"id":"sweet_001","label":"错把总裁当相亲对象","template":"甜宠_高糖轻虐","emoji":"💕","tags":["欢喜冤家","总裁","甜宠"],"inspiration":"女主被逼去相亲却走错了包间——坐在她对面的不是秃顶大叔而是西装革履的冷面总裁。她毫不知情开启了吐槽模式：「你多大了？有房吗？月薪多少？能接受丁克吗？」他沉默了三秒起身锁上了门：「林小姐，你对你的未来丈夫似乎有什么误解。」"},
    {"id":"sweet_002","label":"契约婚姻假戏真做","template":"甜宠_高糖轻虐","emoji":"💒","tags":["契约婚姻","先婚后爱","甜宠"],"inspiration":"女主为了救弟弟和素未谋面的集团太子爷签了一年契约婚姻。他冷漠疏离从不碰她，她恪守本分做表面夫妻。直到有一天她的青梅竹马找上门，他第一次失控——「林小姐，你有没有搞清楚，你是谁的太太？」当晚他撕毁契约把她压在墙上。"},
    {"id":"sweet_003","label":"我的室友是隐藏顶流","template":"甜宠_高糖轻虐","emoji":"🎤","tags":["娱乐圈","同居","甜宠"],"inspiration":"女主是音乐学院穷学生分到了一个高冷的男室友。他是校草独来独往从不和任何人说话。她默默喜欢他直到有一天在演唱会上被抽中上台——聚光灯打在「幸运观众」身上的那一刻她看到了舞台上戴着面具的顶流歌手摘下面具。是她的室友。他看着她耳麦里传来一声低笑：「终于抓到你了。」"},
    {"id":"sweet_004","label":"闪婚后老公是帝国继承人","template":"甜宠_高糖轻虐","emoji":"⚡","tags":["闪婚","豪门","甜宠"],"inspiration":"女主喝醉在民政局门口捡了个男人闪婚。婚后发现老公除了长得帅一无是处——没车没房天天窝在家打游戏。直到有一天他接了个电话脸色骤变：「爸，我说过我不回去继承家产。」她手里的泡面啪嗒掉在地上——她嫁的是帝国首富家的独子。"},
    {"id":"sweet_005","label":"校霸暗恋我三年被发现了","template":"甜宠_高糖轻虐","emoji":"🎓","tags":["校园","暗恋","甜宠"],"inspiration":"女主是学霸乖乖女和校霸井水不犯河水。毕业那天他把她堵在空教室里：「你知不知道高一那次你借我的那支笔我到现在都没还？」她愣住，他从书包里掏出一个盒子——里面整整齐齐摆着她三年里掉过的所有东西：发卡、校徽、半块橡皮。"},
]


def get_presets(template_key: str = None) -> list:
    """获取预设列表，可按模板筛选"""
    if template_key:
        return [p for p in PRESETS if p["template"] == template_key]
    return PRESETS
