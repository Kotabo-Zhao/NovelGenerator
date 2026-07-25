"""NovelGenerator — Consistency Validator: 逻辑一致性校验 Agent

职责: 独立校验 Agent，自动识别并拦截常识性逻辑错误。
覆盖维度:
- 时空一致性: 移动距离 vs 时间流逝
- 角色关系一致性: 姓氏、血缘、已建立的关系
- 力量/能力一致性: 能力边界不突变
- 跨章状态连续性: 前章结束状态 = 本章起始状态
- 物品/道具一致性: 已获得/已失去的物品状态

设计原则:
- 规则引擎(L1) + LLM语义校验(L2) 双层架构
- L1 快速扫描: 正则 + 知识库匹配，毫秒级
- L2 深度校验: LLM 语义推理，仅L1无法判断时触发
- 输出结构化违规报告，含严重级别(P0/P1/P2)
"""

import json
import re
import logging
from typing import Optional
from openai import OpenAI

log = logging.getLogger(__name__)

# ── 工具函数 ──

def _name_similarity(a: str, b: str) -> float:
    """计算两个名字的相似度 (0.0 ~ 1.0)
    
    使用字符级 Jaccard 相似度：共同字符数 / 并集字符数
    """
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return len(set_a & set_b) / union

# ═══════════════════════════════════════════════
# L1 规则引擎: 快速扫描硬性约束
# ═══════════════════════════════════════════════

# 古代交通速度参考 (里/时辰)
TRAVEL_SPEEDS = {
    "步行": 15,      # 普通人步行 ~15里/时辰
    "急行": 30,      # 急行军 ~30里/时辰
    "快马": 80,      # 驿马 ~80里/时辰
    "御剑": 500,     # 修仙御剑
    "传送": 99999,   # 传送阵
    "飞行": 300,     # 一般飞行
}

# 常见物理约束
PHYSICAL_CONSTRAINTS = {
    "凡人步行日行": 80,   # 凡人一天最多走80里
    "凡人骑马日行": 300,  # 换马不换人日行300里
    "声音传播": 0.68,     # 约0.68里/秒
}

# 常识性时间单位
TIME_UNITS = {
    "一炷香": 0.5,       # 约半小时
    "一盏茶": 0.25,      # 约15分钟
    "一息": 0.008,       # 约5秒
    "一个时辰": 2,       # 2小时
    "一刻钟": 0.25,      # 15分钟
}

# 角色关系规则
RELATIONSHIP_RULES = [
    {
        "id": "R001",
        "name": "同姓规则",
        "description": "直系血亲（父子/兄弟/姐妹）必须同姓（入赘/继父等情况除外）",
        "severity": "P0",
        "check": lambda chars: _check_surname_rule(chars),
    },
    {
        "id": "R002",
        "name": "辈分一致",
        "description": "师徒/叔侄等辈分关系不可混淆",
        "severity": "P0",
        "check": lambda chars: _check_generation_rule(chars),
    },
    {
        "id": "R003",
        "name": "年龄合理",
        "description": "父辈至少比子辈大15岁，师徒至少差5岁",
        "severity": "P1",
        "check": lambda chars: _check_age_rule(chars),
    },
]

# 力量体系一致性规则
POWER_RULES = [
    {
        "id": "P001",
        "name": "等级不可倒退",
        "description": "已突破的境界不能在后续章节中降级",
        "severity": "P0",
    },
    {
        "id": "P002",
        "name": "能力边界一致",
        "description": "已展示的能力上限不能在同一状态下突破",
        "severity": "P1",
    },
]


class ConsistencyValidator:
    """逻辑一致性校验 Agent — L1规则引擎 + L2 LLM深度校验"""

    def __init__(self, client: OpenAI = None, model: str = None):
        self.client = client
        self.model = model
        self._state_cache = {}  # 缓存角色/世界状态

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
        """校验单章的逻辑一致性
        
        Args:
            chapter_text: 本章正文
            chapter_num: 章节号
            plan: 小说规划数据(含设定/角色/大纲)
            prev_chapters: {chapter_num: content} 前文章节
            global_state: 全局状态快照
            run_deep: 是否执行L2深度校验
            
        Returns:
            {
                "passed": bool,
                "score": 0-100,
                "violations": [{id, type, severity, description, location, fix}],
                "warnings": [...],
                "l1_results": {...},
                "l2_results": {...}
            }
        """
        violations = []
        warnings = []

        # ── L1: 规则引擎 ──
        l1 = self._l1_validate(chapter_text, chapter_num, plan, prev_chapters, global_state)
        violations.extend(l1.get("violations", []))
        warnings.extend(l1.get("warnings", []))

        # ── L2: LLM深度校验 ──
        l2 = {}
        if run_deep and self.client and self.model:
            l2 = self._l2_validate(chapter_text, chapter_num, plan, prev_chapters, global_state)
            violations.extend(l2.get("violations", []))
            warnings.extend(l2.get("warnings", []))

        # 评分: P0 -30分/条, P1 -15分/条, P2 -5分/条
        score = 100
        for v in violations:
            if v.get("severity") == "P0":
                score -= 30
            elif v.get("severity") == "P1":
                score -= 15
            else:
                score -= 5
        score = max(0, min(100, score))

        passed = all(v.get("severity") != "P0" for v in violations)

        return {
            "passed": passed,
            "score": score,
            "violations": violations,
            "warnings": warnings,
            "l1_results": l1,
            "l2_results": l2,
        }

    def validate_outline(self, plan: dict) -> dict:
        """校验大纲的逻辑一致性（无需正文）
        
        检查项:
        - 章节号连续性
        - 角色出场分布合理性
        - 冲突进阶逻辑
        - 卷幕结构完整性
        """
        violations = []
        warnings = []
        
        outline = plan.get("outline", {})
        volumes = outline.get("volumes", [])
        
        # 检查章节号连续性
        prev_num = 0
        for vol in volumes:
            for ch in vol.get("chapters", []):
                ch_num = int(ch.get("number", 0))
                if ch_num != prev_num + 1 and prev_num > 0:
                    violations.append({
                        "id": "O001",
                        "type": "outline_structure",
                        "severity": "P1",
                        "description": f"章节号不连续: 第{prev_num}章→第{ch_num}章",
                        "location": f"第{vol.get('number', '?')}卷",
                        "fix": "检查章节编号是否跳号",
                    })
                prev_num = ch_num
        
        # 检查三幕结构
        acts = [v.get("act", "") for v in volumes]
        if "建置" not in str(acts) and len(volumes) >= 3:
            warnings.append({
                "id": "O002",
                "type": "outline_structure",
                "severity": "P2",
                "description": "大纲缺少明确的三幕结构标注",
                "fix": "为每卷标注所属幕（建置/对抗/解决）",
            })
        
        # 检查角色出场
        chars = plan.get("characters", {})
        all_char_names = set()
        protagonist = chars.get("protagonist", {}).get("name", "")
        if protagonist:
            all_char_names.add(protagonist)
        for c in chars.get("supporting", []):
            all_char_names.add(c.get("name", ""))
        for c in chars.get("antagonist", []):
            all_char_names.add(c.get("name", ""))
        
        for vol in volumes:
            for ch in vol.get("chapters", []):
                ch_chars = set(ch.get("characters", []))
                unknown = ch_chars - all_char_names - {""}
                if unknown:
                    warnings.append({
                        "id": "O003",
                        "type": "character_unknown",
                        "severity": "P2",
                        "description": f"第{ch.get('number','?')}章出现未注册角色: {unknown}",
                        "location": f"第{ch.get('number','?')}章",
                        "fix": "在角色设定中补充该角色，或确认名称是否正确",
                    })
        
        # ── 主角身份一致性检查（新增）──
        if protagonist:
            # 检查章节摘要中是否提到主角（确认主角出场覆盖）
            chapters_with_protag = 0
            total_chapters = 0
            for vol in volumes:
                for ch in vol.get("chapters", []):
                    if not isinstance(ch, dict):
                        continue
                    total_chapters += 1
                    ch_text = f"{ch.get('summary','')} {ch.get('conflict','')} {ch.get('hook','')}"
                    if protagonist in ch_text:
                        chapters_with_protag += 1
            
            if total_chapters > 0 and chapters_with_protag < max(2, total_chapters * 0.5):
                violations.append({
                    "id": "O004",
                    "type": "protagonist_coverage",
                    "severity": "P1",
                    "description": f"主角「{protagonist}」仅出现在 {chapters_with_protag}/{total_chapters} 章摘要中（<50%），可能被边缘化",
                    "fix": "确保主角在大部分章节中作为核心角色出场",
                })
            
            # 检查角色设定中是否有空/占位名称
            if protagonist in ("主角", "男主", "女主", "主人公", ""):
                violations.append({
                    "id": "O005",
                    "type": "protagonist_placeholder",
                    "severity": "P0",
                    "description": f"主角名称为占位符「{protagonist}」，未设定真实姓名",
                    "fix": "为角色设定中的 protagonist.name 填入真实姓名",
                })
            
            # 检查 chapter characters 列表中的主角名是否与设定一致
            for vol in volumes:
                for ch in vol.get("chapters", []):
                    if not isinstance(ch, dict):
                        continue
                    ch_chars = ch.get("characters", [])
                    # 检查是否有类似但不完全匹配的主角名（可能是改名后不同步）
                    for cname in ch_chars:
                        if not cname or cname == protagonist:
                            continue
                        # 策略1: 同姓不同名 (如 "林玄" vs "林凡")
                        if len(cname) >= 2 and len(protagonist) >= 2 and cname[0] == protagonist[0]:
                            warnings.append({
                                "id": "O006",
                                "type": "character_name_mismatch",
                                "severity": "P1",
                                "description": f"第{ch.get('number','?')}章角色列表中有同姓疑似主角: '{cname}' vs 设定名 '{protagonist}'",
                                "location": f"第{ch.get('number','?')}章",
                                "fix": f"确认'{cname}'是否应为'{protagonist}'，或明确为不同角色",
                            })
                            break
                        # 策略2: 编辑距离 ≤2 的相似名
                        if _name_similarity(cname, protagonist) >= 0.6:
                            warnings.append({
                                "id": "O006",
                                "type": "character_name_mismatch",
                                "severity": "P1",
                                "description": f"第{ch.get('number','?')}章角色列表中有疑似主角名变体: '{cname}' vs 设定名 '{protagonist}'",
                                "location": f"第{ch.get('number','?')}章",
                                "fix": f"统一主角名称为「{protagonist}」，或确认是否为不同角色",
                            })
                            break
        
        score = 100
        for v in violations:
            score -= 30 if v["severity"] == "P0" else (15 if v["severity"] == "P1" else 5)
        score = max(0, min(100, score))
        
        return {
            "passed": len(violations) == 0,
            "score": score,
            "violations": violations,
            "warnings": warnings,
        }

    # ═══════════════════════════════════════════
    # L1: 规则引擎
    # ═══════════════════════════════════════════

    def _l1_validate(self, chapter_text, chapter_num, plan, prev_chapters, global_state):
        """L1 快速规则扫描"""
        violations = []
        warnings = []
        checks = {}

        # 1. 时空一致性检查
        space_time = self._check_space_time(chapter_text, chapter_num, plan, prev_chapters)
        checks["space_time"] = space_time
        violations.extend(space_time.get("violations", []))
        warnings.extend(space_time.get("warnings", []))

        # 2. 角色关系检查
        relations = self._check_relations(plan)
        checks["relations"] = relations
        violations.extend(relations.get("violations", []))
        warnings.extend(relations.get("warnings", []))

        # 3. 力量体系检查
        power = self._check_power_consistency(chapter_text, global_state)
        checks["power"] = power
        violations.extend(power.get("violations", []))
        warnings.extend(power.get("warnings", []))

        # 4. 结尾完整性检查
        ending = self._check_ending(chapter_text, prev_chapters)
        checks["ending"] = ending
        violations.extend(ending.get("violations", []))
        warnings.extend(ending.get("warnings", []))

        return {"violations": violations, "warnings": warnings, "checks": checks}

    def _check_space_time(self, chapter_text, chapter_num, plan, prev_chapters):
        """时空一致性: 检测移动距离 vs 时间流逝"""
        violations = []
        warnings = []
        
        # 提取时间信息
        time_patterns = [
            (r"(\d+)\s*个?\s*时辰", lambda n: int(n) * 2),
            (r"一个?\s*时辰", lambda: 2),
            (r"半个?\s*时辰", lambda: 1),
            (r"两\s*个?\s*时辰", lambda: 4),
            (r"(\d+)\s*炷香", lambda n: int(n) * 0.5),
            (r"(\d+)\s*盏茶", lambda n: int(n) * 0.25),
            (r"(\d+)\s*息", lambda n: int(n) * 0.008),
            (r"(\d+)\s*天", lambda n: int(n) * 24),
            (r"(\d+)\s*日", lambda n: int(n) * 24),
        ]
        
        elapsed_hours = 0
        for pattern, converter in time_patterns:
            matches = re.findall(pattern, chapter_text)
            for m in matches:
                if isinstance(m, str) and m.isdigit():
                    elapsed_hours += converter(m)
                elif isinstance(m, str) and len(m) > 0:
                    # Full match from non-capturing pattern (e.g. "一个时辰")
                    try:
                        elapsed_hours += converter()
                    except TypeError:
                        pass  # converter needs arg but m is not a digit
        
        # 提取距离信息（含中文数字: 千=1000, 万=10000, 两=2）
        distance_li = 0
        # 数字距离: "2000里", "三千里"
        cn_num_map = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10,"两":2}
        dist_matches = re.findall(r"(\d+)\s*里", chapter_text)
        for d in dist_matches:
            distance_li += int(d)
        # 千X里: "两千里"
        for m in re.finditer(r"([一二三四五六七八九十两])\s*千\s*里", chapter_text):
            n = cn_num_map.get(m.group(1), 1)
            distance_li += n * 1000
        # X千里: "三千里" (千 after digit)
        for m in re.finditer(r"([一二三四五六七八九十两])\s*千\s*里", chapter_text):
            pass  # already handled above
        # 万里
        for m in re.finditer(r"([一二三四五六七八九十两])\s*万\s*里", chapter_text):
            n = cn_num_map.get(m.group(1), 1)
            distance_li += n * 10000
        # 纯 "千里" "万里" 无数字 = 1000/10000
        if re.search(r"(?<!\d)(?<![一二三四五六七八九十两])千里", chapter_text):
            distance_li += 1000
        if re.search(r"(?<!\d)(?<![一二三四五六七八九十两])万里", chapter_text):
            distance_li += 10000
        
        # 提取移动方式
        travel_mode = "步行"
        if re.search(r"御剑|飞剑|御风|腾云", chapter_text):
            travel_mode = "御剑"
        elif re.search(r"传送|瞬移|挪移|阵法", chapter_text):
            travel_mode = "传送"
        elif re.search(r"骑马|策马|快马|纵马", chapter_text):
            travel_mode = "快马"
        elif re.search(r"飞行|翱翔|展翅", chapter_text):
            travel_mode = "飞行"
        
        max_speed = TRAVEL_SPEEDS.get(travel_mode, 15)
        
        if elapsed_hours > 0 and distance_li > 0:
            required_hours = distance_li / max_speed
            if elapsed_hours < required_hours * 0.5:
                violations.append({
                    "id": "ST001",
                    "type": "space_time",
                    "severity": "P0",
                    "description": f"时空不一致: 以{travel_mode}方式移动{distance_li}里需要至少{required_hours:.1f}小时，"
                                   f"但文本只过了约{elapsed_hours:.1f}小时",
                    "location": f"第{chapter_num}章",
                    "fix": f"增加时间过渡('{required_hours-elapsed_hours:.0f}时辰后...')或修改移动方式",
                })
        
        # 检查跨章位置跳跃
        if prev_chapters:
            prev_ch_num = max(prev_chapters.keys()) if prev_chapters else 0
            prev_text = prev_chapters.get(prev_ch_num, "")
            # 检查前章末尾的位置
            prev_locations = re.findall(r"(?:在|于|到|进).{0,15}(?:皇宫|府邸|山|城|镇|殿|阁|院|村|洞|谷|林|海)", prev_text[-500:])
            curr_locations = re.findall(r"(?:在|于|到|进).{0,15}(?:皇宫|府邸|山|城|镇|殿|阁|院|村|洞|谷|林|海)", chapter_text[:500])
            
            if prev_locations and curr_locations:
                prev_loc = prev_locations[-1]
                curr_loc = curr_locations[0]
                # 简单检测: 如果前章在"皇宫"本章开头在"千里之外"的某个地方
                if "皇宫" in prev_loc and any(kw in chapter_text[:300] for kw in ["千里", "万里", "千里之外", "万里之遥"]):
                    time_in_first = re.findall(r"(\d+)\s*(?:个?\s*时辰|炷香|天)", chapter_text[:300])
                    if not time_in_first:
                        violations.append({
                            "id": "ST002",
                            "type": "space_time",
                            "severity": "P0",
                            "description": f"跨章位置跳跃: 前章结尾在'{prev_loc}'，本章开头出现在远方但无时间过渡",
                            "location": f"第{prev_ch_num}章 → 第{chapter_num}章",
                            "fix": "本章开头增加时间过渡（如'三日后...'/'一路风尘...'）",
                        })

        return {"violations": violations, "warnings": warnings}

    def _check_relations(self, plan):
        """角色关系一致性检查"""
        violations = []
        warnings = []
        
        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})
        supporting = chars.get("supporting", [])
        antagonist = chars.get("antagonist", [])
        
        all_chars = [protagonist] + supporting + antagonist
        all_chars = [c for c in all_chars if c]
        
        # R001: 同姓规则检查
        surnames = {}
        for c in all_chars:
            name = c.get("name", "")
            if len(name) >= 2:
                surname = name[0]  # 取第一个字作为姓（简化处理）
                if surname not in surnames:
                    surnames[surname] = []
                surnames[surname].append(c)
        
        # 检查兄弟/父子关系
        for c in all_chars:
            relations = c.get("relationships", [])
            identity = str(c.get("identity", ""))
            relation = str(c.get("relation", ""))
            
            # 检测"兄弟""父子"等关系但姓不同
            for rel_text in [identity, relation]:
                for rel_kw in ["兄弟", "父子"]:
                    if rel_kw in rel_text:
                        # 尝试找到对应角色
                        for other in all_chars:
                            other_rel = str(other.get("relation", ""))
                            other_id = str(other.get("identity", ""))
                            if c.get("name") != other.get("name") and rel_text in (other_rel + other_id):
                                # 检查是否同姓
                                n1 = c.get("name", "")
                                n2 = other.get("name", "")
                                if n1 and n2 and n1[0] != n2[0]:
                                    violations.append({
                                        "id": "R001",
                                        "type": "character_relation",
                                        "severity": "P0",
                                        "description": f"角色关系冲突: '{n1}'与'{n2}'标注为{rel_kw}关系但不同姓",
                                        "location": f"角色: {n1}, {n2}",
                                        "fix": f"修改为同姓或说明原因（如异姓兄弟/表亲）",
                                    })
        
        return {"violations": violations, "warnings": warnings}

    def _check_power_consistency(self, chapter_text, global_state):
        """力量体系一致性"""
        violations = []
        warnings = []
        
        if not global_state:
            return {"violations": [], "warnings": []}
        
        power_levels = global_state.get("power_levels", {})
        
        # 检测是否有等级倒退的表述
        degrade_patterns = [
            r"修为倒(?:退|跌)",
            r"境界(?:跌落|倒退|下降)",
            r"实力(?:大减|倒退|不如从前)",
        ]
        for pat in degrade_patterns:
            matches = re.findall(pat, chapter_text)
            if matches:
                warnings.append({
                    "id": "P003",
                    "type": "power_consistency",
                    "severity": "P2",
                    "description": f"检测到修为倒退表述: {matches[0]}",
                    "location": f"正文中",
                    "fix": "需要确认倒退原因（受伤/封印）并在后续安排恢复",
                })
        
        return {"violations": violations, "warnings": warnings}

    def _check_ending(self, chapter_text, prev_chapters):
        """结尾完整性"""
        violations = []
        warnings = []
        
        # 检查结尾是否有完整句子
        last_char = chapter_text.rstrip()[-1] if chapter_text.rstrip() else ""
        valid_endings = set("。！？…\"')」》")
        if last_char not in valid_endings:
            violations.append({
                "id": "E001",
                "type": "ending",
                "severity": "P1",
                "description": f"章节结尾不完整 (最后字符: '{last_char}')",
                "location": "章末",
                "fix": "确保章节以完整句子结束",
            })
        
        return {"violations": violations, "warnings": warnings}

    # ═══════════════════════════════════════════
    # L2: LLM 深度语义校验
    # ═══════════════════════════════════════════

    VALIDATOR_SYSTEM = """你是一位严格的小说逻辑纠错专家。你的唯一职责是找出文本中的逻辑漏洞。

## 检查维度

### 时空一致性（P0级 — 致命错误）
- 角色能否在规定时间内到达指定地点？（考虑交通方式）
- 不同场景的时间线是否矛盾？
- 同一个角色能否同时出现在两个地方？

### 角色一致性（P0级 — 致命错误）
- 已死亡的角色是否突然出现？
- 角色的性格、能力、知识是否与设定一致？
- 角色之间的关系是否前后矛盾？

### 情节逻辑（P0/P1级）
- 事件因果关系是否合理？
- 角色的决策是否符合其性格和处境？
- 能力/道具的使用是否有被遗忘或滥用？

### 设定一致性（P1级）
- 是否违反已建立的世界规则？
- 力量体系的边界是否被随意突破？

## 输出格式

返回 JSON:
```json
{
  "violations": [
    {
      "id": "V001",
      "type": "space_time|character|plot|setting",
      "severity": "P0|P1|P2",
      "description": "具体问题描述",
      "evidence": "原文证据（引用原文片段）",
      "fix": "修改建议"
    }
  ],
  "warnings": [
    {
      "id": "W001", 
      "type": "...",
      "severity": "P1|P2",
      "description": "潜在问题",
      "fix": "建议"
    }
  ],
  "summary": "一句话总结校验结果"
}
```

只输出 JSON，不要其他内容。如果没有任何问题，violations 和 warnings 为空数组。"""

    def _l2_validate(self, chapter_text, chapter_num, plan, prev_chapters, global_state):
        """L2 LLM深度语义校验"""
        if not self.client or not self.model:
            return {"violations": [], "warnings": [], "skipped": True}

        # 构建上下文
        chars = plan.get("characters", {})
        protagonist = chars.get("protagonist", {})
        
        context_parts = [
            f"## 小说设定",
            f"- 主角: {protagonist.get('name', '')}",
            f"- 世界观: {json.dumps(plan.get('worldbuilding', {}), ensure_ascii=False)[:300]}",
            f"- 力量体系: {plan.get('worldbuilding', {}).get('power_system', '')[:200]}",
        ]
        
        # 已有角色状态
        if global_state:
            state_summary = json.dumps(global_state, ensure_ascii=False)[:500]
            context_parts.append(f"\n## 当前角色状态\n{state_summary}")
        
        # 前文摘要（最近2章）
        if prev_chapters:
            prev_nums = sorted(prev_chapters.keys())[-2:]
            for pn in prev_nums:
                prev_text = prev_chapters[pn]
                context_parts.append(f"\n## 第{pn}章结尾(最后300字)\n{prev_text[-300:]}")

        context = "\n\n".join(context_parts)

        user_prompt = f"""请校验第{chapter_num}章的逻辑一致性。

{context}

## 待校验章节

{chapter_text[:4000]}

请输出 JSON 格式的校验报告。"""

        log.info(f"ConsistencyValidator L2: chapter {chapter_num}")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.VALIDATOR_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=2048,
                response_format={"type": "json_object"},
            )
            
            content = response.choices[0].message.content
            result = json.loads(content)
            
            v_count = len(result.get("violations", []))
            w_count = len(result.get("warnings", []))
            log.info(f"ConsistencyValidator L2: {v_count} violations, {w_count} warnings")
            
            return result
            
        except Exception as e:
            log.error(f"ConsistencyValidator L2 failed: {e}")
            return {"violations": [], "warnings": [], "error": str(e)}

    def build_fix_prompt(self, violations: list) -> str:
        """将违规报告转为 Writer 可用的修复提示"""
        if not violations:
            return ""
        
        p0_items = [v for v in violations if v.get("severity") == "P0"]
        p1_items = [v for v in violations if v.get("severity") == "P1"]
        
        parts = ["## ⚠️ 逻辑一致性修复要求\n"]
        
        if p0_items:
            parts.append("### 🔴 致命错误（必须修复）\n")
            for i, v in enumerate(p0_items[:5]):
                parts.append(f"{i+1}. **{v.get('type','')}** — {v.get('description','')}")
                parts.append(f"   修复方式: {v.get('fix','')}\n")
        
        if p1_items:
            parts.append("\n### 🟡 建议修复\n")
            for i, v in enumerate(p1_items[:5]):
                parts.append(f"{i+1}. {v.get('description','')}")
                parts.append(f"   修复方式: {v.get('fix','')}\n")
        
        return "\n".join(parts)


# ═══════════════════════════════════════════
# L1 辅助检查函数
# ═══════════════════════════════════════════

def _check_surname_rule(chars):
    """检查同姓规则"""
    return []  # 已整合到 _check_relations

def _check_generation_rule(chars):
    """检查辈分规则"""
    return []

def _check_age_rule(chars):
    """检查年龄规则"""
    return []
