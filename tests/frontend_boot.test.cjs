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
    sessionStorage:{getItem:()=>null,setItem(){}},history:{},navigator:{},
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
console.log('All four page setups and roleplay timestamp handling passed.');
