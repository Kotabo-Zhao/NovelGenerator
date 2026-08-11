# NovelGenerator 原生微信小程序 MVP

当前版本只提供移动端核心信息架构和可运行页面骨架：

- 书架：作品摘要、进度、进入写作。
- 创建：灵感 → 基调 → 确认三步向导。
- 写作：当前章节阅读、章节切换和生成入口占位。
- 我的：隐私、朗读、反馈等二级入口。

## 本地配置

编辑 `utils/api.js` 中的 `API_BASE`，替换为 HTTPS API 合法域名。开发者工具可临时使用：

```js
wx.setStorageSync('apiBase', 'https://your-dev-api.example.com')
```

接口地址切换不包含生产凭据。当前小程序已经接入 `/api/auth/wechat-login` 和认证 `/v1` API：Bearer 会话只保存在当前运行内存，不写入 `wx` 持久存储；服务端会按 owner_id 隔离作品和任务。

正式上线前仍需在后端配置 `WECHAT_APP_ID`、`WECHAT_APP_SECRET`、`WECHAT_SESSION_SECRET`，并在微信后台配置 HTTPS 合法域名、隐私同意和内容审核策略。

## 当前限制

- `生成本章` 已通过认证 v1 章节 job 接口创建任务，使用状态轮询展示进度，并支持离开页面后恢复查询、取消和失败重试。
- 当前采用短轮询而非 SSE/WebSocket；任务执行器仍在 API 进程内，服务重启后的队列恢复和分布式 worker 尚未完成。
- 需要在微信开发者工具和真机上验证安全区、弱网、长文本滚动和合法域名配置。
