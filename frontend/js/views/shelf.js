import { apiFetch } from '../api.js';
import { escapeHtml, setState, showToast, state } from '../store.js';

let showCreate = false;

export function renderShelf(container, { onOpenNovel, onRefresh }) {
  const styles = state.styles.length ? state.styles : [{ name: '克制镜头', description: '' }];
  const genres = state.genres.length ? state.genres : ['都市悬疑', '古风权谋', '硬核科幻', '现实文学'];

  container.innerHTML = `
    <div class="page-head">
      <div>
        <h1>书架</h1>
        <p>所有作品、大纲契约、角色状态与版本历史统一保存在本地 SQLite 账本中</p>
      </div>
      <div class="row-actions">
        <button class="btn btn-primary" id="btn-toggle-create">${showCreate ? '收起新建表单' : '新建作品'}</button>
      </div>
    </div>

    ${showCreate ? `
      <form class="create-drawer" id="create-form">
        <div class="grid-2">
          <div class="field">
            <label for="f-title">作品名称</label>
            <input id="f-title" required placeholder="例如：雨夜三号仓库">
          </div>
          <div class="field">
            <label for="f-genre">题材</label>
            <select id="f-genre">${genres.map(g => `<option value="${escapeHtml(g)}">${escapeHtml(g)}</option>`).join('')}</select>
          </div>
        </div>
        <div class="grid-3">
          <div class="field">
            <label for="f-style">叙事风格</label>
            <select id="f-style">${styles.map(s => `<option value="${escapeHtml(s.name)}">${escapeHtml(s.name)}</option>`).join('')}</select>
          </div>
          <div class="field">
            <label for="f-mode">创作取向</label>
            <select id="f-mode">
              <option value="web">类型小说（强冲突/钩子）</option>
              <option value="literary">严肃文学（人物驱动/留白）</option>
            </select>
          </div>
          <div class="field">
            <label for="f-words">单章目标字数</label>
            <input id="f-words" type="number" min="200" max="10000" step="100" value="2000">
          </div>
        </div>
        <div class="field">
          <label for="f-premise">核心构想与人物冲突</label>
          <textarea id="f-premise" rows="3" placeholder="用 2-4 句话写清主角处境、核心谜团与不可退让的冲突…"></textarea>
        </div>
        <div class="row-actions">
          <button type="submit" class="btn btn-primary" id="btn-submit-create">创建并规划开篇大纲</button>
          <button type="button" class="btn" id="btn-cancel-create">取消</button>
        </div>
      </form>
    ` : ''}

    ${state.novels.length === 0 ? `
      <div class="empty-state">
        <p>还没有作品。点击右上角「新建作品」，即可在内置演示模式或自有模型下体验四阶段写作流。</p>
      </div>
    ` : `
      <div class="novel-list">
        ${state.novels.map(n => `
          <div class="novel-row">
            <div>
              <h2 class="novel-title" data-open="${escapeHtml(n.id)}">${escapeHtml(n.title)}</h2>
              <div class="novel-meta">
                <span>${escapeHtml(n.genre)}</span>
                <span>${escapeHtml(n.style)}</span>
                <span>已完成 ${n.completed_chapters || 0} / ${n.planned_chapters || n.target_chapters} 章</span>
                <span>${(n.total_words || 0).toLocaleString()} 字</span>
              </div>
              ${n.premise ? `<p class="novel-premise">${escapeHtml(n.premise)}</p>` : ''}
            </div>
            <div class="row-actions">
              <button class="btn btn-primary btn-sm" data-open="${escapeHtml(n.id)}">进入工坊</button>
              <a class="btn btn-sm" href="/api/novels/${encodeURIComponent(n.id)}/export?fmt=txt" download>导出 TXT</a>
              <a class="btn btn-sm" href="/api/novels/${encodeURIComponent(n.id)}/export?fmt=json" download>备份账本</a>
              <button class="btn btn-danger btn-sm" data-del="${escapeHtml(n.id)}">删除</button>
            </div>
          </div>
        `).join('')}
      </div>
    `}
  `;

  container.querySelector('#btn-toggle-create')?.addEventListener('click', () => {
    showCreate = !showCreate;
    renderShelf(container, { onOpenNovel, onRefresh });
  });
  container.querySelector('#btn-cancel-create')?.addEventListener('click', () => {
    showCreate = false;
    renderShelf(container, { onOpenNovel, onRefresh });
  });

  container.querySelector('#create-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = container.querySelector('#btn-submit-create');
    btn.disabled = true;
    btn.textContent = '规划大纲与账本中…';
    try {
      const res = await apiFetch('/api/novels', {
        method: 'POST',
        body: JSON.stringify({
          title: container.querySelector('#f-title').value.trim(),
          genre: container.querySelector('#f-genre').value,
          style: container.querySelector('#f-style').value,
          mode: container.querySelector('#f-mode').value,
          target_words_per_chapter: Number(container.querySelector('#f-words').value) || 2000,
          premise: container.querySelector('#f-premise').value.trim(),
          auto_plan: true,
        }),
      });
      showCreate = false;
      showToast(`已创建《${res.novel.title}》并生成 ${res.planned_count} 章大纲契约`);
      await onRefresh();
      await onOpenNovel(res.novel.id, 1);
    } catch (err) {
      showToast(err.message);
      btn.disabled = false;
      btn.textContent = '创建并规划开篇大纲';
    }
  });

  container.querySelectorAll('[data-open]').forEach(el => {
    el.addEventListener('click', () => onOpenNovel(el.dataset.open, 1));
  });

  container.querySelectorAll('[data-del]').forEach(el => {
    el.addEventListener('click', async () => {
      if (!window.confirm('确认删除这部作品及其所有章节与账本数据？')) return;
      try {
        await apiFetch(`/api/novels/${encodeURIComponent(el.dataset.del)}`, { method: 'DELETE' });
        if (state.activeNovel?.id === el.dataset.del) {
          setState({ activeNovel: null, activeChapter: null });
        }
        showToast('已删除作品');
        await onRefresh();
      } catch (err) {
        showToast(err.message);
      }
    });
  });
}
