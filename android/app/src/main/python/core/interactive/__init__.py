"""interactive 包 — 互动小说模式（v3.0）

对标 docs/interactive-novel-plan.html：
- story_director.py   剧情引擎：场景生成 + 节点检测三层保障 + 目标锚定 + PACT 提取 + 回扣验证
- dialogue_engine.py  对话引擎：多轮对话 + 角色卡三明治 + @角色切换 + OOC 抽检
- interact_store.py   存档层：原子写/快照/日志重放
- voice_director.py   音色映射（Phase 2 实现，v1 用规则版）
"""
