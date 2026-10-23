(function () {
  'use strict';
  const {createApp,ref,reactive,computed,onMounted,onUnmounted,watch}=Vue;
  const api=NovelApp.request;
  const app=createApp({setup(){
    const params=new URLSearchParams(location.search);
    const tab=ref(params.get('tab')==='store'?'store':'library');
    const book=ref(params.get('novel') || (tab.value==='library'?'*':''));
    const novels=ref([]),characters=ref([]),items=ref([]),query=ref(''),source=ref('official');
    const page=ref(1),total=ref(0),loading=ref(false),error=ref(''),notice=ref('');
    const remoteLink=ref(''),preview=ref(null),previewLoading=ref(false),installing=ref('');
    const editing=ref(null),targetBook=ref(''),saving=ref(false),fileInput=ref(null),failedImages=reactive({});
    const previewFields=[{key:'description',label:'角色设定'},{key:'personality',label:'性格'},{key:'scenario',label:'场景'},{key:'first_mes',label:'开场白'},{key:'creator_notes',label:'作者说明'}];
    let listController=null,previewController=null,loadSequence=0,previewSequence=0,noticeTimer=null,searchTimer=null;
    const filtered=computed(()=>characters.value.filter(c=>(book.value==='*'||(c.novel_id||'')===book.value)&&[c.name,c.description,...(c.tags||[])].join(' ').toLowerCase().includes(query.value.trim().toLowerCase())));
    const bookTitle=id=>id?(novels.value.find(n=>n.id===id)?.title||'原小说已移除'):'未分组角色';
    const plain=text=>String(text||'').replace(/<[^>]*>/g,'').replace(/[*#_`]/g,'').slice(0,240);
    const gradient=name=>{const hue=Array.from(String(name)).reduce((a,c)=>a+c.charCodeAt(0),0)%360;return `linear-gradient(135deg,hsl(${hue} 35% 82%),hsl(${(hue+50)%360} 30% 66%))`;};
    function notify(text){notice.value=text;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>notice.value='',6000);}
    function updateUrl(){const p=new URLSearchParams({tab:tab.value});if(book.value&&book.value!=='*')p.set('novel',book.value);history.replaceState(null,'','/cards.html?'+p);}
    async function loadLibrary(options={}){const d=await api('/api/roleplay/characters?summary=true',options);characters.value=d.characters||[];}
    async function reload(){
      const sequence=++loadSequence;
      listController?.abort();listController=new AbortController();loading.value=true;error.value='';
      try{
        if(tab.value==='store'){
          const p=new URLSearchParams({source:source.value,q:query.value,page:page.value});
          const d=await api('/api/roleplay/marketplace/catalog?'+p,{signal:listController.signal});
          if(sequence!==loadSequence)return;
          items.value=d.items||[];total.value=d.total||0;
        }else {
          const d=await api('/api/roleplay/characters?summary=true',{signal:listController.signal});
          if(sequence===loadSequence)characters.value=d.characters||[];
        }
      }catch(e){if(sequence===loadSequence)error.value=e.message;}
      finally{if(sequence===loadSequence)loading.value=false;}
    }
    function search(){clearTimeout(searchTimer);page.value=1;reload();}
    function changeBook(){updateUrl();}
    function setTab(value){clearTimeout(searchTimer);tab.value=value;query.value='';page.value=1;if(value==='store'&&book.value==='*')book.value='';updateUrl();reload();}
    function turnPage(delta){if(loading.value)return;page.value=Math.max(1,Math.min(Math.max(1,Math.ceil(total.value/24)),page.value+delta));reload();window.scrollTo({top:0,behavior:'smooth'});}
    async function previewCard(card){
      const sequence=++previewSequence;
      previewController?.abort();previewController=new AbortController();preview.value=null;previewLoading.value=true;error.value='';
      try{
        const d=card.id?await api('/api/roleplay/characters/'+encodeURIComponent(card.id),{signal:previewController.signal}):await api('/api/roleplay/marketplace/preview',{method:'POST',body:{source:card.source,reference:card.reference},signal:previewController.signal,timeout:45000});
        if(sequence===previewSequence)preview.value=d.character;
      }catch(e){if(sequence===previewSequence){error.value=e.message;preview.value=null;}}
      finally{if(sequence===previewSequence)previewLoading.value=false;}
    }
    function previewLink(){previewCard({source:'chub',reference:remoteLink.value.trim()});}
    function closePreview(){++previewSequence;previewController?.abort();preview.value=null;previewLoading.value=false;}
    async function install(card){
      if(installing.value)return;installing.value=card.reference;error.value='';
      try{
        const d=await api('/api/roleplay/marketplace/install',{method:'POST',body:{source:card.source,reference:card.reference,novel_id:book.value==='*'?'':book.value},timeout:45000});
        await loadLibrary();closePreview();notify(d.reused?'这张卡已在所选小说中，已保留现有设定和对话。':'「'+d.character.name+'」已加入'+bookTitle(d.character.novel_id));
      }catch(e){error.value=e.message;}finally{installing.value='';}
    }
    function manage(card){editing.value=card;targetBook.value=card.novel_id||'';}
    async function saveGroup(copy){
      if(!editing.value||saving.value)return;saving.value=true;error.value='';
      try{const id=editing.value.id;await api('/api/roleplay/characters/'+encodeURIComponent(id)+(copy?'/copy':''),{method:copy?'POST':'PUT',body:{novel_id:targetBook.value}});editing.value=null;await loadLibrary();notify(copy?'已创建独立角色副本，原角色和聊天保持原样。':'分组已更新，聊天记录已保留。');}
      catch(e){error.value=e.message;}finally{saving.value=false;}
    }
    async function importFile(event){
      const file=event.target.files?.[0];event.target.value='';if(!file)return;
      if(file.size>8*1024*1024){error.value='角色卡最大支持 8MB';return;}
      loading.value=true;error.value='';
      try{const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('读取角色卡失败'));reader.readAsDataURL(file);});const d=await api('/api/roleplay/characters/import',{method:'POST',body:{filename:file.name,data_base64:data,novel_id:book.value==='*'?'':book.value}});tab.value='library';updateUrl();await loadLibrary();notify('「'+d.character.name+'」已导入，世界书资料已一并保留。');}
      catch(e){error.value=e.message;}finally{loading.value=false;}
    }
    async function exportCard(card){try{const data=await api('/api/roleplay/characters/'+encodeURIComponent(card.id)+'/export?format=json');NovelUI.download(card.name+'.json',JSON.stringify(data,null,2),'application/json');}catch(e){error.value=e.message;}}
    function toggleTheme(){NovelUI.setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark');}
    function escape(event){if(event.key==='Escape'){closePreview();editing.value=null;}}
    watch(query,()=>{if(tab.value==='store'){clearTimeout(searchTimer);searchTimer=setTimeout(search,500);}});
    onMounted(async()=>{window.addEventListener('keydown',escape);const initial=reload();try{const d=await api('/api/novels');novels.value=d.novels||[];if(book.value&&book.value!=='*'&&!novels.value.some(n=>n.id===book.value))book.value='';}catch(e){error.value=e.message;}await initial;});
    onUnmounted(()=>{++loadSequence;++previewSequence;listController?.abort();previewController?.abort();clearTimeout(searchTimer);clearTimeout(noticeTimer);window.removeEventListener('keydown',escape);});
    return {tab,book,novels,characters,items,query,source,page,total,loading,error,notice,remoteLink,preview,previewLoading,installing,editing,targetBook,saving,fileInput,failedImages,previewFields,filtered,bookTitle,plain,gradient,reload,search,changeBook,setTab,turnPage,previewCard,previewLink,closePreview,install,manage,saveGroup,importFile,exportCard,toggleTheme,chatUrl:NovelApp.characterUrl};
  }});
  NovelApp.register(app);app.mount('#app');
})();
