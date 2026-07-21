# 📖 NovelGenerator — AI 小说生成器

> 输入灵感，AI 自动生成世界观、角色、大纲和逐章正文。
> **PWA 免安装**，浏览器打开即用。iOS 添加到主屏幕 ≈ 原生 App。

## ✨ 功能

- 🎨 **灵感→完整设定** — 输入题材+风格+一句话灵感，AI 自动生成世界观、角色卡、分卷大纲
- ✍️ **逐章写作** — 流式输出，实时看到 AI 写作过程（打字机效果）
- 🧠 **分层记忆** — 核心设定 + 近期上下文 + 伏笔追踪，保持长篇一致性
- 📱 **PWA 支持** — 添加到 iOS/Android 主屏幕，全屏运行
- 📤 **导出 TXT** — 一键导出全文（EPUB/PDF 即将支持）

## 🚀 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek API Key
```

> 获取 API Key: https://platform.deepseek.com

### 3. 启动后端

```bash
cd backend
python api/server.py
# → http://localhost:8000
```

### 4. 打开前端

```bash
# 方式一：直接打开 web/index.html（需改 API 地址为服务器地址）
# 方式二：用任意静态文件服务器
cd web
python -m http.server 3000
# → http://localhost:3000
```

### 5. iOS 添加到主屏幕

```
Safari 打开 → 点「分享」→「添加到主屏幕」
→ 主屏幕出现 App 图标 → 点击全屏打开
```

## 🏗️ 架构

```
web/index.html          ← Vue 3 SPA (PWA)
    ↓ HTTP/SSE
backend/api/server.py   ← FastAPI
    ↓
backend/core/
  ├── engine.py         ← 创作管线编排
  ├── planner.py        ← 世界观/角色/大纲生成
  ├── writer.py         ← 章节流式写作
  └── memory.py         ← 分层上下文管理 (L1/L2/L3)
    ↓
DeepSeek API            ← LLM (deepseek-chat)
```

## 📂 项目结构

```
NovelGenerator/
├── backend/
│   ├── api/server.py       # FastAPI 服务器
│   ├── core/
│   │   ├── engine.py       # 创作引擎
│   │   ├── planner.py      # 规划器
│   │   ├── writer.py       # 写手
│   │   └── memory.py       # 记忆管理
│   ├── config.py           # 配置
│   └── requirements.txt
├── web/
│   ├── index.html          # Vue 3 SPA (单文件)
│   └── manifest.json       # PWA 配置
├── novels/                 # 已创作小说 (Markdown)
├── research/               # 调研报告
│   ├── 01-ecosystem-survey.md
│   └── 02-feasibility-assessment.md
└── .env.example
```

## 📝 使用流程

1. **输入灵感** → 选题材/风格/字数 → 点「开始创作」
2. **AI 生成设定** → 世界观+角色+分卷大纲（可编辑）
3. **逐章写作** → 选章节 → 点「生成」→ 流式实时预览
4. **导出** → 一键下载 TXT

## 🔧 技术栈

| 层 | 技术 |
|:---|:---|
| 前端 | Vue 3 (CDN) · 纯 HTML · PWA |
| 后端 | Python FastAPI · SSE 流式 |
| LLM | DeepSeek Chat API |
| 存储 | 文件系统 (Markdown + JSON) |

## 📄 License

MIT
