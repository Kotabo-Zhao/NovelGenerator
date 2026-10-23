/* Execute each page's setup, so missing runtime helpers cannot hide behind a syntax check. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function boot(page, search = '') {
  const errors = [], hooks = [], storage = new Map();
  let state, app;
  const element = {style:{},dataset:{},setAttribute(){},getAttribute(){return null;}};
  const document = {documentElement:element,body:element,getElementById:()=>element,
    querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){}};
  const location = {origin:'http://localhost',pathname:'/',search};
  const window = {location,innerWidth:1360,addEventListener(){},removeEventListener(){}};
  const Vue = {
    ref:value=>({value}),reactive:value=>value,computed:get=>({get value(){return get();}}),
    watch(){},nextTick(){},onMounted:fn=>hooks.push(fn),onUnmounted(){},onErrorCaptured(){},
    createApp(options){
      app = {config:{},component(){return app;},mount(){state=options.setup();}};
      return app;
    }
  };
  const context = vm.createContext({window,document,location,Vue,console,URL,URLSearchParams,
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    sessionStorage:{getItem:key=>storage.get('session:'+key)||null,setItem:(key,value)=>storage.set('session:'+key,value),removeItem:key=>storage.delete('session:'+key)},history:{replaceState(){}},navigator:{},
    setTimeout,clearTimeout,setInterval,clearInterval,AbortController,TextDecoder,
    matchMedia:()=>({matches:false,addEventListener(){}})});
  for (const file of ['runtime.js','app-shell.js']) {
    vm.runInContext(fs.readFileSync('web/assets/'+file,'utf8'),context,{filename:file});
    context.NovelUI=window.NovelUI;context.NovelApp=window.NovelApp;
  }
  const original = context.NovelUI.showError;
  context.NovelUI.showError=message=>errors.push(message);
  vm.runInContext(fs.readFileSync('web/assets/'+page,'utf8'),context,{filename:page});
  context.NovelUI.showError=original;
  assert.deepEqual(errors,[],page+' setup reported an error');
  assert.ok(state && Object.keys(state).length>10,page+' must expose usable page state');
  assert.equal(typeof app.config.errorHandler,'function',page+' must report Vue errors visibly');
  return {state,app,context};
}

const roleplay=boot('roleplay.js','?character=builtin-suwan');
assert.equal(typeof roleplay.state.fmtTime,'function');
assert.equal(roleplay.state.fmtTime(null),'');
assert.equal(roleplay.state.fmtTime('bad'),'');
assert.equal(roleplay.state.fmtTime(0),'');
assert.equal(roleplay.state.fmtTime(1700000000),roleplay.state.fmtTime(1700000000000));
assert.match(roleplay.state.fmtTime(Date.now()),/^\d{2}:\d{2}$/);
assert.equal(typeof roleplay.state.sendComposer,'function');
assert.equal(typeof roleplay.state.openSettings,'function');
const scene=boot('scene.js','?novel=smoke-book');
const cards=boot('cards.js');
const workshop=boot('workshop.js','?view=settings');
assert.equal(workshop.state.currentView.value,'settings');
async function cardEditorRoundtrip() {
  const source={id:'fixture',name:'Guard',description:'D'.repeat(25000),personality:'Calm',
    creator_notes:'Instructions',alternate_greetings:['First paragraph\n\nSecond paragraph','Another\nGreeting']};
  roleplay.state.openEdit(source);
  assert.deepEqual(Array.from(roleplay.state.charForm.alternate_greetings),source.alternate_greetings);
  assert.equal(roleplay.state.charForm.creator_notes,source.creator_notes);
  let payload;
  roleplay.context.fetch=async (url,init)=>{
    if(init && init.method==='PUT') payload=JSON.parse(init.body);
    return {ok:true,status:200,json:async()=>({character:{id:'fixture'},characters:[]})};
  };
  await roleplay.state.saveCharacter();
  assert.ok(payload,'editing must send a payload');
  assert.equal(payload.description,source.description);
  assert.equal(payload.personality,source.personality);
  assert.equal(payload.creator_notes,source.creator_notes);
  assert.deepEqual(payload.alternate_greetings,source.alternate_greetings);
  assert.notEqual(roleplay.state.charForm.alternate_greetings,source.alternate_greetings);
}
async function characterSelectionRace() {
  const {state,context}=boot('roleplay.js');
  const a={id:'a',name:'Alpha',_summary:true},b={id:'b',name:'Beta',_summary:true};
  state.characters.value=[a,b];
  let resolveA;
  const response=data=>({ok:true,status:200,json:async()=>data});
  context.fetch=async(url)=>{
    if(url.endsWith('/a'))return new Promise(resolve=>{resolveA=resolve;});
    if(url.endsWith('/b'))return response({character:{id:'b',name:'Beta',description:'Full setting',personality:'Patient',alternate_greetings:['First\nSecond']}});
    if(url.endsWith('/identity'))return response({identity:{user_name:'Player'},choices:[],compatibility:{}});
    if(url.endsWith('/chat'))return response({messages:[{role:'assistant',content:'Welcome'}],identity_bound:true});
    if(url.endsWith('/worldinfo'))return response({entries:[]});
    throw new Error('Unexpected URL '+url);
  };
  const pending=state.selectFromLibrary(a);
  await state.selectFromLibrary(b);
  resolveA(response({character:{id:'a',name:'Alpha',description:'Stale'}}));
  await pending;
  assert.equal(state.currentCharacter.value.id,'b');
  assert.equal(state.currentCharacter.value.description,'Full setting');
  assert.equal(state.chatLoading.value,false);
  await state.openEdit(b);
  assert.equal(state.charForm.description,'Full setting');
  assert.deepEqual(Array.from(state.charForm.alternate_greetings),['First\nSecond']);
  state.draft.value='Unsent Beta draft';
  const next=state.selectFromLibrary(a);
  resolveA(response({character:{id:'a',name:'Alpha'}}));await next;
  state.draft.value='Unsent Alpha draft';
  await state.selectFromLibrary(b);
  assert.equal(state.draft.value,'Unsent Beta draft');
}
Promise.all([cardEditorRoundtrip(),characterSelectionRace()]).then(()=>console.log('All four page setups, timestamps and lossless card editor roundtrip passed.')).catch(error=>{console.error(error);process.exitCode=1;});
