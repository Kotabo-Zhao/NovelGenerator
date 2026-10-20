(function () {
  'use strict';

  /* ═════════════ 启动守卫 ═════════════ */

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

  /* ═════════════ 常量 ═════════════ */

  var THEME_COLORS = { dark: '#0b0912', light: '#f4f2fb' };
  var READ_SIZES = { md: '15px', lg: '17px' };
  var ROLE_LABELS = {
    protagonist: '主角', supporting: '配角', antagonist: '反派',
    npc: '配角', extra: '路人', main: '主角'
  };
  var INTENT_LABELS = { travel: '移动', talk: '对话', act: '行动' };

  var novelId = '';
  try {
    novelId = new URLSearchParams(window.location.search).get('novel') || '';
  } catch (e) { novelId = ''; }

  var API_ROOT = '/api/novels/' + encodeURIComponent(novelId) + '/interactive';
  var URLS = {
    start: API_ROOT + '/start',
    scene: API_ROOT + '/scene',
    chat: API_ROOT + '/chat',
    act: API_ROOT + '/act',
    state: API_ROOT + '/state',
    actions: API_ROOT + '/actions',
    casts: API_ROOT + '/cast-options',
    restart: API_ROOT + '/restart',
    rollback: API_ROOT + '/rollback'
  };

  /* ═════════════ 工具函数 ═════════════ */

  function safeStorageGet(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }
  function safeStorageSet(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* 隐私模式忽略 */ }
  }
  function nowTs() { return Math.floor(Date.now() / 1000); }
  function shorten(text, max) {
    var s = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
    return s.length > max ? s.slice(0, max) + '…' : s;
  }
  function hashString(text) {
    var h = 2166136261;
    var s = String(text == null ? '' : text);
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }
  /* 每名角色固定一个色调（0–7），同名跨会话一致 */
  function charTone(name) {
    return hashString(String(name || '？').trim()) % 8;
  }
  function charInitial(name) {
    var s = String(name || '').trim();
    if (!s) return '？';
    if (s === '旁白' || s === '叙述') return '旁';
    return s.charAt(0);
  }
  function roleLabel(role) {
    var key = String(role || '').toLowerCase();
    if (ROLE_LABELS[key]) return ROLE_LABELS[key];
    return role ? String(role).slice(0, 4) : '角色';
  }
  function intentLabel(intent) {
    return INTENT_LABELS[String(intent || '')] || '行动';
  }
  function noticeIcon(tone) {
    if (tone === 'error') return '⚠️';
    if (tone === 'warn') return '⚠️';
    if (tone === 'success') return '✅';
    return '✦';
  }
  /* 移除模型原始台词标记（旁白/动作），仅用于展示层 */
  function cleanText(text) {
    return String(text == null ? '' : text)
      .replace(/【(?:旁白|叙述|动作|描写)】/g, ' ')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/[ \t]{2,}/g, ' ')
      .trim();
  }

  /* 与后端 parse_scene_markup 对齐：【旁白】/【动作】/【角色名】 */
  function parseMarkupBlocks(text) {
    var blocks = [];
    String(text == null ? '' : text).replace(/\r\n?/g, '\n').split('\n').forEach(function (raw) {
      var line = raw.trim();
      if (!line) return;
      if (line.charAt(0) === '【') {
        var end = line.indexOf('】');
        if (end > 1) {
          var speaker = line.slice(1, end).trim();
          var content = line.slice(end + 1).trim();
          if (!content) return;
          if (speaker === '旁白' || speaker === '叙述' || speaker === '旁白/叙述') {
            blocks.push({ type: 'narration', speaker: '', content: content });
          } else if (speaker === '动作' || speaker === '描写') {
            blocks.push({ type: 'action', speaker: '', content: content });
          } else {
            blocks.push({ type: 'dialogue', speaker: speaker, content: content });
          }
          return;
        }
      }
      if (blocks.length && blocks[blocks.length - 1].type === 'narration') {
        blocks[blocks.length - 1].content += line;
      } else {
        blocks.push({ type: 'narration', speaker: '', content: line });
      }
    });
    return blocks;
  }

  function blockTypeForSpeaker(speaker) {
    var s = String(speaker || '').trim();
    if (s === '旁白' || s === '叙述' || s === '旁白/叙述') return 'narration';
    if (s === '动作' || s === '描写') return 'action';
    return 'dialogue';
  }

  /* ── 安全 Markdown：先拆分为文本 token，再交给 Vue 文本节点渲染；绝不拼接 HTML ── */
  function pushInlineTokens(out, text) {
    var re = /\*\*([\s\S]+?)\*\*|\*([^*\n]+?)\*/g;
    var last = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) out.push({ t: 'text', v: text.slice(last, m.index) });
      if (m[1] != null) out.push({ t: 'strong', v: m[1] });
      else out.push({ t: 'em', v: m[2] });
      last = re.lastIndex;
    }
    if (last < text.length) out.push({ t: 'text', v: text.slice(last) });
  }
  function mdTokens(raw) {
    var text = String(raw == null ? '' : raw).replace(/\r\n?/g, '\n');
    var out = [];
    var lines = text.split('\n');
    for (var li = 0; li < lines.length; li++) {
      if (li > 0) out.push({ t: 'br', v: '' });
      var parts = lines[li].split('`');
      for (var pi = 0; pi < parts.length; pi++) {
        if (pi % 2 === 1) {
          if (parts[pi]) out.push({ t: 'code', v: parts[pi] });
        } else {
          pushInlineTokens(out, parts[pi]);
        }
      }
    }
    return out;
  }

  /* ═════════════ 主题 ═════════════ */

  function readStoredTheme() {
    return safeStorageGet('theme') === 'light' ? 'light' : 'dark';
  }
  function applyThemeValue(value) {
    var themeName = value === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', themeName);
    var meta = document.getElementById('sc-theme-color');
    if (meta) meta.setAttribute('content', THEME_COLORS[themeName]);
    safeStorageSet('theme', themeName);
  }

  /* ═════════════ 鉴权 / API / SSE ═════════════ */

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
    return NovelUI.requestJson(path, Object.assign({}, options || {}, { headers: authHeaders() }));
  }
  function readSSE(response, onEvent, controller) {
    return NovelWorldRuntime.readEvents(response, onEvent, NovelUI.readSSE, {
      abort: function () { controller.abort(); }
    });
  }

  /* ═════════════ Vue 应用 ═════════════ */

  var app = createApp({ setup: setup });

  function setup() {
    /* ───────── 界面状态 ───────── */
    var theme = ref(readStoredTheme());
    var fontSize = ref(safeStorageGet('scene_fontsize') === 'lg' ? 'lg' : 'md');
    var menuOpen = ref(false);
    var targetPickerOpen = ref(false);
    var panelOpen = ref(false);
    var viewportW = ref(window.innerWidth);
    var autoScroll = ref(true);

    /* ───────── 数据 ───────── */
    var initializing = ref(true);
    var initError = ref('');
    var state = ref(null);
    var castOptions = ref([]);
    var characters = ref([]);
    var novelTitle = ref('');
    var novelGenre = ref('');
    var novelStyle = ref('');
    var feed = ref([]);
    var visibleCount = ref(80);
    var visibleFeed = computed(function () { return NovelWorldRuntime.historyWindow(feed.value, visibleCount.value); });
    var earlierCount = computed(function () { return Math.max(0, feed.value.length - visibleCount.value); });
    async function showEarlier() {
      var el = streamEl.value;
      var height = el ? el.scrollHeight : 0;
      visibleCount.value += 80;
      await nextTick();
      if (el) el.scrollTop += el.scrollHeight - height;
    }
    var feedRestored = ref(false);
    var snap = ref(null);
    var nodeInfo = ref(null);
    var actions = ref([]);
    var actionsLoading = ref(false);
    var startChar = ref('');
    var startBusy = ref(false);
    var startError = ref('');
    var sceneStream = reactive({ active: false, phase: '', text: '', sceneNum: 0 });
    var chatStream = reactive({ active: false, phase: '' });
    var streamKind = ref('');
    var busy = ref(false);
    var draft = ref('');
    var inputMode = ref('dialogue');
    var target = ref('');
    var streamEl = ref(null);
    var draftEl = ref(null);
    var toasts = ref([]);
    var toastSeq = 0;
    var confirmBox = reactive({
      visible: false, title: '', message: '', confirmText: '确定', cancelText: '取消', resolve: null
    });

    /* 内部非响应状态 */
    var feedSeq = 0;
    var abortCtrl = null;
    var liveChat = null;
    var liveResult = null;
    var introTimer = null;
    var scrollTickPending = false;
    var scrollLock = false;
    var scrollLockTimer = null;
    var lastDesktopLayout = null;

    /* ───────── 派生数据 ───────── */
    var hasState = computed(function () { return !!state.value; });
    var playerName = computed(function () {
      var st = state.value;
      return (st && st.player_char && st.player_char.name) || '你';
    });
    var playerInitial = computed(function () { return charInitial(playerName.value); });
    var playerIdentity = computed(function () {
      var pc = state.value && state.value.player_char;
      if (!pc) return '';
      return String(pc.identity || pc.personality_brief || '').trim();
    });
    var innerState = computed(function () { return (state.value && state.value.state) || {}; });
    var world = computed(function () { return (state.value && state.value.world) || {}; });
    var sceneNo = computed(function () {
      var s = snap.value;
      if (s && typeof s.scene_num === 'number') return Math.max(0, s.scene_num);
      if (state.value) {
        var n = Number(state.value.scene_num);
        if (isFinite(n)) return Math.max(0, n);
      }
      return 0;
    });
    var sceneLabel = computed(function () {
      var n = sceneNo.value;
      return n > 0 ? ('第 ' + n + ' 幕') : '序章';
    });
    var locationLabel = computed(function () {
      var s = snap.value;
      if (s && s.location) return String(s.location);
      var w = world.value;
      var st = state.value;
      return String(
        w.location ||
        innerState.value.location ||
        (st && st.player_state && st.player_state.location) ||
        '未知地点'
      ).trim() || '未知地点';
    });
    var timeLabel = computed(function () {
      var t = world.value.time;
      var label = (t && t.label) || '';
      if (!label && state.value && state.value.player_state) label = state.value.player_state.time || '';
      return String(label || '未知时辰');
    });
    var objective = computed(function () {
      var s = snap.value;
      if (s && s.objective) return String(s.objective);
      return String(innerState.value.objective || '');
    });
    var headerTitle = computed(function () {
      return novelTitle.value || '小说世界';
    });
    var appStyle = computed(function () {
      return { '--read-size': READ_SIZES[fontSize.value] || READ_SIZES.md };
    });
    var isPanelOverlay = computed(function () { return viewportW.value < 1180; });
    var streamPhase = computed(function () {
      return sceneStream.phase || chatStream.phase || '';
    });
    var scenePreview = computed(function () {
      var text = String(sceneStream.text || '').replace(/【[^】]{1,14}】/g, ' ').replace(/\s+/g, ' ').trim();
      return text.length > 280 ? '…' + text.slice(-280) : text;
    });
    var canSend = computed(function () {
      return hasState.value && !busy.value && String(draft.value || '').trim().length > 0;
    });
    var composerHint = computed(function () {
      if (busy.value) return streamPhase.value || '生成中…';
      if (inputMode.value === 'action') return '描述你的行动，AI 会判断是否触发剧情行动';
      return 'Enter 发送 · 可 @ 指定回应角色';
    });
    var composerPlaceholder = computed(function () {
      var t = target.value;
      if (inputMode.value === 'action') {
        return t ? ('描述你对 ' + t + ' 的行动…') : '描述你要做的事，例如：我推门走进雨里';
      }
      return t ? ('对 ' + t + ' 说点什么…') : '你想说什么？也可以 @角色 指定回应者';
    });
    var showJump = computed(function () { return !autoScroll.value; });

    var avatarMap = computed(function () {
      var map = {};
      characters.value.forEach(function (c) {
        var name = String((c && c.name) || '').trim();
        if (!name) return;
        map[name] = String((c && c.avatar_url) || '');
        map[name.toLowerCase()] = String((c && c.avatar_url) || '');
      });
      return map;
    });
    function avatarSrc(name) {
      var key = String(name || '').trim();
      if (!key) return '';
      return avatarMap.value[key] || avatarMap.value[key.toLowerCase()] || '';
    }

    var startOptions = computed(function () {
      return castOptions.value.map(function (o) {
        return {
          name: o.name,
          identity: o.identity || '',
          personality: o.personality || '',
          speak_style: o.speak_style || '',
          role: o.role || '',
          avatar: avatarSrc(o.name),
          tone: charTone(o.name)
        };
      });
    });

    var presentCast = computed(function () { return buildCast().filter(function (c) { return c.present; }); });
    var absentCast = computed(function () { return buildCast().filter(function (c) { return !c.present; }); });
    var castCards = computed(function () { return buildCast(); });
    var presentCount = computed(function () { return presentCast.value.length; });

    function buildCast() {
      var st = state.value;
      if (!st) return [];
      var player = playerName.value;
      var casts = st.casts || {};
      var cs = st.cast_states || {};
      var w = st.world || {};
      var inner = st.state || {};
      var ps = st.player_state || {};
      var myLoc = String(w.location || inner.location || ps.location || '').trim();
      var locChars = [];
      var entry = myLoc && w.locations ? w.locations[myLoc] : null;
      if (entry && Array.isArray(entry.chars)) {
        locChars = entry.chars.map(function (x) { return String(x).trim(); }).filter(Boolean);
      }
      var optionMap = {};
      castOptions.value.forEach(function (o) {
        if (o && o.name && !optionMap[o.name]) optionMap[o.name] = o;
      });
      var names = [];
      var seen = {};
      function add(name) {
        var n = String(name || '').trim();
        if (!n || n === player || seen[n]) return;
        seen[n] = true;
        names.push(n);
      }
      Object.keys(casts).forEach(add);
      Object.keys(cs).forEach(add);
      locChars.forEach(function (n) { if (casts[n] || cs[n]) add(n); });

      var list = names.map(function (name) {
        var c = casts[name] || {};
        var s = cs[name] || {};
        var opt = optionMap[name] || {};
        var present = false;
        if (s.present === false) present = false;
        else if (s.location && myLoc && String(s.location).trim() === myLoc) present = true;
        else if (s.present === true) present = true;
        else if (locChars.indexOf(name) >= 0) present = true;
        else if (c.present === true) present = true;
        var mood = String(s.mood || '').trim();
        var location = String(s.location || '').trim();
        var identity = String(opt.identity || c.identity || '').trim();
        var subline = mood || (identity ? shorten(identity, 34) : '');
        return {
          name: name,
          present: present,
          mood: mood,
          location: location,
          identity: identity,
          role: c.role || opt.role || '',
          subline: subline,
          tone: charTone(name)
        };
      });
      list.sort(function (a, b) {
        if (a.present !== b.present) return a.present ? -1 : 1;
        return 0;
      });
      return list;
    }

    function castSubline(c) {
      if (!c) return '';
      var bits = [];
      if (c.mood) bits.push(c.mood);
      if (c.identity && bits.length < 2) bits.push(shorten(c.identity, 24));
      if (!bits.length) bits.push(c.present === false ? '不在场' : '在场');
      return bits.join(' · ');
    }

    var relationsList = computed(function () {
      var s = snap.value;
      var raw = (s && s.relations) || innerState.value.relations || {};
      if (!raw || typeof raw !== 'object') return [];
      return Object.keys(raw).slice(0, 6).map(function (name) {
        var value = raw[name];
        return {
          name: name,
          value: value,
          label: typeof value === 'number' && isFinite(value) ? String(Math.round(value)) : String(value)
        };
      });
    });
    var recentEvents = computed(function () {
      var s = snap.value;
      var events = (s && s.events) || (state.value && state.value.events) || [];
      if (!Array.isArray(events)) return [];
      return events.slice(-4).map(function (ev) {
        return {
          ts: String((ev && ev.ts) || ''),
          summary: String((ev && ev.summary) || '')
        };
      }).filter(function (ev) { return ev.summary; });
    });
    var flags = computed(function () {
      var s = snap.value;
      var list = (s && s.flags) || innerState.value.flags || [];
      if (!Array.isArray(list)) return [];
      return list.slice(-4).map(String);
    });
    var factsCount = computed(function () {
      var s = snap.value;
      if (s && typeof s.facts_count === 'number') return s.facts_count;
      var facts = (state.value && state.value.facts) || [];
      if (!Array.isArray(facts)) return 0;
      return facts.filter(function (f) { return f && f.status === 'active'; }).length;
    });

    function relationBarStyle(rel) {
      var n = Number(rel && rel.value);
      if (!isFinite(n)) return { width: '48%' };
      var pct = Math.max(6, Math.min(100, Math.abs(n)));
      return { width: pct + '%' };
    }

    /* ───────── 流式项辅助 ───────── */
    function makeItem(type, props) {
      var item = Object.assign({ id: ++feedSeq, type: type, stream: false, ts: nowTs() }, props || {});
      return item;
    }
    function pushItem(item) {
      feed.value.push(item);
      return item;
    }
    function findItem(id) {
      for (var i = 0; i < feed.value.length; i++) {
        if (feed.value[i].id === id) return feed.value[i];
      }
      return null;
    }
    function removeItem(id) {
      feed.value = feed.value.filter(function (i) { return i.id !== id; });
    }
    function currentSceneNum() {
      return Number(sceneNo.value) || 0;
    }
    function feedHasIntro() {
      for (var i = 0; i < feed.value.length; i++) {
        if (feed.value[i].type === 'intro') return true;
      }
      return false;
    }
    function pushIntro(content, expanded) {
      if (!content) return;
      if (feedHasIntro()) {
        for (var i = 0; i < feed.value.length; i++) {
          if (feed.value[i].type === 'intro') feed.value[i].content = String(content);
        }
        return;
      }
      feed.value.unshift(makeItem('intro', { content: String(content), expanded: !!expanded }));
    }
    function pushBlock(block, sceneNum) {
      if (!block || !String(block.content || '').trim()) return null;
      var type = block.type === 'dialogue' ? 'dialogue' : (block.type === 'action' ? 'action' : 'narration');
      if (type === 'dialogue') {
        return pushItem(makeItem('dialogue', {
          speaker: String(block.speaker || '旁白'),
          content: String(block.content),
          sceneNum: sceneNum || 0
        }));
      }
      return pushItem(makeItem(type, { content: String(block.content), sceneNum: sceneNum || 0 }));
    }
    function pushBlocksFromMarkup(text, fallbackSpeaker) {
      var blocks = parseMarkupBlocks(text);
      if (!blocks.length) {
        var content = String(text || '').trim();
        if (!content) return;
        blocks = [{ type: blockTypeForSpeaker(fallbackSpeaker), speaker: fallbackSpeaker, content: content }];
      }
      blocks.forEach(function (b) { pushBlock(b, currentSceneNum()); });
    }
    function pushNotice(tone, text, kind) {
      var value = String(text || '').trim();
      if (!value) return;
      if (kind === 'state') {
        var last = feed.value[feed.value.length - 1];
        if (last && last.type === 'notice' && last.kind === 'state') {
          last.text = value;
          return;
        }
      }
      pushItem(makeItem('notice', { tone: tone || 'info', text: value, kind: kind || '' }));
      autoScrollTick();
    }
    function pushCta(text) {
      feed.value = feed.value.filter(function (i) { return i.type !== 'cta'; });
      pushItem(makeItem('cta', { text: String(text || '可以继续下一幕了') }));
      autoScrollTick();
    }
    function pruneStreamingItems() {
      feed.value = feed.value.filter(function (item) {
        if (!item.stream) return true;
        if (item.type !== 'dialogue' && item.type !== 'narration' && item.type !== 'action' && item.type !== 'result') return true;
        return String(item.content || '').trim().length > 0;
      });
    }

    /* ───────── 滚动 ───────── */
    function autoScrollTick() {
      if (!autoScroll.value) return;
      if (scrollTickPending) return;
      scrollTickPending = true;
      var raf = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 16); };
      raf(function () {
        scrollTickPending = false;
        var el = streamEl.value;
        if (el && autoScroll.value) el.scrollTop = el.scrollHeight;
      });
    }
    function scrollToBottom(smooth) {
      nextTick(function () {
        var el = streamEl.value;
        if (!el) return;
        autoScroll.value = true;
        if (smooth && el.scrollTo) {
          scrollLock = true;
          if (scrollLockTimer) clearTimeout(scrollLockTimer);
          scrollLockTimer = setTimeout(function () {
            scrollLock = false;
            scrollLockTimer = null;
          }, 650);
          try { el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }); return; } catch (e) { scrollLock = false; }
        }
        el.scrollTop = el.scrollHeight;
      });
    }
    function onStreamScroll() {
      if (scrollLock) return;
      var el = streamEl.value;
      if (!el) return;
      var distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      autoScroll.value = distance < 90;
    }

    /* ───────── 状态加载 ───────── */
    function setNovelTitle(title) {
      var value = String(title || '').trim();
      if (value) {
        novelTitle.value = value;
        document.title = '《' + value + '》· 小说世界 — 墨·局';
      } else if (!novelTitle.value) {
        document.title = '小说世界 — 墨·局';
      }
    }
    function applyStateMeta(st) {
      if (!st || typeof st !== 'object') return;
      state.value = st;
      snap.value = null;
      if (st.title) setNovelTitle(st.title);
      if (st.genre) novelGenre.value = String(st.genre);
      if (st.style) novelStyle.value = String(st.style);
      if (target.value) {
        var stillPresent = presentCast.value.some(function (c) {
          return c.name === target.value && c.present;
        });
        if (!stillPresent) target.value = '';
      }
    }
    function restoreFeed(st) {
      feed.value = [];
      liveChat = null;
      liveResult = null;
      nodeInfo.value = null;
      feedRestored.value = false;
      if (st && st.intro) pushIntro(st.intro, false);
      var blocks = st && Array.isArray(st.recent_blocks) ? st.recent_blocks.slice(-260) : [];
      blocks.forEach(function (b) {
        var type = String((b && b.type) || 'narration');
        var content = String((b && b.content) || '');
        if (!content.trim()) return;
        if (type === 'dialogue') {
          pushBlock({
            type: blockTypeForSpeaker(b.speaker),
            speaker: String(b.speaker || '旁白'),
            content: content
          }, 0);
          return;
        }
        if (content.indexOf('【') >= 0) {
          var parsed = parseMarkupBlocks(content);
          if (parsed.length) {
            parsed.forEach(function (x) { pushBlock(x, 0); });
            return;
          }
        }
        pushItem(makeItem(type === 'action' ? 'action' : 'narration', { content: content }));
      });
      if (st && st.pending_node) {
        showNode({
          chars: st.node_chars || [],
          reason: st.pending_reason || '',
          agenda: st.agenda || null,
          suggestions: st.suggestions || [],
          snapshot: null
        });
      }
      feedRestored.value = feed.value.length > 0;
    }
    async function loadState(rebuild) {
      var payload;
      try { payload = await apiJson(URLS.state); }
      catch (error) {
        if (error.status !== 404) throw error;
        state.value = null; snap.value = null; nodeInfo.value = null;
        feed.value = []; feedRestored.value = false;
        return false;
      }
      var st = payload && payload.state;
      if (!st || (!st.scene_num && !(st.player_char && st.player_char.name))) {
        state.value = null;
        return false;
      }
      applyStateMeta(st);
      if (rebuild) restoreFeed(st);
      return true;
    }

    async function loadCasts() {
      try {
        var data = await apiJson(URLS.casts);
        var options = Array.isArray(data.options) ? data.options : [];
        castOptions.value = options.map(function (o) {
          return {
            name: String((o && o.name) || ''),
            identity: String((o && o.identity) || ''),
            personality: String((o && o.personality) || ''),
            speak_style: String((o && o.speak_style) || ''),
            role: String((o && o.role) || '')
          };
        }).filter(function (o) { return o.name; });
        if (!startChar.value || !castOptions.value.some(function (o) { return o.name === startChar.value; })) {
          startChar.value = defaultStartChar();
        }
      } catch (e) { /* 角色选项失败不阻塞主流程 */ }
    }
    async function loadCharacters() {
      try {
        var data = await apiJson('/api/roleplay/characters');
        characters.value = Array.isArray(data.characters) ? data.characters : [];
      } catch (e) { characters.value = []; }
    }
    async function loadNovelMeta() {
      if (!novelId) return;
      try {
        var data = await apiJson('/api/novels/' + encodeURIComponent(novelId));
        var novel = (data && data.novel) || {};
        setNovelTitle(novel.title || novel.novel_title || novel.name || '');
        novelGenre.value = String(novel.genre || '');
        novelStyle.value = String(novel.style || '');
      } catch (e) { /* 书名读取失败时保留状态里的标题 */ }
    }
    async function loadActions() {
      if (!hasState.value) { actions.value = []; return; }
      actionsLoading.value = true;
      try {
        var data = await apiJson(URLS.actions);
        actions.value = Array.isArray(data.actions) ? data.actions : [];
      } catch (e) { /* 契约：/actions 失败静默，不挡主流程 */ }
      finally { actionsLoading.value = false; }
    }
    async function refreshActions() {
      if (!hasState.value || busy.value) return;
      await loadActions();
      if (!actions.value.length) toastInfo('暂时没有新的行动建议，可直接输入对话或动作', '行动');
    }
    async function refreshStateNow() {
      if (busy.value) return;
      menuOpen.value = false;
      try {
        var ok = await loadState(false);
        if (!ok) { toastInfo('还没有互动存档', '暂无存档'); return; }
        await loadActions();
        toastSuccess('已同步最新状态', '状态已更新', '🔄');
      } catch (err) {
        toastError((err && err.message) || '同步失败', '同步失败');
      }
    }

    function defaultStartChar() {
      var list = castOptions.value;
      if (!list.length) return '';
      for (var i = 0; i < list.length; i++) {
        if (String(list[i].role || '').toLowerCase() === 'protagonist') return list[i].name;
      }
      return list[0].name;
    }
    function ensureIntroFromState() {
      if (state.value && state.value.intro && !feedHasIntro()) {
        pushIntro(state.value.intro, false);
      }
    }
    function lastSceneBreak() {
      for (var i = feed.value.length - 1; i >= 0; i--) {
        if (feed.value[i].type === 'scene-break') return feed.value[i];
      }
      return null;
    }

    /* ───────── 快照 / 节点 ───────── */
    function applySnapshot(value) {
      if (!value || typeof value !== 'object') return;
      snap.value = Object.assign({}, snap.value || {}, value);
    }
    function showNode(ev) {
      var chars = Array.isArray(ev.chars) ? ev.chars.map(String).filter(Boolean) : [];
      var suggestions = Array.isArray(ev.suggestions) ? ev.suggestions.map(String).filter(Boolean) : [];
      var agenda = ev.agenda && typeof ev.agenda === 'object' ? ev.agenda : null;
      nodeInfo.value = {
        chars: chars,
        reason: String(ev.reason || ''),
        agenda: agenda,
        suggestions: suggestions,
        rounds: Number(ev.suggested_rounds || 0)
      };
      if (ev.snapshot) applySnapshot(ev.snapshot);
      feed.value = feed.value.filter(function (i) { return i.type !== 'node'; });
      pushItem(makeItem('node', {
        chars: chars,
        reason: String(ev.reason || ''),
        agenda: agenda,
        suggestions: suggestions
      }));
      autoScrollTick();
    }
    function useSuggestion(text) {
      var value = String(text || '').trim();
      if (!value) return;
      draft.value = value;
      if (!target.value && nodeInfo.value && nodeInfo.value.chars.length === 1) {
        target.value = nodeInfo.value.chars[0];
      }
      nextTick(function () {
        autoGrow();
        if (draftEl.value) draftEl.value.focus();
      });
    }
    function dismissItem(item) {
      if (!item) return;
      feed.value = feed.value.filter(function (i) { return i.id !== item.id; });
    }

    /* ───────── 场景流（start / scene） ───────── */
    function commitScene(ev) {
      var num = Number(ev.scene_num) || sceneStream.sceneNum || (currentSceneNum() + 1);
      var place = (ev.snapshot && ev.snapshot.location) || locationLabel.value;
      feed.value = feed.value.filter(function (i) { return i.type !== 'cta'; });
      pushItem(makeItem('scene-break', { sceneNum: num, place: String(place || '') }));
      var blocks = Array.isArray(ev.blocks) ? ev.blocks : [];
      blocks.forEach(function (b) { pushBlock(b, num); });
      if (ev.snapshot) applySnapshot(ev.snapshot);
      autoScrollTick();
    }
    function sceneEventHandler(ev, ctx) {
      if (!ev || !ev.type) return;
      var type = ev.type;
      if (type === 'phase' || type === 'scene_retry' || type === 'scene_chunk') {
        NovelWorldRuntime.updateDraft(sceneStream, ev);
        autoScrollTick();
        return;
      }
      if (type === 'scene_end') {
        commitScene(ev);
        sceneStream.active = false;
        sceneStream.text = '';
        return;
      }
      if (type === 'intro') {
        pushIntro(ev.content, true);
        return;
      }
      if (type === 'node_check') {
        showNode(ev);
        return;
      }
      if (type === 'error') {
        ctx.error = String(ev.message || '场景生成失败');
        return false;
      }
      if (type === 'done') return false;
    }

    async function runSceneLike(kind, url, body) {
      busy.value = true;
      streamKind.value = kind;
      sceneStream.active = true;
      sceneStream.phase = kind === 'start' ? '正在开启故事…' : '正在铺开下一幕…';
      sceneStream.text = '';
      sceneStream.sceneNum = currentSceneNum() + 1;
      var controller = new AbortController();
      abortCtrl = controller;
      var ctx = { error: '', aborted: false };

      try {
        var init = {
          method: 'POST',
          headers: authHeaders({ 'Accept': 'text/event-stream' }),
          signal: controller.signal
        };
        if (body !== undefined && body !== null) {
          init.headers['Content-Type'] = 'application/json';
          init.body = JSON.stringify(body);
        }
        var response = await fetch(url, init);
        await readSSE(response, function (ev) { return sceneEventHandler(ev, ctx); }, controller);
      } catch (err) {
        if (err && err.name === 'AbortError') ctx.aborted = true;
        else ctx.error = (err && err.message) || '网络异常，请稍后重试';
      } finally {
        abortCtrl = null;
        sceneStream.active = false;
        sceneStream.text = '';
        sceneStream.phase = '';
        streamKind.value = '';
        feed.value.forEach(function (i) { i.stream = false; });

        if (ctx.error) {
          pushNotice('error', ctx.error, kind === 'start' ? 'start' : 'scene');
          toastError(ctx.error, kind === 'start' ? '进入失败' : '生成失败');
          if (kind === 'start') {
            var startOk = false;
            try { startOk = await loadState(false); } catch (e) { startOk = false; }
            if (startOk) {
              ensureIntroFromState();
              pushCta('开场场景生成失败 · 可以再试一次继续剧情');
            } else {
              startError.value = ctx.error;
            }
          }
        } else if (ctx.aborted) {
          toastInfo('已停止生成，已生成的内容会保留', '已停止');
        } else {
          try { await loadState(false); } catch (e) { /* 状态刷新失败不遮场景 */ }
          if (kind === 'start') {
            ensureIntroFromState();
            scheduleIntroPoll(0);
          }
          try { await loadActions(); } catch (e) { /* 已被内部吞掉 */ }
          var latest = lastSceneBreak();
          if (latest) {
            pushCta('第 ' + latest.sceneNum + ' 幕已就绪 · 可与在场角色对话，或继续推进');
          }
        }

        busy.value = false;
        autoScrollTick();
      }
    }

    function scheduleIntroPoll(attempt) {
      if (introTimer) { clearTimeout(introTimer); introTimer = null; }
      if (attempt >= 3 || feedHasIntro()) return;
      var delays = [2600, 7000, 15000];
      introTimer = setTimeout(async function () {
        introTimer = null;
        try {
          var response = await fetch(URLS.state, { headers: authHeaders({ 'Accept': 'application/json' }) });
          if (response.ok) {
            var payload = await response.json();
            var st = payload && payload.state;
            if (st && st.title && !novelTitle.value) setNovelTitle(st.title);
            if (st && !feedHasIntro() && st.intro) pushIntro(st.intro, false);
          }
        } catch (e) { /* 开场背景轮询失败可忽略 */ }
        if (!feedHasIntro()) scheduleIntroPoll(attempt + 1);
      }, delays[Math.min(attempt, delays.length - 1)]);
    }

    async function startInteractive() {
      if (startBusy.value || busy.value) return;
      if (startOptions.value.length > 0 && !startChar.value) {
        toastInfo('请先选择一个要扮演的角色', '选择角色');
        return;
      }
      startBusy.value = true;
      startError.value = '';
      menuOpen.value = false;
      try {
        var body = startChar.value ? { character: startChar.value } : {};
        await runSceneLike('start', URLS.start, body);
      } finally {
        startBusy.value = false;
      }
    }
    async function continueScene() {
      if (busy.value || !hasState.value) return;
      autoScroll.value = true;
      await runSceneLike('scene', URLS.scene, null);
    }

    /* ───────── 对话流（chat / act 共用） ───────── */
    function appendLiveChat(speaker, chunk) {
      var key = String(speaker || '旁白').trim() || '旁白';
      if (!liveChat || liveChat.key !== key || !findItem(liveChat.id)) {
        if (liveChat) {
          var prev = findItem(liveChat.id);
          if (prev) prev.stream = false;
        }
        var type = blockTypeForSpeaker(key);
        var item;
        if (type === 'narration') item = makeItem('narration', { content: '', stream: true });
        else if (type === 'action') item = makeItem('action', { content: '', stream: true });
        else item = makeItem('dialogue', { speaker: key, content: '', stream: true });
        pushItem(item);
        liveChat = { id: item.id, key: key, type: type };
        autoScrollTick();
      }
      var live = findItem(liveChat.id);
      if (live) live.content += chunk;
      if (chunk) autoScrollTick();
    }
    function finalizeChat(ev) {
      var speaker = String(ev.speaker || '旁白');
      var content = NovelWorldRuntime.content(ev.content);
      var segments = Array.isArray(ev.segments) ? ev.segments.filter(function (s) {
        return s && String(s.content || '').trim();
      }) : [];
      var live = liveChat;
      liveChat = null;
      var partial = '';
      if (live) {
        var item = findItem(live.id);
        partial = item ? String(item.content || '') : '';
        removeItem(live.id);
      }
      feed.value = feed.value.filter(function (i) {
        return !(i.stream && (i.type === 'dialogue' || i.type === 'narration' || i.type === 'action') && !String(i.content || '').trim());
      });

      if (segments.length > 1) {
        segments.forEach(function (seg) {
          var sp = String(seg.speaker || speaker);
          pushBlock({ type: blockTypeForSpeaker(sp), speaker: sp, content: String(seg.content || '') }, currentSceneNum());
        });
      } else if (segments.length === 1) {
        var sp1 = String(segments[0].speaker || speaker);
        pushBlock({ type: blockTypeForSpeaker(sp1), speaker: sp1, content: String(segments[0].content || '') }, currentSceneNum());
      } else if (content.trim()) {
        pushBlocksFromMarkup(content, speaker);
      } else if (partial.trim()) {
        pushBlock({ type: blockTypeForSpeaker(speaker), speaker: speaker, content: partial }, currentSceneNum());
      }
      autoScrollTick();
    }
    function appendLiveResult(chunk) {
      if (!liveResult || !findItem(liveResult.id)) {
        if (liveResult) {
          var old = findItem(liveResult.id);
          if (old) old.stream = false;
        }
        var item = pushItem(makeItem('result', { content: '', stream: true }));
        liveResult = { id: item.id };
        autoScrollTick();
      }
      var live = findItem(liveResult.id);
      if (live) live.content += chunk;
      if (chunk) autoScrollTick();
    }
    function finalizeResult(content) {
      var live = liveResult;
      liveResult = null;
      var partial = '';
      if (live) {
        var item = findItem(live.id);
        partial = item ? String(item.content || '') : '';
        removeItem(live.id);
      }
      var text = String(content || partial || '').trim();
      if (!text) return;
      var blocks = parseMarkupBlocks(text);
      var hasDialogue = blocks.some(function (b) {
        return blockTypeForSpeaker(b.speaker) === 'dialogue';
      });
      if (hasDialogue) {
        blocks.forEach(function (b) { pushBlock(b, currentSceneNum()); });
      } else {
        var merged = blocks.map(function (b) { return String(b.content || ''); }).filter(Boolean).join('\n').trim() || text;
        pushItem(makeItem('result', { content: merged, sceneNum: currentSceneNum() }));
      }
      autoScrollTick();
    }
    function chatEventHandler(ev, ctx) {
      if (!ev || !ev.type) return;
      var type = ev.type;
      if (type === 'phase') {
        chatStream.phase = String(ev.label || '');
        return;
      }
      if (type === 'chat_chunk') {
        appendLiveChat(ev.speaker, NovelWorldRuntime.content(ev.content));
        return;
      }
      if (type === 'chat_end') {
        finalizeChat(ev);
        return;
      }
      if (type === 'action_detect') {
        var summary = String(ev.summary || '');
        chatStream.phase = summary ? ('行动：' + shorten(summary, 18)) : '正在执行行动…';
        if (ev.blocked) {
          pushNotice('warn', '这个行动暂时无法执行' + (summary ? '：' + summary : '。'), 'chat');
        }
        return;
      }
      if (type === 'action_chunk') {
        appendLiveResult(NovelWorldRuntime.content(ev.content));
        return;
      }
      if (type === 'action_end') {
        finalizeResult(ev.content);
        applySnapshot(ev.snapshot);
        return;
      }
      if (type === 'action_done') {
        applySnapshot(ev.snapshot);
        if (ev.end_chat && !ctx.endPrompt) {
          ctx.endPrompt = true;
          pushCta('行动改变了场景 · 可以继续下一幕');
        }
        return;
      }
      if (type === 'state_change') {
        applySnapshot(ev.snapshot);
        var changes = Array.isArray(ev.changes) ? ev.changes.map(String).filter(Boolean) : [];
        if (changes.length) pushNotice('info', changes.join('；'), 'state');
        return;
      }
      if (type === 'drift_check') {
        if (ev.on_track === false) {
          pushNotice('warn', String(ev.reason || '剧情有些跑偏，注意回到主线。'), 'chat');
        }
        return;
      }
      if (type === 'natural_end') {
        if (!ctx.endPrompt) {
          ctx.endPrompt = true;
          pushCta(String(ev.message || '这段对话可以自然收尾了'));
        }
        return;
      }
      if (type === 'error') {
        ctx.error = String(ev.message || '生成失败');
        return false;
      }
      if (type === 'done') return false;
    }
    async function consumeStream(url, body, kind) {
      busy.value = true;
      streamKind.value = kind;
      chatStream.active = true;
      chatStream.phase = kind === 'act' ? '正在执行行动…' : '角色正在回应…';
      liveChat = null;
      liveResult = null;
      var controller = new AbortController();
      abortCtrl = controller;
      var ctx = { error: '', aborted: false, endPrompt: false };

      try {
        var init = {
          method: 'POST',
          headers: authHeaders({ 'Accept': 'text/event-stream' }),
          signal: controller.signal
        };
        if (body !== undefined && body !== null) {
          init.headers['Content-Type'] = 'application/json';
          init.body = JSON.stringify(body);
        }
        var response = await fetch(url, init);
        await readSSE(response, function (ev) { return chatEventHandler(ev, ctx); }, controller);
      } catch (err) {
        if (err && err.name === 'AbortError') ctx.aborted = true;
        else ctx.error = (err && err.message) || '网络异常，请稍后重试';
      } finally {
        abortCtrl = null;
        chatStream.active = false;
        chatStream.phase = '';
        pruneStreamingItems();
        feed.value.forEach(function (i) { i.stream = false; });
        liveChat = null;
        liveResult = null;
        streamKind.value = '';

        if (ctx.error) {
          pushNotice('error', ctx.error, kind === 'act' ? 'act' : 'chat');
          toastError(ctx.error, kind === 'act' ? '行动失败' : '生成失败');
        } else if (ctx.aborted) {
          toastInfo('已停止生成，已生成的内容会保留', '已停止');
        }

        try { await loadState(false); } catch (e) { /* 忽略 */ }
        try { await loadActions(); } catch (e) { /* 忽略 */ }
        busy.value = false;
        autoScrollTick();
      }
    }
    async function sendChat() {
      if (busy.value || !hasState.value) return;
      var raw = String(draft.value || '').trim();
      if (!raw) return;
      var tgt = target.value;

      /* 支持在输入框直接以 @名字 开头指定目标（仅在场角色） */
      var match = /^@([^\s@]{1,24})\s+([\s\S]+)$/.exec(raw);
      if (match) {
        var candidate = match[1];
        var hit = null;
        for (var i = 0; i < castCards.value.length; i++) {
          if (castCards.value[i].name === candidate) { hit = castCards.value[i]; break; }
        }
        if (hit) {
          tgt = hit.name;
          raw = match[2].trim();
        }
      }
      if (!raw) return;

      var mode = inputMode.value;
      target.value = tgt || '';
      pushItem(makeItem('user', {
        mode: mode,
        speaker: playerName.value,
        content: raw,
        target: tgt || ''
      }));
      draft.value = '';
      targetPickerOpen.value = false;
      menuOpen.value = false;
      autoGrow();
      autoScroll.value = true;
      scrollToBottom();

      var payload = { message: raw };
      if (tgt) {
        payload.target = tgt;
        if (mode === 'dialogue') payload.force_talk = true;
      }
      await consumeStream(URLS.chat, payload, 'chat');
    }
    function onAction(action) {
      if (!action || busy.value || !hasState.value) return;
      if (action.intent === 'talk') {
        if (isPanelOverlay.value) closePanel();
        target.value = String(action.target || '');
        targetPickerOpen.value = false;
        inputMode.value = 'dialogue';
        focusComposer();
        toastInfo('已指定「' + (action.target || '角色') + '」，输入你想说的话', '对话目标');
        return;
      }
      if (isPanelOverlay.value) closePanel();
      var label = String(action.label || action.summary || '行动');
      pushItem(makeItem('user', {
        mode: 'action',
        speaker: playerName.value,
        content: label,
        target: action.target || ''
      }));
      target.value = '';
      autoScroll.value = true;
      scrollToBottom();
      var payload = {
        action_id: action.id || null,
        intent: action.intent || 'act',
        target: action.target || null,
        message: ''
      };
      consumeStream(URLS.act, payload, 'act');
    }

    /* ───────── 存档操作 ───────── */
    async function restart() {
      if (busy.value) return;
      if (!hasState.value) { toastInfo('当前还没有互动存档', '无需重开'); return; }
      var ok = await askConfirm(
        '重开本局',
        '将清空当前互动存档（旧存档会自动备份），并回到「选择扮演角色」。确定重开吗？',
        { confirmText: '重开' }
      );
      if (!ok) return;
      menuOpen.value = false;
      busy.value = true;
      try {
        await apiJson(URLS.restart, { method: 'POST' });
        feed.value = [];
        feedRestored.value = false;
        state.value = null;
        snap.value = null;
        nodeInfo.value = null;
        startError.value = '';
        target.value = '';
        targetPickerOpen.value = false;
        startChar.value = defaultStartChar();
        try { await loadState(false); } catch (e) { /* 404 正常 */ }
        try { await loadActions(); } catch (e) { /* 忽略 */ }
        try { await loadCasts(); } catch (e) { /* 忽略 */ }
        if (panelOpen.value && isPanelOverlay.value) panelOpen.value = false;
        toastSuccess('互动存档已重置，请选择角色重新进入', '已重开', '↺');
        nextTick(function () { if (streamEl.value) streamEl.value.scrollTop = 0; });
      } catch (err) {
        toastError((err && err.message) || '重开失败', '重开失败');
      } finally {
        busy.value = false;
      }
    }
    async function rollback() {
      if (busy.value) return;
      if (!hasState.value) { toastInfo('当前还没有互动存档', '无法回退'); return; }
      var ok = await askConfirm(
        '回退一步',
        '将回退到最近一次快照（场景生成或对话之前）。确定回退吗？',
        { confirmText: '回退' }
      );
      if (!ok) return;
      menuOpen.value = false;
      busy.value = true;
      try {
        await apiJson(URLS.rollback, { method: 'POST' });
        var restored = await loadState(true);
        if (!restored) {
          feed.value = [];
          toastInfo('回退后存档为空', '已回退');
        } else {
          await loadActions();
          scrollToBottom();
          toastSuccess('已回退到上一个快照', '回退成功', '⏪');
        }
      } catch (err) {
        toastError((err && err.message) || '没有可回退的快照', '回退失败');
      } finally {
        busy.value = false;
      }
    }
    async function reloadAll() {
      initError.value = '';
      initializing.value = true;
      try {
        await Promise.all([loadCasts(), loadCharacters(), loadNovelMeta()]);
        var ok = await loadState(true);
        if (!ok) {
          startChar.value = defaultStartChar();
        } else {
          await loadActions();
          scrollToBottom();
        }
      } catch (err) {
        initError.value = (err && err.message) || '无法载入互动存档';
      } finally {
        initializing.value = false;
      }
    }

    /* ───────── 交互辅助 ───────── */
    function setInputMode(mode) {
      inputMode.value = mode === 'action' ? 'action' : 'dialogue';
      focusComposer();
    }
    function focusComposer() {
      nextTick(function () {
        if (draftEl.value) draftEl.value.focus();
      });
    }
    function toggleTargetPicker() {
      if (!presentCast.value.length) {
        toastInfo('此刻没有可对话的角色，先观察环境或继续剧情吧', '暂无对象');
        return;
      }
      targetPickerOpen.value = !targetPickerOpen.value;
      menuOpen.value = false;
    }
    function pickTarget(name) {
      target.value = String(name || '');
      targetPickerOpen.value = false;
      focusComposer();
    }
    function clearTarget() {
      target.value = '';
      targetPickerOpen.value = false;
    }
    function onRailCast(card) {
      if (!card) return;
      if (!card.present) {
        toastInfo(card.name + ' 此刻不在场，先移动到 Ta 所在的地点吧', '不在场');
        return;
      }
      pickTarget(card.name);
    }
    function startPanelTip() {
      toastInfo('先在启动面板里选择角色并点击「进入小说」', '提示');
    }
    function autoGrow() {
      var el = draftEl.value;
      if (!el) return;
      el.style.height = 'auto';
      var height = Math.min(176, Math.max(38, el.scrollHeight));
      el.style.height = height + 'px';
      el.style.overflowY = el.scrollHeight > 176 ? 'auto' : 'hidden';
    }
    function onComposerKeydown(event) {
      if (!event) return;
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        sendChat();
      }
    }
    function stopStream() {
      if (abortCtrl) {
        try { abortCtrl.abort(); } catch (e) { /* 忽略 */ }
      }
    }

    /* ───────── 主题 / 布局 ───────── */
    function toggleTheme() {
      theme.value = theme.value === 'dark' ? 'light' : 'dark';
    }
    function setTheme(value) {
      theme.value = value === 'light' ? 'light' : 'dark';
    }
    function setFontSize(value) {
      fontSize.value = value === 'lg' ? 'lg' : 'md';
    }
    function toggleMenu() {
      menuOpen.value = !menuOpen.value;
      if (menuOpen.value) targetPickerOpen.value = false;
    }
    function openPanel() {
      panelOpen.value = true;
      menuOpen.value = false;
      targetPickerOpen.value = false;
    }
    function closePanel() {
      panelOpen.value = false;
    }
    function onResize() {
      viewportW.value = window.innerWidth;
      var desktop = viewportW.value >= 1180;
      if (desktop !== lastDesktopLayout) {
        lastDesktopLayout = desktop;
        panelOpen.value = desktop;
      }
    }

    /* ───────── Toast / 确认框 ───────── */
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

    /* ───────── 全局事件 ───────── */
    function onGlobalKeydown(event) {
      if (!event || event.key !== 'Escape') return;
      if (confirmBox.visible) { resolveConfirm(false); return; }
      if (menuOpen.value) { menuOpen.value = false; return; }
      if (targetPickerOpen.value) { targetPickerOpen.value = false; return; }
      if (panelOpen.value && isPanelOverlay.value) { closePanel(); return; }
    }
    function onDocumentClick() {
      if (menuOpen.value) menuOpen.value = false;
      if (targetPickerOpen.value) targetPickerOpen.value = false;
    }
    function onUnhandledRejection(event) {
      var reason = event && event.reason;
      var message = reason && (reason.message || reason);
      if (message) toastError(String(message), '未处理的异常');
    }

    /* ───────── 监听 ───────── */
    watch(theme, function (value) { applyThemeValue(value); });
    watch(fontSize, function (value) {
      safeStorageSet('scene_fontsize', value === 'lg' ? 'lg' : 'md');
    });

    /* ───────── 生命周期 ───────── */
    onMounted(async function () {
      applyThemeValue(theme.value);
      window.addEventListener('resize', onResize);
      window.addEventListener('keydown', onGlobalKeydown);
      document.addEventListener('click', onDocumentClick);
      window.addEventListener('unhandledrejection', onUnhandledRejection);
      onResize();
      autoGrow();

      if (!novelId) {
        initializing.value = false;
        return;
      }
      try {
        await Promise.all([loadCasts(), loadCharacters(), loadNovelMeta()]);
        var ok = await loadState(true);
        if (!ok) {
          startChar.value = defaultStartChar();
        } else {
          await loadActions();
          nextTick(function () { scrollToBottom(); });
        }
      } catch (err) {
        initError.value = (err && err.message) || '无法载入互动存档';
      } finally {
        initializing.value = false;
        nextTick(function () { autoGrow(); scrollToBottom(); });
      }
    });

    onUnmounted(function () {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('keydown', onGlobalKeydown);
      document.removeEventListener('click', onDocumentClick);
      window.removeEventListener('unhandledrejection', onUnhandledRejection);
      if (abortCtrl) {
        try { abortCtrl.abort(); } catch (e) { /* 忽略 */ }
      }
      if (introTimer) { clearTimeout(introTimer); introTimer = null; }
      if (scrollLockTimer) { clearTimeout(scrollLockTimer); scrollLockTimer = null; }
    });

    /* ───────── 暴露给模板 ───────── */
    return {
      /* 顶栏 */
      theme: theme,
      fontSize: fontSize,
      toggleTheme: toggleTheme,
      setTheme: setTheme,
      setFontSize: setFontSize,
      menuOpen: menuOpen,
      toggleMenu: toggleMenu,
      novelId: novelId, headerTitle: headerTitle,
      hasState: hasState,
      sceneLabel: sceneLabel,
      sceneNo: sceneNo,
      locationLabel: locationLabel,
      timeLabel: timeLabel,
      presentCount: presentCount,
      novelId: novelId,
      novelTitle: novelTitle,
      novelGenre: novelGenre,
      novelStyle: novelStyle,
      appStyle: appStyle,
      /* 角色 */
      playerName: playerName,
      playerInitial: playerInitial,
      playerIdentity: playerIdentity,
      objective: objective,
      avatarSrc: avatarSrc,
      charInitial: charInitial,
      charTone: charTone,
      roleLabel: roleLabel,
      castSubline: castSubline,
      castCards: castCards,
      presentCast: presentCast,
      absentCast: absentCast,
      onRailCast: onRailCast,
      startPanelTip: startPanelTip,
      /* 启动 / 载入 */
      initializing: initializing,
      initError: initError,
      reloadAll: reloadAll,
      castOptions: castOptions,
      startOptions: startOptions,
      startChar: startChar,
      startBusy: startBusy,
      startError: startError,
      startInteractive: startInteractive,
      /* 场景流 */
      feed: feed, visibleFeed: visibleFeed, earlierCount: earlierCount, showEarlier: showEarlier,
      feedRestored: feedRestored,
      sceneStream: sceneStream,
      scenePreview: scenePreview,
      streamKind: streamKind,
      streamPhase: streamPhase,
      busy: busy,
      continueScene: continueScene,
      stopStream: stopStream,
      cleanText: cleanText,
      noticeIcon: noticeIcon,
      useSuggestion: useSuggestion,
      dismissItem: dismissItem,
      /* 对话输入 */
      draft: draft,
      draftEl: draftEl,
      inputMode: inputMode,
      setInputMode: setInputMode,
      target: target,
      targetPickerOpen: targetPickerOpen,
      toggleTargetPicker: toggleTargetPicker,
      pickTarget: pickTarget,
      clearTarget: clearTarget,
      composerHint: composerHint,
      composerPlaceholder: composerPlaceholder,
      canSend: canSend,
      sendChat: sendChat,
      onComposerKeydown: onComposerKeydown,
      autoGrow: autoGrow,
      /* 行动 / 状态 */
      actions: actions,
      actionsLoading: actionsLoading,
      refreshActions: refreshActions,
      onAction: onAction,
      intentLabel: intentLabel,
      relationsList: relationsList,
      relationBarStyle: relationBarStyle,
      recentEvents: recentEvents,
      flags: flags,
      factsCount: factsCount,
      nodeInfo: nodeInfo,
      /* 面板 / 滚动 */
      streamEl: streamEl,
      onStreamScroll: onStreamScroll,
      scrollToBottom: scrollToBottom,
      showJump: showJump,
      autoScroll: autoScroll,
      panelOpen: panelOpen,
      openPanel: openPanel,
      closePanel: closePanel,
      isPanelOverlay: isPanelOverlay,
      /* 存档 */
      restart: restart,
      rollback: rollback,
      refreshStateNow: refreshStateNow,
      /* 弹窗 / Toast */
      confirmBox: confirmBox,
      resolveConfirm: resolveConfirm,
      toasts: toasts,
      dismissToast: dismissToast
    };
  }

  /* ═════════════ 小组件 ═════════════ */

  app.component('sc-avatar', {
    props: {
      name: { type: String, default: '' },
      size: { type: String, default: 'md' },
      src: { type: String, default: '' },
      initial: { type: String, default: '' },
      tone: { type: Number, default: 0 },
      user: { type: Boolean, default: false }
    },
    data: function () { return { failed: false }; },
    computed: {
      sizeClass: function () {
        return ['xs', 'sm', 'md', 'lg'].indexOf(this.size) >= 0 ? this.size : 'md';
      },
      initialText: function () {
        return this.initial || String(this.name || '？').trim().charAt(0) || '？';
      }
    },
    watch: {
      src: function () { this.failed = false; }
    },
    template: '#sc-avatar-tpl'
  });

  app.component('md-text', {
    props: { content: { type: String, default: '' } },
    computed: {
      tokens: function () { return mdTokens(this.content); }
    },
    template: '#sc-md-tpl'
  });

  NovelApp.register(app);
  app.mount('#app');
})();
