# 📖 NovelGenerator v2.3 — AI 小说生成器

> 输入灵感，AI 自动完成世界观搭建、角色设计、分卷大纲和逐章正文创作。  
> **单文件 PWA**，浏览器打开即用。支持 iOS/Android 添加到主屏幕，原生 App 体验。  
> v2.3 核心升级：**原子化逐beat生成** · **剧情图谱可视化** · **闭环干预反馈** · **活人感增强**

---

## ✨ 核心能力

### 创作管线

| 阶段 | 功能 | 特点 |
|:---|:---|:---|
| **灵感 → 设定** | AI 自动生成世界观、势力分布、力量体系、角色宝典、分卷大纲 | 三幕式结构，可手动编辑 |
| **大纲迭代** | 自然语言反馈 → LLM 深度语义拆解 → 精准修改 | 支持"加感情线/调节奏/改主角性格"等模糊指令 |
| **原子化写作 (v2.3)** | 章节拆解为 5-7 个独立 beat → 逐 beat 独立 LLM 调用 → 装配 | 每个 beat 独立 temperature，候选池随机选，多样性 10^14/章 |
| **标准写作** | SSE 流式输出，实时打字机效果 | 去 AI 味后处理，42 条硬规则 |
| **批量生成** | 一键生成多章 | 可中断，已生成章节自动保存 |
| **质量评估 (v2.3)** | 8 维自动评分 + A/B 对比 + HTML 报告 | 词汇多样性/语义多样性/钩子强度/爽点密度/连贯性/去AI味 |

### v2.3 剧情图谱系统

**三层记忆架构**，剧情数据可视化 + 反哺写作：

| 模块 | 功能 | 特点 |
|:---|:---|:---|
| **StoryGraph** | 剧情线追踪 · 伏笔全生命周期 · 角色实时快照 · 因果链 | 每章自动更新，LLM 提取 |
| **ArcPlanner** | 三幕式弧规划 · 自动分组 · 弧上下文注入 | 高潮检测 + 反转建议 |
| **AutoCalibrator** | 每10章自动校准 · 偏移检测 · 超期伏笔 · 角色一致性 | 健康度评分 |
| **图谱可视化** | 5 子tab（概览/剧情线/伏笔/角色/校准）· 可展开 · 筛选 · 时间线 | 章节滑块回溯历史状态 |
| **闭环干预 (v2.3.1)** | 7 种自动检测 → must/should/suggest 指令 → 注入 Writer 上下文 | 伏笔到期/剧情线休眠/角色缺席/情绪同质化/弧过渡 |
| **活人感增强 (v2.3.1)** | 感官细节词库 (5类30条) · 不完美行为库 · 对话质感 · 角色口癖 | 规则驱动，随机注入 |

### 智能体系统

23 个专业 Agent 协同工作：

**创作核心**
- `Planner` — 世界观 / 角色宝典 / 三幕式大纲生成
- `Writer` — 两遍式章节生成 + 42 条去 AI 味规则 + 三态情感弧线
- `BeatDecomposer` (v2.3) — 章节 → 5-7 个独立 beat 拆解，4 种序列变体
- `AtomicWriter` (v2.3) — 逐 beat 独立 LLM 调用，候选池随机选
- `BeatAssembler` (v2.3) — 去重 + 平滑过渡 + 一致性检查 + 评估
- `Embellisher` — 文学润色（描写增强、对话增色）

**剧情连贯性 (v2.3)**
- `StoryGraph` — 剧情图谱系统（线程/伏笔/角色/因果链）
- `ArcPlanner` — 弧规划器（三幕弧自动分组 + 高潮检测）
- `AutoCalibrator` — 自动校准器（每10章偏移检测 + 健康度评分）
- `StoryGraphInterventions` (v2.3.1) — 闭环干预（7 种检测 → Writer 上下文注入）
- `LogicSupervisor` — 12 类全维度逻辑监督（时间线/空间/实力/行为/物品/因果…）

**质量控制**
- `ConsistencyValidator` — 跨章节一致性校验
- `PacingChecker` — 节奏分析（情绪弧线 / 句长分布）
- `OpeningOptimizer` — 开篇优化（黄金 300 字分析 + 替代方案）
- `TwistDesigner` — 转折设计（全局 + 单章）
- `EvaluationSystem` (v2.3) — 8 维自动评估 + A/B 对比报告

**v2.1 反馈系统**
- `FeedbackDecomposer` — 自然语言意见 → 精确可执行指令
- `OutlineInteractive` — 交互式大纲迭代引擎

**v2.2 需求管理**
- `RequirementDecomposer` — 灵感 → 结构化子任务列表
- `RequirementSupervisor` — 逐条监督 + 量化评分

**辅助系统**
- `ContextUpdater` — 全局状态快照（角色位置/力量/关系）
- `ForeshadowingDesigner` — 伏笔追踪 + 回收计划
- `ChapterSummarizer` — 渐进式摘要压缩（应对上下文窗口）
- `Humanizer` — 24 种 AI 写作痕迹检测 + 自动去痕

### 记忆管理

`SharedMemoryManager` 统一管理 7 种持久化文件：

| 文件 | 内容 | 缓存策略 |
|:---|:---|:---|
| `plan.json` | 世界观 / 角色 / 大纲（Soul） | 30s TTL + 乐观锁 |
| `state.json` | 写作进度 / 已完成章节 | 2s TTL（分级） |
| `storygraph.json` (v2.3) | 剧情线 / 伏笔账本 / 角色快照 / 因果链 | 按需读写 |
| `arcplans.json` (v2.3) | 弧规划数据 | 按需读写 |
| `global_state.json` | 角色状态快照 | 30s TTL + 乐观锁 |
| `character_bible.json` | 人物关系图谱 | 读时缓存 |
| `chapters/*.md` | 章节正文 | 按需读取 |

### 风格系统

**38 种内置风格**，分为五大类：
- **18 男频经典**：热血爽文、轻松搞笑、黑暗深沉、快节奏打脸、系统流爽文、悬疑烧脑，以及唐三/土豆/辰东/猫腻/烽火/肘子/乌贼/老鹰/宅猪/远瞳等名作者风格
- **10 女频经典**：甜宠言情、古风言情、女强爽文、虐恋深情、校园青春、悬疑爱情、宅斗宫斗，以及顾漫/墨香铜臭/priest/丁墨/Twentine 等名作者风格
- **5 大众题材**：悬疑推理、科幻末世、历史权谋、都市生活、温馨治愈
- **参数化自定义**：6 维参数调节
- **自由描述**：直接输入文笔风格描述，支持保存为风格种子

---

## 🚀 快速开始

### 1. 环境要求

- Python ≥ 3.10
- DeepSeek API Key（[免费获取](https://platform.deepseek.com)）

### 2. 安装

```bash
git clone https://github.com/Kotabo-Zhao/NovelGenerator.git
cd NovelGenerator/backend
pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
```

编辑 `.env`：
```env
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_MODEL=deepseek-chat
HOST=0.0.0.0
PORT=8000
```

### 4. 启动

```bash
cd backend
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://localhost:8000`。

---

## 📝 使用流程

### 创作

1. **输入灵感** — 选题材、风格、目标字数、核心灵感
2. **AI 生成设定** — 世界观 + 角色宝典 + 分卷大纲（可手动编辑）
3. **大纲迭代** — 自然语言提意见，AI 深度分析后精确修改
4. **确认并开始写作** — 进入写作界面

### 写作 (v2.3)

- **原子化模式** — 默认开启，逐 beat 独立生成，随机性 10^14/章
- **标准模式** — 传统单次生成，可随时切换
- **批量生成** — 设置起止章节 → 自动连续生成（可中断）
- **意见重写** — 输入修改意见 → 按意见重新生成

### 剧情图谱 (v2.3)

- **5 子tab** — 概览/剧情线/伏笔/角色/校准
- **章节回溯** — 拖动滑块查看任意时间点的剧情状态
- **展开交互** — 点击卡片查看完整描述、时间线、关系变化
- **状态筛选** — 按进行中/已完结/超期等过滤
- **自动刷新** — 每章生成后自动更新图谱数据

### 导出

- 单本导出 TXT / PDF
- 批量导出（勾选多本）

---

## 🏗️ 架构

```
┌──────────────────────────────────────────────────────┐
│                     Frontend (Vue 3 SPA)              │
│  web/index.html  ·  vue.global.prod.js (self-hosted)  │
│  sw.js (PWA)  ·  manifest.json                        │
│                                                       │
│  Views: 书架 · 新建 · 写作(✍️/🔍双tab) · 导出          │
│  Writing: 原子化开关 · 剧情图谱5子tab · 章节滑块        │
└─────────────────────┬────────────────────────────────┘
                      │ HTTP / SSE
┌─────────────────────▼────────────────────────────────┐
│                  FastAPI Server                       │
│  backend/api/server.py  (~55 endpoints)               │
└─────────────────────┬────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────┐
│                 NovelEngine (v2.3)                     │
│                                                       │
│  ┌──────────┐  ┌──────────────┐  ┌─────────────────┐ │
│  │ Planner  │→ │ BeatDecomp.  │→ │ AtomicWriter    │ │
│  │ 世界观   │  │ 章节→5-7beat │  │ 逐beat独立LLM   │ │
│  │ 角色宝典 │  └──────────────┘  │ +候选池随机选   │ │
│  │ 分卷大纲 │                    └────────┬────────┘ │
│  └──────────┘                             │          │
│                          ┌────────────────▼────────┐ │
│  ┌──────────────┐       │ BeatAssembler            │ │
│  │ StoryGraph   │       │ 去重·平滑·一致性·评估    │ │
│  │ 剧情图谱     │       └─────────────────────────┘ │
│  │ ArcPlanner   │                                    │
│  │ AutoCalib.   │  ┌──────────────────────────────┐ │
│  │ Interventions│→ │ Writer (标准模式 · 42条规则)  │ │
│  └──────────────┘  └──────────────────────────────┘ │
│                                                       │
│  ┌──────────────────────────────────────────────────┐│
│  │ EvaluationSystem · 8维评估 · A/B对比 · HTML报告   ││
│  └──────────────────────────────────────────────────┘│
│                                                       │
│  辅助: Humanizer · ConsistencyValidator ·             │
│        ChapterSummarizer · Embellisher ·              │
│        ContextUpdater · TwistDesigner                 │
└─────────────────────┬────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────┐
│                 DeepSeek API                          │
│          (deepseek-chat / deepseek-v4-flash)          │
└──────────────────────────────────────────────────────┘
```

---

## 📂 项目结构

```
NovelGenerator/
├── backend/
│   ├── api/
│   │   └── server.py                     # FastAPI (~55 endpoints, SSE)
│   ├── core/
│   │   ├── engine.py                     # 创作管线编排器
│   │   ├── planner.py                    # 世界观/角色宝典/三幕式大纲
│   │   ├── writer.py                     # 两遍式章节生成 + 去AI味规则
│   │   ├── beat_decomposer.py  (v2.3)    # 章节→beat拆解 (10种模板·4种变体)
│   │   ├── atomic_writer.py    (v2.3)    # 逐beat独立LLM (候选池·最小上下文)
│   │   ├── beat_assembler.py   (v2.3)    # 去重·平滑·一致性·评估
│   │   ├── evaluation_system.py(v2.3)    # 8维评估 + A/B对比 + HTML报告
│   │   ├── storygraph.py       (v2.3)    # 剧情图谱 (线程/伏笔/角色/因果链)
│   │   ├── arcplanner.py       (v2.3)    # 三幕弧自动分组 + 高潮检测
│   │   ├── autocalibrator.py   (v2.3)    # 每10章偏移检测 + 健康度
│   │   ├── storygraph_interventions.py   # 闭环干预 (7种检测→Writer注入)
│   │   ├── logic_supervisor.py (v2.3)    # 12类全维度逻辑监督
│   │   ├── shared_memory.py              # 统一记忆管理层
│   │   ├── feedback_decomposer.py        # 反馈语义拆解 Agent
│   │   ├── outline_interactive.py        # 交互式大纲迭代引擎
│   │   ├── requirement_decomposer.py     # 需求拆解 Agent
│   │   ├── requirement_supervisor.py     # 需求监督 Agent
│   │   ├── consistency_validator.py      # 跨章一致性校验
│   │   ├── pacing_checker.py             # 节奏分析
│   │   ├── opening_optimizer.py          # 开篇优化
│   │   ├── twist_designer.py             # 转折设计
│   │   ├── context_updater.py            # 全局状态快照
│   │   ├── foreshadowing_designer.py     # 伏笔追踪
│   │   ├── chapter_summarizer.py         # 渐进式摘要压缩
│   │   ├── embellisher.py                # 文学润色
│   │   ├── humanizer.py                  # 24种AI写作痕迹检测
│   │   ├── ai_detector.py                # AI味检测器
│   │   ├── styles.py                     # 38种写作风格引擎
│   │   ├── style_fingerprint.py          # 风格指纹分析
│   │   ├── writing_examples.py           # 写作示例库
│   │   └── atomic_io.py                  # 原子化文件IO
│   ├── config.py                         # 全局配置
│   └── requirements.txt
├── web/
│   ├── index.html                        # Vue 3 SPA (单文件, ~115KB)
│   ├── vue.global.prod.js (v3.5.13)     # 自托管
│   ├── sw.js (v4)                        # Service Worker (PWA)
│   └── manifest.json                     # PWA 清单
├── novels/                               # 已创作小说存储
│   └── {书名}/
│       ├── plan.json                     # 世界观 + 角色 + 大纲
│       ├── state.json                    # 写作进度
│       ├── storygraph.json  (v2.3)       # 剧情图谱 (线程/伏笔/角色/因果链)
│       ├── arcplans.json     (v2.3)      # 弧规划
│       ├── calibration.json  (v2.3)      # 校准报告
│       ├── global_state.json             # 角色状态快照
│       ├── character_bible.json          # 人物关系图
│       └── chapters/                     # 章节正文 (*.md)
├── tests/
├── reports/                              # 分析报告
├── style_seeds/                          # 用户保存的风格种子
├── research/                             # 项目调研文档
└── .env.example
```

---

## 🔌 API 概览

### 创作 API

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `POST` | `/api/novels/create-stream` | SSE 流式创建小说 |
| `GET` | `/api/novels` | 书架列表 |
| `GET` | `/api/novels/{id}` | 小说完整数据 |
| `PUT` | `/api/novels/{id}` | 保存编辑后的大纲 |
| `POST` | `/api/novels/generate` | SSE 流式生成单章（标准） |
| `POST` | `/api/novels/generate/atomic` | **SSE 流式生成单章（原子化 v2.3）** |
| `POST` | `/api/novels/{id}/generate/batch` | SSE 流式批量生成 |

### 剧情图谱 API (v2.3)

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `GET` | `/api/novels/{id}/storygraph` | 剧情图谱全景 + 统计 |
| `GET` | `/api/novels/{id}/storygraph?chapter=N` | **分章节回溯** |
| `GET` | `/api/novels/{id}/arcs` | 弧规划 + 当前弧位置 |
| `GET` | `/api/novels/{id}/calibration` | 最新校准报告 |

### 大纲交互 API (v2.1)

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `POST` | `/api/novels/{id}/interactive-outline` | 交互式大纲修改 |
| `POST` | `/api/novels/{id}/decompose-feedback` | 预览：拆解反馈 |
| `POST` | `/api/novels/{id}/chapter-feedback/{n}` | 章节反馈拆解 |

### 质量 API

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `POST` | `/api/novels/{id}/validate-outline` | 大纲完整性校验 |
| `POST` | `/api/novels/{id}/validate-chapter/{n}` | 单章质量校验 |
| `POST` | `/api/novels/{id}/logic-check/{n}` | 全维度逻辑检查 (v2.3) |
| `POST` | `/api/novels/{id}/logic-check-batch` | 批量L1扫描 (v2.3) |
| `POST` | `/api/novels/{id}/analyze-opening` | 开篇分析 |
| `POST` | `/api/novels/{id}/pacing-check/{n}` | 节奏分析 |

### 需求管理 API (v2.2)

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `POST` | `/api/novels/{id}/requirements/decompose` | 拆解需求为子任务 |
| `POST` | `/api/novels/{id}/requirements/supervise` | 监督执行 |
| `POST` | `/api/novels/{id}/requirements/verify-loop` | SSE 循环校验 |
| `POST` | `/api/requirements/preview-decompose` | 创建前预览拆解 |

### 风格 · 导出 · 辅助 API

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `GET` | `/api/styles` | 38 种风格库 |
| `POST` | `/api/styles/build-custom` | 构建自定义风格 |
| `GET` | `/api/novels/{id}/export?fmt=txt\|pdf` | 单本导出 |
| `POST` | `/api/novels/export/batch` | 批量导出 |
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/repair-states` | 修复所有小说状态 (v2.2.1) |

---

## 🔧 技术栈

| 层 | 技术 | 说明 |
|:---|:---|:---|
| 前端 | Vue 3.5.13 (CDN-free) | 单文件 SPA, ~115KB, 自托管 |
| PWA | Service Worker v4 + Manifest | 离线缓存 · 添加到主屏幕 |
| 后端 | Python FastAPI | ~55 端点 · SSE 流式响应 |
| LLM | DeepSeek Chat API | deepseek-chat / deepseek-v4-flash |
| 存储 | 文件系统 | JSON 元数据 + Markdown 正文 + 原子写入 |
| 部署 | Render / 本地 | render.yaml 一键部署 |

---

## 📊 v2.3 原子化生成 vs 传统生成

| 指标 | 传统生成 | 原子化生成 |
|:---|:---|:---|
| LLM 调用/章 | 1 次 | 5-7 次 |
| 随机性空间 | ~10^4 | ~10^14 |
| 评估体系 | 无 | 8 维自动评分 |
| 剧情图谱 | 无 | 实时追踪 + 可视化 |
| 闭环干预 | 无 | 7 种检测 → Writer 注入 |
| 活人感 | 仅去AI味规则 | 感官细节 + 不完美行为 + 口癖 |
| 章节回溯 | 不支持 | 拖动滑块查看历史状态 |

---

## 📄 License

MIT

---

## 🔗 相关项目

- [blader/humanizer](https://github.com/blader/humanizer) — AI 写作痕迹检测
- [Novelforge](https://github.com/CalWade/novelforge) — 多智能体小说流水线
- [Autonovel (NousResearch)](https://github.com/NousResearch/autonovel) — Hermes Agent 小说管线
- [Frankentext (ACL 2026)](https://arxiv.org/abs/2505.18128) — 碎片化生成提升多样性
- [DeepSeek API](https://platform.deepseek.com) — LLM 服务
