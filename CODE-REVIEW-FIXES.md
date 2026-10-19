# 代码审计修复记录（对照 CODE-REVIEW.md）

审计报告：`CODE-REVIEW.md`（只读，快照 2026-10-19 19:18）。以下为审计后已落地的修复与仍保留项。

## 已修复

| 编号 | 问题 | 修复 |
|---|---|---|
| P0-1 | `/api/roleplay/*` 无鉴权、from-novel 绕过 owner | 路由级 `Depends(require_owner)`；from-novel 增加 `ownership_store.owns()`；非 internal 返回 401；前端自动带 Bearer token 并提示登录。internal 本机模式保持无感。 |
| P1-1 | 伪流式、停止无法中断上游、半成品不保存 | roleplay 改为真实增量流（线程 + asyncio 队列桥接同步 OpenAI stream）；取消时 `response.close()` 并保存已生成片段。 |
| P1-2 | regenerate/swipe 变体目标错位、覆盖开场白 | 只有最后一条确为 assistant 时才走 variant；否则 append；`swipe_existing` 只操作最后一条回复；新增回归测试。 |
| P1-3 | 全局 1MB 上限与 8MB 导入冲突、chunked 绕过 | 全局默认 16MB；roleplay JSON body 读取后 12MB 校验；base64 先估算再解码；前端 8MB 校验保持一致。 |
| P1-4 | PNG zTXt/iTXt 解压炸弹、base64 提前分配 | 有界 `decompressobj`（8MB）+ base64 长度预检。 |
| P1-5 | 导入/AI 卡绕过字段长度限制 | 新增 `_clamp_card`，导入与 AI 写卡统一套用 `_TEXT_LIMITS`。 |
| P1-6 | 上下文只按条数截断 | 增加 24,000 字符预算（从新到旧）+ 单条 8,000 字符上限。 |
| P1-7 | first_mes 为空时开场白索引漂移 | 前端 greetingOptions 与后端 greetings 数组对齐（空 first 不计槽位）。 |
| P2 | 世界书先截断后排序 / 字符预算 break | 先按 order 降序再按条数/字符预算；首条超长截断而非丢弃。 |
| P2 | 删除不存在世界书条目 200、enabled 字符串真值 | 返回 404；enabled 严格布尔校验。 |
| P2 | 头像缓存不失效 | 上传后 bump `updated_at`。 |
| P2 | SSE 错误泄露 provider 细节 | 客户端只回通用文案，细节写日志。 |
| P2 | 前端模式前缀走错分支、乐观气泡重复 | 按最终 `text` 路由到 sendCommand；预事件不覆盖流式占位。 |
| P2 | 旧 v1 消息每次 GET 生成不同 id | 首次读取时持久化生成的 id。 |
| P2 | 页面禁止缩放 | 移除 `user-scalable=no`。 |
| P2 | from-novel 非法 body 静默走模板 | 有 body 但非 JSON/对象时返回 400。 |

## 仍保留（记录在审计报告）

- **多租户存储**：roleplay store 仍是全局单例，未按 owner 分区；公开多租户部署需要进一步隔离（当前 internal 模式不受影响）。
- **共享网关伪流式**：`core/resilient_client.py` 保留兼容用的同步流式适配；角色扮演继续走独立真流式实现。
- 聊天文件无分页/上限；`save_chat` 整文件重写，长会话 O(n²)。
- settings 读改写无 revision/CAS。
- 世界书 `depth/position` 保留字段但未按 ST 深度插入；无递归扫描/正则键。
- ST 高级功能未实现：群聊、TTS、向量/摘要记忆、Prompt Manager 多预设、Quick Replies、聊天分支树、扩展系统。
- 未在真机运行（本机无 adb 设备）；APK 为 debug 签名。
