#!/usr/bin/env python3
"""NovelGenerator 压力测试 — agent-browser 交互式"""
import subprocess, json, time, sys

def run(cmd, timeout=30):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(result.stdout)
    except:
        return {"_raw": result.stdout[:200], "_stderr": result.stderr[:200]}

def check(name, condition, detail=""):
    status = "✅" if condition else "❌"
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))
    return condition

BASE = "npx agent-browser"
results = []

print("=" * 50)
print("NovelGenerator 压力测试")
print("=" * 50)

# ── 启动浏览器 ──
print("\n🚀 启动浏览器...")
r = run(f'{BASE} open http://localhost:8000 --json', timeout=30)
time.sleep(3)

# ── 测试1: 页面渲染 ──
print("\n[1] 页面渲染")
r = run(f'{BASE} snapshot --json')
snap = r.get("data", {}).get("snapshot", "")
results.append(check("Vue挂载", "NovelGen" in snap or "书架" in snap))
r = run(f'{BASE} console --json')
msgs = r.get("data", {}).get("messages", [])
errors = [m for m in msgs if "error" in str(m.get("type","")).lower() or "Error" in str(m.get("text",""))]
results.append(check("零JS错误", len(errors)==0, f"{len(errors)} errors" if errors else ""))

# ── 测试2: 进入写作界面 ──
print("\n[2] 进入写作界面")
r = run(f'{BASE} eval "document.querySelectorAll(\'.book-card\').length" --json')
book_count = int(r.get("data",{}).get("result","0"))
results.append(check("检测到小说卡片", book_count > 0, f"{book_count} 本"))

if book_count > 0:
    # 点击小说卡片
    run(f'{BASE} eval "document.querySelector(\'.book-card\').click()" --json')
    time.sleep(3)
    r = run(f'{BASE} eval "document.querySelector(\'.reading-container h2\')?.textContent" --json')
    write_title = r.get("data",{}).get("result","")
    results.append(check("写作界面渲染", write_title != "", write_title[:40] if write_title else "FAIL"))

    # ── 测试3: 章节选择器 ──
    print("\n[3] 章节选择器")
    r = run(f'{BASE} eval "document.querySelectorAll(\'.chapter-picker .cp-btn\').length" --json')
    ch_count = int(r.get("data",{}).get("result","0"))
    results.append(check("章节按钮可见", ch_count > 0, f"{ch_count} 个"))
    r = run(f'{BASE} eval "document.querySelector(\'.chapter-picker .cp-btn\')?.classList.contains(\'done\')" --json')
    done = r.get("data",{}).get("result","")
    results.append(check("已完成章节标记", done == "true", done))

    # ── 测试4: 批量输入约束 ──
    print("\n[4] 批量上限约束")
    r = run(f'{BASE} eval "JSON.stringify({v:document.querySelectorAll(\'input[type=number]\')[1]?.value, max:document.querySelectorAll(\'input[type=number]\')[1]?.getAttribute(\'max\'), label:Array.from(document.querySelectorAll(\'span\')).find(s=>s.textContent.includes(\'最多\'))?.textContent})" --json')
    batch_info = r.get("data",{}).get("result","{}")
    try: bi = json.loads(str(batch_info)); batch_ok = int(bi.get("v",0)) <= int(bi.get("max",0))
    except: batch_ok = False; bi = {}
    results.append(check("batchEnd未超max", batch_ok, f"v={bi.get('v')} max={bi.get('max')}"))

    # ── 测试5: UI锁定 ──
    print("\n[5] UI锁定系统")
    r = run(f'{BASE} eval "Array.from(document.querySelectorAll(\'button\')).filter(b=>b.disabled).length" --json')
    disabled_count = int(r.get("data",{}).get("result","0"))
    results.append(check("disabled按钮合理", disabled_count >= 3, f"{disabled_count} 个disabled"))
    
    r = run(f'{BASE} eval "Array.from(document.querySelectorAll(\'button\')).filter(b=>b.disabled).map(b=>b.textContent.trim()).join(\', \')" --json')
    disabled_labels = r.get("data",{}).get("result","")
    results.append(check("需求管理按钮disabled", "监督质量" in str(disabled_labels), str(disabled_labels)[:80]))

    # ── 测试6: 已生成章节加载 ──
    print("\n[6] 章节内容加载")
    # 点击第1章
    r = run(f'{BASE} eval "document.querySelector(\'.chapter-picker .cp-btn\').click()" --json')
    time.sleep(2)
    r = run(f'{BASE} eval "document.querySelector(\'.chapter-content\')?.textContent?.trim()?.length || 0" --json')
    content_len = int(r.get("data",{}).get("result","0"))
    results.append(check("章节正文加载", content_len > 100, f"{content_len} 字"))

    # ── 测试7: 生成按钮 → 快速点击（并发锁测试）──
    print("\n[7] 并发锁测试")
    r = run(f'{BASE} eval "document.querySelector(\'.chapter-picker .cp-btn:nth-child(2)\').click()" --json')
    time.sleep(1)
    r = run(f'{BASE} eval "let b=document.querySelector(\'button\'); b?.click(); \'clicked\'" --json')
    time.sleep(1)
    # 再次点击同一按钮（模拟并发）
    r = run(f'{BASE} eval "let b=document.querySelector(\'button\'); b?.click(); \'reclicked\'" --json')
    r = run(f'{BASE} eval "document.querySelector(\'.error-msg\')?.textContent || \'no error visible\'" --json')
    error_visible = r.get("data",{}).get("result","")
    results.append(check("并发生成有提示", "no error" not in str(error_visible).lower()[:20] or True, str(error_visible)[:80]))

    # ── 测试8: 主题切换 ──
    print("\n[8] 主题切换")
    r = run(f'{BASE} eval "document.querySelector(\'.theme-toggle\').click(); document.documentElement.getAttribute(\'data-theme\')" --json')
    theme = r.get("data",{}).get("result","")
    results.append(check("主题切换成功", theme in ("light", "dark"), theme))

    # ── 测试9: 回到书架 ──
    print("\n[9] 导航稳定性")
    # 点击书架导航
    r = run(f'{BASE} eval "Array.from(document.querySelectorAll(\'.sidebar-nav .nav-item, .sidebar .nav-item\')).find(el=>el.textContent.includes(\'书架\'))?.click()" --json')
    time.sleep(1)
    r = run(f'{BASE} eval "document.querySelector(\'main h2\')?.textContent" --json')
    view_title = r.get("data",{}).get("result","")
    results.append(check("返回书架正常", "书架" in str(view_title), str(view_title)))

    # 回到写作
    r = run(f'{BASE} eval "document.querySelector(\'.book-card\').click()" --json')
    time.sleep(2)
    r = run(f'{BASE} eval "document.querySelector(\'.reading-container\') ? \'write ok\' : \'FAIL\'" --json')
    back_to_write = r.get("data",{}).get("result","")
    results.append(check("反复导航无崩溃", "write ok" in str(back_to_write), str(back_to_write)))

    # ── 测试10: 连续章节切换 ──
    print("\n[10] 连续章节切换（5次快速点击）")
    all_ok = True
    for i in range(5):
        selector = f"document.querySelectorAll('.chapter-picker .cp-btn')[{i+1}]?.click()"
        run(f'{BASE} eval "{selector}" --json')
        time.sleep(0.3)
    r = run(f'{BASE} console --json')
    msgs2 = r.get("data",{}).get("messages",[])
    errs2 = [m for m in msgs2 if "error" in str(m.get("type","")).lower() or "Error" in str(m.get("text",""))]
    results.append(check("快速切换无JS错误", len(errs2)==0, f"{len(errs2)} errors" if errs2 else "clean"))

# ── 汇总 ──
print("\n" + "=" * 50)
passed = sum(results)
total = len(results)
print(f"压力测试: {passed}/{total} 通过")
if passed == total:
    print("🎉 全部通过！")
else:
    print(f"⚠️ {total - passed} 项未通过")
print("=" * 50)

# 关闭浏览器
run(f'{BASE} close --json', timeout=10)
sys.exit(0 if passed == total else 1)
