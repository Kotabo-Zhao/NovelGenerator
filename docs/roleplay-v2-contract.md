# Roleplay v2 — 指令 / 世界书 / 前端重构契约

本文件是 backend 与 `web/roleplay.html` 的接口契约。backend 正在并行实现，
字段与事件名以本文件为准。

## 1. 斜杠指令（SillyTavern 风格）

输入框以 `/` 开头即为指令。UI 应提供指令面板/自动补全与帮助。

| 指令 | 别名 | 行为 |
|---|---|---|
| `/help` | `/h` | 返回指令列表文本（`command_result`） |
| `/say 文本` | `/s` `/send` | 作为普通对话发送并生成回复 |
| `/act 动作描述` | `/do` `/action` `/me` | 玩家动作：记录为 `kind=action`，生成「动作结果 + 角色反应」 |
| `/nar 旁白` | `/narrate` `/sys` | 追加一条旁白消息（assistant, kind=narration），不调用模型 |
| `/ooc 内容` | `/out` | 出戏发言：记录为 system/ooc，并生成角色回应 |
| `/roll NdM[+K]` | `/dice` `/r` | 掷骰（如 `/roll 1d20+3`、`/roll 2d6`），记录为 kind=roll，不调用模型 |
| `/continue` | `/cont` | 让角色继续上一条回复（不新增用户消息） |
| `/regen` | — | 重新生成最后一条回复（新增一个 swipe 版本） |
| `/swipe [next|prev|new]` | — | 切换已有版本 / `new` 生成新版本 |
| `/impersonate` | `/imp` | 让模型替你写一句台词，返回文本放入输入框（不发送） |
| `/note 文本` | — | 设置作者注释（author's note），返回 `command_result` |
| `/world` | — | 返回当前世界书条目数与启用数 |

未知指令返回 `command_result` 提示并给出 `/help`，不发送给模型。

## 2. 消息结构

```json
{
  "id": "uuid",
  "role": "user|assistant|system",
  "kind": "dialogue|action|narration|ooc|roll|system",
  "speaker": "顾尘|你|旁白|系统",
  "content": "...",
  "ts": 1730000000,
  "variants": ["版本1", "版本2"],
  "variant_index": 0
}
```

- assistant 消息可有 `variants`；`content` 等于当前选中版本。
- 旧消息没有这些字段时前端按 `kind=dialogue` 兼容。
- 动作消息显示为「动作」样式；旁白居中弱化；掷骰显示骰子徽章。

## 3. SSE 事件（`/chat/send`、`/chat/regenerate`、`/chat/swipe`）

沿用：`meta` / `delta` / `done` / `error`，新增：

```json
{"type":"command_result","command":"roll","text":"🎲 1d20+3 = 17","data":{...}}
{"type":"message","message":{...}}          // 新增/更新的消息（narration/roll/ooc 等）
{"type":"swipe","index":0,"total":2}        // 当前版本
{"type":"impersonate","text":"……"}          // 仅 /impersonate，文本给输入框
```

客户端收到 `message` 可局部更新；`done` 后仍建议 GET `/chat` 做最终同步。

### 3.1 `/chat/swipe` 响应

- `{"direction":"next"|"prev"}`：返回 JSON `{"ok":true,"messages":[...]}`（不调用模型）；
- `{"direction":"new"}`：返回 SSE（`meta/delta/message/swipe/done`）。

## 4. 设置新增字段

`GET/PUT /api/roleplay/settings`：

```json
{
  "authors_note": "",
  "authors_note_depth": 4,
  "lorebook_enabled": true,
  "lorebook_budget": 5,
  "lorebook_scan_depth": 6
}
```

## 5. 世界书（Lorebook）

- `GET /api/roleplay/characters/{id}/worldinfo`
  → `{"ok":true,"entries":[{"id":"e1","comment":"","keys":["剑冢"],"secondary_keys":[],"content":"...","enabled":true,"constant":false,"selective":true,"selective_logic":0,"order":100}]}`
- `POST /api/roleplay/characters/{id}/worldinfo/import`
  body：`{"filename":"world.json","data_base64":"..."}` 或 `{"json_text":"..."}`
  支持 ST 世界书 JSON（`entries` 对象/数组）、角色卡 `character_book`。
  → `{"ok":true,"imported":3,"entries":[...]}`
- `POST /api/roleplay/characters/{id}/worldinfo` body `{"entries":[...]}` 覆盖保存
- `PATCH /api/roleplay/characters/{id}/worldinfo/{entry_id}` body `{"enabled":false}`
- `DELETE /api/roleplay/characters/{id}/worldinfo/{entry_id}`

## 6. 前端重构要求（`web/roleplay.html`）

目标：现代、精致、移动优先；与主应用紫色玻璃态一致，但角色扮演页可以有自己更强的氛围。

必须包含：

1. **布局**
   - 桌面：左侧角色库（可搜索、分组、最近使用）+ 中间聊天 + 右侧可折叠「世界书 / 指令 / 设置」面板。
   - 移动：顶部角色横向卡 + 聊天 + 底部输入；世界书/设置用底部抽屉。
   - 顶部：返回应用、书名/角色名、模型 chip、主题切换、设置。
2. **视觉**
   - 深色默认、支持浅色；渐变光晕、玻璃卡片、细边框、柔和阴影；
   - 圆角 14–20px；字号/行高适合长文阅读；消息气泡最大宽度 72ch；
   - 动作消息用斜体/左侧色条；旁白居中、弱化；OOC 用虚线框；掷骰用徽章。
   - 首次加载 skeleton；空状态插画风（CSS 即可）；过渡动画克制（180–240ms）。
3. **聊天**
   - 消息头像/名字/时间；assistant 气泡支持 **变体切换** ‹ 1/2 ›，可复制、重新生成；
   - 流式光标；自动滚动（用户上翻时暂停）；「回到底部」按钮；
   - 渲染轻量 Markdown：`**粗体**`、`*斜体*`、`` `代码` ``、换行；禁止 innerHTML 注入（必须转义）。
4. **输入**
   - 多行自适应；Enter 发送 / Shift+Enter 换行；
   - 模式切换：对话 / 动作 / 旁白 / OOC（选择后自动加对应斜杠前缀或提交 kind）；
   - `/` 触发指令面板（键盘上下选择、Enter 执行、Esc 关闭），展示指令说明；
   - 动作模式 placeholder：「描述你的动作，例如：推门进去，同时拔剑，对苏晚说：别动」；
   - 发送中变为「停止」。
5. **世界书面板**
   - 列出条目（启用开关、关键词、内容摘要、删除）；导入 ST 世界书 JSON；空状态说明。
6. **设置抽屉**
   - user_name / user_persona / system_prompt / authors_note / temperature / max_tokens / context_messages / 主题。
7. **角色管理**
   - 保留现有导入 PNG/JSON、从本书生成（AI 写卡 / 快速导入）、新建/编辑/删除、头像上传。
8. 所有网络错误可见（toast），不要静默失败；不要修改其他文件。

## 7. 验收（Lead 执行）

- 浏览器：页面加载无 JS 报错；发送/`/act`/`/roll`/`/swipe`/旁白/世界书导入可用；
- 390px 无横向溢出；浅/深色切换正常；
- Android 打包后资源一致。
