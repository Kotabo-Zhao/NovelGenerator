# 墨局 · NovelGenerator 4.3.0

本地小说创作与沉浸式角色扮演工作台。保留世界观、大纲、章节写作、剧情图谱、质量监督与导出；保留兼容 SillyTavern 角色卡的角色扮演，以及进入原作剧情的小说世界。

## Windows 启动与升级

已构建 Windows 程序时，双击根目录 **`启动墨局.cmd`** 或 **`启动Windows版.cmd`**，打开 Windows 原生窗口，并沿用当前项目的 `novels/`、`_roleplay/` 和 `llm_config.json`。可执行程序位于 `dist/windows-4.3.0/NovelGenerator/NovelGenerator.exe`。首次克隆仓库请按下文安装源码依赖或构建 Windows 程序。

独立分发：解压 `dist/NovelGenerator-v4.3.0-windows-x64.zip`，运行里面的 `NovelGenerator.exe`，无需安装 Python。独立运行的数据在 `%LOCALAPPDATA%/NovelGenerator`，也可通过 `--data-dir <目录>` 指定。窗口使用系统 WebView2；若窗口环境不可用，会回退到本机浏览器。

Windows 4.1.1 修复「开始扮演」进入后白屏：补回消息时间格式化函数，所有页面接入统一异常提示，并提供重新加载、返回书架和模型设置入口。

当前版本包含统一前端导航、模型配置入口、角色商店、按小说管理角色卡、真实参与小说剧情的入世模式，以及消息流与保存可靠性修复。安卓版本为 `4.3.0-internal`（versionCode 72），保留无原生顶部标题栏的界面。

仓库提交源码；`dist/` 中的 EXE、ZIP 和 APK 是本地构建产物，不随 Git 提交。README 中的 `dist/` 路径不代表 GitHub 下载链接。

首次使用在「设置」填写模型地址、名称和 API Key，保存并测试。模型用于小说创作、小说世界和角色扮演。密钥保存在本机 `llm_config.json`，读取接口不返回密钥。升级不会删除已有作品、角色或对话。

源码启动：`python run.py`（需安装根目录 requirements.txt），`--no-browser --port 8787` 仅启动服务。启动器会核对已有服务的版本和数据工作区，其他版本占用端口时会选用新端口。

推荐使用 Python 3.13。在 Windows PowerShell 中执行：

```powershell
git clone https://github.com/Kotabo-Zhao/NovelGenerator.git
cd NovelGenerator
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

## 手机浏览器访问

手机和电脑连接同一局域网。将下面的示例 IP 换成电脑当前的 IPv4 地址：

```powershell
.\.venv\Scripts\python.exe tools/serve_lan.py --host 192.168.0.101 --port 8791
```

手机打开终端显示的 `LAN_URL`，用户名为 `novel`，密码由服务每次启动随机生成，并显示为 `LAN_PASSWORD`。如需固定密码，可在启动进程的环境变量中设置 `NOVEL_LAN_PASSWORD`，不要写入源码。服务器只绑定指定的局域网 IP，所有页面和 API 均经过访问密码校验；沿用当前项目的作品、角色和模型配置。

若手机无法连接，请确认电脑防火墙允许该 Python 进程接收 TCP 8791 的局域网连接。停止服务按 `Ctrl+C`。主程序 `run.py` 仍默认只监听本机回环地址。

## 角色库与商店

底栏统一为「书架 / 创作 / 入世 / 角色 / 商店 / 设置」。桌面书架侧栏及每本书的「本书角色」也能进入。

- 商店读取 SillyTavern 官方角色资源，支持 Chub 社区搜索、分页和角色页链接预览。
- 预览设定、性格、开场白与作者说明后，可加入指定小说或未分组角色库；保留作者、扩展和世界书内容。
- 同来源卡片重复加入同一本小说时复用现有角色，保留已编辑的内容和聊天；加入不同小说则创建独立角色。
- 「整理」可以移动或复制角色。移动保留聊天，复制建立独立对话。支持本地 PNG / JSON 导入、完整编辑和 JSON 导出。
- 小说世界读取角色资料时优先使用本书卡片，同名角色不会串用另一部小说的设定。未分组卡片可作为通用资料。
- 商店是在线角色卡目录；作品、已下载角色和对话保存在本机。网络故障会提示重试，本地角色仍可使用。

## 创作

- 书架支持搜索、待开篇/创作中筛选，以及书名和章节数量排序。
- 创作工作台保留题材、风格、自然语言灵感、需求拆解、世界观与角色生成、大纲修改。
- 写作空间保留标准/原子化生成、文学/网文模式、批量章节、改稿、朗读、剧情图谱和质量监督。较少使用的约束工具收纳在可展开面板中。
- 支持 UTF-8 TXT、PDF、EPUB 单本导出与批量 TXT 导出。Windows PDF 自动使用系统中文字体，其他系统可通过 `NOVELGEN_CJK_FONT` 指定中文字体文件。

## 角色扮演

4.2 明确区分玩家 Persona 与 AI 卡：新对话先选「你扮演谁」，小说人物可作为玩家身份，世界卡可作为主持/旁白。每张卡分别保存玩家名字、设定、代词与叙事人称；已有消息保留原说话者。按 SillyTavern 的字段语义补齐末尾指令、角色深度提示、多行示例、世界书位置和正则处理；富文本状态栏可正常展示。详见 [身份与角色卡兼容说明](docs/ROLEPLAY-CARD-COMPATIBILITY.md)，其中明确列出了外部扩展与脚本执行器的边界。

- 导入 SillyTavern PNG、JSON V2/V3 角色卡，从小说角色宝典生成，或手动创建角色。
- 支持角色描述、性格、场景、开场白、备选开场白、示例对话和 `{{char}}` / `{{user}}` 宏。
- 安全渲染动作斜体、粗体与代码；不执行角色卡或消息里的 HTML。
- 流式回复、停止保存、重新生成、回复版本切换、JSON 角色卡导出。
- 清空并重开对话前自动留存历史快照。
- 不同角色可各自聊天；同一角色同时只允许一项生成或修改操作。

这是兼容角色卡与提示词结构的轻量角色扮演实现，不等同于完整 SillyTavern：不包含它的完整扩展生态和群聊系统；支持角色卡内嵌世界书与独立世界书导入。

## 数据与代码

小说保存在 `novels/`，角色扮演数据在 `_roleplay/`，模型连接在 `llm_config.json`。可通过 `NOVELS_DIR` / `ROLEPLAY_DIR` 改变存储位置。小说世界入口为 `scene.html?novel=<作品ID>`；正式小说与入世存档分开保存。

前端唯一源目录是 `web/`，后端为 `backend/`。详见 [架构说明](docs/ARCHITECTURE.md) 与 [本次重构与验收记录](docs/REFACTOR-20261019.md)。Android 内嵌源码和前端副本通过同步脚本生成。

## 安卓安装包

`dist/NovelGenerator-v4.3.0-arm64-debug.apk`，Android 8.0+，ARM64，版本号 72，内部 debug 版。同步玩家身份、人称、角色卡字段与富文本状态栏改进，并保留此前的白屏修复。与此前版本保持包名和 debug 签名，原生顶部标题栏继续移除。模型、角色库、商店和小说世界使用同一套前端。安卓 PDF / EPUB 暂缓，仍保留 TXT 导出。

构建及签名验证通过。没有连接安卓真机，实际安装、键盘和后台行为仍需真机验收。Windows 已验证打包后的 EXE 启动与内嵌 API/页面资源；浏览器核对了桌面和手机布局，真实模型长时间体验不属于这些技术检查。

## 构建与源码

前端唯一源目录是 `web/`。`app-shell.js` 管理共用导航，`runtime.js` 管理请求、SSE 与文本格式；角色扮演不再保留页面内嵌与外部文件两套实现。资源 URL 按内容哈希更新，避免 WebView 混用旧资源。

执行 `python tools/build_release.py --target all` 会准备资源、构建 Windows 并同步构建安卓；也可选择 `windows` 或 `android`。Windows 构建依赖见 `desktop/requirements.txt`，安卓需要 JDK 17 和 SDK。打包不包含作品、模型密钥和开发期数据库。

```powershell
# Windows：在安装根目录 requirements.txt 后安装打包依赖。
.\.venv\Scripts\python.exe -m pip install -r desktop/requirements.txt
.\.venv\Scripts\python.exe tools/build_release.py --target windows

# Android：设置为本机实际安装路径，构建 ARM64 内部测试 APK。
$env:JAVA_HOME = 'C:\dev\jdk-17'
$env:ANDROID_HOME = 'C:\dev\android-sdk'
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:CHAQUOPY_BUILD_PYTHON = (Resolve-Path '.\.venv\Scripts\python.exe').Path
.\.venv\Scripts\python.exe tools/build_release.py --target android
```

Windows 输出目录为 `dist/windows-4.3.0/NovelGenerator/`，分发时须保留整个目录；Android 输出为 `android/app/build/outputs/apk/debug/app-debug.apk`。本地沿用原 debug 签名可覆盖旧内部版；其他电脑自行构建的签名可能不同，请先备份数据。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
node tests/frontend_runtime.test.cjs
node tests/frontend_boot.test.cjs
.\.venv\Scripts\ruff.exe check backend --select F821,F811
node tests/novel_world_runtime.test.cjs
```

浏览器验收使用已安装的 Edge、本地模型测试服务和临时数据目录。真实模型连通性可在连接设置里单独测试；自动检查不代表真实模型的文学质量、长时间使用或 Android 真机验收。

4.2 已通过四个页面的初始化检查、消息流回归、270 项 Python 测试、真实官方 Amy 卡到模拟生成请求的字段核对、玩家身份与状态栏的桌面/手机尺寸浏览器检查，以及 Windows 成品启动、Android 构建及签名校验。Windows 原生窗口逐项点击复测、Android 真机安装和真实模型人称遵从度尚未完成。


### 角色扮演与世界书后续修复

- 扮演页采用正文优先布局：角色库、设置、世界书和对话操作收进二级菜单或抽屉；对话、动作、旁白、OOC 切换常驻显示。
- 世界书导入避免跨文件编号冲突、重复导入及容量超限时静默丢失；修复旧字段覆盖启用状态、空世界书导出恢复旧条目等问题。
- 统一商店旧版定义与 V2/V3 卡片转换，保留开场白、内嵌世界书及扩展，覆盖安装、导出、重新导入的往返验证。
- 本节修复已包含在 4.3.0 的 Windows 与安卓构建中。


## 4.3 小说世界参与重构

开局先选玩家角色、进入章节、剧情规则（允许分支 / 保守参与）和个人目标。原著正文按章节编号关联，缺失章节明确标为仅大纲；未来章节正文不注入当前生成。过去证据和当前章节参考分别检索，当前原著走向不等同于本局已经发生。

行动经过独立裁定，再提交物品、人物和事件结果。拒绝、条件设想、缺少物品、目标不在场或前置条件不足不会直接完成事件；模型不可用时保留未决。物品交付、拾取和消耗有实际库存变化；救援和战斗需要模型裁定，效果只通过受限规则提交。节点不再因场景数或提到关键词自动完成。允许分支时可以明确拒绝当前事件，记录拒绝后果并放弃完成奖励。

身份 / 事件 / 行动结果面板显示原著依据、当前目标、执行结果、完成事件和本局变化。旧存档可以保留进度升级，升级前建立快照；重开仍保留旧存档备份。小说正文不会被游玩结果覆盖。

验证覆盖隔离存档与模拟模型，不等于真实模型长时间游玩或安卓真机验收。细节见 [小说参与契约](docs/NOVEL-PARTICIPATION-4.3.md)。
