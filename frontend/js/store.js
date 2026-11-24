/**
 * Lightweight reactive pub/sub store for NovelGenerator v5 (zero framework overhead).
 */

const listeners = new Set();

export const state = {
  view: 'shelf',
  novels: [],
  activeNovel: null,
  activeChapterNum: 1,
  activeChapter: null,
  ledger: { entities: [], foreshadows: [], world_entries: [], canon_events: [] },
  inspectorTab: 'contract',
  styles: [],
  genres: [],
  llm: { mode: 'demo', model: 'deepseek-chat', base_url: 'https://api.deepseek.com/v1', has_api_key: false },
  zenMode: false,
  streaming: false,
  stageMessage: '',
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function setState(patch) {
  Object.assign(state, patch);
  for (const fn of listeners) fn(state);
}

export function showToast(message) {
  const host = document.getElementById('toast-host');
  if (!host) return;
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

export function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
