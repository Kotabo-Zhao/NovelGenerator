# 墨局 · NovelGenerator 4.0

本地小说创作与沉浸式角色扮演工作台。保留世界观、大纲、章节写作、剧情图谱、质量监督与导出；互动体验统一为兼容 SillyTavern 角色卡的角色扮演模式。

## 在这台电脑上使用

双击根目录 **`启动墨局.cmd`**，打开 <http://127.0.0.1:8787>。

首次使用在「连接设置」中填写 API 地址、模型名称和 API Key，再点「保存并测试」。支持 DeepSeek 与 OpenAI 兼容接口，本地模型可以不填 Key。密钥仅保存在本机 `_settings/provider.json`，不会通过读取接口返回。环境变量和原有根目录 `.env` 仍可在首次启动时提供配置；保存的界面设置会在以后启动时继续生效。

命令行启动：

```powershell
.\.venv\Scripts\python.exe -X utf8 run.py
```

新设备安装（Python 3.10 及以上）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 run.py
```

`run.py --no-browser --port 8787` 可只启动服务。关闭启动窗口或按 Ctrl+C 停止服务。

## 创作

- 书架支持搜索、待开篇/创作中筛选，以及书名和章节数量排序。
- 创作工作台保留题材、风格、自然语言灵感、需求拆解、世界观与角色生成、大纲修改。
- 写作空间保留标准/原子化生成、文学/网文模式、批量章节、改稿、朗读、剧情图谱和质量监督。较少使用的约束工具收纳在可展开面板中。
- 支持 UTF-8 TXT、PDF、EPUB 单本导出与批量 TXT 导出。Windows PDF 自动使用系统中文字体，其他系统可通过 `NOVELGEN_CJK_FONT` 指定中文字体文件。

## 角色扮演

- 导入 SillyTavern PNG、JSON V2/V3 角色卡，从小说角色宝典生成，或手动创建角色。
- 支持角色描述、性格、场景、开场白、备选开场白、示例对话和 `{{char}}` / `{{user}}` 宏。
- 安全渲染动作斜体、粗体与代码；不执行角色卡或消息里的 HTML。
- 流式回复、停止保存、重新生成、消息编辑、对话导出、角色卡导出、按角色保存草稿。
- 开启新对话前保存历史快照，可点击「恢复上次对话」交换恢复。
- 不同角色可各自聊天；同一角色同时只允许一项生成或修改操作。

这是兼容角色卡与提示词结构的轻量角色扮演实现，不等同于完整 SillyTavern：不包含它的扩展生态、群聊和完整世界书系统。

## 数据与代码

小说保存在 `novels/`，角色扮演数据在 `_roleplay/`，模型连接在 `_settings/`。可通过 `NOVELS_DIR` / `ROLEPLAY_DIR` 改变存储位置。旧互动入口和代码已移除，既有小说与旧互动存档不删除。

前端唯一源目录是 `web/`，后端为 `backend/`。详见 [架构说明](docs/ARCHITECTURE.md) 与 [本次重构与验收记录](docs/REFACTOR-20261019.md)。Android 内嵌源码和前端副本通过同步脚本生成。

## 安卓安装包

新版安装包：[NovelGenerator 4.0.1 · arm64 · debug](https://github.com/Kotabo-Zhao/NovelGenerator/releases/download/v4.0.1/NovelGenerator-v4.0.1-arm64-debug.apk)，约 30 MiB，支持 Android 8.0 及以上的 ARM64 手机。已核对与本目录旧版 2.5.68 的包名及签名一致，版本号提高到 58，支持覆盖升级并沿用原数据目录。安卓顶部的原生标题栏与常驻连接条已移除，页面加载完成后直接显示创作界面。

首次打开可直接进入书架，再在「模型连接」中填写连接。角色卡支持系统文件选择器；TXT、角色卡和聊天导出在 Android 10 及以上保存到「下载/墨局」，Android 8/9 通过系统分享菜单保存。按当前要求，**安卓 PDF / EPUB 导出暂缓，保留 TXT 导出**。

构建、签名与内嵌源码检查通过；没有连接安卓真机，实际安装、文件选择/保存、键盘与后台行为未进行真机验收。详见 [安卓 4.0.1 交付记录](docs/ANDROID-4.0.1.md)。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
node tests/frontend_runtime.test.cjs
.\.venv\Scripts\ruff.exe check backend --select F821,F811
.\.venv\Scripts\python.exe tools/verify_browser.py
```

浏览器验收使用已安装的 Edge、本地模型测试服务和临时数据目录。真实模型连通性可在连接设置里单独测试；自动检查不代表真实模型的文学质量、长时间使用或 Android 真机验收。
