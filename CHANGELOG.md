# Changelog

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
