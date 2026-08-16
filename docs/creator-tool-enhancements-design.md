# NovelGenerator 创作者功能增强 — 详细设计文档

> 版本：v1.3 · 2026-08-15
> 范围：面向**小说创作者**的工具化增强（非软件工程基建）
> 状态：**6 项已全部实施完成**（章节精细操作 / 全文检索 / 世界圣经 / 统计仪表盘 / 灵感箱 / 设定扫描）；大纲拖拽重组、导出扩展+备份暂缓（保留备查）
> **前端：PC / 安卓双方案**（见 §0.5），共享数据模型与 API，交互载体分端设计
> 前置阅读：`docs/architecture-v2.html`（架构总览）、`backend/core/shared_memory.py`（数据层）、`backend/api/routers/*.py`（API 层）、`web/index.html`（单体前端）

---

## 目录

- §0 现状盘点与设计原则
- §0.5 **前端信息架构规划（PC / 安卓双方案）**：双端差异基线 → 入口归属矩阵 → PC 方案 → 安卓方案 → CSS 断点 → 函数清单 → 实施顺序
- §2 章节精细操作（✅ 已实施：POST /chapters/{num}/surgical + GET/POST versions）
- §3 全文检索（✅ 已实施：GET /api/novels/{id}/search）
- §4 世界圣经管理（✅ 已实施：bible.json + GET/PUT + terms/extract + relations/render）
- §5 写作统计仪表盘（✅ 已实施：GET /stats + PUT /stats/goal）
- §7 灵感收集箱 + 素材库（✅ 已实施：ideas.json + CRUD）
- §8 设定冲突 AI 扫描（✅ 已实施：POST /scan-consistency + settings_scanner.py）
- §9 实施计划 / §10 验收标准
- 附录 A：暂缓项（§1 大纲拖拽重组、§6 导出扩展+备份）

---

## 实施记录（v1.3 追加）

| 功能 | 后端文件 | 前端 | 测试文件 |
|---|---|---|---|
| 全文检索 | `api/routers/novels.py` (search_novel) | 搜索弹层（PC居中/安卓全屏） | `tests/test_creator_m1.py` |
| 章节精细操作 | `api/routers/novels.py` (surgical_edit/versions/rollback) | 选中工具条 + 版本弹层 | `tests/test_creator_m1.py` |
| 世界圣经 | `api/routers/bible.py`（新）+ bible.json | bible-view（4 Tab + Canvas 关系图） | `tests/test_creator_m2.py` |
| 统计仪表盘 | `api/routers/novels.py` (novel_stats/save_goal) | stats-view（4卡 + CSS柱状 + 目标） | `tests/test_creator_m2.py` |
| 灵感箱 | `api/routers/ideas.py`（新）+ ideas.json | ideas-view（卡片 + tag + 素材库） | `tests/test_creator_m2.py` |
| 设定扫描 | `core/settings_scanner.py`（新）+ POST scan | 扫描弹层（结果 + 定位） | `tests/test_creator_m2.py` |

验证：296 passed（根）+ 148 passed（backend）；CDP PC 1440px + 安卓 390px 双端通过；三副本同步。

---

## 实施记录 v2.0：改造定稿（2026-08-15）

> 依据真实验证（后端 11 项 API 实测全过；前端 CDP 发现展示层粗糙、定位脱节）与用户反馈
> （设定扫描不应是独立按钮，应生成时自动检查 + 正文内联标注），整合确定以下改造方案。

### V2.0-A. 功能定位（每项一句话）

| 功能 | 定位 | 解决的核心痛点 |
|---|---|---|
| ✂️ 章节精细操作 | 改稿手术刀 | 不想整章重生成，只改某段且可回滚 |
| 🔍 全文检索 | 定位工具 | 长篇忘了设定/情节在哪，快速找到 |
| 📖 世界圣经 | 设定总账（可增删改） | 前后矛盾、设定记不清 |
| 📊 统计仪表盘 | 进度感知 | 不知道写了多少、离目标多远 |
| 💡 灵感箱 | 素材收集 | 好点子随手记、一键成书 |
| 🔬 设定扫描 | **质量体检（生成时自动）** | 长文越写越乱，主动找矛盾 |

### V2.0-B. 真实验证结论（现状与设计的差距）

后端 ✅ 全部兑现设计（11 项实测）：
- 搜索命中章节/大纲/角色卡 ✓
- 圣经读取/写入持久化/名词提取/关系图数据 ✓
- 统计字数与磁盘一致、目标持久化 ✓
- 灵感增删查 ✓
- 真实 LLM 续写/精简/版本历史 ✓
- 设定扫描执行（6 章 3 实体）✓

前端 ❌ 展示层粗糙（CDP 实测）：
- 世界圣经 **0 编辑按钮、0 输入框**（设计 CRUD，实现只有展示）
- 搜索 **无命中高亮**
- 灵感箱 **无内联输入**（依赖原生 prompt）
- 统计 **无 SVG 趋势折线**
- 设定扫描是**独立按钮手动触发**（用户要求改为生成时自动）

### V2.0-C. 改造清单（按优先级）

#### 🔴 P0：设定扫描并入生成流程 + 正文内联标注（用户核心诉求）

**定位变化**：设定扫描从"独立按钮"改为"章节生成时自动检查 + 正文标注不符合项"。

**后端**（`backend/core/mixins/generation.py` + `core/settings_scanner.py`）：
1. 扩展 `_outline_compliance_step`：生成后校验大纲合规时，**同时跑设定一致性检查**
   - 实体名单自动提取：plan.json（角色/地点/势力）+ bible.json + 已写章节高频词（复用 settings_scanner._load_entities，无需用户手动建 bible）
   - 检查本章正文与**已写前文**的实体属性矛盾（年龄/关系/地点/能力/时间线）
2. SSE `outline_compliance` 事件新增 `annotations` 字段：
   ```json
   {"type": "outline_compliance", "chapter": 5, "pct": 85, "level": "partial",
    "annotations": [
      {"text": "林晚今年30岁，已是长老", "issue": "第3章设定25岁，年龄冲突", "type": "setting", "chapter_ref": 3},
      {"text": "他答应过报仇", "issue": "大纲节拍2（冲突升级）未落地", "type": "outline", "chapter_ref": null}
    ]}
   ```
3. 不阻断生成：仅提示 + 标注（自动补写逻辑保留给大纲缺失）

**前端**（`web/index.html`）：
1. 生成完成收到 `outline_compliance.annotations` → 存储到 `writing.annotations`
2. 正文渲染：问题句用 `<mark class="anno">` 高亮（设定冲突=红色底纹，大纲缺失=黄色底纹）
3. 悬停/点击标注 → tooltip 显示 `issue` + 「查看参照章」按钮（跳转 chapter_ref）
4. 章节卡/正文顶部显示合规摘要：`✓ 100%` / `⚠ 85% · 2 处标注`

#### 🔴 P0：世界圣经完整 CRUD（定位"设定总账"）

- 人物/地点/名词卡片：加「编辑」「新增」「删除」按钮
- 内联表单（名称/身份/描述/备注/首次出场），保存调 `PUT /bible`
- 关系图 Tab：加「添加关系」（from/to/type/note 下拉表单）
- 复用现有 `PUT /api/novels/{id}/bible`（已实测可持久化）

#### 🟠 P1：搜索增强

- 命中关键词 `<mark>` 高亮（snippet 内）
- 搜索范围：当前书 / 全部书（后端需支持跨书，或前端循环调用）
- 搜索历史（最近 5 条，localStorage）

#### 🟠 P1：统一弹窗组件（消除 7 处原生 prompt/confirm）

- 新增 `modal` 状态 + 通用弹窗（支持输入框/文本域/多字段）
- 替换：灵感新增/编辑、素材新增、重写意见、删除确认、回滚确认、风格命名

#### 🟠 P1：统计仪表盘补全

- 每日字数 SVG 折线（后端 stats 已返回 daily 数据）
- 目标进度条（当前字数/目标字数）

#### 🟡 P2：章节操作反馈

- 操作完成后 toast 显示改动量（`修改后 N 字`）
- 续写支持目标字数输入（modal）

### V2.0-D. 实施顺序

| 步 | 内容 |
|---|---|
| 1 | 后端：`_outline_compliance_step` 集成设定检查 + annotations 事件 |
| 2 | 前端：正文标注渲染（mark 高亮 + tooltip） |
| 3 | 前端：世界圣经 CRUD（编辑/新增/删除/关系） |
| 4 | 前端：统一弹窗组件 + 替换 prompt/confirm |
| 5 | 前端：搜索高亮 + 范围 |
| 6 | 前端：统计 SVG 趋势 + 目标进度 |
| 7 | 全量测试 + CDP 双端验证 + 同步 + APK |

### V2.0-E. 验收标准（v2.0）

- [x] 生成章节后正文自动标注设定冲突/大纲缺失，无独立"设定扫描"按钮
- [x] 世界圣经可编辑（增删改持久化）
- [x] 搜索命中高亮 + 跨书可选
- [x] 无原生 prompt/confirm 对话框（新功能）
- [x] 统计有趋势折线 + 目标进度
- [x] 301+148 测试通过，CDP 双端通过

### V2.0-F. 实施记录（已完成）

| 项 | 实现 |
|---|---|
| 设定扫描并入生成 | `_outline_compliance_step` 后集成 `_setting_annotation_step`（settings_scanner.check_chapter_against_prior，本章 vs 前文 LLM 判定），SSE 产出 `outline_compliance_annotations` 事件 |
| 正文标注 | `annotatedContent` computed（mark.anno-setting/outline 高亮）+ `onAnnoClick` tooltip（issue + 跳转参照章） |
| 圣经 CRUD | bibleStartNew/Edit/Save/Delete + relation 表单，内联编辑弹层，PUT /bible 持久化 |
| 统一弹窗 | `modal` 响应式 + openModal/confirmModal，替换灵感/素材/重写/回滚/圣经删除 5 处 prompt/confirm |
| 搜索高亮 | `highlightSnippet`（mark.search-mark）+ 当前书/全部书范围切换 + 跨书跳转 |
| 统计趋势 | `trendPoints`/`trendMax` computed + SVG polyline + goal-progress-fill 进度条 |

---

## 0. 现状盘点与设计原则

### 0.1 现有能力（本设计复用，不重复造轮子）

| 领域 | 已有实现 | 位置 |
|---|---|---|
| 大纲存储 | `plan.json → outline.volumes[].chapters[]`（每章含 summary/beats/hook/conflict 等 12+ 字段） | `backend/core/shared_memory.py` |
| 章节存储 | `chapters/chapter_%04d.md`（0 填充 4 位，与 chapter_num 映射） | 同上 |
| 剧情图谱 | `storygraph.json`：plot_threads / foreshadow_ledger / char_snapshots / causal_links / arcs | `backend/core/`（storygraph 模块） |
| 角色圣经 | `character_bible.json`（AI 生成，含 personality/motivation/arc） | `backend/core/character_profiler.py` |
| 导出 | TXT / **EPUB 已实现** | `backend/api/routers/novels.py:509` |
| 大纲编辑 | 加卷/删卷/加章/删章/重编号（renumberChapters） | `web/index.html` 前端函数 |
| 一致性校验 | 单章 L1/L2 校验（consistency_validator） | `backend/core/consistency_validator.py` |
| 质量闭环 | 大纲合规 / 节奏 / 开篇 / 章末 / 需求监督 | `backend/core/` 多模块 |

### 0.2 设计原则

1. **不引入新数据库**：继续用文件系统 + `atomic_write_json`（多进程安全的原子写），数据模型向后兼容。
2. **前端仍是单体**：`web/index.html` 单文件 Vue3，新增功能以"视图 + 函数"追加，不拆分构建。
3. **后端路由按域拆分**：新端点放进对应 domain router（novels/outline/quality 等），不塞进 server.py。
4. **所有 AI 调用走 `engine.client` / `writer._create` / `_resilient`**：复用重试、断点续传、超时治理。
5. **可回滚**：所有破坏性操作（排序/合并/删除）先写版本快照，失败可恢复。
6. **与三副本同步兼容**：后端改动需过 `tools/sync_android_core.py`，前端改动过 `tools/sync_frontend.py`。

---

## 0.5 前端信息架构规划（PC / 安卓双方案）

> 6 项新功能（章节精细操作/全文检索/世界圣经/统计仪表盘/灵感箱/设定扫描）必须在不破坏现有 5 视图导航的前提下落地。
> 核心策略：**导航位不增，功能按"场景归属"收纳**——每项功能挂到它最自然的操作场景。
> **PC 与安卓交互差异显著（鼠标悬停/宽屏 vs 触屏/窄屏），分两套方案设计**，共享同一套数据模型与 API。

### 0.5.1 双端现状与差异基线

| 维度 | PC（宽屏 + 鼠标） | 安卓（窄屏 + 触屏） |
|---|---|---|
| 导航 | 顶部 topbar（5 项可见） | 底部 bottom-nav（5 项）+ topbar 隐藏 |
| 书卡操作 | 鼠标悬停显示快捷图标 | 无悬停 → 长按弹菜单 |
| 工具栏 | wb-actions 可平铺 5-6 个图标 | 窄屏 → 收进「⋯ 更多」折叠 |
| 弹层 | 居中面板 | 全屏抽屉 |
| 新视图返回 | 顶部导航可随时切走 | 需显式「返回」按钮（topbar 隐藏） |

**共享**：currentView 扩展值（bible/stats/ideas）、API、数据模型、`_returnView` 逻辑。

### 0.5.2 功能 → 入口归属矩阵（双端统一决策）

| 功能 | PC 入口 | 安卓入口 | 载体 |
|---|---|---|---|
| ✂️ 章节精细操作 | 选中正文 → 悬浮工具条 + 工具栏「🕘」 | 选中正文 → 底部浮动条 + 章节卡长按「版本」 | 内嵌 |
| 🔍 全文检索 | 书架头部「🔍」+ 写作页工具栏「🔍」 | 书架头部「🔍」+ 写作页「⋯」菜单内 | 弹层（PC 居中 / 安卓全屏） |
| 📖 世界圣经 | 书卡悬停「📖」+ 写作页「📖」 | 书卡长按菜单「📖」+ 写作页「⋯」内 | bible-view |
| 📊 统计仪表盘 | 书卡悬停「📊」+ 写作页「📊」 | 书卡长按菜单「📊」+ 写作页「⋯」内 | stats-view |
| 💡 灵感箱 | 书架头部「💡」 | 书架头部「💡」+ 底部导航常驻？否——书架页内 | ideas-view |
| 🔬 设定扫描 | 写作页工具栏「🔬」 | 写作页「⋯」菜单内「🔬」 | 弹层 |

---

### 0.5.3 PC 方案（宽屏 · 鼠标 · 平铺可见）

#### PC-A. 书架视图（shelf-view）

```
┌────────────────────────────────────────────────────────────┐
│ 我的书架                    [💡 灵感] [🔍 搜索] [✨ 开始新作]     │
│ 统计概览卡（已有）                                             │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐                     │
│ │书卡       │ │书卡       │ │书卡       │                      │
│ │ 悬停→✕📐📊📖 │ │          │ │          │                      │
│ └──────────┘ └──────────┘ └──────────┘                     │
└────────────────────────────────────────────────────────────┘
```

- 头部右侧 3 按钮：`💡灵感 → ideas-view`、`🔍搜索 → 搜索弹层`、`✨开始新作`（已有）
- 书卡 **hover 显示** 4 个 tile-action：`✕删除(最右) | 📐大纲 | 📊统计 | 📖圣经`
  - 桌面 `.tile-action { opacity:0 }`，`.book-tile:hover .tile-action { opacity:1 }`
  - 4 图标横向排列，宽 26px 间距 4px
- 搜索弹层：**居中面板**（max-width 560px），复用 toast-overlay 遮罩

#### PC-B. 写作页（write-view）— 工具栏全展开

```
┌──────────────────────────────────────────────────────────┐
│ [写作] [互动]  《书名》   [🔍][📖][📊][🔬][🕘][📋大纲] │ ← wb-actions 平铺 6 图标
│ ┌──────┬────────────────────────────┬──────────────┐     │
│ │章节列表│  ✂️选中工具条(浮动) 正文      │ 质量面板      │     │
│ │      │                            │              │     │
│ └──────┴────────────────────────────┴──────────────┘     │
└──────────────────────────────────────────────────────────┘
```

- **wb-actions**（顶部右侧，已有「📋大纲」）：平铺 6 图标 `🔍📖📊🔬🕘📋`
  - PC 宽度 ≥ 1200px 全展开；900-1200px 收掉低优先级（🕘📊）
- **正文选中工具条**：选中文字 → 正文上方浮动条 `✂️续写 | ➕扩写 | ➖精简 | ✏️重写`
- **章节卡悬停**：出现 `🕘` 小图标（快速看版本）

#### PC-C. 新视图（bible/stats/ideas）— 完整页面

- 顶部导航保持可见，切换随时可回
- 内容区宽屏利用：bible 人物卡 3-4 列、关系图 600px、统计图表并排
- 返回：顶部导航天然提供，另加「← 返回写作」按钮（记录 `_returnView`）

---

### 0.5.4 安卓方案（窄屏 · 触屏 · 折叠 + 长按）

#### AN-A. 书架视图（shelf-view）

```
┌──────────────────────────────┐
│ 我的书架   [💡] [🔍]  [✨开卷] │   ← 头部 3 图标（紧凑）
│ 统计概览卡（2列）              │
│ ┌────────┐ ┌────────┐        │
│ │书卡     │ │书卡     │         │
│ │        │ │        │         │
│ └────────┘ └────────┘        │
│       长按书卡 → 弹出菜单       │
└──────────────────────────────┘
```

- 头部图标缩小（icon-btn 32px），`💡灵感` `🔍搜索` 放左侧，`✨开卷` 右侧
- 书卡 **长按（500ms）弹出底部菜单**：`📐大纲 | 📊统计 | 📖圣经 | ✕删除`（替代 4 图标）
  - 实现：`@touchstart` 定时器 + `@touchend` 取消；菜单用底部 sheet（复用 toast 样式）
  - 单击书卡 = 打开写作（不变）
- 搜索弹层：**全屏**（100vw/100vh），输入框聚焦自动弹键盘

#### AN-B. 写作页（write-view）— 工具栏折叠「⋯ 更多」

```
┌──────────────────────────────┐
│ [写作][互动] 《书名》   [⋯]    │   ← wb-actions 折叠
│ ┌─────┬────────────────────┐ │
│ │章节  │ 正文                │ │
│ │列表  │ ✂️底部浮动操作条     │ │
│ └─────┴────────────────────┘ │
│ [⋯] 展开菜单：                 │
│  🔍搜索 📖圣经 📊统计 🔬扫描 🕘版本 📋大纲 │
└──────────────────────────────┘
```

- **wb-actions 折叠**：窄屏只显示「⋯ 更多」按钮 → 展开下拉菜单（6 项）
  - CSS `@media(max-width:768px)`：`.wb-actions .icon-btn {display:none}`，仅 `.wb-more` 可见
- **正文选中操作条**：选中文字 → **正文底部浮动条**（拇指可达）：`✂️续写 | ➕扩写 | ➖精简 | ✏️重写`
- **章节卡长按**：弹版本历史底部 sheet

#### AN-C. 新视图（bible/stats/ideas）— 单列 + 显式返回

```
┌──────────────────────────────┐
│ ← 返回    📖 世界圣经 · 《书名》  [保存] │
│ [人物] [地点] [名词] [关系]     │
│ ┌──────────────────────────┐ │
│ │人物卡（单列）              │ │
│ └──────────────────────────┘ │
└──────────────────────────────┘
```

- 顶部固定栏：`← 返回`（_returnView）+ 标题 + 操作按钮
- 单列布局，卡片全宽；关系图 Canvas 全宽 280px 高
- 统计图表：柱状图横滑（scroll-x），趋势图全宽
- 底部不占用 bottom-nav（bottom-nav 常驻，视图内容 padding-bottom 预留）

---

### 0.5.5 双端共享的 CSS 断点策略

| 断点 | 适用 | 策略 |
|---|---|---|
| `≥1200px` | PC 大屏 | wb-actions 全平铺（6 图标） |
| `768-1200px` | PC 窄窗/平板横 | wb-actions 收 🕘📊；书卡 hover 图标 |
| `≤768px` | 安卓/移动 | wb-actions 折叠「⋯」；书卡长按菜单；弹层全屏；新视图单列+返回 |

**实现方式**：CSS 媒体查询控制显隐（`.wb-actions .icon-btn`、`.tile-action`、`.pc-only` / `.mobile-only` 类），**JS 逻辑统一**（同一套函数，仅交互载体不同）。

### 0.5.6 前端新增/修改函数清单（全部注册进 return，双端共用）

| 函数 | 用途 | 所属 |
|---|---|---|
| `openSearch()` / `closeSearch()` / `runSearch(q)` / `jumpToSearchHit(hit)` | 搜索面板 | 全局 |
| `openBible()` / `loadBible()` / `saveBible()` / `extractTerms()` / `drawRelationGraph()` / `switchBibleTab(t)` | 世界圣经 | bible-view |
| `openStats()` / `loadStats()` / `saveGoal()` | 统计 | stats-view |
| `openIdeas()` / `loadIdeas()` / `addIdea()` / `deleteIdea()` / `ideaToNovel(i)` / `switchIdeasTab(t)` | 灵感箱 | ideas-view |
| `surgicalEdit(op)` / `openVersions(ch)` / `rollbackVersion(v)` / `showSelectionBar()` | 章节精细操作 | write |
| `openScanPanel()` / `runConsistencyScan()` / `jumpToConflict(c)` | 设定扫描 | write |
| `openNovelActions(n)`（安卓长按菜单）/ `closeNovelActions()` | 书卡操作 | shelf |
| `openWriteMoreMenu()` / `closeWriteMoreMenu()`（安卓「⋯」） | 写作页折叠菜单 | write |

**currentView 变更点**：新增 `bible` / `stats` / `ideas`；导航高亮映射：`bible/stats → write`，`ideas → shelf`。

### 0.5.7 前端实施顺序（与后端里程碑对应）

| 步骤 | 内容 | 双端 |
|---|---|---|
| 1 | 新增 3 个视图骨架（bible/stats/ideas + currentView 扩展 + 导航高亮） | 共享 |
| 2 | 搜索面板（PC 居中版先做，随后安卓全屏版） | 分端 |
| 3 | 写作页 wb-actions 工具栏（PC 平铺 + 安卓「⋯」折叠） + 版本历史弹层 | 分端 |
| 4 | 正文选中工具条（PC 顶部浮动 / 安卓底部浮动） | 分端 |
| 5 | 世界圣经视图（Tab + CRUD + 关系图） | 共享 + 分端布局 |
| 6 | 统计仪表盘（4 卡 + SVG + 柱状 + 目标） | 共享 + 分端布局 |
| 7 | 灵感箱（卡片 + tag + 素材库 + 用它创作） | 共享 + 分端布局 |
| 8 | 设定扫描面板 | 共享 |
| 9 | CDP 双端验证（PC 1440px + 安卓 390px）+ 同步 + APK | 双端 |

---

## 1. 大纲可视化总览 + 拖拽重组（P0）

### 1.1 目标

- 树状总览：卷 → 章 → 摘要（title + summary + 冲突强度标签）
- 章节同卷换序、跨卷移动（拖拽）
- 自动重编号（复用现有 `renumberChapters` 逻辑）
- 三幕结构色块提示（建置/对抗/解决）

### 1.2 数据模型（无新增文件，复用 plan.json）

`plan.json` 结构不变。拖拽结果 = 修改 `outline.volumes[].chapters[]` 数组顺序 + `number` 字段重排，通过现有 `PUT /api/novels/{title}` 保存。

**新增派生视图（只读，不持久化）**：三幕归属由 `vol.act` 字段（`first_act/second_act/third_act` 或中文）推导，缺省时按卷序估算。

### 1.3 API（新增 2 个端点，进 `outline.py`）

```python
# backend/api/routers/outline.py

@router.post("/api/novels/{novel_id}/outline/reorder")
async def reorder_outline(novel_id: str, req: ReorderRequest, request: Request):
    """保存拖拽后的卷/章顺序。req = {volumes: [{vol_index, chapters: [chapter_num顺序]}]}
    服务端校验：章节数不变、每章存在 → 重排 chapters 数组 + 重编号 → atomic_write plan.json
    返回：{status, renumbered: [{old, new}], total_chapters}
    """

@router.get("/api/novels/{novel_id}/outline/overview")
async def outline_overview(novel_id: str, request: Request):
    """轻量总览（前端拖拽面板用）：卷+章+摘要+冲突强度+三幕色块。
    返回 outline.volumes 的裁剪视图（去掉 beats 大字段，减少传输）。
    """
```

**ReorderRequest 校验规则**：
- 每卷章节数是多重集合（相同集合校验，允许顺序变化）
- 跨卷移动 = 源卷移除 + 目标卷插入 + 全卷重编号
- 冲突：与生成中章节（存在 `.generating_XXXX.lock`）冲突时返回 409 拒绝

### 1.4 前端组件（追加到 `web/index.html`）

- **入口**：大纲预览卡顶部新增「📐 结构总览」按钮 → 打开 `outlineView` 模态/抽屉
- **组件**：
  - `OutlineTree`：递归渲染卷→章，章卡片显示 `title · summary(截断) · 冲突强度徽章(1-5) · 三幕色块`
  - 拖拽：**优先原生 HTML5 drag API**（无新依赖，与单文件架构一致）。同卷换序用 `dragstart/dragover/drop` 移动数组项；跨卷移动用 `dataTransfer` 携带章节 num + 源卷 index，drop 到目标卷
  - 保存按钮：收集最终数组顺序 → `POST reorder` → 成功后刷新 `planResult` + 调用现有 `renumberChapters` 前端逻辑同步
- **JS 函数**（新增，注册进 return）：
  - `openOutlineOverview()` / `closeOutlineOverview()`
  - `onChapterDragStart(e, volIdx, chIdx)` / `onChapterDrop(e, targetVolIdx)`
  - `saveOutlineOrder()`

### 1.5 测试

- 后端：`tests/test_outline_reorder.py` — 同卷换序 / 跨卷移动 / 重编号连续性 / 章节数不变校验 / 生成中锁冲突 409
- 前端：CDP 拖拽模拟（dragstart/drop 事件派发）验证数组顺序 + 保存请求体

---

## 2. 章节级精细化操作（P0）

### 2.1 目标

| 操作 | 说明 | AI 或规则 |
|---|---|---|
| 续写 | 从章末继续，保留已有内容 | AI |
| 扩写 | 选中段落 → 丰富细节 | AI |
| 精简 | 选中段落 → 压缩 | AI |
| 局部重写 | 选中段落 → 按意见重写 | AI |
| 拆分 | 一章按场景拆两章 | AI（找拆分点）+ 规则（重编号） |
| 合并 | 两章合并为一章 | 规则（拼接 + 去重标题） |

### 2.2 API（新增 1 个端点，进 `novels.py`；段落定位复用现有章节读取）

```python
# backend/api/routers/novels.py

@router.post("/api/novels/{novel_id}/chapters/{chapter_num}/surgical")
async def surgical_edit(novel_id: str, chapter_num: int, req: SurgicalEditRequest, request: Request):
    """章节精细化操作。
    req = {op: "continue"|"expand"|"condense"|"rewrite"|"split"|"merge",
           selection: {start, end}  # 选中的段落范围（可选，continue/split 忽略）
           instruction: str        # rewrite/expand 的用户意见
           merge_with: int         # merge 操作的目标章号}
    流程：
    1. 读取 chapter_{num:04d}.md 全文
    2. 按 op 分支调用 writer._create / 规则拼接
    3. 结果写回章节文件（先备份旧版到 .versions/）
    4. 若 split：新章写入 chapter_{num+1}.md，后续章节文件重命名 + plan.json 重编号
    5. 返回 {status, content, new_chapters: [...]}
    """
```

**各 op 的 prompt 设计**（复用 `writer.py` 的 `_create`，temperature 0.7，max_tokens 按目标）：

| op | user prompt 要点 |
|---|---|
| continue | "以下是第X章现有内容（最后500字）。请从断点自然续写，保持视角/风格，直到完成本章目标字数" |
| expand | "以下是选中段落。请扩写：补充感官细节、动作层次、心理活动，不改变情节走向，控制在 2-3 倍长度" |
| condense | "以下是选中段落。请压缩至 40-60% 长度，保留所有关键信息与情绪节点" |
| rewrite | "以下是选中段落。按此意见重写：{instruction}" |

### 2.3 版本历史（配套 P0）

**存储**：每章目录下 `.versions/chapter_{num:04d}.v{1..5}.md`（最多 5 版，超出滚动删除最旧）。

**API**：
```python
GET  /api/novels/{novel_id}/chapters/{chapter_num}/versions    # 版本列表
POST /api/novels/{novel_id}/chapters/{chapter_num}/versions/rollback   # {to_version} → 恢复
```

**写入时机**：任何修改章节内容的前端操作（生成/续写/扩写/精简/重写/手动编辑）前，先快照当前版。

### 2.4 前端

> **入口见 §0.5.4-B（写作页 wb-actions 工具栏）**；版本历史弹层见 §0.5.4-B，正文选中工具条见 §0.5.4-B。

- 正文区选中文字 → 悬浮工具条：「扩写 / 精简 / 重写 / 续写」（选中不同段落显示不同按钮）
- 章节卡 / 正文顶部：「版本历史」按钮 → 版本列表 + 当前版标记 + 回滚
- 新函数：`surgicalEdit(op)`, `openVersions(ch)`, `rollbackVersion(v)`

### 2.5 测试

- 后端：`tests/test_surgical_edit.py` — 各 op 的 mock LLM 返回 → 文件写入正确 / split 重编号 / 版本快照滚动
- 回归：章节读取接口不受影响

---

## 3. 全文检索（P0）

### 3.1 目标

全书关键词搜索（章节正文 + 大纲 + 角色卡），结果按章节分组，点击跳转。

### 3.2 API

```python
# backend/api/routers/novels.py

@router.get("/api/novels/{novel_id}/search")
async def search_novel(novel_id: str, q: str, request: Request, limit: int = 50):
    """全文搜索。
    遍历：chapters/*.md（正文）、plan.json（大纲/设定）、character_bible.json（角色）
    匹配：大小写不敏感子串（中文直接子串；英文/数字支持词边界）
    返回：{results: [{source: "chapter"|"outline"|"bible", chapter_num, title, snippet, line}]}
    snippet = 命中句前后各 40 字符
    """
```

**性能**：单书章节数 ≤ 数百，直接线性扫描可行（每文件 < 100KB）。不做索引，量级不需要。`limit` 默认 50，超限提示"结果过多，请精确关键词"。

### 3.3 前端

> **入口见 §0.5.4-C（全局搜索 overlay）**；触发位置：书架头部 + 写作页 wb-actions + 底部导航兜底（移动端书架页头部）。

- 书架卡/写作页工具栏新增 🔍 图标 → 搜索面板（输入框 + 结果列表）
- 结果项：章号 + 标题 + 高亮 snippet → 点击 → `openNovel` + `writing.chapter=命中章` + 定位到行
- 新函数：`searchNovel(q)`, `jumpToSearchResult(r)`

### 3.4 测试

- 后端：`tests/test_search.py` — 中文/英文/大小写/命中多章/无结果/limit 截断
- 前端：CDP 输入关键词 → 结果渲染 → 点击跳转章节

---

## 4. 世界圣经管理界面（P1）

### 4.1 目标

集中管理：人物档案 / 地点档案 / 专有名词表 / 人物关系图。

### 4.2 数据模型（新增 `bible.json`，与 character_bible.json 共存）

```json
{
  "schema_version": 1,
  "characters": {          // 人物档案（从 character_bible 合并增强）
    "林晚": {
      "name": "林晚", "identity": "设计师", "age": 30,
      "appearance": "", "personality": {"surface": "", "true_self": "", "flaw": ""},
      "motivation": {"want": "", "need": ""}, "arc": "",
      "first_appear_chapter": 1, "last_appear_chapter": 0,
      "notes": "人工补充备注"
    }
  },
  "locations": {           // 地点档案
    "云顶集团": {"description": "", "first_appear_chapter": 1, "note": ""}
  },
  "terms": {               // 专有名词表（功法/组织/物品）
    "反系统屏蔽": {"category": "技术/能力", "description": "", "first_appear_chapter": 2}
  },
  "relations": [           // 人物关系
    {"from": "林晚", "to": "陈默", "type": "敌对/前任", "note": "被背叛", "changed_chapter": 1}
  ]
}
```

**与 character_bible.json 的关系**：bible.json 是**人工可编辑的超集**；character_profiler AI 生成结果仍写 character_bible.json，bible.json 的 `characters` 优先展示（人工编辑覆盖 AI）。首次打开世界圣经时，从 character_bible + plan.json 自动合并初始化（迁移脚本）。

### 4.3 API（新增 domain router `bible.py`）

```python
GET  /api/novels/{novel_id}/bible                     # 全量读取
PUT  /api/novels/{novel_id}/bible                     # 全量保存（前端整份提交，简单可靠）
POST /api/novels/{novel_id}/bible/terms/extract       # AI 提取专有名词（正文扫描 → LLM 分类 → 返回候选）
POST /api/novels/{novel_id}/bible/relations/render    # 关系图数据（前端直接画，也可后端返回坐标）
```

### 4.4 前端

> **入口见 §0.5.4-D（bible-view 视图）**；触发位置：书卡 tile-action「📖」+ 写作页 wb-actions「📖」。

- 新视图 `bible-view`（currentView 扩展）：Tab 切换「人物 / 地点 / 名词 / 关系图」
- 人物/地点/名词：卡片网格 + 编辑表单（内联）
- 关系图：**Canvas 2D 手绘**（无图库依赖）：节点 = 人物，边 = relation.type 着色（盟友绿/敌对红/暧昧粉/导师蓝），力导向简化布局（固定半径 + 按关系数调整）
- 名词提取按钮 → 弹候选列表 → 勾选确认 → 入表
- 新函数：`loadBible()`, `saveBible()`, `extractTerms()`, `drawRelationGraph()`

### 4.5 测试

- 后端：`tests/test_bible.py` — CRUD / 合并初始化 / 名词提取 mock / schema 版本
- 前端：CDP 新建/编辑条目 → 保存 → 刷新保留

---

## 5. 写作统计仪表盘（P1）

### 5.1 目标

全书进度 + 每日字数趋势 + 每章分布 + 目标追踪 + AI 成本。

### 5.2 数据来源（无新存储，复用现有文件）

| 指标 | 来源 |
|---|---|
| 总字数 / 每章字数 | 遍历 `chapters/*.md` |
| 章节完成数 | `state.json → completed_chapters` |
| 生成时间/成本 | `global_state.json → summaries` 的写入时间戳（若无，从文件 mtime 近似） |
| 每日字数 | 章节文件 mtime 聚合（按天） |
| 目标 | `plan.json._meta.goals`（新增可选字段，前端可编辑） |

### 5.3 API

```python
# backend/api/routers/novels.py

@router.get("/api/novels/{novel_id}/stats")
async def novel_stats(novel_id: str, request: Request):
    """聚合统计：{total_chars, total_chapters, avg_chars_per_chapter,
       daily: [{date, chars}], per_chapter: [{num, chars}],
       goal: {target_chars, target_chapters, started_at, deadline}}
    """
```

### 5.4 前端

> **入口见 §0.5.4-E（stats-view 视图）**；触发位置：书卡 tile-action「📊」+ 写作页 wb-actions「📊」。

- 新视图 `stats-view`（currentView 扩展）：4 统计卡 + SVG 趋势 + CSS 柱状 + 目标设置
- 图表：**纯 CSS/SVG 手绘**（柱状图 = div 高度；趋势线 = SVG polyline），不引图表库
- 每日目标：输入框 + 保存到 `plan._meta.goals`（走 PUT /api/novels）
- 新函数：`loadStats()`, `saveGoal()`

### 5.5 测试

- 后端：`tests/test_stats.py` — 字数统计 / mtime 聚合 / goals 读写
- 前端：CDP 图表 DOM 断言

---

## 6. 导出扩展 + 整书备份（P2）

### 6.1 现状确认

- TXT ✓、**EPUB 已实现**（novels.py:509）
- **缺**：Markdown 导出（创作者本地整理用）、整书备份打包

### 6.2 API

```python
# backend/api/routers/novels.py（扩展）

GET /api/novels/{novel_id}/export?fmt=markdown   # 新增 markdown 分支（复用现有 txt 组装逻辑 + 加 ## 标题）
GET /api/novels/{novel_id}/backup                # 整书备份：zip{plan.json, character_bible.json,
                                                 #   storygraph.json, foreshadowing.json, chapters/*}
                                                 #   + 还原说明 README.txt（文件清单 + 放置路径）
POST /api/novels/restore                        # 上传 zip → 校验目录结构 → 解压到 novels/{title}/
```

**安全**：restore 走 `_validate_novel_id` + zip 路径穿越防护（拒绝 `../`），文件大小上限。

### 6.3 前端

- 导出视图（export-view）每本书增加「MD」「备份」按钮
- 「还原备份」：文件选择 → 上传 → 刷新书架
- 新函数：`exportMarkdown(n)`, `backupNovel(n)`, `restoreBackup()`

### 6.4 测试

- 后端：`tests/test_backup.py` — zip 内容完整 / 还原目录结构 / 路径穿越拒绝 / 大小上限

---

## 7. 灵感收集箱 + 素材库（P2）

### 7.1 数据模型（新增 `ideas.json`，全局共享，存 `NOVELS_DIR/ideas.json`）

```json
{
  "ideas": [
    {"id": "i_001", "text": "退休程序员穿越修真界开飞剑快递", "tags": ["穿越", "科幻修真"],
     "created_at": "2026-08-15T10:00:00", "source": "manual"}
  ],
  "materials": [   // 素材库
    {"id": "m_001", "category": "桥段", "title": "背叛", "content": "……", "tags": []}
  ]
}
```

### 7.2 API

```python
# backend/api/routers/ideas.py（新 router）

GET  /api/ideas                     # 灵感列表（可按 tag 过滤）
POST /api/ideas                     # 新增灵感 {text, tags}
DELETE /api/ideas/{id}
GET  /api/ideas/materials?cat=      # 素材库列表
POST /api/ideas/materials           # 新增素材
POST /api/ideas/{id}/to-novel       # 用灵感开新书：预填创建表单（前端跳转 create 视图并填入）
```

### 7.3 前端

> **入口见 §0.5.4-F（ideas-view 视图）**；触发位置：书架页头部「💡 灵感」+ 开卷页左上角。

- 书架页新增「💡 灵感」入口 → ideas-view（灵感卡片墙 + 素材 Tab）
- 卡片操作：编辑标签 / 删除 / 「用它创作」→ openCreate + form.inspiration=text
- 新函数：`loadIdeas()`, `addIdea()`, `deleteIdea()`, `ideaToNovel(i)`

### 7.4 测试

- 后端：`tests/test_ideas.py` — CRUD / tag 过滤 / to-novel 预填
- 前端：CDP 增删改 + 跳转创建

---

## 8. 设定冲突 AI 扫描（P2）

### 8.1 目标

跨章节扫描同名实体属性矛盾（年龄/关系/地点状态/时间线），输出冲突报告。

### 8.2 设计（复用 consistency_validator）

```python
# backend/core/settings_scanner.py（新模块）

class SettingsScanner:
    """跨章设定一致性扫描。"""
    def scan(self, novel_id: str, plan: dict) -> dict:
        """1. 读取全部已写章节文本
           2. 提取实体（角色/地点/组织/时间词）—— 用 bible.json 的名单 + 正则
           3. 对每对章节检测同名实体属性矛盾（LLM 判断，batch 化）
           4. 返回 {conflicts: [{entity, chapter_a, chapter_b, claim_a, claim_b, type}]}
        """
    def scan_batch(self, novel_id: str, chapters: list) -> dict:  # 批量（每批5章）
```

**API**：
```python
POST /api/novels/{novel_id}/scan-consistency    # 触发全量扫描（异步？同步 10-30s，前端 loading）
```

**前端**：写作页工具栏「🔬 一致性」→ 扫描结果列表（实体 + 章节对 + 原句）→ 点击定位章节。
> **入口见 §0.5.4-G（设定扫描弹层）**；触发位置：写作页 wb-actions「🔬」。

### 8.3 测试

- 后端：`tests/test_settings_scanner.py` — mock LLM 返回矛盾对 → 报告格式 / 无矛盾 / 章节对去重

---

## 9. 实施计划

### 9.1 里程碑（按确认的 6 项 + 前端 §0.5.7 顺序）

| 阶段 | 后端内容 | 前端内容（对应 §0.5.7） | 预估 |
|---|---|---|---|
| M1 | 章节精细操作 API + 版本历史 + 全文检索 API | 视图骨架 + 搜索面板 + wb-actions 工具栏 + 选中工具条 | 3-4 天 |
| M2 | 世界圣经（bible.json + CRUD + 提取） | bible-view（4 Tab + 关系图） | 2-3 天 |
| M3 | 统计仪表盘（/stats） | stats-view（4卡 + SVG + 目标） | 1-2 天 |
| M4 | 灵感箱（ideas.json + CRUD） | ideas-view（卡片 + tag + 素材） | 1-2 天 |
| M5 | 设定扫描（settings_scanner） | 扫描弹层 | 1-2 天 |

### 9.2 每个功能的落地清单（统一模板）

每完成一个功能，需同步：
1. `backend/` 代码 + `tests/test_<feature>.py`
2. `tools/sync_android_core.py`（后端副本）
3. `web/index.html` 视图/函数 + `tools/sync_frontend.py`
4. CDP 真机验证（PC + 移动端）
5. 全量测试 `pytest tests backend/tests`
6. `docs/` 更新（本设计文档勾选完成项）
7. APK 构建 + 飞书分发（如用户需要）

### 9.3 风险与缓解

| 风险 | 缓解 |
|---|---|
| 章节拆分重命名文件 | 拆分走"新章写入末尾 → 重编号映射表"而非原地改名，避免并发读写竞态 |
| bible.json 与 character_bible.json 双写 | bible 为人工层，AI 生成层只读；合并函数单一入口 |
| 全文检索性能 | 单书 ≤ 数百章线性扫描够用；如超限再加简单倒排缓存 |
| 移动端 5 项导航溢出 | 底部导航保持 5 项不变，新功能全部从上下文（书卡/写作页工具栏/弹层）进入 |
| 版本历史磁盘占用 | 每章最多 5 版，超限滚动删除；`.versions/` 加入备份与 gitignore |

---

## 10. 验收标准（整体）

- [ ] **章节精细操作**：续写/扩写/精简/重写保留未选中内容；版本可回滚（≥1 版）
- [ ] **全文检索**：中文/英文命中多章，结果按章节分组，点击定位到对应章节
- [ ] **世界圣经**：人物/地点/名词增删改持久化；关系图渲染正确；名词 AI 提取可勾选入表
- [ ] **统计仪表盘**：4 项统计与真实文件字数一致；目标保存生效
- [ ] **灵感箱**：增删改 + tag 过滤 + 「用它创作」预填创建表单
- [ ] **设定扫描**：冲突报告展示实体 + 章节对 + 原句，点击可定位
- [ ] 前端入口全部可用：书架工具条 / 写作页工具栏 / 书卡快捷操作 / 弹层，移动端无导航溢出
- [ ] 全部通过 CI（语法/安全/副本一致/全量测试）
- [ ] 三副本同步，Android APK 验证通过

---

## 附录 A：暂缓项（v1.0 已设计，保留备查）

- §1 大纲可视化总览 + 拖拽重组（P0，原设计见 v1.0 文档 §1）
- §6 导出扩展（Markdown）+ 整书备份打包（P2，原设计见 v1.0 文档 §6；注意 EPUB 导出已实现，仅缺 Markdown 与 zip 备份）

---

## 实施记录 v2.7.6：目标字数自由填写（字数放开）

> 需求：不需要单独的超短篇模式——把字数改为自由输入（去掉预设下拉），前端提示下限，
> 后端放开字数限制。超短篇（几百字）与长篇（几万字）均可。

### 限制链（放开前）

| 位置 | 限制 | 后果 |
|---|---|---|
| API `CreateNovelRequest` | `target_words ge=1000` | 填 <1000 字被拒 |
| planner | `total_chapters = max(8, tw//2000)` | 800 字被规划成 **8 章×2000 字=16000 字**（超出意图 20 倍） |
| planner | 每章固定 2000 字 | 单章 target_words 与总字数脱节 |
| writer `_check_truncation` | `min_acceptable = max(500, 50%)` | 300 字目标生成 300 字被判"长度不足"→ 无限续写 |
| quality_gate | `minimum = max(120, 25%)` | 100 字目标被 120 硬底卡死 |
| atomic beat | 模板固定 300-450 字/拍 | 800 字目标被 6 拍撑到 2500+ |

### 修复内容

| 项 | 文件 | 说明 |
|---|---|---|
| 前端控件 | `web/index.html` | 目标字数 select → number input（min=100），提示"最少 100 字"；默认值 50万→5万 |
| API 下限 | `backend/api/routers/novels.py` | `ge=1000 → ge=100` |
| 章节规划 | `backend/core/planner.py` | 提取 `compute_chapter_plan` 纯函数；去掉"最少8章"，`总章数 = tw//2000`（最少1章），`每章字数 = tw//章数`；长篇（≥2000字/章）行为不变 |
| 截断下限 | `backend/core/writer.py` | `min_acceptable = max(500→40, 50%)`；正常目标(≥1000字)时 50%≥500 行为不变 |
| 质量门槛 | `backend/core/quality_gate.py` | `minimum = max(120→50, 25%)`；正常目标不变 |
| atomic beat 缩放 | `backend/core/beat_decomposer.py` | 目标 <1500 字时按比例缩放各 beat 字数（防模板撑爆）；长篇模板不变 |
| atomic max_tokens | `backend/core/atomic_writer.py` | 下限 300→80，配合缩放后的短拍 |

### 验证

- 新增 `tests/test_fix_v276.py`（17 项）：API 下限边界、compute_chapter_plan（800→1章/800字、5000→2章/2500、100000→50章/2000 不变）、截断下限、quality_gate 下限、beat 缩放
- 全量测试：**484 passed + 1 skipped + 33 subtests**（基线 467 + 新增 17）
- 真实 LLM 验证：创建 target_words=800 超短篇 → 规划 1 章/800 字 → 生成第 1 章字数落在合理范围
- CDP：字数控件为 number input（min=100）、提示正常、0 控制台错误
- android 三副本同步（core/api + web）

---

## 实施记录 v2.7.5：摘要完整性修复（长文隐患复盘）

> 背景：复盘长篇生成隐患时发现——**原子化模式（前端默认开启）生成路径完全没有摘要逻辑**，
> 导致大量章节缺失 `summaries` 条目，L2c 已发生事件清单 fallback 到大纲「计划」内容冒充
> 「已发生」，长文防重复机制依赖失真数据。

### 根因（实测证据链）

1. 前端 `web/index.html` `useAtomicMode = ref(true)` **默认原子化** → 单章生成走 `/generate/atomic`
2. `atomic_generate_chapter_stream` 末尾只有 final_quality + done，**无 summarize_chapter、无 check_and_compress**
   （摘要+压缩只在 `generate_chapter_stream` 标准路径）
3. 实测：熵破苍穹第 11 章完整（3583 字）但无摘要；替身的告别 1-6 章永久缺；万界篡改者 1-4 缺；天命骗局全缺
4. 无任何补回机制（历史章节缺摘要永远缺）

### 修复内容

| 项 | 文件 | 说明 |
|---|---|---|
| 懒补机制 | `backend/core/mixins/generation.py` `_backfill_missing_summaries` | 生成前补「事件清单窗口（最近 19 章）内缺失摘要」的章节；最新优先、上限 5 章/次、正文 <200 字符跳过、失败降级不阻塞 |
| 两条生成路径接入 | 同上（generate_chapter_stream + atomic_generate_chapter_stream 开头） | 锁内、build_writer_context 之前调用，yield status 提示 |
| 原子路径补摘要+压缩 | 同上（atomic 末尾 final_quality 与 done 之间） | 与标准路径对齐：单章摘要 + check_and_compress（每 8 章间隔） |
| fallback 语义修正 | `backend/core/shared_memory.py` L2c | 缺摘要 fallback 大纲时标注「[大纲计划·未确认]」，不再冒充「已发生」 |

### 验证

- 新增 `tests/test_fix_v275.py`（8 项）：backfill 窗口/最新优先/上限/跳过已有/短内容跳过/首章跳过/异常降级；L2c fallback 标注（tempfile 最小书）
- 全量测试：**467 passed + 1 skipped + 33 subtests**（基线 311+148 + 新增 8）
- 真实 LLM 验证：替身的告别（9 章仅 7/8/9 有摘要）生成第 10 章前 backfill 补齐 1-6 章真实摘要 ✓
- CDP 前端健康：页面加载、书架渲染、0 控制台错误
- APK 2.7.5（versionCode 61）构建成功
- android 三副本已同步（sync_android_core.py）
