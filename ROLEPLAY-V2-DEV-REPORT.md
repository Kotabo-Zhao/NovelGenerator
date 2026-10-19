# 角色扮演 v2 开发报告（SillyTavern 深度借鉴 + 前端完整重构）

- 分支：`feature/miniprogram-phase0` @ `d73e81a`（v2.8.6-internal）
- 工作目录：`NovelGenerator-miniprogram/`
- 本次范围：**角色扮演模式的后端能力扩展 + 前端完整重构 + 代码复盘**
- SillyTavern 参考：`slash-commands.js`、`world-info.js`（只借鉴行为与数据格式，未直接复制 GPL 代码；实现为独立 Python/Vue 代码）

---

## 1. 复杂动作指令（SillyTavern 风格）

### 1.1 斜杠指令（全部走 `POST /chat/send {"message":"/..."}`）

| 指令 | 别名 | 行为 |
|---|---|---|
| `/help` | `/h` | 指令列表 |
| `/say 文本` | `/s` `/send` | 普通对话 |
| `/act 动作` | `/do` `/action` `/me` | 动作：生成「动作结果 + 角色反应」 |
| `/nar 旁白` | `/narrate` `/sys` | 插入旁白，不调模型 |
| `/ooc 内容` | `/out` | 出戏发言，角色回应 |
| `/roll NdM±K` | `/dice` `/r` | 掷骰（1–100 颗、2–1000 面、±1000），记录骰子消息 |
| `/continue` | `/cont` | 续写上一条角色回复 |
| `/regen` | — | 新增一个回复版本（swipe） |
| `/swipe next/prev/new` | — | 切换版本 / 生成新版本 |
| `/impersonate` | `/imp` | AI 替玩家写一句台词，填入输入框不发送 |
| `/note 文本` | — | 设置作者注释 |
| `/world` | — | 查看世界书启用状态 |

### 1.2 自然语言动作

无需指令：`我推门进去，同时拔剑`、`（叹气）`、`上车` 等会被规则预筛为动作，按 `kind=action` 存储；提示词注入【动作规则】，要求先描写环境/对象反馈，再写角色反应，**不替玩家决定成败与台词**。支持多动作、动作+台词混写。

### 1.3 动作与对话的隔离

- 消息新增 `kind`：`dialogue / action / narration / ooc / roll / system`；
- 提示词渲染：动作 → `[动作] ...`，旁白 → `【旁白】...`，OOC/骰子 → system；
- 前端四种模式：对话 / 动作 / 旁白 / OOC（自动加对应指令前缀）。

## 2. 借鉴 ST 的其它能力

### 2.1 世界书（Lorebook）

- 导入 ST 世界书 JSON（`entries` 字典/数组）、角色卡 `character_book`；
- 条目字段：`keys / secondary_keys / content / comment / enabled / constant / selective / selective_logic / order / probability / depth`；
- 激活：最近 `lorebook_scan_depth` 条消息 + 当前输入做大小写不敏感关键词匹配；支持 `AND_ANY / AND_ALL / NOT_ANY / NOT_ALL`、constant、probability、order 排序、`lorebook_budget` 条数上限与 6000 字预算；`depth` 字段保留但 v2 统一插入 system 末尾（未实现 ST 的按深度插入）；
- 前端右侧「世界书」面板：导入、启用开关、删除、条目预览；角色卡导入时自动吸收 `character_book`。

### 2.2 作者注释（Author's Note）

- 设置新增 `authors_note` / `authors_note_depth`，按深度插入 system 消息（ST 式）；
- `/note` 指令快速设置，前端设置抽屉可视化编辑。

### 2.3 回复版本（Swipe）

- 每条 assistant 消息保存 `variants[] + variant_index`；
- `/regen`、`/swipe new` 生成新版本；`/swipe next|prev` 本地循环切换（不调模型）；
- 前端 `‹ 1/2 ›` 控件与重新生成按钮。

### 2.4 生成安全与一致性

- 每角色**单并发生成锁**：重复发送返回「正在生成中」而非交错写入；
- 取消/断线时保存已生成片段（append/variant/continue 分别处理）；
- 聊天写入保持原子 JSON；消息 id/kind/variants 在读时归一化，兼容 v1 旧数据；
- 角色卡导入自动提取 lorebook，不把大对象持久化到卡片上。

### 2.5 审计后的安全/流式修复

- **鉴权**：`/api/roleplay/*` 路由级 `Depends(require_owner)`；非 internal 部署返回 401。`from-novel` 额外校验 `ownership_store.owns()`，不再跨 owner 读取作品。前端自动读取 `access_token/owner_token/session_token` 并加 Bearer 头，401 时提示从主应用登录。
- **真流式**：roleplay 改为真实 token 增量流（线程 + asyncio 队列桥接同步 OpenAI stream），不再等上游收完；取消时 `response.close()` 中断上游并保存已生成片段。
- **请求体/解压上限**：全局默认请求体 16MB；roleplay JSON body 12MB、base64 先估算再解码；PNG zTXt/iTXt 解压上限 8MB（防解压炸弹）。
- **入库截断**：导入卡与 AI 卡统一走 `_clamp_card`，与手工编辑共用字段上限。
- **上下文预算**：历史按 24,000 字符预算从新到旧裁剪，单条最多 8,000 字符。
- **变体目标**：`regenerate` / `/swipe new` 仅在最后一条确为 assistant 时追加变体；否则新增 assistant，不再覆盖开场白。
- **世界书预算**：先按 `order` 降序，再按条数/字符预算截取；首条超长时截断。
- **其它**：SSE 错误只返回通用文案；头像上传刷新 `updated_at`；删除不存在世界书条目返回 404；`enabled` 严格布尔校验；前端 401/模式前缀/开场白索引修复。

## 3. 前端完整重构

`web/roleplay.html`：**约 5,700 行 / 215 KB 单文件**（Vue 3 global，无构建步骤）。

- **布局**：桌面三栏（角色库 / 聊天 / 世界书·指令面板）；移动单列 + 角色横栏 + 底部抽屉；1200px 以下自动折叠。
- **视觉**：深色默认 + 浅色主题；紫蓝渐变光晕、玻璃卡片、细边框、柔和阴影、14–20px 圆角；骨架屏、空状态、克制过渡。
- **聊天**：头像/名字/时间；流式光标；动作色条、居中旁白、虚线 OOC、骰子徽章；安全 Markdown（先 `escapeHtml` 再插标签，零 `innerHTML` 注入）；自动滚动暂停 + 回到底部；复制/重新生成/版本切换。
- **输入**：自增高 textarea；Enter 发送 / Shift+Enter 换行；四模式；`/` 指令面板（↑↓/Enter/Esc，12 条指令与别名）；停止生成。
- **面板**：世界书导入/开关/删除；指令说明；设置抽屉（user_name、persona、system_prompt、authors_note、温度、max_tokens、上下文、lorebook、主题）。
- **角色管理**：PNG/JSON 导入、AI 写卡 / 快速导入、新建/编辑/删除、头像上传、搜索/分组/最近使用。
- **兼容**：SSE 事件 `meta/delta/done/error/command_result/message/swipe/impersonate`，忽略 `ping`；AbortController；完成后回拉 `/chat` 同步。

## 4. 接口变更摘要

```
POST /api/roleplay/characters/{id}/chat/send         # 支持 / 指令
POST /api/roleplay/characters/{id}/chat/regenerate
POST /api/roleplay/characters/{id}/chat/swipe        # {direction: next|prev|new}
GET/POST/PATCH/DELETE .../worldinfo[/import|/{entry_id}]
GET/PUT /api/roleplay/settings                      # 新增 authors_note / lorebook_*
```

消息结构见 `docs/roleplay-v2-contract.md`。

## 5. 验证

| 项目 | 结果 |
|---|---|
| 角色扮演专项测试 | **38 passed**（指令解析/掷骰/动作检测、世界书导入与四种 selective logic、prompt 注入、lorebook/作者注释、动作消息、swipe、last-user regenerate、API worldinfo/swipe、AI 写卡） |
| 分支全量后端测试 | **187 passed** |
| 真实链路（PC 后端 + mock LLM） | health / import / SSE / regenerate / **/help /roll /nar /act /note /swipe /impersonate** / worldinfo / AI 写卡 **全部 PASS** |
| Android 内嵌后端（Python 3.13 + pinned deps） | **同一套 live check 全部 PASS** |
| 浏览器验收（Edge） | roll/旁白/动作/指令面板/浅色/390px/世界书导入 全 PASS，无 JS 报错，390px 无横向溢出 |
| Android 构建 | `BUILD SUCCESSFUL`，APK 内 `web/roleplay.html` 与源码 SHA-256 一致，`commands.pyc`/`worldinfo.pyc`/`engine.pyc` 均已打包 |
| APK | `dist/NovelGenerator-roleplay-v2.8.6-internal-debug.apk`（24,334,062 B，arm64-v8a，debug 签名）<br>SHA-256：`C8F3730AC6D873840D5437E3465A57DB47BF27E389266DAB6E8DE9AB40E18B75` |

截图：`dist/v2-desktop-clean.png`、`dist/v2-mobile-clean.png`、`dist/v2-light-clean.png`、`dist/v2-palette.png`、`dist/v2-worldinfo.png`。

## 6. 代码复盘

独立审计报告见 `CODE-REVIEW.md`（P0/P1/P2 + file:line，只读审计）。审计后已修复：

- **P0** 角色扮演 API 鉴权 + `from-novel` owner 校验（internal 本机模式不受影响）；
- **P1** 真流式 + 取消时保存片段；
- **P1** regenerate / swipe 变体目标错位；
- **P1** 1MB 请求体上限与 8MB 导入冲突、chunked 绕过（roleplay JSON body 12MB 限制）；
- **P1** PNG 解压炸弹、base64 提前估算、导入/AI 卡字段截断；
- **P1** 上下文仅按条数截断（新增字符预算）；
- **P1** 开场白索引在 first_mes 为空时漂移（前端与后端列表对齐）；
- **P2** 世界书 order/预算顺序、删除 404、布尔校验、头像缓存、SSE 错误信息、模式前缀路由、页面缩放。

仍保留（已记录在审计报告）：roleplay 存储仍是全局单例，未按 owner 分区（公开多租户部署需进一步隔离）；`interactive` 仍使用共享 resilient client 的伪流式；聊天文件无分页/上限；settings 读改写无 CAS。

## 7. 已知边界（相对完整 ST）

本次覆盖的是 ST 角色扮演的核心闭环，但**未**实现：群聊/多角色同场、TTS 语音、向量记忆/摘要记忆、Prompt Manager 多预设、世界书递归扫描与正则键、Quick Replies/QR、作者注释多插槽、聊天分支树、扩展系统。小程序端仍未迁移角色扮演（需要非流式适配与微信登录）。APK 为 debug 签名，未在真机运行。
