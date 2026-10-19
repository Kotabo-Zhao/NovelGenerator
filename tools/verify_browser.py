"""Isolated browser acceptance using a local model fixture; no user data touched."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/refactor-20261019'
OUT.mkdir(parents=True, exist_ok=True)
EXPECTED = '她抬起头，把茶杯推到你面前。\n\n「你好，旅行者。雨还在下。🌙」'


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def ready(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
        try:
            with opener.open(url, timeout=.5):
                return
        except OSError:
            time.sleep(.1)
    raise RuntimeError('Server did not become ready')


def seed(root):
    for index, title in enumerate(['长街听雨', '星河的来信', '山海之间']):
        folder = root / f'sample-{index}'
        (folder / 'chapters').mkdir(parents=True)
        plan = {'title': title, 'genre': ['古风','科幻','玄幻'][index], 'style': '温馨治愈', 'target_words':30000,
                'inspiration':'旧城里的重逢。', 'worldbuilding':{'era':'近代','geography':'旧城','core_conflict':'寻找一封失落的来信','power_system':'无','factions':[]},
                'characters':{'protagonist':{'name':'苏晚','identity':'书斋店主','personality':'温柔而敏锐'},'supporting':[],'antagonist':[],'bible_summary':'旧友的重逢'},
                'outline':{'total_chapters':3,'volumes':[{'number':1,'title':'旧城来信','chapters':[{'number':i,'title':'雨夜来客','summary':'在旧城相逢，寻找来信。','conflict':'记忆与真相','characters':['苏晚'],'target_words':1500} for i in range(1,4)]}]},
                '_meta':{'created_at':'2026-10-19T10:00:00','model':'test'}}
        (folder/'plan.json').write_text(json.dumps(plan,ensure_ascii=False),encoding='utf-8')
        for number in range(1,index+1):
            (folder/'chapters'/f'chapter_{number:04d}.md').write_text('# 雨夜来客\n\n细雨敲着窗。苏晚合上那本旧书。\n\n「进来吧，茶还热着。」',encoding='utf-8')
        state={'completed_chapters':list(range(1,index+1)),'total_chapters':3,'current_chapter':index,'total_words':1500*index,'status':'writing','created_at':f'2026-10-{19-index:02d}T10:00:00'}
        (folder/'state.json').write_text(json.dumps(state),encoding='utf-8')


def exercise(base, temporary):
    result={'checks':[],'layout':[],'page_errors':[],'screenshots':[]}
    def ok(name):
        result['checks'].append(name)
        print('PASS', name,flush=True)
    def shot(page,name,full=False):
        page.screenshot(path=str(OUT/name),full_page=full)
        result['screenshots'].append(name)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport={'width':1440,'height':960},service_workers='block',accept_downloads=True)
        page=context.new_page()
        page.on('pageerror',lambda error:result['page_errors'].append(str(error)))
        try:
            page.goto(base,wait_until='networkidle')
            expect(page.locator('.book-card:not(.book-add)')).to_have_count(3)
            expect(page.get_by_role('button',name='我的书架',exact=True)).to_be_visible()
            shot(page,'library-desktop.png',True)
            ok('canonical library loads')
            page.get_by_role('textbox',name='搜索作品').fill('星河')
            expect(page.locator('.book-card:not(.book-add)')).to_have_count(1)
            page.get_by_role('textbox',name='搜索作品').fill('')
            page.get_by_role('button',name='待开篇',exact=True).click()
            expect(page.locator('.book-card:not(.book-add)')).to_have_count(1)
            page.get_by_role('button',name='全部作品',exact=True).click()
            ok('search and progress filters')
            page.locator('.book-card:not(.book-add)').nth(1).click()
            expect(page.locator('.reading-container')).to_be_visible()
            shot(page,'writing-desktop.png',True)
            ok('existing novel opens in writing workspace')
            assert page.get_by_text('旧互动剧情模式').count()==0
            page.get_by_role('button',name='角色扮演',exact=False).first.click()
            page.wait_for_url('**/roleplay.html*')
            expect(page.locator('.rp-messages')).to_be_visible()
            expect(page.locator('.rp-message-text em').first).to_be_visible()
            ok('roleplay greeting renders action formatting')
            page.locator('.rp-composer textarea').fill('你好，中文与 emoji 🌙，请回应。')
            page.locator('.rp-send').click()
            expect(page.locator('.rp-stop')).not_to_be_visible(timeout=20000)
            assert page.locator('.rp-msg.assistant .rp-message-text').last.inner_text()==EXPECTED
            assert page.locator('.rp-msg.assistant .rp-message-text').last.locator('em').count()==1
            ok('HTTP SSE and SDK pipeline show clean Chinese dialogue')
            page.reload(wait_until='networkidle')
            assert page.locator('.rp-msg.assistant .rp-message-text').last.inner_text()==EXPECTED
            page.get_by_role('button',name='重新生成',exact=False).click()
            expect(page.locator('.rp-stop')).not_to_be_visible(timeout=20000)
            assert page.locator('.rp-msg.assistant').count()==2
            ok('reload persistence and regeneration')
            page.locator('.rp-msg.assistant').last.hover()
            page.locator('.rp-msg.assistant').last.get_by_title('编辑消息').click()
            page.get_by_role('textbox',name='消息正文').fill('*她微笑了一下。*\n\n「修改后的中文台词。」')
            page.get_by_role('button',name='保存修改',exact=True).click()
            expect(page.locator('.rp-edit-message')).not_to_be_visible()
            assert '修改后的中文台词' in page.locator('.rp-msg.assistant .rp-message-text').last.inner_text()
            with page.expect_download() as download:
                page.get_by_role('button',name='导出',exact=True).click()
            target=temporary/'chat.txt';download.value.save_as(str(target))
            assert '修改后的中文台词' in target.read_text(encoding='utf-8')
            ok('message editing and UTF-8 chat export')
            page.locator('.rp-composer textarea').fill('[slow] 测试停止')
            page.locator('.rp-send').click()
            page.wait_for_function("Array.from(document.querySelectorAll('.rp-message-text')).at(-1).textContent.length>8")
            before=time.perf_counter();assert page.request.get(base+'/api/health').ok
            latency=time.perf_counter()-before;assert latency<1,latency
            result['health_during_stream_ms']=round(latency*1000,2)
            page.locator('.rp-stop').click()
            expect(page.locator('.rp-stop')).not_to_be_visible(timeout=10000)
            partial=page.locator('.rp-msg.assistant .rp-message-text').last.inner_text()
            page.reload(wait_until='networkidle')
            assert page.locator('.rp-msg.assistant .rp-message-text').last.inner_text()==partial
            ok('stop persists partial text; health stays responsive')
            page.locator('.rp-composer textarea').fill('[auth-error] test')
            page.locator('.rp-send').click()
            expect(page.locator('.rp-stop')).not_to_be_visible(timeout=10000)
            expect(page.locator('.rp-toast-msg')).to_contain_text('鉴权')
            page.locator('.rp-toast').click()
            ok('readable provider failure')
            card={'spec':'chara_card_v2','data':{'name':'林间来客','description':'安静的山林旅人','personality':'温柔而坚定','scenario':'秋日的山间，落叶随风飘过。','first_mes':'*林间来客合上书。*\n\n「你好，{{user}}。」','alternate_greetings':['开场一','开场二'],'tags':['山林','治愈']}}
            page.locator('input[type=file]').first.set_input_files({'name':'card.json','mimeType':'application/json','buffer':json.dumps(card,ensure_ascii=False).encode()})
            expect(page.locator('.rp-chat-name')).to_have_text('林间来客')
            assert '{{user}}' not in page.locator('.rp-message-text').inner_text()
            page.locator('.rp-composer textarea').fill('这是一条还没发送的草稿')
            page.locator('.rp-side-card').filter(has_text='苏晚').click()
            page.locator('.rp-side-card').filter(has_text='林间来客').click()
            expect(page.locator('.rp-composer textarea')).to_have_value('这是一条还没发送的草稿')
            page.reload(wait_until='networkidle')
            expect(page.locator('.rp-composer textarea')).to_have_value('这是一条还没发送的草稿')
            ok('SillyTavern import, macros, character selection, and persistent drafts')
            page.locator('.rp-composer textarea').fill('山间有什么？');page.locator('.rp-send').click()
            expect(page.locator('.rp-stop')).not_to_be_visible(timeout=20000)
            page.get_by_role('button',name='开启新对话',exact=True).click()
            page.get_by_role('alertdialog').get_by_role('button',name='确定',exact=True).click()
            expect(page.locator('.rp-msg')).to_have_count(1)
            page.get_by_role('button',name='恢复上次对话',exact=True).click()
            expect(page.locator('.rp-msg')).to_have_count(3)
            ok('new conversation archive and restore')
            page.get_by_title('模型连接',exact=True).click()
            page.get_by_label('模型名称',exact=True).fill('local-test-model')
            page.get_by_role('button',name='保存并测试',exact=True).click()
            expect(page.locator('.connection-result')).to_contain_text('模型已成功返回正文',timeout=25000)
            assert 'api_key' not in page.request.get(base+'/api/system/connection').json()
            page.locator('.connection-dialog').get_by_role('button',name='关闭',exact=True).click()
            ok('provider settings apply without restart; key never returned')
            page.locator('.rp-composer textarea').fill('')
            shot(page,'roleplay-desktop.png')
            for width,height in [(360,800),(390,844),(768,1024),(1440,960),(1920,1080)]:
                page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(150)
                metrics=page.evaluate("({width:innerWidth,overflow:document.documentElement.scrollWidth-innerWidth,bottom:document.querySelector('.rp-composer').getBoundingClientRect().bottom,sendRight:document.querySelector('.rp-send').getBoundingClientRect().right})")
                assert metrics['overflow']==0 and metrics['bottom']<=height+1 and metrics['sendRight']<=width,metrics
                result['layout'].append(metrics)
                if width==390:shot(page,'roleplay-mobile.png')
            ok('roleplay fits 360/390/768/1440/1920px viewports')
            page.set_viewport_size({'width':1440,'height':960})
            page.get_by_title('切换深色主题',exact=True).click()
            expect(page.locator('html')).to_have_attribute('data-theme','dark');page.wait_for_timeout(250);shot(page,'roleplay-dark.png')
            page.goto(base,wait_until='networkidle')
            for width,height in [(360,800),(390,844),(768,1024),(1440,960)]:
                page.set_viewport_size({'width':width,'height':height})
                assert page.evaluate('document.documentElement.scrollWidth-innerWidth')==0
                if width==390:shot(page,'library-mobile.png',True)
            ok('theme persistence and responsive library')
            assert not result['page_errors'],result['page_errors']
            assert not page.locator('#app-errors').count()
            ok('no uncaught browser or Vue errors')
        except Exception:
            shot(page,'failure.png',True)
            (OUT/'failure-dom.txt').write_text(page.locator('body').inner_text(),encoding='utf-8')
            raise
        finally:
            (OUT/'browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            browser.close()
    return result


def main():
    api_port,mock_port=port(),port()
    with tempfile.TemporaryDirectory(prefix='novelgen-qa-') as directory:
        temporary=Path(directory);seed(temporary/'novels')
        env=os.environ.copy();env.update(NOVELS_DIR=str(temporary/'novels'),ROLEPLAY_DIR=str(temporary/'roleplay'),DEEPSEEK_API_KEY='local-test-only',DEEPSEEK_BASE_URL=f'http://127.0.0.1:{mock_port}/v1',DEEPSEEK_MODEL='local-test-model',MOCK_PORT=str(mock_port),NO_PROXY='127.0.0.1,localhost',PYTHONIOENCODING='utf-8')
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        with (OUT/'server.log').open('w',encoding='utf-8') as api_log,(OUT/'mock.log').open('w',encoding='utf-8') as mock_log:
            mock=subprocess.Popen([sys.executable,'-X','utf8','backend/tests/mock_llm_server.py'],cwd=ROOT,env=env,stdout=mock_log,stderr=subprocess.STDOUT,creationflags=flags)
            api=subprocess.Popen([sys.executable,'-X','utf8','-m','uvicorn','api.server:app','--app-dir','backend','--host','127.0.0.1','--port',str(api_port)],cwd=ROOT,env=env,stdout=api_log,stderr=subprocess.STDOUT,creationflags=flags)
            try:
                ready(f'http://127.0.0.1:{mock_port}/v1/models');ready(f'http://127.0.0.1:{api_port}/api/health')
                result=exercise(f'http://127.0.0.1:{api_port}',temporary)
                print(f"Browser acceptance: {len(result['checks'])} passed.",flush=True)
            finally:
                api.terminate();mock.terminate();api.wait(timeout=10);mock.wait(timeout=10)


if __name__=='__main__':
    main()
