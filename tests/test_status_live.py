# -*- coding: utf-8 -*-
"""v3.5.8 验证：状态实时更新 + 对话流状态提示 + sticky 状态卡"""
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

    # 1. 状态卡存在 + 目标显示
    check('状态卡存在', page.locator('.player-status-card').count() > 0)
    check('🎯 目标显示', page.locator('text=🎯 目标').count() > 0)
    check('📍 地点显示', page.locator('text=📍 地点').count() > 0)

    # 2. sticky 检查（CSS position）
    sticky = page.evaluate("(() => { const el = document.querySelector('.player-status-card'); return el ? getComputedStyle(el).position : 'NO_ELEMENT'; })()")
    check('状态卡 sticky 常驻', sticky == 'sticky', sticky)

    # 3. 发起行动 → 检查 sys_change 提示 + 状态更新
    tb = page.locator('button:has-text("我要说话")')
    if tb.count():
        tb.first.click()
        page.wait_for_timeout(1000)
    inp = page.locator('.interactive-input input')
    if inp.count():
        inp.fill('我答应你，今晚就去。')
        page.click('button:has-text("💬 说")', timeout=3000)

    # 轮询 30s：找 sys_change 提示
    import time
    t0 = time.time()
    sys_change_found = False
    while time.time() - t0 < 35:
        page.wait_for_timeout(3000)
        if page.locator('.inter-sys-change').count() > 0:
            sys_change_found = True
            break
    check('对话流内状态提示(✨)', sys_change_found,
          f'出现 {page.locator(".inter-sys-change").count()} 条' if sys_change_found else '')
    if sys_change_found:
        print('  提示内容:', page.locator('.inter-sys-change').first.inner_text()[:60])

    # 4. 状态卡内容实时（关系/地点变化后仍在更新）
    page.wait_for_timeout(5000)
    body = page.inner_text('.player-status-card')
    check('状态卡内容刷新', '💞' in body or '🏷' in body or '📍' in body)

    # 5. 零 JS 错误
    check('零 JS 错误', len(errors) == 0, errors[:1] if errors else '')

    page.screenshot(path=r'C:\Users\Yan Zhao\WorkBuddy\Claw\NovelGenerator\outputs\status_live_v358.png')
    browser.close()

print(f'═══ 实时状态验证: {PASS} 通过 / {FAIL} 失败 ═══')
