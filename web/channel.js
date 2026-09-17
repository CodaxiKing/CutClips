(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = (value, digits=0) => value == null ? '—' : Number(value).toLocaleString('pt-BR', {maximumFractionDigits:digits});
  let connected=false, busy=false, loadId=0, setupInitialized=false;
  const metrics = [['views','Visualizações','pessoas assistindo'],['impressions','Impressões','alcance das miniaturas'],['ctr','CTR de impressões','interesse em assistir'],['averageViewPercentage','Média assistida','atenção ao conteúdo'],['hours','Horas assistidas','tempo com seu conteúdo'],['subscribers','Saldo de inscritos','crescimento da comunidade']];
  function cards(current={}, previous={}, reach={}, oldReach={}) {
    const values={...current, ...reach, hours:current.estimatedMinutesWatched==null?null:current.estimatedMinutesWatched/60, subscribers:current.subscribersGained==null?null:current.subscribersGained-current.subscribersLost};
    const before={...previous,...oldReach,hours:previous.estimatedMinutesWatched==null?null:previous.estimatedMinutesWatched/60,subscribers:previous.subscribersGained==null?null:previous.subscribersGained-previous.subscribersLost};
    el('ytKpis').innerHTML=metrics.map(([key,label,note],i)=>{
      const value=values[key], percent=['ctr','averageViewPercentage'].includes(key);
      let change='Aguardando conexão';
      if(connected) change=value==null?'Aguardando dados':note;
      if(value!=null && before[key]!=null && before[key]>0 && (!['ctr','impressions'].includes(key) || reach.days===reach.expected_days && oldReach.days===oldReach.expected_days)) {
        const delta=(value-before[key])/before[key]*100;
        change=`<span class="${delta<0?'yt-down':'yt-up'}">${delta<0?'↘':'↗'} ${fmt(Math.abs(delta),1)}%</span> vs. período anterior`;
      }
      if(['ctr','impressions'].includes(key) && value!=null && reach.days<reach.expected_days) change=`Cobertura parcial · ${reach.days}/${reach.expected_days} dias`;
      return `<article class="yt-kpi ${i===0?'yt-kpi-featured':''}"><span>${label}</span><strong>${fmt(value,percent||key==='hours'?1:0)}${value!=null&&percent?'%':''}</strong><small>${change}</small></article>`;
    }).join('');
  }
  async function api(path, method='GET', body) {
    const response=await fetch('/api/youtube/'+path,{method,...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const data=await response.json();
    if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Não foi possível concluir a operação.');
    return data;
  }
  function message(text, error=false) {el('ytMessage').textContent=text;el('ytMessage').className=text?(error?'yt-alert yt-alert-error':'yt-alert'):'';}
  async function status() {
    const data=await api('status'); connected=data.connected;
    el('ytCallback').textContent=data.callback;if(!setupInitialized){el('ytSetup').open=!data.configured;setupInitialized=true;}if(data.configured)el('ytSetup').open=false;
    el('ytConnect').textContent=connected?'Trocar canal ↗':'Conectar YouTube ↗';
    el('ytDisconnect').hidden=!connected;el('ytRefresh').disabled=!connected;
    el('ytBadge').textContent=connected?'● Canal conectado':data.configured?'Pronto para conectar':'Configuração necessária';
    el('ytBadge').classList.toggle('is-connected',connected);
    el('ytConnectTitle').textContent=connected?(data.channel?.name || 'Seu canal está conectado'):'Conecte seu canal uma vez.';
    el('ytConnectText').textContent=connected?'A conexão está ativa. Seus dados são carregados automaticamente ao abrir esta aba e atualizados a cada cinco minutos enquanto ela estiver visível.':'Visualizações, retenção e inscritos vêm direto do YouTube. Impressões e CTR são sincronizados assim que os relatórios de alcance estiverem disponíveis.';
    return data;
  }
  const trafficLabels={YT_SEARCH:'Pesquisa do YouTube',RELATED_VIDEO:'Vídeos sugeridos',SHORTS:'Feed de Shorts',SUBSCRIBER:'Página inicial e inscrições',EXT_URL:'Sites externos',YT_CHANNEL:'Páginas de canais',NO_LINK_OTHER:'Direto ou desconhecido',PLAYLIST:'Playlists',NOTIFICATION:'Notificações',END_SCREEN:'Telas finais',ADVERTISING:'Publicidade'};
  function chart(daily) {
    if(!daily.length){el('ytChart').innerHTML='<div class="yt-empty"><p>O YouTube ainda não retornou dados diários neste período.</p></div>';return;}
    const width=720,height=185,pad=12,max=Math.max(...daily.map(x=>x.views),1);
    const points=daily.map((row,i)=>[pad+i*(width-pad*2)/Math.max(daily.length-1,1),height-pad-row.views/max*(height-pad*2)]);
    const path=points.map((p,i)=>`${i?'L':'M'}${p.join(',')}`).join(' ');
    el('ytChart').innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Visualizações diárias; pico de ${fmt(max)} visualizações"><defs><linearGradient id="ytFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#ff665c" stop-opacity=".28"/><stop offset="100%" stop-color="#ff665c" stop-opacity="0"/></linearGradient></defs>${[.25,.5,.75].map(n=>`<path d="M0 ${height*n}H${width}" stroke="rgba(255,255,255,.06)" stroke-dasharray="4 5"/>`).join('')}<path d="${path} L${points.at(-1)[0]},${height} L${points[0][0]},${height} Z" fill="url(#ytFill)"/><path d="${path}" fill="none" stroke="#ff7569" stroke-width="2.5"/>${points.map((p,i)=>`<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="#ff7569"><title>${esc(daily[i].day)}: ${fmt(daily[i].views)} visualizações</title></circle>`).join('')}</svg><div class="yt-chart-labels"><span>${esc(daily[0].day)}</span><span>${fmt(max)} no pico</span><span>${esc(daily.at(-1).day)}</span></div><details class="yt-chart-data"><summary>Ver dados diários</summary><div class="yt-table-wrap"><table class="yt-table"><thead><tr><th>Data</th><th>Visualizações</th></tr></thead><tbody>${daily.map(x=>`<tr><td>${esc(x.day)}</td><td>${fmt(x.views)}</td></tr>`).join('')}</tbody></table></div></details>`;
  }
  function render(data) {
    cards(data.current,data.previous,data.reach,data.previous_reach);
    el('ytPeriod').textContent=`${data.start.split('-').reverse().join('/')} — ${data.end.split('-').reverse().join('/')} · Todos os formatos`;
    chart(data.daily);
    const total=data.traffic.reduce((s,x)=>s+x.views,0);
    el('ytTraffic').className='yt-traffic';
    el('ytTraffic').innerHTML=data.traffic.length?data.traffic.slice(0,7).map(x=>`<div><div class="yt-source-label"><span>${esc(trafficLabels[x.insightTrafficSourceType] || x.insightTrafficSourceType)}</span><strong>${fmt(x.views)}</strong></div><div class="yt-bar"><i style="width:${total?x.views/total*100:0}%"></i></div></div>`).join(''):'<p class="yt-muted">Sem dados de origem disponíveis.</p>';
    el('ytWarnings').innerHTML=data.warnings.map(x=>`<p class="yt-alert">${esc(x)}</p>`).join('');
    el('ytRecommendations').innerHTML=data.recommendations.map((x,i)=>`<div class="yt-action-item"><span>${String(i+1).padStart(2,'0')}</span><p>${esc(x)}</p></div>`).join('');
    el('ytVideos').className='yt-table-wrap';
    el('ytVideos').innerHTML=data.top.length?`<table class="yt-table"><thead><tr><th>Vídeo</th><th>Visualizações</th><th>Média assistida</th><th>Horas</th></tr></thead><tbody>${data.top.map((v,i)=>`<tr><td><span class="yt-rank">${i+1}</span><a href="https://www.youtube.com/watch?v=${encodeURIComponent(v.video)}" target="_blank" rel="noopener">${esc(v.title)}</a></td><td>${fmt(v.views)}</td><td>${fmt(v.averageViewPercentage,1)}%</td><td>${fmt(v.estimatedMinutesWatched/60,1)}</td></tr>`).join('')}</tbody></table>`:'<p class="yt-muted">Nenhum vídeo retornado para este período.</p>';
    el('ytNote').textContent=data.note;
    message(`Sincronizado às ${new Date(data.synced_at*1000).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}. Fonte: YouTube Analytics e Reporting.`);
  }
  async function sync() {
    if(!connected||busy)return;
    busy=true;const generation=++loadId;el('ytRefresh').disabled=true;el('ytDays').disabled=true;el('ytRefresh').textContent='Sincronizando…';message('Buscando os dados oficiais do seu canal…');
    try{const data=await api('dashboard?days='+el('ytDays').value);if(generation===loadId)render(data);}catch(err){message(err.message,true);}finally{busy=false;el('ytRefresh').disabled=!connected;el('ytDays').disabled=false;el('ytRefresh').textContent='↻ Atualizar';}
  }
  el('ytConnect').onclick=async()=>{
    const button=el('ytConnect');button.disabled=true;
    try{const s=await status();if(!s.configured){el('ytSetup').open=true;el('ytSetup').scrollIntoView({behavior:'smooth',block:'center'});message('Importe o JSON do cliente OAuth do Google na configuração abaixo. Depois, conecte sua conta.');return;}await api('open-browser','POST');message('Continue a conexão na janela do navegador padrão. Esta aba reconhecerá a conexão automaticamente.');}catch(err){message(err.message,true);}finally{button.disabled=false;}
  };
  el('ytClientFile').onchange=async e=>{
    const file=e.target.files[0];if(!file)return;
    try{if(file.size>20000)throw new Error('O arquivo de configuração deve ter até 20 KB.');await api('configure','POST',{content:await file.text()});await status();resetDashboard();message('Configuração salva com proteção do Windows. Agora clique em Conectar YouTube.');}catch(err){message(err.message,true);}finally{e.target.value='';}
  };
  function resetDashboard(){loadId++;cards();el('ytPeriod').textContent='Conecte o canal para carregar métricas reais';for(const id of ['ytChart','ytTraffic','ytVideos','ytRecommendations'])el(id).innerHTML='<p class="yt-muted">Conecte seu canal para carregar os dados.</p>';el('ytWarnings').textContent='';}
  el('ytDisconnect').onclick=async()=>{el('ytDisconnect').disabled=true;try{await api('disconnect','POST');await status();resetDashboard();message('Acesso revogado e dados locais da conexão removidos.');}catch(err){message(err.message,true);}finally{el('ytDisconnect').disabled=false;}};
  el('ytRefresh').onclick=sync;el('ytDays').onchange=sync;
  el('channelForm').onsubmit=async e=>{
    e.preventDefault();const button=e.target.querySelector('button'),result=el('channelResult');button.disabled=true;result.textContent='Consultando o canal público…';
    try{const response=await fetch('/api/channel/inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Confira o link do canal.');result.innerHTML=`<h3>${esc(data.name || 'Canal')}</h3><p class="yt-muted">${esc(data.source)}</p><p>${data.videos.length} vídeos · ${fmt(data.sample_views)} visualizações acumuladas na amostra.</p><p class="yt-muted">${esc(data.delivery)}</p><ul>${data.recommendations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><div class="yt-table-wrap"><table class="yt-table"><thead><tr><th>Vídeo</th><th>Visualizações</th></tr></thead><tbody>${data.videos.map(v=>`<tr><td><a href="https://www.youtube.com/watch?v=${encodeURIComponent(v.id)}" target="_blank" rel="noopener">${esc(v.title)}</a></td><td>${fmt(v.views)}</td></tr>`).join('')}</tbody></table></div>`;}catch(err){result.textContent=err.message;}finally{button.disabled=false;}
  };
  cards();
  async function enter(){if(location.hash!=='#/channel')return;try{await status();await sync();}catch(err){message(err.message,true);}}
  window.addEventListener('hashchange',enter);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)enter();});
  setInterval(()=>{if(!document.hidden && location.hash==='#/channel')enter();},300000);
  setInterval(async()=>{if(!connected && !document.hidden && location.hash==='#/channel'){try{await status();if(connected)await sync();}catch{}}},10000);
  const authResult=new URLSearchParams(location.search).get('youtube');if(authResult){history.replaceState(null,'',location.pathname+'#/channel');if(authResult==='cancelled')message('Conexão cancelada. Você pode tentar novamente quando quiser.');}
  if(new URLSearchParams(location.search).get('connect')==='youtube'){history.replaceState(null,'',location.pathname+'#/channel');status().then(async data=>{if(data.configured){const auth=await api('connect','POST');window.location.assign(auth.url);}}).catch(err=>message(err.message,true));}else enter();
})();
