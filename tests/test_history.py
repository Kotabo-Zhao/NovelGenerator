# -*- coding: utf-8 -*-
"""v3.5.10 验证：历史场景保留 + 场景分隔 + 智能滚动"""
import sys
sys.path.insert(0, '.')
from playwright.sync_api import sync_playwright

PASS, FAIL = 0, 0

def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  OK {name} {detail}')
    else:
        FAIL += 1
        print(f'  XX {name} {detail}')

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1100, 'height': 800})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)[:150]))
    page.goto('http://127.0.0.1:8787', wait_until='networkidle', timeout=30000)
    page.wait_for_timeout(1500)
    page.click('text=替身的告别', timeout=8000)
    page.wait_for_timeout(1500)
    page.click('text=🎮 互动模式', timeout=8000)
    for i in range(14):
        page.wait_for_timeout(5000)
        if page.locator('.interactive-shell').count() > 0:
            break
    page.wait_for_timeout(6000)

    # 1. 初始场景块数量
    narr0 = page.locator('.inter-narr').count()
    print(f'  [初始旁白块: {narr0}]')

    # 2. 发起行动 → 自动流推进 → 检查历史保留 + 分隔线
    tb = page.locator('button:has-text("我要说话")')
    if tb.count():
        tb.first.click()
        page.wait_for_timeout(1000)
    inp = page.locator('.interactive-input input')
    if inp.count():
        inp.fill('我答应你，我们重新开始。')
        page.click('button:has-text("💬 说")', timeout=3000)

    # 等自动流推进 2+ 场景（最长 100s）
    import time
    t0 = time.time()
    divs = 0
    while time.time() - t0 < 100:
        page.wait_for_timeout(6000)
        d = page.locator('.inter-scene-div').count()
        if d >= 2:
            divs = d
            break
        if d > divs:
            divs = d
    print(f'  [场景分隔线: {divs} 条]')
    check('场景分隔线出现', divs >= 1, f'{divs} 条')
    narr1 = page.locator('.inter-narr').count()
    check('历史旁白保留（增长）', narr1 > narr0, f'{narr0} → {narr1}')
    check('分隔线内容含场景号', page.locator('.inter-scene-div').first.inner_text().strip().startswith('──'))

    # 3. 智能滚动：回看历史时自动流不抢滚动
    story = page.locator('.interactive-story')
    story.evaluate('(el) => { el.scrollTop = 0; }')   # 滚回顶部（回看）
    page.wait_for_timeout(1000)
    at_bottom_before = page.evaluate('''() => {
      const el = document.querySelector('.interactive-story');
      return (el.scrollHeight - el.scrollTop - el.clientHeight) < 120;
    }''')
    check('回看状态检测（不在底部）', not at_bottom_before)
    # 等 8s（自动流 delay 5s）→ 滚动位置不应被强制拉到底
    page.wait_for_timeout(8000)
    at_bottom_after = page.evaluate('''() => {
      const el = document.querySelector('.interactive-story');
      return (el.scrollHeight - el.scrollTop - el.clientHeight) < 120;
    }''')
    check('回看时不被抢滚动', not at_bottom_after)
    # 滚回底部 → 自动流恢复跟随
    story.evaluate('(el) => { el.scrollTop = el.scrollHeight; }')
    page.wait_for_timeout(1000)
    at_bottom_final = page.evaluate('''() => {
      const el = document.querySelector('.interactive-story');
      return (el.scrollHeight - el.scrollTop - el.clientHeight) < 120;
    }''')
    check('滚回底部恢复跟随', at_bottom_final)

    check('零 JS 错误', len(errors) == 0, errors[:1] if errors else '')
    page.screenshot(path=r'C:\Users\Yan Zhao\WorkBuddy\Claw\NovelGenerator\outputs\history_v3510.png')
    browser.close()

print(f'═══ 历史保留验证: {PASS} 通过 / {FAIL} 失败 ═══')
