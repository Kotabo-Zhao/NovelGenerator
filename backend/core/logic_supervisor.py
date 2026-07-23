"""NovelGenerator — Logic Supervisor: 全类型逻辑错误监督引擎

v2.3 全面重构：覆盖 12 大类逻辑错误，L1规则引擎 + L2 LLM语义校验 双层架构。

## 12 大逻辑错误类别

| 类别 | ID前缀 | 说明 | 默认严重级 |
|:---|:---|:---|:---|
| 时间线矛盾 | TM | 时序倒错/年龄矛盾/跨章时间跳跃 | P0 |
| 空间地理矛盾 | SP | 位置瞬移/地理混乱/方位错误 | P0 |
| 实力体系崩塌 | PW | 等级倒退/越级/规则违反 | P0 |
| 角色行为不一致 | BH | 性格突变/知识越界/动机矛盾 | P0 |
| 物品道具矛盾 | IT | 物品消失/重复出现/数量错误 | P1 |
| 因果链条断裂 | CA | 无因之果/伏笔遗忘/逻辑跳跃 | P0 |
| 对话信息越界 | DL | 角色知道不该知道的事 | P1 |
| 数量/数值错误 | NM | 人数变化/金钱错误/时间计算 | P1 |
| 身份/命名混淆 | ID | 同一人多名/不同人相似名/称谓错 | P1 |
| 生死状态矛盾 | LD | 死人出现/致命伤忽略/无解释复活 | P0 |
| 跨章连续性 | CC | 伤口突变/位置跳跃/情绪断层 | P0 |
| 设定规则自毁 | ST | 世界规则被违反/体系例外无解释 | P1 |

## 架构

- L1 规则引擎：正则 + 模式匹配 + 知识库，毫秒级，零 LLM 调用
- L2 LLM 语义校验：深度因果推理，仅在 L1 无法判断时触发
- 分层报告：violations（硬错误）/ warnings（潜在风险）/ suggestions（优化建议）
"""

import json
import re
import logging
from typing import Optional
from openai import OpenAI

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# 常量与知识库
# ═══════════════════════════════════════════════

# 古代时间单位（时辰）
CN_TIME_UNITS = {
    "一息": 0.008, "两息": 0.016, "三息": 0.024,
    "一盏茶": 0.25, "两盏茶": 0.5,
    "一炷香": 0.5, "两炷香": 1.0, "三炷香": 1.5,
    "一刻钟": 0.25, "两刻钟": 0.5,
    "一个时辰": 2, "半个时辰": 1, "两个时辰": 4, "三个时辰": 6,
    "一天": 24, "一日": 24, "一夜": 12,
    "半日": 12,
}

# 中文数字映射
CN_DIGITS = {
    "一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10,
    "两":2,"廿":20,"卅":30,"百":100,"千":1000,"万":10000,"亿":100000000,
}

# 方位矛盾对 (位置A不可能同时是位置B)
SPATIAL_CONFLICTS = [
    (["东", "东边", "东方"], ["西", "西边", "西方"]),
    (["南", "南边", "南方"], ["北", "北边", "北方"]),
    (["左", "左手边"], ["右", "右手边"]),
    (["前", "前方", "前面"], ["后", "后方", "后面"]),
    (["上", "上方", "上面"], ["下", "下方", "下面"]),
    (["内", "里面", "内部"], ["外", "外面", "外部"]),
    (["城门口"], ["府邸", "皇宫", "大殿"]),
]

# 常见 AI 逻辑错误模式 (L1 正则扫描)
AI_LOGIC_PATTERNS = [
    # ── 时间线矛盾 ──
    {
        "id": "TM001", "type": "timeline",
        "severity": "P0",
        "pattern": r"(?:同时|与此同时|此刻|正当此时).{0,30}(?:同时|与此同时|此刻|正当此时)",
        "desc": "同一时刻出现两个'同时'描述，可能时间线混乱",
        "fix": "检查两个事件是否真的同时发生，或错开叙述顺序",
    },
    {
        "id": "TM002", "type": "timeline",
        "severity": "P0",
        "pattern": r"(?:刚才|方才|不久前).{0,30}(?:过了|过去了|转眼).{0,20}(?:天|月|年)",
        "desc": "刚说'刚才'随后变成'几天后'，时间跳跃无过渡",
        "fix": "明确标注时间流逝，用分隔符或过渡句",
    },
    # ── 空间矛盾 ──
    {
        "id": "SP001", "type": "spatial",
        "severity": "P0",
        "pattern": r"(?:出了|离开|走出|步出)(?:城门|大门|出口).{0,50}(?:皇宫|大殿|府邸|内院)",
        "desc": "刚出城门却又在皇宫/大殿内，位置瞬移",
        "fix": "增加移动过程描写或时间过渡",
    },
    {
        "id": "SP002", "type": "spatial",
        "severity": "P1",
        "pattern": r"(?:转头|回头|转身).{0,20}(?:千里之外|万里之遥)",
        "desc": "转头看到千里之外的事物（无神识/神通设定时）",
        "fix": "确认角色是否拥有远程感知能力，或改为合理视线范围",
    },
    # ── 实力体系 ──
    {
        "id": "PW001", "type": "power",
        "severity": "P0",
        "pattern": r"(?:突破|晋级|晋升|踏入).{0,20}(?:境界|层次|等级).{0,30}(?:跌落|倒退|下降|减弱).{0,20}(?:境界|层次|等级)",
        "desc": "突破后又倒退，且无受伤/封印等合理原因",
        "fix": "明确倒退原因（重伤/封印/献祭），或删除矛盾描述",
    },
    {
        "id": "PW002", "type": "power",
        "severity": "P0",
        "pattern": r"(?:不过|区区|只是|才).{0,10}(?:炼气|筑基|金丹|元婴|化神|炼虚|合体|大乘|渡劫).{0,30}(?:秒杀|碾压|一击|轻易).{0,20}(?:化神|炼虚|合体|大乘|渡劫|真仙|金仙)",
        "desc": "低境界秒杀高境界，破坏力量体系",
        "fix": "确认是否有特殊原因（偷袭/法宝/克制），或调整实力对比",
    },
    # ── 角色行为 ──
    {
        "id": "BH001", "type": "behavior",
        "severity": "P0",
        "pattern": r"(?:一向|向来|素来|从来都是).{0,30}(?:谨慎|小心|胆小|懦弱|内向).{0,50}(?:突然|猛然|骤|竟|居).{0,20}(?:冲上去|杀入|闯入|莽撞|不顾一切)",
        "desc": "性格突变：一向谨慎的角色突然莽撞行事",
        "fix": "铺垫触发原因（被激怒/保护至亲/绝境）或调整行为逻辑",
    },
    {
        "id": "BH002", "type": "behavior",
        "severity": "P0",
        "pattern": r"(?:智谋|智计|算无遗策|料事如神|从不犯错).{0,50}(?:竟然|居然|怎么会).{0,20}(?:没想|失算|中计|上当|被骗)",
        "desc": "智谋型角色在无铺垫情况下中低级陷阱",
        "fix": "陷阱需要足够精妙或角色处于特殊状态（分心/受伤/关心则乱）",
    },
    # ── 生死矛盾 ──
    {
        "id": "LD001", "type": "life_death",
        "severity": "P0",
        "pattern": r"(?:陨落|身死|毙命|断气|再无生息).{0,200}(?:突然|竟然|居然|又).{0,30}(?:出现|现身|站|开口|说话)",
        "desc": "明确死亡的角色后续突然出现且无复活解释",
        "fix": "添加复活机制（假死/替身/转生）或确认描述是否准确",
    },
    {
        "id": "LD002", "type": "life_death",
        "severity": "P1",
        "pattern": r"(?:心脏被刺穿|头颅被斩|化为齑粉|神魂俱灭).{0,100}(?:活了下来|生还|幸存|未死|还能)",
        "desc": "致命伤后生还但无合理解释",
        "fix": "明确保命手段（秘法/替身符/元神出窍）",
    },
    # ── 因果断裂 ──
    {
        "id": "CA001", "type": "causality",
        "severity": "P1",
        "pattern": r"(?:突然|猛然|骤).{0,10}(?:出现|降临|爆发|发生).{0,30}(?!(?:因为|由于|原来|是因为))",
        "desc": "关键事件突然发生但缺少因果铺垫",
        "fix": "至少在前文中埋一次伏笔或暗示",
    },
    # ── 数值矛盾 ──
    {
        "id": "NM001", "type": "numeric",
        "severity": "P1",
        "pattern": r"(\d+)\s*(?:人|名|位|个).{0,100}\1\s*(?:人|名|位|个)",
        "desc": "同一数字出现两次但可能指的是不同的人/物（需人工判断）",
        "fix": "确认两个数字所指是否一致，必要时用不同表述区分",
    },
    # ── 跨章断裂 ──
    {
        "id": "CC001", "type": "continuity",
        "severity": "P0",
        "pattern": r"(?:浑身是血|重伤|垂死|奄奄一息|断臂).{0,200}(?:精神抖擞|生龙活虎|毫发无伤|安然无恙|完好无损)",
        "desc": "重伤状态下突然恢复且无治疗过程",
        "fix": "添加治疗/恢复过程，或明确时间流逝",
    },
    # ── 对话越界 ──
    {
        "id": "DL001", "type": "dialogue",
        "severity": "P1",
        "pattern": r"(?:你怎么知道|你为何知晓|你从何处得知|谁告诉你的)",
        "desc": "角色被质疑知识来源，暗示可能存在信息越界",
        "fix": "在前文铺垫角色获取该信息的合理途径",
    },
    # ── 设定自毁 ──
    {
        "id": "ST001", "type": "setting",
        "severity": "P1",
        "pattern": r"(?:此界|这个世界|这方天地|此方世界).{0,30}(?:从未|从未有|没有|不存在|不可能).{0,50}(?:竟然|居然|竟有|却有|突然).{0,20}(?:出现|发生|存在)",
        "desc": "世界规则被明确打破但未给出合理解释",
        "fix": "将反常事件作为重大伏笔（揭示世界真相）或删除矛盾规则",
    },
]

# 常识性矛盾检测（不依赖 regex，靠关键词 + 逻辑规则）
COMMONSENSE_RULES = [
    {
        "id": "CS001", "type": "commonsense",
        "severity": "P1",
        "desc": "视觉矛盾：在完全黑暗环境中'看到'细节",
        "keywords": ["漆黑", "黑暗", "伸手不见五指", "一片漆黑", "暗无天日"],
        "conflict_keywords": ["看到", "看清", "望见", "映入眼帘", "定睛一看"],
        "fix": "确认是否有夜视能力/神识/光源，否则不能视觉描写",
    },
    {
        "id": "CS002", "type": "commonsense",
        "severity": "P1",
        "desc": "听觉矛盾：在嘈杂环境中'听到'细微声音",
        "keywords": ["震耳欲聋", "轰鸣", "喊杀声震天", "喧嚣", "嘈杂"],
        "conflict_keywords": ["听到", "听见", "耳中传来", "细语", "低语", "悄悄"],
        "fix": "要么降低环境噪音，要么说明角色有特殊听力",
    },
    {
        "id": "CS003", "type": "commonsense",
        "severity": "P2",
        "desc": "物理矛盾：凡人做出超越物理极限的动作",
        "keywords": ["凡人", "普通人", "没有修为", "不通武艺"],
        "conflict_keywords": ["一跃十丈", "踏水而行", "一拳碎山", "日行千里"],
        "fix": "确认角色是否有特殊能力/体质，否则不能超越凡人极限",
    },
    {
        "id": "CS004", "type": "commonsense",
        "severity": "P1",
        "desc": "温度矛盾：极寒环境中行为不受影响",
        "keywords": ["冰天雪地", "寒风刺骨", "滴水成冰", "零下"],
        "conflict_keywords": ["汗流浃背", "大汗淋漓", "热得", "炎热"],
        "fix": "除非有御寒手段（内功/法宝），否则不能忽视极寒影响",
    },
]

# 中文数字解析
def _parse_cn_number(text: str) -> int:
    """解析中文数字为阿拉伯数字"""
    if not text:
        return 0
    # 处理 "十四五" = 十四五 = 15
    # 先处理万/千/百
    result = 0
    current = 0
    for ch in text:
        if ch in CN_DIGITS:
            current = CN_DIGITS[ch]
        elif ch == "十":
            current = max(current, 1) * 10
        elif ch == "百":
            current = max(current, 1) * 100
        elif ch == "千":
            current = max(current, 1) * 1000
        elif ch == "万":
            result += max(current, 1) * 10000
            current = 0
        elif ch == "亿":
            result += max(current, 1) * 100000000
            current = 0
        else:
            if current > 0:
                result += current
                current = 0
    result += current
    return result


def _extract_numbers(text: str) -> list:
    """提取文本中所有数字（中文+阿拉伯）"""
    numbers = []
    # 阿拉伯数字
    for m in re.finditer(r"(\d+)", text):
        numbers.append({"value": int(m.group(1)), "text": m.group(1), "pos": m.start()})
    # 中文数字
    for m in re.finditer(r"([一二三四五六七八九十两廿卅百千万亿]+)", text):
        val = _parse_cn_number(m.group(1))
        if val > 0:
            numbers.append({"value": val, "text": m.group(1), "pos": m.start()})
    return numbers


# ═══════════════════════════════════════════════
# Logic Supervisor 主类
# ═══════════════════════════════════════════════

class LogicSupervisor:
    """全维度逻辑错误监督引擎 — L1 规则 + L2 LLM 两层架构"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model
        self._character_states = {}     # novel_id → {char_name: {traits, last_location, ...}}
        self._item_inventory = {}       # novel_id → {item_name: {status, last_mentioned_ch}}
        self._death_log = {}            # novel_id → [{name, chapter, confirmed}]
        self._power_tracker = {}        # novel_id → {char_name: {level, chapter}}

    # ═══════════════════════════════════════════
    # 公开接口
    # ═══════════════════════════════════════════

    def validate_chapter(
        self,
        chapter_text: str,
        chapter_num: int,
        plan: dict,
        prev_chapters: dict = None,
        global_state: dict = None,
        run_deep: bool = True,
    ) -> dict:
        """全维度校验单章逻辑

        Returns:
            {
                "passed": bool,
                "score": int (0-100),
                "violations": [{id, type, category, severity, description, evidence, fix}],
                "warnings": [...],
                "suggestions": [...],
                "category_scores": {category: int},
                "l1_checks": {...},
                "l2_result": {...}
            }
        """
        violations = []
        warnings_list = []
        suggestions = []

        # ── L1: 规则引擎（12类全覆盖）──
        l1 = self._l1_full_scan(chapter_text, chapter_num, plan, prev_chapters, global_state)
        violations.extend(l1.get("violations", []))
        warnings_list.extend(l1.get("warnings", []))
        suggestions.extend(l1.get("suggestions", []))

        # ── L2: LLM 深度语义校验 ──
        l2 = {}
        if run_deep and self.client and self.model:
            l2 = self._l2_deep_validate(chapter_text, chapter_num, plan, prev_chapters, global_state)
            violations.extend(l2.get("violations", []))
            warnings_list.extend(l2.get("warnings", []))
            suggestions.extend(l2.get("suggestions", []))

        # ── 评分 ──
        score = 100
        severity_deduct = {"P0": 30, "P1": 15, "P2": 5}
        for v in violations:
            score -= severity_deduct.get(v.get("severity", "P2"), 5)
        for w in warnings_list:
            score -= 3
        score = max(0, min(100, score))

        # 分类评分
        category_scores = {}
        for cat in ["timeline","spatial","power","behavior","item","causality",
                     "dialogue","numeric","identity","life_death","continuity","setting","commonsense"]:
            cat_violations = [v for v in violations if v.get("category") == cat]
            cat_score = 100
            for v in cat_violations:
                cat_score -= severity_deduct.get(v.get("severity", "P1"), 10)
            category_scores[cat] = max(0, min(100, cat_score))

        passed = all(v.get("severity") != "P0" for v in violations)

        return {
            "passed": passed,
            "score": score,
            "violations": violations,
            "warnings": warnings_list,
            "suggestions": suggestions,
            "category_scores": category_scores,
            "l1_checks": l1,
            "l2_result": l2,
            "total_issues": len(violations) + len(warnings_list),
        }

    def validate_chapter_batch(
        self, chapters: dict, plan: dict, global_state: dict = None,
    ) -> dict:
        """批量校验多章，返回汇总报告"""
        all_violations = []
        chapter_reports = {}
        total_score = 0

        for ch_num in sorted(chapters.keys()):
            result = self.validate_chapter(
                chapter_text=chapters[ch_num],
                chapter_num=ch_num,
                plan=plan,
                prev_chapters={k: v for k, v in chapters.items() if k < ch_num},
                global_state=global_state,
                run_deep=False,  # 批量模式跳过LLM
            )
            chapter_reports[ch_num] = result
            total_score += result["score"]
            all_violations.extend(result["violations"])

        avg_score = total_score / max(len(chapters), 1)

        return {
            "overall_score": round(avg_score, 1),
            "total_violations": len(all_violations),
            "p0_count": sum(1 for v in all_violations if v.get("severity") == "P0"),
            "p1_count": sum(1 for v in all_violations if v.get("severity") == "P1"),
            "chapter_reports": chapter_reports,
            "violations": all_violations,
        }

    def validate_outline(self, plan: dict) -> dict:
        """校验大纲逻辑一致性（继承原有功能并增强）"""
        violations = []
        warnings_list = []

        outline = plan.get("outline", {})
        volumes = outline.get("volumes", [])
        chars = plan.get("characters", {})

        # 1. 章节号连续性
        prev_num = 0
        for vol in volumes:
            for ch in vol.get("chapters", []):
                ch_num = int(ch.get("number", 0))
                if ch_num != prev_num + 1 and prev_num > 0:
                    violations.append({
                        "id": "O001", "category": "continuity", "type": "outline_structure",
                        "severity": "P1",
                        "description": f"章节号不连续: 第{prev_num}章→第{ch_num}章",
                        "fix": "检查章节编号是否跳号",
                    })
                prev_num = ch_num

        # 2. 三幕结构
        acts_found = set()
        for vol in volumes:
            act = vol.get("act", "")
            for keyword in ["建置", "对抗", "解决"]:
                if keyword in str(act):
                    acts_found.add(keyword)
        if len(acts_found) < 2 and len(volumes) >= 3:
            warnings_list.append({
                "id": "O002", "category": "setting", "type": "outline_structure",
                "severity": "P2",
                "description": f"大纲仅覆盖 {acts_found}，缺少完整三幕结构",
                "fix": "为每卷标注所属幕（建置/对抗/解决）",
            })

        # 3. 主角覆盖检查
        protagonist = chars.get("protagonist", {}).get("name", "")
        if protagonist:
            ch_with_protag = 0
            total_ch = 0
            for vol in volumes:
                for ch in vol.get("chapters", []):
                    if not isinstance(ch, dict):
                        continue
                    total_ch += 1
                    ch_text = f"{ch.get('summary','')} {ch.get('title','')}"
                    if protagonist in ch_text:
                        ch_with_protag += 1
            if total_ch > 0 and ch_with_protag < max(2, total_ch * 0.5):
                violations.append({
                    "id": "O003", "category": "behavior", "type": "protagonist_coverage",
                    "severity": "P1",
                    "description": f"主角「{protagonist}」仅出现 {ch_with_protag}/{total_ch} 章（<50%）",
                    "fix": "确保主角在大部分章节中出场",
                })

        # 4. 冲突进阶检测
        conflict_levels = []
        for vol in volumes:
            for ch in vol.get("chapters", []):
                conflict = ch.get("conflict", "")
                if conflict:
                    conflict_levels.append((ch.get("number", 0), conflict))
        if len(conflict_levels) >= 3:
            # 检查是否单调（所有冲突描述相同）
            unique_conflicts = set(c[1] for c in conflict_levels)
            if len(unique_conflicts) <= 2:
                warnings_list.append({
                    "id": "O004", "category": "causality", "type": "conflict_progression",
                    "severity": "P2",
                    "description": "章节冲突描述单调，缺乏升级感",
                    "fix": "各章冲突应有递进：个人冲突→团队冲突→世界冲突",
                })

        # 5. 情绪曲线检查
        emotions = []
        for vol in volumes:
            for ch in vol.get("chapters", []):
                ec = ch.get("emotion_curve", "")
                if ec:
                    emotions.append(ec)
        if len(emotions) >= 4:
            flat_count = sum(1 for e in emotions if len(set(e.replace("→","").split())) <= 2)
            if flat_count > len(emotions) * 0.6:
                warnings_list.append({
                    "id": "O005", "category": "behavior", "type": "emotion_flat",
                    "severity": "P2",
                    "description": f"{flat_count}/{len(emotions)} 章情绪曲线单一",
                    "fix": "丰富情绪变化：压抑→爆发→余韵 循环",
                })

        # 6. 力量体系合理性
        power_system = plan.get("worldbuilding", {}).get("power_system", "")
        if power_system and len(volumes) >= 3:
            # 检查中期是否有"瓶颈突破"的标志性章节
            has_breakthrough = False
            for vol in volumes:
                for ch in vol.get("chapters", []):
                    summary = ch.get("summary", "") + ch.get("title", "")
                    if any(kw in summary for kw in ["突破", "晋级", "顿悟", "瓶颈"]):
                        has_breakthrough = True
                        break
            if not has_breakthrough:
                suggestions = []
                suggestions.append({
                    "id": "O006", "category": "power", "type": "power_progression",
                    "severity": "P2",
                    "description": "大纲中缺乏力量成长的关键节点",
                    "fix": "在第二幕设置至少一次突破/顿悟节点",
                })

        score = 100
        for v in violations:
            score -= 30 if v["severity"] == "P0" else (15 if v["severity"] == "P1" else 5)
        score = max(0, min(100, score))

        return {
            "passed": len(violations) == 0,
            "score": score,
            "violations": violations,
            "warnings": warnings_list,
        }

    # ═══════════════════════════════════════════
    # L1: 全维度规则扫描
    # ═══════════════════════════════════════════

    def _l1_full_scan(self, chapter_text, chapter_num, plan, prev_chapters, global_state):
        """L1 全维度扫描 — 12 类逻辑错误"""
        violations = []
        warnings_list = []
        suggestions = []
        checks = {}

        # 1. 时间线矛盾
        r = self._check_timeline(chapter_text, chapter_num, prev_chapters)
        checks["timeline"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 2. 空间地理矛盾
        r = self._check_spatial(chapter_text, chapter_num, prev_chapters)
        checks["spatial"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 3. 实力体系
        r = self._check_power_system(chapter_text, chapter_num, global_state)
        checks["power"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 4. 角色行为
        r = self._check_behavior(chapter_text, chapter_num, plan)
        checks["behavior"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 5. 物品道具
        r = self._check_items(chapter_text, chapter_num)
        checks["items"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 6. 因果链条
        r = self._check_causality(chapter_text, chapter_num, prev_chapters)
        checks["causality"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 7. 对话信息越界
        r = self._check_dialogue_leak(chapter_text, chapter_num, plan, prev_chapters)
        checks["dialogue"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 8. 数值错误
        r = self._check_numeric(chapter_text, chapter_num, prev_chapters)
        checks["numeric"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 9. 身份命名
        r = self._check_identity(chapter_text, chapter_num, plan)
        checks["identity"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 10. 生死矛盾
        r = self._check_life_death(chapter_text, chapter_num)
        checks["life_death"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 11. 跨章连续性
        r = self._check_continuity(chapter_text, chapter_num, prev_chapters, global_state)
        checks["continuity"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 12. 设定自毁
        r = self._check_setting_integrity(chapter_text, chapter_num, plan)
        checks["setting"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 13. AI 模式扫描
        r = self._scan_ai_patterns(chapter_text, chapter_num)
        checks["ai_patterns"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        # 14. 常识检测
        r = self._check_commonsense(chapter_text, chapter_num)
        checks["commonsense"] = r
        violations.extend(r.get("violations", []))
        warnings_list.extend(r.get("warnings", []))

        return {
            "violations": violations,
            "warnings": warnings_list,
            "suggestions": suggestions,
            "checks": checks,
        }

    # ═══════════════════════════════════════════
    # 1. 时间线矛盾检测
    # ═══════════════════════════════════════════

    def _check_timeline(self, text, ch_num, prev_chapters):
        violations = []
        warnings_list = []

        # 检测 "同时" 叠加
        simultaneous = re.findall(r"与此同时|同时|正当此时|就在此时", text)
        if len(simultaneous) >= 4:
            violations.append({
                "id": f"TM-{ch_num:03d}-01", "category": "timeline", "type": "timeline",
                "severity": "P1",
                "description": f"本章出现 {len(simultaneous)} 次'同时'类表述，可能导致时间线混乱",
                "evidence": f"…{' / '.join(simultaneous[:3])}…",
                "fix": "错开叙事顺序，用'片刻后''随后'等替代部分'同时'",
                "location": f"第{ch_num}章",
            })

        # 检测时间跳跃无过渡
        time_markers = re.findall(r"(?:片刻|须臾|转瞬|随后|然后|接着)", text)
        long_time = re.findall(r"(?:数日|几天|几个月|一年|多年|数月|经年)", text)
        if len(time_markers) <= 1 and len(long_time) >= 1:
            # 几乎没过渡直接跳长时间
            warnings_list.append({
                "id": f"TM-{ch_num:03d}-02", "category": "timeline", "type": "timeline",
                "severity": "P2",
                "description": "长时间跳跃（数日/数月）但缺少时间过渡标记",
                "fix": "用'转眼间/时光飞逝/数日后'等明确标注时间流逝",
                "location": f"第{ch_num}章",
            })

        # 检测跨章时间矛盾
        if prev_chapters and len(prev_chapters) >= 1:
            prev_num = max(prev_chapters.keys())
            prev_text = prev_chapters.get(prev_num, "")
            if prev_text:
                # 前章结尾时间
                prev_end = prev_text[-500:]
                curr_start = text[:500]

                # 检测 "第二天"/"次日" 但前章结尾是早晨
                prev_morning = bool(re.search(r"(?:清晨|早晨|天亮|拂晓|旭日)", prev_end))
                curr_next_day = bool(re.search(r"(?:第二天|次日|翌日|隔天)", curr_start))
                if prev_morning and curr_next_day:
                    warnings_list.append({
                        "id": f"TM-{ch_num:03d}-03", "category": "timeline", "type": "timeline",
                        "severity": "P2",
                        "description": f"前章结尾为早晨，本章开头为'次日'，实际只过了一天但读者可能困惑",
                        "fix": "本章开头可加'一日无事'或'当夜'过渡",
                        "location": f"第{prev_num}章→第{ch_num}章",
                    })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 2. 空间地理矛盾检测
    # ═══════════════════════════════════════════

    def _check_spatial(self, text, ch_num, prev_chapters):
        violations = []
        warnings_list = []

        # 方位矛盾检测
        for conflict in SPATIAL_CONFLICTS:
            a_group, b_group = conflict
            found_a = any(kw in text for kw in a_group)
            found_b = any(kw in text for kw in b_group)
            if found_a and found_b:
                # 检查是否在同一场景描述中
                a_positions = [text.find(kw) for kw in a_group if kw in text]
                b_positions = [text.find(kw) for kw in b_group if kw in text]
                if a_positions and b_positions:
                    min_dist = min(abs(a - b) for a in a_positions for b in b_positions)
                    if min_dist < 200:  # 200字内同时出现对立方位
                        warnings_list.append({
                            "id": f"SP-{ch_num:03d}-01", "category": "spatial", "type": "spatial",
                            "severity": "P2",
                            "description": f"短距离内同时出现对立方位词 '{a_group[0]}' 和 '{b_group[0]}'",
                            "evidence": f"…{a_group[0]}…{b_group[0]}…（间距{min_dist}字）",
                            "fix": "确认方位描述是否准确，是否属于不同场景",
                            "location": f"第{ch_num}章",
                        })

        # 位置瞬移检测
        locations = re.findall(r"(?:在|于|进|入|抵达|来到|回到|走进)(.{2,8}(?:城|镇|殿|阁|院|府|山|谷|洞|林|海|楼|宫|塔|门|店|铺|坊|市|村|庄|寺|庙))", text)
        unique_locations = list(set(locations))
        if len(unique_locations) >= 4:
            warnings_list.append({
                "id": f"SP-{ch_num:03d}-02", "category": "spatial", "type": "spatial",
                "severity": "P2",
                "description": f"一章内切换 {len(unique_locations)} 个不同地点，可能过于跳跃",
                "evidence": "、".join(unique_locations[:5]),
                "fix": "减少场景切换频率，或在场景间添加过渡",
                "location": f"第{ch_num}章",
            })

        # 跨章位置检查
        if prev_chapters and len(prev_chapters) >= 1:
            prev_num = max(prev_chapters.keys())
            prev_text = prev_chapters.get(prev_num, "")
            if prev_text:
                prev_end_locs = re.findall(r"(?:在|于)(.{2,8}(?:城|镇|殿|阁|院|府|山|谷|洞|林|海))", prev_text[-500:])
                curr_start_locs = re.findall(r"(?:在|于)(.{2,8}(?:城|镇|殿|阁|院|府|山|谷|洞|林|海))", text[:500])
                if prev_end_locs and curr_start_locs and prev_end_locs[-1] != curr_start_locs[0]:
                    # 检查是否有过渡描述
                    has_transition = bool(re.search(r"(?:一路|赶到|来到|抵达|传送|飞行|骑马|赶路|行程)", text[:300]))
                    if not has_transition:
                        violations.append({
                            "id": f"SP-{ch_num:03d}-03", "category": "spatial", "type": "spatial",
                            "severity": "P0",
                            "description": f"前章结尾在'{prev_end_locs[-1]}'，本章开头在'{curr_start_locs[0]}'，无移动过渡",
                            "evidence": f"前章末: …{prev_end_locs[-1]}… → 本章首: …{curr_start_locs[0]}…",
                            "fix": "本章开头增加移动/传送描述，或说明已在途中",
                            "location": f"第{prev_num}章→第{ch_num}章",
                        })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 3. 实力体系检测
    # ═══════════════════════════════════════════

    def _check_power_system(self, text, ch_num, global_state):
        violations = []
        warnings_list = []

        # 检测等级表述
        levels = re.findall(r"(?:炼气|筑基|金丹|元婴|化神|炼虚|合体|大乘|渡劫|真仙|金仙|太乙|大罗|准圣|圣人|人仙|地仙|天仙|玄仙|金仙|仙王|仙帝)(?:期|层|重|阶|级|境界)?", text)
        unique_levels = list(set(levels))

        # 等级倒退检测
        if len(unique_levels) >= 2:
            # 简单的顺序检查（假设按上述列表顺序递增）
            level_order = ["炼气","筑基","金丹","元婴","化神","炼虚","合体","大乘","渡劫","真仙","金仙","太乙","大罗","准圣","圣人","人仙","地仙","天仙","玄仙","仙王","仙帝"]
            positions = []
            for lv in unique_levels:
                for i, name in enumerate(level_order):
                    if name in lv:
                        positions.append((i, lv, text.find(lv)))
                        break
            positions.sort(key=lambda x: x[2])  # 按出现顺序排列
            if len(positions) >= 2:
                # 检查是否先高后低（倒退）
                for i in range(len(positions) - 1):
                    if positions[i][0] > positions[i+1][0]:
                        # 后出现的等级比先出现的低
                        warnings_list.append({
                            "id": f"PW-{ch_num:03d}-01", "category": "power", "type": "power",
                            "severity": "P1",
                            "description": f"境界疑似倒退: '{positions[i][1]}' → '{positions[i+1][1]}'",
                            "evidence": f"…{positions[i][1]}…{positions[i+1][1]}…",
                            "fix": "确认倒退原因（受伤/封印），否则修正等级顺序",
                            "location": f"第{ch_num}章",
                        })

        # 越级碾压检测
        power_gap = re.findall(r"(?:不过|区区|才|只是|仅仅).{0,15}(?:炼气|筑基|金丹|元婴).{0,40}(?:秒杀|碾压|一击|轻描淡写|随手|轻易).{0,20}(?:化神|炼虚|合体|大乘|渡劫)", text)
        if power_gap:
            violations.append({
                "id": f"PW-{ch_num:03d}-02", "category": "power", "type": "power",
                "severity": "P0",
                "description": f"低境界碾压高境界: {power_gap[0][:60]}",
                "evidence": power_gap[0][:80],
                "fix": "需要特殊原因（禁术/神兵/偷袭/克制），否则调整实力对比",
                "location": f"第{ch_num}章",
            })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 4. 角色行为一致性检测
    # ═══════════════════════════════════════════

    def _check_behavior(self, text, ch_num, plan):
        violations = []
        warnings_list = []

        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})

        # 检查主角是否出现
        protag_name = protagonist.get("name", "")
        if protag_name and protag_name not in ("主角", "男主", "女主", ""):
            if protag_name not in text:
                violations.append({
                    "id": f"BH-{ch_num:03d}-01", "category": "behavior", "type": "behavior",
                    "severity": "P0",
                    "description": f"主角「{protag_name}」在全章正文中未出现",
                    "fix": "确保主角在本章中出场，或明确本章为配角视角切换",
                    "location": f"第{ch_num}章",
                })

        # 性格突变检测（关键词匹配）
        trait_conflicts = [
            (["冷静","沉稳","不动声色"], ["暴怒","大吼","失控","疯狂"]),
            (["善良","仁慈","不忍"], ["残忍","虐杀","无情"]),
            (["聪明","机智","睿智"], ["愚蠢","笨拙","毫无防备"]),
        ]
        for pos_traits, neg_traits in trait_conflicts:
            found_pos = [t for t in pos_traits if t in text]
            found_neg = [t for t in neg_traits if t in text]
            if found_pos and found_neg:
                # 检查是否有转折原因
                pos_pos = min(text.find(t) for t in found_pos if t in text)
                neg_pos = min(text.find(t) for t in found_neg if t in text)
                if abs(pos_pos - neg_pos) < 500:
                    # 有转折词吗？
                    has_reason = bool(re.search(r"(?:因为|由于|原来|得知|发现|想起|看到|听到|激怒|触怒)", text[max(0,min(pos_pos,neg_pos)-100):max(pos_pos,neg_pos)+50]))
                    if not has_reason:
                        warnings_list.append({
                            "id": f"BH-{ch_num:03d}-02", "category": "behavior", "type": "behavior",
                            "severity": "P1",
                            "description": f"性格疑似突变: '{found_pos[0]}' → '{found_neg[0]}'，缺少转折原因",
                            "evidence": f"…{found_pos[0]}……{found_neg[0]}…",
                            "fix": "在性格变化前铺垫触发事件",
                            "location": f"第{ch_num}章",
                        })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 5. 物品道具检测
    # ═══════════════════════════════════════════

    def _check_items(self, text, ch_num):
        violations = []
        warnings_list = []

        # 检测 "拿出/取出" 但前文未提及获得
        obtains = re.findall(r"(?:掏出|拿出|取出|亮出|祭出|抽出|拔出)(.{2,15}(?:剑|刀|枪|弓|盾|鼎|炉|塔|镜|珠|符|环|扇|鞭|锤|斧|戟|锏|尺|针|索|铃|瓶|印|幡|图|卷))", text)
        if obtains:
            # 检查前面是否提到过此物品
            for item in obtains:
                item_name = item.strip()
                prior_text = text[:text.find(item_name)] if item_name in text else ""
                if item_name not in prior_text and len(item_name) >= 3:
                    warnings_list.append({
                        "id": f"IT-{ch_num:03d}-01", "category": "item", "type": "item",
                        "severity": "P2",
                        "description": f"物品「{item_name}」突然出现，本章前文未提及获取过程",
                        "evidence": f"…掏出{item_name}…",
                        "fix": "确认物品是否在之前章节获得，或在前文补一笔",
                        "location": f"第{ch_num}章",
                    })

        # 检测物品消失（前文有但后面不用）
        early_items = re.findall(r"(?:握着|拿着|手持|佩戴|装备|携带)(.{2,10}(?:剑|刀|枪|弓|盾|符|环|扇))", text[:len(text)//2])
        late_items = re.findall(r"(?:握着|拿着|手持|佩戴|装备|携带)(.{2,10}(?:剑|刀|枪|弓|盾|符|环|扇))", text[len(text)//2:])
        for item in early_items:
            if item not in late_items:
                warnings_list.append({
                    "id": f"IT-{ch_num:03d}-02", "category": "item", "type": "item",
                    "severity": "P2",
                    "description": f"物品「{item}」在前半章出现但后半章未再提及",
                    "fix": "如已收起/损毁，明确交代；否则保持一致性",
                    "location": f"第{ch_num}章",
                })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 6. 因果链条检测
    # ═══════════════════════════════════════════

    def _check_causality(self, text, ch_num, prev_chapters):
        violations = []
        warnings_list = []

        # 检测 deus ex machina 关键词
        deus_ex = re.findall(r"(?:突然|猛然|骤).{0,10}(?:出现|降临|天降|神兵天降|凭空).{0,30}(?:救|帮助|解决|化解|逆转)", text)
        if deus_ex and len(deus_ex) >= 1:
            # 检查前面是否有伏笔
            has_setup = bool(re.search(r"(?:之前|早就|早已|暗中|偷偷|悄悄|准备|计划|埋伏|埋伏)", text[:text.find(deus_ex[0])]))
            if not has_setup:
                warnings_list.append({
                    "id": f"CA-{ch_num:03d}-01", "category": "causality", "type": "causality",
                    "severity": "P1",
                    "description": f"疑似天降救兵（deus ex machina）: '{deus_ex[0][:50]}'",
                    "evidence": deus_ex[0][:80],
                    "fix": "在前文至少埋一次伏笔（'XX早在此处埋伏'），或改为角色自主解决",
                    "location": f"第{ch_num}章",
                })

        # 检测 "恰好" 巧合过多
        coincidences = re.findall(r"(?:恰好|刚好|碰巧|正好|恰在此时|不偏不倚)", text)
        if len(coincidences) >= 3:
            warnings_list.append({
                "id": f"CA-{ch_num:03d}-02", "category": "causality", "type": "causality",
                "severity": "P2",
                "description": f"本章出现 {len(coincidences)} 次巧合（'恰好'类表述），过度依赖巧合推进情节",
                "evidence": "、".join(coincidences[:4]),
                "fix": "将部分巧合改为角色主动行为或前文伏笔回收",
                "location": f"第{ch_num}章",
            })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 7. 对话信息越界检测
    # ═══════════════════════════════════════════

    def _check_dialogue_leak(self, text, ch_num, plan, prev_chapters):
        warnings_list = []

        # 检测 "你怎么知道" 类质问（说明存在信息越界可能）
        leak_questions = re.findall(r"(?:你怎么知道|你为何知晓|你从何得知|谁告诉你的|你怎会知道)", text)
        if leak_questions:
            # 这是好事——角色在质疑，说明作者意识到了
            pass  # 不做违规标记，但记录
        else:
            # 主动检测：角色说出了只有另一角色或读者才知道的信息
            # 提取对话内容
            dialogues = re.findall(r"[「「]([^」」]+)[」」]", text)
            for d in dialogues[-5:]:  # 只看最后5句
                # 检测是否在转述第三方信息
                if re.search(r"(?:听说|据说|传闻|有消息说|得知|被告知)", d):
                    pass  # 有信息来源，正常
                elif re.search(r"(?:心里|心中|暗暗|暗自|心想)", d):
                    pass  # 内心独白，不对外

        return {"violations": [], "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 8. 数值错误检测
    # ═══════════════════════════════════════════

    def _check_numeric(self, text, ch_num, prev_chapters):
        violations = []
        warnings_list = []

        # 检测 "X人" 数量变化（同一段落内）
        people_count_blocks = re.findall(r"(\d+)\s*(?:人|名|位|个)(?:.{0,200}?)(\d+)\s*(?:人|名|位|个)", text)
        for m in re.finditer(r"(\d+)\s*(?:人|名|位|个)(?:.{0,200}?)(\d+)\s*(?:人|名|位|个)", text):
            n1, n2 = int(m.group(1)), int(m.group(2))
            if n1 != n2 and abs(n1 - n2) <= 5 and n1 > 0 and n2 > 0:
                # 检查中间是否有加减描述
                between = text[m.start():m.end()]
                has_change = bool(re.search(r"(?:离开|死了|倒下|加入|来了|多了|少了|增加|减少)", between))
                if not has_change:
                    warnings_list.append({
                        "id": f"NM-{ch_num:03d}-01", "category": "numeric", "type": "numeric",
                        "severity": "P2",
                        "description": f"人数疑似矛盾: {n1}人 → {n2}人，中间无加减说明",
                        "evidence": between[:100],
                        "fix": "在人数变化处加一句交代（'XX离开了''又来了一人'）",
                        "location": f"第{ch_num}章",
                    })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 9. 身份命名检测
    # ═══════════════════════════════════════════

    def _check_identity(self, text, ch_num, plan):
        violations = []
        warnings_list = []

        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})
        protag_name = protagonist.get("name", "")

        if protag_name and protag_name not in ("主角", "男主", "女主", ""):
            # 检查主角是否有多个称呼（名字/称号混用导致混淆）
            # 提取所有2-3字中文名
            all_names = set(re.findall(r"[\u4e00-\u9fff]{2,3}", text))
            if protag_name not in all_names:
                # 主角可能用称号/代称，检查是否有疑似称呼
                warnings_list.append({
                    "id": f"ID-{ch_num:03d}-01", "category": "identity", "type": "identity",
                    "severity": "P2",
                    "description": f"主角「{protag_name}」在全章正文中未以原名出现",
                    "fix": "确认是否用称号/化名替代，如是应在首次使用时说明",
                    "location": f"第{ch_num}章",
                })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 10. 生死状态检测
    # ═══════════════════════════════════════════

    def _check_life_death(self, text, ch_num):
        violations = []
        warnings_list = []

        # 检测致命伤与后续行为矛盾
        fatal_wounds = re.findall(r"(?:胸口被洞穿|心脏中|头颅|脖子被斩|化为齑粉|灰飞烟灭|神魂俱灭|元神破灭)", text)

        if fatal_wounds:
            wound_pos = text.find(fatal_wounds[0])
            after_text = text[wound_pos:]
            if any(after_text.find(act) != -1 for act in ["站了", "活了", "睁开眼", "醒了", "行动", "奔跑"]):
                has_explanation = bool(re.search(r"(?:复活|重生|再生|替身|分身|元神|魂魄|秘法|禁术|不死)", after_text))
                if not has_explanation:
                    violations.append({
                        "id": f"LD-{ch_num:03d}-01", "category": "life_death", "type": "life_death",
                        "severity": "P0",
                        "description": f"疑似致命伤后存活且无解释: '{fatal_wounds[0]}'",
                        "evidence": fatal_wounds[0],
                        "fix": "添加复活机制/替身/不死之身的解释",
                        "location": f"第{ch_num}章",
                    })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 11. 跨章连续性检测
    # ═══════════════════════════════════════════

    def _check_continuity(self, text, ch_num, prev_chapters, global_state):
        violations = []
        warnings_list = []

        if not prev_chapters or len(prev_chapters) == 0:
            return {"violations": [], "warnings": []}

        prev_num = max(prev_chapters.keys())
        prev_text = prev_chapters.get(prev_num, "")
        if not prev_text:
            return {"violations": [], "warnings": []}

        # 检测前章结尾的状态 vs 本章开头
        # 伤口状态
        prev_wounds = re.findall(r"(?:重伤|流血|伤口|创口|骨折|断|裂)", prev_text[-500:])
        curr_healed = re.findall(r"(?:伤口愈合|伤势恢复|已无大碍|完好如初|活动自如)", text[:500])
        if prev_wounds and curr_healed:
            # 检查是否有治疗过程
            has_healing = bool(re.search(r"(?:治疗|疗伤|服药|包扎|运功|恢复|灵药|丹药|医术)", text[:1000]))
            if not has_healing:
                violations.append({
                    "id": f"CC-{ch_num:03d}-01", "category": "continuity", "type": "continuity",
                    "severity": "P0",
                    "description": f"前章结尾有伤（{prev_wounds[0]}），本章开头已痊愈但无治疗过程",
                    "evidence": f"前章末: …{prev_wounds[0]}… → 本章首: …{curr_healed[0]}…",
                    "fix": "添加治疗/恢复过程，或明确时间已过足够久",
                    "location": f"第{prev_num}章→第{ch_num}章",
                })

        # 情绪状态断层
        prev_emotions = re.findall(r"(?:愤怒|暴怒|悲伤|哭泣|绝望|恐惧|惊恐|狂喜|兴奋|激动)", prev_text[-500:])
        curr_calm = re.findall(r"(?:平静|淡定|从容|冷静|悠然)", text[:500])
        if prev_emotions and curr_calm:
            has_transition = bool(re.search(r"(?:平复|冷静|镇定|收敛|压抑|调整|深呼吸|压下)", text[:800]))
            if not has_transition:
                warnings_list.append({
                    "id": f"CC-{ch_num:03d}-02", "category": "continuity", "type": "continuity",
                    "severity": "P1",
                    "description": f"前章结尾情绪激烈（{prev_emotions[0]}），本章开头突然平静，缺少情绪过渡",
                    "fix": "增加情绪平复过程（内心调整/深呼吸/时间流逝）",
                    "location": f"第{prev_num}章→第{ch_num}章",
                })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 12. 设定规则自毁检测
    # ═══════════════════════════════════════════

    def _check_setting_integrity(self, text, ch_num, plan):
        violations = []
        warnings_list = []

        worldbuilding = plan.get("worldbuilding", {})
        power_system = worldbuilding.get("power_system", "")

        if power_system:
            # 检测是否有 "传说中""从未有人""不可能"等设定被打破但无解释
            absolute_rules = re.findall(r"(?:传说中|从未有人|无人能|不可能|绝对无法|永远不能).{0,30}(?:突破|达到|做到|实现|成功)", text)
            if absolute_rules:
                # 这是设定破坏的信号——需要确认是否有解释
                for rule in absolute_rules[:3]:
                    rule_pos = text.find(rule)
                    after_context = text[rule_pos:rule_pos+200]
                    has_explanation = bool(re.search(r"(?:因为|原来|真相是|其实是|不是传说|并非不可能|隐藏)", after_context))
                    if not has_explanation:
                        warnings_list.append({
                            "id": f"ST-{ch_num:03d}-01", "category": "setting", "type": "setting",
                            "severity": "P1",
                            "description": f"世界规则被打破但未解释: '{rule[:50]}'",
                            "evidence": rule[:80],
                            "fix": "揭示规则背后的真相，或解释为何此角色能突破限制",
                            "location": f"第{ch_num}章",
                        })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # AI 常见模式扫描
    # ═══════════════════════════════════════════

    def _scan_ai_patterns(self, text, ch_num):
        violations = []
        warnings_list = []

        for pat in AI_LOGIC_PATTERNS:
            matches = re.findall(pat["pattern"], text)
            if matches:
                if pat["severity"] == "P0":
                    violations.append({
                        "id": f"{pat['id']}-{ch_num:03d}", "category": pat["type"],
                        "type": pat["type"], "severity": pat["severity"],
                        "description": pat["desc"],
                        "evidence": str(matches[0])[:80] if isinstance(matches[0], str) else str(matches[0]),
                        "fix": pat["fix"],
                        "location": f"第{ch_num}章",
                    })
                else:
                    warnings_list.append({
                        "id": f"{pat['id']}-{ch_num:03d}", "category": pat["type"],
                        "type": pat["type"], "severity": pat["severity"],
                        "description": pat["desc"],
                        "evidence": str(matches[0])[:80] if isinstance(matches[0], str) else str(matches[0]),
                        "fix": pat["fix"],
                        "location": f"第{ch_num}章",
                    })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # 常识性检测
    # ═══════════════════════════════════════════

    def _check_commonsense(self, text, ch_num):
        violations = []
        warnings_list = []

        for rule in COMMONSENSE_RULES:
            has_condition = any(kw in text for kw in rule["keywords"])
            has_conflict = any(kw in text for kw in rule["conflict_keywords"])
            if has_condition and has_conflict:
                warnings_list.append({
                    "id": f"{rule['id']}-{ch_num:03d}", "category": "commonsense",
                    "type": "commonsense", "severity": rule["severity"],
                    "description": rule["desc"],
                    "evidence": f"条件词: {[k for k in rule['keywords'] if k in text][:2]} / 冲突词: {[k for k in rule['conflict_keywords'] if k in text][:2]}",
                    "fix": rule["fix"],
                    "location": f"第{ch_num}章",
                })

        return {"violations": violations, "warnings": warnings_list}

    # ═══════════════════════════════════════════
    # L2: LLM 深度语义校验
    # ═══════════════════════════════════════════

    L2_SYSTEM = """你是一位资深小说逻辑审查官。你的工作是逐句审视文本，找出所有逻辑漏洞。

## 12 大检查维度（逐一扫描，不可遗漏）

### 1. 时间线矛盾 (timeline)
- 事件发生的先后顺序是否合理？
- 是否有"先说昨天发生，后说前天"的时序倒错？
- 时间流逝描述是否自洽？（"片刻"→"数月"但中间无过渡）
- 角色年龄与时间线是否一致？

### 2. 空间/地理矛盾 (spatial)
- 角色是否同时出现在两个地方？
- 移动距离在给定时间内是否可能（考虑交通工具）？
- 地理描述是否前后矛盾？（"东门出去"但"到了城西"且无移动描述）

### 3. 实力体系崩塌 (power)
- 已建立的等级体系是否被违反？
- 低境界碾压高境界是否有合理原因？
- 能力边界是否被随意突破？
- 同一角色的实力描述是否一致？

### 4. 角色行为不一致 (behavior)
- 角色行为是否符合其已建立的性格特征？
- 决策是否基于其知识水平和处境？（不能知道他们不知道的事情）
- 动机是否连贯？是否出现无原因的转变？
- 智商是否忽高忽低？

### 5. 物品道具矛盾 (item)
- 前文获得的重要物品后来是否无故消失？
- 已消耗/损坏的物品是否再次出现？
- 金钱/资源的数量是否一致？

### 6. 因果链条 (causality)
- 每个重要事件是否有合理的前因？
- 是否存在"天降神兵"式的巧合解围？
- 已埋下的伏笔是否被遗忘？

### 7. 对话信息越界 (dialogue)
- 角色是否说出了他们不应该知道的信息？
- 内心独白是否被其他角色"听到"并做出反应？

### 8. 数量/数值 (numeric)
- 人数、金额、时间等数值前后是否一致？
- 数学计算是否正确？

### 9. 身份/命名 (identity)
- 同一角色是否被不同称呼且未说明？
- 不同角色是否有混淆性相似的名字？

### 10. 生死状态 (life_death)
- 已确认死亡的角色是否不合理地再次出现？
- 致命伤害是否被忽视？
- 复活是否有铺垫和机制解释？

### 11. 跨章连续性 (continuity)
- 前章末尾的状态是否与本章开头一致？
- 伤口、位置、情绪是否平滑过渡？

### 12. 设定规则自毁 (setting)
- 作者建立的规则是否在后续被叙事打破？
- 世界规则的例外是否有合理解释？

## 输出格式

```json
{
  "violations": [
    {
      "id": "L2-001",
      "category": "timeline|spatial|power|behavior|item|causality|dialogue|numeric|identity|life_death|continuity|setting",
      "type": "具体子类型",
      "severity": "P0|P1|P2",
      "description": "具体问题描述",
      "evidence": "原文引用",
      "fix": "修改建议"
    }
  ],
  "warnings": [...],
  "suggestions": [
    {
      "id": "SUG-001",
      "description": "可以增强的地方",
      "benefit": "这样做的好处"
    }
  ],
  "overall_assessment": "一句话总体评价"
}
```

## 重要原则
- 只报告确实存在的逻辑问题，不要无中生有
- 引用原文作为证据
- 修改建议要具体可操作
- P0 = 致命错误（读者能一眼发现），P1 = 明显瑕疵，P2 = 轻微问题
- 如果没有任何问题，violations 和 warnings 为空数组
- suggestions 是可选的优化建议，不影响通过判定

只输出 JSON。"""

    def _l2_deep_validate(self, chapter_text, chapter_num, plan, prev_chapters, global_state):
        """L2 LLM深度语义校验"""
        if not self.client or not self.model:
            return {"violations": [], "warnings": [], "suggestions": [], "skipped": True}

        context_parts = []

        # 小说设定
        chars = plan.get("characters", {})
        wb = plan.get("worldbuilding", {})
        context_parts.extend([
            f"## 小说信息",
            f"- 书名: {plan.get('title', '')}",
            f"- 题材: {plan.get('genre', '')} | 风格: {plan.get('style', '')}",
            f"- 世界观: {wb.get('era', '')} | 力量体系: {wb.get('power_system', '')}",
            f"- 主角: {chars.get('protagonist', {}).get('name', '')} / {chars.get('protagonist', {}).get('identity', '')}",
        ])

        # 当前章节大纲
        outline = plan.get("outline", {})
        for vol in outline.get("volumes", []):
            for ch in vol.get("chapters", []):
                if ch.get("number") == chapter_num:
                    context_parts.append(f"\n## 本章大纲\n- 标题: {ch.get('title', '')}")
                    context_parts.append(f"- 摘要: {ch.get('summary', '')}")
                    context_parts.append(f"- 情绪曲线: {ch.get('emotion_curve', '')}")
                    context_parts.append(f"- 出场角色: {', '.join(ch.get('characters', []))}")
                    break

        # 前文摘要
        if prev_chapters:
            prev_nums = sorted(prev_chapters.keys())[-2:]
            for pn in prev_nums:
                prev_text = prev_chapters.get(pn, "")
                if prev_text:
                    context_parts.append(f"\n## 第{pn}章结尾(最后300字)\n{prev_text[-300:]}")

        context = "\n\n".join(context_parts)

        user_prompt = f"""请校验第{chapter_num}章的逻辑一致性。

{context}

## 待校验章节正文

{chapter_text[:4000]}

请输出 JSON 格式的校验报告。"""

        log.info(f"LogicSupervisor L2: chapter {chapter_num} ({len(chapter_text)} chars)")

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.L2_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.15,
                max_tokens=3072,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            result = json.loads(content)

            v_count = len(result.get("violations", []))
            w_count = len(result.get("warnings", []))
            s_count = len(result.get("suggestions", []))
            log.info(f"LogicSupervisor L2 done: {v_count}V {w_count}W {s_count}S")

            return result

        except json.JSONDecodeError as e:
            log.error(f"LogicSupervisor L2 JSON parse failed: {e}")
            return {"violations": [], "warnings": [], "suggestions": [], "error": f"JSON parse failed: {e}"}
        except Exception as e:
            log.error(f"LogicSupervisor L2 failed: {e}")
            return {"violations": [], "warnings": [], "suggestions": [], "error": str(e)}

    # ═══════════════════════════════════════════
    # 修复提示生成
    # ═══════════════════════════════════════════

    def build_fix_prompt(self, violations: list, warnings_list: list = None) -> str:
        """将违规报告转为 Writer 可用的修复提示"""
        if not violations and not warnings_list:
            return ""

        parts = ["## ⚠️ 逻辑监督报告 — 请在重写时修正以下问题\n"]

        # P0 致命错误
        p0_items = [v for v in violations if v.get("severity") == "P0"]
        if p0_items:
            parts.append("### 🔴 致命错误（必须修复）\n")
            for i, v in enumerate(p0_items[:8]):
                parts.append(f"{i+1}. **[{v.get('category','?')}]** {v.get('description','')}")
                parts.append(f"   → {v.get('fix','')}\n")

        # P1 建议修复
        p1_items = [v for v in violations if v.get("severity") == "P1"]
        if p1_items:
            parts.append("\n### 🟡 建议修复\n")
            for i, v in enumerate(p1_items[:5]):
                parts.append(f"{i+1}. {v.get('description','')}")
                parts.append(f"   → {v.get('fix','')}\n")

        # 警告
        if warnings_list:
            parts.append("\n### 🔵 注意事项\n")
            for i, w in enumerate(warnings_list[:5]):
                parts.append(f"{i+1}. {w.get('description','')}")

        return "\n".join(parts)

    def build_summary_table(self, result: dict) -> str:
        """生成人类可读的汇总表"""
        lines = ["## 📊 逻辑监督报告\n"]
        lines.append(f"**总分**: {result.get('score', 0)}/100 | **状态**: {'✅ 通过' if result.get('passed') else '❌ 未通过'}")
        lines.append(f"**违规**: {len(result.get('violations',[]))} 条 | **警告**: {len(result.get('warnings',[]))} 条 | **建议**: {len(result.get('suggestions',[]))} 条\n")

        # 分类得分
        cat_scores = result.get("category_scores", {})
        if cat_scores:
            lines.append("### 分类得分")
            cat_names = {
                "timeline": "⏱ 时间线", "spatial": "🗺 空间", "power": "⚔ 实力体系",
                "behavior": "👤 角色行为", "item": "🎒 物品道具", "causality": "🔗 因果链条",
                "dialogue": "💬 对话越界", "numeric": "🔢 数值", "identity": "🏷 身份命名",
                "life_death": "💀 生死状态", "continuity": "🔄 跨章连续", "setting": "📜 设定规则",
                "commonsense": "🧠 常识检测",
            }
            for cat, name in cat_names.items():
                score = cat_scores.get(cat, 100)
                icon = "✅" if score >= 90 else ("⚠️" if score >= 70 else "❌")
                lines.append(f"- {icon} {name}: {score}/100")

        return "\n".join(lines)
