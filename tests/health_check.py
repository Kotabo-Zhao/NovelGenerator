#!/usr/bin/env python3
"""NovelGenerator 全模块健康检查"""
import json, sys, time
import urllib.request, urllib.error

BASE = "http://localhost:8000"
NOVEL_ID = "逆天废材：从悬崖开始的无敌路"
ENCODED = urllib.parse.quote(NOVEL_ID)
OK, FAIL, WARN = "✅", "❌", "⚠️"

def req(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method,
        headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw)
            except:
                return resp.status, {"_raw": raw.decode("utf-8", errors="replace")[:500]}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except:
            return e.code, {"_raw": raw.decode("utf-8", errors="replace")[:500], "_error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}

def check(name, code, result, keys=None, condition=None):
    if code == 200 and (keys is None or all(k in result for k in keys)):
        if condition is None or condition(result):
            print(f"  {OK} {name}")
            return True
    print(f"  {FAIL} {name} (HTTP {code}): {json.dumps(result, ensure_ascii=False)[:150]}")
    return False

print("=" * 50)
print("NovelGenerator 全模块健康检查")
print("=" * 50)

results = []

# ── 基础 ──
print("\n📡 基础服务")
s, r = req("GET", "/api/health")
results.append(check("健康检查", s, r, ["status","novel_count"]))
s, r = req("GET", "/")
results.append(check("前端HTML", s, r, condition=lambda r: "NovelGenerator" in str(r) or "index.html" in str(r)))
s, r = req("GET", "/vue.global.prod.js")
results.append(check("Vue.js静态文件", s, r, condition=lambda r: len(str(r)) > 1000))

# ── 书架 ──
print("\n📚 书架")
s, r = req("GET", "/api/novels")
results.append(check("书架列表", s, r, ["novels"]))
if "novels" in r:
    print(f"    小说数: {len(r['novels'])} 本")

# ── 风格系统 ──
print("\n🎨 风格系统")
s, r = req("GET", "/api/styles")
results.append(check("风格库", s, r, ["categories"]))
if "categories" in r:
    cats = r["categories"]
    total = sum(len(v) for v in cats.values())
    print(f"    分类数: {len(cats)} | 风格总数: {total}")

s, r = req("GET", "/api/styles/params")
results.append(check("参数化风格配置", s, r, ["params"]))

s, r = req("GET", "/api/styles/seeds")
results.append(check("风格种子列表", s, r, ["seeds"]))

# ── 小说数据 ──
print(f"\n📖 小说: {NOVEL_ID}")
s, r = req("GET", f"/api/novels/{ENCODED}")
results.append(check("小说详情", s, r, ["novel"]))
if "novel" in r:
    n = r["novel"]
    print(f"    题材: {n.get('genre','?')} | 风格: {n.get('style','?')}")
    outline = n.get("outline", {})
    vols = len(outline.get("volumes", []))
    chs = outline.get("total_chapters", 0)
    print(f"    卷数: {vols} | 章数: {chs}")
    state = n.get("state", {})
    done = state.get("completed_chapters", [])
    print(f"    已完成: {len(done)} 章")

# ── 大纲校验 ──
print("\n📋 大纲校验")
s, r = req("GET", f"/api/novels/{ENCODED}/validate-outline")
results.append(check("大纲一致性校验", s, r, ["result"]))
if "result" in r:
    res = r["result"]
    print(f"    通过: {res.get('passed')} | 得分: {res.get('score')}/100")
    v_count = len(res.get("violations", []))
    w_count = len(res.get("warnings", []))
    print(f"    违规: {v_count} | 警告: {w_count}")

# ── 逻辑监督 ──
print("\n🔍 逻辑监督 (L1模式)")
ch_num = 1
s, r = req("POST", f"/api/novels/{ENCODED}/logic-check/{ch_num}", {"run_deep": False})
results.append(check(f"第{ch_num}章逻辑监督", s, r, ["result"]))
if "result" in r:
    res = r["result"]
    print(f"    通过: {res.get('passed')} | 总分: {res.get('score')}/100")
    v_count = len(res.get("violations", []))
    w_count = len(res.get("warnings", []))
    s_count = len(res.get("suggestions", []))
    print(f"    违规: {v_count} | 警告: {w_count} | 建议: {s_count}")
    cat_scores = res.get("category_scores", {})
    weak = [(c, s) for c, s in cat_scores.items() if s < 90]
    if weak:
        print(f"    薄弱项: {', '.join(f'{c}({s})' for c, s in weak)}")
    else:
        print(f"    全部达标")

# ── 章节API ──
print("\n📝 章节API")
s, r = req("GET", f"/api/novels/{ENCODED}/chapters/{ch_num}")
results.append(check(f"获取第{ch_num}章", s, r, condition=lambda r: "content" in r or "error" in r))

s, r = req("GET", f"/api/novels/{ENCODED}/chapters/{ch_num}/exists")
results.append(check(f"章节存在检查", s, r, ["exists"]))

# ── Token预算 ──
s, r = req("GET", f"/api/novels/{ENCODED}/token-budget")
results.append(check("Token预算", s, r, ["budget"]))

# ── 人物宝典 ──
s, r = req("GET", f"/api/novels/{ENCODED}/character-bible")
results.append(check("人物宝典生成", s, r, condition=lambda r: "bible" in str(r) or "尚未生成" in str(r) or "character" in str(r).lower()))

# ── 导出 ──
print("\n📤 导出")
s, r = req("GET", f"/api/novels/{ENCODED}/export?fmt=txt")
results.append(check("TXT导出", s, r, condition=lambda r: len(str(r)) > 10))

# ── 需求管理 ──
print("\n🧪 需求管理")
s, r = req("POST", f"/api/novels/{ENCODED}/requirements/decompose", 
    {"inspiration": "写一个废材逆袭的修仙爽文，主角有金手指玉佩"})
results.append(check("需求拆解", s, r, ["requirements"]))

if "requirements" in r:
    s, r = req("POST", f"/api/novels/{ENCODED}/requirements/supervise", {})
    results.append(check("需求监督", s, r, ["overall_score"]))

# ── 风格指纹 ──
print("\n🔬 风格分析")
s, r = req("POST", "/api/styles/fingerprint", {
    "text": "林玄站在悬崖边缘，冷风灌入衣襟。他握紧了手中的玉佩，眼中闪过一丝决然。",
    "style": "热血爽文"
})
results.append(check("风格指纹分析", s, r, ["fingerprint"]))

# ── 汇总 ──
print("\n" + "=" * 50)
passed = sum(1 for r in results if r)
total = len(results)
print(f"总计: {passed}/{total} 通过")
if passed == total:
    print("🎉 全部模块正常运行！")
else:
    print(f"⚠️ {total - passed} 项检查失败，需要排查")
print("=" * 50)
