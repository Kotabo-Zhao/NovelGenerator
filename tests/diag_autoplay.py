# -*- coding: utf-8 -*-
"""诊断：行动后自动流为何不触发"""
import sys
sys.path.insert(0, '.')
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1100, 'height': 800})
    logs = []
    page.on('console', lambda m: logs.append(f'{m.type}: {m.text[:150]}') if m.type in ('error', 'warning', 'log') else None)
    page.on('pageerror', lambda e: logs.append(f'PAGEERROR: {str(e)[:200]}'))
    page.goto('http://127.0.0.1:8787', wait_until='networkidle', timeout=30000)
    page.wait_for_timeout(1500)
    page.click('text=替身的告别', timeout=8000)
    page.wait_for_timeout(1500)
    page.click('text=🎮 互动模式', timeout=8000)
    for i in range(14):
        page.wait_for_timeout(5000)
        if page.locator('.interactive-shell').count() > 0: break
    page.wait_for_timeout(6000)

    # 主动发起对话
    tb = page.locator('button:has-text("我要说话")')
    print('我要说话按钮:', tb.count())
    if tb.count():
        tb.first.click()
        page.wait_for_timeout(1000)
    inp = page.locator('.interactive-input input')
    print('点击后输入框:', inp.count())
    if inp.count():
        inp.fill('我答应你，今晚就去。')
        page.click('button:has-text("💬 说")', timeout=3000)
        print('消息已发送')
    page.wait_for_timeout(15000)

    # 检查 blocks 类型分布
    blocks = page.locator('.interactive-shell').inner_text()
    import re
    sc = re.search(r'场景\s*(\d+)', blocks)
    print('15s 后场景号:', sc.group(1) if sc else '?')
    print('页面有 action_desc:', page.locator('.inter-action-desc').count())
    print('页面有 action_result:', page.locator('.inter-action-result').count())
    print('页面有 chat 气泡:', page.locator('.inter-bubble').count())
    print('可对话标记:', page.locator('text=可对话').count())
    print('已暂停标记:', page.locator('text=已暂停').count())
    print('输入框还在:', page.locator('.interactive-input input').count())

    # 等 20s 看自动推进
    page.wait_for_timeout(20000)
    blocks = page.locator('.interactive-shell').inner_text()
    sc = re.search(r'场景\s*(\d+)', blocks)
    print('35s 后场景号:', sc.group(1) if sc else '?')
    print('--- console 日志 ---')
    for l in logs[-15:]:
        print(' ', l)
    browser.close()
