/**
 * Unified HTTP & SSE client for NovelGenerator v5.
 */

export async function apiFetch(path, options = {}) {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!resp.ok) {
    let detail = `请求失败 (${resp.status})`;
    try {
      const data = await resp.json();
      detail = data.detail || data.message || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return resp.json();
}

export async function apiStreamSSE(path, body, { onEvent, signal }) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`流式连接失败 (${resp.status})`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      if (!line.startsWith('data:')) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      try {
        onEvent(JSON.parse(raw));
      } catch (_) {}
    }
  }
}
