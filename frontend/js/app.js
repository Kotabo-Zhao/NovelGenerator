import { apiFetch } from './api.js';
import { escapeHtml, setState, showToast, state, subscribe } from './store.js';
import { renderLedger } from './views/ledger.js';
import { renderSettings } from './views/settings.js';
import { renderShelf } from './views/shelf.js';
import { abortActiveStream, renderStudio } from './views/studio.js';

const workspace = document.getElementById('workspace');
const railChapters = document.getElementById('rail-chapters');
const chapterListEl = document.getElementById('chapter-list');
const modePill = document.getElementById('mode-pill');
const btnTheme = document.getElementById('btn-theme');

async function refreshShelf() {
  const res = await apiFetch('/api/novels');
  setState({ novels: res.novels || [] });
}

async function openNovel(novelId, chapterNum = 1) {
  abortActiveStream();
  const [novel, ledger] = await Promise.all([
    apiFetch(`/api/novels/${encodeURIComponent(novelId)}`),
    apiFetch(`/api/novels/${encodeURIComponent(novelId)}/ledger`),
  ]);
  const targetCh = novel.chapters?.length
    ? (novel.chapters.find(c => c.chapter_num === chapterNum)?.chapter_num || novel.chapters[0].chapter_num)
    : 1;
  let chapter = null;
  if (novel.chapters?.some(c => c.chapter_num === targetCh)) {
    chapter = await apiFetch(`/api/novels/${encodeURIComponent(novelId)}/chapters/${targetCh}`);
  }
  setState({
    view: 'studio',
    activeNovel: novel,
    activeChapterNum: targetCh,
    activeChapter: chapter,
    ledger,
  });
}

async function selectChapter(chNum) {
  if (!state.activeNovel) return;
  abortActiveStream();
  const chapter = await apiFetch(`/api/novels/${encodeURIComponent(state.activeNovel.id)}/chapters/${chNum}`);
  setState({ activeChapterNum: chNum, activeChapter: chapter, view: 'studio' });
}

async function reloadActiveNovel(chNum) {
  if (!state.activeNovel) return;
  await openNovel(state.activeNovel.id, chNum || state.activeChapterNum);
}

async function reloadLedger() {
  if (!state.activeNovel) return;
  const ledger = await apiFetch(`/api/novels/${encodeURIComponent(state.activeNovel.id)}/ledger`);
  setState({ ledger });
}

function toggleZen(force) {
  const next = typeof force === 'boolean' ? force : !state.zenMode;
  document.body.classList.toggle('zen-mode', next);
  setState({ zenMode: next });
}

function renderRail() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === state.view);
  });
  modePill.textContent = state.llm.mode === 'openai_compatible' ? state.llm.model : '演示模式';

  if (state.activeNovel && (state.view === 'studio' || state.view === 'ledger')) {
    railChapters.hidden = false;
    chapterListEl.innerHTML = (state.activeNovel.chapters || []).map(ch => `
      <button class="ch-item ${ch.chapter_num === state.activeChapterNum ? 'active' : ''}" data-ch="${ch.chapter_num}">
        <span class="ch-dot ${ch.status === 'completed' ? 'done' : ''}"></span>
        <span class="ch-title">第${ch.chapter_num}章 ${escapeHtml(ch.title)}</span>
        <span class="ch-wc">${ch.word_count || ''}</span>
      </button>
    `).join('');
    chapterListEl.querySelectorAll('[data-ch]').forEach(btn => {
      btn.addEventListener('click', () => selectChapter(Number(btn.dataset.ch)));
    });
  } else {
    railChapters.hidden = true;
  }
}

function renderCurrentView() {
  renderRail();
  workspace.classList.toggle('studio-mode', state.view === 'studio');
  if (state.view === 'shelf') {
    renderShelf(workspace, { onOpenNovel: openNovel, onRefresh: refreshShelf });
  } else if (state.view === 'studio') {
    renderStudio(workspace, {
      onReloadNovel: reloadActiveNovel,
      onSelectChapter: selectChapter,
      onToggleZen: toggleZen,
    });
  } else if (state.view === 'ledger') {
    renderLedger(workspace, { onReloadLedger: reloadLedger });
  } else if (state.view === 'settings') {
    renderSettings(workspace);
  }
}

// Navigation & Theme
document.querySelectorAll('.nav-btn').forEach(btn => {
  btn.addEventListener('click', () => setState({ view: btn.dataset.view }));
});

document.getElementById('btn-plan-more')?.addEventListener('click', async () => {
  if (!state.activeNovel) return;
  try {
    const nextCount = (state.activeNovel.chapters?.length || 0) + 3;
    await apiFetch(`/api/novels/${encodeURIComponent(state.activeNovel.id)}/plan?chapter_count=${nextCount}`, { method: 'POST' });
    showToast('已更新章节大纲契约');
    await reloadActiveNovel(state.activeChapterNum);
  } catch (err) {
    showToast(err.message);
  }
});

btnTheme.addEventListener('click', () => {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  const next = isDark ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('novelgen_v5_theme', next);
  btnTheme.textContent = next === 'dark' ? '浅色主题' : '深色主题';
});

// Global shortcuts
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && state.zenMode) {
    toggleZen(false);
    return;
  }
  const tag = e.target?.tagName?.toUpperCase() || '';
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
  if (e.altKey && (e.key === 'z' || e.key === 'Z') && state.view === 'studio') {
    e.preventDefault();
    toggleZen();
  } else if (e.altKey && e.key === 'ArrowRight' && state.view === 'studio' && state.activeNovel) {
    const maxCh = Math.max(...(state.activeNovel.chapters || []).map(c => c.chapter_num), 1);
    if (state.activeChapterNum < maxCh) selectChapter(state.activeChapterNum + 1);
  } else if (e.altKey && e.key === 'ArrowLeft' && state.view === 'studio' && state.activeChapterNum > 1) {
    selectChapter(state.activeChapterNum - 1);
  }
});

// Boot
async function boot() {
  const savedTheme = localStorage.getItem('novelgen_v5_theme') || 'light';
  document.documentElement.setAttribute('data-theme', savedTheme);
  btnTheme.textContent = savedTheme === 'dark' ? '浅色主题' : '深色主题';

  subscribe(() => renderCurrentView());
  try {
    const [stylesRes, llmRes, novelsRes] = await Promise.all([
      apiFetch('/api/system/styles'),
      apiFetch('/api/system/llm'),
      apiFetch('/api/novels'),
    ]);
    setState({
      styles: stylesRes.styles || [],
      genres: stylesRes.genres || [],
      llm: llmRes,
      novels: novelsRes.novels || [],
    });
  } catch (err) {
    showToast(`初始化失败：${err.message}`);
    renderCurrentView();
  }
}

boot();
