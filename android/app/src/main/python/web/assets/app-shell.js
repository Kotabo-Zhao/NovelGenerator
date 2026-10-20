/* One route map and one navigation component for every desktop/mobile page. */
(function () {
  'use strict';
  const routes = [
    {id:'shelf',label:'书架',url:'/?view=shelf',icon:'M4 4h6v6H4z M14 4h6v6h-6z M4 14h6v6H4z M14 14h6v6h-6z'},
    {id:'create',label:'创作',url:'/?view=create',icon:'M12 20H4v-8 M8 16 19 5l-3-3L5 13l-1 7 7-1'},
    {id:'worlds',label:'入世',url:'/?view=worlds',icon:'M12 3 21 7v10l-9 4-9-4V7z M3 7l9 4 9-4 M12 11v10'},
    {id:'characters',label:'角色',url:'/cards.html?tab=library',icon:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M17 4a4 4 0 0 1 0 7 M22 21v-2a4 4 0 0 0-3-4'},
    {id:'store',label:'商店',url:'/cards.html?tab=store',icon:'M3 9h18L19 3H5z M4 9v12h16V9 M9 21v-7h6v7'},
    {id:'settings',label:'设置',url:'/?view=settings',icon:'M4 7h16 M4 17h16 M8 3v8 M16 13v8'}
  ];
  const component = {
    props:{active:{type:String,default:'shelf'},novel:{type:String,default:''},desktop:{type:Boolean,default:false}},
    emits:['navigate'],
    computed:{routes:()=>routes},
    methods:{link(route){return route.url + (this.novel && ['characters','store'].includes(route.id) ? '&novel='+encodeURIComponent(this.novel) : '');}},
    template:`<nav class="app-bottom-nav" :class="{'shell-desktop':desktop}" aria-label="主导航"><a v-for="route in routes" :key="route.id" class="bn-item" :class="{active:active===route.id}" :href="link(route)" :aria-current="active===route.id?'page':null" @click="$emit('navigate',route.id,$event)"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="route.icon"></path></svg><span>{{route.label}}</span></a></nav>`
  };
  window.NovelApp = {
    version:'4.2.0', routes,
    register(app){NovelUI.guard(app);app.component('app-navigation',component);},
    async request(path,options={}) {
      const headers = {...options.headers};
      for (const key of ['access_token','token','auth_token','rp_access_token']) {
        const token = NovelUI.storage.get(key);
        if (token) { headers.Authorization='Bearer '+token.replace(/^Bearer\s+/i,'');break; }
      }
      return NovelUI.requestJson(path,{...options,headers});
    },
    characterUrl(card){return '/roleplay.html?character='+encodeURIComponent(card.id)+(card.novel_id?'&novel='+encodeURIComponent(card.novel_id):'');}
  };
})();
