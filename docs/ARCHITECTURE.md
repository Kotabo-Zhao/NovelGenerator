# 墨局 4.1 架构

`README.md` 与本文描述当前实现。仓库早期的审计报告是历史记录。

前端以 `web/` 为唯一源文件目录，无 npm 构建、外部字体或 CDN 依赖：

| 文件 | 职责 |
| --- | --- |
| `index.html` / `assets/workshop.js` | 书架、创作、大纲编辑、章节写作、剧情图谱、质量监督、导出 |
| `roleplay.html` / `assets/roleplay.js` | 角色库、角色卡编辑/导入、对话、草稿、消息编辑与恢复 |
| `cards.html` / `assets/cards.js` / `assets/cards.css` | 在线商店、小说分组、预览、移动/复制、本地导入与导出 |
| `assets/app-shell.js` | 唯一公共入口表与导航组件，主工作台路由与浏览器地址同步 |
| `scene.html` / `assets/scene.js` / `assets/scene.css` | 原作身份、群像剧情、行动、世界状态与进度 |
| `assets/runtime.js` | API、SSE 协议解析、正文提取、安全格式渲染、下载、主题、连接设置 |
| `assets/theme.css` | 两个页面共用的主题、字体、控件和连接弹窗 |
| `assets/workshop.css` / `tools.css` | 创作空间布局与功能组件 |
| `assets/roleplay.css` | 角色扮演布局、消息和表单 |

桌面服务只读取根目录 `web/`。`backend/web/` 已删除。Android 内嵌资源是构建所需的生成副本，由 `tools/sync_frontend.py` 单向同步，不能作为第二份源文件编辑。

FastAPI 的装配位于 `backend/api/server.py`，业务按域分在 `api/routers/`。小说核心管线继续使用现有 Planner、Writer、AtomicWriter、StoryGraph 和记忆管理，保留创作功能。小说世界通过 `scene.html` 与 `core/interactive/` 接入原作上下文；正式章节和世界存档分开，共用语音组件位于 `core/audio/`。

LLM 流通过 `ResilientLLMClient` 读取。同步 SDK 的创建、读取和关闭都移出主事件循环；只在尚未输出正文时自动重试。`core/llm_text.py` 只提取文本，不把 SDK 对象、用量或思考字段强制转成字符串。SSE 队列最多缓存 64 个事件，客户端断开时取消生产任务，连接并发配额持有到响应结束。

`api/jobs.py` 防止同一本小说的章节任务互相覆盖。角色扮演另有角色级生成/修改互斥、请求 ID 去重、两秒检查点、停止后的最终保存和重生成失败保留原回复。前端约 32ms 合并一批文本更新，初始只渲染最近 150 条消息，可展开更早记录。

数据目录保持独立：

| 路径 | 数据 |
| --- | --- |
| `NOVELS_DIR`，默认 `novels/` | 作品设定、章节、进度、图谱 |
| `ROLEPLAY_DIR`，默认同级 `_roleplay/` | 角色卡、头像、聊天、偏好、历史快照 |
| 同级 `llm_config.json` | 本机模型连接；接口不返回 API Key |

JSON/文本写入使用临时文件、flush/fsync 与原子替换，并重试 Windows 临时占用错误。读取损坏 JSON 时保留原文件与恢复副本。重开和编辑对话前保存快照，每个角色最多保留 30 份；恢复会交换当前与上一份记录，因此可再次撤回。原来的旧互动存档不删除，但不转换成角色扮演聊天。

本机启动使用单个服务进程。文件存储互斥只在该进程内生效；多进程部署需要另行设计数据库或进程间锁。

验证入口：`python -m pytest`、`node tests/frontend_runtime.test.cjs`、`ruff check backend --select F821,F811`、`python tools/verify_browser.py`。浏览器验证创建临时数据并运行本地模型测试服务，不触碰用户作品、不使用真实 API Key。


4.1 商店适配位于 `core/roleplay/marketplace.py`，仅接受已知来源的标识，网络读取有超时和 8MB 上限，目录与角色资料缓存并合并重复请求。官方目录来自 SillyTavern-Content；Chub 的字段转换对照 SillyTavern 官方 content-manager 实现。角色卡作为数据解析，页面不执行其中的 HTML 或脚本。

角色存储以文件修改时间和大小校验缓存，按 ID 索引取卡，返回防御性副本；摘要和小说筛选在复制大字段前执行。下载按「来源 / 来源标识 / 小说」去重。移动沿用角色 ID 与聊天；复制生成新 ID，并复制头像、设定和世界书。入世资料按小说隔离，同名角色优先使用本书版本。

普通请求、登录限制和 SSE 并发配额集中在 `api/middleware.py`，不再叠加两套通用限流。角色修改操作与生成互斥。书架只保留一个在途刷新请求，空列表会如实清空，过期回复不会把旧作品放回界面。

`tools/prepare_frontend.py` 根据资源内容生成 URL 哈希和 Service Worker 缓存组。`tools/build_release.py` 串联 Windows 打包、Android 同步和构建。Windows 宿主 `desktop/main.py` 在回环地址运行后端，关闭窗口时停止服务，数据始终位于应用资源之外。
