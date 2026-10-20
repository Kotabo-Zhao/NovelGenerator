# 群像实景（Scene）前端契约

新页面：`web/scene.html`，入口 `?novel=<novel_id>`。后端复用现有 interactive 引擎，
不需要新 API。页面与 `roleplay.html` 视觉同级，但定位是「进入小说世界、多角色同场」。

## 1. 页面流程

1. 读取 `novel` query；没有则显示「请从书架选择一本小说进入」。
2. 并行请求：
   - `GET /api/novels/{id}/interactive/cast-options` → `{options:[{name,identity,personality,speak_style,role}]}`
   - `GET /api/novels/{id}/interactive/state` → 200 `{state:{...}}` 或 404（无存档）
   - `GET /api/roleplay/characters` → 用 `name` 匹配头像（`avatar_url`）
3. 无存档：显示「进入小说」启动面板——选择扮演角色（cast 卡片），点击后
   `POST /api/novels/{id}/interactive/start` body `{character}`，读 SSE。
4. 有存档：直接渲染 `state.recent_blocks`，顶部显示场景号/地点/时间/在场角色。
5. 顶栏：返回主应用（`/`）、书名、场景状态、设置/重开。
6. 底部输入：对话 / 动作 / 指定 @角色；有行动按钮条时优先展示（来自 `/actions`）。

## 2. 启动/场景 SSE（`/start`、`/scene`）

事件：
- `phase` `{label}`
- `scene_chunk` `{scene_num, content}`：原始标记文本（`【旁白】...`），可临时显示
- `scene_end` `{scene_num, blocks:[{type,speaker,content}], ...}`：用 blocks 替换临时文本
  - block.type：`narration` / `action` / `dialogue`；dialogue 的 `speaker` 是角色名
- `scene_retry`、`node_check`、`intro` `{content}`、`done`、`error`

## 3. 对话 SSE（`/chat`）

body：`{message, target?: "角色名", force_talk?: true}`
事件：
- `chat_chunk` `{speaker, content}`：同一 `speaker` 连续 chunk 追加到同一气泡
- `chat_end` `{speaker, content, segments?, snapshot?}`：收尾/状态快照
- `action_detect` `{summary, blocked, action_type, end_chat}`
- `action_chunk` `{content}` / `action_end` `{content, snapshot}`
- `action_done` `{end_chat, snapshot}`：可自动推进下一场景
- `state_change` `{changes, snapshot}`
- `drift_check` / `natural_end` / `error` / `done`
- `snapshot` 结构不需要完整理解，用于刷新状态栏（location/time/tension/present 等）。

## 4. 行动按钮（`/actions`、`/act`）

- `GET /actions` → `{actions:[{id,label,intent,target,summary}]}`，`intent` 为 `travel|talk|act`
- 点击 `talk`：聚焦输入框并锁定目标；发送时 `POST /chat` body `{message,target,force_talk:true}`
- 点击 `travel/act`：`POST /act` body `{action_id,intent,target,message}`，读与 chat 相同的 SSE
- `/actions` 失败静默（不要挡主流程）。

## 5. 其它

- 重开：`POST /interactive/restart`；回退：`POST /interactive/rollback`（可选按钮）。
- 角色头像：按名字匹配 roleplay 角色卡；无头像用首字。
- 所有模型文本必须转义；安全 Markdown（同 roleplay.html）。
- 显示「多角色同场」：每个角色不同颜色/头像；旁白居中；动作左侧色条。
- 流式期间显示停止按钮；SSE 用 fetch + reader，AbortController；完成刷新 `/state`。
- 桌面三栏：左「在场角色/小说信息」、中场景流、右「行动/状态」；移动单列 + 底部抽屉。
- 深色默认 + 浅色；紫蓝玻璃视觉；390px 无横向溢出。
- 只能修改 `web/scene.html`；不要改其它文件。

## 6. 验收

- 无存档时显示扮演角色选择；有存档恢复 blocks；
- 点「继续剧情」能看到流式场景/角色对白；
- 输入普通对话/动作/@角色 能收到多角色回复；
- 行动按钮可用；状态栏更新；无 JS 报错；390px 正常。
