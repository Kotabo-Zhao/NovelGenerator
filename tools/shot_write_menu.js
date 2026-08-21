/* Capture screenshots: menu open (mobile) + write view (desktop) for visual check */
const CDP_PORT = process.argv[2] || '9225';
const APP_PORT = process.argv[3] || '8010';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ng-cdp-shot-'));
const chrome = spawn(CHROME, [
  '--headless=new', '--remote-debugging-port=' + CDP_PORT,
  '--user-data-dir=' + profile, '--no-first-run', '--disable-gpu', '--hide-scrollbars', 'about:blank'
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

  async function evalJs(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('JS error: ' + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }
  async function shot(name) {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(__dirname, name), Buffer.from(r.data, 'base64'));
    console.log('saved', name);
  }

  // --- mobile: menu open ---
  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send('Page.navigate', { url: `http://127.0.0.1:${APP_PORT}/` });
  await sleep(3500);
  await evalJs(`document.querySelector('.book-tile').click()`);
  await sleep(2500);
  await evalJs(`document.querySelector('button[aria-label="更多工具"]').click()`);
  await sleep(500);
  await shot('shot-menu-mobile.png');

  // --- desktop: write view (regression check; ⋯ is mobile-only) ---
  await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await send('Page.navigate', { url: `http://127.0.0.1:${APP_PORT}/` });
  await sleep(3500);
  await evalJs(`document.querySelector('.book-tile').click()`);
  await sleep(2500);
  await shot('shot-write-desktop.png');

  ws.close(); chrome.kill();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch {}
})().catch(e => { console.error('FATAL:', e); chrome.kill(); process.exit(1); });
