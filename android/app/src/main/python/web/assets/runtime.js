/* Shared browser runtime. No CDN, build step, or external font dependency. */
(() => {
  'use strict';
  if (window.AndroidBridge) document.documentElement.dataset.platform = 'android';
  const storage = {
    get(key, fallback = '') { try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (_) {} }
  };
  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    storage.set('theme', theme);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = theme === 'dark' ? '#171923' : '#f7f8fc';
  }
  setTheme(storage.get('theme', 'light'));

  function detailMessage(data) {
    const detail = data && (data.detail || data.message);
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(item => item.msg || '').filter(Boolean).join('；');
    return '';
  }

  async function requestJson(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), options.timeout || 25000);
    const abort = () => controller.abort();
    if (options.signal?.aborted) abort();
    else if (options.signal) options.signal.addEventListener('abort', abort, { once: true });
    try {
      const init = { ...options, signal: controller.signal, headers: { Accept: 'application/json', ...options.headers } };
      if (init.body && typeof init.body !== 'string') {
        init.body = JSON.stringify(init.body);
        init.headers['Content-Type'] = 'application/json';
      }
      const response = await fetch(url, init);
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const error = new Error(detailMessage(data) || `请求失败（${response.status}）`);
        error.status = response.status;
        throw error;
      }
      if (data === null) throw new Error('服务器返回了无法读取的数据');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') {
        const cancelled = !!options.signal?.aborted;
        const failure = new Error(cancelled ? '请求已取消' : '请求超时，请重试');
        failure.name = cancelled ? 'AbortError' : 'TimeoutError';
        throw failure;
      }
      throw error;
    } finally {
      clearTimeout(timer);
      if (options.signal) options.signal.removeEventListener('abort', abort);
    }
  }

  async function readSSE(response, onEvent, idleTimeout = 120000) {
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(detailMessage(data) || `服务器错误（${response.status}）`);
    }
    if (!response.body) throw new Error('当前浏览器不支持流式响应');
    if (!(response.headers.get('content-type') || '').includes('text/event-stream')) {
      throw new Error('服务器没有返回消息流，请刷新页面后重试');
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let line = '', dataLines = [], afterCR = false, finished = false, timedOut = false;
    let eventSize = 0;
    const maxEventSize = 2 * 1024 * 1024;
    let lastData = Date.now();
    function dispatch() {
      if (!dataLines.length || finished) return;
      const raw = dataLines.join('\n');
      dataLines = [];
      eventSize = 0;
      let event;
      try { event = raw === '[DONE]' ? { type: 'done' } : JSON.parse(raw); }
      catch (_) { throw new Error('消息流格式异常，请重试；已保存的对话仍可查看'); }
      if (!event || typeof event !== 'object' || Array.isArray(event)) throw new Error('消息流包含无效事件');
      if (event.type !== 'ping' && onEvent(event) === false) finished = true;
    }
    function endLine() {
      if (!line) dispatch();
      else if (line.startsWith('data:')) {
        eventSize += line.length + 1;
        if (eventSize > maxEventSize) throw new Error('消息流事件过大');
        dataLines.push(line.slice(5).replace(/^ /, ''));
      }
      line = '';
    }
    function consume(text) {
      for (const char of text) {
        if (afterCR && char === '\n') { afterCR = false; continue; }
        afterCR = false;
        if (char === '\r' || char === '\n') { endLine(); afterCR = char === '\r'; }
        else {
          line += char;
          if (line.length + eventSize > maxEventSize) throw new Error('消息流事件过大');
        }
        if (finished) break;
      }
    }
    const timer = setInterval(() => {
      if (Date.now() - lastData > idleTimeout) {
        timedOut = true;
        reader.cancel().catch(() => {});
      }
    }, 1000);
    try {
      while (!finished) {
        const { value, done } = await reader.read();
        if (done) break;
        lastData = Date.now();
        consume(decoder.decode(value, { stream: true }));
      }
      if (timedOut) throw new Error('模型长时间没有响应，已保留对话，请重试');
      if (!finished) { consume(decoder.decode()); if (line) endLine(); dispatch(); }
    } finally {
      clearInterval(timer);
      try { await reader.cancel(); } catch (_) {}
      reader.releaseLock();
    }
  }

  function textContent(value) {
    if (value == null) return '';
    if (typeof value === 'string') {
      const raw = value.trim();
      if (raw.startsWith('{')) {
        try { const object = JSON.parse(raw); if (object.choices || object.content !== undefined) return textContent(object); } catch (_) {}
      }
      return value.replace(/\r\n/g, '\n').replace(/\u0000/g, '');
    }
    if (Array.isArray(value)) return value.map(textContent).join('');
    if (typeof value !== 'object') return '';
    if (value.type && !['text', 'output_text', 'delta', 'text_delta'].includes(value.type)) return '';
    if (value.choices) { const first = value.choices[0]; return first ? textContent(first.delta || first.message) : ''; }
    return textContent(value.content ?? value.text ?? value.delta);
  }
  function escapeHtml(text) {
    return text.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }
  function renderMessage(value) {
    return textContent(value).split(/(```[\s\S]*?```)/g).map(part => {
      if (part.startsWith('```') && part.endsWith('```')) {
        return '<pre><code>' + escapeHtml(part.slice(3, -3).replace(/^[a-zA-Z0-9_-]+\n/, '')) + '</code></pre>';
      }
      return escapeHtml(part).replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
        .replace(/`([^`\n]+)`/g, '<code>$1</code>');
    }).join('');
  }
  function download(name, content, type = 'text/plain;charset=utf-8') {
    const filename = name.replace(/[<>:"/\\|?*]/g, '_');
    const blob = content instanceof Blob ? content : new Blob([content], { type });
    if (window.AndroidBridge && typeof window.AndroidBridge.saveFile === 'function') {
      const reader = new FileReader();
      reader.onload = () => {
        try { window.AndroidBridge.saveFile(filename, type.split(';')[0], String(reader.result).split(',')[1]); }
        catch (error) { showError(error.message || '无法保存文件'); }
      };
      reader.onerror = () => showError('无法读取导出文件');
      reader.readAsDataURL(blob);
      return;
    }
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copy(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(textContent(text));
    const input = document.createElement('textarea'); input.value = textContent(text); input.style.cssText = 'position:fixed;left:-9999px';
    document.body.appendChild(input); input.select();
    const success = document.execCommand('copy'); input.remove();
    if (!success) throw new Error('无法自动复制，请选中文字复制');
  }

  const paths = {
    book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 3H20v19H6.5A2.5 2.5 0 0 1 4 19.5v-14A2.5 2.5 0 0 1 6.5 3Z"/><path d="M8 7h8M8 11h5"/>',
    feather: '<path d="M20.2 3.8a5.5 5.5 0 0 1 0 7.8L9 22H2v-7L13.4 3.8a4.8 4.8 0 0 1 6.8 0Z"/><path d="m16 8-9 9M8 16h6"/>',
    mask: '<path d="M3 4c6 3 12 3 18 0v7c0 5-4 9-9 11-5-2-9-6-9-11V4Z"/><path d="M7 10h2m6 0h2M8 15c2 2 6 2 8 0"/>',
    search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    moon: '<path d="M20.5 13A9 9 0 0 1 11 3.5 9 9 0 1 0 20.5 13Z"/>',
    download: '<path d="M12 3v12m-4-4 4 4 4-4M4 15v5h16v-5"/>',
    settings: '<path d="m9 3-1 3-3 1-2 3 2 2v3l2 2 3-1 2 2 3-1 1-3 3-1 2-3-2-2V7l-2-2-3 1-2-2Z"/><circle cx="12" cy="11" r="3"/>',
    spark: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    edit: '<path d="m16 3 5 5L8 21H3v-5Z"/><path d="m13 6 5 5"/>',
    copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    refresh: '<path d="M20 7a9 9 0 1 0 1 9M20 3v6h-6"/>',
    send: '<path d="m22 2-7 20-4-9L2 9Z"/><path d="M22 2 11 13"/>',
    stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    compass: '<circle cx="12" cy="12" r="9"/><path d="m16 8-3 5-5 3 3-5Z"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'
  };
  function install(app) {
    app.component('Icon', { props: ['name', 'size'], render() { return Vue.h('svg', {
      viewBox: '0 0 24 24', width: this.size || 19, height: this.size || 19, fill: 'none', stroke: 'currentColor',
      'stroke-width': 1.65, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true',
      class: 'icon', innerHTML: paths[this.name] || paths.spark
    }); } });
    app.component('ConnectionDialog', {
      props: ['open'], emits: ['close'],
      setup(props, { emit }) {
        const form = Vue.reactive({ base_url: '', model: '', api_key: '' });
        const busy = Vue.ref(false), message = Vue.ref(''), failed = Vue.ref(false), configured = Vue.ref(false);
        Vue.watch(() => props.open, async open => {
          if (!open) return;
          message.value = ''; form.api_key = '';
          try { const status = await requestJson('/api/system/connection'); Object.assign(form, { base_url: status.base_url, model: status.model }); configured.value = status.configured; }
          catch (error) { message.value = error.message; failed.value = true; }
          Vue.nextTick(() => document.getElementById('provider-url')?.focus());
        });
        async function save(probe = false) {
          if (busy.value) return;
          busy.value = true; message.value = ''; failed.value = false;
          try {
            const status = await requestJson('/api/system/connection', { method: 'PUT', body: form });
            configured.value = status.configured; form.api_key = '';
            window.dispatchEvent(new CustomEvent('provider-changed', { detail: status }));
            message.value = probe ? (await requestJson('/api/system/connection/test', { method: 'POST' })).message : '连接设置已保存';
          } catch (error) { message.value = error.message; failed.value = true; }
          finally { busy.value = false; }
        }
        return { form, busy, message, failed, configured, save, close: () => { if (!busy.value) emit('close'); } };
      },
      template: `<div v-if="open" class="connection-overlay" @click.self="close" @keydown.esc.stop="close">
        <section class="connection-dialog" role="dialog" aria-modal="true" aria-labelledby="provider-title">
          <header><div><span class="eyebrow">PREFERENCES</span><h2 id="provider-title">模型连接</h2></div><button class="icon-button" @click="close" aria-label="关闭"><Icon name="close"/></button></header>
          <p class="connection-intro">连接你使用的模型，让每个故事都有回应。支持 DeepSeek 和 OpenAI 兼容接口。</p>
          <form @submit.prevent="save(false)"><label for="provider-url">API 地址</label><input id="provider-url" v-model="form.base_url" type="url" required placeholder="https://api.deepseek.com"/>
          <label for="provider-model">模型名称</label><input id="provider-model" v-model="form.model" required placeholder="deepseek-chat"/>
          <label for="provider-key">API Key <span>{{ configured ? '已保存 · 留空则保留' : '本地模型可留空' }}</span></label><input id="provider-key" v-model="form.api_key" type="password" autocomplete="new-password" placeholder="请输入密钥"/>
          <p class="field-note">密钥保存在这台设备。测试会发送一条简短请求。</p>
          <div v-if="message" class="connection-result" :class="{failed}" role="status">{{ message }}</div>
          <footer><button type="button" class="btn btn-secondary" :disabled="busy" @click="save(true)">{{ busy ? '处理中…' : '保存并测试' }}</button><button type="submit" class="btn btn-primary" :disabled="busy">保存设置</button></footer></form>
        </section></div>`
    });
    guard(app);
  }
  function guard(app) {
    app.config.errorHandler = error => {
      console.error(error);
      showError(error.message || '页面操作失败，请刷新后重试');
    };
  }
  function showError(message) {
    let banner = document.getElementById('app-errors');
    if (!banner) {
      banner = document.createElement('div'); banner.id = 'app-errors'; banner.setAttribute('role', 'alert');
      document.body.appendChild(banner);
    }
    banner.textContent = '页面遇到问题：' + String(message).slice(0, 220);
    const actions = document.createElement('div'); actions.className = 'app-error-actions';
    const reload = document.createElement('button'); reload.textContent = '重新加载'; reload.onclick = () => location.reload(); actions.appendChild(reload);
    const home = document.createElement('a'); home.textContent = '返回书架'; home.href = '/?view=shelf'; actions.appendChild(home);
    const settings = document.createElement('a'); settings.textContent = '模型设置'; settings.href = '/?view=settings'; actions.appendChild(settings);
    const close = document.createElement('button'); close.textContent = '关闭提示'; close.onclick = () => banner.remove(); actions.appendChild(close);
    banner.appendChild(actions);
  }
  window.addEventListener('error', event => { if (event.message) showError(event.message); });
  window.addEventListener('unhandledrejection', event => showError(event.reason?.message || '网络操作失败，请重试'));
  document.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const dialogs = document.querySelectorAll('[aria-modal="true"]');
    const modal = dialogs[dialogs.length - 1];
    if (!modal) return;
    const nodes = Array.from(modal.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href]')).filter(node => node.getClientRects().length);
    if (!nodes.length) return;
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) { last.focus(); event.preventDefault(); }
    else if (!event.shiftKey && (document.activeElement === last || !modal.contains(document.activeElement))) { first.focus(); event.preventDefault(); }
  });
  window.NovelUI = { storage, setTheme, requestJson, readSSE, textContent, renderMessage, download, copy, install, guard, showError };
  window.addEventListener('load', () => {
    if (!window.AndroidBridge && 'serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    }
  });
})();
