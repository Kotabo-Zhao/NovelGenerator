# 前端单文件巨石（P1-3）拆分评估

**评估日期**: 2026-07-31
**状态**: 已评估，建议延后（优先级低于生成质量优化）

## 现状

- `web/index.html` 3718 行：~560 行 CSS + ~1330 行 HTML 模板 + ~1800 行 JS
- JS 全部内联在**单个 setup() 函数**中：224 个函数、86 个 ref/reactive 状态、30+ computed，共享同一闭包作用域
- 无构建链（CDN Vue 3 + vue.global.prod.js 自托管）、无 Vue Router（手写 `currentView` + v-if）、无类型检查
- 三副本同步机制（web/ → backend/web + android/web）基于**最终产物文件**的 md5 对比

## 拆分方案（可行路径）

若未来拆分，推荐「源码多文件 + 构建拼接」而非直接切分：

```
web/src/                     ← 源码（新增）
  app.js                     ← createApp + setup 入口
  state.js                   ← 共享状态（86 ref 抽离）
  api.js                     ← 全部 fetch 调用集中
  views/shelf.js             ← 书架视图逻辑
  views/create.js            ← 开卷/追风视图逻辑
  views/write.js             ← 写作/质量视图逻辑
  views/storygraph.js        ← 剧情图谱 5 子 tab
  views/export.js            ← 付梓视图逻辑
tools/build_frontend.py      ← 拼接器：按固定顺序合并 → web/index.html（保持三副本机制不变）
```

关键约束：
1. **拼接顺序必须稳定**（state → api → views → app），否则闭包引用断裂
2. 构建后必须 `sync_frontend.py` 同步三副本，CI 的 --check 自然覆盖
3. 每次拆分一个视图，拆分后人工走一遍该视图全流程（无自动化前端测试兜底）

## 为什么建议延后

| 维度 | 评估 |
|:---|:---|
| 工作量 | 3-4 小时（组件化重构，非机械移动） |
| 风险 | **高**：无前端测试，闭包共享状态一处拆错 = 整个前端不可用 |
| 收益 | 可维护性（个人项目前端改动频率低，收益有限） |
| 与质量优化的关系 | 无前置依赖——生成质量优化不需要动前端结构 |

## 建议触发条件

- 前端开始接新的大型功能（如可视化编辑器、用户系统）时，先做拆分再开发
- 或出现第二个前端维护者时
