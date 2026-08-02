# -*- coding: utf-8 -*-
"""v3.5.13 验证：开场背景卡显示 + 可折叠"""
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
    page.wait_for_timeout(10000)

    # 1. 背景卡出现
    check('背景卡出现', page.locator('.inter-intro').count() > 0)
    if page.locator('.inter-intro').count():
        body_txt = page.locator('.inter-intro-body').inner_text()
        print(f'  背景内容({len(body_txt)}字): {body_txt[:80]}…')
        check('背景内容充实', len(body_txt) > 100)
        check('包含主角', '沈念薇' in body_txt or '你' in body_txt)
        # 2. 折叠/展开
        page.click('.inter-intro-head')
        page.wait_for_timeout(500)
        check('点击可折叠', page.locator('.inter-intro-body').count() == 0)
        page.click('.inter-intro-head')
        page.wait_for_timeout(500)
        check('再次点击展开', page.locator('.inter-intro-body').count() > 0)
    # 3. 剧情正常渲染（背景不阻塞场景）
    check('场景旁白渲染', page.locator('.inter-narr').count() > 0)
    check('零 JS 错误', len(errors) == 0, errors[:1] if errors else '')
    page.screenshot(path=r'C:\Users\Yan Zhao\WorkBuddy\Claw\NovelGenerator\outputs\intro_v3513.png')
    browser.close()

print(f'═══ 开场背景验证: {PASS} 通过 / {FAIL} 失败 ═══')
