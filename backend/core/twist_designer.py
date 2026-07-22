"""NovelGenerator — Twist Designer: 剧情反转设计 Agent

职责: 在故事中融入出乎意料但逻辑自洽的剧情转折，增强可读性和悬念感。
覆盖:
- 反转类型库（身份反转/视角翻转/预期颠覆/虚假胜负/信息揭露）
- 反转点规划（章末/卷末/幕转折）
- 伏笔需求计算（每个反转需要多少前置线索）
- 反转强度控制（不让读者猜到的同时保持合理性）
- 反转钩子生成（为Writer提供反转写作指引）
"""

import json
import re
import logging
from typing import Optional
from openai import OpenAI

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════
# 反转类型目录
# ═══════════════════════════════════════════

TWIST_CATALOG = {
    "identity_reveal": {
        "name": "身份揭露",
        "description": "揭示某个角色的真实身份与表面身份完全不同",
        "strength": 9,
        "foreshadowing_needed": 3,  # 至少需要3个前置伏笔
        "placement": ["chapter_end", "volume_end"],
        "subtypes": [
            "盟友实为敌人（卧底反转）",
            "敌人实为守护者（误解反转）",
            "路人实为大佬（隐藏身份反转）",
            "主角身世揭露（血缘反转）",
        ],
        "examples": [
            "一路帮助主角的老者，实为最终Boss的分身",
            "被认为已死的挚友，以敌方将领身份现身",
            "主角的废物体质，其实是远古血脉被封印",
        ],
        "risk": "伏笔太明显会被读者提前猜到，太隐晦会显得突兀",
    },
    "perspective_flip": {
        "name": "视角翻转",
        "description": "从另一个视角重新诠释已发生的事件，颠覆读者的理解",
        "strength": 8,
        "foreshadowing_needed": 2,
        "placement": ["chapter_end", "mid_arc"],
        "subtypes": [
            "善良行径背后的残酷真相",
            "反派动机的合理性揭示",
            "胜利背后的巨大代价",
        ],
        "examples": [
            "主角以为自己在拯救村民——从村民视角看，他才是灾星",
            "反派的屠杀是为了阻止更大的灾难",
        ],
        "risk": "容易陷入'洗白反派'的俗套，需要足够的铺垫",
    },
    "expectation_subversion": {
        "name": "预期颠覆",
        "description": "打破读者基于套路的预期，给出完全不同的走向",
        "strength": 8,
        "foreshadowing_needed": 1,
        "placement": ["chapter_end", "any"],
        "subtypes": [
            "退婚不悔（反退婚流套路）",
            "奇遇是陷阱",
            "金手指有致命代价",
            "大决战以意想不到的方式结束",
        ],
        "examples": [
            "主角辛苦修炼到巅峰，发现整个世界只是一个牢笼",
            "被退婚的女方不是反派，而是被家族逼迫的受害者",
        ],
        "risk": "为反而反会激怒读者，必须逻辑自洽",
    },
    "false_outcome": {
        "name": "虚假胜负",
        "description": "表面的胜利是失败，表面的失败是布局",
        "strength": 7,
        "foreshadowing_needed": 2,
        "placement": ["chapter_end", "mid_chapter"],
        "subtypes": [
            "假胜实败（主角赢了战斗输了战略）",
            "假败实胜（表面失败实为诱敌）",
            "双输（双方都是输家，第三势力获利）",
        ],
        "examples": [
            "主角击败了Boss——但Boss临死前启动了真正的毁灭机关",
            "主角被击败后，敌人发现中计——主角的目标从来不是赢",
        ],
        "risk": "使用过多会让读者对'胜利'失去信任",
    },
    "information_bomb": {
        "name": "信息炸弹",
        "description": "在关键时刻揭露一个颠覆性的信息，改变整个故事格局",
        "strength": 9,
        "foreshadowing_needed": 4,
        "placement": ["volume_end", "act_transition"],
        "subtypes": [
            "世界观真相揭露",
            "历史被篡改的真相",
            "核心设定的真正含义",
        ],
        "examples": [
            "修炼体系的真相：所有修士的力量都来自一个垂死古神的生命",
            "主角所在的世界，其实是上层世界的'养殖场'",
        ],
        "risk": "信息量太大可能让读者觉得'机械降神'",
    },
}

# 反转节奏建议（基于总章节数）
TWIST_RHYTHM = {
    "short": {  # <100章
        "minor_twists": 5,      # 小反转（章末级别）
        "medium_twists": 2,     # 中等反转（卷末级别）
        "major_twists": 1,      # 大反转（幕转折/结局）
    },
    "medium": {  # 100-300章
        "minor_twists": 12,
        "medium_twists": 4,
        "major_twists": 2,
    },
    "long": {  # >300章
        "minor_twists": 20,
        "medium_twists": 8,
        "major_twists": 3,
    },
}


class TwistDesigner:
    """剧情反转设计师 Agent"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model

    # ═══════════════════════════════════════════
    # 公开接口
    # ═══════════════════════════════════════════

    def design_twists(self, plan: dict) -> dict:
        """为整部小说规划反转点
        
        Args:
            plan: 小说规划数据
            
        Returns:
            {
                "twists": [{id, type, chapter, strength, description, foreshadowing_plan, twist_hook}],
                "rhythm_analysis": str,
                "foreshadowing_map": {twist_id: [chapter_nums]}
            }
        """
        total_chapters = plan.get("outline", {}).get("total_chapters", 30)
        genre = plan.get("genre", "玄幻")
        
        # 确定反转节奏
        if total_chapters < 100:
            rhythm = TWIST_RHYTHM["short"]
        elif total_chapters < 300:
            rhythm = TWIST_RHYTHM["medium"]
        else:
            rhythm = TWIST_RHYTHM["long"]
        
        # 基于大纲分析反转机会
        volumes = plan.get("outline", {}).get("volumes", [])
        
        if not self.client or not self.model:
            return self._rule_based_twists(plan, rhythm)
        
        return self._llm_design_twists(plan, rhythm, volumes)

    def design_chapter_twist(
        self,
        chapter_num: int,
        plan: dict,
        chapter_outline: dict,
        prev_chapters_summary: str = "",
    ) -> dict:
        """为单章设计反转钩子
        
        Returns:
            {has_twist, twist_type, twist_text, foreshadowing_check, strength}
        """
        # 判断本章是否适合放反转
        is_suitable = self._is_twist_suitable(chapter_num, plan)
        
        if not is_suitable:
            return {"has_twist": False, "reason": "本章不适合放置反转（过渡章节或已有冲突高潮）"}
        
        if not self.client or not self.model:
            return self._simple_twist(chapter_num, chapter_outline)
        
        return self._llm_chapter_twist(chapter_num, plan, chapter_outline, prev_chapters_summary)

    def build_twist_prompt(self, twist_plan: dict) -> str:
        """将反转计划转为 Writer 可用的写作指引"""
        if not twist_plan.get("has_twist"):
            return ""
        
        twist_type = twist_plan.get("twist_type", "")
        twist_text = twist_plan.get("twist_text", "")
        foreshadowing = twist_plan.get("foreshadowing_check", "")
        
        parts = [f"## 🔄 本章反转设计\n"]
        parts.append(f"反转类型: {TWIST_CATALOG.get(twist_type, {}).get('name', twist_type)}")
        parts.append(f"反转核心: {twist_text}")
        
        if foreshadowing:
            parts.append(f"\n### 前置伏笔检查\n{foreshadowing}")
        
        parts.append(f"\n### 写作要求")
        parts.append("- 反转前保持自然叙述，不要刻意暗示")
        parts.append("- 反转点放在本章70%-90%位置（给读者消化的空间）")
        parts.append("- 反转后留白——不要立即解释，让冲击力沉淀")
        parts.append("- 确保前文至少有一个微小线索可以被回溯解读")
        
        return "\n".join(parts)

    # ═══════════════════════════════════════════
    # 内部分析
    # ═══════════════════════════════════════════

    def _is_twist_suitable(self, chapter_num: int, plan: dict) -> bool:
        """判断某章是否适合反转"""
        volumes = plan.get("outline", {}).get("volumes", [])
        chapter_outline = None
        for vol in volumes:
            for ch in vol.get("chapters", []):
                if int(ch.get("number", 0)) == chapter_num:
                    chapter_outline = ch
                    break
        
        if not chapter_outline:
            return False
        
        # 已经是高潮章节 → 不适合再放反转（冲突叠太多）
        conflict = chapter_outline.get("conflict", "")
        if "强度5" in str(conflict) or "生死" in str(conflict):
            return False
        
        # 卷的第一章 → 适合（建立预期后颠覆）
        for vol in volumes:
            chapters = vol.get("chapters", [])
            if chapters and int(chapters[0].get("number", 0)) == chapter_num:
                return False  # 卷首不适合反转（先建立期待）
        
        # 卷的最后一章 → 特别适合
        for vol in volumes:
            chapters = vol.get("chapters", [])
            if chapters and int(chapters[-1].get("number", 0)) == chapter_num:
                return True
        
        # 每5章一次小反转
        return chapter_num % 5 == 0

    def _rule_based_twists(self, plan: dict, rhythm: dict) -> dict:
        """基于规则的本地反转规划"""
        total = plan.get("outline", {}).get("total_chapters", 30)
        
        twists = []
        
        # 分配反转
        minor_count = rhythm["minor_twists"]
        medium_count = rhythm["medium_twists"]
        major_count = rhythm["major_twists"]
        
        # 大反转: 放在幕转折处
        acts = self._get_act_boundaries(plan)
        for i, act_ch in enumerate(acts[:major_count]):
            twists.append({
                "id": f"T_major_{i+1}",
                "type": "information_bomb",
                "chapter": act_ch,
                "strength": 9,
                "description": f"第{act_ch}章幕转折处的大反转",
                "foreshadowing_plan": f"需要在前{max(1, act_ch-5)}-{act_ch-1}章埋设至少3个伏笔",
                "twist_hook": "在幕转折处揭示颠覆性信息，改变故事格局",
            })
        
        # 中等反转: 卷末
        vol_ends = self._get_volume_ends(plan)
        for i, ve in enumerate(vol_ends[:medium_count]):
            if ve not in [t["chapter"] for t in twists]:
                twists.append({
                    "id": f"T_medium_{i+1}",
                    "type": "identity_reveal",
                    "chapter": ve,
                    "strength": 8,
                    "description": f"第{ve}章卷末反转",
                    "foreshadowing_plan": f"需要在前{max(1, ve-3)}-{ve-1}章埋设至少2个伏笔",
                    "twist_hook": "卷末揭示隐藏信息，颠覆读者对某个角色的认知",
                })
        
        # 小反转: 均匀分布
        step = max(1, total // (minor_count + 1))
        for i in range(minor_count):
            ch = (i + 1) * step
            if ch > total:
                break
            if ch not in [t["chapter"] for t in twists]:
                twists.append({
                    "id": f"T_minor_{i+1}",
                    "type": "expectation_subversion",
                    "chapter": ch,
                    "strength": 7,
                    "description": f"第{ch}章小反转",
                    "foreshadowing_plan": f"需要在前一章中隐含至少1个线索",
                    "twist_hook": "小规模预期颠覆，给读者'原来如此'的体验",
                })
        
        return {
            "twists": sorted(twists, key=lambda t: t["chapter"]),
            "rhythm_analysis": f"共{len(twists)}个反转点（{major_count}大/{medium_count}中/{minor_count}小）",
            "foreshadowing_map": {t["id"]: [max(1, t["chapter"]-3), t["chapter"]-1] for t in twists},
        }

    def _get_act_boundaries(self, plan: dict) -> list:
        """获取幕边界章节号"""
        volumes = plan.get("outline", {}).get("volumes", [])
        boundaries = []
        for vol in volumes:
            act = vol.get("act", "")
            if "对抗" in act or "解决" in act:
                chapters = vol.get("chapters", [])
                if chapters:
                    boundaries.append(int(chapters[0].get("number", 0)))
        return boundaries

    def _get_volume_ends(self, plan: dict) -> list:
        """获取每卷最后一章的章节号"""
        volumes = plan.get("outline", {}).get("volumes", [])
        ends = []
        for vol in volumes:
            chapters = vol.get("chapters", [])
            if chapters:
                ends.append(int(chapters[-1].get("number", 0)))
        return ends

    def _simple_twist(self, chapter_num, chapter_outline):
        """无LLM时的简单反转生成"""
        return {
            "has_twist": True,
            "twist_type": "expectation_subversion",
            "twist_text": f"在第{chapter_num}章结尾处设置意外转折",
            "foreshadowing_check": "确保本章前文有至少1个可回溯的线索",
            "strength": 7,
        }

    def _llm_design_twists(self, plan, rhythm, volumes):
        """LLM反转规划"""
        outline_summary = self._summarize_outline(plan)
        total = plan.get("outline", {}).get("total_chapters", 30)
        
        system = """你是小说反转设计大师。为故事规划出乎意料但逻辑自洽的剧情转折。

原则:
1. 反转不能无中生有——必须有前置伏笔支撑
2. 反转频率要合理——太多让读者麻木，太少让故事平淡
3. 不同类型交替——身份揭露/视角翻转/预期颠覆/虚假胜负/信息炸弹
4. 反转强度渐进——前期小反转，后期大反转
5. 反转后留消化空间——不能连续两次大反转

输出JSON:
```json
{
  "twists": [
    {
      "id": "T_001",
      "type": "identity_reveal|perspective_flip|expectation_subversion|false_outcome|information_bomb",
      "chapter": 章节号,
      "strength": 1-10,
      "description": "反转内容描述",
      "foreshadowing_plan": "需要哪些前置伏笔",
      "twist_hook": "为Writer提供的反转钩子"
    }
  ],
  "rhythm_analysis": "反转节奏分析"
}
```"""

        user = f"""小说大纲:
{outline_summary}

总章节数: {total}
反转配额: 大反转{rhythm['major_twists']}个, 中等反转{rhythm['medium_twists']}个, 小反转{rhythm['minor_twists']}个

请规划反转分布。只输出JSON。"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.7,
                max_tokens=3072,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            
            twists = result.get("twists", [])
            log.info(f"TwistDesigner: {len(twists)} twists designed")
            
            return {
                "twists": twists,
                "rhythm_analysis": result.get("rhythm_analysis", ""),
                "foreshadowing_map": self._build_foreshadowing_map(twists),
            }
        except Exception as e:
            log.error(f"TwistDesigner failed: {e}")
            return self._rule_based_twists(plan, rhythm)

    def _llm_chapter_twist(self, chapter_num, plan, chapter_outline, prev_summary):
        """LLM单章反转设计"""
        system = """你是单章反转设计师。为指定章节设计结尾反转。

要求:
- 反转必须与本章核心事件相关
- 反转前文需要有至少一个可回溯的线索
- 反转后不给解释（留给下一章）
- 输出JSON:
```json
{
  "has_twist": true,
  "twist_type": "类型",
  "twist_text": "反转的核心内容（50字内）",
  "foreshadowing_check": "前文应埋设的伏笔描述",
  "strength": 1-10
}
```"""

        user = f"第{chapter_num}章: {json.dumps(chapter_outline, ensure_ascii=False)[:300]}\n前情: {prev_summary[:200]}"

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.8,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            log.error(f"Chapter twist failed: {e}")
            return {"has_twist": False}

    def _summarize_outline(self, plan: dict) -> str:
        """摘要化大纲"""
        parts = []
        for vol in plan.get("outline", {}).get("volumes", []):
            vol_info = f"第{vol['number']}卷「{vol.get('title','')}」({vol.get('act','')}): {vol.get('theme','')}"
            ch_list = []
            for ch in vol.get("chapters", [])[:5]:
                ch_list.append(f"  第{ch['number']}章: {ch.get('summary','')[:30]}")
            parts.append(vol_info + "\n" + "\n".join(ch_list))
        return "\n\n".join(parts)

    def _build_foreshadowing_map(self, twists: list) -> dict:
        """构建反转→伏笔映射"""
        fmap = {}
        for t in twists:
            ch = t.get("chapter", 0)
            deps = t.get("foreshadowing_needed", 2)
            reveal_ch = ch
            plant_start = max(1, ch - deps * 2)
            fmap[t.get("id", "")] = list(range(plant_start, reveal_ch))
        return fmap
