(() => {
  'use strict';
  const el=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  // Trecho automático: ao colar o link, a duração do vídeo define o fim (até MAX_CLIP)
  // e os limites dos campos, para nunca pedir além do que o vídeo tem. Serve ao
  // formulário e à correção de uma posição na tela de erro.
  const MAX_CLIP=15;
  const round2=v=>Math.round(v*100)/100;
  const clock=s=>`${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`;
  const looksLikeTikTok=url=>/^https:\/\/(?:(?:www\.|m\.|vm\.|vt\.)?tiktok\.com\/\S+|(?:www\.|m\.)?youtube\.com\/shorts\/[\w-]{11})/i.test(url);
  function trimmer(f,onUpdate){
    const t={length:null,probed:undefined,loading:false,error:'',manualEnd:false,timer:0};
    t.limits=()=>{
      const total=t.length;
      let s=Math.max(0,Number(f.start.value)||0);
      if(total)s=Math.min(s,Math.max(0,round2(total-.5)));
      if(Number(f.start.value)!==s||f.start.value==='')f.start.value=s;
      f.start.max=total?Math.max(0,round2(total-.5)):600;
      const ceiling=round2(total?Math.min(s+MAX_CLIP,total):s+MAX_CLIP);
      f.end.min=round2(Math.min(s+.5,ceiling));f.end.max=ceiling;
      if(!t.manualEnd)f.end.value=total?ceiling:'';
      else if(f.end.value!==''){const e=Number(f.end.value);if(e>ceiling)f.end.value=ceiling;else if(e<s+.5)f.end.value=round2(Math.min(s+.5,ceiling));}
    };
    t.probe=async()=>{
      const url=f.url.value.trim();
      if(t.probed===url)return;
      t.probed=url;t.length=null;t.error='';t.loading=looksLikeTikTok(url);
      t.limits();onUpdate();
      if(!t.loading)return;
      try{
        const response=await fetch('/api/top5/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})});
        const body=await response.json().catch(()=>({}));
        if(t.probed!==url)return;
        if(!response.ok)throw new Error(typeof body.detail==='string'?body.detail:'Não foi possível ler a duração do vídeo');
        t.length=body.duration;
      }catch(error){if(t.probed===url)t.error=error.message;}
      if(t.probed!==url)return;
      t.loading=false;t.limits();onUpdate();
    };
    // Duração já conhecida (vídeo baixado no projeto): evita consultar o TikTok de novo.
    t.known=(url,length)=>{t.probed=url;t.length=length;t.loading=false;t.error='';t.limits();onUpdate();};
    t.newSource=()=>{clearTimeout(t.timer);if(f.url.value.trim()===t.probed)return;f.start.value=0;t.manualEnd=false;t.probe();};
    t.check=()=>{
      const total=t.length,s=Number(f.start.value)||0,e=f.end.value===''?null:Number(f.end.value);
      const startMessage=total&&s>total-.5?`O início vai até ${round2(Math.max(0,total-.5))}s: o vídeo tem ${round2(total)}s.`:'';
      const endMessage=e===null?'':e-s<.5?'O fim deve ser pelo menos 0,5 segundo depois do início.'
        :e-s>MAX_CLIP+.005?`Cada trecho pode ter no máximo ${MAX_CLIP} segundos.`
        :total&&e>total+.005?`O vídeo tem só ${round2(total)}s.`:'';
      f.start.setCustomValidity(startMessage);f.end.setCustomValidity(endMessage);
      if(startMessage||endMessage)return startMessage||endMessage;
      if(t.loading)return 'Lendo a duração do vídeo…';
      const length=total?`Vídeo de ${clock(total)} · `:'';
      if(e===null)return t.error?`Não deu para ler a duração agora; o trecho terá até ${MAX_CLIP}s a partir de ${s}s.`
        :`${length}Cole o link: o trecho de até ${MAX_CLIP}s é preenchido sozinho.`;
      return `${length}De ${s}s a ${e}s · ${round2(e-s)}s no ranking`;
    };
    // Eventos dos três campos: link colado (com pausa), fim editado à mão, limites ao sair do campo.
    f.url.addEventListener('input',()=>{clearTimeout(t.timer);t.timer=setTimeout(()=>{if(looksLikeTikTok(f.url.value.trim()))t.newSource();},700);});
    f.url.addEventListener('change',t.newSource);
    f.start.addEventListener('change',t.limits);
    f.end.addEventListener('change',()=>{t.manualEnd=f.end.value!=='';t.limits();});
    return t;
  }
  el('t5Entries').innerHTML=Array.from({length:5},(_,i)=>`<fieldset class="t5-entry"><legend>Posição ${i+1}</legend><div class="t5-entry-side"><span class="t5-number" aria-hidden="true">${i+1}</span><div class="t5-move"><button type="button" data-t5-up="${i}" aria-label="Trocar a posição ${i+1} com a de cima" title="Subir">↑</button><button type="button" data-t5-down="${i}" aria-label="Trocar a posição ${i+1} com a de baixo" title="Descer">↓</button></div></div><div class="t5-entry-fields"><label for="t5Url${i}">Link do TikTok ou YouTube Shorts<input id="t5Url${i}" type="url" required maxlength="600" placeholder="https://www.tiktok.com/@perfil/video/… ou youtube.com/shorts/…"></label><label for="t5Name${i}">Nome ao lado do número<input id="t5Name${i}" required maxlength="32" placeholder="Ex.: O gol impossível"></label><label for="t5Price${i}">Preço · card no ranking (opcional)<input id="t5Price${i}" maxlength="16" placeholder="Ex.: R$ 47,90"></label><div class="t5-trim-heading">✂ Trecho automático · até ${MAX_CLIP}s, ajuste se quiser</div><div class="t5-trim"><label>Início (segundos)<input id="t5Start${i}" type="number" min="0" max="600" step="0.01" value="0"></label><label>Fim (segundos)<input id="t5End${i}" type="number" min="0.5" max="${MAX_CLIP}" step="0.01" placeholder="Automático"></label></div><p class="t5-trim-summary" id="t5TrimSummary${i}"></p><div class="t5-source" id="t5Source${i}" hidden></div></div></fieldset>`).join('');
  const trims=Array.from({length:5},(_,i)=>trimmer({url:el('t5Url'+i),start:el('t5Start'+i),end:el('t5End'+i)},()=>preview()));
  // Vídeos escolhidos em Descobrir trazem um MP4 tocável: a prévia mostra o trecho real e
  // cada posição pode ser trocada pelo próximo vídeo mais visto do mesmo tópico.
  let media=Array(5).fill(null),pool=null;
  const compact=n=>new Intl.NumberFormat('pt-BR',{notation:'compact',maximumFractionDigits:1}).format(n||0);
  function rankName(v){
    const text=String(v.title||'').replace(/https?:\/\/\S+/g,'').replace(/[#@][\p{L}\p{M}\p{N}_.]+/gu,'').replace(/[^\p{L}\p{M}\p{N}\p{P}\p{Zs}]/gu,'').replace(/\s+/g,' ').trim();
    if(text.replace(/\p{P}/gu,'').trim().length<3)return (v.source==='youtube'?(v.author||'YouTube Shorts'):'@'+v.author).slice(0,32);
    if(text.length<=32)return text;
    const cut=text.slice(0,31),space=cut.lastIndexOf(' ');
    return (space>15?cut.slice(0,space):cut).replace(/[\s,.;:!?-]+$/,'')+'…';
  }
  function showSource(i){
    const box=el('t5Source'+i),m=media[i];
    box.hidden=!m&&!pool;
    box.innerHTML=box.hidden?'':`${m?`<img src="${esc(m.cover)}" alt="" referrerpolicy="no-referrer"><div><strong>${compact(m.views)} visualizações</strong><small>${m.source==='youtube'?`${esc(m.author||'YouTube Shorts')}`:`@${esc(m.author)}`} · ${esc(pool?.name||'Descobrir')}</small></div>`:'<div><small>Link colado à mão · a prévia mostra só o texto</small></div>'}${pool?`<button type="button" class="btn btn-outline btn-sm" data-t5-swap="${i}" title="Substituir pelo próximo vídeo mais visto de ${esc(pool.name)}">↻ Trocar vídeo</button>`:''}`;
  }
  function showPool(){
    el('t5Pool').innerHTML=pool?`Vídeos de <strong>${esc(pool.name)}</strong> · ${pool.videos.length} disponíveis. Use <b>↻ Trocar vídeo</b> para substituir uma posição. <a href="#/discover">Escolher outro tópico</a>`
      :'<a href="#/discover">Sem vídeos ainda? Preencha com os mais vistos de um tópico →</a>';
    for(let i=0;i<5;i++)showSource(i);
  }
  // A duração vem do próprio MP4 da prévia, sem consultar o TikTok pelo servidor.
  function measure(i){
    const m=media[i],url=el('t5Url'+i).value.trim(),trim=trims[i];
    if(!m)return trim.probe();
    trim.probed=url;trim.length=null;trim.loading=true;trim.error='';trim.limits();preview();
    const probe=document.createElement('video');probe.preload='metadata';probe.muted=true;
    probe.onloadedmetadata=()=>{if(media[i]===m&&Number.isFinite(probe.duration)&&probe.duration>0)trim.known(url,probe.duration);probe.onerror=null;probe.removeAttribute('src');probe.load();};
    probe.onerror=()=>{if(media[i]===m){trim.probed=undefined;trim.probe();}};
    probe.src=m.preview;
  }
  function assign(i,v){
    el('t5Url'+i).value=v.url;el('t5Name'+i).value=rankName(v);el('t5Start'+i).value=0;el('t5End'+i).value='';
    trims[i].manualEnd=false;media[i]=v;showSource(i);measure(i);
    el('t5Url'+i).dispatchEvent(new Event('change'));
  }
  function swap(i){
    if(!pool)return;
    const count=Number(el('t5Count').value),current=el('t5Url'+i).value.trim();
    const inUse=new Set(Array.from({length:count},(_,n)=>el('t5Url'+n).value.trim()));
    const pick=()=>pool.videos.find(v=>!inUse.has(v.url)&&!pool.skipped.has(v.url));
    pool.skipped.add(current);
    let next=pick();
    // Todos os outros já passaram por aqui: recomeça a fila, sem repetir o que está no ranking.
    if(!next){pool.skipped=new Set([current]);next=pick();}
    if(!next){window.toast?.(`Não há outro vídeo de ${pool.name} fora do ranking.`);return;}
    assign(i,next);
    const order=Array.from({length:count},(_,n)=>n+1);if(el('t5Order').value==='countdown')order.reverse();
    el('t5PreviewStep').value=order.indexOf(i+1);player.key='';player.finished=false;preview(true);
  }
  for(let i=0;i<5;i++)el('t5Url'+i).addEventListener('input',()=>{if(media[i]&&el('t5Url'+i).value.trim()!==media[i].url){media[i]=null;showSource(i);}});
  // Reordenar: troca duas posições inteiras (link, nome, trecho, áudio e autorização).
  function move(i,step){
    const count=Number(el('t5Count').value),j=i+step;
    if(j<0||j>=count)return;
    const spec=data(),lengths=trims.map(t=>t.length),sources=[...media];
    [spec.entries[i],spec.entries[j]]=[spec.entries[j],spec.entries[i]];
    [lengths[i],lengths[j]]=[lengths[j],lengths[i]];
    [sources[i],sources[j]]=[sources[j],sources[i]];
    fill(spec,sources);
    // A duração de cada vídeo já era conhecida: evita consultar tudo de novo.
    lengths.forEach((length,n)=>{if(length)trims[n].known(el('t5Url'+n).value.trim(),length);});
    const order=Array.from({length:count},(_,n)=>n+1);if(el('t5Order').value==='countdown')order.reverse();
    el('t5PreviewStep').value=order.indexOf(j+1);player.key='';player.finished=false;preview(true);
    el('t5Url'+j).closest('.t5-entry').querySelector(`[data-t5-${step<0?'up':'down'}]`).focus();
    window.toast?.(`Posição ${i+1} ↔ posição ${j+1}`);
  }
  el('t5Entries').addEventListener('click',event=>{
    const swapButton=event.target.closest('[data-t5-swap]');if(swapButton)return swap(Number(swapButton.dataset.t5Swap));
    const up=event.target.closest('[data-t5-up]'),down=event.target.closest('[data-t5-down]');
    if(up)move(Number(up.dataset.t5Up),-1);else if(down)move(Number(down.dataset.t5Down),1);
  });
  const player={video:el('t5PreviewVideo'),bg:el('t5PreviewBg'),playing:false,finished:false,key:'',end:0,timer:0,frame:0,token:0};
  function drawBackground(){
    const {video,bg}=player,vw=video.videoWidth,vh=video.videoHeight;
    if(video.readyState<2||!vw||!vh)return;
    const scale=Math.max(bg.width/vw,bg.height/vh);
    bg.getContext('2d').drawImage(video,(bg.width-vw*scale)/2,(bg.height-vh*scale)/2,vw*scale,vh*scale);
  }
  function drawLoop(){cancelAnimationFrame(player.frame);drawBackground();if(!player.video.paused)player.frame=requestAnimationFrame(drawLoop);}
  function stopPlayer(finished=false){
    player.playing=false;player.finished=finished;clearTimeout(player.timer);player.video.pause();
    el('t5Play').textContent=finished?'▶ Tocar de novo':'▶ Tocar prévia';
  }
  function advance(){
    const count=Number(el('t5Count').value),step=Number(el('t5PreviewStep').value)||0;
    if(step+1>=count)return stopPlayer(true);
    el('t5PreviewStep').value=step+1;preview(true);
  }
  // Mantém o vídeo da prévia no trecho do passo simulado; só recarrega quando o vídeo ou o início mudam.
  function syncVideo(spec,rank){
    const i=rank-1,m=media[i],entry=spec.entries[i]||{},start=entry.start||0,phone=document.querySelector('.t5-phone');
    phone.dataset.video=String(!!m);phone.dataset.layout=spec.layout;
    player.end=start+(entry.duration??MAX_CLIP);
    const key=m?`${m.preview}|${start}`:`none|${i}`;
    if(key===player.key)return;
    player.key=key;clearTimeout(player.timer);const token=++player.token,{video}=player;
    if(!m){
      video.pause();video.removeAttribute('src');video.removeAttribute('poster');video.load();
      if(player.playing)player.timer=setTimeout(advance,Math.min(entry.duration??3,4)*1000);
      return;
    }
    if(video.getAttribute('src')!==m.preview){video.poster=m.cover;video.src=m.preview;}
    const seek=()=>{
      if(token!==player.token)return;
      video.currentTime=Math.min(start,Math.max(0,(video.duration||start)-.1));
      if(player.playing)video.play().catch(()=>stopPlayer());
    };
    if(video.readyState>=1)seek();else video.addEventListener('loadedmetadata',seek,{once:true});
  }
  // Barra do trecho: mostra e edita início e fim do vídeo do passo simulado.
  const bar={track:el('t5TrimTrack'),range:el('t5TrimRange'),head:el('t5TrimHead'),start:el('t5TrimStart'),end:el('t5TrimEnd'),index:0,length:0};
  const sec=v=>`${(Math.round(v*10)/10).toFixed(1).replace('.',',')}s`;
  const pct=(v,total)=>Math.max(0,Math.min(100,total?v/total*100:0));
  function renderTrimBar(spec,rank){
    const i=rank-1,length=trims[i]?.length||0,entry=spec.entries[i]||{};
    bar.index=i;bar.length=length;
    el('t5TrimBar').hidden=!length;
    if(!length)return;
    const from=entry.start||0,to=Math.min(from+(entry.duration??MAX_CLIP),length);
    bar.range.style.left=pct(from,length)+'%';bar.range.style.width=Math.max(0,pct(to,length)-pct(from,length))+'%';
    bar.start.style.left=pct(from,length)+'%';bar.end.style.left=pct(to,length)+'%';
    [[bar.start,from],[bar.end,to]].forEach(([node,value])=>{node.setAttribute('aria-valuemax',round2(length));node.setAttribute('aria-valuenow',round2(value));node.setAttribute('aria-valuetext',sec(value));});
    el('t5TrimInfo').textContent=`Posição ${rank} · trecho ${sec(from)} → ${sec(to)} de ${clock(length)} · ${round2(to-from)}s no ranking`;
  }
  function applyTrim(handle,value){
    const i=bar.index,length=bar.length,trim=trims[i];
    if(!length)return;
    let from=Number(el('t5Start'+i).value)||0;
    let to=el('t5End'+i).value===''?Math.min(from+MAX_CLIP,length):Number(el('t5End'+i).value);
    if(handle==='start'){
      from=Math.max(0,Math.min(value,round2(length-.5)));
      to=Math.min(Math.max(to,from+.5),Math.min(from+MAX_CLIP,length));
    }else to=Math.max(from+.5,Math.min(value,Math.min(from+MAX_CLIP,length)));
    el('t5Start'+i).value=round2(from);el('t5End'+i).value=round2(to);
    trim.manualEnd=true;trim.limits();
    preview();
    // Mostra o quadro que está sendo ajustado, sem recarregar o vídeo.
    const video=player.video;
    if(video.getAttribute('src')&&video.readyState>=1)video.currentTime=handle==='start'?from:Math.max(from,to-.25);
  }
  function fromPointer(event){
    const box=bar.track.getBoundingClientRect();
    return round2(Math.max(0,Math.min(1,(event.clientX-box.left)/box.width))*bar.length);
  }
  [['start',bar.start],['end',bar.end]].forEach(([handle,node])=>{
    node.addEventListener('pointerdown',event=>{
      if(!bar.length)return;
      event.preventDefault();stopPlayer();node.setPointerCapture(event.pointerId);node.dataset.dragging='true';
    });
    node.addEventListener('pointermove',event=>{if(node.dataset.dragging==='true')applyTrim(handle,fromPointer(event));});
    const stop=event=>{if(node.dataset.dragging==='true'){node.dataset.dragging='false';node.releasePointerCapture?.(event.pointerId);}};
    node.addEventListener('pointerup',stop);node.addEventListener('pointercancel',stop);
    node.addEventListener('keydown',event=>{
      const step=event.shiftKey?1:.1,now=Number(node.getAttribute('aria-valuenow'))||0;
      const moves={ArrowLeft:now-step,ArrowRight:now+step,ArrowDown:now-step,ArrowUp:now+step,Home:0,End:bar.length};
      if(!(event.key in moves))return;
      event.preventDefault();applyTrim(handle,round2(moves[event.key]));
    });
  });
  // Clicar na barra move o início para ali.
  bar.track.addEventListener('pointerdown',event=>{if(event.target===bar.track||event.target===bar.range)applyTrim('start',fromPointer(event));});
  player.video.addEventListener('timeupdate',()=>{bar.head.style.left=pct(player.video.currentTime,bar.length)+'%';});
  player.video.addEventListener('loadedmetadata',()=>preview());
  player.video.addEventListener('seeked',drawBackground);player.video.addEventListener('loadeddata',drawBackground);player.video.addEventListener('play',drawLoop);
  player.video.addEventListener('timeupdate',()=>{if(player.playing&&player.video.currentTime>=player.end-.04)advance();});
  player.video.addEventListener('ended',()=>{if(player.playing)advance();});
  player.video.addEventListener('error',()=>{if(player.video.getAttribute('src')&&player.playing)player.timer=setTimeout(advance,1500);});
  el('t5Play').onclick=()=>{
    if(player.playing)return stopPlayer();
    const {video}=player,resume=video.getAttribute('src')&&video.currentTime<player.end-.1&&!player.finished&&document.querySelector('.t5-phone').dataset.video==='true';
    player.playing=true;el('t5Play').textContent='❚❚ Pausar';
    if(player.finished)el('t5PreviewStep').value=0;
    player.finished=false;
    if(resume){video.play().catch(()=>stopPlayer());return;}
    player.key='';preview(true);
  };
  el('t5Sound').onclick=()=>{const on=player.video.muted;player.video.muted=!on;el('t5Sound').setAttribute('aria-pressed',String(on));el('t5Sound').textContent=on?'🔊 Som':'🔇 Mudo';};
  window.addEventListener('hashchange',()=>{if(location.hash!=='#/top5'&&player.playing)stopPlayer();});
  function data(){return {headline:el('t5Headline').value.trim(),order:el('t5Order').value,layout:el('t5Layout').value,card_scale:Number(el('t5CardScale').value),card_position:Number(el('t5CardPosition').value),card_radius:Number(el('t5CardRadius').value),normalize_audio:el('t5Normalize').checked,title_font:el('t5TitleFont').value,rank_font:el('t5RankFont').value,text_effect:el('t5Effect').value,accent_color:el('t5Accent').value,title_color:el('t5TitleColor').value,rank_color:el('t5RankColor').value,outline_color:el('t5OutlineColor').value,outline_width:Number(el('t5OutlineWidth').value),shadow_color:el('t5ShadowColor').value,shadow_depth:Number(el('t5ShadowDepth').value),animation_style:el('t5AnimationStyle').value,title_size:Number(el('t5TitleSize').value),rank_size:Number(el('t5RankSize').value),rank_position:Number(el('t5RankPosition').value),animate_reveal:el('t5Animate').checked,animate_intro:el('t5Intro').checked,watermark:el('t5Watermark').value.trim(),watermark_opacity:Number(el('t5WatermarkOpacity').value),watermark_font:el('t5WatermarkFont').value,entries:Array.from({length:Number(el('t5Count').value)},(_,i)=>({...window.TopFiveTools?.entry(i),url:el('t5Url'+i).value.trim(),name:el('t5Name'+i).value.trim(),price:el('t5Price'+i).value.trim(),start:Number(el('t5Start'+i).value||0),duration:el('t5End'+i).value===''?null:round2(Number(el('t5End'+i).value)-Number(el('t5Start'+i).value||0))}))};}
  function preview(replay=false){
    const spec=data(),count=spec.entries.length;
    el('t5Watermark').setCustomValidity(!spec.watermark||/^@?[\p{L}\p{N}_.-]{1,31}$/u.test(spec.watermark)?'':'Use até 31 letras, números, pontos, hífens ou sublinhados, sem espaços.');
    el('t5Entries').querySelectorAll('.t5-entry').forEach((row,i)=>{row.hidden=i>=count;row.querySelectorAll('input').forEach(input=>input.disabled=i>=count);
      row.querySelector('[data-t5-up]').disabled=i===0;row.querySelector('[data-t5-down]').disabled=i>=count-1;});
    const order=Array.from({length:count},(_,i)=>i+1);if(spec.order==='countdown')order.reverse();
    const step=Math.min(Number(el('t5PreviewStep').value)||0,count-1),rank=order[step],seen=order.slice(0,step+1);
    el('t5PreviewStep').innerHTML=order.map((r,i)=>`<option value="${i}">Trecho ${i+1} · posição ${r}</option>`).join('');el('t5PreviewStep').value=step;
    el('t5Order').options[0].textContent=Array.from({length:count},(_,i)=>i+1).join(' → ');el('t5Order').options[1].textContent=Array.from({length:count},(_,i)=>count-i).join(' → ');
    el('t5Create').textContent=`Montar meu Top ${count} →`;
    const phone=document.querySelector('.t5-phone');phone.dataset.effect=spec.text_effect;phone.dataset.motion=spec.animation_style;
    phone.style.setProperty('--t5-rank-color',spec.rank_color);phone.style.setProperty('--t5-outline-color',spec.outline_color);phone.style.setProperty('--t5-outline-width',`${spec.outline_width/10.8}cqw`);phone.style.setProperty('--t5-shadow-color',spec.shadow_color);phone.style.setProperty('--t5-shadow-depth',`${spec.shadow_depth/10.8}cqw`);
    el('t5OutlineValue').textContent=spec.outline_width;el('t5ShadowValue').textContent=spec.shadow_depth;
    // Cartão: vídeo menor na frente, com o fundo desfocado atrás.
    el('t5CardControls').hidden=spec.layout!=='card';
    el('t5CardScaleValue').textContent=spec.card_scale;el('t5CardPositionValue').textContent=spec.card_position;el('t5CardRadiusValue').textContent=spec.card_radius;
    phone.style.setProperty('--t5-card-width',`${spec.card_scale}%`);phone.style.setProperty('--t5-card-top',`${spec.card_position}%`);phone.style.setProperty('--t5-card-radius',`${spec.card_radius/10.8}cqw`);
    // O cartão nunca passa das bordas: mesmo limite usado na montagem do vídeo.
    const cardW=player.video.videoWidth,cardH=player.video.videoHeight;
    if(spec.layout==='card'&&cardW&&cardH){
      const boxHeight=Math.min(1,spec.card_scale/100*(cardH/cardW)*(9/16));
      const boxTop=Math.min(Math.max(0,spec.card_position/100-boxHeight/2),1-boxHeight);
      phone.style.setProperty('--t5-card-top',`${(boxTop+boxHeight/2)*100}%`);
    }phone.dataset.animate=String(spec.animate_reveal);phone.dataset.intro=String(spec.animate_intro);phone.dataset.opening=String(step===0);
    spec.entries.forEach((item,i)=>{el('t5TrimSummary'+i).textContent=trims[i].check();});
    el('t5PreviewWatermark').textContent=spec.watermark?'@'+spec.watermark.replace(/^@+/,''):'';el('t5PreviewWatermark').style.opacity=spec.watermark_opacity/100;phone.style.setProperty('--t5-watermark-font',spec.watermark_font);
    if(replay===true){phone.classList.remove('t5-playing');void phone.offsetWidth;phone.classList.add('t5-playing');}
    phone.style.setProperty('--t5-accent',spec.accent_color);phone.style.setProperty('--t5-title-color',spec.title_color);
    phone.style.setProperty('--t5-title-font',spec.title_font);phone.style.setProperty('--t5-rank-font',spec.rank_font);
    phone.style.setProperty('--t5-title-size',`${spec.title_size/10.8}cqw`);phone.style.setProperty('--t5-rank-size',`${spec.rank_size/10.8}cqw`);
    phone.style.setProperty('--t5-number-size',`${(spec.rank_size+16)/10.8}cqw`);
    phone.style.setProperty('--t5-rank-top',`${(1920*spec.rank_position/100-((count-1)*150+spec.rank_size)/2)/19.2}%`);
    el('t5PreviewTitle').textContent=spec.headline||'Sua frase aparece aqui';
    el('t5PreviewClip').textContent=`Vídeo da posição ${rank}`;
    el('t5PreviewRanks').innerHTML=spec.entries.map((item,i)=>`<li class="${i+1===rank?'active':seen.includes(i+1)?'revealed':'future'}"><b style="--t5-delay:${i*45}ms">${i+1}.</b><span>${seen.includes(i+1)?esc(item.name||`Nome do vídeo ${i+1}`)+(item.price?` <em class="t5-price">${esc(item.price)}</em>`:''):''}</span></li>`).join('');
    syncVideo(spec,rank);renderTrimBar(spec,rank);
  }
  function fill(spec,sources=[]){
    window.TopFiveTools?.fill(spec.entries||[]);
    media=Array.from({length:5},(_,i)=>sources[i]||null);player.key='';
    el('t5Preset').value='custom';
    el('t5Count').value=Math.max(3,Math.min(5,spec.entries?.length||5));
    const controls={t5RankColor:spec.rank_color||'#ffffff',t5OutlineColor:spec.outline_color||'#101010',t5OutlineWidth:spec.outline_width??5,t5ShadowColor:spec.shadow_color||'#000000',t5ShadowDepth:spec.shadow_depth??2,t5AnimationStyle:spec.animation_style||'slide',t5TitleFont:spec.title_font||'Impact',t5RankFont:spec.rank_font||'Arial',t5Effect:spec.text_effect||'outline',t5Accent:spec.accent_color||'#ffdd45',t5TitleColor:spec.title_color||'#ffffff',t5TitleSize:spec.title_size||72,t5RankSize:spec.rank_size||54,t5RankPosition:spec.rank_position||50,t5Watermark:spec.watermark||'',t5WatermarkOpacity:spec.watermark_opacity??40,t5WatermarkFont:spec.watermark_font||'Arial'};
    Object.entries(controls).forEach(([id,value])=>el(id).value=value);el('t5Animate').checked=spec.animate_reveal!==false;el('t5Intro').checked=spec.animate_intro!==false;
    el('t5Headline').value=spec.headline||'';el('t5Order').value=spec.order||'ascending';el('t5Layout').value=spec.layout||'fit';el('t5CardScale').value=spec.card_scale??78;el('t5CardPosition').value=spec.card_position??38;el('t5CardRadius').value=spec.card_radius??28;el('t5Normalize').checked=spec.normalize_audio!==false;
    spec.entries?.slice(0,5).forEach((e,i)=>{el('t5Url'+i).value=e.url||'';el('t5Name'+i).value=e.name||'';el('t5Price'+i).value=e.price||'';el('t5Start'+i).value=e.start||0;el('t5End'+i).value=e.duration==null?'':Number(((e.start||0)+e.duration).toFixed(2));const trim=trims[i];trim.manualEnd=e.duration!=null;trim.probed=undefined;trim.limits();if(media[i])measure(i);else trim.probe();});showPool();preview();
  }
  const presets={
    comics:{title:'Comic Sans MS',rank:'Comic Sans MS',titleColor:'#ffe84a',rankColor:'#ffffff',accent:'#ff7657',outline:'#171126',width:8,shadow:'#000000',depth:4,effect:'outline',motion:'pop'},
    bubble:{title:'Comic Sans MS',rank:'Comic Sans MS',titleColor:'#ff99dc',rankColor:'#fff2fc',accent:'#80faff',outline:'#702a99',width:10,shadow:'#321046',depth:5,effect:'outline',motion:'pop'},
    neon:{title:'Bahnschrift',rank:'Arial',titleColor:'#c2ffff',rankColor:'#ffffff',accent:'#ff73f4',outline:'#963dff',width:4,shadow:'#461670',depth:2,effect:'neon',motion:'slide'},
    marker:{title:'Segoe Print',rank:'Segoe Print',titleColor:'#ffffff',rankColor:'#ffffff',accent:'#75d9ff',outline:'#152e58',width:5,shadow:'#071225',depth:3,effect:'shadow',motion:'slide'},
    arcade:{title:'Consolas',rank:'Consolas',titleColor:'#a7ff5b',rankColor:'#ffffff',accent:'#ffdd45',outline:'#132818',width:6,shadow:'#000000',depth:5,effect:'shadow',motion:'pop'}
  };
  el('t5Preset').onchange=()=>{const p=presets[el('t5Preset').value];if(!p)return;const values={t5TitleFont:p.title,t5RankFont:p.rank,t5TitleColor:p.titleColor,t5RankColor:p.rankColor,t5Accent:p.accent,t5OutlineColor:p.outline,t5OutlineWidth:p.width,t5ShadowColor:p.shadow,t5ShadowDepth:p.depth,t5Effect:p.effect,t5AnimationStyle:p.motion};Object.entries(values).forEach(([id,value])=>el(id).value=value);el('t5Intro').checked=true;el('t5Animate').checked=true;preview(true);};
  el('topfiveForm').addEventListener('input',event=>{if(event.target.id!=='t5Preset')el('t5Preset').value='custom';preview();});el('topfiveForm').addEventListener('change',preview);el('t5PreviewStep').onchange=()=>{player.finished=false;preview(true);};el('t5Count').addEventListener('change',showPool);showPool();el('t5Replay').onclick=()=>preview(true);preview(true);
  el('topfiveForm').onsubmit=async event=>{
    event.preventDefault();el('t5Create').disabled=true;el('t5Error').textContent='';
    try{
      if(window.TopFiveTools && !await window.TopFiveTools.beforeGenerate(data()))return;
      const response=await fetch('/api/top5',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data())});const result=await response.json();
      if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:(result.detail||[]).map(x=>`${x.loc?.[1]==='entries'?`Vídeo ${Number(x.loc[2])+1}: `:''}${x.msg}`).join('\n')||'Não foi possível criar o ranking');
      location.hash='#/job/'+result.job_id;window.refresh?.();
    }catch(error){el('t5Error').textContent=error.message;}finally{el('t5Create').disabled=false;}
  };
  // "DownloadError: Vídeo 3 (Nome): motivo\nVídeo 5 (Nome): motivo" -> {3: motivo, 5: motivo}
  function parseFailures(error){
    const text=String(error||'').replace(/^\w+(?:Error|Exception): /,''),byEntry={},general=[];
    text.split('\n').forEach(line=>{const m=/^Vídeo (\d+)(?: \([^)]*\))?: ([\s\S]*)$/.exec(line.trim());if(m)byEntry[Number(m[1])-1]=m[2];else if(line.trim())general.push(line.trim());});
    return {byEntry,general:general.join(' ')};
  }
  function friendlyReason(reason){
    const [main,detail]=reason.split(/\s*Detalhe:\s*/);
    return `<p class="t5-fix-reason">${esc(main)}</p>${detail?`<p class="t5-fix-detail">${esc(detail)}</p>`:''}`;
  }
  function renderError(job,box,back){
    const key=job.id+':top5:error:'+job.error;if(box.dataset.key===key)return;box.dataset.key=key;
    const spec=job.settings.top5,entries=spec.entries,{byEntry,general}=parseFailures(job.error),failed=Object.keys(byEntry).map(Number);
    const title=failed.length?`${failed.length===1?'Um vídeo precisa':`${failed.length} vídeos precisam`} de ajuste`:'Não foi possível montar o ranking';
    box.innerHTML=`${back}<div class="state-card t5-fix">
      <div class="err-title"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg><h1>${esc(title)}</h1></div>
      <p class="state-sub">${failed.length?'Corrija só a posição marcada e tente de novo. Os vídeos que já foram baixados e os trechos prontos são reaproveitados.':esc(general||'Tente novamente; os vídeos já baixados são reaproveitados.')}</p>
      <ol class="t5-fix-list">${entries.map((e,i)=>{const end=e.duration==null?'':round2((e.start||0)+e.duration);return `<li class="t5-fix-row${i in byEntry?' is-failed':''}" data-t5-row="${i}">
        <span class="t5-number" aria-hidden="true">${i+1}</span>
        <div class="t5-fix-main">
          <div class="t5-fix-head"><div><strong>${esc(e.name)}</strong><span class="t5-fix-state" data-t5-state>${i in byEntry?'Precisa de ajuste':'Verificando…'}</span></div>
            <button type="button" class="btn btn-outline btn-sm" data-t5-edit="${i}" aria-expanded="false">Editar</button></div>
          ${i in byEntry?friendlyReason(byEntry[i]):''}
          <form class="t5-fix-form" data-t5-form="${i}" hidden novalidate>
            <label>Link do TikTok ou YouTube Shorts<input name="url" type="url" required maxlength="600" value="${esc(e.url)}"></label>
            <label>Nome ao lado do número<input name="name" required maxlength="32" value="${esc(e.name)}"></label><label>Preço · card no ranking (opcional)<input name="price" maxlength="16" value="${esc(e.price||'')}"></label>
            <div class="t5-trim"><label>Início (segundos)<input name="start" type="number" min="0" max="600" step="0.01" value="${e.start||0}"></label><label>Fim (segundos)<input name="end" type="number" min="0.5" max="${MAX_CLIP}" step="0.01" placeholder="Automático" value="${end}"></label></div>
            <p class="t5-trim-summary" data-t5-summary></p>
            <p class="t5-fix-form-error" data-t5-form-error role="alert"></p>
            <div class="t5-fix-actions"><button class="btn btn-primary btn-sm" type="submit">Salvar e tentar novamente</button><button class="btn btn-outline btn-sm" type="button" data-t5-cancel>Cancelar</button></div>
          </form>
        </div></li>`;}).join('')}</ol>
      <details class="t5-fix-tech"><summary>Detalhes técnicos</summary><div class="err-box">${esc(job.error)}</div></details>
      <div class="t5-fix-actions"><button class="btn btn-primary btn-sm" data-retry-project>Tentar novamente sem mudar</button><button class="btn btn-danger btn-sm" data-delete-project>Excluir projeto</button></div>
    </div>`;
    const known=[];
    const forms=entries.map((e,i)=>{
      const form=box.querySelector(`[data-t5-form="${i}"]`),row=box.querySelector(`[data-t5-row="${i}"]`),button=row.querySelector('[data-t5-edit]');
      const summary=()=>{form.querySelector('[data-t5-summary]').textContent=trim.check();};
      const field=n=>form.elements.namedItem(n);
      const trim=trimmer({url:field('url'),start:field('start'),end:field('end')},summary);
      trim.manualEnd=e.duration!=null;
      const toggle=open=>{form.hidden=!open;button.hidden=open;button.setAttribute('aria-expanded',String(open));if(open){
        // Início depois do fim do vídeo: volta ao trecho automático em vez de espremer 0,5s no final.
        if(known[i]&&field('url').value.trim()===e.url&&Number(field('start').value)>known[i]-.5){field('start').value=0;field('end').value='';trim.manualEnd=false;}
        if(known[i]&&field('url').value.trim()===e.url)trim.known(e.url,known[i]);else trim.probe();summary();}};
      button.onclick=()=>toggle(true);
      form.querySelector('[data-t5-cancel]').onclick=()=>{form.reset();trim.manualEnd=e.duration!=null;trim.probed=undefined;form.querySelector('[data-t5-form-error]').textContent='';toggle(false);};
      form.addEventListener('input',summary);form.addEventListener('change',summary);
      form.onsubmit=async event=>{
        event.preventDefault();trim.limits();summary();
        const problem=form.querySelector('[data-t5-form-error]');
        if(!form.checkValidity()){problem.textContent=[...form.elements].find(x=>x.validationMessage)?.validationMessage||'Confira os campos';return;}
        const submit=form.querySelector('[type=submit]');submit.disabled=true;problem.textContent='';
        const start=Number(field('start').value)||0;
        const body={url:field('url').value.trim(),name:field('name').value.trim(),price:field('price').value.trim(),start,duration:field('end').value===''?null:Math.min(MAX_CLIP,round2(Number(field('end').value)-start))};
        try{
          const response=await fetch(`/api/top5/${job.id}/entries/${i}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
          const result=await response.json().catch(()=>({}));
          if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:(result.detail||[]).map(x=>x.msg).join('\n')||'Não foi possível salvar');
          window.toast?.(`Posição ${i+1} salva · montando de novo`);box.dataset.key='';window.refresh?.();
        }catch(error){problem.textContent=error.message;submit.disabled=false;}
      };
      return {toggle};
    });
    // Abre as posições com problema depois de saber quais vídeos já estão baixados,
    // para usar a duração local em vez de consultar o TikTok de novo.
    fetch(`/api/top5/${job.id}/sources`).then(r=>r.ok?r.json():null).then(result=>{
      if(!result||box.dataset.key!==key)return;
      result.sources.forEach(s=>{
        const state=box.querySelector(`[data-t5-row="${s.index}"] [data-t5-state]`);if(!state)return;
        if(s.downloaded){known[s.index]=s.duration;const e=entries[s.index],from=e.start||0,to=Math.min(from+(e.duration??MAX_CLIP),s.duration),sec=v=>`${Math.round(v*10)/10}s`.replace('.',',');
          state.textContent=`✓ Baixado · vídeo de ${clock(s.duration)}`+(s.index in byEntry||to-from<.5?'':` · trecho ${sec(from)}–${sec(to)}`);}
        else state.textContent=s.index in byEntry?'Precisa de ajuste':'Ainda não baixado';
      });
    }).catch(()=>{}).finally(()=>{if(box.dataset.key===key)failed.forEach(i=>forms[i]?.toggle(true));});
  }
  window.TopFive={importCandidates(entries,headline){
    if(!Array.isArray(entries)||entries.length<3||entries.length>5)throw new Error('Selecione de 3 a 5 vídeos.');
    const current=data();
    if(current.entries.some(e=>e.url||e.name)&&!confirm('Substituir os vídeos e o título do ranking em edição pelos candidatos selecionados?'))return false;
    pool=null;fill({...current,headline,entries:entries.map(e=>({url:e.url,name:e.name,start:0,duration:null}))});
    location.hash='#/top5';return true;
  },importTopic(topic,count){
    const videos=(topic?.videos||[]).filter(v=>v&&v.url&&v.preview);
    if(!(count>=3&&count<=5))throw new Error('Escolha Top 3, 4 ou 5.');
    if(videos.length<count)throw new Error(`Com esses filtros o tópico tem só ${videos.length} vídeo(s).`);
    const current=data();
    if(current.entries.some(e=>e.url||e.name)&&!confirm('Substituir os vídeos e o título do ranking em edição pelos mais vistos deste tópico?'))return false;
    stopPlayer();pool={name:topic.name,videos,skipped:new Set()};
    const label=String(topic.name||'').split(' · ')[0].replace(/[“”]/g,''),headline=(topic.id?`Top ${count} ${label}`:`Top ${count} mais vistos do TikTok`).toUpperCase().slice(0,80);
    el('t5PreviewStep').value=0;
    fill({...current,headline,entries:videos.slice(0,count).map(v=>({url:v.url,name:rankName(v),start:0,duration:null}))},videos.slice(0,count));
    location.hash='#/top5';window.scrollTo({top:0});return true;
  },rankName,renderError,renderProject(job,box,back){
    if(job.status!=='done'){
      const pct=Math.round((job.progress||0)*100);
      box.innerHTML=`${back}<div class="state-card"><p class="t5-eyebrow">MONTAGEM DE RANKING</p><h1>${esc(job.title)}</h1><p class="state-sub">${job.status==='queued'?'Na fila. A montagem começa assim que o worker estiver livre.':esc(job.stage)}</p><div class="big-progress"><i style="width:${pct}%"></i></div><p>${pct}% · Download → sequência → título e ranking</p><p class="note">O processamento continua mesmo se você fechar esta página.</p></div>`;
      return;
    }
    const key=job.id+':top5:done:'+job.finished_at;if(box.dataset.key===key)return;box.dataset.key=key;
    const clip=job.manifest.clips[0],url=`/api/jobs/${job.id}/clips/${encodeURIComponent(clip.file)}`;
    box.innerHTML=`${back}<header class="t5-result-header"><div><p class="t5-eyebrow">RANKING PRONTO</p><h1>${esc(job.title)}</h1><p class="t5-help">1080 × 1920 · ${Number(clip.actual_duration).toFixed(1)} segundos · ${job.manifest.timeline.length} vídeos em sequência</p></div><div class="t5-result-actions"><a href="${url}" download class="btn btn-primary">Baixar MP4</a><a href="/api/jobs/${job.id}/download" class="btn btn-outline">Pacote + créditos</a><button class="btn btn-outline" id="t5Reuse">Editar e criar versão</button><button class="btn btn-outline" data-delete-project>Excluir</button></div></header><div class="t5-result-grid"><video src="${url}" poster="/api/jobs/${job.id}/clips/${encodeURIComponent(clip.thumbnail)}" controls playsinline preload="metadata"></video><div class="t5-panel"><h2>Sua sequência</h2><p class="t5-help">O nome é revelado no início de cada trecho e permanece no ranking.</p>${job.manifest.timeline.map(s=>`<div class="t5-timeline-row"><b>${s.rank}</b><div><strong>${esc(s.name)}</strong><p>${s.start.toFixed(1)}s → ${s.end.toFixed(1)}s · <a href="${esc(s.url)}" target="_blank" rel="noopener">Vídeo original ↗</a></p></div></div>`).join('')}<p class="t5-help">Para alterar links, nomes ou a frase, use a configuração e gere uma nova versão como outro projeto.</p></div></div>`;
    window.TopFiveTools?.mount(job,box);
    el('t5Reuse').onclick=()=>{pool=null;fill(job.settings.top5);el('t5Error').textContent='';location.hash='#/top5';};
  }};
})();
