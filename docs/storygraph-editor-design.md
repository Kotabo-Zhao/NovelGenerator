# 剧情图谱编辑器 — 设计方案

> v2.0 | 2026-07-25 | UX 增强版：三层交互 + 方向盘面板 + 自然语言指令 + 视觉图编辑

---

## 一、动机

当前剧情图谱（StoryGraph）全程由 LLM 自动生成和维护，人对剧情走向完全没有控制力。典型痛点：

- LLM 把某个配角当主角推进，但用户想按住那条线
- 某个伏笔自动提取的回收时间不对，应该提前或推迟
- 主线紧张度一直在 5/10，没什么波澜——用户想手动调到 8
- 角色目标已经达成，但 LLM 还在写"寻找XXX"

**核心价值**：在剧情图谱面板上直接改数据 → 下一章自动按新设定生成。

---

## 二、架构总览

```
┌──────────────────────────────────────────────────┐
│              前端 剧情图谱面板                      │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌──────────┐  │
│  │概览 │ │剧情线│ │伏笔 │ │角色 │ │人物关系图 │  │
│  │     │ │ ✏️  │ │ ✏️  │ │ ✏️  │ │ 剧情线图  │  │
│  └─────┘ └──┬──┘ └──┬──┘ └──┬──┘ └──────────┘  │
│             │       │       │                    │
│        PUT/POST/DELETE 端点                       │
└─────────────┼───────┼───────┼────────────────────┘
              │       │       │
┌─────────────┼───────┼───────┼────────────────────┐
│  后端 API                                        │
│  PUT  /storygraph/threads/{id}                   │
│  POST /storygraph/threads                        │
│  PUT  /storygraph/foreshadows/{id}               │
│  PUT  /storygraph/characters/{name}              │
│  ...                                             │
│              │                                    │
│        atomic_write_json(storygraph.json)        │
└──────────────┼───────────────────────────────────┘
               │
┌──────────────┼───────────────────────────────────┐
│  写作管线 (已有，无需改动)                         │
│  _build_writer_context()                         │
│    └─ _build_storygraph_context() ← 读 storygraph │
│  analyze_and_inject() ← 读 storygraph            │
│    └─ 生成干预指令 → 注入 Writer prompt           │
└──────────────────────────────────────────────────┘
```

**关键设计决策**：编辑写入 storygraph.json 即可，写作管线零改动。因为管线的 `_build_writer_context` 和 `analyze_and_inject` 已经从 `storygraph.json` 读取数据——改文件等同于改行为。

---

## 三、数据模型（已存在，此处仅标注可编辑字段）

### 3.1 剧情线 (plot_thread)

```
thread_id: str (不可编辑)
├── name: str              — ✏️ 名称
├── type: enum             — ✏️ main_plot|subplot|character_arc|mystery
├── status: enum           — ✏️ dormant|active|advancing|climax|resolved
├── priority: int(1-5)     — ✏️ 优先级，影响排序
├── description: str       — ✏️ 描述
├── current_tension: int   — ✏️ 1-10 紧张度
├── next_planned: str      — ✏️ 下一步计划
├── characters: [str]      — ✏️ 关联角色
└── key_nodes: [{chapter, event, tension}]
                           — ✏️ 节点列表（增删改）
```

### 3.2 伏笔 (foreshadow)

```
foreshadow_id: str (不可编辑)
├── description: str           — ✏️ 描述
├── planted_chapter: int       — 只读（由系统记录）
├── planned_payoff_chapter: int — ✏️ 计划回收章节
├── status: enum               — ✏️ planted|hinted|revealed|resolved
├── importance: int(1-5)       — ✏️ 重要度
├── thread_id: str             — ✏️ 关联剧情线
└── hint_count: int            — 只读
```

### 3.3 角色快照 (char_snapshot)

```
name: str (不可编辑)
├── current_location: str    — ✏️ 当前位置
├── current_emotion: str     — ✏️ 当前情绪
├── current_power_level: str — ✏️ 实力等级
├── status_effects: [str]    — ✏️ 异常状态
├── active_goals: [str]      — ✏️ 当前目标
└── known_secrets: [str]     — ✏️ 已知秘密
```

---

## 四、API 详细设计

### 4.1 基础约定

- Content-Type: `application/json`
- 所有写操作使用 `atomic_write_json()` 保证原子性
- 先读 storygraph.json → 修改 → 写回
- 错误返回 `{"error": "message"}` + HTTP 4xx/5xx

### 4.2 剧情线端点

#### PUT `/api/novels/{novel_id}/storygraph/threads/{thread_id}`

更新单条剧情线的可编辑字段。

**Request Body:**
```json
{
  "name": "复仇主线",
  "type": "main_plot",
  "status": "advancing",
  "priority": 5,
  "description": "主角寻找灭门真相",
  "current_tension": 8,
  "next_planned": "下一章揭露师父死因的线索",
  "characters": ["主角", "反派", "女主"],
  "key_nodes": [
    {"chapter": 1, "event": "门派被灭", "tension": 8},
    {"chapter": 3, "event": "找到线索", "tension": 7}
  ]
}
```

**Response:** `{"ok": true, "thread_id": "t1"}`

**填充策略**：只更新 Body 中包含的字段，未包含的字段保持原值（partial update）。

#### POST `/api/novels/{novel_id}/storygraph/threads`

创建新剧情线。

**Request Body:**
```json
{
  "id": "custom_thread_001",
  "name": "暗线：幕后黑手",
  "type": "mystery",
  "priority": 4,
  "description": "揭示真正的幕后操控者",
  "characters": ["反派Boss", "神秘人"]
}
```

**Response:** `{"ok": true, "thread_id": "custom_thread_001"}`

#### DELETE `/api/novels/{novel_id}/storygraph/threads/{thread_id}`

删除剧情线（软删除：status 设为 resolved，不物理删除以保留历史）。

**Response:** `{"ok": true}`

### 4.3 伏笔端点

#### PUT `/api/novels/{novel_id}/storygraph/foreshadows/{fs_id}`

**Request Body:**
```json
{
  "description": "师父的玉佩有古怪",
  "planned_payoff_chapter": 15,
  "status": "hinted",
  "importance": 4,
  "thread_id": "t1"
}
```

#### POST `/api/novels/{novel_id}/storygraph/foreshadows`

**Request Body:**
```json
{
  "id": "custom_fs_001",
  "description": "沈清许的母亲另有身份",
  "planted_chapter": 12,
  "planned_payoff_chapter": 25,
  "importance": 5,
  "thread_id": "t2"
}
```

### 4.4 角色端点

#### PUT `/api/novels/{novel_id}/storygraph/characters/{name}`

**Request Body:**
```json
{
  "current_location": "暗影殿地牢",
  "current_emotion": "绝望",
  "current_power_level": "金丹中期（被压制）",
  "status_effects": ["内力被封", "左臂骨折"],
  "active_goals": ["越狱", "找到内奸"],
  "known_secrets": ["殿主真实身份"]
}
```

### 4.5 后端实现模式（Python 伪代码）

```python
@app.put("/api/novels/{novel_id}/storygraph/threads/{thread_id}")
async def update_thread(novel_id: str, thread_id: str, body: dict):
    data = _read_novel_file(novel_id, "storygraph.json")
    
    if thread_id not in data.get("plot_threads", {}):
        raise HTTPException(404, "剧情线不存在")
    
    thread = data["plot_threads"][thread_id]
    # Partial update — 只覆盖传入的字段
    for key in ("name","type","status","priority","description",
                 "current_tension","next_planned","characters","key_nodes"):
        if key in body:
            thread[key] = body[key]
    
    data["version"] += 1
    _write_novel_file(novel_id, "storygraph.json", data)
    return {"ok": True, "thread_id": thread_id}
```

---

## 五、前端交互设计

### 5.1 编辑入口

在每个剧情线卡片 / 伏笔卡片 / 角色卡片的**右上角**增加一个 ✏️ 图标按钮。

```
┌──────────────────────────────────┐
│ 复仇主线  [主线] [P5]      ✏️   │  ← 点击进入编辑
│ ████████████░░░░ 紧张度 8/10     │
│ Ch1: 门派被灭 → Ch3: 找到线索    │
└──────────────────────────────────┘
```

### 5.2 编辑状态

点击 ✏️ 后，卡片变为编辑模式：

```
┌──────────────────────────────────┐
│ 名称: [复仇主线_______________]  │
│ 类型: [主线 ▾]                   │
│ 状态: [推进中 ▾]   优先级: [5 ▾]│
│ 紧张度: [■■■■■■■■□□] 8/10       │  ← 滑块
│ 描述:                             │
│ ┌──────────────────────────────┐ │
│ │ 主角寻找灭门真相，从线索A追踪 │ │
│ │ 到线索B...                    │ │
│ └──────────────────────────────┘ │
│ 下一步: [揭露师父死因的线索____] │
│ 关联角色: [主角] [反派] [+添加] │
│                                   │
│ 关键节点:                         │
│ Ch[1] 事件[门派被灭_________] 删 │
│ Ch[3] 事件[找到线索_________] 删 │
│ [+ 添加节点]                      │
│                                   │
│         [取消]  [保存]            │
└──────────────────────────────────┘
```

### 5.3 表单组件规范

| 字段 | 控件 | 验证 |
|:-----|:-----|:-----|
| name | text input | 必填，≤30字 |
| type | select (下拉) | 4选1 |
| status | select (下拉) | 5选1 |
| priority | number select (1-5) | 范围校验 |
| current_tension | range slider (1-10) | 范围校验 |
| description | textarea | ≤200字 |
| next_planned | text input | ≤60字 |
| characters | tag input (+添加/×删除) | 至少1个 |
| key_nodes | 行内列表（可增删） | chapter 必须数字 |

### 5.4 新增按钮

在每个子标签页的顶部工具栏增加一个 `+ 新增` 按钮：

- 剧情线 tab → `+ 新增剧情线`
- 伏笔 tab → `+ 新增伏笔`
- 角色 tab → `+ 新增角色`

点击后弹出一个简化版的新建表单（只有必填字段）。

### 5.5 保存反馈

- 保存成功：绿色 toast "已保存"，卡片恢复展示模式
- 保存失败：红色 toast 显示错误信息，保留编辑内容
- 网络异常：红色 toast "保存失败，请检查网络"
- 自动保存：暂不实现（避免误操作），以手动点保存为准

---

## 六、改动模块清单

| 文件 | 当前行数 | 预计新增 | 改动说明 |
|:-----|:-------:|:------:|:-----|
| `backend/api/server.py` | 1180 | +80 | 6 个 REST 端点 + _write_novel_file 辅助函数 |
| `backend/core/storygraph.py` | 530 | +40 | update_thread / update_foreshadow / update_char 方法 |
| `web/index.html` (CSS) | ~400 | +20 | 编辑模式样式（表单、标签、滑块） |
| `web/index.html` (Template) | ~1400 | +120 | 内联编辑表单、新增按钮、tag input |
| `web/index.html` (JS) | ~1300 | +70 | 编辑状态管理、API 调用、表单验证 |

**总计：约 +330 行**，预计开发时间 2 小时。

### 不改动的模块

| 模块 | 原因 |
|:-----|:-----|
| `shared_memory.py` | storygraph.json 由 API 直接写入，不走 SharedMemoryManager |
| `engine.py` | 写作管线已从 storygraph.json 读取，无需修改 |
| `writer.py` | 同上 |
| `storygraph_interventions.py` | 同上 |

---

## 七、预期效果

### 7.1 使用场景

**场景 A — 调紧张度**
- 用户在剧情线面板看到「主线」紧张度一直在 5/10
- 点击 ✏️ → 拖滑块到 8/10 → 保存
- 下一章 `analyze_and_inject()` 检测到紧张度偏低 → 生成 "TENSION_ADJUST" 干预指令
- Writer 收到 "本章需要升温" → 自动写冲突/揭露/反转

**场景 B — 推迟伏笔回收**
- 伏笔「师父的玉佩有古怪」计划在 Ch10 回收
- 用户觉得太早，改为 Ch18 → 保存
- `get_due_foreshadows()` 的窗口期后移，Ch10-17 不再催促回收
- Ch18 时自动进入 "FORESHADOW_DUE" 高优提醒

**场景 C — 新增暗线**
- 写了几章后，用户想加一条「朝廷内斗」的暗线
- 在剧情线 tab 点 `+ 新增` → 填名称/类型/优先级 → 保存
- 从下一章开始，`_build_storygraph_context` 会注入新线程的摘要
- Writer 上下文中出现新的剧情线，LLM 自然会在生成中提及或推进

### 7.2 影响链路总结

```
用户编辑 → API 写 storygraph.json
                    ↓
        自动影响（无需额外操作）：
        ├── L4b 剧情图谱上下文注入（_build_storygraph_context）
        │   └── 线程摘要 / 伏笔提醒 / 角色快照 → Writer 看到
        ├── 干预指令（analyze_and_inject）
        │   └── FORESHADOW_DUE / TENSION_ADJUST / THREAD_STALE → Writer 遵循
        └── 前端可视化同步更新
            └── 人物关系图 / 剧情线图 即时反映数据变化
```

---

## 八、验证方案

### 8.1 单元测试 — API 端点

| 用例 | 方法 | 预期 |
|:-----|:-----|:-----|
| 更新已存在的剧情线 | PUT thread | 200, 数据正确写入 |
| 更新不存在的剧情线 | PUT thread | 404 |
| 创建新剧情线 | POST thread | 200, 新线程出现在 storygraph.json |
| partial update 只改 priority | PUT thread {priority:5} | 其他字段不变 |
| 更新角色位置 | PUT character | 200, snap 更新 |
| 非法 priority (0 或 6) | PUT thread {priority:6} | 400 validation error |
| storygraph.json 不存在 | PUT thread | 404 |

### 8.2 集成测试 — 编辑→生成链路

| 步骤 | 操作 | 验证点 |
|:-----|:-----|:-----|
| 1 | 编辑主线紧张度 5→9 | storygraph.json 中 current_tension 变为 9 |
| 2 | 生成下一章 | `analyze_and_inject()` 日志中出现 EMOTION_BALANCE 或 TENSION_ADJUST 干预 |
| 3 | 检查章节内容 | 新章有明显的冲突/高潮场景（非平淡叙述） |
| 4 | 编辑伏笔回收时间 Ch10→Ch5 | 生成 Ch5 时日志出现 FORESHADOW_DUE |
| 5 | 新增暗线 custom_thread | 生成下一章时 _build_storygraph_context 包含新线程 |

### 8.3 前端验证

| 用例 | 操作 | 预期 |
|:-----|:-----|:-----|
| 编辑按钮显示 | 进入剧情线/伏笔/角色 tab | 每个卡片右上角有 ✏️ 按钮 |
| 内联表单 | 点击 ✏️ | 卡片变为编辑模式，字段正确回填当前值 |
| 滑块交互 | 拖动 tension slider | 数值实时更新 |
| 保存成功 | 修改字段 → 点保存 | toast "已保存"，卡片恢复展示模式，数据更新 |
| 保存失败 | 断网 → 编辑 → 保存 | toast "保存失败" |
| 取消编辑 | 修改字段 → 点取消 | 数据不变化，卡片恢复展示模式 |
| 新增线程 | 点 + 新增 → 填表 → 保存 | 列表中出现新卡片 |
| 删除线程 | 点删除 → 确认 | 线程 status 变为 resolved，从活跃列表消失 |
| 关系图同步 | 编辑角色location后切换到人物关系图 | tooltip 显示新位置 |

### 8.4 回归验证

- 所有现有功能（生成章节、剧情图谱查看、人物关系图、剧情线图）不受影响
- storygraph.json 文件格式向后兼容（新增字段不影响 LLM 提取）
- atomic_write_json 原子性保证（并发安全）

---

## 九、风险与缓解

| 风险 | 等级 | 缓解措施 |
|:-----|:----:|:-----|
| 字段类型校验不严导致 JSON 损坏 | 中 | API 层做严格字段验证（type/status enum + priority range + string length） |
| 编辑后的数据与 LLM 自动提取冲突 | 低 | LLM 提取走 `apply_extraction()`，只做增量更新不覆盖用户手动设置的值（后续可加 merge 策略） |
| 前端表单状态泄漏（切换 tab 丢失编辑） | 低 | 切换 tab 前检测是否有未保存编辑 → 弹确认框 |

---

## 十、UX 增强：从"填表单"到"说人话"

> 原设计（第五~六节）已覆盖功能实现。本章聚焦于**怎么让这个编辑器真的好用**——不是能改数据，是改得顺手。

### 10.1 核心洞察

目前的设计本质上是「JSON 编辑器套了 HTML 皮肤」——每个字段对应一个 input/select/slider。这对开发者友好，但对写作者不友好。作者的心理模型是：

| 作者想的是 | 而不是 |
|:----------|:-----|
| "这条线该升温了" | current_tension 从 5 调到 8 |
| "这个伏笔别那么早回收" | planned_payoff_chapter 从 10 改到 18 |
| "这条暗线不重要，先放着" | priority 从 4 调到 2，status 改 dormant |
| "让男女主关系在这一章冲突爆发" | 同时改感情线的 tension、status、next_planned |

**核心问题**：一个意图需要操作多个字段才能表达。

### 10.2 三层交互架构

```
Layer 1: 自然语言指令 ─→ 一句话表达意图，系统解析为结构化编辑
Layer 2: 快捷操作按钮 ─→ 一键触发预设的行为组合
Layer 3: 详细表单编辑 ─→ 精确控制每个字段（原有设计，兜底用）
```

用户 90% 的需求在 Layer 1-2 就能满足，Layer 3 只在需要精确调整时才用。

---

## 十一、"方向盘"面板设计（Layer 1+2）

### 11.1 面板位置

在剧情图谱子 tab 栏下方，新增一个可折叠的「🎛️ 方向盘」面板。

```
┌─ 子标签栏 ───────────────────────────────────────────┐
│ [📊概览] [📋剧情线] [📌伏笔] [👤角色] ...  [🎛️方向盘] │
└──────────────────────────────────────────────────────┘
┌─ 方向盘面板 ──────────────────────────────────────────┐
│                                                        │
│  💬 我想... ┌────────────────────────────────────┐    │
│             │ 让复仇主线在下5章内升温到高潮       │    │
│             └────────────────────────────────────┘    │
│  [执行] [展开快捷操作 ▼]                               │
│                                                        │
└────────────────────────────────────────────────────────┘
```

### 11.2 自然语言指令解析器

输入一句话，前端轻量解析拆成结构化操作：

| 输入示例 | 解析结果 |
|:---------|:---------|
| "让复仇主线升温" | tension+2, status→advancing |
| "暂停感情线" | status→dormant |
| "复仇主线优先级提到最高" | priority→5 |
| "把玉佩秘密推迟到25章回收" | planned_payoff→25 |
| "回收玉佩秘密" | status→resolved |
| "沈清许现在在暗影殿地牢" | location→暗影殿地牢 |
| "主角的复仇目标已完成" | 从 goals 中移除"复仇" |

**解析策略**：两段式
1. **前端正则匹配**（毫秒级）：覆盖 80% 常用句式，关键词+参数提取+模糊匹配名称
2. **降级到 LLM 解析**（点击"AI解析"按钮）：复杂句式交给轻量 LLM（`temperature=0.1, max_tokens=200`），返回结构化 JSON

```javascript
function parseCommand(cmd, threads, foreshadows, characters) {
  const patterns = [
    { re: /让?(.+?)(?:在.*?内)?升温/, fn: (m) => ({ type:'thread', target:m[1], action:'heat_up' }) },
    { re: /暂停(.+)/, fn: (m) => ({ type:'thread', target:m[1], action:'pause' }) },
    { re: /(.+?)优先级提[到为](最高|\d)/, fn: (m) => ({ type:'thread', target:m[1], action:'set_priority', value: m[2]==='最高'?5:parseInt(m[2]) }) },
    { re: /把?(.+?)推迟到?第?(\d+).*?回收/, fn: (m) => ({ type:'foreshadow', target:m[1], action:'delay', ch:parseInt(m[2]) }) },
    { re: /回收(.+)/, fn: (m) => ({ type:'foreshadow', target:m[1], action:'resolve' }) },
    { re: /(.+?)现在在(.+)/, fn: (m) => ({ type:'character', target:m[1], action:'set_location', value:m[2] }) },
  ];
  // fuzzy match against existing thread/character/foreshadow names
}
```

### 11.3 快捷操作面板（展开后）

```
┌─ 方向盘面板（展开状态）────────────────────────────────┐
│                                                        │
│  ── 📋 剧情线 ────────────────────────────────────── │
│                                                        │
│  🔴 复仇主线  P5  推进中  ████████░░ 8/10              │
│  [🔥升温] [⏸️暂停] [✅完结] [⬆️提优] [⬇️降优] [✏️详细]│
│                                                        │
│  🟡 感情线    P2  进行中  ██░░░░░░░░ 3/10              │
│  [🔥升温] [⏸️暂停] [⬆️提优] [✏️详细]                  │
│                                                        │
│  [+ 新增剧情线]                                        │
│                                                        │
│  ── 📌 伏笔 ──────────────────────────────────────── │
│                                                        │
│  ⏰ 玉佩秘密  Ch3→Ch12 (还有2章)  ⭐P4                  │
│  [✅现在回收] [⏪推迟5章] [⏩提前到Ch8]                │
│                                                        │
│  ⏰ 大师兄身份  Ch3→Ch8 (已超期!)  ⭐P3                 │
│  [✅立即回收] [⏪推迟3章]                               │
│                                                        │
│  [+ 新增伏笔]                                          │
│                                                        │
│  ── 👤 角色 ──────────────────────────────────────── │
│                                                        │
│  👤 沈清许  📍东宫  😢悲伤  🎯越狱·保护妹妹            │
│  [📍改位置] [😶改情绪] [🎯改目标] [✏️全部]            │
│                                                        │
│  👤 萧景桓  📍靖王府  😡愤怒  🎯复仇                    │
│  [📍改位置] [😶改情绪] [✏️全部]                        │
│                                                        │
└────────────────────────────────────────────────────────┘
```

### 11.4 快捷操作行为映射

| 按钮 | 剧情线效果 | 伏笔效果 |
|:-----|:---------|:-------|
| 🔥 升温 | tension += 2 (cap 10), status→advancing | — |
| ⏸️ 暂停 | status = dormant | — |
| ✅ 完结/回收 | status = resolved | status = resolved |
| ⬆️ 提优 / ⬇️ 降优 | priority ± 1 (range 1-5) | importance ± 1 |
| ⏪ 推迟N章 | — | planned_payoff += N |
| ⏩ 提前到ChN | — | planned_payoff = N |

每个操作执行后：toast 反馈 + 数据即时刷新 + 支持撤销最近 1 个操作。

---

## 十二、视觉图上直接编辑（Layer 2 增强）

在人物关系图和剧情线图上增加编辑能力：

### 12.1 剧情线图交互

```
- 拖拽事件节点左右移动 → 改变事件所在章节（500ms 防抖后自动保存）
- 拖拽节点上下移动 → 改变事件紧张度（Y轴映射 1-10）
- 右键泳道空白处 → 「在此处新增事件」
- 拖拽过程中显示浮层：章节号 / 紧张度数值
```

### 12.2 人物关系图交互

```
- 双击角色节点 → 弹出角色快速编辑浮窗
- 右键节点 → 菜单「编辑角色」「查看出场记录」「高亮关联线」
- 右键空白处 → 「新增角色」
- 点击连线 → 查看关系详情 + 「编辑关系」按钮
```

---

## 十三、智能建议（Layer 0）

不依赖用户主动操作——系统自动分析剧情状态，在方向盘面板顶部显示可执行建议：

```
┌─ 💡 智能建议 ────────────────────────────────────────┐
│                                                       │
│  ⚠️  复仇主线已连续6章无进展，建议升温  [执行]        │
│  ⚠️  伏笔"玉佩秘密"将在2章后到期  [推迟] [现在回收]   │
│  💡  感情线紧张度持续偏低，可考虑制造冲突  [升温]      │
│  💡  角色"大长老"已8章未出场  [安排出场]              │
│                                                       │
└───────────────────────────────────────────────────────┘
```

这些建议**直接来自已有的干预指令系统**（`storygraph_interventions.py`：THREAD_STALE / FORESHADOW_DUE / EMOTION_BALANCE / CHARACTER_ABSENT 等），只是换个形式——从被动注入 Writer prompt 变成主动推荐给用户。用户一键执行。

---

## 十四、更新后的改动清单

| 文件 | 估计 | 说明 |
|:-----|:----:|:-----|
| `backend/api/server.py` | +120 行 | 快捷操作端点 + 自然语言解析端点 |
| `backend/core/storygraph.py` | +40 行 | update 方法 |
| `backend/core/command_parser.py` | +80 行 | **新文件**: 自然语言→结构化编辑 |
| `web/index.html` (CSS) | +40 行 | 方向盘面板 + 浮窗 + 建议卡片 |
| `web/index.html` (Template) | +250 行 | 方向盘面板 + 视觉图拖拽 |
| `web/index.html` (JS) | +180 行 | 指令解析 + 快捷操作 + 撤销 + 拖拽 |

**总计约 +710 行**，分 4 个 Phase 实施。

---

## 十五、分阶段实施

### Phase 1 — 基础编辑（2h）
- 后端 PUT/POST API
- 前端内联编辑表单
- toast 反馈

### Phase 2 — 方向盘面板（2h）
- 🎛️ 方向盘可折叠面板
- 快捷操作按钮（升温/暂停/回收）
- 前端正则指令解析器
- 智能建议卡片

### Phase 3 — 视觉编辑（1.5h）
- 剧情线图拖拽编辑
- 人物关系图双击编辑
- 撤销功能

### Phase 4 — LLM 增强（1h）
- 自然语言 LLM 解析降级
- 编辑历史记录
