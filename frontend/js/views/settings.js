import { apiFetch } from '../api.js';
import { escapeHtml, setState, showToast, state } from '../store.js';

export function renderSettings(container) {
  const llm = state.llm;
  container.innerHTML = `
    <div class="page-head">
      <div>
        <h1>模型设置</h1>
        <p>支持任意 OpenAI 兼容接口（DeepSeek / OpenAI / Ollama 等）；API Key 仅保存在本机配置文件，接口永不回传明文</p>
      </div>
    </div>

    <form class="create-drawer" id="llm-form" style="max-width:640px">
      <div class="field">
        <label for="s-mode">运行模式</label>
        <select id="s-mode">
          <option value="demo" ${llm.mode === 'demo' ? 'selected' : ''}>内置演示模式（零 API Key 体验完整写作流）</option>
          <option value="openai_compatible" ${llm.mode === 'openai_compatible' ? 'selected' : ''}>外部模型接口（OpenAI / DeepSeek / 本地 Ollama）</option>
        </select>
      </div>
      <div class="field">
        <label for="s-url">API Base URL</label>
        <input id="s-url" value="${escapeHtml(llm.base_url || 'https://api.deepseek.com/v1')}" placeholder="https://api.deepseek.com/v1">
      </div>
      <div class="grid-2">
        <div class="field">
          <label for="s-model">模型名称</label>
          <input id="s-model" value="${escapeHtml(llm.model || 'deepseek-chat')}" placeholder="deepseek-chat">
        </div>
        <div class="field">
          <label for="s-temp">创作温度 (0.0 – 1.5)</label>
          <input id="s-temp" type="number" step="0.05" min="0" max="1.5" value="${llm.temperature ?? 0.8}">
        </div>
      </div>
      <div class="field">
        <label for="s-key">API Key ${llm.has_api_key ? `（已保存：${escapeHtml(llm.api_key_masked)}，留空则保持不变）` : ''}</label>
        <input id="s-key" type="password" autocomplete="off" placeholder="输入新的 API Key">
      </div>
      <div class="row-actions">
        <button type="submit" class="btn btn-primary">保存配置</button>
        <button type="button" class="btn" id="btn-test-llm">测试连通性</button>
      </div>
    </form>
  `;

  container.querySelector('#llm-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    try {
      const updated = await apiFetch('/api/system/llm', {
        method: 'PUT',
        body: JSON.stringify({
          mode: container.querySelector('#s-mode').value,
          base_url: container.querySelector('#s-url').value.trim(),
          model: container.querySelector('#s-model').value.trim(),
          temperature: Number(container.querySelector('#s-temp').value) || 0.8,
          api_key: container.querySelector('#s-key').value.trim(),
        }),
      });
      setState({ llm: updated });
      showToast('模型配置已保存');
    } catch (err) {
      showToast(err.message);
    }
  });

  container.querySelector('#btn-test-llm')?.addEventListener('click', async () => {
    try {
      const res = await apiFetch('/api/system/llm/test', { method: 'POST' });
      showToast(res.message);
    } catch (err) {
      showToast(err.message);
    }
  });
}
