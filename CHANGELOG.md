# Changelog

## 2.7.7-internal — 2026-08-20

### Fixed

- 修复互动小说 `end-chat` 未入队主角状态提取的问题。
- 修复章节正文以破折号截断或中文引号收尾时被错误判定为生成失败的问题。
- 统一三条章节生成保存路径的最终文本校验，并同步 Android 内嵌运行时。

### Verification

- Python tests: 550 passed, 1 skipped
- Interactive anchor regression: 186 passed
