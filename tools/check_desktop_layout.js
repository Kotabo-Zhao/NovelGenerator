/* Desktop regression check: pc-only toolbar buttons + no overlap of tabs bar */
const CDP_PORT = process.argv[2] || '9226';
const APP_PORT = process.argv[3] || '8010';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ng-cdp-desk-'));
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

  await evalJs(`document.querySelector('.book-tile').click()`);
  await sleep(2500);

  const out = await evalJs(`(function(){
    const res = { workbench: !!document.querySelector('.workbench'), wbTabsZ: getComputedStyle(document.querySelector('.wb-tabs')).zIndex };
    // pc-only buttons visible & hit-testable?
    const pcBtns = Array.from(document.querySelectorAll('.wb-actions .pc-only'));
    res.pcBtnCount = pcBtns.length;
    res.pcBtnInfo = pcBtns.map(b => {
      const r = b.getBoundingClientRect();
      const cx = r.left + r.width/2, cy = r.top + r.height/2;
      const top = document.elementFromPoint(cx, cy);
      return { label: b.getAttribute('aria-label')||b.title, hit: top === b || b.contains(top), visible: r.width>0 && r.height>0 };
    });
    // does the tabs bar overlap the sidebar/rail (regression from z-index)?
    const tabs = document.querySelector('.wb-tabs').getBoundingClientRect();
    const side = document.querySelector('.wb-side').getBoundingClientRect();
    const rail = document.querySelector('.wb-rail') ? document.querySelector('.wb-rail').getBoundingClientRect() : null;
    res.tabsBottom = Math.round(tabs.bottom); res.sideTop = Math.round(side.top); res.railTop = rail ? Math.round(rail.top) : null;
    return JSON.stringify(res);
  })()`);
  console.log(out);
  ws.close(); chrome.kill();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch {}
})().catch(e => { console.error('FATAL:', e); chrome.kill(); process.exit(1); });
