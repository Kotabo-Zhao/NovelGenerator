import { apiFetch, apiStreamSSE } from '../api.js';
import { escapeHtml, setState, showToast, state } from '../store.js';

let activeAbort = null;

export function abortActiveStream() {
  if (activeAbort) {
    activeAbort.abort();
    activeAbort = null;
  }
}

export function renderStudio(container, { onReloadNovel, onSelectChapter, onToggleZen }) {
  const novel = state.activeNovel;
  const chapter = state.activeChapter;
  if (!novel) {
    container.innerHTML = `<div class="empty-state"><p>请先在书架选择或新建一部作品，再进入写作工坊。</p></div>`;
    return;
  }

  const chNum = state.activeChapterNum;
  const contract = chapter?.contract || { chapter_num: chNum, title: `第${chNum}章`, summary: '', must_happen: [], characters: [] };
  const content = chapter?.content || '';
  const words = content.replace(/\s+/g, '').length;
  const paras = content.split(/\n+/).map(s => s.trim()).filter(Boolean).length;
  const readMins = Math.max(1, Math.ceil(words / 450));
  const craft = chapter?.craft || null;
  const versions = chapter?.versions || [];

  container.innerHTML = `
    <div class="studio-grid">
      <section class="manuscript-pane">
        <div class="studio-topbar">
          <div class="studio-title-wrap">
            <strong>第${chNum}章 · ${escapeHtml(contract.title || '')}</strong>
            <div class="studio-stats">
              <span id="stat-words">${words} 字</span>
              <span id="stat-paras">${paras} 段</span>
              <span id="stat-mins">约 ${readMins} 分钟阅读</span>
            </div>
          </div>
          <div class="row-actions">
            ${state.streaming
              ? `<button class="btn btn-danger btn-sm" id="btn-stop-draft">停止生成</button>`
              : `<button class="btn btn-primary btn-sm" id="btn-draft">${content ? '重新生成' : '开始写作'}</button>`}
            <button class="btn btn-sm" id="btn-save-ch" ${state.streaming ? 'disabled' : ''}>保存章节</button>
            <button class="btn btn-sm" id="btn-craft-now" ${state.streaming ? 'disabled' : ''}>文风体检</button>
            <button class="btn btn-sm" id="btn-surgical" ${state.streaming ? 'disabled' : ''} title="选中一段正文后点击精修">划词精修</button>
            <button class="btn btn-sm" id="btn-zen" title="Alt+Z 切换，Esc 退出">${state.zenMode ? '退出专注' : '专注模式'}</button>
          </div>
        </div>

        ${state.stageMessage ? `<div class="stage-banner" id="stage-banner">${escapeHtml(state.stageMessage)}</div>` : ''}

        <div class="manuscript-scroll">
          <textarea class="manuscript-editor" id="manuscript-editor" placeholder="点击右上角「开始写作」启动四阶段流式生成，或直接在此输入正文…">${escapeHtml(content)}</textarea>
        </div>

        <div class="revision-bar">
          <input id="revision-input" placeholder="输入修改意见（如：加强对话潜台词、压缩环境铺垫、章末钩子更凌厉），点击按意见重写…">
          <button class="btn btn-sm" id="btn-revise" ${state.streaming ? 'disabled' : ''}>按意见重写</button>
        </div>
      </section>

      <aside class="inspector">
        <div class="insp-tabs">
          <button class="insp-tab ${state.inspectorTab === 'contract' ? 'active' : ''}" data-tab="contract">大纲契约</button>
          <button class="insp-tab ${state.inspectorTab === 'craft' ? 'active' : ''}" data-tab="craft">文风体检</button>
          <button class="insp-tab ${state.inspectorTab === 'versions' ? 'active' : ''}" data-tab="versions">版本历史</button>
        </div>
        <div class="insp-body" id="insp-body">
          ${renderInspectorContent(contract, craft, versions)}
        </div>
      </aside>
    </div>
  `;

  const editor = container.querySelector('#manuscript-editor');
  const revInput = container.querySelector('#revision-input');

  editor.addEventListener('input', () => {
    const val = editor.value;
    if (state.activeChapter) state.activeChapter.content = val;
    const wc = val.replace(/\s+/g, '').length;
    const pc = val.split(/\n+/).map(s => s.trim()).filter(Boolean).length;
    container.querySelector('#stat-words').textContent = `${wc} 字`;
    container.querySelector('#stat-paras').textContent = `${pc} 段`;
    container.querySelector('#stat-mins').textContent = `约 ${Math.max(1, Math.ceil(wc / 450))} 分钟阅读`;
  });

  container.querySelectorAll('.insp-tab').forEach(btn => {
    btn.addEventListener('click', () => setState({ inspectorTab: btn.dataset.tab }));
  });

  container.querySelector('#btn-zen')?.addEventListener('click', () => onToggleZen());

  async function runDraft(feedback = '') {
    abortActiveStream();
    activeAbort = new AbortController();
    setState({ streaming: true, stageMessage: '准备四阶段写作引擎…' });
    let acc = '';
    try {
      await apiStreamSSE(
        `/api/novels/${encodeURIComponent(novel.id)}/chapters/${chNum}/draft-stream`,
        { revision_feedback: feedback, auto_commit: true },
        {
          signal: activeAbort.signal,
          onEvent: (evt) => {
            if (evt.type === 'stage') {
              const banner = container.querySelector('#stage-banner');
              state.stageMessage = evt.message;
              if (banner) banner.textContent = evt.message;
            } else if (evt.type === 'chunk') {
              acc += evt.text;
              const ed = container.querySelector('#manuscript-editor');
              if (ed) {
                ed.value = acc;
                ed.scrollTop = ed.scrollHeight;
              }
            } else if (evt.type === 'done') {
              showToast(`第${chNum}章已定稿并写入状态账本（质感 ${evt.craft?.craft_score ?? '-'}/100）`);
              setState({ streaming: false, stageMessage: '', inspectorTab: 'craft' });
              onReloadNovel(chNum);
            } else if (evt.type === 'error') {
              showToast(`生成出错：${evt.message}`);
            }
          },
        },
      );
    } catch (err) {
      if (err.name === 'AbortError') {
        showToast('已停止生成');
      } else {
        showToast(err.message);
      }
      setState({ streaming: false, stageMessage: '' });
    } finally {
      activeAbort = null;
    }
  }

  container.querySelector('#btn-draft')?.addEventListener('click', () => runDraft(''));
  container.querySelector('#btn-stop-draft')?.addEventListener('click', () => abortActiveStream());
  container.querySelector('#btn-revise')?.addEventListener('click', () => {
    const fb = revInput.value.trim();
    if (!fb) {
      showToast('请先输入修改意见，或点击「文风体检」一键生成建议');
      return;
    }
    runDraft(fb);
  });

  container.querySelector('#btn-save-ch')?.addEventListener('click', async () => {
    const text = editor.value.trim();
    if (!text) return showToast('正文为空，无需保存');
    try {
      const saved = await apiFetch(`/api/novels/${encodeURIComponent(novel.id)}/chapters/${chNum}`, {
        method: 'PUT',
        body: JSON.stringify({ content: text, note: '手动编辑保存', extract_state: true }),
      });
      setState({ activeChapter: saved });
      showToast('已保存章节并同步更新叙事账本');
      await onReloadNovel(chNum);
    } catch (err) {
      showToast(err.message);
    }
  });

  container.querySelector('#btn-craft-now')?.addEventListener('click', async () => {
    const text = editor.value.trim();
    if (!text) return showToast('请先生成或输入正文再进行体检');
    try {
      const report = await apiFetch(`/api/novels/${encodeURIComponent(novel.id)}/chapters/${chNum}/craft-check`, {
        method: 'POST',
        body: JSON.stringify({ text }),
      });
      if (state.activeChapter) state.activeChapter.craft = report;
      setState({ inspectorTab: 'craft' });
      showToast(report.summary);
    } catch (err) {
      showToast(err.message);
    }
  });

  container.querySelector('#btn-surgical')?.addEventListener('click', async () => {
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const selected = editor.value.slice(start, end).trim();
    if (selected.length < 4) {
      return showToast('请先在正文中鼠标划选至少 4 个字的片段');
    }
    const instruction = window.prompt('请输入对选中片段的精修指令：', '增强动作画面感与感官细节，去掉心理直述');
    if (!instruction) return;
    let rewritten = '';
    try {
      await apiStreamSSE(
        `/api/novels/${encodeURIComponent(novel.id)}/chapters/${chNum}/surgical-stream`,
        { selected_text: selected, instruction },
        {
          onEvent: (evt) => {
            if (evt.type === 'chunk') rewritten += evt.text;
            if (evt.type === 'done' && evt.rewritten) rewritten = evt.rewritten;
          },
        },
      );
      if (rewritten) {
        editor.value = editor.value.slice(0, start) + rewritten + editor.value.slice(end);
        editor.dispatchEvent(new Event('input'));
        showToast('已完成局部精修，确认后请点击「保存章节」');
      }
    } catch (err) {
      showToast(err.message);
    }
  });

  // Inspector dynamic buttons
  container.querySelector('#btn-apply-craft-prompt')?.addEventListener('click', () => {
    const prompt = state.activeChapter?.craft?.suggested_revision_prompt;
    if (prompt && revInput) {
      revInput.value = prompt;
      revInput.focus();
      showToast('已填入下方改稿框，点击「按意见重写」即可执行');
    }
  });

  container.querySelectorAll('[data-restore]').forEach(btn => {
    btn.addEventListener('click', async () => {
      try {
        const restored = await apiFetch(
          `/api/novels/${encodeURIComponent(novel.id)}/chapters/${chNum}/versions/${btn.dataset.restore}/restore`,
          { method: 'POST' },
        );
        setState({ activeChapter: restored });
        showToast(`已回滚至版本 #${btn.dataset.restore}`);
        await onReloadNovel(chNum);
      } catch (err) {
        showToast(err.message);
      }
    });
  });
}

function renderInspectorContent(contract, craft, versions) {
  if (state.inspectorTab === 'craft') {
    if (!craft) {
      return `<div class="empty-state"><p>点击顶部「文风体检」，零成本检测 AI 套话、句长节奏与感官密度。</p></div>`;
    }
    return `
      <div class="insp-sec">
        <div class="metric-row">
          <div class="metric-cell"><span>文风质感</span><strong>${craft.craft_score}</strong></div>
          <div class="metric-cell"><span>AI 痕迹</span><strong>${craft.ai_score}</strong></div>
          <div class="metric-cell"><span>对话占比</span><strong>${craft.dialogue_ratio_pct}%</strong></div>
          <div class="metric-cell"><span>句长节奏 CV</span><strong>${craft.sentence_rhythm_cv}</strong></div>
        </div>
        <p>${escapeHtml(craft.summary)}</p>
        ${craft.suggested_revision_prompt ? `<button class="btn btn-primary btn-sm" id="btn-apply-craft-prompt">一键填入改稿框</button>` : ''}
      </div>
      ${(craft.suggestions || []).length ? `
        <div class="insp-sec">
          <h4>可执行改稿建议</h4>
          <ul>${craft.suggestions.map(s => `<li>${escapeHtml(s)}</li>`).join('')}</ul>
        </div>
      ` : ''}
      ${(craft.flagged_sections || []).length ? `
        <div class="insp-sec">
          <h4>命中特征</h4>
          <div>${craft.flagged_sections.map(f => `<span class="chip ${f.severity === 'high' ? 'high' : 'warn'}" title="${escapeHtml(f.excerpt || '')}">${escapeHtml(f.reason)}</span>`).join('')}</div>
        </div>
      ` : ''}
    `;
  }

  if (state.inspectorTab === 'versions') {
    if (!versions.length) {
      return `<div class="empty-state"><p>本章尚无历史快照，生成或保存后会自动留存。</p></div>`;
    }
    return versions.map(v => `
      <div class="insp-sec">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <strong>版本 #${v.id} · ${v.word_count} 字</strong>
          <button class="text-btn" data-restore="${v.id}">恢复此版</button>
        </div>
        <p style="font-size:12px;color:var(--ink-3)">${escapeHtml(v.note)} · ${escapeHtml(v.created_at)}</p>
        <p>${escapeHtml(v.preview)}…</p>
      </div>
    `).join('');
  }

  return `
    <div class="insp-sec">
      <h4>场景概要</h4>
      <p>${escapeHtml(contract.summary || '尚未填写概要')}</p>
    </div>
    ${contract.goal ? `<div class="insp-sec"><h4>主角目标</h4><p>${escapeHtml(contract.goal)}</p></div>` : ''}
    ${contract.conflict ? `<div class="insp-sec"><h4>核心冲突</h4><p>${escapeHtml(contract.conflict)}</p></div>` : ''}
    ${(contract.characters || []).length ? `
      <div class="insp-sec">
        <h4>出场角色</h4>
        <div>${contract.characters.map(c => `<span class="chip">${escapeHtml(c)}</span>`).join('')}</div>
      </div>
    ` : ''}
    ${(contract.must_happen || []).length ? `
      <div class="insp-sec">
        <h4>必发事件</h4>
        <ul>${contract.must_happen.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul>
      </div>
    ` : ''}
    ${contract.hook_direction ? `<div class="insp-sec"><h4>章末钩子方向</h4><p>${escapeHtml(contract.hook_direction)}</p></div>` : ''}
  `;
}
