> 历史记录：当前实现与验收请以 [README](README.md) 和 [2026-10-19 重构记录](docs/REFACTOR-20261019.md) 为准。

# 二次开发报告：SillyTavern 风格「角色扮演」模式

日期：2026-10-19 · 基于 `Kotabo-Zhao/NovelGenerator`（提交克隆时 main 分支）

## 1. 目标与结论

把原写作页的「🎮 互动模式」（StoryDirector 场景 + DialogueEngine 角色对话）替换为
**SillyTavern 风格的角色扮演模式**，同时保留旧互动剧情引擎作为可回退入口。

- 新入口：写作页「🎭 角色扮演」标签、左侧导航 / 底部导航「角色扮演」。
- 旧入口：写作页标签下方「🕹 旧互动剧情模式 →」，原代码零改动保留。
- 角色来源：SillyTavern / TavernAI 角色卡（PNG/JSON）、当前小说角色宝典生成、手动新建/编辑、内置示例。
- 前端为独立页面，不侵入 32 万行主 SPA；后端新增独立模块与路由，旧功能测试全部通过。

## 2. 功能

### 角色卡
- PNG：解析 `tEXt` / `zTXt` / `iTXt` chunk，支持 `chara`（V2）与 `ccv3`（V3，优先），
  base64 → JSON；PNG 原图自动存为头像。
- JSON：V2/V3 envelope（`{"spec":"chara_card_v2","data":{...}}`）或裸卡对象。
- 字段：name / description / personality / scenario / first_mes / mes_example /
  system_prompt / post_history_instructions / alternate_greetings / tags / creator /
  character_version / creator_notes。
- 小说角色宝典 → 卡：读取 `plan.json` 的 `characters.protagonist / supporting / antagonist`，
  映射身份、外貌、背景、性格、动机、关系、反派动机/人性面，并注入世界观摘要。

### 对话
- SillyTavern 式提示词：
  `[系统提示词] + [角色描述] + [性格] + [场景] + [关于 {{user}}] + [补充指示]`，
  然后 `mes_example`（按 `<START>` 解析为 user/assistant 示例消息）+ 最近 N 条历史。
- 宏：`{{char}}` / `{{user}}` / `<BOT>` / `<USER>`，所有卡字段与历史后置指令都会展开。
- SSE 流式：`meta` → `delta`* → `done`，错误为 `error`；断开/停止时保存已生成部分。
- 重新生成、清空/重开（随机或指定 `alternate_greetings`）、上下文条数、温度、最大 token。
- 角色级设置与全局设置（user_name、user_persona、system_prompt）。

### 数据
- 默认保存到 `NOVELS_DIR` 同级的 `_roleplay/`（可用 `ROLEPLAY_DIR` 覆盖）：
  `characters.json`、`settings.json`、`avatars/<id>.png`、`chats/<id>.json`；
  不会出现在书架/小说列表。
- 所有写入使用项目既有 `atomic_write_json`（临时文件 + `os.replace`）。

## 3. 新增 / 修改文件

新增（PC 与 Android 副本同步）：

```
backend/core/roleplay/__init__.py
backend/core/roleplay/card_codec.py     ST PNG/JSON 卡解析、宏、开场白
backend/core/roleplay/prompt.py         提示词与示例对话组装
backend/core/roleplay/store.py          角色/聊天/设置/头像持久化
backend/core/roleplay/engine.py         SSE 生成、重新生成、开场白重置
backend/core/roleplay/novel_cards.py    plan.json 角色 → 角色卡
backend/api/routers/roleplay.py         /api/roleplay/* 路由
web/roleplay.html                       独立角色扮演页面（Vue 3 global，无构建）
docs/roleplay-api-contract.md           接口契约
backend/tests/test_roleplay.py          16 个单元/API 测试
backend/tests/mock_llm_server.py         本地 OpenAI 兼容 mock（SSE）
backend/tests/roleplay_live_check.py     真实链路 live check
backend/tests/ui_layout_check.py         Playwright 响应式溢出检查
```

修改：

```
backend/api/server.py        注册 roleplay router；新增 /roleplay.html 路由
web/index.html               侧栏/底部导航新增「角色扮演」；写作 tab 替换为角色扮演；
                             新增 openRoleplay()；保留「旧互动剧情模式」入口
backend/web/*                web 副本（服务端实际读取）
android/app/src/main/python/*  Android 内嵌 Python 同步（新增 roleplay 包/路由/页面）
android/app/build.gradle.kts  Chaquopy buildPython 配置（可用 CHAQUOPY_BUILD_PYTHON 覆盖）
README.md                    新增角色扮演模式说明
```

## 4. API 一览（前缀 `/api/roleplay`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/PUT | `/settings` | 读取/更新全局设置（partial update） |
| GET | `/characters` | 角色列表（含 `avatar_url`） |
| GET | `/characters/{id}` | 单个角色 |
| POST | `/characters` | 手动新建 |
| PUT | `/characters/{id}` | 更新 |
| DELETE | `/characters/{id}` | 删除（含聊天/头像） |
| POST | `/characters/import` | `{filename, data_base64}` 导入 PNG/JSON 卡 |
| POST | `/characters/{id}/avatar` | `{data_base64}` 上传头像 |
| GET | `/characters/{id}/avatar` | 头像二进制 |
| POST | `/characters/from-novel/{novel_id}` | 从小说角色宝典生成（同名去重） |
| GET | `/characters/{id}/chat` | 聊天记录 |
| POST | `/characters/{id}/chat/start` | 空聊天时生成开场白 |
| POST | `/characters/{id}/chat/send` | SSE 发送消息 |
| POST | `/characters/{id}/chat/regenerate` | SSE 删除末尾回复后重生成 |
| POST | `/characters/{id}/chat/clear` | 重置为开场白（可指定 index） |

SSE 事件：`meta` / `delta` / `done` / `error`，每 5s 心跳 `ping`。

## 5. 验证证据

| 项目 | 结果 |
|---|---|
| 角色扮演专项测试 | `16 passed`（卡解析 PNG/V2/V3/裸 JSON、拒绝非卡、宏、提示词分段、示例消息解析、上下文裁剪、存储 CRUD/设置钳制、小说转卡、SSE 引擎、API CRUD/导入/from-novel/聊天/清空/流式） |
| 全量后端测试 | `131 passed`（含原有 interactive/attr/world_state 等，无回归） |
| 真实链路（PC 后端） | 启动 uvicorn + 本地 mock OpenAI：健康检查、导入卡、开场白、SSE 流式、持久化、重生成、设置全部 PASS |
| 真实链路（Android 内嵌后端） | 用 Python 3.13 + pydantic 1.10.26 / fastapi 0.125.0 / openai 1.39.0（与 APK 完全一致）跑 Android 副本：**全部 PASS** |
| 浏览器端到端（Edge 155） | 页面加载无 JS 错误；发送→mock 流式回复；重新生成；设置保存；UI 新建角色并自动选中 |
| 响应式 | Playwright 390/360/768/1280 宽度：**水平溢出 0px**；390px 下发送按钮、工具条、设置按钮均可见 |
| Android 构建 | Gradle 8.9 + AGP 8.7.3 + Chaquopy 17.0.0：`BUILD SUCCESSFUL`（47 tasks，1m29s） |
| APK 审计 | `com.novelgen.app` v2.5.68，minSdk 26 / targetSdk 35，label「小说工坊」，debug 签名；`app.imy` 内含 `web/roleplay.html`、`api/routers/roleplay.pyc`、`core/roleplay/*.pyc` |

产物：`dist/NovelGenerator-roleplay-v2.5.68-debug.apk`（30.6 MB，arm64-v8a）。

## 6. 本地运行 / 构建

### PC（后端 + 网页）
```powershell
cd NovelGenerator/backend
..\.venv\Scripts\python.exe -m uvicorn api.server:app --host 0.0.0.0 --port 8000
```
浏览器打开 `http://localhost:8000/`，写作页点「🎭 角色扮演」。

### Android APK
```powershell
$env:JAVA_HOME="C:\dev\jdk-17"
$env:ANDROID_HOME="C:\dev\android-sdk"
cd NovelGenerator/android
.\gradlew.bat :app:assembleDebug
```
- 需要 Android SDK 35 与 **CPython 3.13**（Chaquopy buildPython）；
  本机路径 `C:/dev/python-3.13/python.exe`，其他机器用 `CHAQUOPY_BUILD_PYTHON` 覆盖。
- 产物：`android/app/build/outputs/apk/debug/app-debug.apk`。

## 7. 已知限制 / 注意事项

- 未在真机上运行 APK：本机无 adb 设备，且 VBS/Hyper-V 环境无法启用硬件加速模拟器；
  已用「同版本依赖 + 同代码的 Android 内嵌后端」与浏览器 UI 验证替代。
- APK 为 **debug 签名**：若手机上已安装同包名 release 版（`com.novelgen.app`），
  需先卸载再安装，否则签名冲突。
- 角色卡导入上限 8MB（前端/后端一致）；头像支持 PNG/JPEG/WebP。
- 「从本书生成」需要该小说已有 `plan.json` 与角色宝典；无则返回 404 并提示。
- 旧互动剧情模式保留但不作为主入口；两套模式数据互不影响。
