/* 验证选中工具条：切到第 1 章（有正文）后模拟选择 */
const CDP_PORT = process.argv[2] || '9556';
const APP_PORT = process.argv[3] || '8010';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ng-cdp-sel-'));
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
  await send('Page.enable'); await send('Runtime.enable');
  await send('Page.navigate', { url: `http://127.0.0.1:${APP_PORT}/` });
  await sleep(3500);

  async function evalJs(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('JS error: ' + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }

  // 打开小说 → 切到第 1 章
  await evalJs(`document.querySelector('.book-tile').click()`);
  await sleep(2500);
  await evalJs(`(function(){ const ch=Array.from(document.querySelectorAll('.ws-ch')).find(b=>b.textContent.includes('1')); if(ch) ch.click(); })()`);
  await sleep(2000);

  const selTest = await evalJs(`(async function(){
    const content = document.querySelector('.chapter-content');
    if (!content) return 'no chapter content';
    const text = content.textContent || '';
    // 真实鼠标选择流程：selectionchange 由 document 监听
    const range = document.createRange();
    const node = content.firstChild || content;
    range.setStart(node, 0);
    range.setEnd(node, Math.min(10, (node.textContent||'').length));
    const sel = window.getSelection();
    sel.removeAllRanges(); sel.addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
    await new Promise(r=>setTimeout(r, 400));
    const bar = document.querySelector('.sel-bar');
    const visible = bar && bar.offsetParent && getComputedStyle(bar).display !== 'none';
    return JSON.stringify({ textLen: text.length, barVisible: visible, barText: bar ? bar.innerText.trim().slice(0,80) : null });
  })()`);
  console.log('SELECTION:', selTest);
  ws.close(); chrome.kill();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch {}
})().catch(e => { console.error('FATAL:', e); chrome.kill(); process.exit(1); });
