# Android 4.0.2 导航与设置恢复

基于 `NovelGenerator-miniprogram` 的 `feature/miniprogram-phase0`，基线提交 `311431c`。

修复合并造成的模板闭合错误：书架、写作和导出视图的标签错位，把底部导航及设置页放进了不可见的条件分支。恢复被移除的多供应商配置代码、完整设置入口、世界圣经、创作统计和灵感素材功能。

角色扮演继续使用 DeepSeek 的 SillyTavern v2 实现。手机界面新增全局底栏与模型配置入口，保留角色卡、输入模式、世界书、斜杠指令和角色扮演参数。输入区位于底栏上方。

旧模型连接接口改为调用统一的供应商配置和持久化流程，小说创作与角色扮演共用实时生效的模型，修复不存在的 `PROVIDER_FILE` 引用。

- APK：`dist/NovelGenerator-v4.0.2-miniprogram-arm64-debug.apk`
- 包名：`com.novelgen.app`
- 版本：`4.0.2-internal`，versionCode `67`
- Android 8.0+，ARM64，Debug 签名与上一包一致，可覆盖安装。
- 文件大小：22,859,845 字节。
- SHA256：`97590c92aca5562460c3e68a589f51a572d05f52c6e96c8d802531270e55984f`

验证：Gradle 构建、APK v2 签名和包名/架构检查通过；6 个修复涉及的前端资源与 APK 内文件逐字节一致。7 项模型配置回归测试通过；浏览器 360px、390px 手机视口下验证了导航、完整设置、模型保存/测试/重新载入、已有作品写作、世界圣经、创作统计及角色扮演发送消息。模型请求使用隔离的本地模拟服务，未使用用户的 API Key。没有连接安卓真机，安装、输入法及后台生命周期尚未做真机验证。

构建收据：`dist/ui-repair-20261020/build-receipt.json`。本次修复尚未提交或推送 GitHub。
