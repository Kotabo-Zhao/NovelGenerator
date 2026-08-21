# Changelog

## 2.8.5-internal — 2026-08-21

### Fixed（各子模块功能完善批次）

**后端 API**
- `/api/ideas*` 全端点改 `Depends(require_owner)`：非 internal 模式不再必 401，灵感箱真正可用。
- 批量导出恢复同步实现（此前 410 致 web 端必失败）；PDF 按钮改 EPUB（后端仅支持 txt/epub）。
- surgical 章节精细操作：版本快照先读旧内容再快照——此前存新不存旧，回滚形同虚设。
- surgical / scan-consistency / requirements / opening-alternatives 的同步 LLM 调用全部放
  `asyncio.to_thread`，不再阻塞事件循环（高并发下全站卡死风险）。
- quality-dashboard 补逐条伏笔账本 + 摘要数改读 global_state（此前恒 0）。

**后端 core**
- 批量章节任务心跳（`job_store.touch()`）：此前 >900s 任务被 requeue 二次调度 →
  重复生成、双份计费、成功任务落 FAILED；needs_revision 中间态保留错误码。
- 交互式大纲卷级修改：保留卷元数据只替换 chapters（此前整卷被替换成 `{data,chapters}`
  包装并持久化损坏）。
- 超短篇质量门 None 崩溃修复；ContextUpdater 落盘走 per-path 锁（此前直写与
  summarizer/character_state 并发覆盖丢失角色记忆）。
- planner LLM 脏输出防御；评估器主角名注入（此前硬编码「沈砚」）；humanizer 三条
  规则可触发；beat_assembler 一致性真检查；需求监督空结果不再假通过；大纲合规
  LLM 故障时 critical 项保守计缺失；global milestone 激活全书章节；需求兜底项不再
  硬门拦截；参数化风格 7 参数全部生效；配额按本地时区计日。

**web 前端**
- 写作页右栏"本章质量/伏笔账本"接入 quality-dashboard（此前变量未定义，面板永不显示）。
- 正文选中工具条（续写/扩写/精简/重写）绑定 selectionchange（此前完全不可达）。
- 未选书搜索自动降级跨书（此前 `/api/novels//search` 404）；音色语速/音调滑块修复
  格式（此前 `+0%`/`+0Hz` 被 v-model.number 破坏）；编辑大纲加载态；删除确认迁移
  统一弹窗；对话议程卡片渲染；savePlan 用 activeNovel.id 并剔除 state。

**miniprogram**
- 生成成功清理幂等键 + 刷新进度（此前再点生成静默无效、进度卡死不更新）；
  注销后直接清会话不调 logout（此前必 401 报错且登录态残留）。

**android / tools**
- build.sh / build_and_install.bat SDK 路径自动探测 + local.properties（此前写死
  不存在路径，构建必失败）；gradle 补 ebooklib（APK 内 EPUB 导出可用）；
  sync_android_core 不再跳过 `__init__.py`。

### Verification

- Python tests: 400 passed, 2 skipped, 33 subtests
- CDP 浏览器实测：质量面板/伏笔账本/选中工具条/导出按钮/批量导出/搜索降级全部生效
- surgical 快照回滚实测恢复旧内容；三副本（web/backend/android）同步一致

## 2.7.8-internal — 2026-08-21

### Fixed

- 修复写作页「⋯」折叠菜单选项不可点击的问题：`.wb-tabs` 的 `backdrop-filter` 使菜单
  `z-index:300` 被困在自身层叠上下文中，被 DOM 更后的侧栏/正文面板盖住，点击全部落空。
  给 `.wb-tabs` 加 `z-index:50`，让菜单浮于正文/侧栏之上（低于顶栏/底栏/浮层）。

### Verification

- CDP 移动视口实测：菜单 6 个选项 hit-test 全部命中 `write-more-item`，逐项点击均打开对应面板
- 桌面端回归：5 个 pc-only 工具按钮 hit-test 正常，tabs 与侧栏无重叠
- Python tests: 400 passed, 2 skipped, 33 subtests

## 2.7.7-internal — 2026-08-20

### Fixed

- 修复互动小说 `end-chat` 未入队主角状态提取的问题。
- 修复章节正文以破折号截断或中文引号收尾时被错误判定为生成失败的问题。
- 统一三条章节生成保存路径的最终文本校验，并同步 Android 内嵌运行时。

### Verification

- Python tests: 550 passed, 1 skipped
- Interactive anchor regression: 186 passed
