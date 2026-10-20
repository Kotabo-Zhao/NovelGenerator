const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const world = require('../web/assets/scene-runtime.js');
const window = {addEventListener:()=>{}};
const context = {window, document:{documentElement:{dataset:{}},querySelector:()=>null,addEventListener:()=>{},querySelectorAll:()=>[]},
  localStorage:{getItem:()=>null,setItem:()=>{}}, setTimeout,clearTimeout,setInterval,clearInterval,
  TextDecoder,AbortController,Blob,URL};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/assets/runtime.js','utf8'),context);
function response(events) {
  const bytes = new TextEncoder().encode(events.map(e=>'data: '+JSON.stringify(e)+'\r\n\r\n').join(''));
  return new Response(new ReadableStream({start(c) {
    for (let i=0; i<bytes.length; i+=3) c.enqueue(bytes.slice(i,i+3));
    c.close();
  }}),{headers:{'content-type':'text/event-stream'}});
}
(async()=>{
  const draft={text:'',sceneNum:1};
  const events=[{type:'scene_chunk',content:'旧剧情'}, {type:'scene_retry'},
    {type:'scene_chunk',content:[{type:'text',text:'新的🌙剧情'}]},
    {type:'scene_chunk',content:{usage:100}}, {type:'done'}];
  await world.readEvents(response(events),e=>world.updateDraft(draft,e),window.NovelUI.readSSE);
  assert.equal(draft.text,'新的🌙剧情');
  const received=[];
  await world.readEvents(response([...events,{type:'scene_chunk',content:'不能泄漏的尾部'}]),e=>received.push(e.type),window.NovelUI.readSSE);
  assert.deepEqual(received,events.map(e=>e.type));
  await assert.rejects(()=>world.readEvents(response(events),()=>{throw new Error('render failure');},window.NovelUI.readSSE),/render failure/);
  const history=Array.from({length:5000},(_,id)=>({id}));
  assert.equal(world.historyWindow(history,80).length,80);
  assert.equal(world.historyWindow(history,160)[0].id,4840);
  assert.equal(history.length,5000);
  console.log('Novel world stream retry, Unicode, object content, terminal events, failures and history: passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
