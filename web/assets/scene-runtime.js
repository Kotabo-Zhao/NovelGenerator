/* Small, independently testable stream and history helpers for the novel world. */
(function (root) {
  'use strict';
  function content(value) {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) return value.map(content).join('');
    if (value && typeof value === 'object') return content(value.text ?? value.content ?? value.delta);
    return '';
  }
  function updateDraft(draft, event) {
    if (event.type === 'phase') draft.phase = content(event.label);
    if (event.type === 'scene_retry') {
      draft.text = '';
      draft.phase = '正在避开重复剧情并重写…';
    }
    if (event.type === 'scene_chunk') {
      if (event.scene_num) draft.sceneNum = Number(event.scene_num) || draft.sceneNum;
      draft.text += content(event.content);
    }
  }
  function historyWindow(items, limit) {
    return items.slice(-Math.max(1, Number(limit) || 80));
  }
  async function readEvents(response, onEvent, readSSE, options) {
    options = options || {};
    var pending = [], timer = null, failure = null;
    function flush() {
      timer = null;
      var batch = pending; pending = [];
      try { batch.forEach(onEvent); }
      catch (error) { failure = error; if (options.abort) options.abort(); }
    }
    try {
      await readSSE(response, function (event) {
        if (event.type === 'ping') return;
        pending.push(event);
        if (event.type === 'done' || event.type === 'error') {
          if (timer !== null) clearTimeout(timer);
          flush();
          return false;
        }
        if (timer === null) timer = setTimeout(flush, options.interval ?? 32);
      });
    } finally {
      if (timer !== null) clearTimeout(timer);
      flush();
    }
    if (failure) throw failure;
  }
  var api = { content: content, updateDraft: updateDraft, historyWindow: historyWindow, readEvents: readEvents };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.NovelWorldRuntime = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
