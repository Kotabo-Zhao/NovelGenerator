/* Verify: click every item in the 「⋯」 menu and check the right panel opens */
const CDP_PORT = process.argv[2] || '9223';
const APP_PORT = process.argv[3] || '8010';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ng-cdp-verify-'));
const chrome = spawn(CHROME, [
  '--headless=new', '--remote-debugging-port=' + CDP_PORT,
  '--user-data-dir=' + profile, '--window-size=390,844',
  '--no-first-run', '--disable-gpu', '--hide-scrollbars', 'about:blank'
], { stdio: 'ignore' });

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  let v = null;
  for (let i = 0; i < 40; i++) {
    try { v = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json(); break; }
    catch { await sleep(250); }
  }
  const t = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?http://127.0.0.1:${APP_PORT}/`, { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  let id = 0; const pend = new Map();
  const send = (m, p = {}) => new Promise((res, rej) => {
    const i = ++id; pend.set(i, { res, rej });
    ws.send(JSON.stringify({ id: i, method: m, params: p }));
  });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pend.has(m.id)) {
      const p = pend.get(m.id); pend.delete(m.id);
      m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result);
    }
  };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send('Page.enable'); await send('Runtime.enable');
  await send('Page.navigate', { url: `http://127.0.0.1:${APP_PORT}/` });
  await sleep(3500);

  async function evalJs(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('JS error: ' + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }

  await evalJs(`document.querySelector('.book-tile').click()`);
  await sleep(2500);

  const clickItem = (idx) => evalJs(`(function(){
    const menu = document.querySelector('.write-more-menu');
    if (!menu) return { opened:false };
    const item = menu.querySelectorAll('.write-more-item')[${idx}];
    const r = item.getBoundingClientRect();
    const cx = r.left + r.width/2, cy = r.top + r.height/2;
    const el = document.elementFromPoint(cx, cy);
    if (!el || !item.contains(el)) return { opened:false, top: (el.className||el.tagName) };
    ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(t => {
      el.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, clientX:cx, clientY:cy, view:window}));
    });
    return { opened:true };
  })()`);

  const expect = ['search', 'bible', 'stats', 'scan', 'versions', 'outline'];
  const checks = {
    search: () => evalJs(`!!document.querySelector('.search-panel') || !!document.querySelector('.toast-overlay')`),
    bible: () => evalJs(`document.body.innerText.includes('世界圣经') && !!document.querySelector('.bible-grid, [class*="bible"]')`),
    stats: () => evalJs(`!!document.querySelector('[class*="stat"]') && !document.querySelector('.workbench')`),
    scan: () => evalJs(`document.body.innerText.includes('设定扫描') || !!document.querySelector('.toast-overlay')`),
    versions: () => evalJs(`document.body.innerText.includes('版本历史') || !!document.querySelector('.toast-overlay')`),
    outline: () => evalJs(`!!document.querySelector('.create-view, [class*="create"]') && !document.querySelector('.workbench')`),
  };

  const results = [];
  for (let i = 0; i < expect.length; i++) {
    // close any previously opened overlay / return to write view first
    await evalJs(`(function(){
      const ov = document.querySelector('.toast-overlay');
      if (ov) {
        const r = ov.getBoundingClientRect();
        ov.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, clientX:r.left+10, clientY:r.top+10, view:window}));
      }
    })()`);
    await sleep(400);
    if (i > 0) {
      // navigate back to the write view (works whether we're in an overlay or a sub-view)
      await evalJs(`(function(){
        const btns = Array.from(document.querySelectorAll('button'));
        const back = btns.find(b => /返回|回写|落笔|←/.test(b.textContent) && !b.closest('.toast-overlay'));
        if (back) { back.click(); return 'clicked-back:'+back.textContent.trim(); }
        const ov = document.querySelector('.toast-overlay');
        if (ov) { const r = ov.getBoundingClientRect(); ov.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, clientX:r.left+10, clientY:r.top+10, view:window})); return 'closed-overlay'; }
        const writeBtn = btns.find(b => b.textContent.includes('落笔'));
        if (writeBtn) { writeBtn.click(); return 'clicked-write'; }
        return 'nothing';
      })()`);
      await sleep(600);
    }
    await evalJs(`(function(){ const b=document.querySelector('button[aria-label="更多工具"]'); if(b) b.click(); })()`);
    await sleep(400);
    const diag = await evalJs(`JSON.stringify({
      overlay: !!document.querySelector('.toast-overlay'),
      menu: !!document.querySelector('.write-more-menu'),
      btn: !!document.querySelector('button[aria-label="更多工具"]')
    })`);
    const r = await clickItem(i);
    await sleep(700);
    const opened = r.opened;
    const panel = opened ? await checks[expect[i]]() : false;
    results.push({ item: expect[i], clicked: opened, panelOpened: panel, diag });
  }
  console.log(JSON.stringify(results, null, 2));
  ws.close(); chrome.kill();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch {}
})().catch(e => { console.error('FATAL:', e); chrome.kill(); process.exit(1); });
