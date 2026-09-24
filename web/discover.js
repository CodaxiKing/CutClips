(() => {
  'use strict';
  const $=id=>document.getElementById(id), key='cutclips.discover.v1', legacyKey='clipforge.discover.v1'; // chave do nome anterior, para não perder candidatos salvos
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function videoURL(raw){
    const u=new URL(raw.trim());
    if(u.protocol!=='https:'||u.username||u.password||u.port)throw Error('Use um link HTTPS de vídeo do TikTok.');
    if(!((['www.tiktok.com','tiktok.com','m.tiktok.com'].includes(u.hostname)&&/^\/@[^/]+\/video\/\d+\/?$/.test(u.pathname))||(['vm.tiktok.com','vt.tiktok.com'].includes(u.hostname)&&/^\/[\w-]+\/?$/.test(u.pathname))))throw Error('Cole o link de um vídeo, não de um perfil ou busca.');
    u.hash='';u.search='';return u.href;
  }
  let candidates=[],loadError='';
  try{const saved=JSON.parse(localStorage.getItem(key)??localStorage.getItem(legacyKey)??'[]');if(!Array.isArray(saved))throw Error();candidates=saved.slice(0,200).filter(e=>{try{return typeof e.name==='string'&&e.name.length<=32&&videoURL(e.url)===e.url;}catch{return false;}});}catch{loadError='Não foi possível recuperar a lista salva neste navegador.';}
  const selected=new Set();
  const nav=document.createElement('a');nav.href='#/discover';nav.dataset.nav='discover';nav.textContent='Descobrir';document.querySelector('.nav-links').insertBefore(nav,document.querySelector('.nav-links > .nav-group'));
  if(location.hash==='#/discover')nav.setAttribute('aria-current','page');
  $('discover').innerHTML=`<header><p class="discover-kicker">DESCOBRIR · TIKTOK</p><h1>Encontre os vídeos do seu ranking.</h1><p>Escolha um tópico, veja os vídeos com mais visualizações e preencha um Top 3, 4 ou 5 com um clique.</p></header>
    <section class="discover-trending" aria-labelledby="trendingTitle">
    <div class="discover-trending-head"><div><h2 id="trendingTitle">Mais vistos por tópico</h2><p class="discover-note">Em alta no TikTok nos últimos 30 dias.</p></div><button id="trendingRefresh" class="btn btn-outline btn-sm" type="button">Atualizar</button></div>
    <details class="discover-about"><summary>Sobre os dados</summary><ul>
      <li>Tópicos: lista pública de vídeos em alta da Central de Criação do TikTok (últimos 7 e 30 dias), só de Estados Unidos, Japão, Indonésia, Tailândia e Vietnã. O TikTok não publica essa lista para o Brasil.</li>
      <li>Dance e a busca (Enter no campo de busca): YouTube Shorts por hashtag, com vídeos do Brasil e do mundo. Uma palavra vira hashtag: “futebol brasil” busca #futebolbrasil, #futebol e #brasil.</li>
      <li>As visualizações do YouTube são arredondadas pelo próprio YouTube. A lista do TikTok inclui conteúdo impulsionado; “Sem publis” esconde só o que está marcado (#ad, parceria).</li>
      <li>A prévia de um Shorts no editor é baixada em qualidade leve na primeira vez (alguns segundos).</li>
    </ul></details>
    <div class="discover-topics" id="trendingTopics" role="group" aria-label="Tópicos"></div>
    <div class="discover-filters"><form class="discover-search" id="trendingSearchForm" role="search"><label><span class="sr-only">Filtrar ou buscar</span><input id="trendingSearch" type="search" maxlength="60" placeholder="Filtrar a lista ou buscar no YouTube Shorts" autocomplete="off"></label><button class="btn btn-outline btn-sm" type="submit" id="trendingSearchGo" hidden>Buscar no Shorts ↵</button></form><label><span class="sr-only">Região</span><select id="trendingRegion"><option value="">Todas as regiões</option></select></label><label><span class="sr-only">Ordenar por</span><select id="trendingSort"><option value="views">Mais visualizações</option><option value="organic">Mais visualizações orgânicas</option></select></label><label class="discover-check"><input id="trendingHideAds" type="checkbox" checked> Sem publis</label></div>
    <div class="discover-tagbar"><span>Filtrar por hashtag</span><div id="trendingTags" role="group" aria-label="Hashtags dos vídeos"></div></div>
    <div class="discover-fill"><div><strong id="trendingStatus" role="status" aria-live="polite">Carregando…</strong><small id="trendingWarn"></small></div><div class="discover-fill-actions"><span id="trendingPicked">Montar ranking</span><button id="trendingClear" class="btn btn-outline btn-sm" type="button" hidden>Limpar seleção</button>${[3,4,5].map(n=>`<button class="btn btn-primary btn-sm" type="button" data-fill-top="${n}" disabled>Top ${n}</button>`).join('')}</div></div>
    <ul class="discover-videos" id="trendingVideos"></ul>
    <details class="discover-hot" id="trendingHotBox"><summary id="trendingHotSummary">Hashtags em alta no Brasil</summary><div class="discover-hot-body"><label>Região<select id="trendingTagRegion"></select></label><div id="trendingHot" role="group" aria-label="Hashtags em alta"></div><small>Referência da Central de Criação, com o número de publicações. Sem login o TikTok não mostra os vídeos dessas hashtags; só dá para filtrar as que aparecem nos vídeos acima.</small></div></details>
    </section>
    <div class="discover-columns"><section class="discover-panel"><h2>Explorar dança</h2><p>Buscas para descobrir vídeos — não são tendências confirmadas.</p>
    <label>Coreografia, música ou hashtag<input id="discoverQuery" maxlength="160" value="dance challenge" placeholder="Ex.: nome da música + dance"></label>
    <div class="discover-row"><label>Região da busca<select id="discoverRegion"><option value="">Mundo todo</option>${[['Brazil','Brasil'],['United States','Estados Unidos'],['United Kingdom','Reino Unido'],['South Korea','Coreia do Sul'],['Japan','Japão'],['Mexico','México'],['France','França'],['Nigeria','Nigéria'],['South Africa','África do Sul'],['Philippines','Filipinas']].map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label></div>
    <p class="discover-note">O país é acrescentado ao texto da busca; não restringe a localização dos criadores.</p>
    <div class="discover-tags">${[['dance challenge','Desafios'],['dance choreography','Coreografias'],['duo dance','Duplas'],['dance crew','Grupos'],['street dance','Dança de rua'],['dance performance','Performances']].map(([v,l])=>`<button class="btn btn-outline btn-sm" data-dance-query="${v}">${l}</button>`).join('')}</div>
    <a id="discoverSearch" class="btn btn-primary" target="_blank" rel="noopener noreferrer">Buscar no TikTok ↗</a>
    <hr style="border:0;border-top:1px solid var(--line);margin:28px 0"><h2>Salvar um candidato</h2><form id="discoverForm"><label>Link do vídeo<input id="discoverUrl" type="url" required maxlength="600" placeholder="https://www.tiktok.com/@perfil/video/…"></label><label>Nome no ranking<input id="discoverName" required maxlength="32" placeholder="Ex.: A melhor sincronia"></label><label>Coleção / música<input id="discoverCollection" maxlength="80" placeholder="Ex.: versões da mesma coreografia"></label><button class="btn btn-outline" type="submit">Salvar candidato</button></form></section>
    <section class="discover-panel"><h2>Seus candidatos</h2><p class="discover-note">Salvos neste navegador. Selecione de 3 a 5 vídeos; a ordem da lista será a posição no ranking.</p><label>Filtrar coleção<select id="discoverFilter"><option value="">Todas as coleções</option></select></label><ul class="discover-candidates" id="discoverList"></ul><label>Título do ranking<input id="discoverHeadline" maxlength="100" placeholder="Automático: TOP 3, 4 ou 5 DANCE"></label><div class="discover-row"><button id="discoverImport" class="btn btn-primary" disabled>Usar no ranking (0/5)</button><button id="discoverClear" class="btn btn-outline btn-sm">Limpar seleção</button></div><p class="discover-note">Você poderá ajustar os cortes no editor antes de gerar. Salvar um link não transfere direitos de uso do vídeo ou da música.</p></section></div><p id="discoverStatus" class="discover-status" role="status" aria-live="polite"></p>`;
  function status(message){$('discoverStatus').textContent=message;}
  function save(next){try{localStorage.setItem(key,JSON.stringify(next));candidates=next;return true;}catch{status('Não foi possível salvar. Verifique o espaço e as permissões de armazenamento do navegador.');return false;}}
  function updateSearch(){const q=[$('discoverQuery').value.trim()||'dance challenge',$('discoverRegion').value].filter(Boolean).join(' ');$('discoverSearch').href='https://www.tiktok.com/search?q='+encodeURIComponent(q);}
  $('discoverQuery').oninput=updateSearch;$('discoverRegion').onchange=updateSearch;updateSearch();
  document.querySelectorAll('[data-dance-query]').forEach(b=>b.onclick=()=>{$('discoverQuery').value=b.dataset.danceQuery;updateSearch();});
  function render(){
    const filter=$('discoverFilter').value,collections=[...new Set(candidates.map(e=>e.collection||'').filter(Boolean))];
    $('discoverFilter').innerHTML='<option value="">Todas as coleções</option>'+collections.map(c=>`<option value="${esc(c)}">${esc(c)}</option>`).join('');$('discoverFilter').value=collections.includes(filter)?filter:'';
    const rows=candidates.map((e,i)=>({e,i})).filter(({e})=>!$('discoverFilter').value||e.collection===$('discoverFilter').value);
    $('discoverList').innerHTML=rows.length?rows.map(({e,i})=>`<li class="discover-candidate"><input type="checkbox" id="dc-${i}" data-select="${i}" ${selected.has(e.url)?'checked':''} aria-label="Selecionar ${esc(e.name)}"><label for="dc-${i}"><strong>${esc(e.name)}</strong><small>${esc(e.collection||'Sem coleção')}</small><a href="${esc(e.url)}" target="_blank" rel="noopener noreferrer">Abrir vídeo ↗</a></label><button class="btn btn-outline btn-sm" data-up="${i}" ${i===0?'disabled':''} aria-label="Mover ${esc(e.name)} para cima">↑</button><button class="btn btn-outline btn-sm" data-remove="${i}" aria-label="Remover ${esc(e.name)}">×</button></li>`).join(''):'<li class="discover-empty">Nenhum candidato ainda.<br>Explore o TikTok e salve os links aqui.</li>';
    $('discoverImport').disabled=selected.size<3||selected.size>5;$('discoverImport').textContent=`Usar no ranking (${selected.size}/5)`;
  }
  $('discoverFilter').onchange=render;
  $('discoverForm').onsubmit=e=>{e.preventDefault();try{const url=videoURL($('discoverUrl').value),name=$('discoverName').value.trim();if(!name)throw Error('Informe um nome para o vídeo.');if(candidates.some(c=>c.url===url))throw Error('Este link já está salvo.');if(candidates.length>=200)throw Error('Limite de 200 candidatos. Remova alguns para continuar.');if(save([...candidates,{url,name,collection:$('discoverCollection').value.trim()}])){$('discoverUrl').value='';$('discoverName').value='';$('discoverFilter').value='';render();status('Candidato salvo.');}}catch(error){status(error.message);}};
  $('discoverList').onchange=e=>{if(!e.target.matches('[data-select]'))return;const entry=candidates[Number(e.target.dataset.select)];if(e.target.checked){if(selected.size>=5){e.target.checked=false;status('Selecione no máximo 5 vídeos.');return;}selected.add(entry.url);}else selected.delete(entry.url);render();};
  $('discoverList').onclick=e=>{const remove=e.target.closest('[data-remove]'),up=e.target.closest('[data-up]');if(remove){const i=Number(remove.dataset.remove),url=candidates[i].url;if(save(candidates.filter((_,n)=>n!==i))){selected.delete(url);render();}}if(up){const i=Number(up.dataset.up);if(i>0){const next=[...candidates];[next[i-1],next[i]]=[next[i],next[i-1]];if(save(next))render();}}};
  $('discoverClear').onclick=()=>{selected.clear();render();};
  $('discoverImport').onclick=()=>{const entries=candidates.filter(e=>selected.has(e.url));if(entries.length<3||entries.length>5)return;const headline=$('discoverHeadline').value.trim()||`TOP ${entries.length} DANCE`;try{if(window.TopFive.importCandidates(entries,headline))status('Candidatos enviados ao editor.');}catch(error){status(error.message);}};
  // Mais vistos por tópico: a lista vem do servidor, que junta regiões e períodos da Central de Criação.
  const compact=n=>new Intl.NumberFormat('pt-BR',{notation:'compact',maximumFractionDigits:1}).format(n||0);
  // picked: vídeos escolhidos (até 5), na ordem do clique = posição no ranking. Vale entre tópicos e filtros.
  const trending={topic:'',videos:[],hashtags:[],name:'',ticket:0,hotTicket:0,picked:[]};
  const tagsOf=title=>[...String(title||'').matchAll(/#([^\s#@,.;:!?()[\]{}"'“”<>|/\\]+)/gu)].map(m=>m[1].toLowerCase());
  // Busca local: "#tag" compara com as hashtags da legenda (início da palavra); texto livre procura na legenda e no @.
  function visibleVideos(){
    const q=$('trendingSearch').value.trim().toLowerCase();
    if(!q)return trending.videos;
    if(q.startsWith('#')){const tag=q.slice(1);return tag?trending.videos.filter(v=>tagsOf(v.title).some(t=>t.startsWith(tag))):trending.videos;}
    return trending.videos.filter(v=>(v.title+' @'+v.author).toLowerCase().includes(q));
  }
  // Na busca, o nome do grupo é o termo buscado ("futebol", "#dancinha"), usado também no título do ranking.
  function poolName(){const q=$('trendingSearch').value.trim(),base=trending.topic==='search'?trending.query:trending.name;return q?(q.startsWith('#')?q:`“${q}”`)+(base?` · ${base}`:''):base;}
  function trendStatus(message,warn=''){$('trendingStatus').textContent=message;$('trendingWarn').textContent=warn;}
  // Erros de rede e API desatualizada viram mensagens acionáveis, em vez de "Failed to fetch".
  async function getJSON(url,fallback){
    let response;
    try{response=await fetch(url);}catch{throw Error('Sem conexão com o servidor do CutClips. Verifique se ele está aberto e recarregue a página.');}
    const body=await response.json().catch(()=>({}));
    if(response.status===404&&body.detail==='Not Found')throw Error('O servidor do CutClips em execução é de uma versão anterior. Feche e abra o CutClips de novo para usar a busca de vídeos.');
    if(!response.ok)throw Error(typeof body.detail==='string'?body.detail:fallback);
    return body;
  }
  async function loadTopics(){
    try{const body=await getJSON('/api/trending/topics','Não foi possível carregar os tópicos.');
      $('trendingTopics').innerHTML=body.topics.map(t=>`<button type="button" class="discover-topic" data-topic="${esc(t.id)}" aria-pressed="${t.id===trending.topic}">${esc(t.name)}</button>`).join('');
      $('trendingRegion').innerHTML='<option value="">Todas as regiões</option>'+(body.regions.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('')+'<option disabled>Brasil · use Dance ou a busca (YouTube Shorts)</option>');
      $('trendingTagRegion').innerHTML=body.hashtag_regions.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('');
      return true;
    }catch(error){trendStatus(error.message);return false;}
  }
  async function loadVideos(refresh=false){
    const searching=trending.topic==='search',ticket=++trending.ticket,params=new URLSearchParams(searching?{q:trending.query,sort:$('trendingSort').value,hide_ads:$('trendingHideAds').checked}:{topic:trending.topic,region:$('trendingRegion').value,sort:$('trendingSort').value,hide_ads:$('trendingHideAds').checked});
    $('trendingRegion').disabled=searching;trending.loading=true;
    if(refresh)params.set('refresh','true');
    document.querySelectorAll('[data-topic]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.topic===trending.topic)));
    document.querySelectorAll('[data-fill-top]').forEach(b=>b.disabled=true);
    trendStatus(searching?`Buscando “${trending.query}” no YouTube Shorts…`:'Carregando vídeos…',searching||trending.topic&&trending.topic!=='dance'?'':'Juntando os tópicos; na primeira vez leva alguns segundos.');$('trendingVideos').setAttribute('aria-busy','true');
    try{
      const body=await getJSON((searching?'/api/trending/search?':'/api/trending/videos?')+params,'Não foi possível consultar os vídeos agora.');
      if(ticket!==trending.ticket)return;
      trending.loading=false;trending.videos=body.videos;trending.hashtags=body.hashtags||[];trending.name=body.topic.name;trending.fetched=body.fetched_at;
      renderVideos();
    }catch(error){if(ticket===trending.ticket){trending.loading=false;document.querySelectorAll('[data-fill-top]').forEach(b=>b.disabled=true);trending.videos=[];trending.hashtags=[];trending.fetched=null;renderVideos();trendStatus(error.message);}}
    finally{if(ticket===trending.ticket){trending.loading=false;$('trendingVideos').removeAttribute('aria-busy');}}
  }
  // Hashtags dos vídeos listados: mostra as que se repetem; o resto fica atrás de "+N".
  function renderTags(){
    const q=$('trendingSearch').value.trim().toLowerCase(),tags=trending.hashtags,active=q.startsWith('#')?q.slice(1):'';
    const repeated=tags.filter(t=>t.videos>1),base=(repeated.length>=3?repeated:tags).slice(0,8);
    const shown=trending.allTags?tags:[...base,...tags.filter(t=>t.name.toLowerCase()===active&&!base.includes(t))];
    const hidden=tags.length-shown.length;
    $('trendingTags').innerHTML=tags.length?shown.map(t=>{const on=t.name.toLowerCase()===active;return `<button type="button" class="discover-tag" data-tag="${esc(t.name)}" aria-pressed="${on}">#${esc(t.name)} <small>${on?'×':t.videos}</small></button>`;}).join('')
      +(hidden>0?`<button type="button" class="discover-tag discover-more" data-more>+${hidden}</button>`:trending.allTags&&tags.length>base.length?'<button type="button" class="discover-tag discover-more" data-more>menos</button>':'')
      :'<small>Nenhuma hashtag nos vídeos deste tópico.</small>';
    const inVideos=new Set(tags.map(t=>t.name.toLowerCase()));
    document.querySelectorAll('#trendingHot [data-hot]').forEach(b=>{const name=b.dataset.hot.toLowerCase(),usable=inVideos.has(name);b.dataset.tag=usable?b.dataset.hot:'';b.setAttribute('aria-pressed',String(usable&&name===active));b.title=usable?'Filtrar os vídeos por esta hashtag':'Buscar esta hashtag no YouTube Shorts';});
  }
  function renderVideos(){
    const list=visibleVideos();renderTags();renderVideosStatus(list);
    $('trendingVideos').innerHTML=list.map((v,i)=>CARD(v,i)).join('');
  }
  function renderVideosStatus(list=visibleVideos()){
    if(trending.fetched&&!trending.loading){
      const filtered=list.length!==trending.videos.length,q=$('trendingSearch').value.trim();
      const count=trending.videos.length?`${list.length} ${list.length===1?'vídeo':'vídeos'} · ${poolName()}${trending.topic==='search'?' · YouTube Shorts':''}`:'Nenhum vídeo';
      const warn=!trending.videos.length?(trending.topic==='search'?'Nenhum Shorts com essa hashtag. Tente outra palavra.':'Nenhum vídeo com esses filtros. Tente outra região ou desmarque “Sem publis”.')
        :filtered&&!list.length?`Nenhum vídeo desta lista ${q.startsWith('#')?'usa '+q:'menciona “'+q+'”'}. Aperte Enter para buscar no YouTube Shorts.`
        :list.length<3?(trending.topic==='dance'&&!filtered?'A lista pública do TikTok tem poucos vídeos de dança agora. O ranking precisa de pelo menos 3.':'O ranking precisa de pelo menos 3 vídeos.'):'';
      trendStatus(count,warn);
    }
    renderFill(list);
  }
  const CARD=(v,i)=>`<li class="discover-video${pickedAt(v)?' is-picked':''}"><div class="discover-thumb" data-preview="${i}"><img src="${esc(v.cover)}" alt="" loading="lazy" referrerpolicy="no-referrer"><span class="discover-rank">${pickedAt(v)?`✓ ${pickedAt(v)}º`:i+1}</span><span class="discover-source is-${v.source}">${v.source==='youtube'?'Shorts':'TikTok'}</span><span class="discover-views" title="${v.views.toLocaleString('pt-BR')} visualizações · ${v.organic_views.toLocaleString('pt-BR')} orgânicas">${compact($('trendingSort').value==='organic'?v.organic_views:v.views)} views</span></div><div class="discover-video-body"><strong title="${esc(v.title)}">${esc(v.title||'Sem legenda')}</strong><small>${v.source==='youtube'?(v.author?esc(v.author)+' · ':'')+'YouTube Shorts':`@${esc(v.author)} · ${compact(v.organic_views)} orgânicas · ${esc(v.regions.join(', '))}`}</small><div class="discover-video-actions"><a href="${esc(v.url)}" target="_blank" rel="noopener noreferrer">${v.source==='youtube'?'YouTube':'TikTok'} ↗</a>${pickButton(v)}</div></div></li>`;
  const pickedAt=v=>trending.picked.findIndex(p=>p.id===v.id)+1;
  function pickButton(v){const n=pickedAt(v);return `<button type="button" class="btn btn-sm ${n?'btn-primary':'btn-outline'}" data-pick="${esc(v.id)}" aria-pressed="${!!n}">${n?`✓ ${n}º no ranking`:'Selecionar'}</button>`;}
  // Sem seleção: Top N com os mais vistos. Com seleção: os escolhidos entram primeiro e o resto é completado.
  function fillPool(list){const ids=new Set(trending.picked.map(v=>v.id));return [...trending.picked,...list.filter(v=>!ids.has(v.id))];}
  function renderFill(list=visibleVideos()){
    const n=trending.picked.length,pool=fillPool(list);
    $('trendingPicked').textContent=n?`${n} ${n===1?'selecionado':'selecionados'} · montar`:'Montar ranking';
    $('trendingClear').hidden=!n;
    document.querySelectorAll('[data-fill-top]').forEach(b=>{const k=Number(b.dataset.fillTop);b.disabled=k<n||pool.length<k;b.classList.toggle('is-suggested',n>=3&&k===n);b.title=n&&k<n?`Você selecionou ${n} vídeos`:'';});
    if(n&&trending.fetched)$('trendingWarn').textContent=n>=3?'Os selecionados entram na ordem em que você clicou.':`Os ${n} selecionados entram primeiro; as outras posições recebem os mais vistos da lista.`;
  }
  function togglePick(id){
    const at=trending.picked.findIndex(v=>v.id===id);
    if(at>=0)trending.picked.splice(at,1);
    else{
      if(trending.picked.length>=5){$('trendingWarn').textContent='Você já selecionou 5 vídeos, o máximo do ranking. Desmarque um para trocar.';return;}
      const v=trending.videos.find(x=>x.id===id);if(!v)return;trending.picked.push({...v,topicId:trending.topic,topicName:poolName()});
    }
    // Atualiza só os cards e a barra, sem recarregar as imagens da grade.
    document.querySelectorAll('#trendingVideos .discover-video').forEach(card=>{
      const button=card.querySelector('[data-pick]'),v=trending.videos.find(x=>x.id===button.dataset.pick),n=v&&pickedAt(v);
      card.classList.toggle('is-picked',!!n);button.outerHTML=pickButton(v);
      card.querySelector('.discover-rank').textContent=n?`✓ ${n}º`:Number(card.querySelector('[data-preview]').dataset.preview)+1;
    });
    renderVideosStatus();
  }
  // Prévia ao passar o mouse ou focar: o vídeo só é carregado quando pedido.
  function startPreview(thumb,clicked=false){
    if(!thumb||thumb.querySelector('video'))return;
    const v=visibleVideos()[Number(thumb.dataset.preview)];if(!v||v.source==='youtube'&&!clicked)return;
    const video=document.createElement('video');Object.assign(video,{src:v.preview,muted:true,loop:true,playsInline:true,autoplay:true});video.setAttribute('referrerpolicy','no-referrer');
    video.onerror=()=>video.remove();thumb.append(video);video.play().catch(()=>{});
  }
  function stopPreview(thumb){thumb?.querySelector('video')?.remove();}
  $('trendingVideos').addEventListener('mouseover',e=>startPreview(e.target.closest('[data-preview]')));
  $('trendingVideos').addEventListener('mouseout',e=>{const t=e.target.closest('[data-preview]');if(t&&!t.contains(e.relatedTarget))stopPreview(t);});
  $('trendingVideos').addEventListener('click',e=>{
    const thumb=e.target.closest('[data-preview]');if(thumb){thumb.querySelector('video')?stopPreview(thumb):startPreview(thumb,true);return;}
    const button=e.target.closest('[data-pick]');if(button)togglePick(button.dataset.pick);
  });
  $('trendingClear').onclick=()=>{trending.picked=[];renderVideos();};
  $('trendingTopics').onclick=e=>{const b=e.target.closest('[data-topic]');if(!b)return;trending.topic=b.dataset.topic;trending.allTags=false;$('trendingSearch').value='';$('trendingSearchGo').hidden=true;loadVideos();loadHot();};
  $('trendingRegion').onchange=()=>loadVideos();$('trendingSort').onchange=()=>loadVideos();$('trendingHideAds').onchange=()=>loadVideos();
  $('trendingRefresh').onclick=()=>{if(!trendingStarted){startTrending();return;}loadVideos(true);loadHot(true);};
  let searchTimer=0;
  $('trendingSearch').addEventListener('input',()=>{$('trendingSearchGo').hidden=!$('trendingSearch').value.trim();clearTimeout(searchTimer);searchTimer=setTimeout(renderVideos,200);});
  // Enter: busca no YouTube Shorts (a lista atual só é filtrada enquanto se digita).
  function runSearch(query){
    const q=String(query||'').trim();if(!q)return;
    clearTimeout(searchTimer);
    if(!trendingStarted){startTrending();return;}
    trending.topic='search';trending.query=q;trending.allTags=false;$('trendingSearch').value='';$('trendingSearchGo').hidden=true;
    loadVideos();loadHot();
  }
  $('trendingSearchForm').onsubmit=e=>{e.preventDefault();runSearch($('trendingSearch').value);};
  function pickTag(button){
    if(!button||!button.dataset.tag)return;
    const tag='#'+button.dataset.tag;
    $('trendingSearch').value=$('trendingSearch').value.trim().toLowerCase()===tag.toLowerCase()?'':tag;renderVideos();
  }
  $('trendingTags').onclick=e=>{if(e.target.closest('[data-more]')){trending.allTags=!trending.allTags;renderTags();return;}pickTag(e.target.closest('[data-tag]'));};
  $('trendingHot').onclick=e=>{const b=e.target.closest('[data-hot]');if(!b)return;if(b.dataset.tag)pickTag(b);else runSearch('#'+b.dataset.hot);};
  async function loadHot(refresh=false){
    const ticket=++trending.hotTicket,params=new URLSearchParams({region:$('trendingTagRegion').value||'BR',topic:trending.topic==='search'?'':trending.topic});if(refresh)params.set('refresh','true');
    $('trendingHotSummary').textContent='Hashtags em alta · carregando…';
    $('trendingHot').innerHTML='';
    try{
      const body=await getJSON('/api/trending/hashtags?'+params,'Hashtags em alta indisponíveis agora.');
      if(ticket!==trending.hotTicket)return;
      const where=body.topic.id?` em ${body.topic.name}`:'';
      $('trendingHotSummary').textContent=`Hashtags em alta no ${body.region.id==='BR'?'Brasil':body.region.name}${where} · ${body.hashtags.length}`;
      $('trendingHotSummary').title=body.sectors.length?`Setor da Central: ${body.sectors.join(', ')}`:'';
      const empty=body.topic.id==='dance'?'Nenhuma hashtag de dança em alta na lista pública. A Central não tem setor de dança; use o filtro por hashtag acima.':'Nenhuma hashtag em alta neste setor.';
      $('trendingHot').innerHTML=body.hashtags.length?body.hashtags.map(t=>`<button type="button" class="discover-tag" data-hot="${esc(t.name)}" aria-pressed="false">#${esc(t.name)} <small>${compact(t.posts)}</small></button>`).join(''):`<small>${esc(empty)}</small>`;
      renderTags();
    }catch(error){if(ticket===trending.hotTicket){$('trendingHotSummary').textContent='Hashtags em alta · indisponíveis';$('trendingHot').innerHTML=`<small>${esc(error.message)}</small>`;}}
  }
  $('trendingTagRegion').onchange=()=>loadHot();
  document.querySelectorAll('[data-fill-top]').forEach(b=>b.onclick=()=>{
    const count=Number(b.dataset.fillTop);
    const picked=trending.picked.length;
    // Título do ranking: o tópico de onde vieram os escolhidos; se vieram de vários, o título genérico.
    const origins=[...new Map(trending.picked.map(v=>[v.topicName,v.topicId||v.topicName])).entries()];
    const topic=!picked?{id:trending.topic||($('trendingSearch').value.trim()?'filter':''),name:poolName()}:origins.length===1?{id:origins[0][1]||'',name:origins[0][0]}:{id:'',name:'Seleção'};
    try{if(window.TopFive.importTopic({...topic,videos:fillPool(visibleVideos())},count)){trending.picked=[];renderVideos();}}
    catch(error){$('trendingWarn').textContent=error.message;}
    if(picked&&trending.picked.length)renderFill();
  });
  let trendingStarted=false;
  function startTrending(){if(trendingStarted||location.hash!=='#/discover')return;trendingStarted=true;loadTopics().then(ok=>{if(ok){loadVideos();loadHot();}else trendingStarted=false;});}
  window.addEventListener('hashchange',startTrending);startTrending();
  render();if(loadError)status(loadError);
})();
