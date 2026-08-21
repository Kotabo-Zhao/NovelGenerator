/* Repro/verify: 写作页「⋯」子菜单可点击性问题
 * 用 CDP 驱动 headless Chrome（移动视口），打开作品 → 写作页 → 点 ⋯ →
 * 检查菜单项中心点 hit-test 命中的元素 + 点击后是否打开目标面板。
 * 用法: node repro_write_menu.js [port] [--fix] 
 *   port 默认 8010；--fix 时在打开菜单后打印预期已修复的信息
 */
const CDP_PORT = process.argv[2] || '9222';
const APP_PORT = process.argv[3] || '8010';
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'ng-cdp-'));
const chrome = spawn(CHROME, [
  '--headless=new',
  '--remote-debugging-port=' + CDP_PORT,
  '--user-data-dir=' + profile,
  '--window-size=390,844',
  '--no-first-run',
  '--disable-gpu',
  '--hide-scrollbars',
  'about:blank'
], { stdio: 'ignore' });

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function getJson(url) {
  const res = await fetch(url);
  return res.json();
}

async function main() {
  // wait for CDP endpoint
  let version = null;
  for (let i = 0; i < 40; i++) {
    try { version = await getJson(`http://127.0.0.1:${CDP_PORT}/json/version`); break; }
    catch { await sleep(250); }
  }
  if (!version) { console.error('CDP not reachable'); process.exit(1); }

  // create a new tab (Chrome requires PUT)
  const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/new?http://127.0.0.1:${APP_PORT}/`, { method: 'PUT' });
  const target = await res.json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let msgId = 0;
  const pending = new Map();

  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++msgId;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  }

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error)));
      else resolve(msg.result);
    }
  };

  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });

  // Emulation: mobile viewport
  await send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true
  });
  await send('Page.enable');
  await send('Runtime.enable');

  await send('Page.navigate', { url: `http://127.0.0.1:${APP_PORT}/` });
  await sleep(3500); // let shelf load

  async function evalJs(expr) {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error('JS error: ' + JSON.stringify(r.exceptionDetails));
    return r.result.value;
  }

  // 1. find the novel tile and click it (enter write view)
  const tiles = await evalJs(`JSON.stringify(Array.from(document.querySelectorAll('.book-tile')).map(t=>({title:t.querySelector('.cover-title')?.textContent, cls:t.className})))`);
  console.log('tiles:', tiles);

  await evalJs(`(function(){ const t=document.querySelector('.book-tile'); if(t){ t.click(); return 'clicked'; } return 'no tile'; })()`);
  await sleep(2500); // write view loads (fetch plan + chapter)

  const view = await evalJs(`document.querySelector('.workbench') ? 'workbench' : (document.querySelector('.view') ? 'view' : 'none')`);
  console.log('view after click:', view);

  // 2. click the ⋯ button
  const moreBtn = await evalJs(`(function(){ const b=document.querySelector('button[aria-label="更多工具"]'); if(!b) return 'no btn'; const r=b.getBoundingClientRect(); b.click(); return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height}); })()`);
  console.log('more button:', moreBtn);
  await sleep(600); // menu animation

  // 3. inspect the menu and hit-test each item's center
  const result = await evalJs(`(function(){
    const menu = document.querySelector('.write-more-menu');
    if (!menu) return JSON.stringify({menu:false});
    const items = Array.from(menu.querySelectorAll('.write-more-item'));
    const out = items.map(item => {
      const r = item.getBoundingClientRect();
      const cx = r.left + r.width/2, cy = r.top + r.height/2;
      const top = document.elementFromPoint(cx, cy);
      const topInfo = top ? (top.className || top.tagName) : null;
      return { text: item.textContent.trim(), cx: Math.round(cx), cy: Math.round(cy),
               topClass: topInfo, hit: top === item || item.contains(top) };
    });
    const mrect = menu.getBoundingClientRect();
    return JSON.stringify({ menu:true, rect:{x:Math.round(mrect.x),y:Math.round(mrect.y),w:Math.round(mrect.width),h:Math.round(mrect.height)}, items: out });
  })()`);
  console.log('menu hit-test:', result);

  // 4. try clicking the first item and see if a panel opens
  const clickResult = await evalJs(`(function(){
    const menu = document.querySelector('.write-more-menu');
    if (!menu) return 'no menu';
    const item = menu.querySelector('.write-more-item');
    const r = item.getBoundingClientRect();
    const cx = r.left + r.width/2, cy = r.top + r.height/2;
    // dispatch real mouse events at the center
    const el = document.elementFromPoint(cx, cy);
    if (el) {
      ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(t => {
        el.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, clientX:cx, clientY:cy, view:window}));
      });
    }
    return JSON.stringify({ target: el ? (el.className||el.tagName) : 'none',
      menuStillOpen: !!document.querySelector('.write-more-menu'),
      searchOpen: !!document.querySelector('.search-panel') || !!document.querySelector('[class*="search"]'),
      overlayOpen: !!document.querySelector('.toast-overlay') });
  })()`);
  console.log('click first item:', clickResult);

  await sleep(800);
  const after = await evalJs(`JSON.stringify({menu: !!document.querySelector('.write-more-menu'), overlays: Array.from(document.querySelectorAll('.toast-overlay')).map(o=>o.className)})`);
  console.log('after click:', after);

  ws.close();
  chrome.kill();
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch {}
}

main().catch(e => { console.error('FATAL:', e); chrome.kill(); process.exit(1); });
