# 二次开发报告（feature/miniprogram-phase0 分支）

> 注：本报告是 v1（角色扮演初版，2026-10-19 早期）。v2（斜杠指令/世界书/版本切换/前端完整重构/代码复盘）见 `ROLEPLAY-V2-DEV-REPORT.md` 与 `CODE-REVIEW.md`；以 v2 报告为准。

- 分支：`feature/miniprogram-phase0`，HEAD `d73e81a`（v2.8.6-internal，2026-08-23）
- 工作目录：`NovelGenerator-miniprogram/`（独立 git worktree，detached HEAD）
- 本次范围：**后端 + Web + Android**。微信小程序按分支 phase0 计划（互动/剧情图谱/语音后续迁移）本次不改。

## 1. 与 main 分支版本的差异处理

该分支相比上次集成时有较大变化：

| 变化 | 对角色扮演的影响 |
|---|---|
| 后端改为**多提供商 LLM**（DeepSeek / Dots / OpenAI / 通义 / Kimi / 智谱 / 豆包 / SiliconFlow / 自定义），运行时可通过 `engine.set_provider_config()` 切换，`engine.client/model` 会被替换 | `RoleplayEngine` 改为持有共享 `NovelEngine`，**每次生成时读取当前 `engine.client/model`** 创建 resilient client；设置里切换供应商后角色扮演立即生效 |
| API 增加 owner 隔离 / `/v1` / auth；internal 版本 `require_owner` 旁路为 `internal-local-owner` | 角色扮演沿用现有 `/api/roleplay/*`，在 internal/本地模式下与其它 `/api` 一致；未额外加鉴权依赖，避免 Web 同源请求 401 |
| Web 重写为紫色玻璃态 UI（topnav + 玻璃卡片 + 底部导航） | 角色扮演页重新配色（`#0f0c29` 底、`#c9a7ff` accent、`#ffd479` gold），入口适配新 topnav / wb-tab / bottom-nav |
| Android 2.8.6-internal（versionCode 66）、签名配置、Chaquopy 17 | 内嵌 Python 同步 roleplay 模块；`buildPython` 指向 CPython 3.13；debug 构建 `INTERNAL_BUILD=true` |

## 2. 功能（与上一版一致）

- 导入 SillyTavern / TavernAI V2/V3 角色卡：PNG（`tEXt/zTXt/iTXt` 的 `chara`/`ccv3`）或 JSON；PNG 原图存头像。
- 从当前小说 `plan.json` 角色宝典生成角色卡；手动新建/编辑/删除；内置示例「苏晚」。
- SillyTavern 式提示词：system + description/personality/scenario + `mes_example` 示例对话 + post_history + 历史；`{{char}}`/`{{user}}`/`<BOT>`/`<USER>` 宏。
- SSE 流式、停止（保存已生成部分）、重新生成、随机/指定开场白、清空重开。
- 设置：user_name / user_persona / system_prompt / temperature / max_tokens / context_messages。
- 数据保存在 `ROLEPLAY_DIR`（默认 `NOVELS_DIR` 同级 `_roleplay/`），原子写入，不入书架。

## 3. AI 写卡导出（本次新增）

「从本书生成角色」不再只是字段拼接，而是让 AI 根据小说生成时沉淀的全部角色资料**自动填好角色卡**：

- 读取资料：`plan.json`（世界观/角色宝典/关系）、`character_bible.json`（人物宝典）、
  `character_profiles.json`（女娲人设蒸馏卡：心智模型/决策启发/表达 DNA/反模式/边界）、
  `character_voices.json`（声音卡：口头禅/称呼/句式/情绪/动作）。
- AI 输出完整 SillyTavern V2 字段：description / personality / scenario / **first_mes**（角色语气开场，带动作与互动钩子）/
  **mes_example**（`<START>` 示例对话）/ system_prompt / post_history_instructions /
  alternate_greetings / tags。
- 每批 3 个角色调用一次当前 LLM（多提供商设置即时生效）；失败或漏角色**自动回退模板**，不会整体失败。
- 角色名、来源小说、角色定位自动写入卡片；重复同名自动跳过。
- 前端弹窗可选：
  - `🤖 AI 补全角色卡（推荐）`：调用模型写卡；
  - `⚡ 快速导入（仅结构化设定）`：不调用模型，秒出模板卡。
- API：`POST /api/roleplay/characters/from-novel/{novel_id}`，body 可选
  `{"mode": "ai"|"template", "characters": ["顾尘", ...]}`；响应含
  `ai_filled` 与 `warnings`（AI 失败/兜底原因）。

## 4. 入口

- 顶栏：`🎭 扮演`（桌面端直接可见）
- 写作页：`✒️ 写作` / `🎭 角色扮演` 双 tab（原「互动」tab 被替换）
- 移动端底部导航：`🎭 扮演`
- 旧互动剧情入口保留：写作页工具栏 `🕹` 按钮 → 原 `openInteractiveTheater()`，旧引擎零改动。

## 5. 新增 / 修改文件（相对分支）

新增（PC 与 Android 副本）：

```
backend/core/roleplay/{__init__,card_codec,prompt,store,engine,novel_cards}.py
backend/api/routers/roleplay.py
web/roleplay.html
backend/tests/{test_roleplay,mock_llm_server,roleplay_live_check,ui_layout_check,ui_branch_check}.py
docs/roleplay-api-contract.md
```

修改：

```
backend/api/server.py         注册 roleplay router；新增 /roleplay.html
web/index.html               topnav「扮演」、写作 tab 替换、底部导航、openRoleplay()、旧互动 🕹
backend/web/*                服务端实际读取的 web 副本
android/app/src/main/python/*  Android 内嵌 Python 同步（roleplay 包/路由/server/web）
android/app/build.gradle.kts  Chaquopy buildPython（CHAQUOPY_BUILD_PYTHON 可覆盖）
```

## 6. 验证证据

| 项目 | 结果 |
|---|---|
| 角色扮演专项测试 | **21 passed**（原 16 个 + AI 写卡：prompt 含宝典/人设/声音卡、JSON 容错、AI 字段填充、失败兜底、API ai 模式） |
| 分支全量后端测试 | **170 passed**（`pytest tests -q`，exit 0） |
| 真实链路（分支后端 :8126 + mock OpenAI） | health / 内置角色 / 导入卡 / 开场白 / SSE 流式 / 持久化 / 重生成 / 设置 / **AI 写卡导出（ai_filled=2，AI 字段写入卡片）** 全部 PASS |
| 浏览器 E2E（Edge 155） | 主界面顶栏出现「扮演」；角色页发送→流式回复；**「从本书生成」弹窗 → AI 写卡 → 角色列表出现带「AI写卡」标签的卡片**；无 JS 报错 |
| 响应式 | 390×844 下 `scrollWidth == clientWidth == 390`，**水平溢出 0** |
| Android 构建 | Gradle 8.9 + AGP 8.7.3 + Chaquopy 17.0.0：`BUILD SUCCESSFUL` |
| APK 审计 | `com.novelgen.app` v2.8.6-internal（versionCode 66），minSdk 26 / targetSdk 35，debug 签名；`app.imy` 内含 `web/roleplay.html`（与源码 SHA-256 一致）、`api/routers/roleplay.pyc`、`core/roleplay/*.pyc`（AI 代码已打包） |

产物：`dist/NovelGenerator-roleplay-v2.8.6-internal-debug.apk`（24,276,242 B，arm64-v8a）。

## 7. 构建

```powershell
$env:JAVA_HOME="C:\dev\jdk-17"
$env:ANDROID_HOME="C:\dev\android-sdk"
cd NovelGenerator-miniprogram/android
.\gradlew.bat :app:assembleDebug
```

- Chaquopy 需要 build 机器上有 CPython 3.13（本机 `C:/dev/python-3.13/python.exe`），
  其他机器用 `CHAQUOPY_BUILD_PYTHON` 覆盖。
- 需要 Android SDK 35 / build-tools 35。
- 产物：`android/app/build/outputs/apk/debug/app-debug.apk`。

## 8. 已知限制

- 未在真机运行：本机无 adb 设备，VBS/Hyper-V 环境无法启用硬件加速模拟器；已用「分支后端 live check + 浏览器 UI E2E + Android 内嵌代码同构」验证。
- APK 为 **debug 签名**（`2.8.6-internal`）。若手机已装同包名 release 版，需先卸载。
- 微信小程序端未改（按分支 phase0 规划，互动/角色扮演后续迁移；小程序不支持 SSE，需要非流式/轮询与微信登录适配）。
- AI 写卡质量取决于所选模型与小说角色资料完整度；无 `character_profiles/character_voices` 时会只用 plan/bible 生成，仍可用。
- 角色卡导入上限 8MB；头像支持 PNG/JPEG/WebP。
- 「从本书生成」需要该小说已有 `plan.json` 与角色宝典。
