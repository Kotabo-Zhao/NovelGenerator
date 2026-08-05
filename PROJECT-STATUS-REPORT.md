# NovelGenerator 项目状态报告

> 报告日期：2026-08-05
> 项目路径：`C:\Users\Yan Zhao\Codex\NovelGenerator`
> 远程仓库：https://github.com/Kotabo-Zhao/NovelGenerator.git（分支 `main`，另有 `android-port`）
> 当前版本：v3.7（互动角色属性数值系统，2026-08-05 提交）

---

## 一、项目概览

**定位**：输入灵感，AI 自动完成世界观搭建、角色设计、分卷大纲和逐章正文创作的**小说生成器**。单文件 PWA，浏览器即用，支持 iOS/Android 打包为原生 App。

**技术栈**：
| 层 | 技术 |
|---|---|
| 前端 | Vue3（单文件 `index.html` 311KB + `vue.global.prod.js`，PWA + Service Worker） |
| 后端 | Python ≥3.10 + FastAPI（`backend/`，81 个 .py） |
| 生成模型 | DeepSeek（`.env` 已配置 API Key / Model / Base URL / NOVELS_DIR） |
| 打包 | Android 壳（PWA→APK）+ PyInstaller（`NovelGenerator.exe` 未迁移） |
| 附加 | 小红书发布管线（`Dockerfile.xhs` / `docker-compose.xhs.yml`） |

---

## 二、开发状态与版本演进

近期开发非常活跃（8/4 ~ 8/5 连续提交），版本从 v2.3 一路推进到 v3.10：

| 版本 | 日期 | 关键内容 |
|---|---|---|
| v2.3 | — | 原子化逐 beat 生成、剧情图谱可视化、闭环干预、活人感增强 |
| v3.6.3 | 08-04 | P0 确认流修复、atomic_write Windows 重试 |
| v3.6.4 | 08-04 | 行动按钮化（方案C）——零 LLM 意图识别 |
| v3.6.5 | 08-04 | 时间连续性——LLM 生成不再时间错乱 |
| v3.6.6 | 08-04 | 互动与写作模式分离 + 互动导出小说 + atomic 流式修复 |
| v2.5.67 | 08-04 | Android 前端打字机流式修复 + pip 清华镜像 |
| **v3.7** | **08-05** | **互动角色属性数值系统**：5 维属性（力量/敏捷/智力/魅力/体魄 1-95）+ 规则推断 + 对话 prompt 注入属性卡（新存档/老存档兜底） |
| v3.9 | 08-05 | 行动按钮条（方案C 升级）——点击即意图，talk 内联输入，零 LLM 识别 |
| **v3.10** | **08-05** | **大纲合规校验**：生成后 L1 规则 + L2 LLM 精判核对核心事件/节拍/角色落地，分级 ok/partial/fail，fail 自动补写缺失核心事件，前端合规徽章 |

**分支**：`main`（当前）+ `android-port`（Android 移植线，远程已同步）。

---

## 三、核心能力体系

### 1. 创作管线（6 阶段）
灵感 → 设定（世界观/势力/力量体系/角色宝典/分卷大纲）→ 大纲迭代（自然语言反馈语义拆解）→ 原子化写作（章节拆 5-7 beat，逐 beat 独立 LLM 调用，多样性 10^14/章）→ 标准写作（SSE 流式打字机 + 42 条去 AI 味硬规则）→ 8 维质量评估（A/B 对比 + HTML 报告）。

### 2. 智能体系统（23+ 专业 Agent）
- **创作核心**：Planner / Writer（两遍式 + 三态情感弧线）/ BeatDecomposer / AtomicWriter / BeatAssembler / Embellisher
- **剧情连贯**：StoryGraph / ArcPlanner / AutoCalibrator / StoryGraphInterventions（7 种自动检测闭环干预）/ LogicSupervisor（12 类逻辑监督）
- **质量控制**：ConsistencyValidator / PacingChecker / OpeningOptimizer / TwistDesigner / EvaluationSystem
- **反馈与需求**：FeedbackDecomposer / OutlineInteractive / RequirementDecomposer / RequirementSupervisor
- **辅助**：ContextUpdater / ForeshadowingDesigner / ChapterSummarizer / Humanizer（24 种 AI 痕迹检测）

### 3. 剧情图谱系统（三层记忆架构）
StoryGraph（剧情线/伏笔/角色快照/因果链）→ ArcPlanner（三幕弧规划/高潮检测/反转建议）→ AutoCalibrator（每 10 章自动校准/健康度评分）。5 子 tab 可视化，章节滑块回溯历史状态。

### 4. 记忆管理（SharedMemoryManager，7 种持久化文件）
`plan.json`（世界观/大纲，30s TTL + 乐观锁）、`state.json`（进度）、`storygraph.json`、`arcplans.json`、`global_state.json`（角色状态）、`character_bible.json`（人物关系）、`chapters/*.md`（正文）。

### 5. 风格系统
38 种内置风格：18 男频（含唐三/土豆/辰东/猫腻等名作者风格）、10 女频（含顾漫/墨香铜臭/priest 等）、5 大众题材、6 维参数化自定义、自由描述保存风格种子。

### 6. 互动小说体系（v3.6 ~ v3.7 主攻方向）
互动与写作双模式分离、行动按钮化（免 LLM 意图识别）、时间连续性修复、**角色 5 维属性数值系统（v3.7 最新）**、互动存档/导出全书（`interactive_export/`）、AI 语音（emotion_tts/voice_director）、情绪服务（emotion_server）。

---

## 四、代码与测试状态

- **backend/core/**：43 个模块（engine、writer、planner、storygraph、memory、humanizer、epub_exporter、xiaohongshu 等）
- **backend/core/interactive/**：11 个模块（attr_system、action_engine、dialogue_engine、world_state、story_director 等）
- **backend/api/**：FastAPI 服务（server + routers，含 interactive 路由）
- **tests/**：26 个测试/诊断脚本（含 e2e、stress、health_check、attr_system 单测等）
- **web/**：单文件 PWA 架构（`web/src/` 为空属正常，前端集中在 index.html）

---

## 五、内容资产（novels/，共 16 个目录）

| 小说 | 章节数 | 备份数 | 状态 |
|---|---|---|---|
| 熵破苍穹：轮回者的解剖刀 | 11 | 1 | 最完整作品，含互动存档 |
| 替身的告别 | 6 | 133 | 互动模式产物，备份堆积 |
| 万界篡改者 | 4 | 1 | 进行中 |
| 天命骗局：我在诸天当神棍 | 3 | 1 | 进行中 |
| 总裁的追妻火葬场：从民政局到民政局 | 1 | 26 | 草稿 |
| 龙门风沙录 | 1 | 0 | 草稿 |
| 删除测试_全链 | 1 | 0 | 测试目录 |
| 其余 8 个（novel1、测试手册、她与星光同路、概率论与火葬场、设计人生 等） | 0 | 0 | 空/测试目录 |

**质量抽样**（熵破苍穹 第1章）：文笔自然、细节有质感（"灰尘被烘烤后残留的温度"），无明显 AI 味，达到可发布水准。

---

## 六、质量与商业化状态

依据 `AUDIT_REPORT.md`（2026-07-28，基于当时 ~12,000 行代码；现 31,790 行）：
- **审计结论**：基础架构扎实，商用化仍有差距
- **发现**：4 个 P0 致命缺陷、6 个 P1 重要缺陷、5 个 P2 优化项
- **实测产出质量**：60-70 分（网文及格线）

### 审计缺陷修复状态（2026-08-05 逐项复核）

| 编号 | 缺陷 | 状态 | 说明 |
|---|---|---|---|
| P0-1 | 章末元指令泄漏 | ✅ 已根治 | Phase 2 已移除（v2.53）；writer/atomic_writer/blueprint 三处提取均剥离 `═══` 标记行；新增生成后指令泄漏检测（`_strip_instruction_leaks`，命中即清理并告警） |
| P0-2 | 质量门误杀率高 | ✅ 本轮修复 | 规则初筛 `score<40` 后新增 **LLM 复核**（确认才重写，LLM 判可接受即保留原文）；新增误杀率数据采集 `quality_gate_log.jsonl`（before/after 分数 + 是否改写） |
| P0-3 | 上下文窗口超载 | ✅ 已缓解 | 已有 9,000 字符上下文预算保护（尾部低优先级段截断，保证 ≥60% token 留给生成） |
| P0-4 | 对话密度恶性循环 | ✅ 本轮修复 | WRITER_SYSTEM 已有硬约束（对话占比 ≤35%、连续 ≤4 轮）；原 5 条冗余告警块瘦身为单行自适应提示，减少 prompt 膨胀 |
| P1-1 | Beat 分解器与 Writer 脱节 | ✅ 本轮修复 | 普通路径注入节拍骨架：`BeatDecomposer` 拆 5-7 beat → `_format_beats_instruction` 渲染后追加进 writer 上下文，单次生成也按节奏推进 |
| P1-2 | Humanizer 跳过阈值过低 | ✅ 已修复 | 阈值 50→70（v2.3.5） |
| P1-3 | 截断/钩子检测不完整 | ✅ 本轮修复 | 关键词列表收紧（移除「然后/但是/这时」等假阳性词）；新增 `Writer.assess_and_enhance_hook`（LLM 钩子强度评分 0-100，<35 分自动局部重写结尾 300 字） |
| P1-4 | 角色状态追踪形同虚设 | ✅ 本轮修复 | `_build_character_state_context` 新增活跃配角注入（最近 5 章出场角色的状态/位置/关系），不再只读 `protagonist_state` |
| P1-5 | Planner JSON 结构不稳定 | ✅ 基本修复 | Planner 已有 3 次重试 + 字段完整性校验（v3.5.24）；兜底大纲从「继续推进主线剧情发展」升级为基于上一章结尾自动推断 |
| P1-6 | API 并发锁简陋 | ✅ 已修复 | `finally` 中删除锁文件（`generation.py:662-665`） |
| P2-2 | 跨章节一致性跟踪缺失 | ✅ 已修复 | ConsistencyValidator 已接入生成管线（`generation.py:1096,1283`） |

> 复核日期：2026-08-05。已验证 `py_compile` + 离线单元测试全部通过（23 项 humanizer/章节摘要 + 9 项规则兜底）。

---

## 七、Git 状态

- **当前分支**：`main`，HEAD = `6860188`（v3.7）
- **未提交改动**：
  - 53 个 deleted：历史 apk 与 `build/`、`dist/` 产物（迁移时未复制，属预期）
  - 41 个 untracked：`outputs/`（语音/UI 截图）、`tmp/`（调试脚本）、`android/build_log.txt`、`consistency_out.txt` 等
- **仓库完整性**：`git fsck` 通过，远程 `ls-remote` 认证正常，可正常 push/pull

---

## 八、风险与待办清单

1. **P0/P1 缺陷复核**：元指令泄漏、质量门误杀等审计项需确认是否已修复
2. **备份文件清理**：`替身的告别` 133 个 `.bak`、`总裁的追妻火葬场` 26 个 `.bak`（atomic 写产生的历史备份，可保留最近几份后清理）
3. **测试/空目录清理**：`novel1`、`nonexistent`、`删除测试*`、`测试手册`、`_e2e_test_interactive` 等 8 个空目录
4. **密钥安全**：`.env` 含 DeepSeek API Key，已随项目迁移，注意勿提交/外泄（确认在 `.gitignore` 中）
5. **未提交工作**：`outputs/`、`tmp/` 等 41 个 untracked 文件建议及时提交或清理
6. **构建产物**：`NovelGenerator.exe`、历史 apk、gradle 分发包未迁移，如需打包 Android 需补迁或重新构建
7. **互动内容归档**：`替身的告别` 有完整互动存档（checkpoints/chat_logs/scene_logs），是互动功能的重要测试资产

---

## 九、结论

NovelGenerator 是一个**功能完整、架构成熟、开发活跃**的 AI 小说创作系统，已从"生成工具"演进为"互动小说引擎"（v3.7 属性数值系统）。代码、文档、测试、内容资产均已完整迁移到 Codex，git 历史与远程同步正常。当前主要风险集中在：审计缺陷的修复确认、备份/测试目录清理、未提交工作整理。