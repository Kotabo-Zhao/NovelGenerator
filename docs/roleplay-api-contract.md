> 初期移植契约，仅供历史参考。其中保留旧互动入口的条款已废止，当前以 [架构说明](ARCHITECTURE.md) 和 [本次验收记录](REFACTOR-20261019.md) 为准。

# Roleplay Mode (SillyTavern-style) — API & UI contract

Status: implementation contract for the secondary development of NovelGenerator.
Owner: Lead. Frontend implementer: `rp-web` teammate.

## 1. 目标

新增「🎭 角色扮演」模式（SillyTavern 风格），替换主应用写作页的「🎮 互动模式」入口：

- 角色来源：导入 SillyTavern 角色卡（PNG `chara`/`ccv3` 或 JSON V2/V3）、从当前小说角色宝典生成、手动新建/编辑、内置示例角色。
- 对话：ST 式提示词（system + description/personality/scenario/mes_example + 历史）、`{{char}}`/`{{user}}` 宏、SSE 流式输出、重新生成、清空重开、开场白（alternate greetings）。
- 页面独立：`web/roleplay.html`，由 FastAPI 在 `/roleplay.html` 提供；主 app 只加导航链接（Lead 负责）。
- 旧互动剧情模式代码保留，主应用里保留一个小入口（Lead 负责）。

## 2. 页面入口

- 主应用写作页标签：`🎭 角色扮演`（取代 `🎮 互动模式`）；点击跳转
  `/roleplay.html`（有当前小说时附加 `?novel=<novel_id>`）。
- 主应用侧边栏 / 底部导航新增 `🎭 角色扮演`，同样跳转。
- `roleplay.html` 顶部有「← 返回应用」链接（`location.href='/'`）。

## 3. REST API（前缀 `/api/roleplay`）

所有请求/响应 JSON（头像二进制除外）。错误统一 `{"detail": "..."}`, HTTP 4xx/5xx。

### 3.1 设置

`GET /api/roleplay/settings`

```json
{"ok": true, "settings": {
  "user_name": "旅行者", "user_persona": "", "system_prompt": "",
  "temperature": 0.85, "max_tokens": 600, "context_messages": 40
}}
```

`PUT /api/roleplay/settings`：body 为上述字段的任意子集（partial），返回同样的
`{"ok": true, "settings": {...}}`。数值范围：temperature 0–2，max_tokens 64–4096，
context_messages 4–200。

### 3.2 角色

`GET /api/roleplay/characters` →

```json
{"ok": true, "characters": [{
  "id": "uuid", "name": "苏晚",
  "description": "...", "personality": "...", "scenario": "...",
  "first_mes": "...", "mes_example": "...",
  "system_prompt": "", "post_history_instructions": "",
  "alternate_greetings": ["..."], "tags": ["..."],
  "creator": "", "character_version": "",
  "source": "png|json|manual|novel|builtin",
  "avatar_url": "/api/roleplay/characters/<id>/avatar?v=<updated_at>",
  "created_at": 1730000000, "updated_at": 1730000000
}]}
```

`GET /api/roleplay/characters/{id}` → `{"ok": true, "character": {...}}`
`POST /api/roleplay/characters`：body 为角色字段（`name` 必填），新建手填角色。
`PUT /api/roleplay/characters/{id}`：body 为角色字段，更新。
`DELETE /api/roleplay/characters/{id}` → `{"ok": true}`（同时删聊天记录/头像）

`POST /api/roleplay/characters/import`

```json
{"filename": "card.png", "data_base64": "<原始文件 base64，不含 data: 前缀>"}
```
支持 PNG（内嵌卡）与 JSON（V2/V3 envelope 或裸卡）。返回
`{"ok": true, "character": {...}, "format": "png"}`。PNG 卡自动把原图保存为头像。

`POST /api/roleplay/characters/{id}/avatar`：`{"data_base64": "..."}` → `{"ok": true, "avatar_url": "..."}`
`GET /api/roleplay/characters/{id}/avatar`：`image/png`（无头像 404）

`POST /api/roleplay/characters/from-novel/{novel_id}`：

- 从该小说 `plan.json` 的 `characters.protagonist / supporting / antagonist` 生成角色卡。
- 已存在同名同来源小说角色则跳过。
- 返回 `{"ok": true, "characters": [...], "created": 3, "skipped": 1}`。
- 小说不存在/无 plan → 404 `{"detail":"..."}`。

### 3.3 聊天

消息对象：`{"role": "user"|"assistant", "content": "...", "ts": 1730000000}`

- `GET /api/roleplay/characters/{id}/chat` → `{"ok": true, "messages": [...]}`
- `POST /api/roleplay/characters/{id}/chat/start` → 若空则用 `first_mes`（或随机
  `alternate_greetings`）生成开场白并保存，返回 `{"ok": true, "messages": [...]}`。
- `POST /api/roleplay/characters/{id}/chat/send`，body `{"message": "..."}` → **SSE**
  - `{"type":"meta","character":"苏晚","user":"旅行者"}`
  - `{"type":"delta","content":"文"}`（多次）
  - `{"type":"done"}`（结束）
  - `{"type":"error","message":"..."}`（失败）
  - 服务端先把 user 消息落盘，流结束后追加 assistant 消息并落盘；中断时保存已生成部分。
- `POST /api/roleplay/characters/{id}/chat/regenerate` → SSE（同上事件），删除末尾
  assistant 消息后重新生成；没有 user 消息时 400。
- `POST /api/roleplay/characters/{id}/chat/clear`，body 可带 `{"greeting_index": 0}`
  （可选，选择 alternate greeting；缺省随机）→ 重置为一条开场白，返回
  `{"ok": true, "messages": [...]}`。

SSE 使用 `text/event-stream`，每 5s 会有 `{"type":"ping"}` 心跳，客户端必须忽略。

## 4. 前端页面要求（`web/roleplay.html`）

单文件、零外部 CDN、原生 Vue 3 global（`/vue.global.prod.js`），与主应用同一套
深色/浅色 CSS 变量风格（可参考 `web/index.html` 的 `:root` / `[data-theme]`，页面
默认深色）。移动端优先，桌面端最大化利用宽度。

必须包含：

1. **顶栏**：返回应用、标题「角色扮演」、当前角色名、设置按钮。
2. **角色区**：
   - 横向可滚动角色卡（头像 + 名字），点击切换；
   - 按钮：导入角色卡（隐藏 `<input type=file accept=".png,.json">`）、从本书生成
     （有 `?novel=` 时显示，调用 from-novel 接口）、新建角色、编辑当前角色、删除角色。
   - 空状态：提示导入 / 从本书生成 / 使用内置角色（后端首次启动会内置「苏晚」）。
3. **聊天区**：
   - 消息气泡：assistant 左侧带头像/名字，user 右侧；保留换行（`white-space: pre-wrap`）；
   - 流式输出时显示光标/打字状态；发送中禁用发送按钮并显示「停止」；
   - 底部输入框（Enter 发送，Shift+Enter 换行）+ 发送按钮；
   - 工具条：重新生成、清空/重开、开场白切换、滚动到底。
4. **角色编辑弹窗**：name / description / personality / scenario / first_mes /
   mes_example / system_prompt / post_history_instructions / alternate_greetings
   （每行一条）/ tags（逗号分隔）/ 头像上传。保存调 POST 或 PUT。
5. **设置弹窗**：user_name / user_persona / system_prompt / temperature /
   max_tokens / context_messages。保存调 PUT。
6. **导入**：FileReader.readAsDataURL → 去掉 `data:*;base64,` 前缀 → POST import；
   成功刷新列表并选中；失败 toast 错误。
7. **SSE 解析**：`fetch` + `response.body.getReader()`，按 `\n` 切行，处理
   `data: ` 行；忽略 `ping`；`delta` 追加到最后一条 assistant 消息；`done` 结束；
   `error` 提示。
8. **停止生成**：`AbortController` 取消 fetch；随后重新 GET chat 恢复已保存内容。
9. 所有网络错误、空数据、404 都要有可见提示，不能静默。
10. 不修改 `web/index.html`、后端或其他文件（Lead 负责集成）。

建议实现结构（单文件内）：`<style>` 自带样式变量 + `.rp-*` 类；模板用 `v-if`；
`Vue.createApp({ setup() { ... } })`，state 用 `reactive/ref`。

## 5. 验证方式（Lead 执行）

- `GET /api/roleplay/characters` 应至少有一个内置角色；
- 用本地 mock OpenAI 服务（SSE）跑通 send/regenerate；
- 浏览器打开 `/roleplay.html` 做端到端冒烟：导入 JSON 卡、发消息、流式显示、重开。
