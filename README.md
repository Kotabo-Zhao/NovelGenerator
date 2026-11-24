# 墨局 · NovelGenerator v5.0

基于**统一叙事状态账本（SQLite WAL + FTS5）**与**四阶段组合式写作引擎**彻底重建的本地优先长篇小说创作工坊。

## v5.0 相对 v4.3.3 的核心架构革新

1. **统一叙事状态账本（`backend/core/database.py`）**：用单文件零依赖 SQLite（WAL 模式 + FTS5 全文检索）替代平铺 JSON 与三套割裂记忆，实现章节正文、版本快照、角色最新状态、伏笔生命周期与正史事件的单事务 ACID 原子提交。
2. **四阶段纯组合写作引擎（`backend/core/writing_engine.py`）**：彻底废除 7 重 Mixin 继承与 800 行巨型函数，拆分为清晰的四阶段流水线：
   - **Stage 1 · Architect**：生成结构化 `ChapterContract`（目标、冲突、必发事件、钩子方向）与初始角色/伏笔/世界观账本。
   - **Stage 2 · ContextCompiler**：按需从 SQLite 编译高信号上下文（仅注入本章出场角色最新状态、上章桥接锚点、到期伏笔、防重复正史事件与 FTS5 检索结果），配合中文段落/句末语义边界裁剪与必选区段保底配额。
   - **Stage 3 · ProseDrafter**：全异步 `httpx` 流式写作 + 划词局部精修，内置零密钥演示模式（Demo Mode）开箱即用。
   - **Stage 4 · Craft & StateCommitter**：零 LLM 消耗毫秒级中文文风体检（42 类 AI 套话、句长节奏 CV、对话占比、感官密度与一键改稿建议）+ 确定性引号/括号平衡质量门 + 状态变更 Delta 原子落库。
3. **全新模块化前端（`frontend/`）**：零 CDN、零构建依赖、兼容桌面 WebView2 / Android WebView，采用宣纸浅色与墨炭暗色双主题、三栏写作工坊、沉浸专注模式（`Alt+Z` / `Esc`）、文风体检一键联动改稿、划词精修与历史版本一键回滚。

## 快速启动

```bash
pip install -r requirements.txt
python run.py
```

打开 `http://127.0.0.1:8787` 即可直接使用（默认内置演示模式，无需 API Key 即可跑通新建 → 大纲规划 → 流式写作 → 文风体检 → 划词精修 → 状态账本检索全流程；在「模型设置」中填入 DeepSeek / OpenAI / Ollama 密钥即可切换真实模型）。

## 运行测试

```bash
python -m unittest discover -s tests -v
```
