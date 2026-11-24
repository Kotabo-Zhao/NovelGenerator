import { apiFetch } from '../api.js';
import { escapeHtml, setState, showToast, state } from '../store.js';

export function renderLedger(container, { onReloadLedger }) {
  const novel = state.activeNovel;
  if (!novel) {
    container.innerHTML = `<div class="empty-state"><p>请先在书架打开一部作品，再查看和编辑其统一叙事状态账本。</p></div>`;
    return;
  }

  const { entities, foreshadows, world_entries, canon_events } = state.ledger;

  container.innerHTML = `
    <div class="page-head">
      <div>
        <h1>叙事状态账本 · ${escapeHtml(novel.title)}</h1>
        <p>角色最新状态、伏笔生命周期、世界观规则与已发生正史事件全部由 SQLite 事务维护，写作时自动按需编译注入</p>
      </div>
      <div class="row-actions">
        <input id="ledger-search-input" placeholder="FTS5 全文检索（角色/设定/正文）…" style="padding:7px 10px;border:1px solid var(--line-strong);border-radius:4px;background:var(--bg-sheet);width:240px">
        <button class="btn btn-sm" id="btn-ledger-search">检索</button>
      </div>
    </div>

    <div id="ledger-search-results"></div>

    <section class="insp-sec">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <h3>角色与实体状态（${entities.length}）</h3>
        <button class="btn btn-sm" id="btn-add-entity">新增实体</button>
      </div>
      <table class="ledger-table">
        <thead><tr><th>名称</th><th>当前位置</th><th>当前状态</th><th>说话风格 / 设定</th></tr></thead>
        <tbody>
          ${entities.length ? entities.map(e => `
            <tr>
              <td><strong>${escapeHtml(e.name)}</strong><br><span style="font-size:11px;color:var(--ink-3)">${escapeHtml(e.kind)}</span></td>
              <td>${escapeHtml(e.location || '—')}</td>
              <td>${escapeHtml(e.current_state || '—')}</td>
              <td>${escapeHtml(e.voice_notes || e.profile || '—')}</td>
            </tr>
          `).join('') : `<tr><td colspan="4">暂无实体记录</td></tr>`}
        </tbody>
      </table>
    </section>

    <section class="insp-sec">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <h3>伏笔生命周期（${foreshadows.length}）</h3>
        <button class="btn btn-sm" id="btn-add-fs">新增伏笔</button>
      </div>
      <table class="ledger-table">
        <thead><tr><th>伏笔</th><th>状态</th><th>埋设 / 目标章</th><th>细节说明</th></tr></thead>
        <tbody>
          ${foreshadows.length ? foreshadows.map(f => `
            <tr>
              <td><strong>${escapeHtml(f.title)}</strong></td>
              <td><span class="chip ${f.status === 'resolved' ? 'ok' : 'warn'}">${escapeHtml(f.status)}</span></td>
              <td>第${f.planted_chapter}章 → 第${f.target_chapter}章</td>
              <td>${escapeHtml(f.detail || '—')}</td>
            </tr>
          `).join('') : `<tr><td colspan="4">暂无伏笔记录</td></tr>`}
        </tbody>
      </table>
    </section>

    <section class="insp-sec">
      <h3>已发生正史事件（${canon_events.length} · 写作防重复硬约束）</h3>
      ${canon_events.length
        ? `<ul>${canon_events.map(ev => `<li><strong>第${ev.chapter_num}章</strong>：${escapeHtml(ev.summary)}</li>`).join('')}</ul>`
        : `<p>尚未沉淀正史事件，完成第一章写作后将自动抽取写入。</p>`}
    </section>

    <section class="insp-sec" style="border-bottom:none">
      <h3>世界观与规则条目（${world_entries.length}）</h3>
      ${world_entries.length
        ? `<ul>${world_entries.map(w => `<li><strong>${escapeHtml(w.title)}</strong>（${escapeHtml(w.keywords)}）：${escapeHtml(w.content)}</li>`).join('')}</ul>`
        : `<p>暂无世界书条目。</p>`}
    </section>
  `;

  container.querySelector('#btn-ledger-search')?.addEventListener('click', async () => {
    const q = container.querySelector('#ledger-search-input').value.trim();
    const box = container.querySelector('#ledger-search-results');
    if (!q) { box.innerHTML = ''; return; }
    try {
      const res = await apiFetch(`/api/novels/${encodeURIComponent(novel.id)}/ledger/search?q=${encodeURIComponent(q)}`);
      box.innerHTML = res.hits.length
        ? `<div class="create-drawer"><strong>检索命中（${res.hits.length}）</strong><ul style="margin:8px 0 0 18px">${res.hits.map(h => `<li><span class="chip">${escapeHtml(h.doc_type)}</span> <strong>${escapeHtml(h.title)}</strong>：${escapeHtml(h.snippet)}</li>`).join('')}</ul></div>`
        : `<div class="create-drawer">未找到与「${escapeHtml(q)}」匹配的记录。</div>`;
    } catch (err) {
      showToast(err.message);
    }
  });

  container.querySelector('#btn-add-entity')?.addEventListener('click', async () => {
    const name = window.prompt('输入角色或实体名称：');
    if (!name) return;
    const profile = window.prompt('一句话设定或说话风格：', '') || '';
    const location = window.prompt('当前位置：', '') || '';
    try {
      await apiFetch(`/api/novels/${encodeURIComponent(novel.id)}/ledger/entities`, {
        method: 'POST',
        body: JSON.stringify({ kind: 'character', name, profile, location }),
      });
      showToast(`已写入实体：${name}`);
      await onReloadLedger();
    } catch (err) {
      showToast(err.message);
    }
  });

  container.querySelector('#btn-add-fs')?.addEventListener('click', async () => {
    const title = window.prompt('输入伏笔标题：');
    if (!title) return;
    const detail = window.prompt('伏笔细节与回收线索：', '') || '';
    try {
      await apiFetch(`/api/novels/${encodeURIComponent(novel.id)}/ledger/foreshadows`, {
        method: 'POST',
        body: JSON.stringify({ title, detail, planted_chapter: state.activeChapterNum, target_chapter: state.activeChapterNum + 3 }),
      });
      showToast(`已埋设伏笔：${title}`);
      await onReloadLedger();
    } catch (err) {
      showToast(err.message);
    }
  });
}
