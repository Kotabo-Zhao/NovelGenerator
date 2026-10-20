(function () {
  'use strict';

  var bootErrorEl = document.getElementById('boot-error');
  function showBootError(message) {
    if (!bootErrorEl) return;
    bootErrorEl.style.display = 'block';
    bootErrorEl.textContent = '页面初始化失败：' + message;
  }
  if (typeof Vue === 'undefined') {
    showBootError('Vue 未加载（/vue.global.prod.js），请刷新重试。');
    return;
  }
  window.addEventListener('error', function (event) {
    showBootError((event && event.message) || '未知脚本错误');
  });

  var createApp = Vue.createApp;
  var ref = Vue.ref;
  var reactive = Vue.reactive;
  var computed = Vue.computed;
  var onMounted = Vue.onMounted;
  var onUnmounted = Vue.onUnmounted;
  var nextTick = Vue.nextTick;
  var watch = Vue.watch;

  /* ═══════════════════════ 常量 ═══════════════════════ */

  var API_BASE = '/api/roleplay';

  // 斜杠指令（契约 §1）。arg=true 表示需要参数，选中后先填入指令前缀。
  var COMMANDS = [
    { name: '/help', aliases: ['/h'], desc: '查看全部可用指令与用法', usage: '/help 或 /h', arg: false },
    { name: '/say', aliases: ['/s'], desc: '以普通对话发送，并让角色回复', usage: '/say 文本', arg: true },
    { name: '/act', aliases: ['/do', '/action'], desc: '描述一个动作，生成动作结果与角色反应', usage: '/act 动作描述', arg: true },
    { name: '/nar', aliases: ['/narrate', '/sys'], desc: '追加一条旁白（不调用模型）', usage: '/nar 旁白内容', arg: true },
    { name: '/ooc', aliases: ['/out'], desc: '出戏发言，并让角色回应', usage: '/ooc 内容', arg: true },
    { name: '/roll', aliases: ['/dice', '/r'], desc: '掷骰并记录结果，如 1d20+3、2d6', usage: '/roll 1d20+3', arg: true },
    { name: '/continue', aliases: ['/cont'], desc: '让角色继续上一条回复（不新增用户消息）', usage: '/continue', arg: false },
    { name: '/regen', aliases: [], desc: '重新生成最后一条回复（新增一个版本）', usage: '/regen', arg: false },
    { name: '/swipe', aliases: [], desc: '切换已有版本：next / prev，或 new 生成新版本', usage: '/swipe next', arg: true },
    { name: '/impersonate', aliases: ['/imp'], desc: '让模型替你写一句台词，填入输入框（不发送）', usage: '/impersonate', arg: false },
    { name: '/note', aliases: [], desc: '设置作者注释（author\u2019s note）', usage: '/note 文本', arg: true },
    { name: '/world', aliases: [], desc: '查看当前世界书条目数与启用数', usage: '/world', arg: false }
  ];

  var COMMAND_BY_ALIAS = {};
  COMMANDS.forEach(function (cmd) {
    COMMAND_BY_ALIAS[cmd.name] = cmd;
    cmd.aliases.forEach(function (alias) { COMMAND_BY_ALIAS[alias] = cmd; });
  });

  var COMMAND_ICONS = {
    help: '💡', say: '💬', act: '🎭', nar: '✨', ooc: '💭', roll: '🎲',
    continue: '⏩', regen: '🔄', swipe: '🔀', impersonate: '🎤', note: '📝', world: '📚'
  };

  var MODES = [
    { key: 'dialogue', label: '对话', icon: '💬' },
    { key: 'action', label: '动作', icon: '🎭' },
    { key: 'narration', label: '旁白', icon: '✨' },
    { key: 'ooc', label: 'OOC', icon: '💭' }
  ];

  var MODE_PREFIX = { dialogue: '', action: '/act', narration: '/nar', ooc: '/ooc' };
  var MODE_HINTS = {
    dialogue: 'Enter 发送',
    action: '将以 /act 发送',
    narration: '将以 /nar 发送',
    ooc: '将以 /ooc 发送'
  };

  var COMMAND_TARGETS = { say: true, act: true, ooc: true, continue: true };

  var CHAR_TABS = [
    { key: 'basic', label: '基础' },
    { key: 'persona', label: '设定' },
    { key: 'greeting', label: '开场' },
    { key: 'advanced', label: '高级' }
  ];

  var SOURCE_LABELS = {
    png: 'PNG 角色卡',
    json: 'JSON 角色卡',
    manual: '手动创建',
    novel: '来自本书',
    builtin: '内置角色'
  };

  var THEME_COLORS = { dark: '#0b0912', light: '#f4f2fb' };

  /* ═══════════════════════ 工具函数 ═══════════════════════ */

  function nowTs() { return Math.floor(Date.now() / 1000); }

  function fmtTime(ts) {
    var n = Number(ts);
    if (!isFinite(n) || n <= 0) return '';
    var date = new Date(n > 1e12 ? n : n * 1000);
    if (isNaN(date.getTime())) return '';
    function pad(value) { return String(value).padStart(2, '0'); }
    var hm = pad(date.getHours()) + ':' + pad(date.getMinutes());
    var now = new Date();
    return date.toDateString() === now.toDateString() ? hm
      : pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' + hm;
  }

  function clampNumber(value, min, max, fallback) {
    var n = Number(value);
    if (!isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, n));
  }

  function splitLines(text) {
    return String(text || '').split(/\r?\n/).map(function (line) { return line.trim(); }).filter(Boolean);
  }

  function splitTags(text) {
    return String(text || '').split(/[,，]/).map(function (tag) { return tag.trim(); }).filter(Boolean);
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

  function safeStorageGet(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }

  function safeStorageSet(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* 隐私模式忽略 */ }
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function applyInline(escaped) {
    return escaped
      .replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
  }

  function renderMarkdown(raw) {
    if (window.NovelCardUI) return NovelCardUI.render(raw);
    var text = String(raw == null ? '' : raw).replace(/\r\n?/g, '\n');
    var lines = text.split('\n');
    var out = [];
    for (var i = 0; i < lines.length; i++) {
      var parts = lines[i].split('`');
      var html = '';
      for (var j = 0; j < parts.length; j++) {
        if (j % 2 === 1) html += '<code>' + escapeHtml(parts[j]) + '</code>';
        else html += applyInline(escapeHtml(parts[j]));
      }
      out.push(html);
    }
    return out.join('<br>');
  }

  /* ═══════════════════════ 主题 ═══════════════════════ */

  function readStoredTheme() {
    return safeStorageGet('theme') === 'light' ? 'light' : 'dark';
  }

  function applyThemeValue(value) {
    var themeName = value === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', themeName);
    var meta = document.getElementById('rp-theme-color');
    if (meta) meta.setAttribute('content', THEME_COLORS[themeName]);
    safeStorageSet('theme', themeName);
  }

  var initialTheme = readStoredTheme();
  applyThemeValue(initialTheme);

  /* 当前小说 id：仅在带 ?novel=xxx 时显示「从本书生成」 */
  var novelId = '';
  try {
    novelId = new URLSearchParams(window.location.search).get('novel') || '';
  } catch (e) { novelId = ''; }

  /* ═══════════════════════ API ═══════════════════════ */

  function getAccessToken() {
    var keys = ['access_token', 'owner_token', 'session_token'];
    for (var i = 0; i < keys.length; i++) {
      var value = safeStorageGet(keys[i]) || safeStorageGet('rp_' + keys[i]);
      if (value) return String(value).replace(/^Bearer\s+/i, '').trim();
    }
    return '';
  }

  function authHeaders(base) {
    var headers = Object.assign({}, base || {});
    var token = getAccessToken();
    if (token) headers.Authorization = 'Bearer ' + token;
    return headers;
  }

  function apiJson(path, options) {
    return NovelUI.requestJson(path, Object.assign({}, options || {}, {headers: authHeaders()}));
  }

  function charPath(id, suffix) {
    return API_BASE + '/characters/' + encodeURIComponent(id) + (suffix || '');
  }

  function apiChar(id, suffix, options) {
    return apiJson(charPath(id, suffix), options);
  }

  /* SSE 读取器：按空行分帧，处理 data: 行，忽略注释与 ping。onEvent 返回 false 提前结束。 */
  var readSSE = NovelUI.readSSE;

  /* ═══════════════════════ Vue 应用 ═══════════════════════ */

  var app = createApp({
    setup: function () {
      /* ───────────── 基础状态 ───────────── */
      var characters = ref([]);
      var charactersLoading = ref(true);
      var charactersError = ref('');
      var charSearch = ref('');
      var currentId = ref('');
      var messages = ref([]);
      var visibleCount = ref(150);
      var visibleMessages = computed(function () {
        var start = Math.max(0, messages.value.length-visibleCount.value);
        return messages.value.slice(start).map(function (message,index) {return {message:message,index:start+index};});
      });
      var chatLoading = ref(false);
      var chatError = ref('');
      var streaming = ref(false);
      var draft = ref('');
      var theme = ref(initialTheme);

      var viewportW = ref(window.innerWidth);
      var autoScroll = ref(true);
      var showJump = computed(function () { return !autoScroll.value; });

      var importLoading = ref(false);
      var deleting = ref(false);
      var clearing = ref(false);
      var fromNovelOpen = ref(false);
      var fromNovelMode = ref('ai');
      var fromNovelLoading = ref(false);

      var panelTab = ref('world');
      var panelOpen = ref(false);
      var libraryOpen = ref(false);

      var chatEl = ref(null);
      var draftEl = ref(null);
      var importInputEl = ref(null);
      var avatarInputEl = ref(null);
      var worldInputEl = ref(null);

      var avatarFailed = reactive({});
      var recentIds = ref(readRecentIds());

      var metaUser = ref('');
      var metaCharacter = ref('');
      var modelInfo = reactive({ model: '', providerName: '', known: false });
      var novelTitle = ref('');

      var abortCtrl = null;
      var streamSeq = 0;
      var lastWideLayout = window.innerWidth >= 1200;

      /* ───────────── 设置 ───────────── */
      var settings = reactive(buildDefaultSettings());
      var settingsForm = reactive(buildDefaultSettings());
      var settingsOpen = ref(false);
      var settingsLoading = ref(false);
      var settingsSaving = ref(false);
      var identity = reactive({user_name:'',user_persona:'',player_pronouns:'auto',narration_pov:'card',role_mode:'auto',source_character:''});
      var identityForm = reactive({});
      var identityOpen = ref(false), identitySaving = ref(false), identityLoading = ref(false);
      var pendingIdentityChat = ref(false);
      var identityChoices = ref([]), compatibility = reactive({mode:'character',features:[],creator_notes:'',group_fields:''});
      var identityError = ref('');
      var cardRepair = ref(null);

      /* ───────────── 角色编辑 ───────────── */
      var charModalOpen = ref(false);
      var charSaving = ref(false);
      var charFormError = ref('');
      var charTab = ref('basic');
      var avatarPreview = ref('');
      var pendingAvatarBase64 = '';
      var charForm = reactive(buildDefaultCharForm());

      /* ───────────── 世界书 ───────────── */
      var worldEntries = ref([]);
      var worldLoading = ref(false);
      var worldError = ref('');
      var worldImporting = ref(false);
      var expandedWorld = reactive({});

      /* ───────────── 输入 / 指令 ───────────── */
      var activeMode = ref('dialogue');
      var paletteDismissed = ref(false);
      var paletteIndex = ref(0);

      /* ───────────── 提示 / 确认 / 指令结果 ───────────── */
      var toasts = ref([]);
      var toastSeq = 0;
      var confirmBox = reactive({
        visible: false, title: '', message: '',
        confirmText: '', cancelText: '', resolve: null
      });
      var commandResult = reactive({ visible: false, text: '', icon: '✨', command: '' });
      var commandTimer = 0;
      var swipeBusy = ref(false);

      /* ═════════════ 默认值 / 设置序列化 ═════════════ */

      function buildDefaultSettings() {
        return {
          user_name: '旅行者',
          user_persona: '',
          system_prompt: '',
          authors_note: '',
          authors_note_depth: 4,
          temperature: 0.85,
          max_tokens: 600,
          context_messages: 40,
          lorebook_enabled: true,
          lorebook_budget: 5,
          lorebook_scan_depth: 6
        };
      }

      function buildDefaultCharForm() {
        return {
          id: '', name: '', description: '', personality: '', scenario: '',
          first_mes: '', mes_example: '', system_prompt: '',
          post_history_instructions: '', alternate_greetings: [], creator_notes: '',
          tags: '', creator: '', character_version: ''
        };
      }

      function assignSettings(target, payload) {
        if (!payload || typeof payload !== 'object') return target;
        var p = payload.settings && typeof payload.settings === 'object' ? payload.settings : payload;
        if (typeof p.user_name === 'string') target.user_name = p.user_name;
        if (typeof p.user_persona === 'string') target.user_persona = p.user_persona;
        if (typeof p.system_prompt === 'string') target.system_prompt = p.system_prompt;
        if (typeof p.authors_note === 'string') target.authors_note = p.authors_note;
        target.authors_note_depth = Math.round(clampNumber(p.authors_note_depth, 0, 20, target.authors_note_depth));
        target.temperature = clampNumber(p.temperature, 0, 2, target.temperature);
        target.max_tokens = Math.round(clampNumber(p.max_tokens, 64, 4096, target.max_tokens));
        target.context_messages = Math.round(clampNumber(p.context_messages, 4, 200, target.context_messages));
        if (typeof p.lorebook_enabled === 'boolean') target.lorebook_enabled = p.lorebook_enabled;
        target.lorebook_budget = Math.round(clampNumber(p.lorebook_budget, 1, 20, target.lorebook_budget));
        target.lorebook_scan_depth = Math.round(clampNumber(p.lorebook_scan_depth, 1, 30, target.lorebook_scan_depth));
        return target;
      }

      function buildSettingsPayload(source) {
        var clampedTemperature = Math.round(clampNumber(source.temperature, 0, 2, 0.85) * 100) / 100;
        return {
          user_name: String(source.user_name || '').trim() || '旅行者',
          user_persona: String(source.user_persona || ''),
          system_prompt: String(source.system_prompt || ''),
          authors_note: String(source.authors_note || ''),
          authors_note_depth: Math.round(clampNumber(source.authors_note_depth, 0, 20, 4)),
          temperature: clampedTemperature,
          max_tokens: Math.round(clampNumber(source.max_tokens, 64, 4096, 600)),
          context_messages: Math.round(clampNumber(source.context_messages, 4, 200, 40)),
          lorebook_enabled: source.lorebook_enabled !== false,
          lorebook_budget: Math.round(clampNumber(source.lorebook_budget, 1, 20, 5)),
          lorebook_scan_depth: Math.round(clampNumber(source.lorebook_scan_depth, 1, 30, 6))
        };
      }

      function copySettingsToForm() {
        assignSettings(settingsForm, settings);
      }

      /* ═════════════ 提示 / 确认 ═════════════ */

      function dismissToast(id) {
        toasts.value = toasts.value.filter(function (item) { return item.id !== id; });
      }

      function pushToast(kind, title, message, icon) {
        var id = ++toastSeq;
        toasts.value.push({
          id: id,
          kind: kind || 'info',
          title: title || '提示',
          message: message == null ? '' : String(message),
          icon: icon || (kind === 'error' ? '⚠️' : kind === 'success' ? '✅' : 'ℹ️')
        });
        if (toasts.value.length > 3) toasts.value.shift();
        setTimeout(function () { dismissToast(id); }, kind === 'error' ? 7000 : 4200);
      }

      function toastSuccess(message, title, icon) { pushToast('success', title || '成功', message, icon); }
      function toastError(message, title, icon) { pushToast('error', title || '出错', message, icon); }
      function toastInfo(message, title, icon) { pushToast('info', title || '提示', message, icon); }

      function askConfirm(title, message, options) {
        options = options || {};
        return new Promise(function (resolve) {
          confirmBox.title = title;
          confirmBox.message = message;
          confirmBox.confirmText = options.confirmText || '确定';
          confirmBox.cancelText = options.cancelText || '取消';
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

      function handleCommandResult(event) {
        var text = String(event && event.text != null ? event.text : '').trim();
        if (!text) return;
        var command = String((event && event.command) || '').replace(/^\//, '').toLowerCase();
        commandResult.text = text;
        commandResult.command = command;
        var commandResultIcon = event && event.level === 'error' ? '⚠️' : (COMMAND_ICONS[command] || '✨');
        commandResult.icon = commandResultIcon;
        commandResult.visible = true;
        if (commandTimer) clearTimeout(commandTimer);
        commandTimer = setTimeout(function () { commandResult.visible = false; }, 14000);
      }

      /* ═════════════ 主题 ═════════════ */

      function toggleTheme() {
        theme.value = theme.value === 'dark' ? 'light' : 'dark';
      }

      function setTheme(value) {
        theme.value = value === 'light' ? 'light' : 'dark';
      }

      watch(theme, function (value) { applyThemeValue(value); });

      /* ═════════════ 计算属性：角色 / 顶栏 ═════════════ */

      var currentCharacter = computed(function () {
        var id = currentId.value;
        if (!id) return null;
        for (var i = 0; i < characters.value.length; i++) {
          if (characters.value[i].id === id) return characters.value[i];
        }
        return null;
      });

      var searchActive = computed(function () { return String(charSearch.value || '').trim().length > 0; });

      var filteredCharacters = computed(function () {
        var query = String(charSearch.value || '').trim().toLowerCase();
        if (!query) return characters.value;
        return characters.value.filter(function (c) {
          if (String(c.name || '').toLowerCase().indexOf(query) >= 0) return true;
          if (String(c.description || '').toLowerCase().indexOf(query) >= 0) return true;
          if (String(c.personality || '').toLowerCase().indexOf(query) >= 0) return true;
          var tags = characterTagsArr(c);
          for (var i = 0; i < tags.length; i++) {
            if (String(tags[i]).toLowerCase().indexOf(query) >= 0) return true;
          }
          return false;
        });
      });

      var recentCharacters = computed(function () {
        var map = {};
        characters.value.forEach(function (c) { map[c.id] = c; });
        var list = [];
        recentIds.value.forEach(function (id) {
          if (map[id]) list.push(map[id]);
        });
        return list;
      });

      var sidebarGroups = computed(function () {
        var list = filteredCharacters.value;
        if (searchActive.value) {
          var resultGroup = { key: 'search', label: '搜索结果', items: list };
          return resultGroup.items.length ? [resultGroup] : [];
        }
        var groups = [];
        var recent = recentCharacters.value;
        var recentMap = {};
        recent.forEach(function (c) { recentMap[c.id] = true; });
        if (recent.length) groups.push({ key: 'recent', label: '最近使用', items: recent });
        var others = list.filter(function (c) { return !recentMap[c.id]; });
        groups.push({ key: 'all', label: recent.length ? '其他角色' : '全部角色', items: others });
        return groups.filter(function (group) { return group.items.length > 0; });
      });

      var userDisplayName = computed(function () {
        return metaUser.value || identity.user_name || settings.user_name || '旅行者';
      });

      var charDisplayName = computed(function () {
        if (metaCharacter.value) return metaCharacter.value;
        return currentCharacter.value ? currentCharacter.value.name : '角色';
      });

      var userInitial = computed(function () {
        var name = String(userDisplayName.value || '你').trim();
        return name ? name.charAt(0) : '你';
      });

      var topTitle = computed(function () {
        return '角色扮演';
      });

      var topSub = computed(function () {
        if (novelTitle.value) return '《' + novelTitle.value + '》';
        if (novelId) return '本书角色 · 沉浸式对话';
        return 'SillyTavern 风格沉浸式对话';
      });

      var modelLabel = computed(function () {
        if (!modelInfo.known) return '模型未连接';
        return shorten(modelInfo.model, 22);
      });

      var modelTitle = computed(function () {
        if (!modelInfo.known) return '未读取到当前模型信息';
        return (modelInfo.providerName ? modelInfo.providerName + ' · ' : '') + modelInfo.model;
      });

      var modelKnown = computed(function () { return !!modelInfo.known; });

      var isPanelOverlay = computed(function () { return true; });

      /* ═════════════ 计算属性：输入 ═════════════ */

      var canSend = computed(function () {
        return !!currentCharacter.value && !chatLoading.value && !streaming.value && String(draft.value || '').trim().length > 0;
      });

      var composerPlaceholder = computed(function () {
        var name = currentCharacter.value ? currentCharacter.value.name : '角色';
        if (!currentCharacter.value) return '请先选择或创建一个角色…';
        if (activeMode.value === 'action') return '描述你的动作，例如：推门进去，同时拔剑，对' + name + '说：别动';
        if (activeMode.value === 'narration') return '以旁白补充场景，例如：窗外的雨忽然停了，铜铃轻轻响了一声';
        if (activeMode.value === 'ooc') return '出戏说明，例如：我想让剧情节奏慢一点，多写一点环境';
        if (compatibility.mode === 'narrator') return '以「' + userDisplayName.value + '」的身份描述行动或台词…';
        return '以「' + userDisplayName.value + '」的身份，与 ' + name + ' 对话…';
      });

      var modeHint = computed(function () {
        return MODE_HINTS[activeMode.value] || 'Enter 发送';
      });

      var temperatureLabel = computed(function () {
        return clampNumber(settingsForm.temperature, 0, 2, 0.85).toFixed(2);
      });

      function setMode(key) {
        for (var i = 0; i < MODES.length; i++) {
          if (MODES[i].key === key) { activeMode.value = key; break; }
        }
        focusDraft();
      }

      var paletteQuery = computed(function () {
        var value = String(draft.value || '');
        if (value.charAt(0) !== '/') return null;
        if (/\s/.test(value)) return null;
        return value.slice(1).toLowerCase();
      });

      var paletteCommands = computed(function () {
        var query = paletteQuery.value;
        if (query === null) return [];
        if (!query) return COMMANDS;
        return COMMANDS.filter(function (cmd) {
          if (cmd.name.slice(1).toLowerCase().indexOf(query) === 0) return true;
          for (var i = 0; i < cmd.aliases.length; i++) {
            if (cmd.aliases[i].slice(1).toLowerCase().indexOf(query) === 0) return true;
          }
          return cmd.desc.indexOf(query) >= 0;
        });
      });

      var paletteOpen = computed(function () {
        return paletteQuery.value !== null && !paletteDismissed.value && !streaming.value;
      });

      watch(paletteQuery, function () {
        paletteDismissed.value = false;
        paletteIndex.value = 0;
      });

      watch(draft, function () { autoGrow(); });

      function applyCommand(cmd) {
        if (!cmd) return;
        paletteDismissed.value = true;
        if (cmd.arg) {
          draft.value = cmd.name + ' ';
          nextTick(function () { autoGrow(); focusDraft(); });
          return;
        }
        draft.value = '';
        sendComposer(cmd.name);
      }

      function onComposerKeydown(event) {
        if (event.key === 'Escape' && paletteOpen.value) {
          event.preventDefault();
          paletteDismissed.value = true;
          return;
        }
        if (paletteOpen.value) {
          var list = paletteCommands.value;
          if (event.key === 'ArrowDown' && list.length) {
            event.preventDefault();
            paletteIndex.value = (paletteIndex.value + 1) % list.length;
            return;
          }
          if (event.key === 'ArrowUp' && list.length) {
            event.preventDefault();
            paletteIndex.value = (paletteIndex.value - 1 + list.length) % list.length;
            return;
          }
          if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
            event.preventDefault();
            if (list[paletteIndex.value]) applyCommand(list[paletteIndex.value]);
            return;
          }
        }
        if (event.key !== 'Enter' || event.shiftKey) return;
        if (event.isComposing || event.keyCode === 229) return;
        event.preventDefault();
        if (!canSend.value) return;
        sendComposer();
      }

      function autoGrow() {
        nextTick(function () {
          var el = draftEl.value;
          if (!el) return;
          var max = window.innerWidth < 600 ? 132 : 176;
          el.style.height = 'auto';
          var nextHeight = Math.min(el.scrollHeight, max);
          el.style.height = nextHeight + 'px';
          el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden';
        });
      }

      function focusDraft() {
        nextTick(function () {
          if (draftEl.value) {
            try { draftEl.value.focus({ preventScroll: true }); } catch (e) { draftEl.value.focus(); }
          }
        });
      }

      /* ═════════════ 角色列表 ═════════════ */

      function readRecentIds() {
        try {
          var parsed = JSON.parse(safeStorageGet('rp_recent_characters') || '[]');
          if (!Array.isArray(parsed)) return [];
          return parsed.filter(function (id) { return typeof id === 'string' && id; }).slice(0, 8);
        } catch (e) { return []; }
      }

      function rememberCharacter(id) {
        if (!id) return;
        var next = recentIds.value.filter(function (item) { return item !== id; });
        next.unshift(id);
        recentIds.value = next.slice(0, 8);
        safeStorageSet('rp_recent_characters', JSON.stringify(recentIds.value));
      }

      function characterTagsArr(character) {
        if (!character) return [];
        var tags = character.tags;
        if (Array.isArray(tags)) return tags.map(function (tag) { return String(tag).trim(); }).filter(Boolean);
        if (typeof tags === 'string') return splitTags(tags);
        return [];
      }

      function characterTags(character) {
        return characterTagsArr(character).slice(0, 3).join(' / ');
      }

      function charSubline(character) {
        if (!character) return '';
        var parts = [sourceLabel(character.source)];
        var tags = characterTagsArr(character);
        if (tags.length) parts.push(tags.slice(0, 2).join(' / '));
        else if (character.description) parts.push(shorten(character.description.replace(/\{\{char\}\}/gi, character.name).replace(/\{\{user\}\}/gi, '玩家'), 16));
        return parts.join(' · ');
      }

      function sourceLabel(source) {
        return SOURCE_LABELS[source] || '角色';
      }

      function charInitial(character) {
        var name = character && character.name ? String(character.name).trim() : '';
        return name ? name.charAt(0) : '?';
      }

      function onAvatarError(id) {
        if (!id) return;
        avatarFailed[id] = true;
      }

      async function loadCharacters() {
        charactersLoading.value = true;
        charactersError.value = '';
        try {
          var data = await apiJson(API_BASE + '/characters' + (novelId ? '?novel_id=' + encodeURIComponent(novelId) : ''));
          characters.value = Array.isArray(data.characters) ? data.characters : [];
          if (currentId.value && !characters.value.some(function (c) { return c.id === currentId.value; })) {
            currentId.value = '';
          }
        } catch (err) {
          characters.value = [];
          charactersError.value = err.message;
          toastError(err.message, '加载角色失败');
        } finally {
          charactersLoading.value = false;
        }
      }

      async function reloadCharacters() {
        await loadCharacters();
        if (!characters.value.length) return;
        await selectCharacter(currentId.value || characters.value[0].id);
      }

      function findCharacter(id) {
        for (var i = 0; i < characters.value.length; i++) {
          if (characters.value[i].id === id) return characters.value[i];
        }
        return null;
      }

      async function openChat(id) {
        var seq = ++chatSeq;
        chatLoading.value = true;
        chatError.value = '';
        messages.value = [];
        autoScroll.value = true;
        try {
          var data = await apiChar(id, '/chat');
          if (seq !== chatSeq) return;
          if (data.identity) Object.assign(identity, data.identity);
          var list = Array.isArray(data.messages) ? data.messages : [];
          if (!list.length && !data.identity_bound) {
            messages.value = []; Object.assign(identityForm, identity);
            pendingIdentityChat.value = true; identityOpen.value = true;
            return;
          }
          if (!list.length) {
            data = await apiChar(id, '/chat/start', { method: 'POST' });
            if (seq !== chatSeq) return;
            list = Array.isArray(data.messages) ? data.messages : [];
          }
          messages.value = list.map(normalizeMessage);
          scrollToBottom();
        } catch (err) {
          if (seq === chatSeq) chatError.value = err.message;
          toastError(err.message, '对话加载失败');
        } finally {
          if (seq === chatSeq) chatLoading.value = false;
        }
      }

      async function selectCharacter(id) {
        var found = findCharacter(id);
        if (!found) return;
        if (streaming.value) stopStream();
        if (currentId.value !== id) {
          draft.value = '';
          activeMode.value = 'dialogue';
        }
        currentId.value = id;
        messages.value = [];
        chatLoading.value = true;
        var routeParams = new URLSearchParams(location.search);
        routeParams.set('character', id);
        history.replaceState(null, '', '/roleplay.html?' + routeParams);
        metaUser.value = '';
        metaCharacter.value = '';
        identity.user_name = '';
        identityOpen.value = false;
        pendingIdentityChat.value = false;
        identityLoading.value = false;
        identityError.value = '';
        identityChoices.value = [];
        rememberCharacter(id);
        safeStorageSet('rp_last_character' + (novelId ? ':'+novelId : ''), id);
        libraryOpen.value = false;
        resetWorldExpanded();
        worldEntries.value = [];
        worldError.value = '';
        await loadIdentity(id);
        if (currentId.value !== id) return;
        await openChat(id);
        loadWorldInfo(true);
      }

      function onCharacterClick(id) {
        if (!id) return;
        if (id === currentId.value && messages.value.length && !chatError.value) return;
        selectCharacter(id);
      }

      function selectFromLibrary(character) {
        if (!character) return;
        libraryOpen.value = false;
        selectCharacter(character.id);
      }

      function openLibrary() { libraryOpen.value = true; }
      function closeLibrary() { libraryOpen.value = false; }

      /* ═════════════ 消息规范化与渲染辅助 ═════════════ */

      function normalizeKind(kind, role) {
        if (kind === 'dialogue' || kind === 'action' || kind === 'narration' ||
            kind === 'ooc' || kind === 'roll' || kind === 'system') return kind;
        if (!kind) return role === 'system' ? 'system' : 'dialogue';
        return 'dialogue';
      }

      function normalizeMessage(raw) {
        var source = raw && typeof raw === 'object' ? raw : {};
        var role = source.role === 'user' || source.role === 'system' ? source.role : 'assistant';
        var variants = Array.isArray(source.variants)
          ? source.variants.map(function (item) { return NovelUI.textContent(item); })
          : [];
        var index = Number(source.variant_index);
        if (!isFinite(index)) index = 0;
        index = variants.length ? Math.min(Math.max(0, Math.round(index)), variants.length - 1) : 0;
        return {
          id: source.id ? String(source.id) : '',
          role: role,
          kind: normalizeKind(source.kind, role),
          speaker: source.speaker ? String(source.speaker) : '',
          content: NovelUI.textContent(source.content),
          display_content: typeof source.display_content === 'string' ? source.display_content : null,
          ts: source.ts,
          variants: variants,
          variant_index: index,
          _streaming: false
        };
      }

      function applyMessages(list) {
        messages.value = (Array.isArray(list) ? list : []).map(normalizeMessage);
      }

      function messageKind(message) {
        return normalizeKind(message && message.kind, message && message.role);
      }

      function messageClass(message) {
        var kind = messageKind(message);
        var role = message && message.role === 'user' ? 'user' : message && message.role === 'system' ? 'system' : 'assistant';
        var classes = ['rp-msg', 'kind-' + kind, 'role-' + role];
        if (kind === 'dialogue') classes.push('is-dialogue');
        if (message && message._streaming) classes.push('is-streaming');
        return classes;
      }

      function messageKey(message, index) {
        return (message && message.id) || ('m-' + index + '-' + (message && message.ts ? message.ts : '0'));
      }

      function isNarration(message) { return messageKind(message) === 'narration'; }
      function isRoll(message) { return messageKind(message) === 'roll'; }
      function isSystem(message) { return messageKind(message) === 'system'; }

      function variantIndex(message) {
        var variants = message && Array.isArray(message.variants) ? message.variants : [];
        if (!variants.length) return 0;
        var index = Number(message.variant_index);
        if (!isFinite(index)) index = 0;
        return Math.min(Math.max(0, Math.round(index)), variants.length - 1);
      }

      function displayContent(message) {
        if (!message) return '';
        if (!message._streaming && typeof message.display_content === 'string') return message.display_content;
        if (!message._streaming && Array.isArray(message.variants) && message.variants.length) {
          var value = message.variants[variantIndex(message)];
          if (typeof value === 'string') return value;
        }
        return message.content == null ? '' : message.content;
      }

      function hasContent(message) {
        return String(message && message.content != null ? message.content : '').trim().length > 0;
      }

      function hasVariants(message) {
        return !!message && message.role === 'assistant' && Array.isArray(message.variants) && message.variants.length > 1;
      }

      function variantLabel(message) {
        var total = message && Array.isArray(message.variants) ? message.variants.length : 0;
        if (!total) return '';
        return (variantIndex(message) + 1) + '/' + total;
      }

      function canSwipePrev(message) {
        return !swipeBusy.value && variantIndex(message) > 0;
      }

      function canSwipeNext(message) {
        var total = message && Array.isArray(message.variants) ? message.variants.length : 0;
        return !swipeBusy.value && variantIndex(message) < total - 1;
      }

      function isStreamingMessage(message) {
        return !!streaming.value && !!message && !!message._streaming;
      }

      function rollText(message) {
        return String(displayContent(message) || '').replace(/^\s*🎲\ufe0f?\s*/, '');
      }

      function speakerOf(message) {
        if (message && message.speaker) return String(message.speaker);
        var kind = messageKind(message);
        if (kind === 'narration') return '旁白';
        if (kind === 'roll') return '骰子';
        if (kind === 'ooc') return userDisplayName.value;
        if (kind === 'system') return '系统';
        if (message && message.role === 'user') return userDisplayName.value;
        return charDisplayName.value;
      }

      function kindLabel(message) {
        var kind = messageKind(message);
        if (kind === 'action') return '动作';
        if (kind === 'ooc') return 'OOC';
        return '';
      }

      function messageAvatarSrc(message) {
        if (!message || message.role === 'user' || messageKind(message) === 'ooc') return '';
        var character = currentCharacter.value;
        if (!character || !character.avatar_url || avatarFailed[character.id]) return '';
        return character.avatar_url;
      }

      function messageAvatarInitial(message) {
        var userLike = !!message && (message.role === 'user' || messageKind(message) === 'ooc');
        return userLike ? userInitial.value : charInitial(currentCharacter.value);
      }

      function messageAvatarClass(message) {
        var userLike = !!message && (message.role === 'user' || messageKind(message) === 'ooc');
        return userLike ? 'user' : '';
      }

      function lastAssistantIndex() {
        for (var i = messages.value.length - 1; i >= 0; i--) {
          if (messages.value[i].role === 'assistant') return i;
        }
        return -1;
      }

      function isLastAssistant(message) {
        var index = messages.value.indexOf(message);
        return index >= 0 && index === lastAssistantIndex();
      }

      function canRegenerate(message) {
        return !!message && message.role === 'assistant' && !streaming.value && isLastAssistant(message)
          && messages.value.some(function (item) { return item.role === 'user'; });
      }

      async function copyMessage(message) {
        var text = String(displayContent(message) || '');
        if (!text) {
          toastInfo('这条消息没有可复制的内容', '复制失败');
          return;
        }
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
          } else {
            fallbackCopy(text);
          }
          toastSuccess('已复制到剪贴板', '复制成功', '📋');
        } catch (err) {
          try {
            fallbackCopy(text);
            toastSuccess('已复制到剪贴板', '复制成功', '📋');
          } catch (e) {
            toastError('浏览器拒绝了剪贴板访问，请手动选择复制', '复制失败');
          }
        }
      }

      function fallbackCopy(text) {
        var area = document.createElement('textarea');
        area.value = text;
        area.setAttribute('readonly', 'readonly');
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        var ok = document.execCommand('copy');
        document.body.removeChild(area);
        if (!ok) throw new Error('copy failed');
      }

      /* ═════════════ 滚动 ═════════════ */

      function scrollToBottom(smooth) {
        nextTick(function () {
          var el = chatEl.value;
          if (!el) return;
          autoScroll.value = true;
          if (smooth && el.scrollTo) {
            try { el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }); return; } catch (e) { /* 回退 */ }
          }
          el.scrollTop = el.scrollHeight;
        });
      }

      function onChatScroll() {
        var el = chatEl.value;
        if (!el) return;
        var distance = el.scrollHeight - el.scrollTop - el.clientHeight;
        autoScroll.value = distance < 80;
      }

      var chatSeq = 0;

      async function retryChat() {
        if (currentId.value) await openChat(currentId.value);
      }

      async function startChat() {
        var character = currentCharacter.value;
        if (!character || chatLoading.value) return;
        chatLoading.value = true;
        chatError.value = '';
        try {
          var data = await apiChar(character.id, '/chat/start', { method: 'POST' });
          applyMessages(data.messages);
          scrollToBottom();
          if (!messages.value.length) {
            toastInfo('该角色还没有开场白，请先在「编辑角色 → 开场」中填写 first_mes', '暂无内容');
          }
        } catch (err) {
          chatError.value = err.message;
          toastError(err.message, '开始对话失败');
        } finally {
          chatLoading.value = false;
        }
      }

      var greetingOptions = computed(function () {
        var character = currentCharacter.value;
        var options = [];
        if (!character) return options;
        var first = String(character.first_mes || '').trim();
        var offset = first ? 1 : 0;
        if (first) options.push({ index: 0, label: '开场白（first_mes）' });
        var greetings = Array.isArray(character.alternate_greetings) ? character.alternate_greetings : [];
        greetings.forEach(function (greeting, i) {
          if (!String(greeting || '').trim()) return;
          options.push({ index: i + offset, label: '备选 ' + (i + 1) + '：' + shorten(greeting, 14) });
        });
        return options;
      });

      function openClearDialog() {
        clearChat(null);
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

      async function clearChat(greetingIndex) {
        var character = currentCharacter.value;
        if (!character || streaming.value || clearing.value) return;
        var useGreeting = typeof greetingIndex === 'number' && isFinite(greetingIndex);
        var ok = await askConfirm(
          '清空对话',
          useGreeting
            ? '将清空与「' + character.name + '」的全部聊天记录，并使用所选开场白重新开始。确定吗？'
            : '将清空与「' + character.name + '」的全部聊天记录，并随机选择一条开场白重新开始。确定吗？',
          { confirmText: '清空并重开' }
        );
        if (!ok) return;
        clearing.value = true;
        try {
          var body = useGreeting ? { greeting_index: greetingIndex } : {};
          var data = await apiChar(character.id, '/chat/clear', { method: 'POST', body: body });
          applyMessages(data.messages);
          autoScroll.value = true;
          scrollToBottom();
          toastSuccess('对话已重新开始', '已清空', '🧹');
        } catch (err) {
          toastError(err.message, '清空对话失败');
        } finally {
          clearing.value = false;
        }
      }

      /* ═════════════ SSE / 发送 ═════════════ */

      function stopStream() {
        if (abortCtrl) {
          try { abortCtrl.abort(); } catch (e) { /* 忽略 */ }
        }
      }

      function resolveStreamIndex(pendingIndex, targetIndex) {
        if (pendingIndex >= 0 && messages.value[pendingIndex] && messages.value[pendingIndex]._streaming) {
          return pendingIndex;
        }
        if (typeof targetIndex === 'number' && messages.value[targetIndex]) return targetIndex;
        for (var i = messages.value.length - 1; i >= 0; i--) {
          if (messages.value[i].role === 'assistant') return i;
        }
        var created = normalizeMessage({ role: 'assistant', content: '', ts: nowTs() });
        created._streaming = true;
        messages.value.push(created);
        return messages.value.length - 1;
      }

      function upsertMessage(raw, pendingIndex) {
        if (!raw || typeof raw !== 'object') return pendingIndex;
        var message = normalizeMessage(raw);
        var idx = -1;
        var i;
        if (message.id) {
          for (i = 0; i < messages.value.length; i++) {
            if (messages.value[i].id && messages.value[i].id === message.id) { idx = i; break; }
          }
        }
        // 乐观插入的用户消息没有 id：用内容匹配，避免同一条消息出现两次
        if (idx < 0 && message.role === 'user') {
          for (i = messages.value.length - 1; i >= 0; i--) {
            var candidate = messages.value[i];
            if (candidate.role === 'user' && !candidate.id && candidate.content === message.content) {
              idx = i;
              break;
            }
          }
        }
        // 只有 assistant 的最终消息才可以替换流式占位气泡
        if (idx < 0 && pendingIndex >= 0 && pendingIndex < messages.value.length) {
          var pending = messages.value[pendingIndex];
          if (!pending.id && pending.role === 'assistant' && message.role === 'assistant') idx = pendingIndex;
        }
        if (idx >= 0) {
          var old = messages.value[idx] || {};
          messages.value[idx] = Object.assign({}, old, message, { _streaming: !!old._streaming });
          return idx === pendingIndex ? idx : pendingIndex;
        }
        // 指令发送（/say、/act、/ooc）没有本地用户气泡：插到流式占位气泡之前
        if (message.role === 'user' && pendingIndex >= 0 && pendingIndex <= messages.value.length) {
          messages.value.splice(pendingIndex, 0, message);
          return pendingIndex + 1;
        }
        messages.value.push(message);
        return pendingIndex;
      }

      function handleSwipeEvent(event) {
        var index = lastAssistantIndex();
        if (index < 0) return;
        var message = messages.value[index];
        if (event && event.index != null) {
          var nextIndex = Number(event.index);
          if (isFinite(nextIndex)) message.variant_index = Math.max(0, Math.round(nextIndex));
        }
        if (event && event.total != null) {
          message._swipeTotal = Number(event.total) || 0;
        }
      }

      async function resyncChat(charId, seq) {
        try {
          var data = await apiChar(charId, '/chat');
          if (seq === streamSeq && currentId.value === charId) {
            applyMessages(data.messages);
            if (autoScroll.value) scrollToBottom();
          }
        } catch (err) {
          if (seq === streamSeq && currentId.value === charId) {
            messages.value = messages.value.filter(function (message) {
              return !(message.role === 'assistant' && !message.id && !message.content);
            });
          }
        }
      }

      async function runStream(options) {
        if (streaming.value) return;
        var charId = options.charId;
        if (!charId) return;
        var seq = ++streamSeq;
        streaming.value = true;
        autoScroll.value = true;
        var controller = new AbortController();
        abortCtrl = controller;

        var pendingIndex = -1;
        if (options.pending) {
          var pendingMessage = normalizeMessage({
            role: 'assistant', kind: 'dialogue', content: '', ts: nowTs(), speaker: charDisplayName.value
          });
          pendingMessage._streaming = true;
          messages.value.push(pendingMessage);
          pendingIndex = messages.value.length - 1;
          scrollToBottom();
        }

        if (typeof options.targetIndex === 'number' && messages.value[options.targetIndex]) {
          var targetMessage = messages.value[options.targetIndex];
          targetMessage._streaming = true;
          if (!options.appendTarget) {
            targetMessage.content = '';
            if (Array.isArray(targetMessage.variants)) targetMessage.variants = [];
          }
          scrollToBottom();
        }

        var pendingDelta = '';
        function flushDelta() {
          if (!pendingDelta) return;
          if (seq === streamSeq && currentId.value === charId) {
            var idx = resolveStreamIndex(pendingIndex, options.targetIndex);
            var message = messages.value[idx];
            if (message) { message._streaming = true; message.content += pendingDelta; }
            if (autoScroll.value) scrollToBottom();
          }
          pendingDelta = '';
        }
        var flushTimer = setInterval(flushDelta, 32);
        var sseError = '';
        try {
          var init = { method: 'POST', signal: controller.signal, headers: authHeaders({ 'Accept': 'text/event-stream' }) };
          if (options.body !== undefined && options.body !== null) {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(options.body);
          }
          var response = await fetch(options.url, init);
          await readSSE(response, function (event) {
            if (seq !== streamSeq) return false;
            var type = event && event.type;
            if (type === 'meta') {
              if (event.character) metaCharacter.value = String(event.character);
              if (event.user) metaUser.value = String(event.user);
              if (event.model) setModelInfo(String(event.model), '');
              return;
            }
            if (type === 'delta') {
              if (!options.discardDeltas && currentId.value === charId) pendingDelta += NovelUI.textContent(event.content);
              return;
            }
            flushDelta();
            if (type === 'message') {
              if (currentId.value !== charId) return;
              pendingIndex = upsertMessage(event.message, pendingIndex);
              if (autoScroll.value) scrollToBottom();
              return;
            }
            if (type === 'command_result') {
              handleCommandResult(event);
              return;
            }
            if (type === 'swipe') {
              handleSwipeEvent(event);
              return;
            }
            if (type === 'impersonate') {
              if (event.text != null && String(event.text) !== '') {
                draft.value = String(event.text);
                nextTick(function () { autoGrow(); focusDraft(); });
                toastSuccess('已生成你的台词，可修改后发送', '台词已填入', '🎤');
              }
              return;
            }
            if (type === 'error') {
              sseError = event.message ? String(event.message) : '生成失败，请稍后重试';
              return false;
            }
            if (type === 'done') return false;
            if (type === 'ping') return;
          });
          if (sseError) toastError(sseError, '生成失败');
        } catch (err) {
          if (err && err.name === 'AbortError') {
            toastInfo('已停止生成，已生成的内容会保存', '已停止', '⏹');
          } else {
            toastError((err && err.message) || '网络异常，请稍后重试', '生成失败');
          }
        } finally {
          clearInterval(flushTimer); flushDelta();
          var stillCurrent = seq === streamSeq && currentId.value === charId;
          streaming.value = false;
          abortCtrl = null;
          messages.value.forEach(function (message) { message._streaming = false; });
          if (stillCurrent) await resyncChat(charId, seq);
        }
      }

      async function sendDialogue(text) {
        var character = currentCharacter.value;
        if (!character) return;
        var charId = character.id;
        messages.value.push(normalizeMessage({
          role: 'user', kind: 'dialogue', speaker: userDisplayName.value, content: text, ts: nowTs()
        }));
        scrollToBottom();
        await runStream({
          charId: charId,
          url: charPath(charId, '/chat/send'),
          body: { message: text },
          pending: true
        });
      }

      function parseCommandToken(text) {
        var match = /^\/([a-zA-Z]+)/.exec(String(text || ''));
        return match ? '/' + match[1].toLowerCase() : '';
      }

      function resolveCommand(text) {
        var token = parseCommandToken(text);
        return token ? (COMMAND_BY_ALIAS[token] || null) : null;
      }

      async function sendCommand(text) {
        var character = currentCharacter.value;
        if (!character) return;
        var charId = character.id;
        var command = resolveCommand(text);
        var name = command ? command.name.slice(1) : '';
        if (name === 'impersonate') {
          // 不写入聊天：忽略流式片段，只接收 impersonate 事件
          await runStream({
            charId: charId, url: charPath(charId, '/chat/send'),
            body: { message: text }, pending: false, discardDeltas: true
          });
          return;
        }
        if (name === 'continue') {
          // 后端把续写追加到最后一條角色的当前版本上，因此直接在该气泡内流式追加
          var continueIndex = lastAssistantIndex();
          await runStream({
            charId: charId, url: charPath(charId, '/chat/send'),
            body: { message: text },
            pending: continueIndex < 0,
            targetIndex: continueIndex >= 0 ? continueIndex : undefined,
            appendTarget: true
          });
          return;
        }
        if (name === 'swipe') {
          var swipeArgs = text.replace(/^\/[a-zA-Z]+/, '').trim().toLowerCase();
          var swipeDirection = swipeArgs.split(/\s+/)[0] || 'next';
          var swipeIndex = lastAssistantIndex();
          if (swipeDirection === 'new' && swipeIndex >= 0) {
            await runStream({
              charId: charId, url: charPath(charId, '/chat/send'),
              body: { message: text }, pending: false, targetIndex: swipeIndex
            });
          } else {
            await runStream({
              charId: charId, url: charPath(charId, '/chat/send'),
              body: { message: text }, pending: false
            });
          }
          return;
        }
        if (name === 'regen') {
          var index = lastAssistantIndex();
          if (index >= 0) {
            await runStream({ charId: charId, url: charPath(charId, '/chat/send'), body: { message: text }, pending: false, targetIndex: index });
          } else {
            await runStream({ charId: charId, url: charPath(charId, '/chat/send'), body: { message: text }, pending: true });
          }
          return;
        }
        var pending = !!COMMAND_TARGETS[name];
        await runStream({
          charId: charId,
          url: charPath(charId, '/chat/send'),
          body: { message: text },
          pending: pending
        });
        if (name === 'note') loadSettings(true).catch(function () { /* 忽略刷新失败 */ });
      }

      async function sendComposer(rawOverride) {
        // Vue passes a PointerEvent to a bare method handler. Only accept explicit
        // string overrides (slash commands); otherwise read the textarea value.
        var raw = typeof rawOverride === 'string' ? rawOverride.trim() : String(draft.value || '').trim();
        if (!raw) return;
        if (!currentCharacter.value || streaming.value || chatLoading.value) return;
        if (commandTimer) clearTimeout(commandTimer);
        commandResult.visible = false;
        var text = raw;
        if (raw.charAt(0) !== '/' && activeMode.value !== 'dialogue') {
          text = MODE_PREFIX[activeMode.value] + ' ' + raw;
        }
        draft.value = '';
        paletteDismissed.value = true;
        autoGrow();
        focusDraft();
        if (text.charAt(0) === '/') await sendCommand(text);
        else await sendDialogue(text);
      }

      /* ───────────── 重新生成 / 版本切换 ───────────── */

      async function regenerate() {
        var character = currentCharacter.value;
        if (!character || streaming.value || chatLoading.value) return;
        var index = lastAssistantIndex();
        if (index < 0) {
          toastInfo('还没有可重新生成的消息', '无法重新生成');
          return;
        }
        if (!messages.value.some(function (message) { return message.role === 'user'; })) {
          toastInfo('还没有你的消息，无法重新生成', '无法重新生成');
          return;
        }
        await runStream({
          charId: character.id,
          url: charPath(character.id, '/chat/regenerate'),
          pending: false,
          targetIndex: index
        });
      }

      async function swipeMessage(message, direction) {
        if (swipeBusy.value || streaming.value) return;
        var character = currentCharacter.value;
        if (!character || !message) return;
        if (!isLastAssistant(message)) {
          toastInfo('只能切换最后一条回复的版本', '版本切换');
          return;
        }
        if (direction !== 'new') {
          swipeBusy.value = true;
          try {
            var data = await apiChar(character.id, '/chat/swipe', { method: 'POST', body: { direction: direction } });
            if (Array.isArray(data.messages)) applyMessages(data.messages);
            else if (data.message) upsertMessage(data.message, -1);
            else await resyncChat(character.id, streamSeq);
            scrollToBottom();
          } catch (err) {
            toastError(err.message, '切换版本失败');
          } finally {
            swipeBusy.value = false;
          }
          return;
        }
        await runStream({
          charId: character.id,
          url: charPath(character.id, '/chat/swipe'),
          body: { direction: 'new' },
          pending: false,
          targetIndex: messages.value.indexOf(message)
        });
      }

      /* ═════════════ 设置读写 ═════════════ */

      async function loadIdentity(id) {
        cardRepair.value = null;
        try {
          var data = await apiChar(id, '/identity');
          if (currentId.value !== id) return;
          Object.assign(identity, data.identity);
          identityChoices.value = data.choices || [];
          Object.assign(compatibility, data.compatibility || {});
        } catch (err) { if (currentId.value === id) identityError.value = err.message; }
      }

      async function openIdentity() {
        if (!currentId.value || streaming.value) return;
        identityOpen.value = true; identityLoading.value = true; identityError.value = '';
        var id = currentId.value;
        await loadIdentity(id);
        if (currentId.value === id) { Object.assign(identityForm, identity); identityLoading.value = false; }
      }

      function chooseIdentity() {
        var choice = identityChoices.value.find(function (item) { return item.id === identityForm.source_character; });
        if (choice) { identityForm.user_name = choice.user_name; identityForm.user_persona = choice.user_persona; }
      }

      async function saveIdentity() {
        if (identitySaving.value || streaming.value) return;
        var id = currentId.value;
        identitySaving.value = true; identityError.value = '';
        try {
          var data = await apiChar(id, '/identity', {method:'PUT',body:identityForm});
          if (currentId.value !== id) return;
          Object.assign(identity, data.identity); Object.assign(compatibility, data.compatibility || {});
          metaUser.value = ''; identityOpen.value = false;
          if (pendingIdentityChat.value) { pendingIdentityChat.value = false; await startChat(); }
          toastSuccess('已绑定到本角色，之后的回复使用此身份；原有对话保持不变。');
        } catch (err) { if (currentId.value === id) identityError.value = err.message; }
        finally { identitySaving.value = false; }
      }

      function repairFieldLabel(field) {
        return ({name:'角色名称',description:'角色设定',personality:'性格',scenario:'场景',first_mes:'开场白',mes_example:'对话示例',creator_notes:'作者说明',system_prompt:'系统提示',post_history_instructions:'历史后置指令',alternate_greetings:'备选开场白',tags:'标签',creator:'作者',character_version:'版本'})[field] || field;
      }
      function repairFieldText(value) { return Array.isArray(value) ? JSON.stringify(value, null, 2) : String(value || ''); }
      async function previewCardMetadata() {
        if (identitySaving.value || streaming.value) return;
        var id = currentId.value;
        identitySaving.value = true; identityError.value = '';
        try {
          var data = await apiChar(id, '/sync-metadata', {timeout:45000});
          if (currentId.value === id) cardRepair.value = {id:id, fields:(data.fields || []).map(function (field) { return Object.assign({selected:false}, field); })};
        } catch (err) { if (currentId.value === id) identityError.value = err.message; }
        finally { identitySaving.value = false; }
      }
      async function syncCardMetadata() {
        if (identitySaving.value || streaming.value) return;
        var id = currentId.value;
        identitySaving.value = true; identityError.value = '';
        try {
          if (!cardRepair.value || cardRepair.value.id !== id) return;
          var chosen = cardRepair.value.fields.filter(function (field) { return field.selected; });
          var expected = {}, proposed = {};
          chosen.forEach(function (field) { expected[field.field] = field.current; proposed[field.field] = field.incoming; });
          var data = await apiChar(id, '/sync-metadata', {method:'POST',timeout:45000,body:{fields:chosen.map(function (field) { return field.field; }),expected:expected,proposed:proposed}});
          if (currentId.value !== id) return;
          Object.assign(compatibility, data.compatibility || {});
          await loadCharacters(); await loadWorldInfo(true); cardRepair.value = null;
          toastSuccess('已恢复勾选字段并补齐元数据；原数据已备份，未勾选字段、玩家身份和聊天保持不变。');
        } catch (err) { if (currentId.value === id) identityError.value = err.message; }
        finally { identitySaving.value = false; }
      }

      function onCardAction(event) {
        var button = event.target.closest('[data-message]');
        if (button && !streaming.value) { draft.value = button.getAttribute('data-message') || ''; focusDraft(); }
      }

      async function loadSettings(silent) {
        try {
          var data = await apiJson(API_BASE + '/settings');
          assignSettings(settings, data.settings || data);
          return settings;
        } catch (err) {
          if (!silent) toastError(err.message, '读取设置失败');
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
          toastError(err.message, '读取设置失败');
        } finally {
          settingsLoading.value = false;
        }
      }

      function closeSettings() {
        if (settingsSaving.value) return;
        settingsOpen.value = false;
      }

      async function saveSettings(silent) {
        if (settingsSaving.value) return;
        var payload = buildSettingsPayload(settingsForm);
        settingsSaving.value = true;
        try {
          var data = await apiJson(API_BASE + '/settings', { method: 'PUT', body: payload });
          assignSettings(settings, data.settings || payload);
          copySettingsToForm();
          metaUser.value = '';
          if (currentId.value) await loadIdentity(currentId.value);
          if (!silent) {
            settingsOpen.value = false;
            toastSuccess('角色扮演设置已保存', '保存成功', '✅');
          }
        } catch (err) {
          toastError(err.message, '保存设置失败');
        } finally {
          settingsSaving.value = false;
        }
      }

      async function toggleLorebook() {
        var payload = buildSettingsPayload(settings);
        payload.lorebook_enabled = settings.lorebook_enabled !== false;
        try {
          var data = await apiJson(API_BASE + '/settings', { method: 'PUT', body: payload });
          assignSettings(settings, data.settings || payload);
        } catch (err) {
          settings.lorebook_enabled = !settings.lorebook_enabled;
          toastError(err.message, '切换世界书开关失败');
        }
      }

      /* ═════════════ 世界书 ═════════════ */

      function normalizeWorldEntry(raw) {
        var source = raw && typeof raw === 'object' ? raw : {};
        return {
          id: source.id != null ? String(source.id) : '',
          comment: source.comment != null ? String(source.comment) : '',
          keys: Array.isArray(source.keys) ? source.keys.map(function (key) { return String(key); }) : [],
          secondary_keys: Array.isArray(source.secondary_keys)
            ? source.secondary_keys.map(function (key) { return String(key); }) : [],
          content: source.content != null ? String(source.content) : '',
          enabled: source.enabled !== false,
          constant: !!source.constant,
          order: Number(source.order) || 100
        };
      }

      var worldEnabledCount = computed(function () {
        return worldEntries.value.filter(function (entry) { return entry.enabled !== false; }).length;
      });

      function resetWorldExpanded() {
        Object.keys(expandedWorld).forEach(function (key) { delete expandedWorld[key]; });
      }

      async function loadWorldInfo(silent) {
        var character = currentCharacter.value;
        if (!character) {
          worldEntries.value = [];
          return;
        }
        worldLoading.value = true;
        worldError.value = '';
        var requestId = character.id;
        try {
          var data = await apiChar(requestId, '/worldinfo');
          if (currentCharacter.value && currentCharacter.value.id === requestId) {
            worldEntries.value = (Array.isArray(data.entries) ? data.entries : []).map(normalizeWorldEntry);
          }
        } catch (err) {
          if (currentCharacter.value && currentCharacter.value.id === requestId) {
            worldError.value = err.message;
          }
          if (!silent) toastError(err.message, '世界书加载失败');
        } finally {
          worldLoading.value = false;
        }
      }

      function worldTitle(entry) {
        if (!entry) return '';
        if (entry.comment) return entry.comment;
        if (entry.keys && entry.keys.length) return entry.keys[0];
        return '未命名条目';
      }

      function worldKeys(entry) {
        return entry && Array.isArray(entry.keys) ? entry.keys.slice(0, 6) : [];
      }

      function worldSecondaryKeys(entry) {
        return entry && Array.isArray(entry.secondary_keys) ? entry.secondary_keys.slice(0, 4) : [];
      }

      function isWorldExpanded(entry) {
        return !!(entry && expandedWorld[entry.id]);
      }

      function toggleWorldExpand(entry) {
        if (!entry || !entry.id) return;
        if (expandedWorld[entry.id]) delete expandedWorld[entry.id];
        else expandedWorld[entry.id] = true;
      }

      async function toggleWorldEntry(entry) {
        var character = currentCharacter.value;
        if (!character || !entry) return;
        var next = entry.enabled === false;
        entry.enabled = next;
        try {
          await apiChar(character.id, '/worldinfo/' + encodeURIComponent(entry.id), {
            method: 'PATCH', body: { enabled: next }
          });
        } catch (err) {
          entry.enabled = !next;
          toastError(err.message, '切换世界书条目失败');
        }
      }

      async function deleteWorldEntry(entry) {
        var character = currentCharacter.value;
        if (!character || !entry) return;
        var ok = await askConfirm('删除世界书条目', '确定删除「' + worldTitle(entry) + '」吗？该操作不可恢复。', { confirmText: '删除' });
        if (!ok) return;
        try {
          await apiChar(character.id, '/worldinfo/' + encodeURIComponent(entry.id), { method: 'DELETE' });
          worldEntries.value = worldEntries.value.filter(function (item) { return item.id !== entry.id; });
          toastSuccess('已删除世界书条目', '删除成功', '🗑');
        } catch (err) {
          toastError(err.message, '删除世界书条目失败');
        }
      }

      function pickWorldImport() {
        if (!currentCharacter.value || worldImporting.value) return;
        if (worldInputEl.value) worldInputEl.value.click();
      }

      async function onWorldFile(event) {
        var input = event && event.target;
        var file = input && input.files ? input.files[0] : null;
        if (input) input.value = '';
        if (!file) return;
        var character = currentCharacter.value;
        if (!character) return;
        if (!/\.json$/i.test(file.name || '') && !/\.txt$/i.test(file.name || '')) {
          toastError('请选择 SillyTavern 世界书 JSON 文件', '文件格式不支持');
          return;
        }
        if (file.size > 8 * 1024 * 1024) {
          toastError('世界书文件不能超过 8MB', '文件过大');
          return;
        }
        worldImporting.value = true;
        try {
          var dataUrl = await readFileAsDataURL(file);
          var base64 = stripDataUrl(dataUrl);
          var data = await apiChar(character.id, '/worldinfo/import', {
            method: 'POST',
            body: { filename: file.name, data_base64: base64, novel_id: novelId }
          });
          if (currentId.value !== character.id) return;
          if (Array.isArray(data.entries)) worldEntries.value = data.entries.map(normalizeWorldEntry);
          else await loadWorldInfo(true);
          var imported = Number(data.imported || 0);
          toastSuccess('已导入 ' + (isFinite(imported) ? imported : 0) + ' 条世界书条目' + (data.skipped ? '，跳过 ' + data.skipped + ' 条重复内容' : ''), '导入成功', '✅');
          panelTab.value = 'world';
          panelOpen.value = true;
        } catch (err) {
          toastError(err.message, '导入世界书失败');
        } finally {
          worldImporting.value = false;
        }
      }

      function openWorldPanel() {
        panelTab.value = 'world';
        panelOpen.value = true;
        if (currentCharacter.value && !worldEntries.value.length && !worldLoading.value) loadWorldInfo(true);
      }

      function togglePanel(tab) {
        if (tab) panelTab.value = tab;
        if (isPanelOverlay.value) {
          panelOpen.value = !panelOpen.value;
          return;
        }
        if (!panelOpen.value) {
          panelOpen.value = true;
          return;
        }
        if (tab && panelTab.value !== tab) {
          panelTab.value = tab;
          return;
        }
        panelOpen.value = false;
      }

      function closePanel() { panelOpen.value = false; }

      /* ═════════════ 角色 CRUD / 导入 / 从本书生成 ═════════════ */

      function resetCharForm() {
        var defaults = buildDefaultCharForm();
        Object.keys(defaults).forEach(function (key) { charForm[key] = defaults[key]; });
      }

      function openCreate() {
        resetCharForm();
        charFormError.value = '';
        charTab.value = 'basic';
        avatarPreview.value = '';
        pendingAvatarBase64 = '';
        charModalOpen.value = true;
      }

      function openEdit(character) {
        if (!character) return;
        if (streaming.value) {
          toastInfo('生成中，暂时不能编辑角色', '请稍候');
          return;
        }
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
          ? character.alternate_greetings.slice() : [];
        charForm.creator_notes = character.creator_notes || '';
        charForm.tags = characterTagsArr(character).join(', ');
        charForm.creator = character.creator || '';
        charForm.character_version = character.character_version || '';
        charFormError.value = '';
        charTab.value = 'basic';
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

      var modalInitial = computed(function () {
        var name = String(charForm.name || '').trim();
        return name ? name.charAt(0) : '?';
      });

      var modalAvatarSrc = computed(function () {
        if (avatarPreview.value) return avatarPreview.value;
        if (!charForm.id) return '';
        var character = findCharacter(charForm.id);
        if (character && character.avatar_url && !avatarFailed[character.id]) return character.avatar_url;
        return '';
      });

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
          toastError('请选择 PNG / JPG / WebP 图片', '头像格式不支持');
          return;
        }
        if (file.size > 8 * 1024 * 1024) {
          toastError('头像文件不能超过 8MB', '图片过大');
          return;
        }
        try {
          var dataUrl = await readFileAsDataURL(file);
          avatarPreview.value = dataUrl;
          pendingAvatarBase64 = stripDataUrl(dataUrl);
        } catch (err) {
          toastError(err.message, '读取头像失败');
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
          alternate_greetings: charForm.alternate_greetings.slice(),
          creator_notes: charForm.creator_notes || '',
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
          toastError('请先填写角色名称', '无法保存');
          charTab.value = 'basic';
          return;
        }
        var editing = !!charForm.id;
        var editingId = charForm.id;
        charSaving.value = true;
        try {
          var payload = buildCharPayload(name);
          if (!editing) payload.novel_id = novelId;
          var savedId = editingId;
          if (editing) {
            var putData = await apiChar(editingId, '', { method: 'PUT', body: payload });
            if (putData.character && putData.character.id) savedId = putData.character.id;
          } else {
            var postData = await apiJson(API_BASE + '/characters', { method: 'POST', body: payload });
            if (postData.character && postData.character.id) savedId = postData.character.id;
          }

          var avatarError = null;
          if (pendingAvatarBase64 && savedId) {
            try {
              await apiChar(savedId, '/avatar', { method: 'POST', body: { data_base64: pendingAvatarBase64 } });
              delete avatarFailed[savedId];
            } catch (err) {
              avatarError = err;
            }
          }

          charModalOpen.value = false;
          avatarPreview.value = '';
          pendingAvatarBase64 = '';
          if (avatarError) {
            toastError('角色已保存，但头像上传失败：' + avatarError.message, '头像上传失败');
          } else {
            toastSuccess(editing ? '角色信息已更新' : '新角色已创建', '保存成功', '✅');
          }

          await loadCharacters();
          if (savedId && findCharacter(savedId)) await selectCharacter(savedId);
          else if (characters.value.length) await selectCharacter(characters.value[0].id);
        } catch (err) {
          charFormError.value = err.message;
          toastError(err.message, '保存角色失败');
        } finally {
          charSaving.value = false;
        }
      }

      async function deleteCharacter(character) {
        var target = character || currentCharacter.value;
        if (!target || deleting.value) return;
        if (streaming.value) stopStream();
        var ok = await askConfirm(
          '删除角色',
          '确定删除「' + target.name + '」吗？该操作会同时删除聊天记录与头像，且不可恢复。',
          { confirmText: '删除', cancelText: '取消' }
        );
        if (!ok) return;
        deleting.value = true;
        try {
          await apiChar(target.id, '', { method: 'DELETE' });
          toastSuccess('已删除「' + target.name + '」', '删除成功', '✅');
          if (currentId.value === target.id) {
            currentId.value = '';
            messages.value = [];
            worldEntries.value = [];
          }
          await loadCharacters();
          if (characters.value.length) await selectCharacter(characters.value[0].id);
        } catch (err) {
          toastError(err.message, '删除角色失败');
        } finally {
          deleting.value = false;
        }
      }

      async function onImportFile(event) {
        var input = event && event.target;
        var file = input && input.files ? input.files[0] : null;
        if (input) input.value = '';
        if (!file) return;
        var lowerName = String(file.name || '').toLowerCase();
        if (!(lowerName.endsWith('.png') || lowerName.endsWith('.json'))) {
          toastError('仅支持 SillyTavern PNG 或 JSON 角色卡', '文件格式不支持');
          return;
        }
        if (file.size > 8 * 1024 * 1024) {
          toastError('角色卡文件不能超过 8MB', '文件过大');
          return;
        }
        importLoading.value = true;
        try {
          var dataUrl = await readFileAsDataURL(file);
          var base64 = stripDataUrl(dataUrl);
          if (!base64) throw new Error('无法读取文件内容');
          var data = await apiJson(API_BASE + '/characters/import', {
            method: 'POST',
            body: { filename: file.name, data_base64: base64, novel_id: novelId }
          });
          var imported = data.character || null;
          var formatText = data.format ? String(data.format).toUpperCase() : '';
          toastSuccess(
            '已导入' + (imported && imported.name ? '「' + imported.name + '」' : '角色卡') + (formatText ? '（' + formatText + '）' : ''),
            '导入成功', '✅'
          );
          await loadCharacters();
          var targetId = imported && imported.id && findCharacter(imported.id)
            ? imported.id
            : (characters.value[0] ? characters.value[0].id : '');
          if (targetId) await selectCharacter(targetId);
        } catch (err) {
          toastError((err && err.message) || '导入失败，请检查角色卡文件', '导入失败');
        } finally {
          importLoading.value = false;
        }
      }

      function openFromNovel() {
        if (!novelId || fromNovelLoading.value) return;
        fromNovelMode.value = 'ai';
        fromNovelOpen.value = true;
      }

      function closeFromNovel() {
        if (fromNovelLoading.value) return;
        fromNovelOpen.value = false;
      }

      async function fromNovel() {
        if (!novelId || fromNovelLoading.value) return;
        fromNovelLoading.value = true;
        try {
          var data = await apiJson(API_BASE + '/characters/from-novel/' + encodeURIComponent(novelId), {
            method: 'POST',
            body: { mode: fromNovelMode.value }
          });
          var created = Number(data.created || 0);
          var skipped = Number(data.skipped || 0);
          var aiFilled = Number(data.ai_filled || 0);
          var warnings = Array.isArray(data.warnings) ? data.warnings : [];
          var message = '本次生成 ' + created + ' 个角色'
            + (aiFilled ? '（AI 写卡 ' + aiFilled + ' 个）' : '')
            + (skipped ? '，跳过 ' + skipped + ' 个已存在的同名角色' : '');
          if (warnings.length) message += '。' + warnings.slice(0, 3).join('；');
          toastSuccess(message, '从本书生成完成', '✅');
          fromNovelOpen.value = false;
          await loadCharacters();
          var first = Array.isArray(data.characters) ? data.characters[0] : null;
          var targetId = first && first.id && findCharacter(first.id)
            ? first.id
            : (characters.value[0] ? characters.value[0].id : '');
          if (targetId) await selectCharacter(targetId);
        } catch (err) {
          toastError(err.message, '从本书生成失败');
        } finally {
          fromNovelLoading.value = false;
        }
      }

      /* ═════════════ 模型 / 小说标题 ═════════════ */

      function setModelInfo(model, providerName) {
        if (model) modelInfo.model = String(model);
        if (providerName) modelInfo.providerName = String(providerName);
        modelInfo.known = !!modelInfo.model;
      }

      async function loadHealth() {
        try {
          var data = await apiJson('/api/health');
          setModelInfo(data.model, data.provider_name || data.provider || '');
        } catch (err) {
          modelInfo.known = false;
        }
      }

      function refreshHealth() {
        loadHealth().then(function () {
          if (!modelInfo.known) toastInfo('未读取到当前模型，请检查后端 LLM 配置', '模型未连接');
        });
      }

      async function loadNovelTitle() {
        if (!novelId) return;
        try {
          var data = await apiJson('/api/novels/' + encodeURIComponent(novelId));
          var novel = data.novel || {};
          novelTitle.value = String(novel.title || novel.novel_title || novel.name || '');
        } catch (err) {
          novelTitle.value = '';
        }
      }

      /* ═════════════ 全局事件 ═════════════ */

      function closeMenus() {
        document.querySelectorAll('.rp-dropdown[open]').forEach(function (menu) { menu.removeAttribute('open'); });
      }
      function closeMenuAction(event) {
        if (event.target.closest('button') && !event.target.closest('button').disabled) closeMenus();
      }
      function onOutsideMenu(event) {
        document.querySelectorAll('.rp-dropdown[open]').forEach(function (menu) {
          if (!menu.contains(event.target)) menu.removeAttribute('open');
        });
      }

      function onResize() {
        var width = window.innerWidth;
        viewportW.value = width;
        var wide = width >= 1200;
        if (wide !== lastWideLayout) {
          lastWideLayout = wide;
          closeMenus();
        }

      }

      function onGlobalKeydown(event) {
        if (event.key !== 'Escape') return;
        closeMenus();
        if (confirmBox.visible) { resolveConfirm(false); return; }
        if (identityOpen.value && !identitySaving.value) { identityOpen.value = false; return; }
        if (settingsOpen.value) { closeSettings(); return; }
        if (charModalOpen.value) { closeCharModal(); return; }
        if (fromNovelOpen.value) { closeFromNovel(); return; }
        if (libraryOpen.value) { closeLibrary(); return; }
        if (panelOpen.value && isPanelOverlay.value) { closePanel(); return; }
        if (paletteOpen.value) paletteDismissed.value = true;
      }

      function onUnhandledRejection(event) {
        var reason = event && event.reason;
        var message = reason && (reason.message || reason);
        if (message) toastError(String(message), '未处理的异常');
      }

      /* ═════════════ 生命周期 ═════════════ */

      onMounted(async function () {
        applyThemeValue(theme.value);
        window.addEventListener('resize', onResize);
        window.addEventListener('keydown', onGlobalKeydown);
        document.addEventListener('click', onOutsideMenu);
        window.addEventListener('unhandledrejection', onUnhandledRejection);
        autoGrow();

        await Promise.all([
          loadSettings(true).catch(function () { /* 设置读取失败不阻塞角色加载 */ }),
          loadCharacters(),
          loadHealth(),
          loadNovelTitle()
        ]);

        panelOpen.value = false;
        if (!characters.value.length) { if (new URLSearchParams(location.search).get('create')) openCreate(); return; }
        var stored = new URLSearchParams(location.search).get('character') || safeStorageGet('rp_last_character' + (novelId ? ':'+novelId : '')) || '';
        var target = stored && findCharacter(stored) ? stored : (currentId.value || characters.value[0].id);
        await selectCharacter(target);
        if (new URLSearchParams(location.search).get('create')) openCreate();
        else if (new URLSearchParams(location.search).get('edit')) openEdit(currentCharacter.value);
      });

      onUnmounted(function () {
        window.removeEventListener('resize', onResize);
        window.removeEventListener('keydown', onGlobalKeydown);
        document.removeEventListener('click', onOutsideMenu);
        window.removeEventListener('unhandledrejection', onUnhandledRejection);
      });

      /* ═════════════ 暴露给模板 ═════════════ */

      return {
        identity:identity, identityForm:identityForm, identityOpen:identityOpen,
        identitySaving:identitySaving, identityLoading:identityLoading, identityChoices:identityChoices, pendingIdentityChat:pendingIdentityChat,
        identityError:identityError, compatibility:compatibility, openIdentity:openIdentity,
        chooseIdentity:chooseIdentity, saveIdentity:saveIdentity, onCardAction:onCardAction, syncCardMetadata:syncCardMetadata, previewCardMetadata:previewCardMetadata, cardRepair:cardRepair, repairFieldLabel:repairFieldLabel, repairFieldText:repairFieldText,
        // 顶栏
        closeMenus: closeMenus,
        closeMenuAction: closeMenuAction,
        topTitle: topTitle,
        topSub: topSub,
        modelLabel: modelLabel,
        modelTitle: modelTitle,
        modelKnown: modelKnown,
        refreshHealth: refreshHealth,
        theme: theme,
        toggleTheme: toggleTheme,
        setTheme: setTheme,
        openSettings: openSettings,
        // 主题/面板
        panelOpen: panelOpen,
        panelTab: panelTab,
        togglePanel: togglePanel,
        closePanel: closePanel,
        openWorldPanel: openWorldPanel,
        isPanelOverlay: isPanelOverlay,
        // 小说
        novelId: novelId,
        // 角色
        characters: characters,
        charactersLoading: charactersLoading,
        charactersError: charactersError,
        reloadCharacters: reloadCharacters,
        currentId: currentId,
        currentCharacter: currentCharacter,
        charSearch: charSearch,
        filteredCharacters: filteredCharacters,
        recentCharacters: recentCharacters,
        sidebarGroups: sidebarGroups,
        avatarFailed: avatarFailed,
        onAvatarError: onAvatarError,
        charInitial: charInitial,
        charSubline: charSubline,
        characterTags: characterTags,
        sourceLabel: sourceLabel,
        onCharacterClick: onCharacterClick,
        selectFromLibrary: selectFromLibrary,
        openLibrary: openLibrary,
        closeLibrary: closeLibrary,
        libraryOpen: libraryOpen,
        // 角色编辑
        charModalOpen: charModalOpen,
        charForm: charForm,
        charFormError: charFormError,
        charSaving: charSaving,
        charTab: charTab,
        charTabs: CHAR_TABS,
        modalInitial: modalInitial,
        modalAvatarSrc: modalAvatarSrc,
        avatarPreview: avatarPreview,
        openCreate: openCreate,
        openEdit: openEdit,
        closeCharModal: closeCharModal,
        saveCharacter: saveCharacter,
        deleteCharacter: deleteCharacter,
        pickAvatar: pickAvatar,
        onAvatarFile: onAvatarFile,
        clearAvatarPick: clearAvatarPick,
        // 导入 / 从本书生成
        importLoading: importLoading,
        importInputEl: importInputEl,
        avatarInputEl: avatarInputEl,
        worldInputEl: worldInputEl,
        pickImport: pickImport,
        onImportFile: onImportFile,
        fromNovelOpen: fromNovelOpen,
        fromNovelMode: fromNovelMode,
        fromNovelLoading: fromNovelLoading,
        openFromNovel: openFromNovel,
        closeFromNovel: closeFromNovel,
        fromNovel: fromNovel,
        // 聊天
        messages: messages, visibleMessages: visibleMessages, visibleCount: visibleCount,
        chatLoading: chatLoading,
        chatError: chatError,
        retryChat: retryChat,
        startChat: startChat,
        streaming: streaming,
        showJump: showJump,
        autoScroll: autoScroll,
        scrollToBottom: scrollToBottom,
        onChatScroll: onChatScroll,
        chatEl: chatEl,
        regenerate: regenerate,
        swipeBusy: swipeBusy,
        swipeMessage: swipeMessage,
        canSwipePrev: canSwipePrev,
        canSwipeNext: canSwipeNext,
        hasVariants: hasVariants,
        variantLabel: variantLabel,
        canRegenerate: canRegenerate,
        copyMessage: copyMessage,
        messageKey: messageKey,
        messageClass: messageClass,
        messageKind: messageKind,
        isNarration: isNarration,
        isRoll: isRoll,
        isSystem: isSystem,
        messageAvatarSrc: messageAvatarSrc,
        messageAvatarInitial: messageAvatarInitial,
        messageAvatarClass: messageAvatarClass,
        speakerOf: speakerOf,
        kindLabel: kindLabel,
        displayContent: displayContent,
        hasContent: hasContent,
        isStreamingMessage: isStreamingMessage,
        rollText: rollText,
        fmtTime: fmtTime,
        renderMarkdown: renderMarkdown,
        greetingOptions: greetingOptions,
        onGreetingSelect: onGreetingSelect,
        openClearDialog: openClearDialog,
        clearing: clearing,
        userDisplayName: userDisplayName,
        userInitial: userInitial,
        // 输入
        draft: draft,
        draftEl: draftEl,
        canSend: canSend,
        activeMode: activeMode,
        modes: MODES,
        setMode: setMode,
        modeHint: modeHint,
        composerPlaceholder: composerPlaceholder,
        onComposerKeydown: onComposerKeydown,
        autoGrow: autoGrow,
        sendComposer: sendComposer,
        stopStream: stopStream,
        paletteOpen: paletteOpen,
        paletteCommands: paletteCommands,
        paletteIndex: paletteIndex,
        applyCommand: applyCommand,
        commands: COMMANDS,
        commandResult: commandResult,
        // 世界书
        settings: settings,
        worldEntries: worldEntries,
        worldLoading: worldLoading,
        worldError: worldError,
        worldImporting: worldImporting,
        worldEnabledCount: worldEnabledCount,
        loadWorldInfo: loadWorldInfo,
        pickWorldImport: pickWorldImport,
        onWorldFile: onWorldFile,
        worldTitle: worldTitle,
        worldKeys: worldKeys,
        worldSecondaryKeys: worldSecondaryKeys,
        isWorldExpanded: isWorldExpanded,
        toggleWorldExpand: toggleWorldExpand,
        toggleWorldEntry: toggleWorldEntry,
        deleteWorldEntry: deleteWorldEntry,
        toggleLorebook: toggleLorebook,
        // 设置
        settingsOpen: settingsOpen,
        settingsForm: settingsForm,
        settingsLoading: settingsLoading,
        settingsSaving: settingsSaving,
        temperatureLabel: temperatureLabel,
        closeSettings: closeSettings,
        saveSettings: saveSettings,
        // 提示 / 确认
        toasts: toasts,
        dismissToast: dismissToast,
        confirmBox: confirmBox,
        resolveConfirm: resolveConfirm
      };
    }
  });

  try {
    NovelApp.register(app);
    app.mount('#app');
  } catch (err) {
    showBootError((err && err.message) || String(err));
  }
})();
