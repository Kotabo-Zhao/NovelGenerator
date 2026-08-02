# -*- coding: utf-8 -*-
"""v3.5.9 验证：状态总览（承诺/刚发生/关系明细）+ 角色记忆沉淀"""
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

    # 1. 状态总览区块齐全
    check('💞 关系明细', page.locator('.ps-rel-item').count() > 0)
    check('🎯 目标', page.locator('text=🎯 目标').count() > 0)
    check('📍 地点', page.locator('text=📍 地点').count() > 0)

    # 2. 发起对话 + 承诺 → 沉淀
    tb = page.locator('button:has-text("我要说话")')
    if tb.count():
        tb.first.click()
        page.wait_for_timeout(1000)
    inp = page.locator('.interactive-input input')
    if inp.count():
        inp.fill('我答应你，今晚一定赴约。')
        page.click('button:has-text("💬 说")', timeout=3000)

    # 等承诺提取（end-chat 后）
    import time
    t0 = time.time()
    fact_found = False
    while time.time() - t0 < 90:
        page.wait_for_timeout(5000)
        body = page.inner_text('body')
        if '⏳ 承诺' in body:
            fact_found = True
            break
    check('⏳ 承诺区出现', fact_found)
    if fact_found:
        # 承诺内容
        fact_txt = page.locator('.ps-fact-item').first.inner_text()
        print('  承诺内容:', fact_txt[:50])

    # 3. 刚发生的事件时间线
    page.wait_for_timeout(5000)
    body = page.inner_text('body')
    check('📌 刚发生区', '📌 刚发生' in body)
    check('零 JS 错误', len(errors) == 0, errors[:1] if errors else '')

    page.screenshot(path=r'C:\Users\Yan Zhao\WorkBuddy\Claw\NovelGenerator\outputs\status_overview_v359.png')
    browser.close()

print(f'═══ 状态总览验证: {PASS} 通过 / {FAIL} 失败 ═══')
