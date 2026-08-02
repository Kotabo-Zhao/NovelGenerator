# -*- coding: utf-8 -*-
"""诊断 v2：行动后自动推进 + 面板消失原因"""
import sys
sys.path.insert(0, '.')
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1100, 'height': 800})
    logs = []
    page.on('console', lambda m: logs.append(f'{m.type}: {m.text[:200]}'))
    page.on('pageerror', lambda e: logs.append(f'PAGEERROR: {str(e)[:250]}'))
    page.on('requestfailed', lambda r: logs.append(f'REQFAIL: {r.url[:100]} {r.failure}'))
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

    tb = page.locator('button:has-text("我要说话")')
    if tb.count():
        tb.first.click()
        page.wait_for_timeout(1000)
    inp = page.locator('.interactive-input input')
    if inp.count():
        inp.fill('我答应你，今晚就去。')
        page.click('button:has-text("💬 说")', timeout=3000)
    page.wait_for_timeout(8000)
    print('8s: shell=', page.locator('.interactive-shell').count(),
          'action_desc=', page.locator('.inter-action-desc').count(),
          'input=', page.locator('.interactive-input input').count())

    # 轮询观察 40s：场景号 / 面板存在性 / 网络
    import re
    for i in range(8):
        page.wait_for_timeout(5000)
        shell = page.locator('.interactive-shell').count()
        body_txt = page.inner_text('body')
        sc = re.search(r'场景\s*(\d+)', body_txt)
        node = '可对话' in body_txt
        pause = '已暂停' in body_txt
        print(f'{(i+1)*5+8}s: shell={shell} 场景={sc.group(1) if sc else "?"} 可对话={node} 已暂停={pause}')
        if not shell:
            print('  面板消失！当前 URL:', page.url)
            break

    print('--- console 日志（后 20 条）---')
    for l in logs[-20:]:
        print(' ', l)
    browser.close()
