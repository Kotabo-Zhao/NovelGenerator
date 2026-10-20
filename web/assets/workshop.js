const API = window.location.origin;
const { createApp, ref, reactive, computed, watch, nextTick, onMounted, onUnmounted, onErrorCaptured } = Vue;

const app = createApp({
  setup() {
    try {
    const requestedView = new URLSearchParams(window.location.search).get('view');
    const routeViews = ['shelf','create','write','settings','export','worlds'];
    const currentView = ref(routeViews.includes(requestedView) ? requestedView : 'shelf');
    const _returnView = ref('shelf');          // bible/stats/ideas 返回来源
    const bibleData = ref({characters:{},locations:{},terms:{},relations:[]});
    const bibleTab = ref('characters');
    const bibleLoading = ref(false);
    const termCandidates = ref([]);
    // v2.0: 圣经 CRUD 编辑状态
    const bibleEdit = reactive({active:false, kind:'character', name:'', form:{}});
    const bibleNewKind = ref('character');
    const statsData = ref(null);
    const statsLoading = ref(false);
    // v2.0: 每日趋势折线计算
    const trendW = 600, trendH = 140, trendPad = 10;
    const trendPoints = computed(() => {
      const daily = (statsData.value?.daily) || [];
      if (daily.length < 2) return '';
      const max = Math.max(...daily.map(d => d.chars), 1);
      const stepX = (trendW - trendPad*2) / Math.max(daily.length - 1, 1);
      return daily.map((d, i) => {
        const x = trendPad + i*stepX;
        const y = trendH - trendPad - (d.chars/max)*(trendH - trendPad*2);
        return x.toFixed(1)+','+y.toFixed(1);
      }).join(' ');
    });
    const trendMax = computed(() => Math.max(...((statsData.value?.daily)||[]).map(d => d.chars), 0));
    const ideasData = ref([]); const ideasTags = ref([]);
    const ideasTab = ref('ideas');             // 'ideas' | 'materials'
    const materialsData = ref([]);
    const ideasLoading = ref(false);
    // v2.0: 统一弹窗（替代 prompt/confirm）
    const modal = reactive({visible:false, title:'', kind:'input', label:'', value:'', rows:1,
                            confirmText:'确定', onConfirm:null, textarea:false});
    function openModal(opts) {
      modal.visible = true; modal.title = opts.title || '';
      modal.kind = opts.kind || 'input';
      modal.label = opts.label || '';
      modal.value = opts.value || '';
      modal.rows = opts.rows || 1;
      modal.textarea = !!opts.textarea;
      modal.confirmText = opts.confirmText || '确定';
      modal.onConfirm = opts.onConfirm || null;
    }
    function closeModal() { modal.visible = false; modal.onConfirm = null; }
    function confirmModal() {
      const cb = modal.onConfirm;
      closeModal();
      if (cb) cb(modal.value);
    }

    const novels = ref([]); const activeNovel = ref(null);
    const planResult = ref(null); const error = ref(''); const preview = ref(null);
    const toast = reactive({ visible:false, title:'', message:'', icon:'⚠️' });
    const abortCtrl = ref(null);
    const styleCategories = ref({}); const exportSelected = reactive({});
    const editSaved = ref(false); const styleMode = ref('library');
    const customStyleDesc = ref(''); const styleSeeds = ref([]);
    const styleParams = ref({
      narrative_person:{label:'叙事人称',options:[
        {value:'first',label:'第一人称（我）'},{value:'third_limited',label:'第三人称限知'},{value:'third_omniscient',label:'第三人称全知'}
      ]},
      tone:{label:'文风基调',options:[
        {value:'hot_blood',label:'热血昂扬'},{value:'calm',label:'冷静克制'},{value:'humorous',label:'幽默轻松'},{value:'dark',label:'暗黑沉重'},{value:'warm',label:'温馨治愈'}
      ]},
      pacing:{label:'节奏控制',options:[
        {value:'fast',label:'快节奏'},{value:'balanced',label:'张弛有度'},{value:'slow',label:'舒缓细腻'}
      ]},
      dialogue_ratio:{label:'对话比例',options:[
        {value:'low',label:'少对话'},{value:'medium',label:'叙事对话均衡'},{value:'high',label:'对话驱动'}
      ]},
      description_density:{label:'描写密度',options:[
        {value:'sparse',label:'简洁留白'},{value:'moderate',label:'适中'},{value:'rich',label:'浓墨重彩'}
      ]},
      emotion_tendency:{label:'情感倾向',options:[
        {value:'positive',label:'积极向上'},{value:'bittersweet',label:'悲喜交加'},{value:'tragic',label:'悲剧底色'},{value:'neutral',label:'冷静旁观'}
      ]},
      length_preference:{label:'篇幅偏好',options:[
        {value:'short',label:'短篇精炼'},{value:'medium',label:'中篇展开'},{value:'long',label:'长篇铺陈'}
      ]}
    }); const customParams = reactive({
      narrative_person:'third_limited', tone:'hot_blood', pacing:'balanced',
      dialogue_ratio:'medium', description_density:'moderate',
      emotion_tendency:'positive', length_preference:'medium', keyword:''
    });
    const requirements = reactive({subtasks:[]}); const reqInspiration = ref(''); const showReqInput = ref(false);
    const supervisionReport = ref(null);
    const theme = ref(NovelUI.storage.get('theme', 'light'));
    const uiStyle = ref(NovelUI.storage.get('uiStyle', 'macOS'));
    const health = ref(null);  // v2.7: 设置页服务状态
    // v2.7+: 设置页 LLM 配置（供应商预设 + OpenAI-compatible + Dots）
    const llmProviders = ref([]);
    const llmProvider = ref('deepseek');
    const llmProviderMeta = computed(() => llmProviders.value.find(p => p.id === llmProvider.value) || null);
    const llmApiKey = ref('');
    const llmBaseUrl = ref('https://api.deepseek.com');
    const llmModel = ref('deepseek-v4-flash');
    const llmCustomModel = ref('');
    const llmCustomModelMode = ref(false);
    const llmSaving = ref(false);
    const llmTesting = ref(false);
    const llmKeySaved = ref(false);
    const llmTested = ref(false);
    const llmEffectiveModel = computed(() => llmCustomModelMode.value ? String(llmCustomModel.value || '').trim() : llmModel.value);

    const createProgress = ref(0); const createPhase = ref(''); const createPhaseLabel = ref('');
    const decomposeSummary = ref(null);  // v2.2: 需求拆解摘要
    const outlineChangePlan = ref([]);
    const outlineExecutionSummary = ref(null);
    const outlineLoading = ref(false);
    const requirementKindOrder = ['book_invariant','milestone','chapter_event','planning_only','preference'];
    const requirementSnapshot = computed(() => {
      const fresh = decomposeSummary.value;
      if (fresh && Array.isArray(fresh.subtasks) && fresh.subtasks.length) return fresh;
      return requirements;
    });
    function requirementsForKind(kind) {
      const list = requirementSnapshot.value?.subtasks;
      return Array.isArray(list) ? list.filter(item => item && (item.kind || 'book_invariant') === kind) : [];
    }
    function requirementKindLabel(kind) {
      return ({
        book_invariant:'全书规则', milestone:'剧情里程碑', chapter_event:'章节事件',
        planning_only:'仅大纲规划', preference:'风格偏好'
      })[kind] || '全书规则';
    }
    function requirementKindHelp(kind) {
      return ({
        book_invariant:'人物、世界规则和禁写内容；每章保持一致，但不要求逐字重复。',
        milestone:'转折、高潮和伏笔回收；只在安排的进度窗口内生效。',
        chapter_event:'明确绑定到某一章的事件，是该章必须完成的硬任务。',
        planning_only:'卷数、章数和总体结构；只用于蓝图与大纲，不会塞入正文。',
        preference:'文风、节奏和对话倾向；作为软质量目标，不阻塞章节完成。'
      })[kind] || '';
    }
    function requirementScopeText(item) {
      const chapters = Array.isArray(item?.chapter_numbers) ? item.chapter_numbers : [];
      if (chapters.length) return '第' + chapters.join('、') + '章';
      return ({global:'全书',opening:'开篇',mid:'中段',ending:'结局',chapter:'指定章节'})[item?.scope] || '待分配';
    }
    // v2.3: 按 category 分组子任务（带防御）
    function groupedSubtasks(cat) {
      try {
        if (!decomposeSummary.value) return [];
        const list = decomposeSummary.value.subtasks;
        if (!Array.isArray(list) || !list.length) return [];
        return list.filter(s => s && s.category === cat);
      } catch(e) { console.warn('groupedSubtasks error:', e); return []; }
    }
    const reviewFeedback = ref(''); const batchStreaming = ref('');
    const chapterProgress = ref(0); const chapterProgressLabel = ref('');
    const outlineCompliance = ref({}); // v3.10: 大纲合规徽章（chapter/pct/level/message/missing）
    const outlineFeedback = ref(''); const isRegenerating = ref(false);

    // ── 全局状态锁：任何生成操作进行时阻断其他操作 ──
    const isBusy = computed(() => loading.create || loading.write || loading.batch || isRegenerating.value);

    // ── 大纲结构校验：防止 plan 字段缺失导致渲染崩溃 ──
    const planReady = computed(() => {
      const p = planResult.value;
      if (!p) return false;
      // 确认嵌套结构都存在
      if (!p.worldbuilding || typeof p.worldbuilding !== 'object') return false;
      if (!p.characters || !p.characters.protagonist || typeof p.characters.protagonist !== 'object') return false;
      if (!p.outline || !Array.isArray(p.outline.volumes)) return false;
      return true;
    });

    // ── SSE 安全读取器（含超时保护）──
    /* ── 错误弹窗 ── */
    function showToast(msg, title='提示', icon='⚠️') {
      toast.title = title; toast.message = msg; toast.icon = icon; toast.visible = true;
    }
    function reportError(msg, title='操作异常') {
      error.value = msg; showToast(msg, title, '⚠️');
    }

    const readSSE = NovelUI.readSSE;

    const form = reactive({ genre:'修仙', style:'热血爽文', inspiration:'', target_words:500000, title:'', naturalNames:true, normalPacing:false, fastFood:false, creativeEnhance:false });
    const loading = reactive({ shelf:false, create:false, write:false, export:false, saving:false, batch:false, xhs:false });

    // v2.50: XHS 短篇模式
    const xhsMode = ref(true);  // 默认展开
    const xhsTemplate = ref('爽文_打脸逆袭');
    const xhsInspiration = ref('');
    const xhsResult = ref(null);
    const xhsTemplates = ref([]);
    const xhsPresets = ref([]);
    const xhsPreset = ref(null);
    const xhsProgress = ref(0);
    const xhsPhase = ref('');
    const xhsStreamText = ref('');
    const shelfLoadFailed = ref(false);  // v2.17: 区分"加载失败"和"真正空书架"
    const writingModeLiterary = ref(false);
    const useAtomicMode = ref(true);  // v2.9: 原子化默认开启 — 已升级蓝图+滚动上下文
    const writing = reactive({ chapter:1, content:'', done:false, activeTab:'write' });

    // v2.3: 剧情图谱
    const sgLoading = ref(false);
    const sgData = ref(null);
    const sgArcs = ref(null);
    const sgCalibration = ref(null);
    // v2.3.1: 交互状态
    const sgSubTab = ref('overview');
    const sgChapterFilter = ref(1);
    const sgThreadFilter = ref('all');
    const sgForeshadowFilter = ref('all');
    const sgExpandedThread = ref(null);
    const sgExpandedForeshadow = ref(null);
    const sgExpandedChar = ref(null);
    const sgExpandedArc = ref(null);

    // v2.5: 编辑器状态
    const editState = reactive({ type:null, id:null, data:{} });
    const editShowForm = reactive({ thread:false, foreshadow:false });
    const quickDoing = ref(null);  // which quick action is in progress

    // ── v2.6: 画布编辑器 ──
    const canvasZoom = ref(1);
    const canvasPan = reactive({x:0, y:0});
    const canvasPanning = ref(false);
    const canvasPanStart = reactive({x:0,y:0,px:0,py:0});
    const canvasWrap = ref(null); const canvasInner = ref(null);
    const canvasSelected = ref(null);
    const canvasDragging = ref(null);
    const canvasDragStart = reactive({x:0, y:0, nx:0, ny:0});
    const canvasDragX = ref(0); const canvasDragY = ref(0);
    const canvasDragCh = ref(0); const canvasDragTension = ref(0);
    const canvasToast = ref('');
    const canvasMenu = reactive({show:false, x:0, y:0, lane:0, idx:0});

    const canvasLaneH = 70;
    const canvasWidth = computed(() => Math.max(1200, plotTimeline.value.chapter_range.max * 60 + 200));
    const canvasHeight = computed(() => 40 + plotTimeline.value.lanes.length * canvasLaneH + 60);
    const canvasTickChapters = computed(() => {
      const r = plotTimeline.value.chapter_range;
      const arr = [];
      for (let c = r.min; c <= r.max; c++) arr.push(c);
      return arr;
    });
    function chToCanvasX(ch) {
      const r = plotTimeline.value.chapter_range;
      const range = r.max - r.min || 1;
      return 120 + ((ch - r.min) / range) * (canvasWidth.value - 200);
    }
    function tensionColor(v) {
      return v<=3?'#3fb950':v<=5?'#58a6ff':v<=7?'#f0883e':'#f85149';
    }

    function zoomCanvas(e) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.92 : 1.08;
      canvasZoom.value = Math.max(0.3, Math.min(2.5, canvasZoom.value * delta));
    }
    function startCanvasPan(e) {
      if (e.target.closest('.sg-event-node')) return;
      canvasPanning.value = true;
      canvasPanStart.x = e.clientX; canvasPanStart.y = e.clientY;
      canvasPanStart.px = canvasPan.x; canvasPanStart.py = canvasPan.y;
      canvasWrap.value?.classList.add('panning');
    }
    function doCanvasPan(e) {
      if (canvasPanning.value) {
        canvasPan.x = canvasPanStart.px + (e.clientX - canvasPanStart.x);
        canvasPan.y = canvasPanStart.py + (e.clientY - canvasPanStart.y);
        return;
      }
      if (!canvasDragging.value) return;
      const dx = (e.clientX - canvasDragStart.x) / canvasZoom.value;
      const r = plotTimeline.value.chapter_range;
      const range = r.max - r.min || 1;
      const rawCh = r.min + ((canvasDragStart.nx + dx - 120) / (canvasWidth.value - 200)) * range;
      const snapCh = Math.max(r.min, Math.min(r.max, Math.round(rawCh)));
      canvasDragX.value = e.clientX; canvasDragY.value = e.clientY;
      canvasDragCh.value = snapCh;
      canvasDragTension.value = Math.max(1, Math.min(10, Math.round(5 - (e.clientY - canvasDragStart.y) / 25)));
    }
    function endCanvasPan() {
      if (canvasPanning.value) { canvasPanning.value = false; canvasWrap.value?.classList.remove('panning'); return; }
      if (!canvasDragging.value) return;
      const [lid, eidStr] = canvasDragging.value.split(':');
      const ei = parseInt(eidStr);
      const li = plotTimeline.value.lanes.findIndex(l => l.id === lid);
      if (li >= 0 && ei >= 0) {
        const lane = plotTimeline.value.lanes[li];
        const ev = lane.events[ei];
        const oldCh = ev.chapter;
        ev.chapter = canvasDragCh.value;
        ev.tension = canvasDragTension.value;
        if (oldCh !== canvasDragCh.value) {
          canvasToast.value = `Ch${oldCh} → Ch${canvasDragCh.value}`;
          setTimeout(() => canvasToast.value = '', 2000);
        }
        saveCanvasEvent(li, ei);
      }
      canvasDragging.value = null;
    }

    function startDragCanvasNode(e, laneIdx, evtIdx) {
      e.preventDefault();
      if (e.target.closest('.sg-card-handle')) return; // 手柄单独处理
      const lane = plotTimeline.value.lanes[laneIdx];
      const ev = lane.events[evtIdx];
      canvasDragging.value = lane.id + ':' + evtIdx;
      canvasSelected.value = lane.id + ':' + evtIdx;
      canvasDragStart.x = e.clientX; canvasDragStart.y = e.clientY;
      canvasDragStart.nx = chToCanvasX(ev.chapter);
      canvasMenu.show = false;
    }

    // ── 连线手柄 ──
    let connectLine = null;
    function startCanvasConnect(e, li, ei) {
      e.stopPropagation(); e.preventDefault();
      const lane = plotTimeline.value.lanes[li];
      const ev = lane.events[ei];
      const startX = chToCanvasX(ev.chapter);
      const startY = 40 + li * canvasLaneH + 32;

      connectLine = document.createElement('div');
      connectLine.className = 'sg-connect-line';
      connectLine.style.left = startX + 'px';
      connectLine.style.top = startY + 'px';
      document.querySelector('.sg-canvas-inner')?.appendChild(connectLine);

      function onMove(me) {
        if (!connectLine) return;
        const rect = document.querySelector('.sg-canvas-inner').getBoundingClientRect();
        const ex = (me.clientX - rect.left) / canvasZoom.value - canvasPan.x / canvasZoom.value;
        const ey = (me.clientY - rect.top) / canvasZoom.value - canvasPan.y / canvasZoom.value;
        const dx = ex - startX, dy = ey - startY;
        const len = Math.sqrt(dx*dx + dy*dy);
        const angle = Math.atan2(dy, dx) * 180 / Math.PI;
        connectLine.style.width = len + 'px';
        connectLine.style.transform = `rotate(${angle}deg)`;
      }
      function onUp(me) {
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        if (connectLine) { connectLine.remove(); connectLine = null; }
        // 检测目标
        const target = document.elementFromPoint(me.clientX, me.clientY);
        const card = target?.closest('.sg-card');
        if (card) {
          canvasToast.value = '🔗 关联已创建';
          setTimeout(() => canvasToast.value = '', 1800);
        }
      }
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    }

    // ── 间隙检测 ──
    function getLaneGaps(li) {
      const lane = plotTimeline.value.lanes[li];
      const events = [...lane.events].sort((a,b) => (a.chapter||0)-(b.chapter||0));
      if (events.length < 2) return [];
      const gaps = [];
      for (let i = 1; i < events.length; i++) {
        const gap = events[i].chapter - events[i-1].chapter;
        if (gap > 5) {
          gaps.push({
            from: events[i-1].chapter,
            to: events[i].chapter,
            chapters: gap,
            mid: Math.round((events[i-1].chapter + events[i].chapter) / 2),
          });
        }
      }
      return gaps.slice(0, 2);
    }
    function addEventAtGap(li, midCh) {
      const lane = plotTimeline.value.lanes[li];
      const name = prompt(`在 Ch${midCh} 添加什么事件？`);
      if (!name) return;
      lane.events.push({ chapter: midCh, event: name, tension: 5 });
      lane.events.sort((a,b) => (a.chapter||0)-(b.chapter||0));
      saveCanvasEvent(li, -1);
      canvasToast.value = `✅ 已在 Ch${midCh} 添加事件`;
      setTimeout(() => { canvasToast.value = ''; loadStoryGraph(0); }, 1500);
    }

    async function saveCanvasEvent(li, ei) {
      if (!activeNovel.value) return;
      const lane = plotTimeline.value.lanes[li];
      try {
        await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/storygraph/threads/'+encodeURIComponent(lane.id), {
          method:'PUT', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({key_nodes: lane.events}),
        });
        log.info('Canvas event saved');
      } catch(e) { console.warn('Canvas save failed:', e); }
    }

    function showCanvasMenu(e, li, ei) {
      canvasMenu.show = true;
      canvasMenu.x = e.clientX; canvasMenu.y = e.clientY;
      canvasMenu.lane = li; canvasMenu.idx = ei;
      canvasSelected.value = plotTimeline.value.lanes[li].id + ':' + ei;
    }
    function hideCanvasMenu() { canvasMenu.show = false; }
    function canvasMenuAction(action) {
      const li = canvasMenu.lane, ei = canvasMenu.idx;
      const lane = plotTimeline.value.lanes[li];
      const ev = lane.events[ei];
      if (action === 'heat') { ev.tension = Math.min(10, (ev.tension||5)+2); saveCanvasEvent(li,ei); canvasToast.value='🔥 已升温'; }
      else if (action === 'cool') { ev.tension = Math.max(1, (ev.tension||5)-2); saveCanvasEvent(li,ei); canvasToast.value='❄️ 已降温'; }
      else if (action === 'addAfter') { const n=prompt('新事件描述:'); if(n){lane.events.push({chapter:(ev.chapter||1)+1,event:n,tension:5});lane.events.sort((a,b)=>(a.chapter||0)-(b.chapter||0));saveCanvasEvent(li,-1);canvasToast.value='✅ 已新增';}}
      else if (action === 'delete') { lane.events.splice(ei,1); saveCanvasEvent(li,ei); canvasToast.value='🗑️ 已删除'; }
      else if (action === 'edit') { startEdit('thread', lane.id); sgSubTab.value = 'threads'; }
      canvasMenu.show = false;
      setTimeout(() => canvasToast.value = '', 1800);
      setTimeout(() => loadStoryGraph(0), 500);
    }
    function editCanvasNode(li, ei) {
      const lane = plotTimeline.value.lanes[li];
      startEdit('thread', lane.id);
      sgSubTab.value = 'threads';
    }

    function startEdit(type, id) {
      const d = sgData.value;
      if (!d) return;
      editState.type = type;
      editState.id = id;
      if (type === 'thread') {
        const t = (d.plot_threads||{})[id];
        editState.data = t ? {
          name: t.name, type: t.type, status: t.status,
          priority: t.priority, description: t.description||'',
          current_tension: t.current_tension||5,
          next_planned: t.next_planned||'',
          characters: [...(t.characters||[])],
        } : {};
      } else if (type === 'foreshadow') {
        const f = (d.foreshadow_ledger||{})[id];
        editState.data = f ? {
          description: f.description||'',
          planned_payoff_chapter: f.planned_payoff_chapter||1,
          status: f.status, importance: f.importance||3,
          thread_id: f.thread_id||'',
        } : {};
      } else if (type === 'character') {
        const s = (d.char_snapshots||{})[id];
        editState.data = s ? {
          current_location: s.current_location||'',
          current_emotion: s.current_emotion||'',
          current_power_level: s.current_power_level||'',
          status_effects: [...(s.status_effects||[])],
          active_goals: [...(s.active_goals||[])],
          known_secrets: [...(s.known_secrets||[])],
        } : {};
      }
    }
    function cancelEdit() { editState.type = null; editState.id = null; editState.data = {}; }
    function hasEdit() { return editState.type !== null; }

    async function saveEdit() {
      if (!activeNovel.value || !editState.type) return;
      const id = encodeURIComponent(activeNovel.value.id);
      const API_BASE = API + '/api/novels/' + id + '/storygraph';
      let url, body;

      if (editState.type === 'thread') {
        url = API_BASE + '/threads/' + encodeURIComponent(editState.id);
        body = editState.data;
      } else if (editState.type === 'foreshadow') {
        url = API_BASE + '/foreshadows/' + encodeURIComponent(editState.id);
        body = editState.data;
      } else if (editState.type === 'character') {
        url = API_BASE + '/characters/' + encodeURIComponent(editState.id);
        body = editState.data;
      }

      try {
        const r = await fetch(url, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
        if (r.ok) {
          showToast('已保存', '', '✅');
          cancelEdit();
          await loadStoryGraph(0);
        } else {
          const e = await r.json();
          showToast('保存失败', e.detail||'未知错误', '❌');
        }
      } catch(e) {
        showToast('保存失败', '网络错误，请重试', '❌');
        console.warn('Edit save failed:', e);
      }
    }

    async function quickAction(type, id, action, extra={}) {
      if (!activeNovel.value) return;
      quickDoing.value = type+':'+id;
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/storygraph/quick-action', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({type, id, action, ...extra}),
        });
        if (r.ok) {
          const result = await r.json();
          const msgs = {
            heat_up: '已升温至 '+result.new_tension,
            pause: '已暂停',
            resume: '已恢复',
            resolve: '已标记完成',
            raise_priority: '优先级已提升',
            lower_priority: '优先级已降低',
            delay: '已推迟',
            advance: '已提前',
          };
          showToast(msgs[action]||'操作完成', '', '✅');
          await loadStoryGraph(0);
        }
      } catch(e) { console.warn('Quick action failed:', e); }
      quickDoing.value = null;
    }

    async function deleteThread(id) {
      if (!confirm('确定删除此剧情线？这将标记为"已完结"。')) return;
      if (!activeNovel.value) return;
      try {
        await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/storygraph/threads/'+encodeURIComponent(id), { method:'DELETE' });
        showToast('已删除', '', '🗑️');
        await loadStoryGraph(0);
      } catch(e) { console.warn('Delete failed:', e); }
    }

    async function deleteNovel(novel) {
      if (!confirm(`永久删除「${novel.title}」？\n\n这将删除所有章节和大纲，不可恢复！`)) return;
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(novel.id), { method:'DELETE' });
        if (!r.ok) throw new Error(await r.text());
        showToast('已删除', novel.title, '🗑️');
        if (activeNovel.value?.id === novel.id) activeNovel.value = null;
        await loadShelf();
      } catch(e) { showToast('删除失败: ' + e.message, '', '⚠️'); }
    }

    async function createThread() {
      if (!activeNovel.value) return;
      const name = prompt('新剧情线名称:');
      if (!name) return;
      const id = 'custom_' + Date.now();
      try {
        await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/storygraph/threads/'+encodeURIComponent(id), {
          method:'PUT', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({name, type:'subplot', priority:3, status:'active', current_tension:5}),
        });
        showToast('剧情线已创建', name, '✅');
        await loadStoryGraph(0);
      } catch(e) { console.warn('Create thread failed:', e); }
    }

    async function createForeshadow() {
      if (!activeNovel.value) return;
      const desc = prompt('伏笔描述:');
      if (!desc) return;
      const payoff = parseInt(prompt('计划回收章节:', '20')) || 20;
      const id = 'custom_fs_' + Date.now();
      try {
        await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/storygraph/foreshadows', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({id, description:desc, planted_chapter:sgData.value?.last_updated_chapter||1, planned_payoff_chapter:payoff, importance:3}),
        });
        showToast('伏笔已创建', '', '✅');
        await loadStoryGraph(0);
      } catch(e) { console.warn('Create foreshadow failed:', e); }
    }

    // v2.4: 可视化交互状态
    const sgHoveredChar = ref(null);
    const sgHoveredEdge = ref(null);
    const sgHoveredEvent = ref(null);
    // 窗口大小（可变）
    const relationHeight = ref(520);
    const timelineHeight = ref(460);
    // 关系图缩放/平移
    const relationZoom = ref(1);
    const relationPan = reactive({x:0, y:0});
    const relationDragging = ref(null);
    const relationDragStart = reactive({x:0, y:0, nx:0, ny:0});
    const relationSvgWrap = ref(null);
    // 时间线图缩放/平移
    const timelineZoom = ref(1);
    const timelinePan = reactive({x:0, y:0});
    const timelineDragging = ref(false);
    const timelineDragStart = reactive({x:0, y:0, px:0, py:0});
    const timelineSvgWrap = ref(null);

    const batchStart = ref(1); const batchEnd = ref(1);
    // 批量上限自动裁剪：batchStart 变化时 clamp batchEnd
    watch(batchStart, (v) => {
      const maxEnd = (v||1) + 9;
      if (batchEnd.value > maxEnd) batchEnd.value = maxEnd;
    });
    const batchProgress = ref(0); const batchCurrent = ref(0); const batchStatus = ref('');
    const exportProgress = ref(0);

    const genres = ['修仙','玄幻','都市','科幻','悬疑','历史','游戏','末日','无限流','武侠','民俗悬疑','规则怪谈','反套路修仙','赛博仙侠','经营建设','苟道流'];

    const selectedCount = computed(() => Object.values(exportSelected).filter(Boolean).length);
    const allSelected = computed(() => novels.value.length>0 && novels.value.every(n=>exportSelected[n.id]));
    const availableChapters = computed(() => activeNovel.value ? Array.from({length:activeNovel.value.state.total_chapters||20},(_,i)=>i+1) : []);
    // ── v2.3.4: 角色人设蒸馏（女娲框架）──
    const charProfiles = ref({});
    const charProfiling = ref('');
    async function distillCharProfile(name) {
      if (!activeNovel.value) return;
      if (charProfiling.value) { showToast('有蒸馏任务进行中，请等待'); return; }
      charProfiling.value = name;
      showToast('正在蒸馏「'+name+'」人设（约1-2分钟）…', '女娲造人', '🧬');
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/characters/'+encodeURIComponent(name)+'/profile',{method:'POST'});
        const d = await r.json();
        if (d.ok) {
          const r2 = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/character-profiles');
          const d2 = await r2.json();
          if (d2.ok) charProfiles.value = d2.profiles || {};
          showToast('「'+name+'」人设蒸馏完成', '', '✅');
        } else {
          showToast(d.detail || '蒸馏失败', '女娲造人', '⚠️');
        }
      } catch(e) { showToast('蒸馏失败: '+e.message, '', '❌'); }
      charProfiling.value = '';
    }
    // ── v2.3.5: 章节反馈闭环 👍👎 ──
    const chapterRating = ref(0);
    const chapterReason = ref('');
    const chapterFeedbackDone = ref(false);
    async function submitChapterFeedback() {
      if (!activeNovel.value || !chapterRating.value) return;
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/chapters/'+writing.chapter+'/feedback',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({rating:chapterRating.value, reason:chapterReason.value})
        });
        const d = await r.json();
        if (d.ok) { chapterFeedbackDone.value = true; setTimeout(()=>{chapterFeedbackDone.value=false; chapterRating.value=0; chapterReason.value='';}, 2500); }
        else showToast(d.detail||'提交失败','反馈','⚠️');
      } catch(e) { showToast('提交失败: '+e.message,'','❌'); }
    }
    const currentOutline = computed(() => {
      if (!activeNovel.value||!planResult.value) return null;
      for (const vol of planResult.value.outline?.volumes||[]) for (const ch of vol.chapters||[]) if (ch.number===writing.chapter) return ch;
      return null;
    });

    // v2.3: StoryGraph 计算属性
    const sgStats = computed(() => {
      if (!sgData.value) return { totalThreads:0, activeThreads:0, totalForeshadows:0, unresolvedForeshadows:0, overdueForeshadows:0, resolvedForeshadows:0, trackedChars:0, causalLinks:0 };
      const d = sgData.value;
      const currentCh = (activeNovel.value?.state?.completed_chapters||[]).length + 1;
      const threads = d.plot_threads||{};
      const fs = d.foreshadow_ledger||{};
      return {
        totalThreads: Object.keys(threads).length,
        activeThreads: Object.values(threads).filter(t=>['active','advancing','climax'].includes(t.status)).length,
        totalForeshadows: Object.keys(fs).length,
        unresolvedForeshadows: Object.values(fs).filter(f=>['planted','hinted'].includes(f.status)).length,
        overdueForeshadows: Object.values(fs).filter(f=>['planted','hinted'].includes(f.status)&&(f.planned_payoff_chapter||999)<currentCh).length,
        resolvedForeshadows: Object.values(fs).filter(f=>f.status==='resolved').length,
        trackedChars: Object.keys(d.char_snapshots||{}).length,
        causalLinks: (d.causal_links||[]).length,
      };
    });
    const sgThreads = computed(() => {
      if (!sgData.value) return [];
      return Object.values(sgData.value.plot_threads||{}).sort((a,b)=>(b.priority||0)-(a.priority||0));
    });
    const sgForeshadows = computed(() => {
      if (!sgData.value) return [];
      return Object.values(sgData.value.foreshadow_ledger||{}).sort((a,b)=>{
        const order={resolved:3,revealed:2,hinted:1,planted:0};
        return (order[b.status]||0)-(order[a.status]||0) || (b.importance||0)-(a.importance||0);
      });
    });
    const sgCharSnapshots = computed(() => sgData.value?.char_snapshots||{});
    const filteredThreads = computed(() => {
      if (!sgThreads.value) return [];
      if (sgThreadFilter.value === 'all') return sgThreads.value;
      if (sgThreadFilter.value === 'active') return sgThreads.value.filter(t => ['active','advancing','climax'].includes(t.status));
      return sgThreads.value.filter(t => t.status === sgThreadFilter.value);
    });
    const filteredForeshadows = computed(() => {
      if (!sgForeshadows.value) return [];
      const currentCh = (activeNovel.value?.state?.completed_chapters||[]).length + 1;
      if (sgForeshadowFilter.value === 'all') return sgForeshadows.value;
      if (sgForeshadowFilter.value === 'active') return sgForeshadows.value.filter(f => ['planted','hinted'].includes(f.status));
      if (sgForeshadowFilter.value === 'overdue') return sgForeshadows.value.filter(f => ['planted','hinted'].includes(f.status) && (f.planned_payoff_chapter||999) < currentCh);
      if (sgForeshadowFilter.value === 'resolved') return sgForeshadows.value.filter(f => f.status === 'resolved');
      return sgForeshadows.value;
    });

    // StoryGraph 辅助函数
    function statusColor(s) { return {dormant:'#8b949e',active:'#58a6ff',advancing:'#f0883e',climax:'#f85149',resolved:'#3fb950'}[s]||'#8b949e'; }
    function statusBg(s) { return {dormant:'rgba(139,148,158,.1)',active:'rgba(88,166,255,.1)',advancing:'rgba(240,136,62,.1)',climax:'rgba(248,81,73,.1)',resolved:'rgba(63,185,80,.1)'}[s]||''; }
    function statusLabel(s) { return {dormant:'休眠',active:'进行中',advancing:'推进中',climax:'高潮',resolved:'已完结'}[s]||s; }
    function tensionGradient(v) { return v<=3?'#3fb950':v<=6?'#58a6ff':v<=8?'#f0883e':'#f85149'; }
    function fsStatusColor(f) { return {resolved:'#3fb950',revealed:'#58a6ff',hinted:'#f0883e',planted:'#8b949e'}[f.status]||'#8b949e'; }
    function fsStatusBg(f) { return {resolved:'rgba(63,185,80,.1)',revealed:'rgba(88,166,255,.1)',hinted:'rgba(240,136,62,.1)',planted:'rgba(139,148,158,.1)'}[f.status]||''; }
    function fsStatusLabel(f) { return {resolved:'✅已回收',revealed:'👁已揭示',hinted:'💡再暗示',planted:'🌱已种'}[f.status]||f.status; }

    // ── v2.4: 人物关系图 ──
    const relationGraph = computed(() => {
      if (!sgData.value) return { nodes: [], edges: [] };
      const snaps = sgData.value.char_snapshots || {};
      const threads = sgData.value.plot_threads || {};
      const nodes = [];
      const edges = [];
      const edgeSet = new Set();
      const charNames = Object.keys(snaps);

      // 情绪→emoji映射
      const emotionIcons = {
        '愤怒':'😡','悲伤':'😢','喜悦':'😄','恐惧':'😨','担忧':'😟',
        '冷静':'😐','坚定':'💪','疑惑':'🤔','绝望':'😱','兴奋':'🤩',
        '嫉妒':'😒','爱慕':'😍','愧疚':'😳','紧张':'😰','厌恶':'😖',
      };
      const charPalette = [
        {color:'#6366f1', glow:'#818cf8'},{color:'#f59e0b', glow:'#fbbf24'},
        {color:'#ec4899', glow:'#f472b6'},{color:'#10b981', glow:'#34d399'},
        {color:'#ef4444', glow:'#f87171'},{color:'#06b6d4', glow:'#22d3ee'},
        {color:'#8b5cf6', glow:'#a78bfa'},{color:'#f97316', glow:'#fb923c'},
      ];
      // 统计每个角色的连接数（用于节点大小）
      const connCount = {};

      // 构建节点
      charNames.forEach((name, i) => {
        const snap = snaps[name] || {};
        const emo = snap.current_emotion || '';
        let emoji = '';
        for (const [k, v] of Object.entries(emotionIcons)) {
          if (emo.includes(k)) { emoji = v; break; }
        }
        const pal = charPalette[i % charPalette.length];
        nodes.push({
          id: name, label: name,
          emotion: emo, emotionIcon: emoji,
          location: snap.current_location || '',
          last_chapter: snap.last_chapter_appeared || 0,
          goals: snap.active_goals || [],
          power_level: snap.current_power_level || '',
          color: pal.color,
          glowColor: pal.glow,
          bgColor: pal.color + '25',
          textColor: pal.color,
          gradIndex: i,
          radius: 28,  // will be adjusted below
        });
      });

      // 构建边（从关系变化中）
      for (const [name, snap] of Object.entries(snaps)) {
        for (const rc of (snap.relationship_changes || [])) {
          const target = rc.with || '';
          if (!target || !snaps[target]) continue;
          const ek = [name, target].sort().join('||');
          if (edgeSet.has(ek)) continue;
          edgeSet.add(ek);
          edges.push({ source: name, target, label: rc.change || '关联', chapter: rc.chapter || 0, color: '#58a6ff', dashed: false });
        }
      }

      // 补充：剧情线中共现的角色
      for (const t of Object.values(threads)) {
        const chars = t.characters || [];
        for (let i = 0; i < chars.length; i++) {
          for (let j = i + 1; j < chars.length; j++) {
            const ek = [chars[i], chars[j]].sort().join('||');
            if (!edgeSet.has(ek) && snaps[chars[i]] && snaps[chars[j]]) {
              edgeSet.add(ek);
              edges.push({ source: chars[i], target: chars[j],
                label: t.name?.substring(0,8) || '共线', chapter: 0, color: '#f0883e', dashed: true });
            }
          }
        }
      }
      // 统计连接数，调整节点半径
      edges.forEach(e => {
        connCount[e.source] = (connCount[e.source] || 0) + 1;
        connCount[e.target] = (connCount[e.target] || 0) + 1;
      });
      nodes.forEach(n => {
        const cnt = connCount[n.id] || 0;
        n.radius = Math.max(22, Math.min(38, 24 + cnt * 3));
      });

      return { nodes, edges };
    });

    // 关系图：计算圆形布局（使用reactive支持拖拽）
    const relationNodeLayout = reactive([]);
    // 当relationGraph变化时重新计算布局
    watch(() => relationGraph.value.nodes, (nodes) => {
      relationNodeLayout.length = 0;
      const n = nodes.length;
      if (!n) return;
      const cx = 400, cy = 300, r = Math.min(280, n * 35);
      nodes.forEach((node, i) => {
        const angle = (2 * Math.PI * i / n) - Math.PI / 2;
        relationNodeLayout.push({
          ...node,
          x: cx + r * Math.cos(angle),
          y: cy + r * Math.sin(angle),
        });
      });
    }, { immediate: true });
    const relationViewBox = computed(() => {
      const pad = 100; const w = 800; const h = 600;
      return { w: Math.round(w / relationZoom.value), h: Math.round(h / relationZoom.value) };
    });
    const relationNodes = computed(() => {
      return relationNodeLayout.map(n => ({
        ...n,
        x: n.x + relationPan.x,
        y: n.y + relationPan.y,
      }));
    });
    const relationEdges = computed(() => {
      const nodeMap = {};
      relationNodes.value.forEach(n => { nodeMap[n.id] = n; });
      return relationGraph.value.edges.map(e => ({
        ...e,
        x1: nodeMap[e.source]?.x || 0,
        y1: nodeMap[e.source]?.y || 0,
        x2: nodeMap[e.target]?.x || 0,
        y2: nodeMap[e.target]?.y || 0,
      }));
    });
    const hoveredCharDetail = computed(() => {
      if (!sgHoveredChar.value) return null;
      return relationGraph.value.nodes.find(n => n.id === sgHoveredChar.value) || null;
    });

    // ── v2.4: 剧情线图 ──
    const plotTimeline = computed(() => {
      if (!sgData.value) return { lanes: [], causal_links: [], chapter_range: {min:1, max:1} };
      const threads = sgData.value.plot_threads || {};
      const links = sgData.value.causal_links || [];
      const typeColors = { main_plot:'#f85149', subplot:'#f59e0b', character_arc:'#8b5cf6', mystery:'#06b6d4' };

      const lanes = Object.values(threads).map(t => ({
        id: t.id, name: t.name, type: t.type || 'subplot',
        status: t.status || 'active', priority: t.priority || 3,
        events: (t.key_nodes || []).slice().sort((a,b) => (a.chapter||0) - (b.chapter||0)),
        color: typeColors[t.type] || '#8b949e', characters: t.characters || [],
      })).sort((a,b) => b.priority - a.priority);

      // 章节范围
      let chMin = 1, chMax = 1;
      for (const l of lanes) {
        for (const e of l.events) {
          if (e.chapter < chMin || chMin === 1) chMin = Math.min(chMin, e.chapter);
          chMax = Math.max(chMax, e.chapter);
        }
      }
      for (const cl of links) {
        chMin = Math.min(chMin, cl.cause_chapter || chMin);
        chMax = Math.max(chMax, cl.effect_chapter || chMax);
      }
      chMax = Math.max(chMax, chMin + 5); // 至少5章宽度

      return { lanes, causal_links: links, chapter_range: {min: chMin, max: chMax} };
    });

    // 时间线布局参数
    const timelineChapters = computed(() => {
      const r = plotTimeline.value.chapter_range;
      const arr = [];
      for (let c = r.min; c <= r.max; c++) arr.push(c);
      return arr;
    });
    const timelineChapterLabels = computed(() => {
      const all = timelineChapters.value;
      const step = Math.max(1, Math.floor(all.length / 12)); // 最多显示~12个标签
      return all.filter((_, i) => i % step === 0);
    });
    const plotEndY = computed(() => plotTimeline.value.lanes.length * 70 + 30);
    const timelineViewBox = computed(() => {
      const w = Math.max(800, plotTimeline.value.chapter_range.max * 50 + 100);
      const h = Math.max(300, plotEndY.value + 10);
      return { w: Math.round(w / timelineZoom.value), h: Math.round(h / timelineZoom.value) };
    });
    function laneY(i) { return i * 70 + 36; }
    function chToX(ch) {
      const r = plotTimeline.value.chapter_range;
      const range = r.max - r.min || 1;
      const margin = 60;
      const contentW = timelineViewBox.value.w - margin * 2;
      return margin + ((ch - r.min) / range) * contentW + timelinePan.x;
    }
    function causalArrowPath(cl) {
      const clFromCh = cl.cause_chapter, clToCh = cl.effect_chapter;
      if (!clFromCh || !clToCh) return '';
      const x1 = chToX(clFromCh), x2 = chToX(clToCh);
      const srcLane = plotTimeline.value.lanes.findIndex(l => l.events.some(e => e.chapter === clFromCh));
      const tgtLane = plotTimeline.value.lanes.findIndex(l => l.events.some(e => e.chapter === clToCh));
      const y1 = laneY(srcLane >= 0 ? srcLane : 0) + 51;
      const y2 = laneY(tgtLane >= 0 ? tgtLane : 0) + 51;
      const midX = (x1 + x2) / 2;
      return `M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`;
    }
    const hoveredEventDetail = computed(() => {
      if (!sgHoveredEvent.value) return null;
      const {lane, idx} = sgHoveredEvent.value;
      const events = plotTimeline.value.lanes[lane]?.events;
      return events ? events[idx] : null;
    });

    // 最后一章（用于角色在线指示器）
    const lastActiveChapter = computed(() => {
      if (!sgData.value) return 0;
      return sgData.value.last_updated_chapter || 0;
    });

    // 边线贝塞尔曲线：从圆心外向弯曲
    function edgeCurve(edge) {
      const cx = (edge.x1 + edge.x2) / 2, cy = (edge.y1 + edge.y2) / 2;
      const centerX = 400, centerY = 300;
      const dx = cx - centerX, dy = cy - centerY;
      const dist = Math.sqrt(dx*dx + dy*dy) || 1;
      const offset = 30;
      const cpx = cx + (dx / dist) * offset;
      const cpy = cy + (dy / dist) * offset;
      return `M${edge.x1},${edge.y1} Q${cpx},${cpy} ${edge.x2},${edge.y2}`;
    }
    function edgeCurveMid(edge) {
      const cx = (edge.x1 + edge.x2) / 2, cy = (edge.y1 + edge.y2) / 2;
      const centerX = 400, centerY = 300;
      const dx = cx - centerX, dy = cy - centerY;
      const dist = Math.sqrt(dx*dx + dy*dy) || 1;
      return { x: cx + (dx / dist) * 18, y: cy + (dy / dist) * 18 };
    }

    // 缩放 & 平移
    function zoomRelation(e) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      relationZoom.value = Math.max(0.4, Math.min(3, relationZoom.value * delta));
    }
    function startPanRelation(e) {
      if (e.target.tagName === 'circle' || e.target.closest('g')) return; // 让节点拖拽优先
      relationDragging.value = -1; // -1 = 画布拖拽
      relationDragStart.x = e.clientX;
      relationDragStart.y = e.clientY;
      relationDragStart.nx = relationPan.x;
      relationDragStart.ny = relationPan.y;
    }
    function doPanRelation(e) {
      if (relationDragging.value === null) return;
      if (relationDragging.value === -1) { // 画布拖拽
        relationPan.x = relationDragStart.nx + (e.clientX - relationDragStart.x) / relationZoom.value;
        relationPan.y = relationDragStart.ny + (e.clientY - relationDragStart.y) / relationZoom.value;
      } else { // 节点拖拽
        const node = relationNodeLayout[relationDragging.value];
        if (node) {
          node.x = relationDragStart.nx + (e.clientX - relationDragStart.x) / relationZoom.value;
          node.y = relationDragStart.ny + (e.clientY - relationDragStart.y) / relationZoom.value;
        }
      }
    }
    function endPanRelation() { relationDragging.value = null; }
    function startDragRelationNode(i, e) {
      e.preventDefault();
      relationDragging.value = i;
      relationDragStart.x = e.clientX;
      relationDragStart.y = e.clientY;
      relationDragStart.nx = relationNodeLayout[i].x;
      relationDragStart.ny = relationNodeLayout[i].y;
    }

    function zoomTimeline(e) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.9 : 1.1;
      timelineZoom.value = Math.max(0.4, Math.min(3, timelineZoom.value * delta));
    }
    function startPanTimeline(e) {
      timelineDragging.value = true;
      timelineDragStart.x = e.clientX;
      timelineDragStart.y = e.clientY;
      timelineDragStart.px = timelinePan.x;
      timelineDragStart.py = timelinePan.y;
    }
    function doPanTimeline(e) {
      if (!timelineDragging.value) return;
      timelinePan.x = timelineDragStart.px + (e.clientX - timelineDragStart.x) / timelineZoom.value;
      timelinePan.y = timelineDragStart.py + (e.clientY - timelineDragStart.y) / timelineZoom.value;
    }
    function endPanTimeline() { timelineDragging.value = false; }

    // ── 拖拽调整窗口大小 ──
    const resizeState = reactive({ active: null, startY: 0, startH: 0 });
    function startResize(type, e) {
      e.preventDefault();
      resizeState.active = type;
      resizeState.startY = e.clientY;
      resizeState.startH = type === 'relation' ? relationHeight.value : timelineHeight.value;
      document.body.classList.add('sg-resizing');
      window.addEventListener('mousemove', onResizeMove);
      window.addEventListener('mouseup', stopResize);
    }
    function onResizeMove(e) {
      if (!resizeState.active) return;
      const delta = e.clientY - resizeState.startY;
      const newH = Math.max(200, Math.min(1200, resizeState.startH + delta));
      if (resizeState.active === 'relation') relationHeight.value = newH;
      else timelineHeight.value = newH;
    }
    function stopResize() {
      resizeState.active = null;
      document.body.classList.remove('sg-resizing');
      window.removeEventListener('mousemove', onResizeMove);
      window.removeEventListener('mouseup', stopResize);
    }

    function pct(n) { const t=n.state.total_chapters||1,c=(n.state.completed_chapters||[]).length; return Math.round(c/t*100)+'%'; }
    function fmtWords(w) { return w>=10000?(w/10000).toFixed(0)+'万字':w+'字'; }
    function coverGradient(id) {
      const c=[['#97a8c9','#7186af'],['#b2a2cc','#8e7fb1'],['#91b7a8','#639786'],['#c8b398','#ad9071'],['#a3adbd','#737f96']];
      const [a,b]=c[(id||'').split('').reduce((a,c)=>a+c.charCodeAt(0),0)%c.length];
      return `linear-gradient(135deg,${a},${b})`;
    }

    onMounted(async () => {
      applyAppearance();
      refreshHealth(); loadLLMProviders();
      NovelUI.requestJson('/api/system/connection').then(value => Object.assign(provider, value)).catch(() => {});
      loadStyles(); loadStyleSeeds(); // fire-and-forget
      // v2.50: 加载小红书模板列表（加载完自动加载预设）
      fetch('/api/xiaohongshu/templates').then(r=>r.json()).then(d=>{ if(d.ok){ xhsTemplates.value=d.templates; loadXhsPresets(); } }).catch(()=>{});

      // v2.11: 兜底 — 无论如何12秒后关闭shelf loading
      setTimeout(() => { if (loading.shelf) { loading.shelf = false; console.warn('Shelf loading: forced close after 12s'); } }, 12000);

      // v2.14: 全局加载安全阀 — 放宽到15分钟(Android WebView后台节流+多阶段LLM生成)
      let _safetyTimer = null;
      const _resetSafety = () => {
        if (_safetyTimer) clearTimeout(_safetyTimer);
        _safetyTimer = setTimeout(() => {
          let recovered = [];
          for (const k of Object.keys(loading)) {
            if (loading[k]) { loading[k] = false; recovered.push(k); }
          }
          if (recovered.length) {
            console.warn('Auto-recovered stuck loading:', recovered.join(', '));
            error.value = '操作超时，已自动恢复（'+recovered.join('、')+'）。请重试。';
          }
        }, 900000);  // 15分钟
      };
      // 用 computed 监听 loading（通过每个字段触发追踪）
      const _anyLoading = Vue.computed(() => loading.shelf || loading.create || loading.write || loading.export || loading.saving || loading.batch);
      Vue.watch(_anyLoading, (busy) => {
        if (busy) _resetSafety();
        else if (_safetyTimer) { clearTimeout(_safetyTimer); _safetyTimer = null; }
      });

      // 书架加载（带重试 + fail-safe + timeout）
      try {
        loading.shelf = true;
        // v2.17: 兼容旧WebView的 fetch timeout（AbortSignal.timeout 需要 Chrome 103+）
        const _fetchTimeout = (url, ms) => {
          const ctrl = new AbortController();
          const timer = setTimeout(() => ctrl.abort(), ms);
          return fetch(url, {signal: ctrl.signal}).finally(() => clearTimeout(timer));
        };
        // 先等API就绪
        try { await _fetchTimeout(API+'/api/health', 8000); } catch(_) {}
        for (let attempt=0; attempt<5; attempt++) {
          const ok = await _loadShelfOnce(_fetchTimeout);
          if (ok) break;
          if (attempt < 4) await new Promise(r => setTimeout(r, 2000));
        }
      } catch(e) {
        console.warn('Shelf load error:', e);
        shelfLoadFailed.value = true;
      } finally {
        loading.shelf = false;
        // v2.17: 全局安全阀 — 无论什么情况，30秒后强制清除loading

      }
      // 恢复上次session — 两步兜底：
      //   1) 书架里找（最优，带完整 state）
      //   2) 书架没找到/加载失败 → 直接用 sessionStorage 的 ID 去 API 拿
      const savedId = new URLSearchParams(location.search).get('novel') || sessionStorage.getItem('activeNovelId');
      const savedView = sessionStorage.getItem('lastView');
      if (savedId) {
        let novel = novels.value.find(n => n.id === savedId);
        if (novel) {
          // 书架里找到了 → 直接恢复
          _restoreSession(novel, savedView);
        } else if (novels.value.length > 0) {
          // 书架有数据但没找到 → 可能是 ID 不匹配，静默忽略
          console.warn('Saved novel not in shelf, clearing session');
          sessionStorage.removeItem('activeNovelId');
          sessionStorage.removeItem('lastView');
        } else {
          // 书架为空（API 挂了）→ 兜底：直接 fetch 小说数据
          console.log('Shelf empty, fallback fetch for session restore');
          try {
            const r = await fetch(API+'/api/novels/'+encodeURIComponent(savedId));
            if (r.ok) {
              const d = await r.json();
              if (d.novel) {
                // 用 API 返回的数据构造最小 novel 对象
                const st = d.novel.state || {};
                novel = {
                  id: d.novel.title || savedId,
                  title: d.novel.title || savedId,
                  genre: d.novel.genre || '',
                  style: d.novel.style || '',
                  state: { total_chapters: st.total_chapters||1, completed_chapters: st.completed_chapters||[], current_chapter: st.current_chapter||0 }
                };
                novels.value = [novel]; // 顺手填充书架
                planResult.value = d.novel;
                _restoreSession(novel, savedView, true);
              }
            }
          } catch(e) {
            console.warn('Fallback session restore failed:', e.message);
          }
        }
      }
    });

    function applyAppearance() {
      NovelUI.setTheme(theme.value);
      document.documentElement.dataset.ui = uiStyle.value;
      NovelUI.storage.set('uiStyle', uiStyle.value);
    }
    function toggleTheme() { theme.value = theme.value === 'dark' ? 'light' : 'dark'; applyAppearance(); }

    // 刷新恢复：保存当前小说和视图到 sessionStorage
    function _restoreSession(novel, savedView, skipPlanFetch) {
      activeNovel.value = novel;
      if (!skipPlanFetch) {
        fetch(API+'/api/novels/'+encodeURIComponent(novel.id))
          .then(r=>r.json()).then(d=>{planResult.value=d.novel;}).catch(()=>{});
      }
      if (!requestedView && savedView && ['create','write'].includes(savedView)) {
        currentView.value = savedView;
        const done = (novel.state.completed_chapters||[]);
        const total = novel.state.total_chapters||1;
        let next = 1; while(next<=total && done.includes(next)) next++;
        writing.chapter = Math.min(next, total);
        if (done.includes(writing.chapter)) {
          fetch(API+'/api/novels/'+encodeURIComponent(novel.id)+'/chapters/'+writing.chapter)
            .then(r=>r.ok?r.json():null)
            .then(d=>{if(d&&d.content){writing.content=d.content;writing.done=true;}})
            .catch(()=>{});
        }
      }
    }
    function _saveSession() {
      if (activeNovel.value) {
        sessionStorage.setItem('activeNovelId', activeNovel.value.id);
        sessionStorage.setItem('lastView', currentView.value);
      }
    }

    async function loadStyles() { try { const r=await fetch(API+'/api/styles'); styleCategories.value=(await r.json()).categories||{}; } catch(e){} }
    let _shelfDebounce=null;
    async function _loadShelfOnce() { return loadShelf(); }
    let shelfPromise = null;
    async function loadShelf() {
      if (shelfPromise) return shelfPromise;
      if (_shelfDebounce) {clearTimeout(_shelfDebounce);_shelfDebounce=null;}
      loading.shelf=true;
      shelfPromise=(async()=>{
        try {
          const data=await NovelApp.request(API+'/api/novels',{timeout:15000});
          if(!Array.isArray(data.novels)) throw new Error('书架数据格式异常');
          novels.value=data.novels;
          if(activeNovel.value) {
            activeNovel.value=data.novels.find(n=>n.id===activeNovel.value.id)||null;
          }
          shelfLoadFailed.value=false;
          return true;
        } catch(e) {shelfLoadFailed.value=true;error.value=e.message;return false;}
        finally {loading.shelf=false;shelfPromise=null;}
      })();
      return shelfPromise;
    }
    function loadShelfDebounced() {
      if (_shelfDebounce) return;
      _shelfDebounce = setTimeout(() => { _shelfDebounce=null; loadShelf(); }, 500);
    }
    function loadNovelPlan() {
      // 从服务端强制重拉最新 plan（主角修改后确保前端同步）
      if (!activeNovel.value) return;
      fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id))
        .then(r=>r.json()).then(d=>{
          if (d.novel) {
            // 用 nextTick 确保 Vue 响应式触发
            planResult.value = null;
            setTimeout(() => { planResult.value = d.novel; }, 50);
          }
        })
        .catch(()=>{});
    }
    function openCreate() { if(isBusy.value) return; planResult.value=null; decomposeSummary.value=null; error.value=''; currentView.value='create'; }

    // ── v2.39: 爆火分析 + 逆天生成 ──
    const trendData = ref(null);
    const bizarreNovels = ref([]);
    const bizarreMode = ref('topical');  // 'topical' | 'twist' | 'mixed'
    const trendLoading = ref(false);

    function openTrends() {
      if(isBusy.value) return;
      planResult.value = null;
      error.value = '';
      currentView.value = 'trends';
      if (!trendData.value) doTrendAnalyze();
    }

    async function doTrendAnalyze() {
      trendLoading.value = true; trendData.value = null; error.value = '';
      try {
        const r = await fetch(API+'/api/trends/analyze', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
        const d = await r.json();
        if (d.ok) trendData.value = d;
        else error.value = d.error;
      } catch(e) { error.value = e.message; }
      trendLoading.value = false;
    }

    async function doBizarreQuick() {
      trendLoading.value = true; error.value = '';
      try {
        const r = await fetch(API+'/api/trends/bizarre/quick', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count:3, mode: bizarreMode.value})});
        const d = await r.json();
        if (d.ok) { bizarreNovels.value = d.bizarre_novels; if (!trendData.value) doTrendAnalyze(); }
        else error.value = d.error;
      } catch(e) { error.value = e.message; }
      trendLoading.value = false;
    }

    async function doBizarreFull() {
      trendLoading.value = true; error.value = '';
      try {
        const r = await fetch(API+'/api/trends/bizarre', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count:5, style:form.style||'热血爽文', mode: bizarreMode.value})});
        const d = await r.json();
        if (d.ok) {
          bizarreNovels.value = d.bizarre_novels;
          if (d.trends) trendData.value = {patterns: d.trends, hot_novels: d.hot_novels, source: 'live'};
        }
        else error.value = d.error;
      } catch(e) { error.value = e.message; }
      trendLoading.value = false;
    }

    // v2.50: 小红书短篇一键生成
    function loadXhsPresets() {
      const tpl = xhsTemplate.value;
      fetch('/api/xiaohongshu/presets?template='+encodeURIComponent(tpl))
        .then(r=>r.json()).then(d=>{ if(d.ok) xhsPresets.value=d.presets; }).catch(()=>{});
    }
    async function doXhsCreate() {
      if (loading.xhs) return;
      loading.xhs = true; xhsResult.value = null; xhsProgress.value = 0; xhsPhase.value = ''; xhsStreamText.value = ''; error.value = '';
      const inspiration = xhsPreset.value ? xhsPreset.value.inspiration : xhsInspiration.value.trim();
      if (!inspiration) { reportError('请选择预设或输入灵感'); loading.xhs = false; return; }
      try {
        const resp = await fetch(API+'/api/xiaohongshu/create', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ template: xhsTemplate.value, inspiration })
        });
        if (!resp.ok) { const d=await resp.json(); reportError(d.detail||'请求失败'); loading.xhs=false; return; }
        const reader = resp.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
        while (true) {
          const {done, value} = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream: true});
          const lines = buffer.split('\n'); buffer = lines.pop() || '';
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const evt = JSON.parse(line.slice(6));
                if (evt.type === 'progress') { xhsProgress.value = evt.pct; xhsPhase.value = evt.message; }
                else if (evt.type === 'chapter_start') { xhsPhase.value = `撰写第${evt.number}章 · ${evt.title}`; xhsProgress.value = evt.pct; }
                else if (evt.type === 'chunk' && evt.chapter === 3) { xhsStreamText.value += evt.text; }
                else if (evt.type === 'chapter_done') { xhsProgress.value = evt.pct; }
                else if (evt.type === 'done') { xhsResult.value = evt; xhsProgress.value = 100; xhsPhase.value = '完成！'; }
                else if (evt.type === 'error') { reportError(evt.message); loading.xhs = false; return; }
              } catch(e) {}
            }
          }
        }
      } catch(e) { reportError(e.message||'网络错误'); }
      finally { loading.xhs = false; }
    }

    async function createBizarreNovel(bn) {
      if (isBusy.value) return;
      isBusy.value = true; error.value = '';
      try {
        const p = bn.premise || bn;
        form.genre = p.genre || '玄幻';
        form.title = p.title || '';
        // 用剧情梗概作为灵感，不去掉换行让LLM理解反转结构
        form.inspiration = (p.synopsis || p.one_liner || '').replace(/\n/g, ' ');
        form.target_words = 50000;
        form.naturalNames = true;
        await doCreate();
      } catch(e) { error.value = e.message; }

    }
    function openWrite() { if(isBusy.value) return; if(activeNovel.value) { currentView.value='write'; writing.activeTab='write'; _saveSession(); } }
    // v3.10: 角色扮演模式（SillyTavern 风格独立页面，携带当前小说用于「从本书生成角色」）
    function navigateMain(id, event) {
      if (['shelf','create','worlds','settings'].includes(id)) {
        event.preventDefault();
        if (id==='create') { activeNovel.value ? openWrite() : openCreate(); }
        else if(id==='settings') openSettings();
        else currentView.value=id;
      }
    }
    function openNovelWorld() { currentView.value = 'worlds'; }
    function enterNovelWorld(novel) {
      if (isBusy.value || !novel) return;
      activeNovel.value = novel;
      _saveSession();
      window.location.href = '/scene.html?novel=' + encodeURIComponent(novel.id);
    }
    function openRoleplay() {
      if (isBusy.value) return;
      const novel = activeNovel.value ? ('?novel=' + encodeURIComponent(activeNovel.value.id)) : '';
      window.location.href = '/roleplay.html' + novel;
    }
    function openOutlineFor(novel) {
      // 书架 → 编辑大纲：选中该书并加载最新 plan 进入大纲预览
      if(isBusy.value) return;
      activeNovel.value = novel; error.value = '';
      _saveSession();
      fetch(API+'/api/novels/'+encodeURIComponent(novel.id))
        .then(r=>r.json()).then(d=>{planResult.value=d.novel;})
        .catch(()=>{});
      currentView.value='create';
    }
    function openOutline() {
      if(isBusy.value) return;
      if(!activeNovel.value) return;
      _saveSession();
      // 从服务端重新加载plan（保留最新版本）
      fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id))
        .then(r=>r.json()).then(d=>{planResult.value=d.novel;})
        .catch(()=>{});
      currentView.value='create';
    }

    async function doCreate() {
      if (!form.inspiration.trim()) { reportError('请输入核心灵感'); return; }
      if (loading.write || loading.batch) { reportError('有任务正在进行中，请等待完成'); return; }
      error.value=''; planResult.value=null; loading.create=true;
      createProgress.value=0; createPhase.value='start'; createPhaseLabel.value='准备中…';
      decomposeSummary.value=null;
      try {
        const r=await fetch(API+'/api/novels/create-stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...form, natural_names:form.naturalNames, normal_pacing:form.normalPacing, fast_food:form.fastFood, creative_enhance:form.creativeEnhance})});
        await readSSE(r, (evt) => {
          if(evt.type==='progress'){ createPhase.value=evt.phase; createPhaseLabel.value=evt.label; createProgress.value=evt.pct; }
          // v2.2: 需求拆解完成
          else if(evt.type==='requirements_decomposed'){
            decomposeSummary.value=evt;
            createPhaseLabel.value=`已分析出 ${evt.subtask_count} 条创作需求`;
          }
          else if(evt.type==='warning'){ showToast(evt.message, '提示', 'ℹ️'); }
          // v2.2: 角色名一致性警告
          else if(evt.type==='name_check'){
            if (!decomposeSummary.value) decomposeSummary.value = {};
            decomposeSummary.value = { ...decomposeSummary.value, name_warning: evt.mismatches, name_suggestion: evt.suggestion };
          }
          else if(evt.type==='done'){ createProgress.value=100; createPhaseLabel.value='完成！'; planResult.value=evt.plan; loadShelfDebounced(); _saveSession(); }
          else if(evt.type==='error'){ reportError(evt.message, '生成失败'); return false; }
        });
      } catch(e){ if(e.name!=='AbortError') reportError(e.message||'连接超时，请重试'); }
      loading.create=false;
    }

    function openNovel(novel) {
      if(isBusy.value){ showToast('有任务正在进行中，请等待完成'); return; }
      activeNovel.value=novel; error.value='';
      _saveSession();
      const done=(novel.state.completed_chapters||[]), total=novel.state.total_chapters||1;
      planResult.value=null;
      let next=1; while(next<=total && done.includes(next)) next++;
      writing.chapter=Math.min(next, total); writing.content=''; writing.done=false;
      currentView.value='write';
      batchStart.value=Math.min(next, total); batchEnd.value=Math.min(total, next+9);
      // 异步加载plan和章节内容
      fetch(API+'/api/novels/'+encodeURIComponent(novel.id))
        .then(r=>r.json()).then(d=>{planResult.value=d.novel;})
        .catch(()=>{});
      // 如果默认章节已生成，加载内容
      const defaultCh = Math.min(next, total);
      if (done.includes(defaultCh)) {
        fetch(API+'/api/novels/'+encodeURIComponent(novel.id)+'/chapters/'+defaultCh)
          .then(r=>r.ok?r.json():null)
          .then(d=>{if(d&&d.content){writing.content=d.content;writing.done=true;}})
          .catch(()=>{});
      }
    }
    function startWriting(title) {
      const n = novels.value.find(x => x.id === title || x.title === title);
      if (n) { openNovel(n); return; }

      // 列表没刷新 — 用 planResult 跳转
      const plan = planResult.value;
      if (plan && (plan.title === title || title === undefined)) {
        activeNovel.value = {
          id: plan.title,
          title: plan.title,
          genre: plan.genre || '',
          style: plan.style || '',
          state: plan.state || { total_chapters: plan.outline?.total_chapters || 1, completed_chapters: [] }
        };
        currentView.value = 'write';
        loadShelf();
        _saveSession();
        return;
      }

      // 最后兜底：直接 fetch
      if (title) {
        fetch(API+'/api/novels/'+encodeURIComponent(title))
          .then(r=>r.json()).then(d=>{
            if (d.novel) openNovel({...d.novel, id: title, state: d.novel.state||{}});
          }).catch(()=>{});
      }
    }

    let _chFetchAbort=null;
    function selectChapter(ch, skipFetch) {
      if(isBusy.value) return;
      writing.chapter=ch; writing.content=''; writing.done=false; error.value='';
      // 取消进行中的请求
      if (_chFetchAbort) { _chFetchAbort.abort(); _chFetchAbort=null; }
      if (skipFetch) return;
      if(!activeNovel.value) return;
      const done=activeNovel.value.state.completed_chapters||[];
      if(!done.includes(ch)) return;
      const ctrl=new AbortController(); _chFetchAbort=ctrl;
      fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/chapters/'+ch, {signal:ctrl.signal})
        .then(r=>r.ok?r.json():null)
        .then(d=>{if(d&&d.content){writing.content=d.content;writing.done=true;}})
        .catch(()=>{})
        .finally(()=>{if(_chFetchAbort===ctrl) _chFetchAbort=null;});
    }

    async function doGenerateChapter() {
      if(!activeNovel.value) return;
      if(isBusy.value){ reportError('有任务正在进行中，请等待完成'); return; }
      if((activeNovel.value.state.completed_chapters||[]).includes(writing.chapter)) return;
      loading.write=true; writing.content=''; writing.done=false; reviewFeedback.value=''; error.value='';
      chapterProgress.value=0; chapterProgressLabel.value='准备中…';
      const targetChars = (currentOutline.value?.target_words || 3000) * 2;
      try {
        const endpoint = useAtomicMode.value ? '/api/novels/generate/atomic' : '/api/novels/generate';
        const r=await fetch(API+endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({novel_id:activeNovel.value.id,chapter_num:writing.chapter,writing_mode:writingModeLiterary.value?'literary':'webnovel'})});
        await readSSE(r, (evt) => {
          // v2.3: 原子化模式的事件
          if(evt.type==='beats_decomposed'){
            chapterProgressLabel.value = `拆解为${evt.count}个节拍`;
          }
          else if(evt.type==='beat_progress'){
            chapterProgressLabel.value = `Beat ${evt.beat_index+1}/${evt.total_beats} · ${evt.function}`;
            chapterProgress.value = Math.round((evt.beat_index+1)/evt.total_beats*50); // 前半段50%
          }
          else if(evt.type==='evaluation'){
            chapterProgress.value = 85;
            chapterProgressLabel.value = `${evt.verdict||''} · ${evt.overall_score||0}分`;
          }
          else if(evt.type==='status'){
            chapterProgressLabel.value = evt.message||'处理中…';
          }
          else if(evt.type==='quality_warning'){
            chapterProgressLabel.value = evt.message||'质量优化中…';
          }
          else if(evt.type==='outline_compliance'){
            outlineCompliance.value = {
              chapter: evt.chapter, pct: evt.pct, level: evt.level,
              message: evt.message||'', missing: evt.missing||[],
              patched: !!evt.patched_text,
            };
            chapterProgressLabel.value = evt.message||'大纲合规校验完成';
            if (evt.patched_text) writing.content += '\n\n' + evt.patched_text;
          }
          else if(evt.type==='quality_retry'){
            chapterProgressLabel.value = `✨ 优化完成：${evt.score_before}分 → ${evt.score_after}分`;
          }
          else if(evt.type==='text'){
            writing.content+=evt.content; chapterProgress.value=Math.min(95, Math.round(writing.content.length/targetChars*100));
            chapterProgressLabel.value=`${writing.content.length}字`;
            nextTick(()=>{if(preview.value)preview.value.scrollTop=preview.value.scrollHeight});
          }
          else if(evt.type==='done'){
            writing.done=true; chapterProgress.value=100; chapterProgressLabel.value='完成';
            const ch=activeNovel.value.state; if(!ch.completed_chapters) ch.completed_chapters=[];
            if(!ch.completed_chapters.includes(evt.chapter_num)) ch.completed_chapters.push(evt.chapter_num);
            ch.completed_chapters.sort((a,b)=>a-b); ch.current_chapter=Math.max(...ch.completed_chapters);
            loadShelfDebounced();
            // v2.35: 生成完成后从API拉取保存版(已过规范化+过滤)，替换流式原始输出
            const novId=encodeURIComponent(activeNovel.value.id);
            fetch(API+'/api/novels/'+novId+'/chapters/'+evt.chapter_num)
              .then(r=>r.ok?r.json():null)
              .then(d=>{if(d&&d.content){writing.content=d.content;}})
              .catch(()=>{});
            // v2.3.1: 自动刷新剧情图谱
            if (writing.activeTab === 'storygraph') loadStoryGraph(0);
          }
          else if(evt.type==='error'){ reportError(evt.message, '生成失败'); return false; }
        });
      } catch(e){ if(e.name!=='AbortError') reportError(e.message||'连接超时，请重试'); }
      loading.write=false; chapterProgress.value=0;
    }
    function nextChapter() { const n=writing.chapter+1; if(n<=(activeNovel.value?.state.total_chapters||999)) selectChapter(n); }
    function copyChapter() { navigator.clipboard.writeText(writing.content); showToast('已复制到剪贴板', '操作成功', '✅'); }

    // ── v2.5.2: 免费 TTS 朗读（edge-tts）──
    // 主通道: 后端 /api/tts（微软神经语音，多音色可调）
    // 降级: Web Speech API（Android 系统 TTS）
    const speaking = ref(false);
    let ttsAudio = null;
    function stopSpeaking(){
      if(ttsAudio){ ttsAudio.pause(); ttsAudio = null; }
      if(window.speechSynthesis) speechSynthesis.cancel();
      speaking.value = false;
    }
    async function speakChapter(){
      const text = (writing.content || '').trim();
      if(!text || speaking.value){ stopSpeaking(); return; }
      speaking.value = true;
      try{
        const r = await fetch(API+'/api/tts', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({ text: text.slice(0, 1500), voice:'zh-CN-XiaoxiaoNeural', rate:'+0%', pitch:'+0Hz' }),
        });
        if(!r.ok) throw new Error('TTS '+r.status);
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        ttsAudio = new Audio(url);
        ttsAudio.onended = () => { speaking.value = false; URL.revokeObjectURL(url); ttsAudio = null; };
        ttsAudio.onerror = () => { speaking.value = false; URL.revokeObjectURL(url); ttsAudio = null; };
        ttsAudio.play();
      }catch(e){
        // 降级: 系统 TTS
        try{
          if(window.speechSynthesis){
            const u = new SpeechSynthesisUtterance(text.slice(0, 1500));
            u.lang = 'zh-CN'; u.rate = 1.0;
            u.onend = () => { speaking.value = false; };
            u.onerror = () => { speaking.value = false; };
            speechSynthesis.speak(u);
            return;
          }
        }catch(_){}
        speaking.value = false;
        showToast('语音合成不可用（需联网）', '朗读失败', '⚠️');
      }
    }

    // ── v2.3: 剧情图谱 ──
    async function switchToStoryGraph() {
      if (!activeNovel.value || isBusy.value) return;
      writing.activeTab = 'storygraph';
      sgSubTab.value = 'overview';
      sgChapterFilter.value = (activeNovel.value.state.completed_chapters||[]).length + 1;
      await loadStoryGraph();
    }

    async function loadStoryGraph(chapterNum = 0) {
      if (!activeNovel.value) return;
      sgLoading.value = true;
      const id = encodeURIComponent(activeNovel.value.id);
      const chParam = chapterNum > 0 ? `?chapter=${chapterNum}` : '';
      try {
        const [sgRes, arcRes, calRes] = await Promise.allSettled([
          fetch(API+'/api/novels/'+id+'/storygraph'+chParam),
          fetch(API+'/api/novels/'+id+'/arcs'),
          fetch(API+'/api/novels/'+id+'/calibration'),
        ]);
        if (sgRes.status==='fulfilled' && sgRes.value.ok) {
          sgData.value = await sgRes.value.json();
          // 同步章节滑块
          if (!chapterNum && sgData.value.last_updated_chapter) {
            sgChapterFilter.value = sgData.value.last_updated_chapter ||
              (activeNovel.value.state.completed_chapters||[]).length + 1;
          }
        }
        if (arcRes.status==='fulfilled' && arcRes.value.ok) sgArcs.value = await arcRes.value.json();
        if (calRes.status==='fulfilled' && calRes.value.ok) sgCalibration.value = await calRes.value.json();
      } catch(e) {
        console.warn('StoryGraph load failed:', e);
      } finally {
        sgLoading.value = false;
      }
    }

    // 章节滑块变化时重新加载
    let _sgSliderTimer = null;
    watch(sgChapterFilter, (newVal) => {
      if (_sgSliderTimer) clearTimeout(_sgSliderTimer);
      _sgSliderTimer = setTimeout(() => {
        if (writing.activeTab === 'storygraph' && activeNovel.value) {
          loadStoryGraph(newVal);
        }
      }, 300); // 300ms 防抖
    });

    function cancelBatch() {
      if(abortCtrl.value){ abortCtrl.value.abort(); abortCtrl.value=null; }
      loading.batch=false; batchStreaming.value=''; writing.done=true;
      showToast('已中断，已生成的章节保留', '批量生成中断', '⏸');
    }

    async function doBatchGenerate() {
      if(!activeNovel.value||loading.batch) return;
      if(isBusy.value){ reportError('有任务正在进行中，请等待完成'); return; }
      const start=batchStart.value||1, end=batchEnd.value||1;
      if(start>end) return;
      const count = end - start + 1;
      if(count > 10) { reportError('批量生成上限为10章，请缩小范围'); return; }
      loading.batch=true; batchProgress.value=0; batchCurrent.value=start-1; error.value=''; batchStreaming.value='';
      let failedChapters = [];
      // v2.6: 断线自动重连（最多重试2次，带 resume 从检查点续传）
      const MAX_ATTEMPTS = 3;
      for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
        if(attempt > 0){
          batchStatus.value = `⚠️ 连接中断，${attempt * 3} 秒后自动恢复…`;
          await new Promise(r2 => setTimeout(r2, attempt * 3000));
          if(!loading.batch) return; // 用户已点中断
          batchStatus.value = '🔄 正在从断点恢复…';
        }
        const ctrl = new AbortController(); abortCtrl.value = ctrl;
        try {
          const r=await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/generate/batch',{
            method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({
              start_chapter:start,end_chapter:end,
              writing_mode:writingModeLiterary.value?'literary':'webnovel',
              resume: attempt > 0,  // v2.6: 重连时启用断点续传（后端检查点自动跳过已完成章）
            }),
            signal:ctrl.signal,
          });
          await readSSE(r, (evt) => {
            if(evt.type==='progress'){
              writing.chapter=evt.chapter; writing.content=''; writing.done=false;
              batchCurrent.value=evt.chapter-1; batchProgress.value=Math.round((evt.chapter-1-start)/(end-start)*100);
              batchStatus.value='第'+evt.chapter+'章 生成中…';
            }
            else if(evt.type==='status'){
              batchStatus.value = (evt.message||'').replace('💾 ','').replace('✍️ ','').replace('🎨 ','');
            }
            else if(evt.type==='outline_compliance'){
              batchStatus.value = evt.message||'';
            }
            else if(evt.type==='quality_warning'){
              batchStatus.value = '质量优化中…';
            }
            else if(evt.type==='text'){
              writing.content+=evt.content;
              batchStreaming.value=(batchStreaming.value+evt.content).slice(-300);
              nextTick(()=>{if(preview.value)preview.value.scrollTop=preview.value.scrollHeight});
            }
            else if(evt.type==='chapter_done'){
              writing.done=true;
              const st=activeNovel.value.state;
              if(!st.completed_chapters) st.completed_chapters=[];
              if(!st.completed_chapters.includes(evt.chapter)) st.completed_chapters.push(evt.chapter);
              st.completed_chapters.sort((a,b)=>a-b);
              st.current_chapter=Math.max(...st.completed_chapters);
              batchCurrent.value=evt.chapter; batchProgress.value=Math.round((evt.chapter-start+1)/(end-start+1)*100);
              batchStreaming.value=''; loadShelfDebounced();
            }
            else if(evt.type==='chapter_failed'){
              failedChapters.push(evt.chapter);
              batchCurrent.value=evt.chapter; batchProgress.value=Math.round((evt.chapter-start+1)/(end-start+1)*100);
              batchStreaming.value='';
            }
            else if(evt.type==='batch_done'){
              batchCurrent.value=end; batchProgress.value=100;
              if((evt.failed||[]).length>0) reportError(`${evt.failed.length}章失败: ${evt.failed.join(',')}`, '批量生成部分失败');
            }
            else if(evt.type==='error'){ reportError(evt.message, '生成失败'); return false; }
          });
          selectChapter(end); loadShelfDebounced();
          break; // 成功完成
        } catch(e){
          if(e.name==='AbortError') return; // 用户主动中断
          if(attempt >= MAX_ATTEMPTS - 1){
            error.value=e.message||'连接超时，已生成的部分已保存';
            reportError('连接中断多次，已生成章节已保存。可点击"批量生成"继续（自动断点续传）', '批量中断');
          }
          // 否则循环重试
        }
      }
      loading.batch=false; abortCtrl.value=null; batchStreaming.value='';
    }

    async function doRegenerate() {
      if(!activeNovel.value||!reviewFeedback.value.trim()){ error.value='请输入修改意见'; return; }
      if(loading.batch){ error.value='批量生成运行中，请等待完成'; return; }
      loading.write=true; writing.content=''; writing.done=false; error.value=''; chapterProgress.value=0;
      try {
        const endpoint = useAtomicMode.value ? '/api/novels/generate/atomic' : '/api/novels/generate';
        const r=await fetch(API+endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({novel_id:activeNovel.value.id,chapter_num:writing.chapter,writing_mode:writingModeLiterary.value?'literary':'webnovel',feedback:reviewFeedback.value.trim()})});
        await readSSE(r, (evt) => {
          if(evt.type==='text'){ writing.content+=evt.content; nextTick(()=>{if(preview.value)preview.value.scrollTop=preview.value.scrollHeight}); }
          else if(evt.type==='done'){ writing.done=true; reviewFeedback.value=''; chapterProgress.value=100; }
          else if(evt.type==='outline_compliance'){
            outlineCompliance.value = {
              chapter: evt.chapter, pct: evt.pct, level: evt.level,
              message: evt.message||'', missing: evt.missing||[],
              patched: !!evt.patched_text,
            };
            if (evt.patched_text) writing.content += '\n\n' + evt.patched_text;
          }
          else if(evt.type==='error'){ reportError(evt.message, '生成失败'); return false; }
        });
      } catch(e){ if(e.name!=='AbortError') reportError(e.message||'连接超时，请重试'); }
      loading.write=false; chapterProgress.value=0;
    }

    async function doRegenerateOutline() {
      const novelId = planResult.value?.title || activeNovel.value?.id;
      if(!novelId || !outlineFeedback.value.trim()){ error.value='请输入修改意见'; return; }
      if(isBusy.value){ error.value='有任务正在进行中'; return; }
      isRegenerating.value=true; error.value=''; createPhaseLabel.value='正在分析修改意见…'; createProgress.value=5;
      try {
        const r=await fetch(API+'/api/novels/'+encodeURIComponent(novelId)+'/interactive-outline',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({feedback:outlineFeedback.value.trim()})});
        await readSSE(r, (evt) => {
          if(evt.type==='progress'){ createPhase.value=evt.phase; createPhaseLabel.value=evt.label; createProgress.value=evt.pct; }
          else if(evt.type==='intent'){ createPhaseLabel.value='已理解: '+evt.summary?.substring(0,50); }
          else if(evt.type==='execution_summary'){ createPhaseLabel.value=`完成 ${evt.executed}/${evt.total} 条修改`; }
          else if(evt.type==='diff'){ createPhaseLabel.value=`共 ${(evt.changes||[]).length} 处变更`; }
          else if(evt.type==='done'){ createProgress.value=100; createPhaseLabel.value='完成'; planResult.value=evt.plan; outlineFeedback.value=''; loadShelfDebounced(); loadNovelPlan(); }
          else if(evt.type==='error'){ reportError(evt.message, '生成失败'); return false; }
        });
      } catch(e){ if(e.name!=='AbortError')error.value=e.message||'连接超时'; }
      isRegenerating.value=false; createProgress.value=0;
    }
    function onModeChange() { /* toggle handled by v-model, just reset writing state */ }

    function toggleExport(id) { exportSelected[id]=!exportSelected[id]; }
    function selectAll() { const v=!allSelected.value; novels.value.forEach(n=>exportSelected[n.id]=v); }
    async function doSingleExport(novel, fmt='txt') {
      loading.export=novel.id+fmt; error.value='';
      try {
        const r=await fetch(API+'/api/novels/'+encodeURIComponent(novel.id)+'/export?fmt='+fmt);
        if(!r.ok) throw new Error((await r.json().catch(()=>({detail:'无章节'}))).detail);
        if(fmt==='pdf'||fmt==='epub'){ downloadBlob(await r.blob(), novel.id+'.'+fmt, false, fmt==='pdf'?'application/pdf':'application/epub+zip'); }
        else downloadBlob(await r.text(), novel.id+'.txt');
      } catch(e){ error.value=e.message; }
      loading.export=false;
    }
    async function doBatchExport() {
      const ids=Object.entries(exportSelected).filter(([,v])=>v).map(([k])=>k);
      if(!ids.length){ error.value='请勾选小说'; return; }
      loading.export='batch'; error.value=''; exportProgress.value=0;
      try {
        const r=await fetch(API+'/api/novels/export/batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({novel_ids:ids,fmt:'txt'})});
        const data=await r.json(); let ok=0,fail=0;
        for(let i=0;i<data.results.length;i++){
          const item=data.results[i];
          if(item.success&&item.content){ downloadBlob(item.content,item.novel_id+'.txt',true); ok++; } else fail++;
          exportProgress.value=Math.round((i+1)/data.results.length*100);
        }
        if(ok>0) error.value='导出 '+ok+' 本'; setTimeout(()=>error.value='',3000);
      } catch(e){ error.value=e.message; }
      loading.export=false;
    }
    function downloadBlob(data, filename, delay=false, mime='text/plain;charset=utf-8') {
      const blob = typeof data === 'string' ? new Blob(['\uFEFF'+data],{type:mime}) : data;
      NovelUI.download(filename, blob, mime);
    }

    function addChapter(vol) {
      const max=Math.max(0,...vol.chapters.map(c=>c.number||0));
      vol.chapters.push({number:max+1,title:'新章节',summary:'待填写',emotion_curve:'平稳→紧张',characters:['主角'],hook:'悬念',target_words:1500});
      renumberChapters();
    }
    function addVolume() {
      const vols=planResult.value.outline.volumes;
      vols.push({number:(Math.max(0,...vols.map(v=>v.number||0))+1),title:'新卷',theme:'待填写',chapters:[{number:1,title:'第一章',summary:'待填写',emotion_curve:'平稳→紧张',characters:['主角'],hook:'悬念',target_words:1500}]});
      renumberChapters();
    }
    function removeVolume(vi) { planResult.value.outline.volumes.splice(vi,1); renumberChapters(); }
    function renumberChapters() {
      const vols=planResult.value?.outline?.volumes; if(!vols)return;
      let n=0; for(const vol of vols) for(const ch of vol.chapters){ n++; ch.number=n; }
      planResult.value.outline.total_chapters=n;
    }
    async function savePlan() {
      if(isBusy.value) return;
      if(!planResult.value) return; loading.saving=true; editSaved.value=false; error.value=''; renumberChapters();
      try {
        const r=await fetch(API+'/api/novels/'+encodeURIComponent(planResult.value.title),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(planResult.value)});
        if(!r.ok) throw new Error((await r.json().catch(()=>({detail:'失败'}))).detail);
        editSaved.value=true; await loadShelf(); setTimeout(()=>editSaved.value=false,3000);
      } catch(e){ error.value=e.message; }
      loading.saving=false;
    }
    async function confirmAndStart() {
      await savePlan();
      if (!error.value && planResult.value) {
        startWriting(planResult.value.title);
        // 安全兜底：如果 300ms 后 activeNovel 仍未设置，回退
        setTimeout(() => {
          if (currentView.value === 'write' && !activeNovel.value) {
            currentView.value = 'create';
            reportError('无法进入写作页面，请从书架重新打开小说', '跳转失败');
          }
        }, 300);
      }
    }

    function applyCustomStyle() { const d=customStyleDesc.value.trim(); if(d) form.style='自定义: '+d; }
    async function loadStyleParams() { try { const r=await fetch(API+'/api/styles/params'); const d=await r.json(); styleParams.value=d.params; } catch(e){ console.error(e); } }
    async function applyParamStyle() {
      try {
        const r=await fetch(API+'/api/styles/build-custom',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...customParams})});
        const d=await r.json();
        if(d.style){ form.style=d.style.name; form._customStyle=d.style; }
      } catch(e){ console.error(e); }
    }

    // ── 需求管理系统 ──
    async function decomposeRequirements() {
      const insp = form.inspiration || prompt('输入核心灵感/需求:', '');
      if (!insp) return;
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/requirements/decompose', {
          method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({inspiration:insp})
        });
        const d = await r.json();
        Object.assign(requirements, d.requirements);
        supervisionReport.value = null;
      } catch(e){ reportError(e.message||'拆解失败'); }
    }
    async function superviseRequirements() {
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/requirements/supervise', {method:'POST'});
        const d = await r.json();
        supervisionReport.value = d;
        if (d.results) d.results.forEach(res => {
          const t = requirements.subtasks.find(s => s.id===res.subtask_id);
          if (t) { t.status = res.passed ? 'passed' : 'failed'; t.feedback = (res.issues||[]).join('; ')||'达标'; }
        });
        requirements.overall_score = d.overall_score;
        requirements.passed_count = d.passed_count;
      } catch(e){ reportError(e.message||'监督失败'); }
    }
    async function runVerifyLoop() {
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/requirements/verify-loop', {method:'POST'});
        await readSSE(r, evt => {
          if (evt.type==='progress') { createPhase.value=evt.phase; createPhaseLabel.value=evt.label; createProgress.value=evt.pct; }
          else if (evt.type==='supervision') { supervisionReport.value=evt.report; }
          else if (evt.type==='done') { createProgress.value=100; createPhaseLabel.value='校验完成'; loadNovelPlan(); }
        });
      } catch(e){ reportError(e.message||'校验失败'); }
    }
    async function addRequirements() {
      const insp = reqInspiration.value.trim(); if(!insp) return;
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/requirements/update', {
          method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({inspiration:insp})
        });
        const d = await r.json();
        Object.assign(requirements, d.requirements);
        reqInspiration.value = ''; showReqInput.value = false;
      } catch(e){ reportError(e.message||'追加失败'); }
    }
    async function loadStyleSeeds() { try { styleSeeds.value=(await (await fetch(API+'/api/styles/seeds')).json()).seeds||[]; } catch(e){} }
    async function saveStyleSeed() {
      const d=customStyleDesc.value.trim(); if(!d) return; const name=prompt('风格名称:',''); if(!name) return;
      try {
        const r=await fetch(API+'/api/styles/seeds',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,description:d,author:'自定义',prose:d,tone:d})});
        if(r.ok){ await loadStyleSeeds(); error.value='已保存'; setTimeout(()=>error.value='',2000); }
      } catch(e){ error.value=e.message; }
    }
    async function loadStyleSeed(seed) { try { const s=(await (await fetch(API+'/api/styles/seeds')).json()).seeds.find(x=>x.name===seed.name); if(s){ customStyleDesc.value=s.description||''; applyCustomStyle(); } } catch(e){} }
    async function deleteStyleSeed(seed) { try { await fetch(API+'/api/styles/seeds/'+encodeURIComponent(seed.filename),{method:'DELETE'}); await loadStyleSeeds(); } catch(e){} }


    function openBible() {
      if (!activeNovel.value) return;
      _returnView.value = currentView.value;
      currentView.value = 'bible';
      loadBible();
    }
    async function loadBible() {
      if (!activeNovel.value) return;
      bibleLoading.value=true;
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/bible');
        bibleData.value = (await r.json()).bible || {characters:{},locations:{},terms:{},relations:[]};
      } catch(e) { reportError('世界圣经加载失败: '+e.message); }
      finally { bibleLoading.value=false; if(bibleTab.value==='relations') drawRelationGraph(); }
    }
    async function saveBible() {
      if (!activeNovel.value) return;
      bibleLoading.value=true;
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/bible', {
          method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(bibleData.value)
        });
        if (!r.ok) throw new Error('HTTP '+r.status);
        showToast('世界圣经已保存', '保存成功', '✅');
      } catch(e) { reportError('保存失败: '+e.message); }
      finally { bibleLoading.value=false; }
    }
    // ── v2.0: 圣经 CRUD ──
    function bibleStartNew() {
      const kind = bibleTab.value==='locations' ? 'location' : (bibleTab.value==='terms' ? 'term' : 'character');
      bibleEdit.active = true; bibleEdit.kind = kind; bibleEdit.name = '';
      bibleEdit.form = {};
      if (kind==='location') bibleEdit.form = {description:'', note:''};
      else if (kind==='term') bibleEdit.form = {category:'术语', description:'', note:''};
      else bibleEdit.form = {identity:'', appearance:'', note:''};
    }
    function bibleStartEdit(kind, name, entry) {
      bibleEdit.active = true; bibleEdit.kind = kind; bibleEdit.name = name;
      bibleEdit.form = JSON.parse(JSON.stringify(entry || {}));
    }
    function bibleCancelEdit() { bibleEdit.active = false; bibleEdit.name=''; bibleEdit.form={}; }
    function bibleSaveEntry() {
      const kind = bibleEdit.kind;
      const name = (bibleEdit.name || '').trim();
      const newName = (bibleEdit.form.name || name || '').trim();
      if (kind !== 'relation' && !newName) { reportError('名称不能为空'); return; }
      if (kind === 'relation' && (!bibleEdit.form.from || !bibleEdit.form.to)) { reportError('请选择关系两端的人物'); return; }
      // 收集表单字段（去掉 name 字段本身，它作为 key）
      const entry = JSON.parse(JSON.stringify(bibleEdit.form));
      delete entry.name;
      // 删除旧 key（如果是改名）
      if (kind==='character') {
        if (name && name !== newName) delete bibleData.value.characters[name];
        bibleData.value.characters[newName] = entry;
      } else if (kind==='location') {
        if (name && name !== newName) delete bibleData.value.locations[name];
        bibleData.value.locations[newName] = entry;
      } else if (kind==='term') {
        if (name && name !== newName) delete bibleData.value.terms[name];
        bibleData.value.terms[newName] = entry;
      } else if (kind==='relation') {
        // 关系：新增/编辑
        const idx = bibleEdit.form._idx;
        const rel = {from: bibleEdit.form.from, to: bibleEdit.form.to, type: bibleEdit.form.type, note: bibleEdit.form.note};
        if (idx !== undefined && idx !== null && bibleData.value.relations[idx]) bibleData.value.relations[idx] = rel;
        else bibleData.value.relations.push(rel);
      }
      bibleCancelEdit();
      showToast('已保存，点击「💾 保存」写入', '编辑完成', '✅');
    }
    function bibleDeleteEntry(kind, name) {
      openModal({
        title: '删除「'+name+'」', kind: 'confirm', label: '确定从世界圣经删除？', confirmText: '删除',
        onConfirm: () => {
          if (kind==='character') delete bibleData.value.characters[name];
          else if (kind==='location') delete bibleData.value.locations[name];
          else if (kind==='term') delete bibleData.value.terms[name];
          showToast('已删除，点击「💾 保存」写入', '删除完成', '✅');
        }
      });
    }
    function bibleStartRelation() {
      bibleEdit.active = true; bibleEdit.kind = 'relation'; bibleEdit.name = '';
      bibleEdit.form = {from:'', to:'', type:'盟友', note:''};
    }
    function bibleEditRelation(idx) {
      const r = bibleData.value.relations[idx] || {};
      bibleEdit.active = true; bibleEdit.kind = 'relation'; bibleEdit.name = '';
      bibleEdit.form = {from:r.from||'', to:r.to||'', type:r.type||'盟友', note:r.note||'', _idx:idx};
    }
    function bibleDeleteRelation(idx) {
      bibleData.value.relations.splice(idx, 1);
    }
    async function extractTerms() {
      if (!activeNovel.value) return;
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/bible/terms/extract', {method:'POST'});
        termCandidates.value = (await r.json()).candidates || [];
        if (!termCandidates.value.length) showToast('未提取到新名词（正文过短或都已收录）', '提取完成', 'ℹ️');
      } catch(e) { reportError('提取失败: '+e.message); }
    }
    function addTermFromCandidate(c) {
      bibleData.value.terms[c.name] = { category:c.category||'术语', description:'', note:'' };
      termCandidates.value = termCandidates.value.filter(x=>x.name!==c.name);
    }
    function switchBibleTab(t) { bibleTab.value=t; if(t==='relations') drawRelationGraph(); }
    function drawRelationGraph() {
      nextTick(() => {
        const canvas = document.getElementById('relationCanvas');
        if (!canvas) return;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        const chars = bibleData.value.characters || {};
        const relations = bibleData.value.relations || [];
        const names = Object.keys(chars);
        if (!names.length) { ctx.fillStyle='rgba(255,255,255,.4)'; ctx.font='14px sans-serif'; ctx.fillText('暂无人物，无法绘制关系图', 20, 30); return; }
        const cx = canvas.width/2, cy = canvas.height/2;
        const R = Math.min(canvas.width, canvas.height)/2 - 60;
        const pos = {};
        names.forEach((n, i) => {
          const angle = (2*Math.PI*i)/names.length - Math.PI/2;
          pos[n] = {x: cx + R*Math.cos(angle), y: cy + R*Math.sin(angle)};
        });
        const typeColor = { '盟友':'#7ee2a8', '敌对':'#ff6b81', '暧昧':'#ff9ecb', '导师':'#9ecbff', '未知':'#c9a7ff' };
        relations.forEach(r => {
          if (!pos[r.from] || !pos[r.to]) return;
          const color = typeColor[r.type] || typeColor['未知'];
          ctx.strokeStyle = color; ctx.globalAlpha = 0.7; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.moveTo(pos[r.from].x, pos[r.from].y); ctx.lineTo(pos[r.to].x, pos[r.to].y); ctx.stroke();
          ctx.globalAlpha = 1;
          const mx = (pos[r.from].x+pos[r.to].x)/2, my = (pos[r.from].y+pos[r.to].y)/2;
          ctx.fillStyle = color; ctx.font='10px sans-serif'; ctx.fillText(r.type, mx, my);
        });
        names.forEach(n => {
          const p = pos[n];
          ctx.beginPath(); ctx.arc(p.x, p.y, 24, 0, 2*Math.PI);
          ctx.fillStyle = 'rgba(201,167,255,.18)'; ctx.fill();
          ctx.strokeStyle = '#c9a7ff'; ctx.lineWidth = 1.5; ctx.stroke();
          ctx.fillStyle = '#f5f3ff'; ctx.font='12px sans-serif'; ctx.textAlign='center';
          ctx.fillText(n.slice(0,4), p.x, p.y+4);
        });
      });
    }

    function openStats() {
      if (!activeNovel.value) return;
      _returnView.value = currentView.value;
      currentView.value = 'stats';
      loadStats();
    }
    async function loadStats() {
      if (!activeNovel.value) return;
      statsLoading.value=true;
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/stats');
        statsData.value = await r.json();
      } catch(e) { reportError('统计加载失败: '+e.message); }
      finally { statsLoading.value=false; }
    }
    async function saveGoal() {
      if (!activeNovel.value || !statsData.value) return;
      try {
        const r = await fetch(API+'/api/novels/'+encodeURIComponent(activeNovel.value.id)+'/stats/goal', {
          method:'PUT', headers:{'Content-Type':'application/json'},
          body:JSON.stringify(statsData.value.goal)
        });
        if (!r.ok) throw new Error('HTTP '+r.status);
        showToast('写作目标已保存', '保存成功', '✅');
      } catch(e) { reportError('保存失败: '+e.message); }
    }

    function openIdeas() {
      _returnView.value = currentView.value;
      currentView.value = 'ideas';
      loadIdeas(); loadMaterials();
    }
    async function loadIdeas() {
      ideasLoading.value=true;
      try {
        const r = await fetch(API+'/api/ideas');
        const d = await r.json();
        ideasData.value = d.ideas || []; ideasTags.value = d.tags || [];
      } catch(e) { reportError('灵感加载失败: '+e.message); }
      finally { ideasLoading.value=false; }
    }
    function filterIdeasByTag(tag) {
      ideasData.value = ideasData.value.filter(i => (i.tags||[]).includes(tag));
    }
    async function loadMaterials() {
      try {
        const r = await fetch(API+'/api/ideas/materials');
        materialsData.value = (await r.json()).materials || [];
      } catch(e) {}
    }
    async function addIdea() {
      openModal({
        title: '💡 记一条灵感', kind: 'input', label: '灵感内容', rows: 3, textarea: true,
        confirmText: '保存灵感',
        onConfirm: async (text) => {
          if (!text || !text.trim()) return;
          try {
            const r = await fetch(API+'/api/ideas', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text.trim(), tags:[]})});
            if (!r.ok) throw new Error('HTTP '+r.status);
            await loadIdeas();
          } catch(e) { reportError('添加失败: '+e.message); }
        }
      });
    }
    async function deleteIdea(id) {
      openModal({
        title: '删除灵感', kind: 'confirm', label: '删除这条灵感？', confirmText: '删除',
        onConfirm: async () => {
          try {
            await fetch(API+'/api/ideas/'+encodeURIComponent(id), {method:'DELETE'});
            await loadIdeas();
          } catch(e) { reportError('删除失败: '+e.message); }
        }
      });
    }
    async function addMaterial() {
      openModal({
        title: '📚 新增素材', kind: 'input', label: '素材（格式：分类｜标题｜内容）', rows: 3, textarea: true,
        confirmText: '保存素材',
        onConfirm: async (raw) => {
          if (!raw || !raw.trim()) return;
          const parts = String(raw).split('｜');
          const category = (parts[0]||'桥段').trim();
          const title = (parts[1]||'').trim();
          const content = (parts.slice(2).join('｜')||parts[1]||'').trim();
          if (!title || !content) { reportError('格式：分类｜标题｜内容'); return; }
          try {
            const r = await fetch(API+'/api/ideas/materials', {method:'POST', headers:{'Content-Type':'application/json'},
              body:JSON.stringify({category:category, title:title, content:content})});
            if (!r.ok) throw new Error('HTTP '+r.status);
            await loadMaterials();
          } catch(e) { reportError('添加失败: '+e.message); }
        }
      });
    }
    function ideaToNovel(text) {
      openCreate();
      form.inspiration = text;
      currentView.value = 'create';
    }
    function switchIdeasTab(t) { ideasTab.value=t; }

    function returnToPrevious() { currentView.value = _returnView.value; }

    function openSettings() {
      currentView.value = 'settings';
      refreshHealth();
      loadLLMProviders();
    }
    async function refreshHealth() {
      if (loading.health) return;
      loading.health = true;
      try {
        health.value = await NovelUI.requestJson('/api/health', { timeout: 8000 });
        Object.assign(provider, { configured: health.value.llm_configured, model: health.value.model });
      } catch (_) {
        health.value = { status: 'offline', llm_configured: false, storage_exists: false };
      } finally { loading.health = false; }
    }
    let llmConfigRequest = null;
    function loadLLMProviders() {
      if (llmConfigRequest) return llmConfigRequest;
      llmConfigRequest = (async () => {
        try {
          const [catalog, saved] = await Promise.all([
            NovelUI.requestJson('/api/config/llm/providers', { timeout: 8000 }),
            NovelUI.requestJson('/api/config/llm', { timeout: 8000 })
          ]);
          llmProviders.value = catalog.providers || [];
          llmProvider.value = saved.provider || 'deepseek';
          llmBaseUrl.value = saved.base_url || '';
          llmModel.value = saved.model || '';
          const meta = llmProviderMeta.value;
          llmCustomModelMode.value = !meta?.models?.includes(llmModel.value);
          llmCustomModel.value = llmCustomModelMode.value ? llmModel.value : '';
        } catch (e) { reportError(e.message, '模型配置读取失败'); }
        finally { llmConfigRequest = null; }
      })();
      return llmConfigRequest;
    }
    function selectLLMProvider(providerId) {
      const preset = llmProviders.value.find(p => p.id === providerId);
      if (!preset) return;
      llmProvider.value = providerId;
      llmBaseUrl.value = preset.base_url || '';
      llmModel.value = preset.default_model || '';
      llmCustomModelMode.value = !preset.models?.length;
      llmCustomModel.value = '';
      llmApiKey.value = '';
      llmKeySaved.value = false;
      llmTested.value = false;
    }
    function onLLMModelSelect() {
      if (llmModel.value === '__custom__') {
        llmCustomModelMode.value = true;
        llmCustomModel.value = '';
      }
      llmTested.value = false;
    }
    async function saveLLMConfig(testAfterSave = false) {
      if (llmSaving.value) return;
      llmSaving.value = true;
      llmTesting.value = false;
      llmKeySaved.value = false;
      llmTested.value = false;
      try {
        const model = String(llmEffectiveModel.value || '').trim();
        if (!model) throw new Error('请先选择或填写模型 ID');
        const body = { provider: llmProvider.value, base_url: llmBaseUrl.value.trim(), model };
        const key = llmApiKey.value.trim();
        if (key) body.api_key = key;
        const result = await NovelUI.requestJson('/api/config/llm', { method: 'POST', body, timeout: 10000 });
        llmKeySaved.value = true;
        llmApiKey.value = '';
        window.dispatchEvent(new CustomEvent('provider-changed', { detail: {
          configured: result.config.api_key_configured, model: result.config.model
        } }));
        if (testAfterSave) {
          llmTesting.value = true;
          await NovelUI.requestJson('/api/config/llm/test', { method: 'POST', timeout: 30000 });
          llmTested.value = true;
        }
        await refreshHealth();
        showToast(testAfterSave ? '连接测试通过，创作和角色扮演已使用该模型' : '配置已保存，创作和角色扮演立即生效', '模型服务', '✅');
      } catch (e) { reportError(e.message || '保存失败', '模型配置失败'); }
      finally { llmSaving.value = false; llmTesting.value = false; }
    }

    const isAndroid = Boolean(window.AndroidBridge);
    window.addEventListener('novelgen:connection', () => { openSettings(); });
    const provider = reactive({ configured: false, model: '' });
    window.addEventListener('provider-changed', event => Object.assign(provider, event.detail));
    const shelfQuery = ref(''), shelfFilter = ref('all'), shelfSort = ref('recent');
    const shelfStats = computed(() => ({
      chapters: novels.value.reduce((sum, n) => sum + (n.state?.completed_chapters || []).length, 0),
      active: novels.value.filter(n => (n.state?.completed_chapters || []).length > 0).length
    }));
    const filteredNovels = computed(() => {
      let result = novels.value.filter(n => ((n.title || '') + (n.genre || '') + (n.style || '')).toLowerCase().includes(shelfQuery.value.toLowerCase()));
      if (shelfFilter.value !== 'all') result = result.filter(n => shelfFilter.value === 'active' ? (n.state?.completed_chapters || []).length > 0 : !(n.state?.completed_chapters || []).length);
      if (shelfSort.value === 'title') result = [...result].sort((a,b) => a.title.localeCompare(b.title, 'zh-CN'));
      if (shelfSort.value === 'progress') result = [...result].sort((a,b) => (b.state?.completed_chapters || []).length - (a.state?.completed_chapters || []).length);
      return result;
    });
    watch(currentView, value => {
      if (!routeViews.includes(value)) return;
      const params = new URLSearchParams({view:value});
      if (value==='write' && activeNovel.value) params.set('novel',activeNovel.value.id);
      const target = '/?' + params;
      if(location.pathname+location.search!==target) history.pushState(null,'',target);
    });
    const restoreRoute = () => {
      const value = new URLSearchParams(location.search).get('view') || 'shelf';
      if(routeViews.includes(value)) currentView.value=value;
    };
    window.addEventListener('popstate',restoreRoute);
    onUnmounted(()=>window.removeEventListener('popstate',restoreRoute));
    const viewLabel = computed(() => ({worlds:'进入小说世界',shelf:'我的书架',create:'创作工作台',write:'写作空间',trends:'灵感探索',export:'作品导出',settings:'设置',bible:'世界圣经',stats:'创作统计',ideas:'灵感与素材'}[currentView.value] || '创作空间'));
    const todayLabel = new Intl.DateTimeFormat('zh-CN', {month:'long', day:'numeric', weekday:'long'}).format(new Date());

    return { isAndroid,provider,shelfQuery,shelfFilter,shelfSort,shelfStats,filteredNovels,viewLabel,todayLabel,currentView,novels,activeNovel,planResult,error,preview,styleCategories,exportSelected,toast,
      health,refreshHealth,applyAppearance,uiStyle,openSettings,
      requirementKindOrder,requirementSnapshot,requirementsForKind,requirementKindLabel,requirementKindHelp,requirementScopeText,
      outlineChangePlan,outlineExecutionSummary,outlineLoading,
      llmProviders,llmProvider,llmProviderMeta,llmApiKey,llmBaseUrl,llmModel,llmCustomModel,llmCustomModelMode,
      llmSaving,llmTesting,llmKeySaved,llmTested,llmEffectiveModel,loadLLMProviders,selectLLMProvider,onLLMModelSelect,saveLLMConfig,
      bibleData,bibleTab,bibleLoading,termCandidates,bibleEdit,statsData,statsLoading,
      ideasData,ideasTags,ideasTab,materialsData,ideasLoading,
      openBible,loadBible,saveBible,extractTerms,addTermFromCandidate,switchBibleTab,
      bibleStartNew,bibleStartEdit,bibleCancelEdit,bibleSaveEntry,bibleDeleteEntry,
      bibleStartRelation,bibleEditRelation,bibleDeleteRelation,
      openStats,loadStats,saveGoal,trendPoints,trendMax,trendW,trendH,
      openIdeas,loadIdeas,loadMaterials,addIdea,deleteIdea,addMaterial,ideaToNovel,switchIdeasTab,filterIdeasByTag,
      modal,closeModal,confirmModal,returnToPrevious,
      form,loading,writing,genres,editSaved,styleMode,customStyleDesc,styleSeeds,shelfLoadFailed,
      selectedCount,allSelected,availableChapters,currentOutline,
      pct,fmtWords,coverGradient,
      navigateMain,openNovelWorld,enterNovelWorld,openCreate,openWrite,openOutline,openOutlineFor,doCreate,openNovel,startWriting,openRoleplay,
      openTrends,doTrendAnalyze,doBizarreQuick,doBizarreFull,createBizarreNovel,trendData,bizarreNovels,trendLoading,bizarreMode,
      selectChapter,doGenerateChapter,nextChapter,copyChapter,speakChapter,stopSpeaking,speaking,
      toggleExport,selectAll,doSingleExport,doBatchExport,loadShelf,
      addChapter,addVolume,removeVolume,renumberChapters,savePlan,confirmAndStart,
      applyCustomStyle,saveStyleSeed,loadStyleSeed,deleteStyleSeed,toggleTheme,
      batchStart,batchEnd,batchProgress,batchCurrent,batchStatus,batchStreaming,exportProgress,writingModeLiterary,useAtomicMode,theme,
      doBatchGenerate,cancelBatch,onModeChange,isBusy,planReady,
      // v2.50
      xhsMode,xhsTemplate,xhsInspiration,xhsTemplates,xhsPresets,xhsPreset,xhsResult,xhsProgress,xhsPhase,xhsStreamText,doXhsCreate,loadXhsPresets,
      createProgress,createPhase,createPhaseLabel,decomposeSummary,reviewFeedback,doRegenerate,
      chapterProgress,chapterProgressLabel,outlineFeedback,doRegenerateOutline,isRegenerating,
      outlineCompliance,
      requirements,reqInspiration,showReqInput,supervisionReport,styleParams,customParams,
      decomposeRequirements,superviseRequirements,runVerifyLoop,addRequirements,
      loadStyleSeeds,loadStyleParams,applyParamStyle,
      // v2.3: 剧情图谱
      sgLoading,sgData,sgArcs,sgCalibration,
      // v3.5.56: showToast 模板有调用但未暴露（P10 静态检查抓出，@click 点击才触发未爆）
      showToast,
      sgStats,sgThreads,sgForeshadows,sgCharSnapshots,charProfiles,distillCharProfile,charProfiling,chapterRating,chapterReason,chapterFeedbackDone,submitChapterFeedback,
      statusColor,statusBg,statusLabel,tensionGradient,fsStatusColor,fsStatusBg,fsStatusLabel,
      switchToStoryGraph,loadStoryGraph,
      sgSubTab,sgChapterFilter,sgThreadFilter,sgForeshadowFilter,
      sgExpandedThread,sgExpandedForeshadow,sgExpandedChar,sgExpandedArc,
      filteredThreads,filteredForeshadows,
      // v2.4: 可视化
      sgHoveredChar,sgHoveredEdge,sgHoveredEvent,
      relationViewBox,relationNodes,relationEdges,relationGraph,
      relationDragging,relationSvgWrap,relationNodeLayout, hoveredCharDetail,
      relationHeight,timelineHeight,startResize,
      zoomRelation,startPanRelation,doPanRelation,endPanRelation,startDragRelationNode,
      edgeCurve,edgeCurveMid,lastActiveChapter,
      plotTimeline,timelineViewBox,timelineChapters,timelineChapterLabels,plotEndY,
      laneY,chToX,causalArrowPath,hoveredEventDetail,
      timelineSvgWrap,zoomTimeline,startPanTimeline,doPanTimeline,endPanTimeline,
      // v2.5: 编辑器
      editState,quickDoing,startEdit,cancelEdit,saveEdit,hasEdit,
      quickAction,deleteThread,deleteNovel,createThread,createForeshadow,
      // v2.6: 画布
      canvasZoom,canvasPan,canvasWrap,canvasInner,
      canvasWidth,canvasHeight,canvasLaneH,canvasTickChapters,
      chToCanvasX,tensionColor,
      zoomCanvas,startCanvasPan,doCanvasPan,endCanvasPan,
      canvasSelected,canvasDragging,canvasDragX,canvasDragY,canvasDragCh,canvasDragTension,
      startDragCanvasNode,startCanvasConnect,showCanvasMenu,hideCanvasMenu,canvasMenuAction,editCanvasNode,
      getLaneGaps,addEventAtGap,canvasToast,canvasMenu };
    } catch(e) {
      console.error('SETUP ERROR:', e.message, e.stack);
      NovelUI.showError(e.message || '工作台加载失败，请刷新');
      return {};
    }
  }
});

NovelApp.register(app);
NovelUI.install(app);
app.mount('#app');
