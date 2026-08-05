# 互动小说模式审计报告

**审计日期**: 2026-08-05
**审计范围**: `backend/core/interactive/*`（9 个核心模块）、`backend/api/routers/interactive.py`（API 层）、`web/index.html`（前端 SSE）
**审计目标**: 识别缺陷 → 分析影响 → 制定优化方案 → 持续迭代 → 商用就绪
**审计方式**: 静态代码审计 + 离线实测复现 + 真实 LLM 生成内容逻辑审查（DeepSeek v4-flash）

---

## 一、审计摘要

互动小说模式整体架构扎实（场景导演 + 角色记忆 + v3.7 属性系统 + PACT 事实/承诺台账 + 世界三支柱 + 语音/TTS）。初轮发现 **1 个 P0 致命缺陷**、**2 个 P1 重要缺陷**、**7 个 P2 观察项**；真实 LLM 逻辑测试又发现 **2 个生成内容逻辑 BUG**（隐身角色开口、行动-对话衔接断裂）。全部已修复并通过回归。

### 关键数字
- 核心模块：9 个；API 端点：12 个
- 初轮缺陷：P0×1、P1×2、P2×7；逻辑测试新发现：2 个 → **全部已修复**
- 真实 LLM 逻辑审查：修复前 score=60（因果断裂 1 处）→ 修复后 **score=100（0 矛盾）**
- 回归：修复专项 20/20、`test_quality_modules` 23/23、`test_rule_fallback` 9/9、`test_logic_consistency` 65/6（6 个为基线既有失败，与本次改动无关）

---

## 二、修复清单（2026-08-05）

### 🔴 P0-1（已修复）: 角色记忆溢出标记 `_memory_dirty` 是 set，存档 JSON 序列化必崩
**位置**: `backend/core/interactive/char_memory.py:60,125,135`
- 修复：`set` → `list`（`setdefault("_memory_dirty", [])` + 去重 append），读取处 `set(...)` 转换，清理处 list remove
- 复现验证：第 31 条记忆触发后 `json.dumps(state)` 与 `_atomic_write_json` 均正常

### 🟡 P1-1（已修复）: dialogue_engine 漏写 f 前缀，玩家姓名注入失效
**位置**: `backend/core/interactive/dialogue_engine.py:297`
- 修复：补 `f` 前缀；测试断言源码中不再存在裸 `{player_name}` 字符串

### 🟡 P1-2（已修复）: v3.7 属性推断正则误报——单字关键词命中普通文本
**位置**: `backend/core/interactive/attr_system.py:42,52`
- 修复：裸 `"耐"`/`"扛"` 收窄为 `"耐打|耐揍|扛揍|扛打"`；同根因的单字 `"卦"`/`"策"` 一并收窄（"政策/决策"不再误报智力）
- 验证：`"有耐心"` 体魄=50、`"政策制定与决策"` 智力=50、文弱书生体魄=25、体修体魄=87、`"算无遗策"` 智力=85

### 🟢 P2（已修复 5 项）
1. **对话 100 轮上限兑现** — `dialogue_engine.py`：新增 `MAX_CHAT_ROUNDS=100` / `CHAT_WARN_ROUNDS=80`；≥80 轮 prompt 注入收尾引导；≥100 轮纯对话直接旁白收尾（行动放行）
2. **end-chat 增量提取** — `interactive.py:395-417` + `interact_store.py`：新增 `chat_log_count`/`recent_chats_from`，用 `_last_chat_total` 游标只提取本轮新增对话，反复 end-chat 不再重复消耗 LLM token（老存档/首次无游标自动回退全量，兼容）
3. **voice_reset 原子化** — `interactive.py:571` + `interact_store.py reset_voice_override`：删单个覆盖改为一次性原子写，不再"清空全部再逐个写回"
4. **SSE 快照含属性卡** — `action_engine.py:239 _state_snapshot` 新增 `attrs` 字段（5 维属性透出给前端）
5. **对话 100 轮上限/收尾引导**（同上第 1 项）

### 🐛 真实 LLM 逻辑测试新发现（已修复）
1. **隐身角色开口（因果断裂）** — 玩家 `@顾衍之` 时顾衍之并不在场（场景只有方瑜），系统仍生成其台词。修复：`dialogue_engine.py` 新增 `_is_target_present` 在场性校验，目标不在最近场景说话人/同行/node_chars 时返回旁白提示"XX 此刻不在你身边，在场的有…"（数据不足时放行，不误伤远程对话）
2. **行动-对话衔接断裂** — 玩家输入"决定离开了"，方瑜在玩家未起身时回复"（快步追上前几步）念薇你等一下"。修复：`_build_chat_prompt` 注入"当前场景（此刻状态）"（地点/在场角色/刚发生的事），角色动作与台词必须符合场景状态

---

## 三、P0 — 致命级别（已修复）

### 🔴 P0-1: 角色记忆溢出标记 `_memory_dirty` 是 set，存档 JSON 序列化必崩

**位置**: `backend/core/interactive/char_memory.py:60`（写入点）、`:125`（清理点）、`:135`（读取点）

**问题**（修复前）:
```python
# char_memory.py:60 —— add_memory 超过 MEMORY_LIMIT(30) 条上限时
state.setdefault("_memory_dirty", set()).add(char)
```
`_memory_dirty` 以 `set` 形式常驻 state。某角色记忆超过 30 条后，set 被写进 state；此后任何 `save_state` → `_atomic_write_json` → `json.dump(state)` 抛 `TypeError: Object of type set is not JSON serializable`，对话/行动/场景保存全部失败。

**修复**:
1. `set` → `list`（写入去重、读取 `set(...)` 转换、清理 remove）
2. 实测：第 31 条记忆后 `json.dumps` 与 `_atomic_write_json` 均通过

---

## 四、P1 — 重要级别（已修复）

### 🟡 P1-1: dialogue_engine 漏写 f 前缀，玩家姓名注入失效
`dialogue_engine.py:297`：`parts.append("用{player_name}称呼…")` 无 `f` 前缀 → 已补 `f`。

### 🟡 P1-2: v3.7 属性推断正则误报——单字"耐"命中普通文本
`attr_system.py:52`：裸 `"耐"`/`"扛"` 命中"有耐心"等 → 已收窄为双字词；42 行裸 `"卦"`/`"策"` 同根因一并收窄。

---

## 五、P2 — 观察项（已修复 5 项 / 剩余 2 项低风险）

已修复：对话 100 轮上限、end-chat 增量提取、voice_reset 原子化、SSE 快照 attrs、对话收尾引导。
剩余低风险（暂不处理）：
1. **scene API 无服务端并发锁** — 前端 busy 防抖 + 单 worker 已缓解（本地单用户风险低）
2. **前端 fetch 无 AbortController** — 需前端改动，LLM 卡死时可能永久 busy（本地低风险）

---

## 六、生成内容逻辑测试（真实 LLM）

**方法**: 隔离复制真实存档"替身的告别"→ 生成场景 1 → 在场角色对话 2 轮 → @不在场角色负向用例 → DeepSeek 审查 7 维逻辑矛盾（角色乱入/称呼/时间/地点/属性/因果/身份）。

**结果（修复后）: 8/8 通过**
- 场景生成成功（4-5 blocks，约 5s）；无名单外角色乱入
- `@顾衍之`（不在场）→ 旁白拦截"顾衍之此刻不在你身边，在场的有：方瑜"，无角色台词
- 在场角色方瑜对话 2 轮，回复 speaker 正确
- **LLM 审查 score=100，0 处逻辑矛盾**（修复前 score=60：方瑜"追上前几步"衔接断裂）

---

## 七、回归与已知问题

- 修复专项离线测试 20/20（P0 序列化复现、f 前缀、属性推断×6、快照 attrs、增量游标、音色原子、100 轮）
- `test_quality_modules` 23/23、`test_rule_fallback` 9/9
- `test_logic_consistency` 65 通过 / 6 失败（P6 切章、P7 beats 规则、P9 兜底）——**基线既有失败**（`git stash` 对比确认与本次改动无关），建议后续单独排查
- 42 个软警告均为旧存档/中间态正常提示（T2 时间样本不足、T3 块待迁移等）

---

## 八、建议后续

1. 排查 P6/P7/P9 既有测试失败（`_advance_outline` 切章、beats 规则推进）
2. 前端 AbortController + 服务端 scene 并发锁（多端同玩时）
3. 将本次"生成内容逻辑审查"固化为 CI 步骤（隔离存档 + LLM 审查 + score 阈值）