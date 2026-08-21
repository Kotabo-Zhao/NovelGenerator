/* 验证修复：质量面板/伏笔账本显示、导出按钮、搜索降级、选中工具条 */
const CDP_PORT = process.argv[2] || '9555';
const APP_PORT = process.argv[3] || '8010';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ng-cdp-fix-'));
const chrome = spawn(CHROME, [
  '--headless=new', '--remote-debugging-port=' + CDP_PORT,
  '--user-data-dir=' + profile, '--window-size=1280,900',
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
  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await send('Page.enable'); await send('Runtime.enable');
  await send('Page.navigate', { url: `http://127.0.0.1:${APP_PORT}/` });
  await sleep(3500);

  async function evalJs(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('JS error: ' + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }

  // 1. 打开小说 → 验证质量面板 + 伏笔账本
  await evalJs(`document.querySelector('.book-tile').click()`);
  await sleep(3000);
  const panel = await evalJs(`(function(){
    const bigNum = document.querySelector('.rail-score .big-num');
    const dims = Array.from(document.querySelectorAll('.rail-block .dim')).map(d=>d.innerText.trim());
    const fsItems = Array.from(document.querySelectorAll('.fs-item')).map(f=>f.innerText.trim());
    return JSON.stringify({ bigNum: bigNum ? bigNum.textContent : null, dims, fsItems });
  })()`);
  console.log('QUALITY PANEL:', panel);

  // 2. 导出视图：验证按钮为 TXT/EPUB（无 PDF）
  await evalJs(`(function(){ const b=Array.from(document.querySelectorAll('.topnav-item')).find(x=>x.textContent.includes('付梓')); if(b) b.click(); })()`);
  await sleep(800);
  const exportView = await evalJs(`(function(){
    const btns = Array.from(document.querySelectorAll('.export-item .ei-actions button')).map(b=>b.textContent.trim());
    return JSON.stringify({ btnLabels: btns, sub: document.querySelector('.export-view .view-sub')?.textContent });
  })()`);
  console.log('EXPORT VIEW:', exportView);

  // 3. 批量导出（应可用，不再 410）
  await evalJs(`(function(){ const c=document.querySelector('.export-item .export-check'); if(c) c.click(); })()`);
  await sleep(300);
  const batchResult = await evalJs(`(async function(){
    const btn = Array.from(document.querySelectorAll('.export-actions button')).find(b=>b.textContent.includes('批量下载'));
    if (!btn) return 'no batch btn';
    btn.click();
    await new Promise(r=>setTimeout(r, 2500));
    const err = document.querySelector('.export-view .error-msg');
    return err ? 'error: '+err.textContent.trim() : 'batch ok (no error)';
  })()`);
  console.log('BATCH EXPORT:', batchResult);

  // 4. 搜索降级：无 activeNovel 时不应 404（先回书架）
  await evalJs(`(function(){ const b=Array.from(document.querySelectorAll('.topnav-item')).find(x=>x.textContent.includes('书架')); if(b) b.click(); })()`);
  await sleep(600);
  const searchTest = await evalJs(`(async function(){
    const openBtn = Array.from(document.querySelectorAll('button')).find(b=>b.getAttribute('aria-label')==='搜索作品' || b.textContent.trim()==='🔍 搜索');
    if (!openBtn) return 'no search btn';
    openBtn.click();
    await new Promise(r=>setTimeout(r, 400));
    const input = document.querySelector('.search-panel input, .toast-overlay input');
    if (!input) return 'no search input';
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, '玉佩');
    input.dispatchEvent(new Event('input', {bubbles:true}));
    await new Promise(r=>setTimeout(r, 300));
    const runBtn = Array.from(document.querySelectorAll('.toast-overlay button, .search-panel button')).find(b=>b.textContent.includes('搜索'));
    if (runBtn) runBtn.click();
    await new Promise(r=>setTimeout(r, 1500));
    const err = document.querySelector('.search-panel .error-msg, .toast-overlay .error-msg');
    return err ? 'error: '+err.textContent.trim() : 'search ok (no 404)';
  })()`);
  console.log('SEARCH:', searchTest);

  // 5. 选中工具条：模拟选择正文 → selBar 应出现
  await evalJs(`(function(){ const b=Array.from(document.querySelectorAll('.topnav-item')).find(x=>x.textContent.includes('落笔')); if(b) b.click(); })()`);
  await sleep(2500);
  const selBarTest = await evalJs(`(async function(){
    const content = document.querySelector('.chapter-content');
    if (!content) return 'no chapter content';
    const range = document.createRange();
    range.selectNodeContents(content);
    const sel = window.getSelection();
    sel.removeAllRanges(); sel.addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
    await new Promise(r=>setTimeout(r, 300));
    const bar = document.querySelector('.sel-bar');
    return bar && bar.offsetParent ? 'sel-bar visible: '+bar.innerText.trim().slice(0,60) : 'sel-bar NOT visible';
  })()`);
  console.log('SELECTION BAR:', selBarTest);

  ws.close(); chrome.kill();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch {}
})().catch(e => { console.error('FATAL:', e); chrome.kill(); process.exit(1); });
