/* ================================================================
   角色扮演页逻辑（原生 Vue 3 global，无构建步骤）
   结构：主题 → 常量/工具 → API → 状态 → 计算属性 → 数据加载
        → 角色 CRUD / 导入 / 从本书生成 → 聊天 / SSE
        → 设置 → 提示与确认 → 生命周期 → 挂载
   ================================================================ */
(function () {
  'use strict';

  const showBootError = NovelUI.showError;
  var createApp = Vue.createApp;
  var ref = Vue.ref;
  var reactive = Vue.reactive;
  var computed = Vue.computed;
  var onMounted = Vue.onMounted;
  var nextTick = Vue.nextTick;

  /* ─────────────────────────── 主题 ─────────────────────────── */
  // 主应用用 localStorage.theme 记录 dark/light，这里同步；默认深色。
  function applyStoredTheme() { NovelUI.setTheme(NovelUI.storage.get('theme','light')); }

  // 当前小说 id：仅当带 ?novel=xxx 时允许「从本书生成」。
  var novelId = '';
  try {
    novelId = new URLSearchParams(window.location.search).get('novel') || '';
  } catch (_) { novelId = ''; }

  /* ─────────────────────────── 工具函数 ─────────────────────────── */
  function nowTs() {
    return Math.floor(Date.now() / 1000);
  }

  function clampNumber(value, min, max, fallback) {
    var n = Number(value);
    if (!isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, n));
  }

  function splitLines(text) {
    return String(text || '').split(/\r?\n/).map(function (line) {
      return line.trim();
    }).filter(Boolean);
  }

  function splitTags(text) {
    return String(text || '').split(/[,，]/).map(function (tag) {
      return tag.trim();
    }).filter(Boolean);
  }

  function stripDataUrl(dataUrl) {
    return String(dataUrl || '').replace(/^data:[^,]*;base64,/, '');
  }

  function shorten(text, max) {
    var s = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
    return s.length > max ? s.slice(0, max) + '…' : s;
  }

  function readFileAsDataURL(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result || '')); };
      reader.onerror = function () { reject(new Error('读取文件失败，请重试')); };
      reader.readAsDataURL(file);
    });
  }

  var API = '/api/roleplay';
  const api = NovelUI.requestJson;
  const readSSE = NovelUI.readSSE;

  /* ═══════════════════════════ Vue 应用 ═══════════════════════════ */
  var app = createApp({
    setup: function () {
      /* ───────────────────── 基础状态 ───────────────────── */
      var characters = ref([]);
      var charactersLoading = ref(false);
      var charactersError = ref('');
      var currentId = ref('');
      const connectionOpen = ref(false), theme = ref(NovelUI.storage.get('theme','light'));
      window.addEventListener('novelgen:connection', () => { connectionOpen.value = true; });
      const characterQuery = ref(''), visibleCount = ref(150), editingMessage = ref(null), editedContent = ref(''), editSaving = ref(false);
      let streamSettled = Promise.resolve(), settleStream = null;
      const pendingRequests = new Map();
      var messages = ref([]);
      var chatLoading = ref(false);
      var chatError = ref('');
      var streaming = ref(false);
      var draft = ref('');
      var deleting = ref(false);
      var clearing = ref(false);
      var importLoading = ref(false);
      var fromNovelLoading = ref(false);

      // 自动滚动：用户向上翻看历史时暂停自动跟随
      var autoScroll = ref(true);
      var chatEl = ref(null);
      var importInputEl = ref(null);
      var avatarInputEl = ref(null);

      // 流式内部控制
      var abortCtrl = null;   // 当前 AbortController（普通变量即可，无需响应式）
      var streamSeq = 0;      // 防止旧流覆盖新对话
      var streamError = '';   // 当前流内错误消息

      // 服务端 meta 事件回报的名称
      var metaUser = ref('');
      var metaCharacter = ref('');

      /* ───────────────────── 设置状态 ───────────────────── */
      var settings = reactive({
        user_name: '旅行者',
        user_persona: '',
        system_prompt: '',
        temperature: 0.85,
        max_tokens: 600,
        context_messages: 40
      });
      var settingsOpen = ref(false);
      var settingsLoading = ref(false);
      var settingsSaving = ref(false);
      var settingsForm = reactive({
        user_name: '旅行者',
        user_persona: '',
        system_prompt: '',
        temperature: 0.85,
        max_tokens: 600,
        context_messages: 40
      });

      /* ───────────────────── 角色编辑状态 ───────────────────── */
      var charModalOpen = ref(false);
      var charSaving = ref(false);
      var charFormError = ref('');
      var avatarPreview = ref('');
      var pendingAvatarBase64 = '';
      var avatarFailed = reactive({}); // 加载失败的头像 id → true
      var charForm = reactive({
        id: '',
        name: '',
        description: '',
        personality: '',
        scenario: '',
        first_mes: '',
        mes_example: '',
        system_prompt: '',
        post_history_instructions: '',
        alternate_greetings: '',
        tags: '',
        creator: '',
        character_version: ''
      });

      /* ───────────────────── 提示 / 确认 ───────────────────── */
      var toast = reactive({ visible: false, title: '', message: '', icon: 'ℹ️', kind: 'info' });
      var toastTimer = 0;

      function showToast(message, title, icon, kind) {
        toast.message = message == null ? '' : String(message);
        toast.title = title || '提示';
        toast.icon = icon || 'ℹ️';
        toast.kind = kind || 'info';
        toast.visible = true;
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { toast.visible = false; }, toast.kind === 'error' ? 6500 : 3800);
      }

      var confirmBox = reactive({ visible: false, title: '', message: '', resolve: null });

      function askConfirm(title, message) {
        return new Promise(function (resolve) {
          confirmBox.title = title;
          confirmBox.message = message;
          confirmBox.visible = true;
          confirmBox.resolve = resolve;
        });
      }

      function resolveConfirm(value) {
        var fn = confirmBox.resolve;
        confirmBox.visible = false;
        confirmBox.resolve = null;
        if (fn) fn(!!value);
      }

      /* ───────────────────── 计算属性 ───────────────────── */
      var currentCharacter = computed(function () {
        var id = currentId.value;
        if (!id) return null;
        for (var i = 0; i < characters.value.length; i++) {
          if (characters.value[i].id === id) return characters.value[i];
        }
        return null;
      });

      var canSend = computed(function () {
        return !!draft.value.trim() && !!currentCharacter.value && !streaming.value && !chatLoading.value;
      });

      var userDisplayName = computed(function () {
        return metaUser.value || settings.user_name || '旅行者';
      });

      var charDisplayName = computed(function () {
        if (metaCharacter.value) return metaCharacter.value;
        return currentCharacter.value ? currentCharacter.value.name : '角色';
      });

      var userInitial = computed(function () {
        var name = userDisplayName.value || '你';
        return name.trim().charAt(0) || '你';
      });

      var composerPlaceholder = computed(function () {
        if (!currentCharacter.value) return '先选择或创建一个角色…';
        return '对 ' + currentCharacter.value.name + ' 说点什么…';
      });

      var charModalTitle = computed(function () {
        return charForm.id ? '编辑角色' : '新建角色';
      });

      var modalInitial = computed(function () {
        var name = String(charForm.name || '').trim();
        return name ? name.charAt(0) : '?';
      });

      var modalAvatarSrc = computed(function () {
        if (avatarPreview.value) return avatarPreview.value;
        if (!charForm.id) return '';
        for (var i = 0; i < characters.value.length; i++) {
          var item = characters.value[i];
          if (item.id === charForm.id) {
            if (item.avatar_url && !avatarFailed[item.id]) return item.avatar_url;
            return '';
          }
        }
        return '';
      });

      // 开场白索引：0 = first_mes，1..n = alternate_greetings[n-1]
      var greetingOptions = computed(function () {
        var options = [];
        var ch = currentCharacter.value;
        if (!ch) return options;
        options.push({ index: 0, label: '开场白（first_mes）' });
        var greetings = Array.isArray(ch.alternate_greetings) ? ch.alternate_greetings : [];
        greetings.forEach(function (greeting, i) {
          options.push({ index: i + 1, label: '备选 ' + (i + 1) + '：' + shorten(greeting, 16) });
        });
        return options;
      });

      /* ───────────────────── 展示辅助 ───────────────────── */
      function sourceLabel(source) {
        var map = {
          png: 'PNG 角色卡',
          json: 'JSON 角色卡',
          manual: '手动创建',
          novel: '来自本书',
          builtin: '内置角色'
        };
        return map[source] || '角色';
      }

      function charInitial(character) {
        var name = character && character.name ? String(character.name).trim() : '';
        return name ? name.charAt(0) : '?';
      }

      function onAvatarError(id) {
        if (!id) return;
        avatarFailed[id] = true;
      }

      /* ───────────────────── 设置读写 ───────────────────── */
      function applySettingsPayload(payload) {
        if (!payload || typeof payload !== 'object') return;
        ['user_name', 'user_persona', 'system_prompt'].forEach(function (key) {
          if (typeof payload[key] === 'string') settings[key] = payload[key];
        });
        var temperature = Number(payload.temperature);
        if (isFinite(temperature)) settings.temperature = clampNumber(temperature, 0, 2, settings.temperature);
        var maxTokens = Number(payload.max_tokens);
        if (isFinite(maxTokens)) settings.max_tokens = Math.round(clampNumber(maxTokens, 64, 4096, settings.max_tokens));
        var contextMessages = Number(payload.context_messages);
        if (isFinite(contextMessages)) settings.context_messages = Math.round(clampNumber(contextMessages, 4, 200, settings.context_messages));
      }

      function copySettingsToForm() {
        settingsForm.user_name = settings.user_name;
        settingsForm.user_persona = settings.user_persona;
        settingsForm.system_prompt = settings.system_prompt;
        settingsForm.temperature = settings.temperature;
        settingsForm.max_tokens = settings.max_tokens;
        settingsForm.context_messages = settings.context_messages;
      }

      async function loadSettings(silent) {
        try {
          var data = await api(API + '/settings');
          applySettingsPayload(data.settings || data);
          return settings;
        } catch (err) {
          if (!silent) showToast(err.message, '读取设置失败', '⚠️', 'error');
          throw err;
        }
      }

      async function openSettings() {
        settingsOpen.value = true;
        copySettingsToForm();
        settingsLoading.value = true;
        try {
          await loadSettings(true);
          copySettingsToForm();
        } catch (err) {
          showToast(err.message, '读取设置失败', '⚠️', 'error');
        } finally {
          settingsLoading.value = false;
        }
      }

      function closeSettings() {
        if (settingsSaving.value) return;
        settingsOpen.value = false;
      }

      async function saveSettings() {
        if (settingsSaving.value) return;
        var temperature = clampNumber(settingsForm.temperature, 0, 2, 0.85);
        var maxTokens = Math.round(clampNumber(settingsForm.max_tokens, 64, 4096, 600));
        var contextMessages = Math.round(clampNumber(settingsForm.context_messages, 4, 200, 40));
        settingsSaving.value = true;
        try {
          var payload = {
            user_name: String(settingsForm.user_name || '').trim(),
            user_persona: settingsForm.user_persona || '',
            system_prompt: settingsForm.system_prompt || '',
            temperature: temperature,
            max_tokens: maxTokens,
            context_messages: contextMessages
          };
          var data = await api(API + '/settings', { method: 'PUT', body: payload });
          applySettingsPayload(data.settings || payload);
          copySettingsToForm();
          settingsOpen.value = false;
          showToast('角色扮演设置已保存', '保存成功', '✅', 'success');
        } catch (err) {
          showToast(err.message, '保存设置失败', '⚠️', 'error');
        } finally {
          settingsSaving.value = false;
        }
      }

      /* ───────────────────── 角色列表与聊天加载 ───────────────────── */
      async function loadCharacters() {
        charactersLoading.value = true;
        charactersError.value = '';
        try {
          var data = await api(API + '/characters');
          characters.value = Array.isArray(data.characters) ? data.characters : [];
          if (currentId.value && !characters.value.some(function (c) { return c.id === currentId.value; })) {
            currentId.value = '';
          }
        } catch (err) {
          characters.value = [];
          charactersError.value = err.message;
          showToast(err.message, '加载角色失败', '⚠️', 'error');
        } finally {
          charactersLoading.value = false;
        }
      }

      async function reloadCharacters() {
        await loadCharacters();
        if (!characters.value.length) return;
        var target = currentId.value || characters.value[0].id;
        await selectCharacter(target);
      }

      var chatSeq = 0;

      // 打开某角色的聊天：GET chat；若为空则自动 POST start 生成开场白
      async function openChat(id) {
        var seq = ++chatSeq;
        chatLoading.value = true;
        chatError.value = '';
        messages.value = [];
        autoScroll.value = true;
        try {
          var data = await api(API + '/characters/' + encodeURIComponent(id) + '/chat');
          if (seq !== chatSeq) return;
          var list = Array.isArray(data.messages) ? data.messages : [];
          if (!list.length) {
            data = await api(API + '/characters/' + encodeURIComponent(id) + '/chat/start', { method: 'POST' });
            if (seq !== chatSeq) return;
            list = Array.isArray(data.messages) ? data.messages : [];
          }
          messages.value = list;
          scrollToBottom();
        } catch (err) {
          if (seq === chatSeq) chatError.value = err.message;
        } finally {
          if (seq === chatSeq) chatLoading.value = false;
        }
      }

      async function selectCharacter(id) {
        var found = null;
        for (var i = 0; i < characters.value.length; i++) {
          if (characters.value[i].id === id) { found = characters.value[i]; break; }
        }
        if (!found) return;
        if (streaming.value) await stopStream();
        if (currentId.value) NovelUI.storage.set('rp.draft.'+currentId.value, draft.value);
        currentId.value = id;
        NovelUI.storage.set('rp.currentCharacter', id);
        draft.value = NovelUI.storage.get('rp.draft.'+id);
        visibleCount.value = 150;
        resizeComposer();
        metaUser.value = '';
        metaCharacter.value = '';
        await openChat(id);
      }

      function onCharacterClick(id) {
        if (!id) return;
        if (id === currentId.value && !chatError.value && messages.value.length) return;
        selectCharacter(id);
      }

      function onTopSelect(event) {
        onCharacterClick(event && event.target ? event.target.value : '');
      }

      // SSE 结束后重新拉取聊天记录，以服务端落盘内容为准（含中断时保存的部分）
      async function resyncChat(charId, seq) {
        try {
          var data;
          for (let attempt=0; attempt<20; attempt++) {
            data = await api(API + '/characters/' + encodeURIComponent(charId) + '/chat');
            if (!data.generating) break;
            await new Promise(resolve => setTimeout(resolve,150));
          }
          if (seq === streamSeq && currentId.value === charId) {
            messages.value = Array.isArray(data.messages) ? data.messages : messages.value;
            if (autoScroll.value) scrollToBottom();
          }
        } catch (_) {
          if (seq === streamSeq && currentId.value === charId) {
            // 同步失败：保留本地内容，但移除完全为空的 assistant 占位气泡
            var last = messages.value[messages.value.length - 1];
            if (last && last.role === 'assistant' && !last.content && !last.streaming) {
              messages.value.pop();
            }
          }
        }
      }

      /* ───────────────────── 聊天工具条动作 ───────────────────── */
      async function startChat() {
        var ch = currentCharacter.value;
        if (!ch || chatLoading.value) return;
        chatLoading.value = true;
        chatError.value = '';
        try {
          var data = await api(API + '/characters/' + encodeURIComponent(ch.id) + '/chat/start', { method: 'POST' });
          messages.value = Array.isArray(data.messages) ? data.messages : [];
          scrollToBottom();
          if (!messages.value.length) {
            showToast('该角色还没有开场白，请先在「编辑角色」中填写 first_mes', '暂无内容', 'ℹ️', 'info');
          }
        } catch (err) {
          chatError.value = err.message;
          showToast(err.message, '开始对话失败', '⚠️', 'error');
        } finally {
          chatLoading.value = false;
        }
      }

      function retryChat() {
        if (currentId.value) openChat(currentId.value);
      }

      async function clearChat(greetingIndex) {
        var ch = currentCharacter.value;
        if (!ch || streaming.value || clearing.value) return;
        var useGreeting = typeof greetingIndex === 'number' && isFinite(greetingIndex);
        var ok = await askConfirm(
          '开启新对话',
          useGreeting
            ? '当前对话会保留历史快照，然后使用所选开场白重新开始。'
            : '当前对话会保留历史快照，然后随机选择一条开场白重新开始。'
        );
        if (!ok) return;
        clearing.value = true;
        try {
          var body = useGreeting ? { greeting_index: greetingIndex } : {};
          var data = await api(API + '/characters/' + encodeURIComponent(ch.id) + '/chat/clear', { method: 'POST', body: body });
          if (currentId.value === ch.id) {
            messages.value = Array.isArray(data.messages) ? data.messages : [];
            autoScroll.value = true;
            scrollToBottom();
          }
          showToast('对话已重新开始', '已清空', '🧹', 'success');
        } catch (err) {
          showToast(err.message, '清空对话失败', '⚠️', 'error');
        } finally {
          clearing.value = false;
        }
      }

      function onGreetingSelect(event) {
        var target = event && event.target ? event.target : null;
        var raw = target ? target.value : '';
        if (target) target.value = '';
        if (raw === '') return;
        var index = Number(raw);
        if (!isFinite(index)) return;
        clearChat(index);
      }

      function scrollToBottom() {
        nextTick(function () {
          var el = chatEl.value;
          if (!el) return;
          el.scrollTop = el.scrollHeight;
        });
      }

      function onChatScroll() {
        var el = chatEl.value;
        if (!el) return;
        // 距底部 90px 以内才继续自动跟随，用户上翻后停止
        autoScroll.value = (el.scrollHeight - el.scrollTop - el.clientHeight) < 90;
      }

      /* ───────────────────── 流式发送 / 重新生成 ───────────────────── */
      async function stopStream() {
        if (abortCtrl) abortCtrl.abort();
        await streamSettled;
      }

      async function runStream(options) {
        if (streaming.value) return;
        const charId = options.charId, seq = ++streamSeq;
        const controller = new AbortController();
        abortCtrl = controller;
        streaming.value = true;
        streamSettled = new Promise(resolve => { settleStream = resolve; });
        autoScroll.value = true;
        let completed = false, acknowledged = false, duplicate = false, pendingText = '', flushTimer = null;
        const responseMessage = { role:'assistant', content:'', ts:nowTs(), streaming:true };
        messages.value.push(responseMessage);
        const index = messages.value.length-1;
        function flush() {
          clearTimeout(flushTimer); flushTimer = null;
          if (pendingText && currentId.value === charId) {
            messages.value[index].content += pendingText;
            pendingText = '';
            if (autoScroll.value) scrollToBottom();
          }
        }
        scrollToBottom();
        try {
          const response = await fetch(options.url, { method:'POST', signal:controller.signal,
            headers:{'Content-Type':'application/json','Accept':'text/event-stream'},
            body:options.body ? JSON.stringify(options.body) : undefined });
          await readSSE(response, event => {
            if (event.type === 'meta') {
              acknowledged = true;
              metaCharacter.value = event.character || '';
              metaUser.value = event.user || '';
              messages.value[index].id = event.message_id;
              if (event.user_message_id && index > 0) messages.value[index-1].id = event.user_message_id;
            } else if (event.type === 'delta') {
              pendingText += NovelUI.textContent(event.content);
              if (!flushTimer) flushTimer = setTimeout(flush,32);
            } else if (event.type === 'error') {
              throw new Error(typeof event.message === 'string' ? event.message : '生成失败，请重试');
            } else if (event.type === 'done') {
              flush(); completed = true; acknowledged = true; duplicate = !!event.duplicate;
              if (event.message && currentId.value === charId) messages.value[index] = {...event.message,content:NovelUI.textContent(event.message.content)};
              return false;
            }
          });
          if (!completed) throw new Error('连接提前结束，已收到的内容会被保留，可以重新生成');
          if (options.body) pendingRequests.delete(charId + ':' + options.body.message);
        } catch (error) {
          if (error.name === 'AbortError') showToast('已收到的内容已保留，随时可以继续对话', '已停止', '✓', 'info');
          else showToast(error.message || '生成失败，请重试', '暂时无法回复', '!', 'error');
          if (!acknowledged && options.body && !draft.value) draft.value = options.body.message;
        } finally {
          flush(); clearTimeout(flushTimer);
          if (messages.value[index]) messages.value[index].streaming = false;
          if (!completed || duplicate) await resyncChat(charId,seq);
          if (seq === streamSeq) { streaming.value = false; abortCtrl = null; }
          if (settleStream) settleStream();
        }
      }

      async function sendMessage() {
        var text = draft.value.trim();
        var ch = currentCharacter.value;
        if (!text || !ch || streaming.value || chatLoading.value) return;
        var charId = ch.id;
        draft.value = '';
        resizeComposer();
        autoScroll.value = true;
        // 先在本地显示用户消息，服务端同样会先落盘该消息
        messages.value.push({ role: 'user', content: text, ts: nowTs() });
        scrollToBottom();
        await runStream({
          charId: charId,
          url: API + '/characters/' + encodeURIComponent(charId) + '/chat/send',
          body: { message: text, request_id: requestId(charId,text) }
        });
      }

      async function regenerate() {
        var ch = currentCharacter.value;
        if (!ch || streaming.value || chatLoading.value) return;
        var hasUser = messages.value.some(function (m) { return m.role === 'user'; });
        if (!hasUser) {
          showToast('还没有你的消息，无法重新生成', '无法重新生成', '⚠️', 'info');
          return;
        }
        // 服务端会删除末尾 assistant 消息后重新生成，本地先移除末尾气泡
        while (messages.value.length && messages.value[messages.value.length - 1].role === 'assistant') {
          messages.value.pop();
        }
        autoScroll.value = true;
        await runStream({
          charId: ch.id,
          url: API + '/characters/' + encodeURIComponent(ch.id) + '/chat/regenerate'
        });
      }

      function onComposerKeydown(event) {
        if (event.key !== 'Enter' || event.shiftKey) return;
        if (event.isComposing || event.keyCode === 229) return; // 中文输入法组合中
        event.preventDefault();
        if (!canSend.value) return;
        sendMessage();
      }

      /* ───────────────────── 角色编辑 / 新建 / 删除 ───────────────────── */
      function resetCharForm() {
        charForm.id = '';
        charForm.name = '';
        charForm.description = '';
        charForm.personality = '';
        charForm.scenario = '';
        charForm.first_mes = '';
        charForm.mes_example = '';
        charForm.system_prompt = '';
        charForm.post_history_instructions = '';
        charForm.alternate_greetings = '';
        charForm.tags = '';
        charForm.creator = '';
        charForm.character_version = '';
      }

      function openCreate() {
        resetCharForm();
        charFormError.value = '';
        avatarPreview.value = '';
        pendingAvatarBase64 = '';
        charModalOpen.value = true;
      }

      function openEdit(character) {
        if (!character) return;
        charForm.id = character.id || '';
        charForm.name = character.name || '';
        charForm.description = character.description || '';
        charForm.personality = character.personality || '';
        charForm.scenario = character.scenario || '';
        charForm.first_mes = character.first_mes || '';
        charForm.mes_example = character.mes_example || '';
        charForm.system_prompt = character.system_prompt || '';
        charForm.post_history_instructions = character.post_history_instructions || '';
        charForm.alternate_greetings = Array.isArray(character.alternate_greetings)
          ? character.alternate_greetings.join('\n') : '';
        charForm.tags = Array.isArray(character.tags) ? character.tags.join(', ') : '';
        charForm.creator = character.creator || '';
        charForm.character_version = character.character_version || '';
        charFormError.value = '';
        avatarPreview.value = '';
        pendingAvatarBase64 = '';
        charModalOpen.value = true;
      }

      function closeCharModal() {
        if (charSaving.value) return;
        charModalOpen.value = false;
        charFormError.value = '';
        avatarPreview.value = '';
        pendingAvatarBase64 = '';
      }

      function pickImport() {
        if (importLoading.value) return;
        if (importInputEl.value) importInputEl.value.click();
      }

      function pickAvatar() {
        if (avatarInputEl.value) avatarInputEl.value.click();
      }

      function clearAvatarPick() {
        avatarPreview.value = '';
        pendingAvatarBase64 = '';
        if (avatarInputEl.value) avatarInputEl.value.value = '';
      }

      async function onAvatarFile(event) {
        var input = event && event.target;
        var file = input && input.files ? input.files[0] : null;
        if (input) input.value = '';
        if (!file) return;
        var looksImage = /^image\//.test(file.type || '') || /\.(png|jpe?g|webp)$/i.test(file.name || '');
        if (!looksImage) {
          showToast('请选择 PNG / JPG / WebP 图片', '头像格式不支持', '⚠️', 'error');
          return;
        }
        if (file.size > 8 * 1024 * 1024) {
          showToast('头像文件不能超过 8MB', '图片过大', '⚠️', 'error');
          return;
        }
        try {
          var dataUrl = await readFileAsDataURL(file);
          avatarPreview.value = dataUrl;
          pendingAvatarBase64 = stripDataUrl(dataUrl);
        } catch (err) {
          showToast(err.message, '读取头像失败', '⚠️', 'error');
        }
      }

      function buildCharPayload(name) {
        return {
          name: name,
          description: charForm.description || '',
          personality: charForm.personality || '',
          scenario: charForm.scenario || '',
          first_mes: charForm.first_mes || '',
          mes_example: charForm.mes_example || '',
          system_prompt: charForm.system_prompt || '',
          post_history_instructions: charForm.post_history_instructions || '',
          alternate_greetings: splitLines(charForm.alternate_greetings),
          tags: splitTags(charForm.tags),
          creator: charForm.creator || '',
          character_version: charForm.character_version || ''
        };
      }

      async function saveCharacter() {
        if (charSaving.value) return;
        var name = String(charForm.name || '').trim();
        charFormError.value = '';
        if (!name) {
          charFormError.value = '角色名称不能为空';
          showToast('请先填写角色名称', '无法保存', '⚠️', 'error');
          return;
        }
        var editing = !!charForm.id;
        var editingId = charForm.id;
        charSaving.value = true;
        try {
          var payload = buildCharPayload(name);
          var savedId = editingId;
          if (editing) {
            var putData = await api(API + '/characters/' + encodeURIComponent(editingId), { method: 'PUT', body: payload });
            if (putData.character && putData.character.id) savedId = putData.character.id;
          } else {
            var postData = await api(API + '/characters', { method: 'POST', body: payload });
            if (postData.character && postData.character.id) savedId = postData.character.id;
          }

          var avatarError = null;
          if (pendingAvatarBase64 && savedId) {
            try {
              await api(API + '/characters/' + encodeURIComponent(savedId) + '/avatar', {
                method: 'POST',
                body: { data_base64: pendingAvatarBase64 }
              });
            } catch (err) {
              avatarError = err;
            }
          }

          charModalOpen.value = false;
          avatarPreview.value = '';
          pendingAvatarBase64 = '';
          if (avatarError) {
            showToast('角色已保存，但头像上传失败：' + avatarError.message, '头像上传失败', '⚠️', 'error');
          } else {
            showToast(editing ? '角色信息已更新' : '新角色已创建', '保存成功', '✅', 'success');
          }

          await loadCharacters();
          if (savedId && characters.value.some(function (c) { return c.id === savedId; })) {
            await selectCharacter(savedId);
          } else if (characters.value.length) {
            await selectCharacter(characters.value[0].id);
          }
        } catch (err) {
          charFormError.value = err.message;
          showToast(err.message, '保存角色失败', '⚠️', 'error');
        } finally {
          charSaving.value = false;
        }
      }

      async function deleteCharacter() {
        var ch = currentCharacter.value;
        if (!ch || deleting.value) return;
        if (streaming.value) stopStream();
        var ok = await askConfirm(
          '删除角色',
          '确定删除「' + ch.name + '」吗？该操作会同时删除聊天记录与头像，且不可恢复。'
        );
        if (!ok) return;
        deleting.value = true;
        try {
          await api(API + '/characters/' + encodeURIComponent(ch.id), { method: 'DELETE' });
          showToast('已删除「' + ch.name + '」', '删除成功', '✅', 'success');
          currentId.value = '';
          messages.value = [];
          await loadCharacters();
          if (characters.value.length) await selectCharacter(characters.value[0].id);
        } catch (err) {
          showToast(err.message, '删除角色失败', '⚠️', 'error');
        } finally {
          deleting.value = false;
        }
      }

      /* ───────────────────── 导入 / 从本书生成 ───────────────────── */
      async function onImportFile(event) {
        var input = event && event.target;
        var file = input && input.files ? input.files[0] : null;
        if (input) input.value = ''; // 允许再次选择同一个文件
        if (!file) return;
        var lowerName = String(file.name || '').toLowerCase();
        if (!(lowerName.endsWith('.png') || lowerName.endsWith('.json'))) {
          showToast('仅支持 SillyTavern PNG 或 JSON 角色卡', '文件格式不支持', '⚠️', 'error');
          return;
        }
        if (file.size > 8 * 1024 * 1024) {
          showToast('角色卡文件不能超过 8MB', '文件过大', '⚠️', 'error');
          return;
        }
        importLoading.value = true;
        try {
          var dataUrl = await readFileAsDataURL(file);
          var base64 = stripDataUrl(dataUrl);
          if (!base64) throw new Error('无法读取文件内容');
          var data = await api(API + '/characters/import', {
            method: 'POST',
            body: { filename: file.name, data_base64: base64 }
          });
          var imported = data.character || null;
          var formatText = data.format ? String(data.format).toUpperCase() : '';
          showToast(
            '已导入' + (imported && imported.name ? '「' + imported.name + '」' : '角色卡') + (formatText ? '（' + formatText + '）' : ''),
            '导入成功', '✅', 'success'
          );
          await loadCharacters();
          var targetId = imported && imported.id && characters.value.some(function (c) { return c.id === imported.id; })
            ? imported.id
            : (characters.value[0] ? characters.value[0].id : '');
          if (targetId) await selectCharacter(targetId);
        } catch (err) {
          showToast((err && err.message) || '导入失败，请检查角色卡文件', '导入失败', '⚠️', 'error');
        } finally {
          importLoading.value = false;
        }
      }

      async function fromNovel() {
        if (!novelId || fromNovelLoading.value) return;
        fromNovelLoading.value = true;
        try {
          var data = await api(API + '/characters/from-novel/' + encodeURIComponent(novelId), { method: 'POST' });
          var created = Number(data.created || 0);
          var skipped = Number(data.skipped || 0);
          showToast(
            '本次生成 ' + created + ' 个角色' + (skipped ? '，跳过 ' + skipped + ' 个已存在的同名角色' : ''),
            '从本书生成完成', '✅', 'success'
          );
          await loadCharacters();
          var first = Array.isArray(data.characters) ? data.characters[0] : null;
          var targetId = first && characters.value.some(function (c) { return c.id === first.id; })
            ? first.id
            : (characters.value[0] ? characters.value[0].id : '');
          if (targetId) await selectCharacter(targetId);
        } catch (err) {
          showToast(err.message, '从本书生成失败', '⚠️', 'error');
        } finally {
          fromNovelLoading.value = false;
        }
      }

      /* ───────────────────── 生命周期 ───────────────────── */
      onMounted(async function () {
        applyStoredTheme();
        await Promise.all([loadSettings(true), loadCharacters()]);
        if (!characters.value.length) return;
        const saved = NovelUI.storage.get('rp.currentCharacter');
        var target = characters.value.some(c => c.id===saved) ? saved : characters.value[0].id;
        await selectCharacter(target);
      });


      const filteredCharacters = computed(() => characters.value.filter(c => (c.name+' '+(c.tags || []).join(' ')).toLowerCase().includes(characterQuery.value.toLowerCase())));
      const visibleMessages = computed(() => messages.value.slice(-visibleCount.value));
      function toggleTheme() { theme.value = theme.value==='dark' ? 'light' : 'dark'; NovelUI.setTheme(theme.value); }
      function requestId(charId,text) {
        const key = charId + ':' + text;
        if (!pendingRequests.has(key)) pendingRequests.set(key, window.crypto?.randomUUID?.() || Date.now().toString(36)+Math.random().toString(36).slice(2));
        return pendingRequests.get(key);
      }
      function formatTime(ts) { const date=new Date((ts || 0)*1000); return ts && !isNaN(date) ? date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}) : ''; }
      function resizeComposer() { nextTick(() => { const el=document.querySelector('.rp-composer textarea'); if(el) { el.style.height='auto'; el.style.height=Math.min(170,el.scrollHeight)+'px'; } }); }
      function showEarlier() { const el=chatEl.value, height=el?.scrollHeight || 0; visibleCount.value+=150; nextTick(() => { if(el) el.scrollTop+=el.scrollHeight-height; }); }
      async function copyMessage(message) { try { await NovelUI.copy(message.content); showToast('文字已复制','已复制','✓','success'); } catch(error) { showToast(error.message,'复制失败','!','error'); } }
      function exportChat() { NovelUI.download(currentCharacter.value.name+'-对话.txt',messages.value.map(m=>(m.role==='user'?userDisplayName.value:charDisplayName.value)+' · '+formatTime(m.ts)+'\n'+NovelUI.textContent(m.content)).join('\n\n')); }
      function exportCharacter() { const card={...currentCharacter.value}; ['id','created_at','updated_at','avatar_url','source','novel_id'].forEach(key=>delete card[key]); NovelUI.download(card.name+'.json',JSON.stringify({spec:'chara_card_v2',spec_version:'2.0',data:card},null,2),'application/json'); }
      function editMessage(message) { if(streaming.value) return; editingMessage.value=message; editedContent.value=NovelUI.textContent(message.content); nextTick(()=>document.querySelector('.rp-edit-message')?.focus()); }
      async function saveEditedMessage() {
        if(!editingMessage.value || editSaving.value) return;
        editSaving.value=true;
        try { const data=await api(API+'/characters/'+currentId.value+'/chat/messages/'+editingMessage.value.id,{method:'PATCH',body:{content:editedContent.value}}); Object.assign(editingMessage.value,data.message); editingMessage.value=null; }
        catch(error) { showToast(error.message,'保存失败','!','error'); }
        finally { editSaving.value=false; }
      }
      async function restoreChat() {
        if(streaming.value || !currentId.value) return;
        try { const data=await api(API+'/characters/'+currentId.value+'/chat/restore',{method:'POST'}); messages.value=data.messages; scrollToBottom(); showToast('已恢复上次记录；再次点击可切回','已恢复','✓','success'); }
        catch(error) { showToast(error.message,'无法恢复','!','error'); }
      }
      let draftTimer;
      Vue.watch(draft, value => { clearTimeout(draftTimer); const id=currentId.value; draftTimer=setTimeout(()=>{if(id) NovelUI.storage.set('rp.draft.'+id,value);},250); });
      window.addEventListener('beforeunload',()=>{if(currentId.value) NovelUI.storage.set('rp.draft.'+currentId.value,draft.value); if(abortCtrl) abortCtrl.abort();});
      document.addEventListener('keydown',event=>{ if(event.key!=='Escape') return; if(editingMessage.value) editingMessage.value=null; else if(charModalOpen.value) closeCharModal(); else if(settingsOpen.value) closeSettings(); });

      /* ───────────────────── 暴露给模板 ───────────────────── */
      return { connectionOpen,theme,toggleTheme,characterQuery,filteredCharacters,visibleMessages,visibleCount,showEarlier,
        renderMessage:NovelUI.renderMessage,formatTime,resizeComposer,copyMessage,exportChat,exportCharacter,editingMessage,editedContent,editSaving,editMessage,saveEditedMessage,restoreChat,
        // 常量
        novelId: novelId,
        // 状态
        characters: characters,
        charactersLoading: charactersLoading,
        charactersError: charactersError,
        currentId: currentId,
        currentCharacter: currentCharacter,
        messages: messages,
        chatLoading: chatLoading,
        chatError: chatError,
        streaming: streaming,
        draft: draft,
        deleting: deleting,
        clearing: clearing,
        importLoading: importLoading,
        fromNovelLoading: fromNovelLoading,
        autoScroll: autoScroll,
        avatarFailed: avatarFailed,
        settings: settings,
        settingsForm: settingsForm,
        settingsOpen: settingsOpen,
        settingsLoading: settingsLoading,
        settingsSaving: settingsSaving,
        charModalOpen: charModalOpen,
        charForm: charForm,
        charFormError: charFormError,
        charSaving: charSaving,
        avatarPreview: avatarPreview,
        toast: toast,
        confirmBox: confirmBox,
        // 模板 refs
        chatEl: chatEl,
        importInputEl: importInputEl,
        avatarInputEl: avatarInputEl,
        // 计算属性
        canSend: canSend,
        userDisplayName: userDisplayName,
        charDisplayName: charDisplayName,
        userInitial: userInitial,
        composerPlaceholder: composerPlaceholder,
        charModalTitle: charModalTitle,
        modalInitial: modalInitial,
        modalAvatarSrc: modalAvatarSrc,
        greetingOptions: greetingOptions,
        // 展示辅助
        sourceLabel: sourceLabel,
        charInitial: charInitial,
        onAvatarError: onAvatarError,
        // 设置
        openSettings: openSettings,
        closeSettings: closeSettings,
        saveSettings: saveSettings,
        // 角色
        loadCharacters: loadCharacters,
        reloadCharacters: reloadCharacters,
        selectCharacter: selectCharacter,
        onCharacterClick: onCharacterClick,
        onTopSelect: onTopSelect,
        openCreate: openCreate,
        openEdit: openEdit,
        closeCharModal: closeCharModal,
        saveCharacter: saveCharacter,
        deleteCharacter: deleteCharacter,
        pickImport: pickImport,
        onImportFile: onImportFile,
        fromNovel: fromNovel,
        pickAvatar: pickAvatar,
        onAvatarFile: onAvatarFile,
        clearAvatarPick: clearAvatarPick,
        // 聊天
        startChat: startChat,
        retryChat: retryChat,
        sendMessage: sendMessage,
        stopStream: stopStream,
        regenerate: regenerate,
        clearChat: clearChat,
        onGreetingSelect: onGreetingSelect,
        scrollToBottom: scrollToBottom,
        onChatScroll: onChatScroll,
        onComposerKeydown: onComposerKeydown,
        // 提示
        showToast: showToast,
        resolveConfirm: resolveConfirm
      };
    }
  });

  try {
    NovelUI.install(app);
    app.mount('#app');
  } catch (err) {
    showBootError((err && err.message) || String(err));
  }
})();
