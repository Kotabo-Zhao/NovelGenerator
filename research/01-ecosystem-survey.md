# AI 小说生成器 — 开源生态调研报告

> **日期**: 2026-07-21 | **调研人**: 波比
> **目标**: 为新项目 NovelGenerator 做技术选型和架构设计

---

## 目录

1. [市场全景](#1-市场全景)
2. [顶级项目深度分析](#2-顶级项目深度分析)
3. [Skill 生态 (WorkBuddy/OpenClaw)](#3-skill-生态)
4. [技术方案对比](#4-技术方案对比)
5. [关键架构模式提取](#5-关键架构模式提取)
6. [推荐技术栈](#6-推荐技术栈)
7. [风险评估与建议](#7-风险评估与建议)

---

## 1. 市场全景

### 1.1 GitHub 星数排名 (2026-07)

| 排名 | 项目 | Stars | 语言 | 定位 |
|:---:|:---|:---:|:---|:---|
| 1 | **InkOS** (Narcooo) | **5,300+** | TypeScript | 10 Agent 全自动写作管线 |
| 2 | **oh-story-claudecode** | **3,212** | JavaScript | 网文 Skill 包 (Claude/OpenClaw) |
| 3 | **AI-Novel-Writing-Assistant** | **1,686** | TypeScript | AI Native 长篇生产系统 |
| 4 | **AI_Gen_Novel** | **~800** | Python | Gradio UI + 9 模型 Provider |
| 5 | **NovelClaw** | **347** | Python | 动态记忆优先 + RAG |
| 6 | **novel-writer** (AI-Practical-Lab) | **~300** | Python | YAML 角色追踪 + 快照回滚 |
| 7 | **NovelWriter** (Hurricane0698) | **~200** | Python+React | 世界模型 + Copilot 模式 |
| 8 | **NovelCraft** (Kesuek) | **~150** | Markdown | OpenClaw Skill + PDF/EPUB |
| 9 | **Million-Word-Novel** | **~100** | Python | LangChain + 分层记忆 |
| 10 | **LangGraph-Novel** | **~50** | Python | Multi-Agent + HITL |

### 1.2 热门程度趋势

- **InkOS** 7 周破 5,300 星 — 这个领域最高的增长速度
- **oh-story-claudecode** 2 月破 3,000 星 — Skill 模式的标杆
- 整个赛道 2025-2026 年爆发式增长，AI Agent 写作是当前最热方向

---

## 2. 顶级项目深度分析

### 2.1 InkOS — 工业化多 Agent 管线

| 维度 | 详情 |
|:---|:---|
| **核心理念** | 10 个专职 Agent 接力完成小说，人工审核门控 |
| **语言/框架** | TypeScript · pnpm monorepo · Vite+React+Hono · Commander.js |
| **记忆系统** | **三层**: ① 结构化 JSON 状态 (Zod schema) ② Markdown 投影 (Truth Files) ③ SQLite 时序记忆库 |
| **Agent 管线** | Radar→Planner→Composer→Architect→Writer→Observer→Reflector→Normalizer→Auditor→Reviser |
| **审计体系** | 37 维度检查 (角色一致性/伏笔闭合/资源追踪/情感弧线...) |
| **Truth Files** | 7 个文件: current_state / particle_ledger / pending_hooks / chapter_summaries / subplot_board / emotional_arcs / character_matrix |
| **模型支持** | 15+ Provider (Gemini/Moonshot/MiniMax/DeepSeek/智谱/百炼/火山/混元/文心/星火/OpenRouter/Ollama...) |
| **多模型路由** | 不同 Agent 可分配不同模型 (如 Claude 写作 + GPT-4o 审计) |
| **部署方式** | npm 全局安装 · Docker · Web UI (studio) · CLI · TUI · Service API |
| **许可证** | AGPL-3.0 |
| **关键创新** | ① 10 Agent 分工 ② 37 维审计自动修复循环 ③ 三层记忆 ④ 多模型路由 ⑤ Studio + CLI 双入口 |

**架构图逻辑**:
```
作者意图 → Planner(规划) → Composer(编排) → Writer(写作) 
→ Observer(提取事实) → Reflector(状态写入) → Normalizer(字数归一化)
→ Auditor(审计37维) → [不通过] → Reviser(修复) → Auditor(再审计)
→ [通过] → 下一章
```

### 2.2 oh-story-claudecode — 网文 Skill 生态标杆

| 维度 | 详情 |
|:---|:---|
| **核心理念** | "套路 = 确定性的情绪满足" — 方法论三步走 (扫榜→拆文→商业化写作) |
| **语言/框架** | JavaScript · Claude Code / OpenClaw Skill 格式 |
| **Skill 体系** | 10 个子 Skill: story-setup / story / story-long-write / story-long-analyze / story-long-scan / story-short-write / story-short-analyze / story-short-scan / story-deslop / story-import |
| **Agent 体系** | 7 Agent: story-architect / character-designer / narrative-writer / consistency-checker / story-researcher / story-explorer / chapter-extractor |
| **核心特色** | ① 扫榜分析 (起点/番茄/晋江趋势) ② 拆文模板化 ③ 去 AI 味体检 ④ 封面图生成 |
| **知识体系** | 100+ 份写作方法论 reference 文件 (大纲排布/开头设计/人物设计/爽点节奏...) |
| **文件结构** | 设定/ (世界观/角色/势力/关系) · 大纲/ (卷纲/细纲) · 正文/ · 对标/ · 追踪/ (上下文/伏笔/时间线/角色状态) |
| **许可证** | MIT |
| **关键创新** | ① 扫榜→拆文→写作全链路 ② 100+ 写作方法论 ③ 7 Agent 协作 ④ 对标书文风学习 |

### 2.3 AI_Gen_Novel — Gradio 全功能桌面应用

| 维度 | 详情 |
|:---|:---|
| **核心理念** | 一键从想法到完整小说的 Gradio WebUI |
| **语言/框架** | Python · Gradio 5.38.0 |
| **模型支持** | 9 Provider: OpenRouter/Claude/Gemini/DeepSeek/LM Studio/智谱/Fireworks/Grok/Lambda |
| **核心特性** | ① 断点续传 (.novel_save) ② RAG 风格学习 ③ Humanizer-zh 去 AI 味 (24 种模式) ④ 伏笔/反转生成 ⑤ WebUI 实时调参 ⑥ 多 Agent 协作 |
| **许可证** | MIT |
| **关键创新** | ① 断点续传机制 ② RAG 驱动的风格一致性 ③ 24 种 AI 写作痕迹检测 |

### 2.4 AI-Novel-Writing-Assistant — LangChain 整本生产链

| 维度 | 详情 |
|:---|:---|
| **核心理念** | AI 导演式长篇生产系统 (不是聊天壳子) |
| **语言/框架** | TypeScript · LangGraph 编排 |
| **核心特性** | ① Creative Hub 创意输入 ② 自动导演开书 ③ 整本生产主链 (章节生成→审核→修复→状态回灌) ④ 写法引擎 ⑤ 漫画/短剧衍生工坊 |
| **状态管理** | 世界手册 · 角色资源账本 · 知识库 (RAG) · 暂停/恢复 |
| **许可证** | MIT |

### 2.5 NovelClaw — 动态记忆优先框架

| 维度 | 详情 |
|:---|:---|
| **核心理念** | 以动态记忆为核心的协作式 AI 框架 |
| **语言/框架** | Python · FastAPI · RAG |
| **记忆机制** | Memory-first: 对话只负责创作，不负责记忆；用文件系统分离设定/大纲/正文 |
| **适合场景** | 长篇连载 (逐章规划+生成) |
| **许可证** | MIT |

---

## 3. Skill 生态

### 3.1 已有 WorkBuddy Skills (本项目可用)

| Skill | 来源 | 定位 |
|:---|:---|:---|
| **open-novel-writing** | 已安装 | 中文长篇小说创作 (世界观→大纲→章节→评审) |
| **0715-scriptwriter** | 已安装 | AI 漫剧/竖屏短剧 70-90 集剧本 |
| **fbs-bookwriter** | 已安装 | 书籍/手册/白皮书/行业指南 |
| **screenwriting-master** | 已安装 | 全格式影视编剧 (短片到电影长片) |
| **text-game-generator** | 已安装 | 文字游戏剧本 (19 种模板) |
| **rpg-text** | 已安装 | 文字 RPG (D&D 规则) |
| **manju-writer** | 已安装 | 漫剧编剧分镜 |

### 3.2 可安装的外部 Skills

| Skill | Stars | 安装方式 | 特色 |
|:---|:---:|:---|:---|
| oh-story-claudecode | 3,212 | `npx skills add worldwonderer/oh-story-claudecode` | 网文全流程 |
| xt-webnovel-writing | ~500 | ClawHub | OpenClaw 原生 6 模块流水线 |
| inkos | 5,300 | `clawhub install inkos` | 10 Agent 全自动 |
| NovelCraft | ~150 | `clawhub install novelcraft` | 全自动出书+PDF/EPUB |
| novel-craft (chaserr) | ~50 | SkillsMP | 5 模块协作 |

---

## 4. 技术方案对比

### 4.1 架构模式对比

| 模式 | 代表项目 | 优点 | 缺点 | 适合 |
|:---|:---|:---|:---|:---|
| **多 Agent 管线** | InkOS, AI_Gen_Novel | 分工明确、可扩展、质量高 | 复杂、token 消耗大 | 长篇高品质 |
| **Skill 组合** | oh-story, NovelCraft | 灵活、按需加载 | 需要 Agent 框架 | 已有 WorkBuddy |
| **端到端一键** | Million-Word, MuMuAI | 简单、上手快 | 黑盒、难定制 | 快速原型 |
| **世界模型驱动** | NovelWriter | 一致性极强 | 构建成本高 | 史诗奇幻 |
| **LangGraph 编排** | LangGraph-Novel, AI-Novel-Assistant | 状态可控、可恢复 | 学习曲线陡 | 工程化需求 |

### 4.2 记忆系统对比

| 方案 | 代表项目 | 存储 | 检索 |
|:---|:---|:---|:---|
| **Truth Files** | InkOS (7 个 MD) | 文件系统 | 全文读取 |
| **分层记忆** | Million-Word (核心/近期/历史) | SQLite | 分级召回 100%/全量/摘要 |
| **RAG 向量检索** | AI_Gen_Novel, NovelClaw | 向量 DB | 语义搜索 |
| **世界模型** | NovelWriter (实体-关系图) | SQL+图谱 | 结构化查询 |
| **YAML 追踪** | novel-writer (角色卡/关系/伏笔) | YAML 文件 | 文件读取 |

### 4.3 模型选择对比

| Provider | 优势 | 劣势 | 适合任务 |
|:---|:---|:---|:---|
| **Claude** | 创意写作最强、长文理解 | 贵、速率限制 | 正文写作 |
| **DeepSeek** | 性价比高、中文好 | 长文一致性略差 | 大纲/设定 |
| **GPT-4o** | 综合能力强 | 中文不如国产 | 审计/检查 |
| **Gemini** | 上下文窗口大 | 中文风格偏硬 | 大纲规划 |
| **Qwen/MiniMax** | 中文原生 | 创意性一般 | 润色/翻译 |
| **本地模型 (Ollama)** | 隐私、免费 | 质量有限 | 草稿/实验 |

---

## 5. 关键架构模式提取

### 5.1 必选模式 (所有成功项目共用)

```
┌──────────────────────────────────────────────────────┐
│                  创意/灵感输入                          │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│              结构化规划 (Structured Planning)           │
│  世界观设定 · 人物小传 · 大纲/细纲 · 势力关系           │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│              长期记忆 (Long-term Memory)               │
│  角色状态 · 伏笔追踪 · 时间线 · 资源账本 · 关系网      │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│              逐章生成 (Chapter Generation)              │
│  上下文组装 → 写作 → 事实提取 → 状态更新                │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│              质量审计 (Quality Audit)                   │
│  一致性检查 · 伏笔回收 · 风格检查 · 去AI味             │
└──────────────────────┬───────────────────────────────┘
                       ↓
┌──────────────────────────────────────────────────────┐
│              输出交付 (Output Delivery)                 │
│  Markdown · PDF · EPUB · TXT                         │
└──────────────────────────────────────────────────────┘
```

### 5.2 差异化模式 (可选增强)

| 模式 | InkOS | oh-story | AI_Gen | NovelWriter |
|:---|:---:|:---:|:---:|:---:|
| 扫榜/市场分析 | ✅ Radar | ✅ scan | ❌ | ❌ |
| 拆文/对标学习 | ❌ | ✅ analyze | ✅ RAG | ❌ |
| 多 Agent 协作 | ✅ 10 Agent | ✅ 7 Agent | ✅ Multi | ✅ Copilot |
| 审计修复循环 | ✅ 37维 | ❌ | ❌ | ❌ |
| 去 AI 味 | ✅ 内置 | ✅ deslop | ✅ 24模式 | ❌ |
| 断点续传 | ❌ | ❌ | ✅ .novel_save | ✅ |
| 世界模型/图谱 | ✅ Truth Files | ✅ 文件结构 | ❌ | ✅ 实体关系 |
| 多模型路由 | ✅ | ✅ | ✅ | ✅ |
| 封面生成 | ❌ | ✅ GPT-Image | ❌ | ❌ |
| 漫画/短剧衍生 | ❌ | ❌ | ❌ | ❌ |

---

## 6. 推荐技术栈

### 6.1 基于 WorkBuddy + Python 的方案

考虑到老赵的技术栈 (Python + WorkBuddy 生态)，推荐以下方案：

| 层级 | 技术选择 | 理由 |
|:---|:---|:---|
| **语言** | Python 3.12+ | 老赵主力语言，生态丰富 |
| **LLM** | DeepSeek (主力) + Claude (正文) | 性价比+质量 |
| **记忆系统** | SQLite + Markdown 文件 | 简单可靠，适合 WorkBuddy 文件操作 |
| **编排** | Python 脚本 + WorkBuddy 自动化 | 复用已有 CI/CD 经验 |
| **状态管理** | JSON + YAML 文件 | 人类可读，Git 友好 |
| **输出** | Markdown → HTML/PDF | 复用 autoquantitize 报告管线 |
| **UI** | WorkBuddy 对话式 + HTML 预览 | 零前端成本 |
| **版本控制** | Git + GitHub | 复用已有 GitHub 工作流 |

### 6.2 核心模块设计

```
NovelGenerator/
├── core/
│   ├── planner.py          # 创意→世界观→大纲→细纲
│   ├── world_builder.py    # 世界观/势力/体系构建
│   ├── character_mgr.py    # 角色管理 (创建/关系/弧线)
│   ├── writer.py           # 章节写作 (上下文组装+生成)
│   ├── auditor.py          # 质量审计 (一致性/伏笔/风格)
│   └── memory.py           # 分层记忆 (核心/近期/历史)
│
├── tools/
│   ├── style_extractor.py  # 文风学习/仿写
│   ├── deai_detector.py    # 去AI味检测
│   ├── cover_gen.py        # 封面生成 (DALL-E/GPT-Image)
│   └── exporter.py         # 导出 (MD/HTML/PDF/EPUB)
│
├── templates/
│   ├── worldbuilding.md    # 世界观模板
│   ├── character_card.md   # 角色卡模板
│   ├── chapter_outline.md  # 章节大纲模板
│   └── foreshadowing.md    # 伏笔追踪模板
│
├── research/               # 调研文档
├── docs/                   # 使用文档
├── novels/                 # 已创作小说
│   └── {书名}/
│       ├── config/         # 设定/世界观/角色
│       ├── outline/        # 大纲/细纲
│       ├── chapters/       # 正文章节
│       ├── tracking/       # 伏笔/时间线/状态
│       └── exports/        # 输出文件
│
└── novel_generator.py      # 主入口脚本
```

### 6.3 知识体系 (参考 oh-story)

写作方法论 reference 文件可从 oh-story 的 100+ 份文件中精选：

- **大纲排布**: 五步大纲法 · 三幕结构 · 升级感设计
- **人物设计**: 角色弧线 · 对话技法 · 反派塑造
- **爽点节奏**: 钩子设计 · 反转工具箱 · 期待感管理
- **去AI味**: 24 种 AI 痕迹检测 · 句式变化 · 词汇疲劳

---

## 7. 风险评估与建议

### 7.1 核心风险

| 风险 | 等级 | 缓解措施 |
|:---|:---|:---|
| **长篇一致性崩塌** | 🔴 高 | 分层记忆 + Truth Files + 每章审计 |
| **AI 味过重** | 🟡 中 | 去 AI 味引擎 + 文风注入 + 人工润色 |
| **Token 成本失控** | 🟡 中 | 摘要压缩 (90-99%) + 上下文精确裁剪 |
| **创意枯竭/同质化** | 🟢 低 | 扫榜分析 + 拆文学习 + 多模型混用 |
| **数据隐私** | 🟢 低 | 全本地文件存储 + Git 私有仓库 |

### 7.2 建议路线

| 阶段 | 时间 | 目标 |
|:---|:---|:---|
| **Phase 1: MVP** | 1-2 周 | 创意→世界观→角色→大纲→单章生成 |
| **Phase 2: 记忆系统** | 1 周 | 分层记忆 + 伏笔追踪 + 角色状态管理 |
| **Phase 3: 质量体系** | 1 周 | 一致性审计 + 去AI味 + 多模型路由 |
| **Phase 4: 完本能力** | 2 周 | 逐章连续生成 + 断点续传 + 整本导出 |
| **Phase 5: 增强** | 持续 | 扫榜分析 · 拆文学习 · 文风仿写 · 封面生成 |

### 7.3 一句话总结

> **InkOS 是最完整的工业化方案 (但 TypeScript + AGPL)，oh-story 是最成熟的 Skill 生态 (可直接挂到 WorkBuddy)，AI_Gen_Novel 是最友好的 Python 桌面应用。对于 Python + WorkBuddy 技术栈，推荐以 InkOS 的架构思想为蓝图、oh-story 的知识体系为血肉、AI_Gen_Novel 的 Python 实现为骨架，自建 NovelGenerator。**

---

## 附录: 项目链接汇总

| 项目 | GitHub |
|:---|:---|
| InkOS | https://github.com/Narcooo/inkos |
| oh-story-claudecode | https://github.com/worldwonderer/oh-story-claudecode |
| AI-Novel-Writing-Assistant | https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant |
| AI_Gen_Novel | https://github.com/ronghuaxueleng/AI_Gen_Novel |
| NovelClaw | https://github.com/iLearn-Lab/NovelClaw |
| novel-writer | https://github.com/AI-Practical-Lab/novel-writer |
| NovelWriter | https://github.com/Hurricane0698/novelwriter |
| NovelCraft | https://github.com/Kesuek/novelcraft |
| Million-Word-Novel | https://github.com/kevinchcn/million-word-novel-ai-creator |
| LangGraph-Novel | https://github.com/bodinggg/LangGraph-based-Novel-by-Agents |
| MuMuAINovel | https://github.com/Soulhuo/MuMuAINovel |
| novel-craft (SkillsMP) | https://skillsmp.com/creators/chaserr/novel-craft |
