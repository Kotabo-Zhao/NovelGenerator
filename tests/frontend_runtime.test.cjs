/* Run with node tests/frontend_runtime.test.cjs. Tests the public stream contract. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const document = {documentElement:{dataset:{}},querySelector:()=>null,addEventListener:()=>{},querySelectorAll:()=>[]};
const window = {addEventListener:()=>{}};
const context = {window,document,localStorage:{getItem:()=>null,setItem:()=>{}},setTimeout,clearTimeout,setInterval,clearInterval,
  TextDecoder,AbortController,Blob,URL,fetch,console};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/assets/runtime.js','utf8'),context);
const ui = window.NovelUI;
const encoder = new TextEncoder();
let checks = 0;

async function parse(text, chunkSize = 1) {
  const bytes = encoder.encode(text), events=[];
  const response = new Response(new ReadableStream({start(controller) {
    for(let i=0;i<bytes.length;i+=chunkSize) controller.enqueue(bytes.slice(i,i+chunkSize));
    controller.close();
  }}), {headers:{'Content-Type':'text/event-stream'}});
  await ui.readSSE(response,event=>events.push(event));
  return events;
}

(async () => {
  let events = await parse(': heartbeat\r\ndata: {"type":"delta","content":"你好🌙"}\r\n\r\ndata: {"type":"done"}\r\n\r\n');
  assert.equal(events[0].content,'你好🌙'); assert.equal(events[1].type,'done'); checks+=2;
  events=await parse('data:{"type":"delta",\ndata: "content":"多行事件"}\n\ndata:[DONE]\n\n',3);
  assert.equal(events[0].content,'多行事件'); assert.equal(events[1].type,'done'); checks+=2;
  events=await parse('data:{"type":"done"}'); assert.equal(events[0].type,'done'); checks++;
  events=await parse('data:{"type":"delta","content":"CR"}\r\rdata:[DONE]\r\r'); assert.equal(events[0].content,'CR'); checks++;
  await assert.rejects(()=>parse('data: invalid json\n\n'),/格式异常/); checks++;
  await assert.rejects(()=>ui.readSSE(new Response('bad',{status:500}),()=>{}),/500/); checks++;
  await assert.rejects(()=>ui.readSSE(new Response('{}',{headers:{'Content-Type':'application/json'}}),()=>{}),/消息流/); checks++;
  const response=new Response('data: {"type":"delta"}\n\n',{headers:{'Content-Type':'text/event-stream'}});
  await assert.rejects(()=>ui.readSSE(response,()=>{throw new Error('handler failure');}),/handler failure/); checks++;
  assert.equal(ui.textContent({choices:[{delta:{content:[{type:'text',text:'正文'},{type:'image_url',image_url:'private'}]}}]}),'正文'); checks++;
  assert.equal(ui.textContent({usage:{total_tokens:23}}),''); checks++;
  assert.equal(ui.textContent('{"content":"正文\\n第二行"}'),'正文\n第二行'); checks++;
  assert.equal(ui.textContent({delta:{reasoning_content:'hidden'}}),''); checks++;
  const html=ui.renderMessage('*动作*\n**台词** <img src=x onerror=alert(1)> `code`');
  assert.ok(html.includes('<em>动作</em>')); assert.ok(html.includes('<strong>台词</strong>')); checks+=2;
  assert.ok(!html.includes('<img')); assert.ok(html.includes('&lt;img')); checks+=2;
  assert.equal(ui.renderMessage('```html\n<script>alert(1)</script>```'),'<pre><code>&lt;script&gt;alert(1)&lt;/script&gt;</code></pre>'); checks++;
  assert.ok(ui.renderMessage('普通 {JSON} 和 C:\\new 路径').includes('C:\\new')); checks++;
  await assert.rejects(()=>parse('data: '+ 'x'.repeat(2*1024*1024+1),65536),/事件过大/); checks++;
  await assert.rejects(()=>parse(('data: '+ 'x'.repeat(1024)+'\n').repeat(2050),65536),/事件过大/); checks++;
  const originalFetch=context.fetch;
  context.fetch=(_,options)=>new Promise((_,reject)=>{
    const abort=()=>{const e=new Error('aborted');e.name='AbortError';reject(e);};
    if(options.signal.aborted)abort();else options.signal.addEventListener('abort',abort,{once:true});
  });
  const cancellation=new AbortController();
  const cancelled=ui.requestJson('/test',{signal:cancellation.signal});cancellation.abort();
  await assert.rejects(cancelled,e=>e.name==='AbortError');checks++;
  await assert.rejects(ui.requestJson('/test',{timeout:5}),e=>e.name==='TimeoutError');checks++;
  context.fetch=originalFetch;
  let saved;
  context.FileReader = class {
    readAsDataURL(blob) {
      blob.arrayBuffer().then(data => {
        this.result = 'data:application/octet-stream;base64,' + Buffer.from(data).toString('base64');
        this.onload();
      });
    }
  };
  window.AndroidBridge = {saveFile: (...args) => { saved = args; }};
  ui.download('测/试.json', '{"你好":"🌙"}', 'application/json');
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(saved[0], '测_试.json'); checks++;
  assert.equal(saved[1], 'application/json'); checks++;
  assert.equal(Buffer.from(saved[2], 'base64').toString('utf8'), '{"你好":"🌙"}'); checks++;
  ui.download('binary.pdf', new Blob([new Uint8Array([0,255,37])]), 'application/pdf');
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.deepEqual([...Buffer.from(saved[2], 'base64')], [0,255,37]); checks++;
  console.log(`Frontend stream and rendering regressions: ${checks} passed.`);
})().catch(error=>{console.error(error);process.exitCode=1;});
