"""StoryDirector — 互动小说剧情引擎（v3.0）

核心能力（对照 docs/interactive-novel-plan.html §5）：
1. 场景生成（SSE 流式）：叙事段落 + 角色台词，标记语言输出
2. 节点检测三层保障：规则预筛（保节奏下限）→ LLM 精判（防注水）→ 玩家主动（完全兜底）
3. 目标锚定 + 回扣验证：每段必须推进目标 / 回扣 active fact
4. PACT 提取：对话结束 → 结构化剧情事实（Promise/Action/Change/Trust）
5. 事实生命周期：active → fulfilled / expired / broken

性能设计（用户要求：生成不能慢）：
- 节点判定优先规则预筛，LLM 精判只对候选场景
- 场景生成单次 LLM 调用，流式输出，目标锚定检查并入下一段判定
- PACT 提取在 end-chat 时同步执行（1 次调用）
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import AsyncIterator, Optional

from ..resilient_client import ResilientLLMClient
from .action_engine import _state_snapshot

log = logging.getLogger(__name__)

# v3.5.29: 互动场景 → 正式章节正文（互动进度回流小说）
INTERACTIVE_TO_CHAPTER_SYSTEM = """你是小说章节整理师。把互动模式的场景记录整合为正式的小说章节正文。

要求：
1. 以"本章大纲摘要"为骨架，以"互动场景记录"为血肉——玩家在互动中实际经历的
   情节、做出的选择、说过的话、产生的关系变化，都必须体现在正文里
2. 视角转换：互动记录是第二人称"你"，正文改为第三人称（用主角姓名），
   保持主角内心戏的细腻度
3. 去除互动痕迹：不出现【旁白】【动作】标签、不出现"场景N"字样、不出现
   "读者""玩家"字样；整合为连贯的段落与对话
4. 小说文笔：环境描写、人物神态、对话自然，与前文风格一致；不要列提纲、
   不要总结、不要"本章讲述了"之类的说明
5. 篇幅：接近目标字数（上下浮动 20% 可接受），宁可充实不要干瘪
只输出章节正文，不要输出标题以外的任何解释。"""

# ── System Prompts ──
SCENE_SYSTEM = """你是互动小说导演。你正在导演一部可以随时与读者对话的互动小说。

**角色扮演（v3.5.12 最高优先级）**：读者不是旁观者，而是故事的主角——「读者化身」
（player_char，见输入中的"你扮演的主角"）。你就是以这个角色的身份在故事里生活，
场景必须完全以 TA 的视角展开。

输出格式（严格遵循标记语言）：
【旁白】叙事段落（1-3 段，文笔优美，类似严肃小说）——指代主角时用"你"，写主角的所见所闻所感
【角色名】该角色的台词（一段，符合人设）——NPC 对"你"说话
【动作】可选：无声的动作描写（如"她指尖一顿，茶水溅出半滴"）

规则：
1. 【旁白】是主体，承担叙事推进；台词用于关键时刻点睛
2. 每条台词必须标注说话人角色名；旁白不标注说话人
3. 剧情必须推进当前目标（objective），不可开无关新线
4. 若给定"待兑现事实"（facts），本段必须自然回扣至少 1 个（兑现/提及/利用/其后果显现）
5. 角色说话必须符合各自的人设卡与声音卡（口头禅/句式/情绪表达）
6. 单场景 300-600 字，节奏紧凑，不要在无关细节上停留
7. v3.5.20 收尾规则（替代 v3.5.5 发问收尾）：场景正常自然收尾，不要每段都以
   角色发问结尾（"一步一问"会让玩家疲惫、剧情推进慢）。**仅当本场景真的包含
   必须由读者当场决定的重大抉择**（生死/去留/信任/交易/身份揭晓）时，才以发问、
   邀约或对峙收尾；普通场景的悬念用旁白收（"她望着你的背影，欲言又止"），
   把话留给后续剧情自然展开。
8. v3.5.7 承接性（最高优先级）：若给定"读者上一步做了什么"（last_action）或
   "刚结束的对话"，本段场景必须从它的后果/余波/反应开始写——
   行动已改变剧情（地点/关系/物品/承诺），严禁无视玩家行为另起炉灶或时间倒流；
   若没有给定，则正常推进主线
   v3.5.20 承接与主线的平衡：承接玩家行动只占本段开头（1-2 句），随后必须
   回到主线轨道——每个场景都必须让 objective 有实质进展（角色关系推进/信息
   揭露/事件发生），玩家行动若偏离主线，用其后果自然牵引回主线（如玩家执意
   逛街 → 逛街中偶遇关键人物/发现线索），严禁剧情跟着闲聊原地打转
9. v3.5.12 视角规则（代入感核心）：
   - 主角（读者化身）是场景中心，旁白写 TA 的所见所闻、内心活动与身体感受
   - 指代主角一律用"你"（如"你推开门""你感到手心发凉"），严禁用"她/他/沈念薇"旁观式转述
   - 主角是行动主体：场景中的事件发生在"你"身上或眼前，不要写成上帝视角的群像
   - NPC 的台词、动作、反应都是冲着"你"来的
10. v3.5.18 铁律（绝对禁止）：严禁生成【主角名】的台词块——主角（如【沈念薇】）的
    台词/行动只能由读者输入决定，你替 TA 说话就是破坏角色扮演。若主角需要反应，
    用旁白写 TA 的心声/身体反应（如"你心中冷笑，面上不露分毫"），而不是台词。
    输出中不得出现以主角名标注的台词行。
11. v3.5.21 空间与时间连续性（P0 级）：前情摘要包含上一场景结尾（谁在场/谁刚
    离开/去了哪里/时间点）。本场景必须严格遵守——已离开的角色不能立即出现在
    现场（除非有新剧情交代其返回）；时间只能向前流动；地点的变化必须有过渡。
    若上一场景角色"推门离去"，本场景他不在场，除非剧情明确安排他回来。
12. v3.5.27 角色白名单（P0）：本场景出场角色【仅限于】"在场角色人设"名单中的角色。
    严禁引入名单之外的角色——读者没有召唤的人不会凭空出现；若剧情确实需要
    新角色，先写环境暗示（脚步声/通报/敲门声），下一场景再登场。
13. v3.5.27 环境交代（P0）：场景【开头必须】先用 1-3 句交代当前环境——
    地点（街道名/房间/氛围）、时间（时辰/光线）、天气/声响等感官细节，
    让读者清楚"我在哪里、什么情况"。禁止一上来就抛对话或直接推进动作。
14. v3.5.32 篇幅精简（P0）：单个场景总长度【200-350 字】（含旁白与台词），
    严禁超过 400 字。一个场景只推进一个事件/一个对话回合：
   - 旁白简洁：环境交代 1-3 句 + 事件推进 2-4 句，不堆砌长篇心理描写
   - 台词克制：每个角色 1-2 句，点到为止，让玩家有接话空间
   - 禁止：大段环境铺陈、多段连续心理活动、重复描述已知信息
   玩家要在移动端快速读完，宁可少写不可啰嗦。
只输出标记语言文本，不要输出解释。"""

INTRO_SYSTEM = """你是互动小说开场解说。为玩家写一份详尽的开场背景介绍（500-700 字），
用第二人称（"你"）写，像小说序章，文笔凝练有氛围感。必须覆盖以下内容（缺一不可）：

一、世界观：时代背景、主要地点、势力格局（谁掌握权力/财富，社会规则是什么）
二、主要人物背景：每个出场角色的身份、与你的关系、性格底色（人人有交代，别只列名字）
三、你的处境：你现在是谁、经历了什么、正处在什么局面
四、你的目标：当前主线目标是什么、为什么

段落分明（用空行分段），先世界观后人物再处境再目标，层层递进。
基于给定资料组织，不要编造资料之外的设定；不要写成教程，要写成有代入感的开场。
只输出介绍文本，不要输出标题和解释。"""

NODE_SYSTEM = """你是互动小说剧情节奏师。判断当前场景是否应该暂停，让读者与角色对话。
**核心原则（v3.5.3）：对话只在影响剧情走向的地方出现。** 不是"该不该聊"，
而是"这一停，会不会改变剧情走向"——不会就不停。对话是剧情的岔路口，不是聊天室。

应该触发（confidence ≥ 0.65）：
- 读者的一句话/一个决定会改变后续剧情（答应/拒绝/信任谁/跟谁走/说出秘密）
- 关键信息即将揭晓，读者有权追问或阻止（真相、阴谋、身世）
- 关系重大转折点（表白/决裂/结盟/背叛前夕）
- 角色提出明确邀请/交易/威胁，读者必须当场回应

不应该触发（confidence ≤ 0.35）：
- 场景本身已有对话且无新决策点（**已有 2 条以上角色台词 → 默认不触发**）
- 过渡、铺垫、日常推进、风景描写——叙事自行推进即可
- 只是读者想插话的场合——读者有「我要说话」按钮，想聊随时能聊，不需要系统停
- 对话无法改变剧情走向时（闲聊、寒暄、信息已定）
- v3.5.20：角色的一般性发问（征求意见"你怎么看"、寒暄式提问"最近好吗"、
  随口试探）——**提问本身不构成节点**，剧情继续推进，读者想答随时可用按钮介入

输出 JSON: {"is_node": true/false, "chars": ["在场角色名"], "suggested_rounds": 2-4, "confidence": 0-1, "reason": "一句话理由"}
只输出 JSON。"""

PACT_SYSTEM = """你是互动小说因果提取器。从读者与角色的对话中，提取影响后续剧情的事实。

提取规则：
1. Promise（承诺）：读者明确说"我会/我答应/我保证/我欠你"等 → 提取，severity high
2. Action（行动）：读者做的重要行为（威胁/示好/隐瞒/揭露/交易）→ 提取
3. Change（关系变化）：对话导致的关系/态度明显变化
4. Trust（秘密）：读者或角色透露的重要信息 → 提取
5. 读者明确说过的话必须逐条提取，一条都不能漏；宁可多提 low severity 项
6. 空泛客套（"改天请你喝茶"）标记 severity=low，不强制回扣
7. 与世界观冲突的荒诞要求（"我是皇帝"）不提取为事实，只记录为角色反应

输出 JSON:
{"facts": [{"id": "f1", "type": "promise/action/secret/change", "subject": "player/角色名", "target": "角色名/player", "content": "一句话描述", "severity": "high/medium/low", "source_chat": 轮次序号}], "relations": {"角色名": "+/-数值或描述"}, "objective_update": "更新后的目标或空", "tone": "对话基调（试探/交易/亲昵/敌对…）"}
只输出 JSON。"""

AGENDA_SYSTEM = """你是互动小说对话编排师。为即将开始的角色对话制定议程（Agenda）。

**Agenda 的目的**：让对话有方向——角色带着目的聊天，而不是陪读者闲聊。对话结束后剧情必须因这场对话而推进。

设计规则：
1. goal：这场对话要达成的目标（获取信息/说服读者/建立关系/考验读者），一句话，必须与当前主线目标相关
2. hooks：2-4 条"推进开关"——读者说出/做出什么，剧情就向前走（如"读者提到金吾卫 → 苏晚松口给线索"）。钩子是对话推进剧情的机关
3. boundaries：角色在这场对话中绝不主动透露/绝不做的事（1-3 条，如"苏晚绝不主动承认认识绣衣使"）——保留剧情张力
4. exit：对话自然收尾条件（min_rounds 最少轮数、condition 何时可以收尾）

输出 JSON:
{"goal": "一句话目标", "hooks": [{"trigger": "读者行为/话语", "outcome": "剧情推进结果"}], "boundaries": ["角色绝不主动做的事"], "exit": {"min_rounds": 3, "condition": "收尾条件"}}
只输出 JSON。"""

HOOK_VERIFY_SYSTEM = """你是互动小说钩子核对器。判断读者与角色的对话中，议程（Agenda）的"推进开关"（hooks）是否已被触发。

判断标准：
- hook 触发 = 对话中读者说/做了与 trigger 实质相符的事（包含威胁、交易、承诺、追问关键信息等）
- 读者明确拒绝/回避该话题 → hit=false，记入拒绝
- 只有闲聊寒暄 → 全部 hit=false

输出 JSON:
{"hook_hits": [{"hook_index": 0, "hit": true/false, "evidence": "对话原文摘录或'未触发'"}], "all_hit": true/false}
只输出 JSON。"""


def _parse_json(content: str) -> Optional[dict]:
    """容错 JSON 解析（复用项目通用模式）"""
    if not content:
        return None
    text = str(content).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _clean_player_dialogue(blocks: list, player_name: str) -> list:
    """v3.5.27: 过滤玩家角色的自动台词（场景/对话/行动结果通用）——
    LLM 偶发替玩家说话（如【沈念薇】xxx），玩家言行只能由读者输入决定；
    转成旁白心声（不占对话气泡、不触发语音）"""
    if not player_name:
        return blocks
    cleaned = []
    for b in blocks:
        if b.get("type") == "dialogue" and b.get("speaker") == player_name:
            cleaned.append({"type": "narration", "speaker": "",
                            "content": f"你心中所想：{b.get('content', '')}"})
        else:
            cleaned.append(b)
    return cleaned


def parse_scene_markup(text: str) -> list:
    """解析标记语言 → [{type, speaker, content}]

    【旁白】... / 【角色名】... / 【动作】...
    """
    blocks = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("【") and "】" in line:
            end = line.index("】")
            speaker = line[1:end].strip()
            content = line[end + 1:].strip()
            if not content:
                continue
            if speaker in ("旁白", "叙述", "旁白/叙述"):
                blocks.append({"type": "narration", "speaker": "", "content": content})
            elif speaker in ("动作", "描写"):
                blocks.append({"type": "action", "speaker": "", "content": content})
            else:
                blocks.append({"type": "dialogue", "speaker": speaker, "content": content})
        else:
            # 未标记的行 → 归入旁白（追加到上一块或新建）
            if blocks and blocks[-1]["type"] == "narration":
                blocks[-1]["content"] += line
            else:
                blocks.append({"type": "narration", "speaker": "", "content": line})
    return blocks


class StoryDirector:
    def __init__(self, client, model: str, store, engine=None):
        self.client = client
        self.model = model
        self.store = store
        self.engine = engine  # NovelEngine 引用（人设蒸馏/读取用，避免重复实例化）
        self._resilient = ResilientLLMClient(client, model)
        self._tracker = None   # v3.5.22: 复用小说模式 CharacterStateTracker（懒加载）
        self._supervisor = None  # v3.5.22: 复用小说模式 LogicSupervisor（懒加载）

    # ── v3.5.22: 复用小说模式逻辑引擎（不另起炉灶）──
    def _logic_tracker(self):
        """角色状态追踪器（小说模式 CharacterStateTracker）——跟踪角色位置/状态，
        保证互动场景的空间连续性有结构化依据"""
        if self._tracker is None and self.engine is not None:
            try:
                from ..character_state import CharacterStateTracker
                self._tracker = CharacterStateTracker(
                    self.engine.client, self.engine.model, self.engine.memory)
            except Exception as e:
                log.warning(f"CharacterStateTracker init failed: {e}")
        return self._tracker

    def _logic_supervisor(self):
        """逻辑监督器（小说模式 LogicSupervisor）——L1 规则引擎检查
        时间线/空间/行为/物品矛盾"""
        if self._supervisor is None and self.engine is not None:
            self._supervisor = getattr(self.engine, "logic_supervisor", None)
        return self._supervisor

    def _logic_context(self, novel_id: str) -> str:
        """复用角色状态追踪：返回当前角色状态文本（位置/健康等）供场景注入"""
        try:
            tr = self._logic_tracker()
            if tr is None:
                return ""
            tr.init_from_plan(novel_id)  # 幂等：已有状态不覆盖
            return tr.build_context(novel_id) or ""
        except Exception as e:
            log.warning(f"logic_context failed: {e}")
            return ""

    def _post_scene_logic_check(self, novel_id: str, scene_num: int, scene_text: str):
        """场景生成后（后台）：复用小说模式引擎做状态更新 + 矛盾检查"""
        try:
            # 1) 角色状态更新（提取位置/状态变化 → global_state.json）
            tr = self._logic_tracker()
            if tr is not None:
                import asyncio
                asyncio.run(tr.update_from_chapter(novel_id, scene_num, scene_text))
            # 2) L1 逻辑监督（时间线/空间/行为/物品矛盾，规则引擎零 LLM 成本）
            sup = self._logic_supervisor()
            if sup is not None:
                plan = None
                gs = None
                try:
                    plan = self.engine.memory.read("plan", novel_id)
                    gs = self.engine.memory.read("global_state", novel_id) or {}
                except Exception:
                    pass
                prev = {}
                if scene_num > 1:
                    last_scenes = self.store.recent_scenes(novel_id, 1) or []
                    if last_scenes:
                        prev[scene_num - 1] = str(last_scenes[0].get("scene_text", ""))
                res = sup.validate_chapter(scene_text, scene_num, plan or {},
                                           prev, gs, run_deep=False)
                # 视角适配：互动模式第二人称（"你"指代主角），小说模式的
                # "主角全名未出现"类检查是误报——过滤
                is_second_person = scene_text.count("你") > 10
                violations = [v for v in (res.get("violations") or [])
                              if not (is_second_person and
                                      "未出现" in str(v.get("description", "")))]
                p0 = [v for v in violations if v.get("severity") == "P0"]
                if p0:
                    cats = [f"{v.get('category', '?')}:{v.get('description', '')[:40]}" for v in p0[:3]]
                    log.warning(f"[逻辑监督] 场景{scene_num} P0 矛盾: {' | '.join(cats)}")
                    try:
                        st = self.store.load_state(novel_id)
                        if st:
                            from .char_memory import add_event
                            add_event(st, f"⚠ 检测到剧情矛盾（已记录待修正）: {p0[0].get('description', '')[:40]}", "warning")
                            self.store.save_state(novel_id, st)
                    except Exception:
                        pass
        except Exception as e:
            log.warning(f"post_scene_logic_check failed: {type(e).__name__}: {str(e)[:100]}")
        # 3) AI 痕迹检测（复用小说模式 AIDetector 离线规则，零 LLM 成本）
        try:
            from ..ai_detector import AIDetector
            det = AIDetector._offline_detect(scene_text)
            if det.get("ai_score", 0) >= 60:
                log.warning(f"[AI检测] 场景{scene_num} AI 痕迹 {det.get('ai_score')}/100")
                try:
                    st = self.store.load_state(novel_id)
                    if st:
                        from .char_memory import add_event
                        add_event(st, f"⚠ 本段 AI 腔较重（{det.get('ai_score')}/100）", "warning")
                        self.store.save_state(novel_id, st)
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"ai_detect failed: {e}")

    # ── LLM 基础 ──
    def _llm(self, system: str, user: str, temperature: float = 0.8,
             max_tokens: int = 2000) -> Optional[str]:
        try:
            resp = self._resilient.create(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content if hasattr(resp, "choices") else resp
            if isinstance(content, str):
                return content.strip()
            return None
        except Exception as e:
            log.warning(f"StoryDirector LLM call failed: {type(e).__name__}: {str(e)[:120]}")
            return None

    async def _llm_stream(self, system: str, user: str,
                          temperature: float = 0.8, max_tokens: int = 2500) -> AsyncIterator[str]:
        try:
            async for chunk in self._resilient.create_stream(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield chunk
        except Exception as e:
            log.warning(f"StoryDirector stream failed: {type(e).__name__}: {str(e)[:120]}")
            yield ""  # 空 chunk，让前端感知结束

    # ── 上下文组装 ──
    def _build_scene_prompt(self, state: dict, summary: str) -> str:
        s = state.get("state", {})
        parts = []
        parts.append(f"## 小说：《{state.get('title', '')}》（{state.get('genre', '')}·{state.get('style', '')}）")
        # v3.5.12: 玩家角色扮演——读者化身是主角，场景以 TA 视角写（代入感核心）
        pc = state.get("player_char") or {}
        if pc.get("name"):
            parts.append(f"## 你扮演的主角（读者化身）: {pc['name']}")
            if pc.get("identity"):
                parts.append(f"身份: {pc['identity']}")
            if pc.get("personality_brief"):
                parts.append(f"性格: {pc['personality_brief'][:120]}")
            parts.append("本场景完全以这位主角的视角展开：旁白用'你'指代 TA，TA 是场景中心，"
                         "事件发生在 TA 身上/眼前，严禁旁观者视角")
        # v3.2: 世界观注入（保证剧情贴合本小说设定）
        wb = state.get("worldbuilding_brief") or ""
        if wb:
            parts.append(f"## 世界观设定（必须严格遵守，不得偏离）:\n{wb[:600]}")
        # v3.5.20: 复用全局状态——时间线/章节脉络（剧情连续）+ 未回收伏笔（可呼应）
        tl = state.get("timeline_brief") or ""
        if tl:
            parts.append(f"故事时间线（保持连续，不要与已发生的事件矛盾）: {tl[:200]}")
        fs = state.get("foreshadows_brief") or ""
        if fs:
            parts.append(f"未揭晓的伏笔（剧情中可自然铺垫/呼应，不必强行回收）: {fs[:200]}")
        # v3.5.22: 复用小说模式角色状态追踪——当前角色位置/状态（结构化，防瞬移）
        nid = state.get("novel_id", "")
        if nid:
            ctx = self._logic_context(nid)
            if ctx:
                parts.append(f"当前角色状态（必须遵守，场景中角色的位置/状态以此为准）:\n{ctx[:300]}")
        parts.append(f"当前场景号: {state.get('scene_num', 0)}")
        # v3.5.28: 大纲驱动——当前章节目标（互动剧情按大纲章节推进）
        oc = state.get("outline_chapters") or []
        op = state.get("outline_progress") or {}
        if oc:
            ci = min(int(op.get("idx", 0)), len(oc) - 1)
            ch = oc[ci]
            parts.append(f"当前剧情章节（本章目标，场景必须围绕它推进）: "
                         f"第{ch.get('number', ci + 1)}章《{ch.get('title', '')}》"
                         f"（{ch.get('volume', '')}）—— {ch.get('summary', '')}")
        if s.get("location"):
            parts.append(f"地点: {s['location']}")
        if s.get("objective"):
            parts.append(f"主线目标（必须推进）: {s['objective']}")
        if s.get("flags"):
            parts.append(f"剧情标记: {'、'.join(s['flags'][-5:])}")
        # 待兑现事实（硬挂钩）
        facts = [f for f in state.get("facts", []) if f.get("status") == "active"]
        if facts:
            parts.append("待兑现事实（本段必须自然回扣至少 1 个）:")
            for f in facts[:6]:
                parts.append(f"- [{f.get('type')}] {f.get('content')}")
        # v3.3.1: 上一场对话未达成的目标（missing hooks）——软约束：后果显现/角色惦记
        missing = state.get("pending_missing_hooks") or []
        if missing:
            parts.append("上一场对话未谈成的事（本段剧情可让其后顾显现，或角色主动提起追问）:")
            for m in missing[:3]:
                parts.append(f"- {str(m)[:60]}")
        if summary:
            parts.append(f"前情摘要: {summary[:600]}")
        # v3.5.7: 读者上一步行动（承接性——新场景必须从行动后果写起）
        la = state.get("last_action") or {}
        if la and la.get("summary"):
            parts.append(f"读者上一步做了什么（本段必须从这件事的后果/余波写起，严禁无视）:")
            parts.append(f"- [{la.get('type', '行动')}] {la.get('summary', '')[:200]}")
        # v3.5.7: 刚结束的对话（承接对话结论）
        nid = state.get("novel_id", "")
        player_name = (state.get("player_char") or {}).get("name", "读者")
        recent_chats = self.store.recent_chats(nid, 6) if (nid and hasattr(self.store, "recent_chats")) else []
        chat_lines = [f"{player_name if c.get('role') == 'user' else c.get('speaker', '角色')}: {str(c.get('content', ''))[:80]}"
                      for c in recent_chats if c.get("content")]
        if chat_lines:
            parts.append("刚结束的对话（本段可自然承接其中情绪/未尽话题，但不要复述）:")
            for line in chat_lines[-4:]:
                parts.append(f"- {line}")
        # v3.5.9: 事件时间线（刚发生的事——保持剧情连续性）
        from .char_memory import events_brief
        ev_brief = events_brief(state, 5)
        if ev_brief:
            parts.append(f"最近发生的事（承接时间线，不要时间倒流）: {ev_brief}")
        # 角色卡（v3.5.12: 主角标注，防止 LLM 替主角写台词/用第三人称转述）
        casts = state.get("casts", {})
        if casts:
            parts.append("在场角色人设（说话必须符合）:")
            for name, c in casts.items():
                if name == player_name:
                    parts.append(f"- {name}（主角，由读者扮演——不要替 TA 写台词，TA 的言行由读者决定）")
                    continue
                prof = c.get("profile", {})
                brief = []
                dna = prof.get("expression_dna", [])[:2]
                for d in dna:
                    brief.append(str(d.get("name", d))[:60] if isinstance(d, dict) else str(d)[:60])
                anti = prof.get("anti_patterns", [])[:2]
                for a in anti:
                    brief.append(f"禁:{a.get('pattern', a) if isinstance(a, dict) else a}"[:60])
                if brief:
                    parts.append(f"- {name}: {'；'.join(brief)}")
        return "\n".join(parts)

    # ── 开场背景介绍（v3.5.13：玩家打开互动模式先知道"我是谁/在哪/要做什么"）──
    def generate_intro(self, novel_id: str, state: dict, force: bool = False) -> str:
        """生成/取缓存的故事背景介绍（v3.5.18: 500-700 字，覆盖世界观/人物/处境/目标）"""
        cached = state.get("intro")
        if cached and not force:
            return cached
        pc = state.get("player_char") or {}
        s = state.get("state", {})
        parts = []
        parts.append(f"小说：《{state.get('title', '')}》（{state.get('genre', '')}·{state.get('style', '')}）")
        if pc.get("name"):
            parts.append(f"你扮演：{pc['name']}（{pc.get('identity', '')}）"
                         f"{'，' + pc.get('personality_brief', '')[:120] if pc.get('personality_brief') else ''}")
        wb = state.get("worldbuilding_brief") or ""
        if wb:
            parts.append(f"世界观（时代/地点/势力/规则）：\n{wb[:600]}")
        # v3.5.18: 注入每个角色的人设档案（身份/性格/与主角关系）
        casts = state.get("casts") or {}
        player_name = pc.get("name", "")
        if casts:
            cast_lines = []
            for name, c in casts.items():
                if name == player_name:
                    continue
                prof = (c.get("profile") or {})
                brief = []
                if prof.get("identity"):
                    brief.append(f"身份:{str(prof['identity'])[:50]}")
                dna = prof.get("expression_dna") or []
                if dna:
                    d0 = dna[0]
                    brief.append(f"性格:{str(d0.get('name', d0))[:40] if isinstance(d0, dict) else str(d0)[:40]}")
                role = c.get("role", "")
                if role:
                    brief.append(f"定位:{role}")
                cast_lines.append(f"- {name}{'（' + '，'.join(brief) + '）' if brief else ''}")
            if cast_lines:
                parts.append("主要人物档案：\n" + "\n".join(cast_lines[:8]))
        if s.get("objective"):
            parts.append(f"主线目标：{s['objective'][:250]}")
        user = "\n".join(parts)
        intro = ""
        try:
            raw = self._llm(INTRO_SYSTEM, user, temperature=0.7, max_tokens=900)
            intro = (raw or "").strip()
            if len(intro) < 120:
                intro = ""
        except Exception as e:
            log.warning(f"intro 生成失败: {e}")
        if not intro:
            # 降级：模板拼接（保底有背景可看）
            name = pc.get("name", "你")
            lines = [f"你是{name}。"]
            if pc.get("identity"):
                lines.append(f"身份：{pc['identity']}。")
            if s.get("objective"):
                lines.append(f"你当前的目标：{s['objective'][:120]}。")
            if casts:
                lines.append(f"与你相关的人：{'、'.join(list(casts.keys())[:6])}。")
            if wb:
                lines.append(str(wb).replace("\n", " ")[:200])
            intro = "".join(lines)
        state["intro"] = intro
        try:
            self.store.save_state(novel_id, state)
        except Exception:
            pass
        return intro

    # ── 场景生成（SSE）──
    async def generate_scene_stream(self, novel_id: str,
                                    force_node_check: bool = True) -> AsyncIterator[dict]:
        """生成下一场景（流式）+ 结束后自动判定节点

        Yields: {type: scene_chunk/block/scene_end/node_check/error/done}
        """
        state = self.store.load_state(novel_id)
        if state is None:
            yield {"type": "error", "message": "互动存档不存在，请先 start"}
            return

        # 快照（生成前备份）
        self.store.snapshot(novel_id)
        scene_num = state.get("scene_num", 0) + 1
        summary = state.get("summary", "")

        prompt = self._build_scene_prompt(state, summary)
        collected = []

        # v3.5.19: 阶段提示——生成前告知前端（显示"正在生成…"避免用户以为卡住）
        yield {"type": "phase", "label": "📖 正在展开剧情…"}
        yield {"type": "scene_chunk", "scene_num": scene_num, "content": ""}
        try:
            # v3.5.32: max_tokens 520（≈360字）——LLM 生成即短，流式显示与落盘一致
            async for chunk in self._llm_stream(SCENE_SYSTEM, prompt, max_tokens=520):
                if chunk:
                    collected.append(chunk)
                    yield {"type": "scene_chunk", "scene_num": scene_num, "content": chunk}
        except Exception as e:
            log.error(f"Scene generation stream error: {e}")
            yield {"type": "error", "message": f"场景生成失败: {type(e).__name__}"}
            return

        scene_text = "".join(collected).strip()
        if not scene_text:
            # 兜底文本
            scene_text = f"【旁白】夜色渐深，{state.get('title', '故事')}还在继续。远处传来更鼓声，故事尚未落幕。"
            yield {"type": "scene_chunk", "scene_num": scene_num, "content": scene_text}

        # v3.5.32: 后处理硬截断——仅影响落盘/回流（前端流式已由 max_tokens 限制，
        # 不重复 yield 避免显示叠加）
        if len(scene_text) > 400:
            cut = scene_text[:380]
            for sep in ("。", "！", "？", "！？", "……"):
                idx = cut.rfind(sep)
                if idx >= 200:
                    scene_text = cut[:idx + 1] + "……"
                    break
            else:
                scene_text = cut + "……"

        # 解析 + 持久化
        blocks = parse_scene_markup(scene_text)
        # v3.5.18/v3.5.27: 过滤玩家角色的自动台词（通用函数，场景/对话/行动结果共用）
        player_name = (state.get("player_char") or {}).get("name", "")
        blocks = _clean_player_dialogue(blocks, player_name)
        scene_record = {
            "scene_num": scene_num,
            "scene_text": scene_text,
            "blocks": blocks,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.store.append_scene(novel_id, scene_record)
        # v3.5.28: 大纲章节推进——每章约 3 个场景后切下一章，目标随章更新
        try:
            self._advance_outline(novel_id, state)
        except Exception as e:
            log.warning(f"advance_outline failed: {e}")
        # v3.5.22: 复用小说模式逻辑引擎（后台，不阻塞场景流）——
        # 角色状态更新 + L1 矛盾检查
        try:
            import threading
            threading.Thread(
                target=self._post_scene_logic_check,
                args=(novel_id, scene_num, scene_text),
                daemon=True).start()
        except Exception:
            pass

        # 更新状态：场景号、摘要、最近场景
        state["scene_num"] = scene_num
        # v3.5.21: 前情摘要改"开头+结尾"双段——开头交代情境、结尾保留空间/人物状态
        # （原只取开头 300 字：角色"出门/离开"发生在场景结尾时会被截断丢失，
        #  下一场景不知情 → 出现"已出门又回到椅子上"的空间矛盾）
        _head = scene_text[:150].strip()
        _tail = scene_text[-260:].strip()
        _summary = (_head + "……" + _tail) if len(scene_text) > 420 else scene_text[:300]
        state["summary"] = _summary
        recent = state.get("recent_scenes", [])
        recent.append(_summary)
        state["recent_scenes"] = recent[-3:]
        # v3.3.1: missing hooks 只影响本段场景，用后即清（软约束不过期悬挂）
        state.pop("pending_missing_hooks", None)
        # 在场角色（从台词块提取）
        speakers = {b["speaker"] for b in blocks if b["type"] == "dialogue"}
        casts = state.get("casts", {})
        state["casts"] = {k: casts.get(k, {"present": True}) for k in casts}
        for sp in speakers:
            if sp and sp not in casts:
                casts[sp] = {"present": True, "profile": {}}
        state["casts"] = casts
        self.store.save_state(novel_id, state)

        yield {"type": "scene_end", "scene_num": scene_num, "blocks": blocks,
               "snapshot": _state_snapshot(state)}

        # ── 节点判定（三层保障的 ① 规则预筛 + ② LLM 精判）──
        if force_node_check:
            # v3.5.19: 节点判定/议程生成可能耗时 3-8s——先提示用户
            yield {"type": "phase", "label": "🤔 正在判断剧情走向…"}
            is_node, node_chars, rounds, reason = self._decide_node(novel_id, scene_num, blocks, state)
            # v3.5.16: 对话候选排除玩家自己（玩家是主角，只跟 NPC 对话）
            player_name = (state.get("player_char") or {}).get("name", "")
            if player_name and player_name in node_chars:
                node_chars = [c for c in node_chars if c != player_name]
            state = self.store.load_state(novel_id)
            state["pending_node"] = is_node
            state["node_chars"] = node_chars
            state["node_rounds"] = rounds
            agenda = None
            if is_node:
                # v3.3: Agenda 机制——对话前生成议程（目标/推进开关/边界），对话围绕它推进
                yield {"type": "phase", "label": "📋 正在安排这场对话…"}
                agenda = self._generate_agenda(novel_id, node_chars, state)
                state["agenda"] = agenda
            self.store.save_state(novel_id, state)
            yield {
                "type": "node_check",
                "is_node": is_node,
                "chars": node_chars,
                "suggested_rounds": rounds,
                "reason": reason,
                "agenda": agenda,   # 前端可展示"这场对话要谈什么"
                "snapshot": _state_snapshot(state),
            }

        yield {"type": "done"}

    # ── 节点判定 ──
    def _decide_node(self, novel_id: str, scene_num: int, blocks: list, state: dict) -> tuple:
        """① 规则预筛 → ② LLM 精判。返回 (is_node, chars, rounds, reason)

        v3.5.3 节奏定稿（老赵："对话只在影响剧情走向的地方"）：
        - 场景已有 ≥2 条角色台词 → 不触发（叙事里已经对话过了，不再打断）
        - 规则 2 保底：连续 3 段无对话才强制（防纯文字荒漠，但不频繁）
        - LLM 精判为主力：阈值 0.5，判定导向"是否影响剧情走向"
        - 玩家主动权兜底：「我要说话」按钮随时可发起
        """
        chars = self._scene_chars(blocks)
        # v3.5.17: 场景纯旁白（v3.5.12 后"你"视角开场可能无 NPC 台词）→
        # 用在场角色兜底，保证开场节点必有对话对象
        if not chars:
            player_name = (state.get("player_char") or {}).get("name", "")
            chars = [n for n in (state.get("casts") or {}) if n != player_name][:3]
        if not chars:
            return False, [], 0, "无在场角色"
        # 规则 1：开场第 1 段必触发（首次体验，优先于其他规则）
        if scene_num <= 1:
            return True, chars, 3, "开场互动"
        # 规则 0（v3.5.3）：场景已有充分对话 → 不触发（不再打断）
        dialogue_count = sum(1 for b in blocks if b.get("type") == "dialogue")
        if dialogue_count >= 2:
            return False, chars, 0, "场景已有充分对话"
        # 规则 2：连续 3 段无对话 → 强制（保互动频率下限）
        last_three = self.store.recent_scenes(novel_id, 3)
        if len(last_three) >= 3:
            no_dialogue = all(
                not any(b.get("type") == "dialogue" for b in sc.get("blocks", []))
                for sc in last_three[-3:]
            )
            if no_dialogue:
                return True, chars, 4, "连续三段无对话"
        # 规则 3：强冲突事件（真正需要玩家抉择的时刻）
        text = " ".join(b["content"] for b in blocks)
        strong_kw = ["拔剑", "刀架", "生死", "追杀", "真相大白", "身份暴露", "决裂", "挟持",
                     "下跪", "自尽", "灭口", "当场", "对质", "摊牌", "交易达成", "背叛"]
        hit = next((k for k in strong_kw if k in text), "")
        if hit:
            return True, chars, 5, f"重大事件: {hit}"
        # LLM 精判（规则未命中才调用）
        result = self._llm_judge_node(text, chars, state)
        if result is None:
            return False, chars, 2, "判定失败，默认不触发（玩家可主动介入）"
        is_node = bool(result.get("is_node"))
        confidence = float(result.get("confidence", 0.5))
        # v3.5.20: 阈值 0.55（过滤一般性发问型场景——"你怎么看"类提问置信度
        # 通常中等，不再触发节点）；刚对话过（<2 段）且置信度一般时抑制
        last_chat_gap = scene_num - state.get("_last_chat_scene", 0)
        if is_node:
            if confidence < 0.55:
                return False, chars, 0, f"置信度不足({confidence:.1f})"
            if confidence < 0.7 and last_chat_gap < 2:
                return False, chars, 0, "刚对话过且置信度一般"
        rounds = int(result.get("suggested_rounds", 3) or 3)
        rounds = max(2, min(rounds, 5))
        return is_node, result.get("chars") or chars, rounds, result.get("reason", "")

    def _llm_judge_node(self, text: str, chars: list, state: dict) -> Optional[dict]:
        user = (
            f"场景文本:\n{text[:800]}\n\n"
            f"在场角色: {', '.join(chars)}\n"
            f"距上次对话: {state.get('scene_num', 0) - state.get('_last_chat_scene', 0)} 段\n"
            f"主线目标: {state.get('state', {}).get('objective', '')}\n"
            f"请判断是否应该暂停让读者与角色对话。"
        )
        raw = self._llm(NODE_SYSTEM, user, temperature=0.3, max_tokens=300)
        return _parse_json(raw) if raw else None

    # ── Agenda 机制（v3.3：对话轨道）──
    def _generate_agenda(self, novel_id: str, chars: list, state: dict) -> Optional[dict]:
        """对话前生成议程：goal（目标）/ hooks（推进开关）/ boundaries（边界）/ exit（收尾条件）

        对话引擎据此"带目标对话"，PACT 提取后据此核对钩子是否命中——
        对话从自由漫游变为受控推进。
        """
        if not chars:
            return None
        s = state.get("state", {})
        casts = state.get("casts", {})
        char_briefs = []
        for name in chars[:3]:
            prof = (casts.get(name) or {}).get("profile", {})
            dna = prof.get("expression_dna", [])[:2]
            brief = "；".join(
                str(d.get("name", d))[:50] if isinstance(d, dict) else str(d)[:50] for d in dna
            ) or "（人设未蒸馏）"
            char_briefs.append(f"- {name}: {brief}")
        facts = [f for f in state.get("facts", []) if f.get("status") == "active"]
        user = (
            f"主线目标: {s.get('objective', '') or '（未定）'}\n"
            f"剧情标记: {'、'.join(s.get('flags', [])[-5:]) or '（无）'}\n"
            f"待兑现事实: {'；'.join(f.get('content', '') for f in facts[:5]) or '（无）'}\n"
            f"最近剧情: {state.get('summary', '')[:200]}\n"
            f"对话角色:\n{chr(10).join(char_briefs)}\n"
            f"请为这场对话制定议程（goal 必须与主线相关，hooks 是剧情推进开关）。"
        )
        raw = self._llm(AGENDA_SYSTEM, user, temperature=0.4, max_tokens=500)
        agenda = _parse_json(raw) if raw else None
        if not isinstance(agenda, dict):
            log.warning(f"Agenda 生成失败，使用默认议程: {novel_id}")
            agenda = {
                "goal": f"推进主线：{s.get('objective', '继续旅程')}",
                "hooks": [],
                "boundaries": [],
                "exit": {"min_rounds": 3, "condition": "读者已了解当前处境"},
            }
        # 规范化
        agenda.setdefault("goal", s.get("objective", "") or "继续旅程")
        agenda["hooks"] = [h for h in agenda.get("hooks", []) if isinstance(h, dict)][:4]
        agenda["boundaries"] = [str(b)[:80] for b in agenda.get("boundaries", [])[:3]]
        ex = agenda.get("exit") or {}
        try:
            min_rounds = max(2, min(int(ex.get("min_rounds", 3) or 3), 10))
        except (TypeError, ValueError):
            min_rounds = 3
        agenda["exit"] = {"min_rounds": min_rounds,
                          "condition": str(ex.get("condition", ""))[:100] or "目标已达成"}
        return agenda

    def verify_hooks(self, novel_id: str, agenda: dict) -> dict:
        """钩子核对：对话结束后检查议程的推进开关是否被触发（1 次轻量 LLM 调用）

        返回: {hook_hits: [{hook_index, hit, evidence}], all_hit, missing: [未触发钩子]}
        """
        hooks = (agenda or {}).get("hooks", [])
        if not hooks:
            return {"hook_hits": [], "all_hit": True, "missing": []}
        chat = self.store.recent_chats(novel_id, 40)
        transcript = []
        for i, e in enumerate(chat):
            if e.get("type") == "action_result":
                role = "行动结果"
            else:
                role = "读者" if e.get("role") == "user" else f"角色{e.get('speaker', '')}"
            transcript.append(f"[{i}] {role}: {e.get('content', '')[:150]}")
        hook_lines = "\n".join(
            f"- hook[{i}] trigger: {h.get('trigger', '')} → outcome: {h.get('outcome', '')}"
            for i, h in enumerate(hooks)
        )
        user = f"对话记录:\n" + "\n".join(transcript[-30:]) + f"\n\nAgenda 推进开关:\n{hook_lines}\n请逐条核对。"
        raw = self._llm(HOOK_VERIFY_SYSTEM, user, temperature=0.2, max_tokens=500)
        result = _parse_json(raw) if raw else None
        if not isinstance(result, dict):
            return {"hook_hits": [], "all_hit": False, "missing": [h.get("trigger", "") for h in hooks]}
        hits = result.get("hook_hits", []) or []
        hit_map = {}
        for hh in hits:
            if isinstance(hh, dict) and "hook_index" in hh:
                hit_map[int(hh["hook_index"])] = bool(hh.get("hit"))
        hook_hits = []
        missing = []
        for i, h in enumerate(hooks):
            evidence = ""
            hit = hit_map.get(i, False)
            for hh in hits:
                if isinstance(hh, dict) and hh.get("hook_index") == i:
                    evidence = str(hh.get("evidence", ""))[:80]
                    break
            hook_hits.append({"hook_index": i, "trigger": h.get("trigger", ""),
                              "hit": hit, "evidence": evidence})
            if not hit:
                missing.append(h.get("trigger", ""))
        return {"hook_hits": hook_hits, "all_hit": len(missing) == 0, "missing": missing}

    @staticmethod
    def _scene_chars(blocks: list) -> list:
        seen, out = set(), []
        for b in blocks:
            sp = b.get("speaker", "")
            if sp and sp not in seen:
                seen.add(sp)
                out.append(sp)
        return out

    # ── PACT 提取（对话结束）──
    def extract_pact(self, novel_id: str, chat_entries: list) -> dict:
        """对话 → 剧情事实（PACT）。返回提取结果，并写入 state"""
        if not chat_entries:
            return {"facts": [], "relations": {}, "objective_update": "", "tone": ""}
        transcript = []
        for i, e in enumerate(chat_entries):
            role = "读者" if e.get("role") == "user" else f"角色{e.get('speaker', '')}"
            transcript.append(f"[{i}] {role}: {e.get('content', '')[:200]}")
        user = "对话记录:\n" + "\n".join(transcript[-40:])
        raw = self._llm(PACT_SYSTEM, user, temperature=0.3, max_tokens=1500)
        result = _parse_json(raw) if raw else {}
        if not isinstance(result, dict):
            result = {}

        facts = result.get("facts", []) or []
        state = self.store.load_state(novel_id)
        if state is None:
            return result
        existing_ids = {f.get("id") for f in state.get("facts", [])}
        # 内容级去重（防 LLM 重复提取同一事实）
        existing_contents = {str(f.get("content", ""))[:40] for f in state.get("facts", [])}
        for f in facts:
            if not f.get("content"):
                continue
            if f.get("id") in existing_ids:
                continue
            # 相似内容合并（前 40 字相同视为重复）
            if str(f.get("content", ""))[:40] in existing_contents:
                continue
            f.setdefault("id", f"f{int(time.time())}_{uuid.uuid4().hex[:4]}")
            f.setdefault("status", "active")
            f.setdefault("due_scene", state.get("scene_num", 0) + 3)
            f.setdefault("evidence", [])
            f["severity"] = f.get("severity", "medium")
            existing_ids.add(f["id"])
            existing_contents.add(str(f.get("content", ""))[:40])
            state.setdefault("facts", []).append(f)
        # relations 合并（只保留真实角色名 key，过滤 player-xxx / xxx-player 垃圾 key）
        rel = result.get("relations") or {}
        casts = state.get("casts", {}) or {}
        for k, v in rel.items():
            if not isinstance(k, str) or "-" in k or k not in casts:
                continue  # LLM 输出的对话基调（"player-沈砚": "试探"）不是关系值，跳过
            rel_map = state["state"].setdefault("relations", {})
            cur = rel_map.get(k, 0)
            try:
                if isinstance(v, str):
                    # 提取开头的 +/- 数字部分（如 "+1（因坦诚而增加信任）"）
                    import re
                    m = re.match(r"^([+-]\d+)", v.strip())
                    if m:
                        delta = int(m.group(1))
                        rel_map[k] = max(0, min(100, int(cur) + delta))
                    elif isinstance(cur, int):
                        rel_map[k] = v  # 无数字前缀的描述保留原文
                    else:
                        rel_map[k] = v
                elif isinstance(v, (int, float)):
                    rel_map[k] = max(0, min(100, int(cur) + int(v)))
                else:
                    rel_map[k] = v
            except (TypeError, ValueError):
                rel_map[k] = v
        # objective 更新
        if result.get("objective_update"):
            state["state"]["objective"] = result["objective_update"]
        # v3.5.9: 对话沉淀为角色记忆——PACT facts 同步进目标角色的专属记忆
        from .char_memory import add_event, add_memory
        for f in state.get("facts", []):
            target = f.get("target") or ""
            if not target or target == "player":
                continue
            tag = {"promise": "承诺", "threat": "威胁", "request": "请求",
                   "secret": "秘密", "info": "告知", "break": "违约"}.get(
                str(f.get("type", "")), "约定")
            add_memory(state, target, "promise",
                       f"读者{tag}了你：{f.get('content', '')}",
                       source="pact")
        # 关系变化 → 事件时间线
        if result.get("relations"):
            rel_changed = [f"{k} ♥{v}" for k, v in result.get("relations", {}).items()
                           if isinstance(k, str) and "-" not in k and k in (state.get("casts") or {})]
            if rel_changed:
                add_event(state, "关系变化: " + "、".join(rel_changed[:3]), "relation")
        state["_last_chat_scene"] = state.get("scene_num", 0)
        self.store.save_state(novel_id, state)
        return result

    # ── 目标锚定 + 回扣验证（并入节点判定，不单独调用）──
    # 说明：目标锚定通过 _build_scene_prompt 注入 objective + facts 硬约束，
    # 回扣验证合并进下一次 _decide_node 的规则 2（连续 2 段无对话强制节点），
    # 以及状态校验器（每 3 段由前端触发 scene 时附带 summary 比对，v1 简化）。

    # ── 工具 ──
    def build_context_from_bible(self, novel_id: str) -> dict:
        """从 character_bible / plan 构建初始互动上下文

        返回: {title, genre, style, protagonist_name, casts_preview}
        """
        from ..mixins.character_profile import CharacterProfileMixin  # noqa 防循环

        try:
            novel = None
            # 通过 engine 的 get_novel 获取
            from ..engine import NovelEngine
            # 避免重复实例化：直接读 plan.json
            import os
            from config import NOVELS_DIR
            plan_path = os.path.join(NOVELS_DIR, novel_id, "plan.json")
            if os.path.exists(plan_path):
                with open(plan_path, "r", encoding="utf-8") as f:
                    novel = json.load(f)
        except Exception as e:
            log.warning(f"build_context_from_bible read plan failed: {e}")
            novel = None
        if not novel:
            return {"title": novel_id, "genre": "", "style": "",
                    "protagonist_name": "", "casts_preview": {}}
        proto = novel.get("protagonist") or {}
        # v3.5.5: characters 字段结构（characters.protagonist）优先
        chars_field = novel.get("characters") or {}
        if not proto and isinstance(chars_field, dict):
            proto = chars_field.get("protagonist") or {}
        casts_preview = {}
        if proto.get("name"):
            casts_preview[proto["name"]] = {"role": "protagonist", "desc": str(proto.get("personality", ""))[:80]}
        for c in novel.get("supporting", []) or []:
            if isinstance(c, dict) and c.get("name"):
                casts_preview[c["name"]] = {"role": "supporting", "desc": str(c.get("personality", ""))[:80]}
        for c in novel.get("antagonist", []) or []:
            if isinstance(c, dict) and c.get("name"):
                casts_preview[c["name"]] = {"role": "antagonist", "desc": str(c.get("personality", ""))[:80]}
        # v3.5.5: characters 字段里的配角/反派
        if isinstance(chars_field, dict):
            for role_key in ("supporting", "antagonist"):
                group = chars_field.get(role_key) or []
                if isinstance(group, list):
                    for c in group:
                        if isinstance(c, dict) and c.get("name") and c["name"] not in casts_preview:
                            casts_preview[c["name"]] = {"role": role_key,
                                                        "desc": str(c.get("personality", ""))[:80]}
        return {
            "title": novel.get("title", novel_id),
            "genre": novel.get("genre", ""),
            "style": novel.get("style", ""),
            "protagonist_name": proto.get("name", ""),
            "protagonist": proto,   # v3.5.5: 完整主角信息（玩家扮演角色）
            "casts_preview": casts_preview,
            "worldbuilding": novel.get("worldbuilding", {}),
        }

    def _advance_outline(self, novel_id: str, state: dict):
        """v3.5.28: 大纲章节推进——场景数达阈值切下一章，objective 随章更新

        每章约 3 个场景（场景数/章按章节 target_words 微调：<2500 字 2 场景，>=5000 字 4 场景）
        v3.5.29: 切章时后台把本章互动剧情沉淀为小说章节正文（互动→章节回流）
        """
        chs = state.get("outline_chapters") or []
        if not chs:
            return
        op = dict(state.get("outline_progress") or {})
        idx = int(op.get("idx", 0))
        # v3.5.30: 最后一章已回流过（final_done）→ 不再触发
        if op.get("final_done"):
            return
        cnt = int(op.get("scene_in_chapter", 0)) + 1
        ch = chs[min(idx, len(chs) - 1)]
        tw = int(ch.get("target_words", 0) or 0)
        per = 4 if tw >= 5000 else (2 if 0 < tw < 2500 else 3)
        # v3.5.30: 最后一章也回流（原条件 idx < len-1 导致最后一章永远不生成章节正文）
        if cnt >= per:
            # ── 本章完成：把 [scene_start, scene_num-1] 的互动剧情沉淀为章节正文 ──
            done_idx = idx
            scene_start = int(op.get("scene_start", 1) or 1)
            try:
                import threading
                threading.Thread(
                    target=self._sync_chapter_from_interactive,
                    args=(novel_id, done_idx, scene_start, state.get("scene_num", 0) or 0),
                    daemon=True,
                ).start()
            except Exception as e:
                log.warning(f"chapter sync thread failed: {e}")
            if idx < len(chs) - 1:
                idx += 1
                cnt = 0
                # v3.5.30: 不再覆盖 objective——目标字段由 PACT 维护（玩家对话产生的方向），
                # 大纲章节目标已在场景 prompt 单独注入"当前剧情章节"，两轨并行不冲突
                log.info(f"Outline advanced → 第{nch.get('number', idx + 1)}章《{nch.get('title', '')}》")
            # 最后一章完成：保持 idx 不变，标记 final_done 防重复回流
            state["outline_progress"] = {"idx": idx, "scene_in_chapter": cnt,
                                         "scene_start": state.get("scene_num", 0) or 0,
                                         "final_done": True}
        else:
            state["outline_progress"] = {"idx": idx, "scene_in_chapter": cnt,
                                         "scene_start": op.get("scene_start", 1) or 1}
        self.store.save_state(novel_id, state)

    def _sync_chapter_from_interactive(self, novel_id: str, chapter_idx: int,
                                       scene_start: int, scene_end: int):
        """v3.5.29: 互动→章节回流——把一章的互动场景 + 玩家行动整合为正式章节正文

        后台线程执行（不阻塞场景流）。场景文本（第二人称"你"）→ 章节正文
        （第三人称主角名），玩家的选择与行动必须体现在正文中。
        """
        try:
            chs = (self.store.load_state(novel_id) or {}).get("outline_chapters") or []
            if chapter_idx >= len(chs):
                return
            ch = chs[chapter_idx]
            ch_num = int(ch.get("number", chapter_idx + 1))
            # 收集本章场景
            scenes = []
            for rec in self.store.recent_scenes(novel_id, 200):
                sn = int(rec.get("scene_num", 0) or 0)
                if scene_start <= sn <= max(scene_end, scene_start):
                    scenes.append((sn, rec.get("scene_text", "")))
            scenes.sort()
            if not scenes:
                return
            # 收集玩家行动/对话
            player_acts = []
            try:
                for h in self.store.recent_chats(novel_id, 200):
                    if h.get("role") == "user" and h.get("content"):
                        player_acts.append(str(h.get("content"))[:100])
            except Exception:
                pass
            scene_text = "\n\n".join(f"[场景{sn}]\n{t}" for sn, t in scenes)
            user = (
                f"## 本章大纲摘要（骨架）\n第{ch_num}章《{ch.get('title', '')}》"
                f"（{ch.get('volume', '')}）\n{ch.get('summary', '')}\n\n"
                f"## 互动场景记录（本玩家真实经历，含其选择与行动）\n{scene_text[:6000]}\n\n"
                + (f"## 玩家在互动中的行动/对话（必须体现在正文）\n"
                   + "\n".join(f"- {a}" for a in player_acts[-8:]) if player_acts else "")
                + f"\n\n请把以上内容整理成正式章节正文（{max(800, int(ch.get('target_words', 1500) or 1500))} 字左右）。"
            )
            raw = self._llm(INTERACTIVE_TO_CHAPTER_SYSTEM, user, temperature=0.7, max_tokens=4000)
            body = (raw or "").strip()
            if len(body) < 200:
                return
            # v3.5.29: LLM 输出可能自带章节标题（## 第X章），剥掉避免与文件头重复
            import re as _re
            body = _re.sub(r"^#{1,3}\s*第?\s*\d+\s*章.*?\n+", "", body, count=1)
            # 写入 chapters/
            import os
            from config import NOVELS_DIR
            ch_dir = os.path.join(NOVELS_DIR, novel_id, "chapters")
            os.makedirs(ch_dir, exist_ok=True)
            fname = f"chapter_{ch_num:04d}.md"
            fpath = os.path.join(ch_dir, fname)
            if os.path.exists(fpath):
                os.replace(fpath, fpath + f".bak_{int(time.time())}")  # 保底备份
            content = f"# 第{ch_num}章 {ch.get('title', '')}\n\n{body}\n"
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            # 同步根 state 进度
            gs_path = os.path.join(NOVELS_DIR, novel_id, "state.json")
            if os.path.exists(gs_path):
                try:
                    with open(gs_path, "r", encoding="utf-8") as f:
                        gs = json.load(f)
                    gs["current_chapter"] = ch_num
                    if ch_num not in (gs.get("completed_chapters") or []):
                        gs.setdefault("completed_chapters", []).append(ch_num)
                    gs["total_words"] = int(gs.get("total_words", 0) or 0) + len(body)
                    gs.setdefault("summaries", {})[str(ch_num)] = body[:120]
                    with open(gs_path, "w", encoding="utf-8") as f:
                        json.dump(gs, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    log.warning(f"gs update failed: {e}")
            log.info(f"Chapter {ch_num} synced from interactive ({len(scenes)} scenes, {len(body)} chars)")
        except Exception as e:
            log.warning(f"_sync_chapter_from_interactive failed: {type(e).__name__}: {str(e)[:100]}")
        # v3.5.31: 章节结束 → 滚动压缩角色记忆（后台，LLM 提炼旧记忆为长期摘要）
        try:
            from .char_memory import compress_all_memories
            _st = self.store.load_state(novel_id)
            if _st and compress_all_memories(_st, self._llm):
                self.store.save_state(novel_id, _st)
                log.info("Memories compressed after chapter sync")
        except Exception as e:
            log.warning(f"memory compress failed: {type(e).__name__}: {str(e)[:80]}")

    def attach_cast_profiles(self, novel_id: str, char_names: list):
        """为出场角色挂载人设卡（有蒸馏数据则用，无则留空由对话引擎即时蒸馏兜底）"""
        engine = self.engine
        if engine is None:
            return
        state = self.store.load_state(novel_id)
        if state is None:
            return
        casts = state.get("casts", {})
        changed = False
        player_name = (state.get("player_char") or {}).get("name", "")
        for name in char_names:
            if not name:
                continue
            # v3.5.16: 玩家角色不挂人设（由玩家扮演，不需要 AI 蒸馏）
            if player_name and name == player_name:
                continue
            existing = casts.get(name, {})
            if existing.get("profile"):
                continue
            try:
                prof = engine.get_character_profile(novel_id, name)
                if prof and "error" not in prof:
                    casts.setdefault(name, {})["profile"] = prof
                    changed = True
            except Exception:
                pass
        if changed:
            state["casts"] = casts
            self.store.save_state(novel_id, state)
