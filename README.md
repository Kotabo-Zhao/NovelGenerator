# 📖 NovelGenerator — AI 小说生成器

> 输入灵感，AI 自动完成世界观搭建、角色设计、分卷大纲和逐章正文创作。
> **单文件 PWA**，浏览器打开即用。支持 iOS/Android 添加到主屏幕，原生 App 体验。

---

## ✨ 核心能力

### 创作管线

| 阶段 | 功能 | 特点 |
|:---|:---|:---|
| **灵感 → 设定** | AI 自动生成世界观、势力分布、力量体系、角色宝典、分卷大纲 | 三幕式结构，可手动编辑 |
| **大纲迭代** | 自然语言反馈 → LLM 深度语义拆解 → 精准修改 | 支持"加感情线/调节奏/改主角性格"等模糊指令 |
| **逐章写作** | SSE 流式输出，实时打字机效果 | 去 AI 味后处理，24 种模式检测 |
| **批量生成** | 一键生成多章 | 可中断，已生成章节自动保存 |
| **质量监督** | 灵感 → 需求拆解 → 逐条监督 → 循环校验 | 可量化评分 |

### 智能体系统

17 个专业 Agent 协同工作：

**创作核心**
- `Planner` — 世界观 / 角色宝典 / 三幕式大纲生成
- `Writer` — 两遍式章节生成 + 42 条去 AI 味规则
- `Embellisher` — 文学润色（描写增强、对话增色）

**质量控制**
- `ConsistencyValidator` — 跨章节一致性校验
- `PacingChecker` — 节奏分析（情绪弧线 / 句长分布）
- `OpeningOptimizer` — 开篇优化（黄金 300 字分析 + 替代方案）
- `TwistDesigner` — 转折设计（全局 + 单章）

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

统一记忆层 `SharedMemoryManager`，管理 6 种持久化文件：

| 文件 | 内容 | 缓存策略 |
|:---|:---|:---|
| `plan.json` | 世界观 / 角色 / 大纲（Soul） | 30s TTL + 乐观锁 |
| `state.json` | 写作进度 / 已完成章节 | 30s TTL + 乐观锁 |
| `global_state.json` | 角色状态快照 | 30s TTL + 乐观锁 |
| `character_bible.json` | 人物关系图谱 | 读时缓存 |
| `foreshadowing.json` | 伏笔追踪表 | 读时缓存 |
| `chapters/*.md` | 章节正文 | 按需读取 |

特性：内存缓存减少 60-80% 磁盘 I/O、乐观锁防并发冲突、变化通知自动失效。

### 风格系统

**38 种内置风格**，分为五大类：

- **18 男频经典**：热血爽文、轻松搞笑、黑暗深沉、快节奏打脸、系统流爽文、悬疑烧脑，以及唐三/土豆/辰东/猫腻/烽火/肘子/乌贼/老鹰/宅猪/远瞳等名作者风格
- **10 女频经典**：甜宠言情、古风言情、女强爽文、虐恋深情、校园青春、悬疑爱情、宅斗宫斗，以及顾漫/墨香铜臭/priest/丁墨/Twentine 等名作者风格
- **5 大众题材**：悬疑推理、科幻末世、历史权谋、都市生活、温馨治愈
- **参数化自定义**：6 维参数调节（叙事人称 / 文风基调 / 节奏控制 / 对话比例 / 描写密度 / 情感倾向）
- **自由描述**：直接输入文笔风格描述，支持保存为风格种子

每个风格包含：文笔特征、语气基调、节奏控制、对话风格、结构偏好、少样本示例、标志句式、禁用写法。

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

### 5. iOS 添加到主屏幕

Safari 打开 → 点「分享」→「添加到主屏幕」→ 主屏幕出现 App 图标 → 全屏原生体验。

---

## 📝 使用流程

### 创作

1. **输入灵感** — 选题材（修仙/玄幻/都市…12 种）、风格（38 种或自定义）、目标字数、书名（可选）、核心灵感（几句话说清故事）
2. **AI 生成设定** — 世界观 + 主角卡（姓名/身份/金手指/背景故事）+ 分卷大纲（可手动编辑卷/章标题和摘要）
3. **大纲迭代** — 用自然语言提意见（"加感情线""节奏太慢加冲突戏""主角太弱"），AI 深度分析后精确修改
4. **确认并开始写作** — 进入写作界面

### 写作

- **逐章生成** — 点击章节序号 → 「生成第 N 章」→ 流式实时预览
- **批量生成** — 设置起止章节 → 「批量生成」→ 自动连续生成（可中断）
- **意见重写** — 对本章不满意？输入修改意见 → 按意见重新生成（大纲结构不变）
- **写作模式** — 网文模式 / 文学模式切换

### 质量保障

- **需求管理** — 拆解灵感为可量化子任务 → 监督执行 → 循环校验
- **大纲修改** — 随时用自然语言调整大纲

### 导出

- 单本导出 TXT / PDF
- 批量导出（勾选多本）

---

## 🏗️ 架构

```
┌─────────────────────────────────────────────────────┐
│                     Frontend                        │
│  web/index.html (Vue 3 SPA, 84KB single-file)      │
│  + manifest.json (PWA)  + sw.js (Service Worker)    │
│  + vue.global.prod.js (CDN-free self-hosted)        │
│                                                     │
│  Views: 书架 · 新建(创建/大纲) · 写作 · 导出          │
│  UI: macOS / iOS 双风格 · 深色/浅色双主题             │
└────────────────────┬────────────────────────────────┘
                     │ HTTP / SSE
┌────────────────────▼────────────────────────────────┐
│                  FastAPI Server                      │
│  backend/api/server.py  (~830 lines, 46 endpoints)   │
│                                                     │
│  Serves: 前端 HTML + 静态资源 + REST API + SSE 流     │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│                 NovelEngine                          │
│  backend/core/engine.py  (创作管线编排器)             │
│                                                     │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐ │
│  │ Planner  │→ │  Writer  │→ │ Consistency       │ │
│  │ 世界观   │  │  章节生成 │  │ Validator 一致性   │ │
│  │ 角色宝典 │  │  42条去  │  └───────────────────┘ │
│  │ 分卷大纲 │  │  AI味规则│  ┌───────────────────┐ │
│  └──────────┘  └──────────┘→ │ Opening Optimizer │ │
│                               │ 开篇优化          │ │
│  ┌──────────────────────┐    └───────────────────┘ │
│  │ FeedbackDecomposer   │    ┌───────────────────┐ │
│  │ + OutlineInteractive │→   │ Twist Designer    │ │
│  │ v2.1 交互式大纲迭代   │    │ 转折设计          │ │
│  └──────────────────────┘    └───────────────────┘ │
│                                                     │
│  ┌──────────────────────┐  ┌──────────────────────┐ │
│  │ RequirementDecomposer│  │ SharedMemoryManager  │ │
│  │ + RequirementSup.    │  │ 6-file unified cache │ │
│  │ v2.2 需求管理        │  │ 30s TTL + 乐观锁     │ │
│  └──────────────────────┘  └──────────────────────┘ │
│                                                     │
│  辅助 Agent: Foreshadowing · ContextUpdater ·       │
│             PacingChecker · Humanizer ·             │
│             ChapterSummarizer · Embellisher         │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│                 DeepSeek API                         │
│            (deepseek-chat / deepseek-v4-flash)       │
└─────────────────────────────────────────────────────┘
```

---

## 📂 项目结构

```
NovelGenerator/
├── backend/
│   ├── api/
│   │   └── server.py              # FastAPI 服务器 (46 endpoints, SSE streaming)
│   ├── core/
│   │   ├── engine.py              # 创作管线编排器
│   │   ├── planner.py             # 世界观/角色宝典/三幕式大纲
│   │   ├── writer.py              # 两遍式章节生成 + 去AI味规则
│   │   ├── shared_memory.py       # 统一记忆管理层 (缓存+乐观锁)
│   │   ├── memory.py              # 旧版记忆接口 (向后兼容)
│   │   ├── styles.py              # 38种写作风格引擎
│   │   ├── humanizer.py           # 24种AI写作痕迹检测+去痕
│   │   ├── feedback_decomposer.py # 反馈语义拆解 Agent (v2.1)
│   │   ├── outline_interactive.py # 交互式大纲迭代引擎 (v2.1)
│   │   ├── requirement_decomposer.py # 需求拆解 Agent (v2.2)
│   │   ├── requirement_supervisor.py # 需求监督 Agent (v2.2)
│   │   ├── consistency_validator.py  # 跨章一致性校验
│   │   ├── pacing_checker.py      # 节奏分析
│   │   ├── opening_optimizer.py   # 开篇优化
│   │   ├── twist_designer.py      # 转折设计
│   │   ├── context_updater.py     # 全局状态快照
│   │   ├── foreshadowing_designer.py # 伏笔追踪
│   │   ├── chapter_summarizer.py  # 渐进式摘要压缩
│   │   ├── embellisher.py         # 文学润色
│   │   ├── style_fingerprint.py   # 风格指纹分析
│   │   ├── writing_examples.py    # 写作示例库
│   │   ├── ai_detector.py         # AI味检测器
│   │   └── atomic_io.py           # 原子化文件IO
│   ├── config.py                  # 全局配置
│   ├── requirements.txt
│   └── templates/                 # 导出模板
├── web/
│   ├── index.html                 # Vue 3 SPA (单文件, 84KB)
│   ├── vue.global.prod.js         # Vue 3 生产版 (自托管)
│   ├── sw.js                      # Service Worker (PWA)
│   └── manifest.json              # PWA 清单
├── novels/                        # 已创作小说存储
│   └── {书名}/
│       ├── plan.json              # 世界观 + 角色 + 大纲
│       ├── state.json             # 写作进度
│       ├── global_state.json      # 角色状态快照
│       ├── character_bible.json   # 人物关系图
│       ├── foreshadowing.json     # 伏笔表
│       └── chapters/              # 章节正文 (*.md)
├── tests/                         # 测试套件
├── style_seeds/                   # 用户保存的风格种子
├── research/                      # 项目调研文档
├── render.yaml                    # Render 部署配置
└── .env.example
```

---

## 🔌 API 概览

### 创作 API

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `POST` | `/api/novels/create-stream` | SSE 流式创建小说（灵感→大纲） |
| `GET` | `/api/novels` | 书架列表 |
| `GET` | `/api/novels/{novel_id}` | 小说完整数据 |
| `PUT` | `/api/novels/{novel_id}` | 保存编辑后的大纲 |
| `POST` | `/api/novels/generate` | SSE 流式生成单章 |
| `POST` | `/api/novels/{novel_id}/generate/batch` | SSE 流式批量生成 |

### 大纲交互 API (v2.1)

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `POST` | `/api/novels/{novel_id}/interactive-outline` | 交互式大纲修改（自然语言反馈→精确修改） |
| `POST` | `/api/novels/{novel_id}/decompose-feedback` | 预览：拆解反馈（不执行修改） |
| `POST` | `/api/novels/{novel_id}/chapter-feedback/{n}` | 章节级别反馈拆解 |

### 质量 API

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `POST` | `/api/novels/{novel_id}/validate-outline` | 大纲完整性校验 |
| `POST` | `/api/novels/{novel_id}/validate-chapter/{n}` | 单章质量校验 |
| `POST` | `/api/novels/{novel_id}/analyze-opening` | 开篇分析（黄金300字） |
| `POST` | `/api/novels/{novel_id}/opening-alternatives` | 开篇替代方案 |
| `POST` | `/api/novels/{novel_id}/pacing-check/{n}` | 节奏分析 |
| `GET` | `/api/novels/{novel_id}/design-twists` | 全局转折设计 |
| `POST` | `/api/novels/{novel_id}/design-chapter-twist` | 单章转折设计 |

### 需求管理 API (v2.2)

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `POST` | `/api/novels/{novel_id}/requirements/decompose` | 拆解需求为子任务 |
| `POST` | `/api/novels/{novel_id}/requirements/update` | 追加新需求 |
| `GET` | `/api/novels/{novel_id}/requirements` | 获取需求状态 |
| `POST` | `/api/novels/{novel_id}/requirements/supervise` | 监督执行 |
| `POST` | `/api/novels/{novel_id}/requirements/verify-loop` | SSE 循环校验 |

### 风格 API

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `GET` | `/api/styles` | 获取 38 种风格库 |
| `GET` | `/api/styles/params` | 获取参数化风格配置 |
| `POST` | `/api/styles/build-custom` | 构建自定义参数化风格 |
| `GET` | `/api/styles/seeds` | 获取已保存的风格种子 |
| `POST` | `/api/styles/seeds` | 保存风格种子 |
| `DELETE` | `/api/styles/seeds/{name}` | 删除风格种子 |
| `POST` | `/api/styles/fingerprint` | 风格指纹分析 |
| `POST` | `/api/styles/compare` | 风格对比 |

### 导出 API

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `GET` | `/api/novels/{novel_id}/export?fmt=txt\|pdf` | 单本导出 |
| `POST` | `/api/novels/export/batch` | 批量导出 |

### 辅助 API

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| `GET` | `/api/novels/{novel_id}/character-bible` | 生成人物宝典 |
| `POST` | `/api/novels/{novel_id}/summarize` | 触发摘要压缩 |
| `GET` | `/api/novels/{novel_id}/token-budget` | Token 预算查询 |
| `POST` | `/api/novels/{novel_id}/sync-state` | 同步写作状态 |
| `GET` | `/api/health` | 健康检查 |

---

## 🔧 技术栈

| 层 | 技术 | 说明 |
|:---|:---|:---|
| 前端 | Vue 3 (CDN-free) | 单文件 SPA，84KB，自托管 |
| PWA | Service Worker + Manifest | 离线缓存，添加到主屏幕 |
| 后端 | Python FastAPI | 46 个端点，SSE 流式响应 |
| LLM | DeepSeek Chat API | 支持 deepseek-chat / deepseek-v4-flash |
| 存储 | 文件系统 | JSON 元数据 + Markdown 正文 + 原子写入 |
| 部署 | Render / 本地 | render.yaml 一键部署 |

---

## ☁️ 部署

### Render（推荐）

项目根目录包含 `render.yaml`，支持一键部署：

```bash
# 在 Render Dashboard 中 New Web Service → Deploy from Git
# 或使用 Render CLI
render blueprint apply
```

配置项：
- **Runtime**: Python
- **Build**: `pip install -r requirements.txt`
- **Start**: `cd backend && uvicorn api.server:app --host 0.0.0.0 --port $PORT`
- **Disk**: 1GB 持久化存储（挂载到 `/var/data/novels`）

环境变量：
```env
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_MODEL=deepseek-chat
HOST=0.0.0.0
NOVELS_DIR=/var/data/novels
```

### Docker（可选）

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "backend.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 📊 写作质量体系

### 去 AI 味（42 条规则）

Writer 内置 42 条硬规则，涵盖：
- **节奏控制**：句长变化强制、段落参差、短句爆点
- **禁用句式**：二元对比壳、伪洞察标记、讲义冒号、空泛总结句、抽象压力句
- **写作质感**：具象优先、对话标注克制、破折号限用、模糊词禁用、AI 过渡词禁用
- **叙事技巧**：三态情感弧线（压抑→释放→余韵）

### Humanizer 后处理（24 种模式）

基于 `blader/humanizer` (7200★) 中文适配版：
- **内容类**（6 种）：意义夸大、靠名头抬高、肤浅分析、空洞价值、虚假紧迫、套话连篇
- **结构类**（6 种）：冒号列举、三段式、总结升华、二元对立、用典过度、首尾呼应套
- **语言类**（6 种）：静词膨胀、逻辑跳跃词、论文腔、模糊量化、被动语态堆砌、冗余修饰
- **格式类**（6 种）：AI 分段模式、过度标题化、强行加粗、列表狂魔、引用狂魔、emoji 滥用

---

## 🎨 创作特性

### 三幕式结构

每部小说自动遵循经典三幕结构：
- **第一幕·建置**（前 25%）：日常世界 → 核心冲突 → 不可逆选择
- **第二幕·对抗**（中 50%）：学习成长 → 中点转折 → 最低谷 → 顿悟
- **第三幕·解决**（后 25%）：集结力量 → 决战 → 新平衡

### 伏笔管理

`ForeshadowingDesigner` 自动：
- 在章节写作中埋设伏笔
- 追踪所有伏笔状态（已埋 / 已揭示 / 已回收）
- 规划回收时机和方式

### 人物宝典

`character_bible.json` 记录：
- 人物关系图谱（亲密度 / 冲突度 / 权力差）
- 角色弧追踪（初始状态 → 成长轨迹 → 最终状态）
- 出场记录和关键决策

---

## 📄 License

MIT

---

## 🔗 相关项目

- [blader/humanizer](https://github.com/blader/humanizer) — AI 写作痕迹检测
- [AI_Gen_Novel](https://github.com/cs2764/AI_Gen_Novel) — 中文 AI 小说生成参考
- [DeepSeek API](https://platform.deepseek.com) — LLM 服务
