# 角色扮演 / 互动模式代码审计（CODE-REVIEW）

- 项目：`NovelGenerator-miniprogram`，快照时间 **2026-10-19 19:18（Asia/Shanghai）**
- 工作树：detached HEAD @ `d73e81a`（对应交付报告中的 v2.8.6-internal 基线）
- 审阅范围：`backend/core/roleplay/*`、`backend/api/routers/roleplay.py`、`web/roleplay.html`、共享的 `backend/api/deps.py` / `core/resilient_client.py` / `api/server.py`、`web/index.html` 入口、`backend/tests/test_roleplay.py` 与 mock/live/UI 脚本；`interactive` 仅看与 roleplay/前端的交叉面。
- 方法：只读 `read`/`grep` + `python -m py_compile`（8 个 changed 后端文件全部通过）；未启动服务、未运行浏览器。
- 快照核对：`web/roleplay.html`、`backend/web/roleplay.html`、`android/app/src/main/python/web/roleplay.html` 三者 SHA-256 一致；Android 的 roleplay core/router 与 PC 版本 hash 一致（快照时点已对齐）。

严重度口径：**P0** = 公开部署即可造成数据/成本/隔离事故；**P1** = 核心功能错误、数据错乱或安全边界失效；**P2** = 边界/性能/可维护性/测试文档问题。

---

## P0（blocking）

### P0-1 角色扮演 API 完全无鉴权，数据全局共享，且可跨用户读取作品资料
- 位置：`backend/api/routers/roleplay.py`（整份文件；`rg "Depends|require_owner|Authorization"` 0 命中，`roleplay.py:34` 为全局 `RoleplayStore()` 单例）；`backend/api/server.py:109-144`（owner 中间件只覆盖 `/api/novels/`）；`backend/api/server.py:526-527`（roleplay router 直接挂载）；`backend/api/auth.py:16-20,52-58`；`backend/api/server.py:70` 的 `render.yaml:7,15-16`；`backend/config.py:40`。
- 证据：
  - roleplay 路由没有任何 `require_owner` / `require_consented_owner` 依赖；数据目录固定为 `NOVELS_DIR` 同级 `_roleplay`（`backend/core/roleplay/store.py:104-117`），不按 owner 分区。
  - `POST /api/roleplay/characters/from-novel/{novel_id}` 直接 `safe_read_json(NOVELS_DIR/{novel_id}/plan.json)`（`roleplay.py:208-224`），绕过 `/api/novels` 的 owner 校验。
  - `render.yaml` 以 `0.0.0.0` 启动且未设置 `NOVEL_GENERATOR_INTERNAL`（默认 false，`auth.py:16-20`），即公开部署；该配置下 roleplay 不在任何鉴权中间件范围内。
- 影响：任意能访问服务的人可读取/创建/修改/删除全部角色卡、聊天记录、头像、世界书与全局设置，可无成本触发生成；可从任意用户的 `novel_id` 导出角色宝典/人设；隐私与 LLM 成本双重风险。
- 建议：所有 `/api/roleplay/*` 加 `Depends(require_owner/require_consented_owner)`；存储按 `owner_id` 分区（或明确该功能仅 internal，并在非 internal 构建中禁用路由）；`from-novel` 复用 `ownership_store.require_owner`；补 401/403 与跨 owner 404 测试。internal 本机回环模式（`server.py:536-545`）不受影响，但公开 Render 配置是实际风险点。

---

## P1（important）

### P1-1 “流式生成”实际是伪流式；停止按钮无法停止上游请求，半成品也不会保存
- 位置：`backend/core/resilient_client.py:277-305`（尤其 `283-300` 的 `_collect_stream_chunks` + `await asyncio.to_thread(_collect_stream_chunks)`）；`backend/core/roleplay/engine.py:429-442`；`web/roleplay.html:4552-4556,4731-4732`。
- 证据：`_collect_stream_chunks()` 在线程中**把 OpenAI stream 全部消费完**才返回列表，之后 `for text in chunks: yield text` 才逐块发给 SSE；`engine` 的 `except asyncio.CancelledError` 只持久化已 `collected` 的 chunk，而取消通常发生在等待 `to_thread` 完成期间（此时 `collected` 为空）。前端停止只 `abort()` fetch，无法取消线程里的上游迭代；toast 仍提示“已生成的内容会保存”。
- 影响：前端几乎在生成结束后才一次性收到全部 delta（长回复期间无进度）；停止后线程继续生成/计费，且聊天记录没有半成品；线程池也更容易被占满。该实现为共享基础设施，`interactive` 同样受影响（pre-existing）。
- 建议：改为真正的异步逐块读取（AsyncOpenAI，或线程 → `asyncio.Queue` 转发）；取消时关闭/中断 response 并持久化已收到片段；补“生成中停止”的测试。

### P1-2 regenerate / swipe new 的变体目标可能错位，覆盖开场白或旧回复
- 位置：`backend/core/roleplay/engine.py:207-217`（`/chat/regenerate`）、`345-355`（`/regen`）、`357-379`（`/swipe`）、`472-515`（`_persist_generation`）。
- 证据：当最后一条不是 assistant 时，`history = messages`（全量），但 `_persist_generation` 生成后无条件从末尾向前找“最后一个 assistant”并追加 variant（`490-513`）。可达路径：首次 send 生成失败/取消后，聊天为 `[greeting, user]`；用户点“重新生成”或 `/regen`、`/swipe new`，服务端把新回复写成 greeting 的 variant，替换 greeting 的内容/variant_index，而不是新增 assistant 消息。前端 `web/roleplay.html:4853-4870` 只检查“存在 user 消息”，不会拦截该状态。
- 影响：开场白/旧回复被覆盖；variants 位置与语义漂移；用户看到“重新生成”却改掉了开场。
- 建议：生成命令携带目标 message id（或明确 target index）；仅当最后一条 assistant 被从 history 排除时才走 variant，否则 append 新 assistant；补“最后一条为 user 时 regenerate”用例。

### P1-3 全局 1MB 请求体上限与 8MB 导入/头像/世界书上限冲突；chunked 请求可绕过
- 位置：`backend/api/server.py:70-86`；`backend/api/routers/roleplay.py:49,176-183,322-335,404-419`；`web/roleplay.html:5088,5227,5349`。
- 证据：`MAX_REQUEST_BYTES` 默认 1,000,000，`render.yaml:41-42` 也设为 1,000,000；中间件**仅当存在 `Content-Length`** 时拒绝（`76-86`）。roleplay 的导入/头像/世界书宣称 8MB（`roleplay.py:49`），前端也按 8MB 拦截。
- 影响：base64 后 >1MB 的常见 ST PNG 卡/头像/世界书会直接 413（功能不可用）；不带 `Content-Length` 的 chunked 请求绕过限制，随后 `await request.json()` 全量读入，形成内存 DoS。
- 建议：统一按端点配置上限（或把 roleplay 上传路由的 8MB 与中间件对齐）；对无长度/流式 body 做字节计数；前端提示与实际限制一致。

### P1-4 PNG 文本块解压炸弹与 base64/JSON 大对象在大小检查前分配
- 位置：`backend/core/roleplay/card_codec.py:54-81`（`66,77` 的 `zlib.decompress` 无 `max_length`）、`99-122`；`backend/api/routers/roleplay.py:65-74,176-187`。
- 证据：`zTXt/iTXt` 的 `zlib.decompress` 未限长；PNG/JSON 先 `base64.b64decode`（`_decode_base64`）再在 `roleplay.py:182` 判断 8MB；JSON 卡对最多 8MB 文本直接 `json.loads`。
- 影响：一个几 MB 的压缩块可膨胀到数百 MB/GB 导致进程 OOM（公开端点即 DoS）；base64 解码与 JSON 解析在 413 判定前已分配内存。
- 建议：`zlib.decompressobj().decompress(data, max_length)` + 解压后总量上限；按 base64 输入长度先估算并拒绝；`json.loads` 前限制大小/深度；补恶意样本测试。

### P1-5 导入卡与 AI 写卡绕过字段长度限制，超长字段进入每次生成
- 位置：`backend/api/routers/roleplay.py:37-47`（`_TEXT_LIMITS`）与 `176-204`（导入后直接 `upsert_character`）；`backend/core/roleplay/card_codec.py:176-199`；`backend/core/roleplay/novel_cards.py:309-338`。
- 证据：手工创建/更新走 `_apply_card_fields` 截断；导入卡只经过 `normalize_card`（不截断），AI 卡写入时也未复用 `_TEXT_LIMITS`。在 1MB body 内仍可写入数百 KB 的 description/system_prompt。
- 影响：每次生成都注入超长 system 内容，放大 token 成本与上下文溢出；列表接口也返回大字段。
- 建议：所有入库路径统一 clamp（store 层或 `upsert_character` 内），导入/AI 卡同样套 `_TEXT_LIMITS`。

### P1-6 上下文只按“消息条数”截断，没有 token/字符预算
- 位置：`backend/core/roleplay/prompt.py:36-79,109-173`（尤其 `129-136`）；`backend/api/routers/roleplay.py:42-47`。
- 证据：`context_messages` 仅做条数裁剪（4–200），没有 token/字符预算；单卡各字段上限合计可达约 8 万字符（description 20k、mes_example 6k、system/persona/note 各 8k），再叠加 200 条历史、世界书 6k、示例 6k。
- 影响：8k/32k 上下文模型容易 400 或被截断；长聊天后核心功能不可用；费用不可控。
- 建议：按模型上下文计算 token/字符预算，优先丢最旧历史、限制单条消息与世界书总量；超限时给前端可解释提示。

### P1-7 开场白索引在“first_mes 为空 + alternate_greetings 非空”时漂移
- 位置：`web/roleplay.html:4497-4506`（0=first_mes，i+1=alternate i）、`4513-4521`；`backend/core/roleplay/card_codec.py:234-247`（空 first_mes 被跳过）；`backend/core/roleplay/engine.py:66-103`；`backend/api/routers/roleplay.py:473-484`。
- 证据：前端固定把 index 0 当作 first_mes、i+1 当作 alternate；后端 `pick_greeting` 只在 first_mes 非空时加入列表。手工创建/编辑允许 first_mes 为空而 alternate_greetings 非空（只校验 name），此时选“备选 2”会被解释成第 3 个 alternate（或取模）。导入卡在 `card_codec.py:200-201` 会回填 first_mes，因此主要影响手工卡。
- 影响：开场白选择错位/重复；选择“开场白”实际落到第一个 alternate。
- 建议：前后端统一“greetings 数组（含空槽位）”协议或改传 greeting id；补首条为空的多开场白测试。

---

## P2（nice-to-have）

### 正确性 / API 边界
- `backend/core/roleplay/store.py:225-261`：旧 v1 消息在每次读取时 `setdefault("id", uuid4())`，未保存前每次 GET 返回不同 id；前端按 id 去重/更新会不稳定。建议迁移时一次性持久化 id。
- `backend/api/routers/roleplay.py:84-93,307-319,323-335`：上传头像不更新 `updated_at`，而 `avatar_url?v=updated_at` 且响应 `Cache-Control: max-age=86400`；替换头像后浏览器最多一天仍显示旧图。建议上传后 bump `updated_at` 或独立 `avatar_version`。
- `backend/core/roleplay/store.py:156-169` + `backend/core/roleplay/engine.py:472-515`：生成过程中删除角色会删聊天，随后 `_persist_generation` 以空 chat 重建“孤儿”聊天文件。建议生成开始时锁定角色/写入前校验存在。
- `backend/api/routers/roleplay.py:371-390` 与 `docs/roleplay-v2-contract.md:46-57`：`/chat/swipe` 的 `next/prev` 返回 JSON，`new` 返回 SSE；契约写的是统一 SSE。前端需分支处理（`web/roleplay.html:4881-4898`）。建议统一或更新契约。
- `backend/core/roleplay/engine.py:105-117`：`swipe_existing` 从末尾找“有多版本的 assistant”，可能切换更早的一条；前端 swipe 事件却总作用于最后一条（`web/roleplay.html:4611-4622`）。建议只允许最后一条回复被 swipe。
- `backend/core/roleplay/worldinfo.py:212-227`：先按遍历顺序截断 `budget`，再按 `order` 排序；高 order 条目可能因文件顺序被丢弃。字符预算在第一个超限条目处 `break`（`224`），会漏掉后续更小条目；首条本身超过 6000 字时仍会被加入（`and capped` 条件）。
- `backend/core/roleplay/worldinfo.py:87-88` 保存了 `position/depth`，但 `prompt.py:67-69` 把所有世界书统一塞进 system 末尾，字段未生效；导入 ST 世界书后位置语义不符。
- `backend/api/routers/roleplay.py:465-470`：删除不存在的 worldinfo 条目仍返回 200；`453-454`：`bool(body["enabled"])` 对字符串 `"false"` 得到 True。建议 404 / 严格布尔。
- `backend/core/roleplay/store.py:304-312`：settings 为读-改-写，多标签并发会丢更新；建议带 revision/CAS。
- `backend/core/roleplay/engine.py:443-449`：SSE error 把异常类名与 160 字符异常正文返回客户端，可能泄露 provider 错误细节；建议对外只给通用文案、细节进日志。
- `web/roleplay.html:4833-4849`：模式前缀（动作/旁白/OOC）用原始 `raw` 判断是否 `/`，导致实际走 `sendDialogue` 而不是 `sendCommand`；本地乐观气泡保留 `/act ...`，服务端返回的 action 消息再 splice（`4572-4608`），生成期间会出现两条用户气泡（resync 后恢复）。建议按 `text` 路由并复用指令分支。
- `backend/api/routers/roleplay.py:208-237`：`characters_from_novel` 的 body 解析失败一律静默按 `{}`（template）处理；发送 AI 模式但 body 损坏时会悄悄走模板。建议非法 body 返回 400。
- `backend/core/roleplay/novel_cards.py:25-26,367-380`：AI 写卡每批 2 角色、`max_tokens=4000`，两张完整卡 JSON 容易截断后走兜底；按批串行且无超时/取消，`asyncio.to_thread` 的 `create` 不可被客户端断开取消。`:266` 的 `re.search(r"\{.*\}", ...)` 为贪婪匹配，JSON 前后有文本时可能整段解析失败。建议按角色分批/提高上限、用 `raw_decode`、加超时与取消。
- `backend/core/roleplay/commands.py:23-35`：实现别名无 `/me`、`/send`，但 `ROLEPLAY-V2-DEV-REPORT.md:17-18` 宣称支持；以代码/合同为准，需二选一修正。
- `web/roleplay.html:5` 的 `user-scalable=no` 禁止缩放（可访问性）；`web/roleplay.html:3377` 的全局 `error` 监听会把任意后续 JS 错误显示成“页面初始化失败”遮罩，建议只用于启动期。

### 性能 / 资源
- 每个 delta 触发 Vue 响应式更新 + `renderMarkdown` 全量重算（`engine.py:429-439`、`web/roleplay.html:4688-4697,3546`）；长回复/移动端开销大。建议按帧批量合并 delta。
- 每次生成结束都 GET 全量 `/chat` 回拉（`web/roleplay.html:4736-4742`），聊天文件无上限、无分页；`list_characters` 返回完整卡（含 description/extensions，`roleplay.py:84-93`）。建议分页/摘要投影/增量同步。
- `save_chat` 每次重写整份聊天（`store.py:210-222`），每条消息 O(n) → 长会话 O(n²)；聊天文件无长度上限。
- 同步文件 I/O 在 async 路径中：`get_avatar` 读整文件（`roleplay.py:307-319`）、`save_chat`/`load_worldinfo`/`settings` 均在事件循环内；世界书最多 500×20k 字符可达 ~10MB，会阻塞其他请求。建议 `asyncio.to_thread` 或缓存。
- `worldinfo.py:156-161,193-210`：每个 key 都对整个 haystack 调用 `.lower()`，条目/关键词多时重复扫描；建议预计算小写 haystack。
- `backend/api/deps.py:55-104`：`asyncio.Queue()` 无界，心跳任务固定 5s；慢客户端或高并发 SSE 时内存增长（pre-existing，roleplay/interactive 共用）。
- `backend/api/server.py:203,223`：限流把 `/generate` 当作生成端点，`/api/roleplay/.../chat/send` 不在其中；roleplay 生成没有并发上限（虽然有 `_busy` 每角色锁，但不同角色/多 IP 可并发）。建议把 roleplay SSE 纳入并发生成限流。

### 测试 / 文档
- `backend/tests/test_roleplay.py`：覆盖了指令/世界书/variants/character_book/busy，但**缺少**：取消/停止后的持久化、最后一条为 user 时 regenerate、greeting index、并发 store 写、上传超限/压缩炸弹、roleplay 鉴权/owner 隔离。`test_engine_rejects_overlapping_generation`（`678-687`）只测了直接 `_busy.add`，不是真实并发。
- `backend/tests/roleplay_live_check.py` 仍是 v1 检查（导入/发送/重生成/设置），未覆盖 `/help /roll /nar /act /note /swipe /impersonate` 与 worldinfo，与 `ROLEPLAY-V2-DEV-REPORT.md:96` 的表述不一致。
- `backend/tests/ui_layout_check.py:23` 选择器 `.rp-composer-inner` 在新前端已不存在（现为 `.rp-composer-shell`），且脚本只 print 不 assert；`ui_branch_check.py` 同样只 print。`ui_v2_check.py` 有断言但未覆盖世界书导入/停止。
- 文档漂移：`ROLEPLAY-DEV-REPORT.md:37` 写“每批 3 个角色”，代码为 `_AI_CHUNK_SIZE=2`；`:62` 引用的 `docs/roleplay-api-contract.md` 不存在；`:79` 的 21 passed 已过时；`ROLEPLAY-V2-DEV-REPORT.md:45` 宣称 `depth/order` 生效但 `depth` 未使用、`:62` 宣称取消保存半成品受 P1-1 影响。建议合并两份报告并以代码为准更新。

---

## 实际没问题的部分（“what is actually fine”）

- **前端 XSS / Markdown**：`web/roleplay.html` 的 `escapeHtml`（`3531-3543`）先转义，再插入固定 `<strong>/<em>/<code>` 标签，`v-html` 仅通过 `renderMarkdown`（`3546-3560`）输出；未发现可直接注入的 `innerHTML`/未转义模型文本。
- **路径穿越**：`novel_id` 统一走 `core/path_security.validate_novel_id`（禁止 `..`、分隔符、绝对路径、Windows 保留名）；角色 id 由服务端生成（uuid/builtin）并在 `_get_char` 存在性校验后才拼路径，当前不可达。仍建议对 `char_id` 做格式白名单作为纵深防御。
- **存储原子性**：`atomic_write_json` 使用临时文件 + `os.replace`；`RoleplayStore` 用 `RLock` 包裹写入，损坏 JSON 会被移到 `.corrupted.*` 而非静默覆盖。
- **角色卡解析**：支持 tEXt/zTXt/iTXt 的 `chara`/`ccv3`、V2/V3 信封、`character_book` 提取，`ccv3` 优先；宏在 prompt 组装时统一展开，AI 卡失败有模板兜底。
- **SSE 读取**：前端按空行分帧、处理 CRLF/最后残留 buffer、忽略 `ping`；服务端 5s 心跳可防代理 idle 超时。消息回显去重（用户消息按内容匹配、assistant 只替换占位气泡）已在最终版修复。
- **并发保护**：`RoleplayEngine._busy`（`engine.py:54,174-182`）阻止同一角色并行生成；`worldinfo` 的 AND_ANY/AND_ALL/NOT_ANY/NOT_ALL 语义在最终版已修正并有测试。
- **互动模式**：roleplay 未直接改 `interactive` 存储/引擎；`web/index.html:2934-2938` 的 `openRoleplay` 正确编码小说 id，旧互动入口仍保留（`index.html:1254`）。两者共用 `_sse_with_heartbeat`，因此 P1-1/P2 队列问题同样影响互动模式，但无新增交叉写。
- **构建/同步**：快照时点 PC `web`、`backend/web`、Android `web` 的 `roleplay.html` hash 一致，Android core/router 与 PC 一致；`python -m py_compile` 对角色扮演全部后端文件通过。

---

_审计只读完成，未修改任何源代码；以上 file:line 均基于 2026-10-19 19:18 快照。_
