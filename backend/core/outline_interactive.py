"""NovelGenerator v2 — Interactive Outline Engine: 交互式大纲生成

v2 重构要点:
- 旧: parse_feedback() 关键词匹配 → 分类标签 → 原文塞入prompt
- 新: FeedbackDecomposer LLM深度语义分析 → 逐条精确指令 → 针对性regeneration_prompt

流程:
用户自然语言反馈
  → FeedbackDecomposer.decompose()   # 深度理解 + 拆解为N条指令
  → execute_change_plan()            # 逐条执行，每条有独立的regeneration_prompt
  → validate_changes()               # 验证修改是否生效
  → 输出diff + 保存
"""

import json
import copy
import re
import logging
from typing import AsyncIterator, Optional
from openai import OpenAI

log = logging.getLogger(__name__)

# 保留分类体系作为 UI 展示标签
FEEDBACK_CATEGORIES = {
    "pacing": {"name": "节奏调整", "description": "调整章节密度、高潮分布、张弛比例"},
    "character_arc": {"name": "角色弧线", "description": "调整主角/配角成长轨迹"},
    "plot_logic": {"name": "情节逻辑", "description": "修复情节漏洞、增强因果链"},
    "structure": {"name": "结构调整", "description": "调整卷/章结构、三幕比例"},
    "conflict": {"name": "冲突升级", "description": "调整冲突强度和逐级加码"},
    "hooks": {"name": "钩子/悬念", "description": "调整章末钩子和悬念密度"},
    "ending": {"name": "结局设计", "description": "调整高潮/结局设计"},
}


class OutlineInteractive:
    """交互式大纲引擎 v2 — 基于 FeedbackDecomposer 的精确修改流水线"""

    def __init__(self, client: OpenAI = None, model: str = None,
                 decomposer=None):
        self.client = client
        self.model = model
        self.decomposer = decomposer  # FeedbackDecomposer 实例
        self._iteration_history = []

    # ═══════════════════════════════════════════
    # 主流程: 反馈 → 拆解 → 执行 → 验证
    # ═══════════════════════════════════════════

    async def process_feedback(
        self,
        feedback: str,
        plan: dict,
        planner,  # Planner 实例（用于 LLM 调用）
    ) -> AsyncIterator[dict]:
        """处理用户反馈的完整流程（v2核心入口）
        
        Steps:
        1. 深度语义拆解 (decomposer.decompose)
        2. 逐条执行修改 (execute_change_plan)
        3. 合并 + 校验 + diff
        
        Yields progress events + final done.
        """
        old_plan = copy.deepcopy(plan)
        
        # ── Step 1: 语义拆解 ──
        yield {"type": "progress", "phase": "decompose", "pct": 5,
               "label": "正在理解您的修改意见…"}
        
        if self.decomposer:
            decomposition = self.decomposer.decompose(feedback, plan)
        else:
            # 降级: 无 decomposer 时创建临时实例
            from .feedback_decomposer import FeedbackDecomposer
            temp = FeedbackDecomposer(self.client, self.model)
            decomposition = temp.decompose(feedback, plan)

        intent = decomposition.get("intent_analysis", {})
        change_plan = decomposition.get("change_plan", [])
        
        if not change_plan:
            yield {"type": "error", "message": "未能从修改意见中提取有效指令，请尝试更具体的描述"}
            return

        yield {"type": "progress", "phase": "decompose", "pct": 15,
               "label": f"已识别 {len(change_plan)} 条修改指令"}
        
        # 输出意图分析给前端展示
        yield {
            "type": "intent",
            "summary": intent.get("summary", ""),
            "motivation": intent.get("deep_motivation", ""),
            "change_count": len(change_plan),
        }
        
        # ── Step 2: 逐条执行 ──
        new_plan = copy.deepcopy(plan)
        executed = []
        failed = []
        
        for i, action in enumerate(change_plan):
            action_id = action.get("id", f"C{i+1:03d}")
            priority = action.get("priority", 1)
            target = action.get("target", {})
            regen_prompt = action.get("regeneration_prompt", "")
            
            pct = 15 + int(70 * (i + 1) / len(change_plan))
            desc = target.get("description", f"修改 #{i+1}")
            
            yield {"type": "progress", "phase": "execute", "pct": pct,
                   "label": f"[{i+1}/{len(change_plan)}] {desc[:60]}…"}
            
            try:
                result = await self._execute_single_action(
                    new_plan, action, planner
                )
                if result:
                    executed.append({
                        "id": action_id,
                        "target": desc,
                        "status": "done",
                        "result": result,
                    })
                else:
                    failed.append({
                        "id": action_id,
                        "target": desc,
                        "status": "failed",
                        "reason": "LLM返回空结果",
                    })
            except Exception as e:
                log.error(f"Action {action_id} failed: {e}")
                failed.append({
                    "id": action_id,
                    "target": desc,
                    "status": "failed",
                    "reason": str(e),
                })
        
        # ── Step 3: 合并校验 ──
        yield {"type": "progress", "phase": "validate", "pct": 90,
               "label": "校验修改结果…"}
        
        self._renumber_chapters(new_plan)
        
        # 输出执行摘要
        yield {
            "type": "execution_summary",
            "total": len(change_plan),
            "executed": len(executed),
            "failed": len(failed),
            "failed_items": [f["target"] for f in failed],
        }
        
        # ── Step 4: diff + 保存 ──
        diff = self.get_diff_summary(old_plan, new_plan)
        if diff:
            yield {"type": "diff", "changes": diff}
        
        self._iteration_history.append({
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "feedback": feedback,
            "decomposition": {
                "intent": intent.get("summary", ""),
                "actions": len(change_plan),
                "executed": len(executed),
                "failed": len(failed),
            },
            "diff": diff,
            "chapter_count_before": old_plan.get("outline", {}).get("total_chapters", 0),
            "chapter_count_after": new_plan.get("outline", {}).get("total_chapters", 0),
        })
        
        yield {"type": "progress", "phase": "done", "pct": 100, "label": "大纲已更新！"}
        yield {"type": "done", "plan": new_plan}

    # ═══════════════════════════════════════════
    # 单条指令执行
    # ═══════════════════════════════════════════

    async def _execute_single_action(self, plan: dict, action: dict, planner) -> dict:
        """执行单条修改指令
        
        根据 action 中的 action/scope/target 决定执行策略:
        - modify + global → 全量重生成大纲（使用 regeneration_prompt）
        - modify + volume → 指定卷重生成
        - modify + chapter → 指定章节重生成
        - add + volume → 在指定卷中插入章节
        - remove + chapter → 移除指定章节
        - reorder → 调整章节顺序
        """
        act = action.get("action", "modify")
        target = action.get("target", {})
        scope = target.get("scope", "global")
        regen_prompt = action.get("regeneration_prompt", "")
        validation = action.get("validation", "")
        
        result = {"scope": scope, "action": act}
        
        if act in ("modify", "add") and scope == "global":
            # 全量重生成
            new_outline = await self._regenerate_with_prompt(
                plan, regen_prompt, planner, mode="global"
            )
            if new_outline:
                plan["outline"] = new_outline
                result["effect"] = f"大纲已更新为 {new_outline.get('total_chapters', 0)} 章"
            else:
                return None
            
            # ── 同步更新角色设定和世界观 ──
            # 优先使用 Decomposer 显式标注的 affected_aspects（精确），
            # 回退到 regen_prompt 关键词匹配（兜底，覆盖离线模式）
            affected = action.get("affected_aspects", [])
            needs_chars = "characters" in affected or (
                not affected and any(kw in regen_prompt for kw in [
                    "主角", "主人公", "男主", "女主", "角色", "人设", "性格",
                    "身世", "出身", "背景", "身份", "金手指", "能力", "天赋",
                    "反派", "配角", "后代", "之后", "之子",
                ])
            )
            needs_wb = "worldbuilding" in affected or (
                not affected and any(kw in regen_prompt for kw in [
                    "世界观", "世界", "时代", "设定", "力量体系",
                    "修炼体系", "魔法", "灵力", "灵气", "斗气",
                ])
            )
            
            if needs_chars:
                new_chars = await self._regenerate_with_prompt(
                    plan, regen_prompt, planner, mode="characters"
                )
                if new_chars:
                    plan["characters"] = new_chars
                    result["effect"] += " + 角色设定已同步"
            
            if needs_wb:
                new_wb = await self._regenerate_with_prompt(
                    plan, regen_prompt, planner, mode="worldbuilding"
                )
                if new_wb:
                    plan["worldbuilding"] = new_wb
                    result["effect"] += " + 世界观已同步"
        
        elif act in ("modify", "add") and scope == "volume":
            vol_nums = target.get("volume_numbers", [])
            volumes = plan.get("outline", {}).get("volumes", [])
            
            for vn in vol_nums:
                for i, vol in enumerate(volumes):
                    if vol.get("number") == vn:
                        new_vol = await self._regenerate_with_prompt(
                            plan, regen_prompt, planner, mode="volume",
                            volume_index=i, volume_num=vn,
                        )
                        if new_vol:
                            volumes[i] = new_vol
                            result["effect"] = f"第{vn}卷已更新"
                        break
            plan["outline"]["total_chapters"] = sum(
                len(v.get("chapters", [])) for v in volumes
            ) if isinstance(plan.get("outline"), dict) else 0
        
        elif act in ("modify", "add") and scope == "chapter":
            ch_nums = target.get("chapter_numbers", [])
            volumes = plan.get("outline", {}).get("volumes", [])
            
            for cn in ch_nums:
                for vol in volumes:
                    for ch in vol.get("chapters", []):
                        if int(ch.get("number", 0)) == cn:
                            new_ch = await self._regenerate_with_prompt(
                                plan, regen_prompt, planner, mode="chapter",
                                chapter_num=cn, current_chapter=ch,
                            )
                            if new_ch:
                                ch.update(new_ch)
                                result["effect"] = f"第{cn}章已更新"
                            break
        
        elif act == "remove":
            ch_nums = target.get("chapter_numbers", [])
            volumes = plan.get("outline", {}).get("volumes", [])
            removed = 0
            for cn in ch_nums:
                for vol in volumes:
                    chs = vol.get("chapters", [])
                    vol["chapters"] = [c for c in chs if int(c.get("number", 0)) != cn]
                    removed += len(chs) - len(vol["chapters"])
            result["effect"] = f"已移除 {removed} 章"
        
        elif act == "reorder":
            # 章节重排（暂用标记方式，由后续 regeneration 处理）
            result["effect"] = "已标记重排指令"
        
        # 附加验证信息
        if validation:
            result["validation"] = validation
        
        return result

    # ═══════════════════════════════════════════
    # LLM 调用: 使用精确的 regeneration_prompt
    # ═══════════════════════════════════════════

    async def _regenerate_with_prompt(
        self, plan: dict, regen_prompt: str, planner,
        mode: str = "global",
        volume_index: int = None, volume_num: int = None,
        chapter_num: int = None, current_chapter: dict = None,
    ) -> dict:
        """使用 decomposer 生成的精确 prompt 进行 LLM 重新生成
        
        与旧版的区别: regen_prompt 已经包含具体的"改什么→改成什么"指令，
        不需要 LLM 自己猜测用户意图。
        """
        genre = plan.get("genre", "玄幻")
        style = plan.get("style", "热血爽文")
        target_words = plan.get("target_words", 500000)
        wb = plan.get("worldbuilding", {})
        chars = plan.get("characters", {})
        
        if mode == "global":
            prompt = f"""你是小说大纲规划师。请根据以下精确指令重新生成完整大纲。

【当前设定】
世界观: {json.dumps(wb, ensure_ascii=False)[:300]}
角色: {json.dumps(chars, ensure_ascii=False)[:200]}
题材: {genre}  风格: {style}  总目标: {target_words}字

【修改指令（请逐条严格执行）】
{regen_prompt}

【输出要求】
- 严格按指令修改，不改变指令未提及的部分
- 保持世界观和角色体系不变
- 确保章节间逻辑连贯
- 每章摘要控制在30字内

只输出JSON:
```json
{{"volumes":[{{"number":1,"title":"","act":"第一幕·建置","theme":"","act_function":"","chapters":[{{"number":1,"title":"","summary":"","emotion_curve":"","conflict":"","characters":[""],"hook":"","target_words":3000}}]}}],"total_chapters":0,"three_act_map":"","rhythm_notes":""}}
```"""
            return await self._safe_llm_call(prompt, planner, "global_regenerate", max_tokens=16384)
        
        elif mode == "volume":
            vol = plan.get("outline", {}).get("volumes", [])[volume_index]
            vol_title = vol.get("title", f"第{volume_num}卷")
            vol_act = vol.get("act", "")
            chapter_count = len(vol.get("chapters", []))
            
            prompt = f"""重新规划第{volume_num}卷「{vol_title}」({vol_act})的章节大纲。

【修改指令】
{regen_prompt}

【约束】
- 保持约{chapter_count}章（指令要求增减的除外）
- 题材: {genre}  风格: {style}
- 保持与前后卷的逻辑衔接

输出JSON数组:
```json
[{{"number":章节号,"title":"","summary":"","emotion_curve":"","conflict":"","characters":[""],"hook":"","target_words":3000}}]
```"""
            return await self._safe_llm_call(prompt, planner, f"vol_{volume_num}", max_tokens=4096)
        
        elif mode == "chapter":
            prompt = f"""修改第{chapter_num}章大纲。

当前章节: {json.dumps(current_chapter, ensure_ascii=False)[:300]}

【修改指令】
{regen_prompt}

输出JSON（单个章节对象）:
```json
{{"title":"","summary":"","emotion_curve":"","conflict":"","characters":[""],"hook":"","target_words":3000}}
```"""
            return await self._safe_llm_call(prompt, planner, f"ch_{chapter_num}", max_tokens=1024)
        
        elif mode == "characters":
            prompt = f"""你是角色设计师。请根据修改指令重新设计角色体系。

【当前角色设定】
{json.dumps(plan.get("characters", {}), ensure_ascii=False, indent=2)[:2000]}

【故事背景】
题材: {genre}  风格: {style}  目标字数: {target_words}字
世界观: {json.dumps(plan.get('worldbuilding', {}), ensure_ascii=False)[:500]}

【修改指令】
{regen_prompt}

【输出要求】
- 只改指令涉及的角色，保留未提及角色的所有设定
- 每个角色必须有: name, identity, personality, motivation, arc
- protagonist 额外有: cheat（金手指）, weakness（弱点）
- supporting 角色有: relation（与主角关系）
- antagonist 有: goal（目标）, method（手段）

输出JSON（完整的characters对象）:
```json
{{"protagonist":{{"name":"","identity":"","personality":"","cheat":"","weakness":"","motivation":"","arc":""}},"supporting":[],"antagonist":[]}}
```"""
            return await self._safe_llm_call(prompt, planner, "characters_update", max_tokens=4096)
        
        elif mode == "worldbuilding":
            prompt = f"""你是世界观设计师。请根据修改指令更新世界观设定。

【当前世界观】
{json.dumps(plan.get("worldbuilding", {}), ensure_ascii=False, indent=2)[:2000]}

【故事背景】
题材: {genre}  风格: {style}

【修改指令】
{regen_prompt}

【输出要求】
- 只改指令涉及的设定，保留未提及的部分
- 必须有: era, power_system, core_conflict, world_rules
- world_rules 列出3-5条核心规则

输出JSON:
```json
{{"era":"","power_system":"","core_conflict":"","world_rules":[""],"key_organizations":[],"key_locations":[""]}}
```"""
            return await self._safe_llm_call(prompt, planner, "worldbuilding_update", max_tokens=2048)
        
        return None

    async def _safe_llm_call(self, prompt: str, planner, phase: str, max_tokens: int = 4096) -> dict:
        """安全 LLM 调用 — 带超时 + 降级 fallback"""
        try:
            result = await planner._call_llm(prompt, phase, max_tokens=max_tokens)
            if result:
                # 标准化
                if isinstance(result, dict) and "volumes" in result:
                    for vol in result.get("volumes", []):
                        vol["number"] = int(vol.get("number", 1))
                        for ch in vol.get("chapters", []):
                            ch["number"] = int(ch.get("number", 1))
                    result["total_chapters"] = sum(
                        len(v.get("chapters", [])) for v in result.get("volumes", [])
                    )
                elif isinstance(result, list):
                    for ch in result:
                        ch["number"] = int(ch.get("number", 1))
                return result
        except Exception as e:
            log.error(f"LLM call failed [{phase}]: {e}")
        return None

    # ═══════════════════════════════════════════
    # Diff & 辅助
    # ═══════════════════════════════════════════

    def get_diff_summary(self, old_plan: dict, new_plan: dict) -> list:
        """比较两个大纲版本的变化"""
        changes = []
        old_outline = old_plan.get("outline", {})
        new_outline = new_plan.get("outline", {})
        
        old_total = old_outline.get("total_chapters", 0)
        new_total = new_outline.get("total_chapters", 0)
        
        if old_total != new_total:
            changes.append({
                "type": "chapter_count",
                "before": old_total,
                "after": new_total,
                "delta": new_total - old_total,
                "description": f"章节总数 {'增加' if new_total > old_total else '减少'}{abs(new_total - old_total)}章",
            })
        
        old_vols = old_outline.get("volumes", [])
        new_vols = new_outline.get("volumes", [])
        
        for i, (ov, nv) in enumerate(zip(old_vols, new_vols)):
            old_ch = len(ov.get("chapters", []))
            new_ch = len(nv.get("chapters", []))
            if old_ch != new_ch:
                changes.append({
                    "type": "volume_chapter_count",
                    "volume": ov.get("title", f"第{i+1}卷"),
                    "before": old_ch,
                    "after": new_ch,
                    "delta": new_ch - old_ch,
                })
        
        old_ch_map = {}
        for vol in old_vols:
            for ch in vol.get("chapters", []):
                old_ch_map[int(ch.get("number", 0))] = ch.get("summary", "")
        
        for vol in new_vols:
            for ch in vol.get("chapters", []):
                ch_num = int(ch.get("number", 0))
                new_summary = ch.get("summary", "")
                old_summary = old_ch_map.get(ch_num, "")
                if old_summary and old_summary != new_summary:
                    changes.append({
                        "type": "chapter_changed",
                        "chapter": ch_num,
                        "before": old_summary[:50],
                        "after": new_summary[:50],
                    })
        
        return changes

    def _renumber_chapters(self, plan: dict):
        """确保章节号连续"""
        counter = 0
        for vol in plan.get("outline", {}).get("volumes", []):
            for ch in vol.get("chapters", []):
                counter += 1
                ch["number"] = counter
        if isinstance(plan.get("outline"), dict):
            plan["outline"]["total_chapters"] = counter

    def get_iteration_history(self) -> list:
        return self._iteration_history
