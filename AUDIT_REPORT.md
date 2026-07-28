# NovelGenerator 商业化质量审计报告

**审计日期**: 2026-07-28
**审计范围**: backend/core/*.py（16个核心模块）、API server、web前端
**审计目标**: 识别漏洞→分析影响→制定优化方案→持续迭代→商用就绪

---

## 一、审计摘要

系统基础架构扎实（多Agent协同、记忆管理、两遍生成），但商用化还有显著差距。共发现 **4个P0致命缺陷**、**6个P1重要缺陷**、**5个P2优化项**。

### 关键数字
- 代码量：~12,000 行 Python + ~3,500 行前端
- 核心模块：16 个
- 质量门：3 层（PacingCheck → AI检测 → 截断检测）
- **已发现但未修复的已知bug：至少 6 个**
- **实测产出质量：60-70分（网文及格线）**

---

## 二、P0 — 致命级别（立即修复，阻塞商用）

### 🔴 P0-1: 章末元指令泄漏风险 — 已缓解但未根治

**位置**: `shared_memory.py:706-718` + `writer.py:279-298`

**问题**:
```python
# shared_memory.py 生成的 outline_text 使用了 ═══ 分隔符和「你必须写」等指令语
outline_text = f"""═══ 以下为写作元指令 ═══
本章必须覆盖以下核心事件，不可偏离：
...
═══ 结束 ═══"""
```

Writer 已经做了提取（Line 283提取到 system prompt），但：
1. **Phase 2 ending generator** 不走这条路径——它直接收 `ending_prompt`（writer.py:442），没有元指令过滤
2. **原子化模式** 也不走这条路径
3. 如果 `instr_marker` 匹配失败（分隔符格式变化），整段元指令会留在 user prompt 中

**实测影响**: 
- 2026-07-27 测试中 Ch2 产出包含 "AI cliches: ['嘴角勾起', '瞳孔骤缩', '浑身一震', '仿佛']"
- Ch3 开头仍有大量短碎片（31个 ≤10 字符的碎片）

**修复方案**:
1. 元指令从自然语言改为**结构化 JSON**，Writer 内部自动解析，user prompt 中只放纯净的正文指令
2. Phase 2 ending generator 增加元指令过滤
3. 增加「指令泄漏检测」——生成后自动扫描正文中是否出现「═══」「元指令」「你必须写」

---

### 🔴 P0-2: 质量门误杀率过高 — 有价值内容被误判为 AI

**位置**: `engine.py:817-855` + `pacing_checker.py`

**问题**:
- Quality gate 阈值 `score < 40` 触发重写，但 PacingChecker 的 `quick_quality_check` 基于**规则匹配**（非语义理解）
- 实测：爽文打脸场景常被判为"节奏过快"或"对话过多"，导致高质量打脸章节被误杀
- 重写后的文本不一定更好——`qr2 > qr + 5` 的判断太粗糙，可能接受了更差的结果

**修复方案**:
1. 质量门从纯规则→**规则+LLM双重验证**（规则初筛 + LLM确认）
2. 误杀率监控 — 每次重写记录 `before_score / after_score / 用户是否满意`，积累数据集
3. 引入 **A/B 测试框架**：同一章两版都保存，让用户选择

---

### 🔴 P0-3: 上下文窗口超载 — 长篇小说后期 prompt 爆炸

**位置**: `shared_memory.py:504-767` (_build_writer_context_impl)

**问题**:
- Writer context 组装了 **7层信息**：
  - L1: 核心设定(世界+角色)
  - L1b: 世界档案
  - L2: 上一章桥接+结尾原文(1000字)
  - L2c: 前几章剧情摘要
  - L3: 全局状态快照
  - L4: 伏笔 → L4b: 剧情图谱
  - L5: 本章大纲+元指令
- 到第50章时，prompt 可能超过 12,000 字符，挤占生成空间
- **Writer 的 max_tokens 计算公式 `target_words * 3`** 不扣除 prompt 开销，导致生成不足

**实测影响**: 长篇章的结尾经常"感觉没写完"——因为 prompt 太长，留给生成的 token 不够

**修复方案**:
1. **分级截断策略**: L1始终保留，L2-L4 按章节号动态压缩（前3章全文→后3章摘要→更早的1行摘要）
2. **Prompt token 预算**: 章节越往后，prompt 越精简，保证至少 60% token 留给生成
3. **记忆摘要合并**: 每隔5章自动将分散的记忆文件合并为1个「前情提要」摘要

---

### 🔴 P0-4: 对话密度的恶性循环 — 系统在教 LLM 多写对话

**位置**: `shared_memory.py:722-747`（对话密度告警）

**问题**:
- 系统检测到连续2章对话 >40% 后会注入「控制对话量」告警
- 但这个告警本身 **增加了 prompt 长度**，而 LLM 倾向于"看到什么就写什么"
- 告警中说「禁止角色之间来回确认已经知道的信息」——这反而提醒了 LLM 关于对话的上下文

**实测数据**（7/27 测试 Ch2）:
- Ch2 非空行 122，短碎片(≤10字) 31（25.4%）——大量是「」「」引号内容被切断
- 对话占比估算 >35%

**修复方案**:
1. **对话密度 → 从"告警"变为"硬约束"**：直接在 system prompt 中限制「本章对话占比 ≤30%，连续对话 ≤4轮」
2. **生成后对话密度检测**：超过阈值自动裁剪最长对话段落
3. **对话质量指标**：不只是量，还要看"每段对话是否推进剧情"

---

## 三、P1 — 重要缺陷（本周修复）

### 🟡 P1-1: Beat 分解器与 Writer 脱节

**位置**: `beat_decomposer.py` → `writer.py`

**问题**:
- BeatDecomposer 把章节大纲分解成 5-8 个 beat
- 但 Writer **完全不使用** beat 信息——它直接根据 `chapter_outline['summary']` 一次性写整章
- 只有在 AtomicWriter 路径（`engine.atomic_generate_chapter_stream`）才用 beat
- 两个路径质量差异大：普通路径一次性出整章（趋同），原子路径逐beat出章节（多样性好但连贯性差）

**修复方案**:
1. 统一：普通 Writer 也接收 beat 信息，按 beat 分节写作
2. 或者：废弃普通路径，全用原子化（加连贯性后处理）

---

### 🟡 P1-2: Humanizer 跳过逻辑过于激进

**位置**: `writer.py:510-543`

**问题**:
```python
# 初稿 ≥2000字 且 评分 ≥50 → 跳过 Humanizer 重写
if len(final_text) >= 2000 and h_result["score"] >= 50:
    log.info(f"Skipping Humanizer pass (score OK: {h_result['score']})")
```

- `score >= 50` 就跳过？50 分只是及格，商用标准应该≥70
- `len(final_text) >= 2000` 也跳过——长文更容易暴露 AI 痕迹
- 这意味着大部分章节都不经过 Humanizer 处理

**修复方案**:
1. Humanizer 阈值从 50 → 70
2. 长度条件去掉——长文也需要 Humanizer
3. 引入「局部 Humanizer」：只改写 AI 痕迹最重的段落（用 AI Detector 标记），而不是全章重写

---

### 🟡 P1-3: 截断检测逻辑不完整

**位置**: `writer.py:588-615` (_check_truncation)

**问题**:
```python
# Hook check — 太宽松
has_hook = any(kw in last_100 for kw in ["突然", "忽然", "这时", "那一刻", "然后", "但是", "然而", "奇怪", "……", "?"])
```

- "然后" "但是" "这时" 这些词几乎每章结尾都有，不算钩子
- 真正的钩子应该是"悬念+冲突+期待感"的组合

**修复方案**:
1. 钩子检测改为 LLM 判断：「这句话读完是否让读者产生'然后呢'的强烈冲动？」
2. 增加「结尾完整性」检测：最后一段是否在句号/问号/省略号处结束
3. 对商业爽文：钩子强度的量化标准（0-100分）

---

### 🟡 P1-4: 角色状态追踪形同虚设

**位置**: `character_state.py` + `shared_memory.py:652-658`

**问题**:
- CharacterStateTracker 每章更新 `global_state.json`，但更新依赖 **LLM 调用**（`update_from_chapter`）
- 实测发现：LLM 更新经常遗漏细微状态变化（如"左手受了轻伤""对XX产生了怀疑"）
- `_build_character_state_context` 只读取 `protagonist_state` 字段，大量配角状态被丢弃
- 状态文件 TTL 10s，但实际更新频率极低（只在章节生成后调用一次），TTL 设置无意义

**修复方案**:
1. 角色状态从「LLM 提取」→「LLM 提取 + 规则校验」双保险
2. 增加「角色知识图谱」：关系变化自动触发状态更新
3. 配角状态不再丢弃，保留最近5章的状态变化

---

### 🟡 P1-5: Planner 产出 JSON 结构不稳定

**位置**: `planner.py`

**问题**:
- Planner 输出 JSON，但 LLM 经常产出畸形 JSON（缺字段、嵌套错误）
- `engine.py:726-737` 有兜底，但兜底太简陋——summary是「继续推进主线剧情发展」
- 「大纲→正文」的信息传递完全依赖 `chapter_outline['summary']` 一个字段，beat/hook/cause 经常为空

**修复方案**:
1. Planner 增加 JSON schema 强校验，格式不对自动重试（已有 ? 但没看到重试逻辑）
2. 兜底大纲从「空壳」升级为「基于前文自动推断」
3. chapter_outline 必填字段强制检查：summary/hook/characters/target_words 缺一则拒绝生成

---

### 🟡 P1-6: API 并发锁过于简陋

**位置**: `engine.py:694-716`

**问题**:
```python
lock_file = os.path.join(novel_dir, f".generating_{chapter_num:04d}.lock")
if os.path.exists(lock_file):
    # 检查是否过期（300s）
```
- 文件锁在 Windows 上不可靠（文件系统缓存延迟）
- 300s 超时太长——DeepSeek 一篇 3000 字章节通常 60-90 秒
- 没有主动释放锁的机制（`finally` 里没删锁文件！）

**修复方案**:
1. 改为 `threading.Lock()` 内存锁（同进程内，不需要文件）
2. 跨进程场景用 `portalocker` 或 SQLite 文件锁
3. `finally` 中删除锁文件

---

## 四、P2 — 优化项（两周内完成）

### 🟢 P2-1: 缺乏 A/B 对比和质量反馈闭环
- 用户不能说"这一章比上一章差"——系统没有收集偏好数据
- 建议：每章加 👍👎 按钮 + 反馈文本框，数据回传训练偏好模型

### 🟢 P2-2: 跨章节一致性跟踪缺失
- 第5章主角左臂受伤，第6章 LLM 可能写他"双手持剑"
- ConsistencyValidator 存在但从未在 generate_chapter_stream 中调用！
- 建议：每章生成后跑 ConsistencyValidator，发现不一致自动注入修正指令

### 🟢 P2-3: 番茄小说/阅文平台的格式适配
- 平台要求：标题格式、章节编号、敏感词过滤、字数控制
- 建议：输出格式化模块，一键导出平台兼容的 txt/html

### 🟢 P2-4: 生成速度可观测性
- 用户不知道生成进度（SSE 有 progress 事件但不够细）
- 建议：预估剩余时间、显示当前阶段、失败自动重试次数

### 🟢 P2-5: 小说数据备份/恢复
- 所有数据在本地 JSON 文件，无备份机制
- 建议：自动导出 ZIP 备份 + 云同步（WebDAV/OSS）

---

## 五、商用化路线图

### Phase 1 — 立即修复（48h）: P0-1 到 P0-4

### Phase 2 — 质量提升（1周）: P1-1 到 P1-6

### Phase 3 — 平台适配（2周）: P2-1 到 P2-5

### Phase 4 — 商用就绪（1月）
- 付费墙集成（微信支付/支付宝）
- 用户系统（注册/登录/书架）
- 内容审核接口（敏感词/政治/色情检测）
- SLA 监控（生成成功率、平均耗时、用户留存）
- CDN 部署 + 移动端优化

---

## 六、关键代码缺陷速查表

| 文件 | 行号 | 问题 |
|------|------|------|
| engine.py | 694-716 | 锁文件没有 finally 释放 |
| engine.py | 821 | PacingChecker 阈值不合理 |
| engine.py | 862-885 | Humanizer 只在质量不合格时才触发 |
| writer.py | 473 | 跳过 polish 的条件太宽松 |
| writer.py | 511-515 | Humanizer 跳过阈值 50 太低 |
| writer.py | 610 | 钩子检测只靠关键词匹配 |
| shared_memory.py | 706-718 | 元指令仍用自然语言模板 |
| shared_memory.py | 652-658 | 角色状态只取 protagonist_state |
| shared_memory.py | 464-767 | 没有 prompt token 预算管理 |
| planner.py | 全文 | 缺少 JSON schema 强校验 + 重试 |
| atomic_writer.py | 24-68 | ATOMIC_WRITER_SYSTEM 缺少最新的防废话规则 |

---

*本报告基于 2026-07-28 代码库。每个修复项应附带测试用例。*
