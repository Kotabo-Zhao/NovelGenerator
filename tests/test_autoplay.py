# -*- coding: utf-8 -*-
"""v3.5.7 剧情自动流验证：行动/对话后剧情是否自动推进"""
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
    page.on('pageerror', lambda e: errors.append(str(e)[:120]))
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
    check('互动面板', page.locator('.interactive-shell').count() > 0)
    check('自动推进开关', page.locator('button:has-text("⏵")').count() > 0)
    check('默认开启', page.locator('button:has-text("⏵ 自动")').count() > 0)

    # 开场节点（必触发）→ 输入行动
    inp = page.locator('.interactive-input input')
    if inp.count():
        inp.fill('我答应你，今晚就去。')
        page.click('button:has-text("💬 说")', timeout=3000)
        page.wait_for_timeout(4000)
        print('  [输入行动: 我答应你，今晚就去。]')
    else:
        print('  [无输入框——尝试点我要说话]')
        tb = page.locator('button:has-text("我要说话")')
        if tb.count():
            tb.first.click()
            page.wait_for_timeout(1000)
            inp = page.locator('.interactive-input input')
            if inp.count():
                inp.fill('我答应你，今晚就去。')
                page.click('button:has-text("💬 说")', timeout=3000)
                page.wait_for_timeout(4000)

    # 记录初始场景号
    def scene_num():
        try:
            txt = page.locator('.interactive-shell').inner_text()
            import re
            m = re.search(r'场景\s*(\d+)', txt)
            return int(m.group(1)) if m else 0
        except Exception:
            return 0

    s0 = scene_num()
    print(f'  [初始场景号: {s0}]')

    # 等待自动推进（行动→结果→自动下一场景，最长 90s）
    import time
    t0 = time.time()
    max_s = s0
    progressed = False
    while time.time() - t0 < 90:
        page.wait_for_timeout(5000)
        s = scene_num()
        if s > max_s:
            max_s = s
            progressed = True
        # 推进发生过且出现"可对话"节点 → 停下（正确行为）
        if progressed and (page.locator('text=💬 可对话').count() > 0 or page.locator('text=已暂停').count() > 0):
            break
    print(f'  [观察结束: 场景 {s0} → {max_s}]')
    check('行动后剧情自动推进', max_s > s0, f'场景数增加 {s0}→{max_s}')
    check('零 JS 错误', len(errors) == 0, errors[:1] if errors else '')

    # 检查"手动推进"按钮是否已非必需（autoPlay 开启时不应出现强制等待）
    page.screenshot(path=r'C:\Users\Yan Zhao\WorkBuddy\Claw\NovelGenerator\outputs\autoplay_v357.png')
    browser.close()

print(f'═══ 自动流验证: {PASS} 通过 / {FAIL} 失败 ═══')
