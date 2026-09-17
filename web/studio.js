(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = value => value == null ? '—' : Number(value).toLocaleString('pt-BR', {maximumFractionDigits: 2});
  const clipURL = (job, file) => `/api/jobs/${job}/clips/${encodeURIComponent(file)}`;
  const statusName = {draft:'Rascunho', approved:'Aprovado', published:'Publicado', queued:'Edição na fila', running:'Renderizando edição', error:'Falha na edição', done:'Edição concluída'};
  async function request(url, method = 'GET', body) {
    const response = await fetch(url, {method, ...(body === undefined ? {} : body instanceof FormData ? {body} : {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : (data.detail || []).map(x => `${x.loc?.slice(-1)}: ${x.msg}`).join('\n') || 'Não foi possível concluir');
    return data;
  }
  const dialog = document.createElement('dialog');
  dialog.className = 'studio-dialog';
  dialog.setAttribute('aria-labelledby', 'studioTitle');
  document.body.append(dialog);
  let cleanup = () => {}, dirty = false, generation = 0;
  function close() { cleanup(); cleanup = () => {}; dirty = false; generation++; dialog.close(); }
  function mayClose() { if (!dirty || confirm('Descartar as alterações ainda não salvas?')) close(); }
  dialog.addEventListener('cancel', event => { event.preventDefault(); mayClose(); });
  function open(title, html) {
    cleanup(); dirty = false; generation++;
    dialog.innerHTML = `<header><h2 id="studioTitle">${esc(title)}</h2><button type="button" class="btn btn-outline btn-sm" id="studioClose" aria-label="Fechar">Fechar</button></header><div class="studio-body">${html}</div>`;
    el('studioClose').onclick = mayClose;
    dialog.oninput = () => { dirty = true; };
    if (!dialog.open) dialog.showModal();
    return generation;
  }
  function error(message) { const box = el('studioError'); if (box) box.textContent = message; }
  function footer(label) { return `<div class="studio-footer"><button class="btn btn-primary" type="submit" id="studioSave">${label}</button><span class="studio-error" role="alert" id="studioError"></span></div>`; }
  function options(values, current) { return values.map(([v,l]) => `<option value="${v}" ${v===current?'selected':''}>${l}</option>`).join(''); }
  const input = (name,label,value,attrs='') => `<label>${label}<input name="${name}" value="${esc(value)}" ${attrs}></label>`;

  function decorate(job) {
    if (job.status !== 'done') return;
    const busy = (job.edits || []).some(x => x.status === 'queued' || x.status === 'running');
    for (const clip of job.manifest.clips) {
      const card = document.querySelector(`[data-clip-index="${clip.index}"]`);
      if (!card || card.dataset.decorated) continue;
      card.dataset.decorated = '1';
      const pub = job.publication?.[clip.index] || {};
      if (pub.title) card.querySelector('h3').textContent = pub.title;
      if (pub.thumbnail) card.querySelector('video').poster = clipURL(job.id, pub.thumbnail);
      const actions = card.querySelector('.clip-actions');
      actions.insertAdjacentHTML('afterbegin', `<button class="btn btn-primary btn-sm" data-studio="edit" data-index="${clip.index}" ${busy?'disabled':''}>Revisar e editar</button>`);
      actions.insertAdjacentHTML('beforeend', `<button class="btn btn-outline btn-sm" data-studio="publish" data-index="${clip.index}">Preparar publicação</button><button class="btn btn-outline btn-sm" data-studio="metrics" data-index="${clip.index}">Registrar resultados</button><a class="btn btn-outline btn-sm" href="/api/jobs/${job.id}/download?clip_index=${clip.index}">Pacote completo</a>${clip.subtitle?`<a class="btn btn-outline btn-sm" href="${clipURL(job.id,clip.subtitle)}">Legenda SRT</a>`:''}`);
      const task = (job.edits || []).find(x => x.clip_index === clip.index);
      const warnings = clip.warnings || [];
      card.querySelector('.clip-info').insertAdjacentHTML('afterbegin', `<div><span class="studio-status">${esc(statusName[pub.status || 'draft'])} · versão ${clip.revision || 0}</span></div>`);
      actions.insertAdjacentHTML('beforebegin', `<div class="studio-note">Seleção: ${esc(clip.provider || job.manifest.provider)} · ${clip.uncertain_words || 0} palavras com baixa confiança${clip.editorial_stale?' · avaliação anterior à edição':''}. A nota não prevê visualizações.</div>${warnings.map(w=>`<div class="studio-warning">${esc(w)}</div>`).join('')}${task && task.status !== 'done'?`<div class="studio-warning">${esc(statusName[task.status])}${task.error?`: ${esc(task.error)}`:''}. ${task.status==='error'?'A versão anterior continua disponível.':''}</div>`:''}`);
    }
    const head = document.querySelector('.head-actions');
    if (head && !head.querySelector('[data-approved]')) head.insertAdjacentHTML('beforeend', `<a data-approved class="btn btn-outline btn-sm" href="/api/jobs/${job.id}/download?approved_only=true">Baixar aprovados</a>`);
  }

  async function edit(job, clip) {
    const version = open('Revisar clipe', '<p>Carregando o vídeo original e as palavras…</p><p id="studioError" role="alert"></p>');
    let data;
    try { data = await request(`/api/jobs/${job.id}/editor/${clip.index}`); } catch (e) { error(e.message); return; }
    if (version !== generation || !dialog.open) return;
    clip = data.clip;
    const settings = {layout:'track',crop_x:0.5,crop_y:0.5,secondary_x:0.8,caption_size:78,caption_max_words:4,caption_position:'bottom',caption_style:'karaoke',captions_enabled:true,normalize_audio:true,denoise_audio:false,auto_edit:true,...clip.edit_settings};
    const select = (name,label,values) => `<label>${label}<select data-setting="${name}">${options(values,settings[name])}</select></label>`;
    const slider = (name,label) => `<label>${label}<input data-setting="${name}" type="range" min="0" max="1" step="0.01" value="${settings[name]}"></label>`;
    const check = (name,label) => `<label><input data-setting="${name}" type="checkbox" ${settings[name]?'checked':''}>${label}</label>`;
    open(`Revisar clipe ${clip.index}`, `<form id="editForm"><div class="studio-grid"><div class="studio-preview"><video id="editVideo" src="/api/jobs/${job.id}/source" controls preload="auto" playsinline></video><div class="studio-actions"><button type="button" class="btn btn-outline btn-sm" id="markStart">Marcar início</button><button type="button" class="btn btn-outline btn-sm" id="markEnd">Marcar fim</button><button type="button" class="btn btn-outline btn-sm" id="playCut">Assistir trecho</button></div><canvas id="editCanvas" width="270" height="480" aria-label="Prévia aproximada do enquadramento vertical"></canvas><p class="studio-note">Prévia aproximada. As guias marcam as margens de leitura. O rastreamento automático e o tratamento de áudio são aplicados na renderização final.</p></div><div><div class="studio-form">${input('start','Início no original (segundos)',clip.source_start,`id="editStart" type="number" min="0" max="${data.duration}" step="0.01" required`)}${input('end','Fim no original (segundos)',clip.source_end,`id="editEnd" type="number" min="0.5" max="${data.duration}" step="0.01" required`)}<div class="full studio-note" id="cutDuration"></div>${select('layout','Enquadramento',[['track','Rosto suave e centralizado'],['active','Estimar quem fala (experimental)'],['manual','Escolher posição'],['split','Dois participantes'],['fit','Imagem inteira com fundo']])}${select('caption_position','Posição da legenda',[['bottom','Inferior (área segura)'],['middle','Centro'],['top','Superior']])}${slider('crop_x','Posição horizontal / participante superior')}${slider('secondary_x','Participante inferior (tela dividida)')}${slider('crop_y','Posição vertical')}${select('caption_style','Estilo de legenda',[['karaoke','Palavra destacada'],['plain','Texto branco']])}<label>Tamanho da legenda<input data-setting="caption_size" type="number" min="24" max="120" value="${settings.caption_size}" required></label><label>Palavras por grupo<input data-setting="caption_max_words" type="number" min="1" max="8" value="${settings.caption_max_words}" required></label>${check('captions_enabled','Exibir legendas')}${check('normalize_audio','Equilibrar volume e controlar picos')}${check('denoise_audio','Reduzir ruído de fundo')}${check('auto_edit','Remover pausas longas e vícios isolados')}<div class="full"><h3>Corrigir palavras</h3><p class="studio-note">Edite os campos abaixo. A borda dourada sinaliza baixa confiança da transcrição. As correções afetam somente este clipe.</p><div class="studio-words" id="editWords"></div></div></div>${data.versions.length?`<details><summary>Versões anteriores preservadas</summary>${data.versions.map(v=>`<p><a href="${clipURL(job.id,v.clip.file)}">Versão ${v.clip.revision || 0} — baixar vídeo</a></p>`).join('')}</details>`:''}</div></div>${footer('Salvar e renderizar este clipe')}</form>`);
    const video = el('editVideo'), canvas = el('editCanvas'), ctx = canvas.getContext('2d');
    const changes = new Map();
    const wordsById = new Map(data.words.map(w => [w.id,w]));
    const readSettings = () => Object.fromEntries([...dialog.querySelectorAll('[data-setting]')].map(x => [x.dataset.setting,x.type==='checkbox'?x.checked:['range','number'].includes(x.type)?Number(x.value):x.value]));
    function rangeChanged() {
      const a = Number(el('editStart').value), b = Number(el('editEnd').value);
      el('cutDuration').textContent = `Duração selecionada: ${fmt(b-a)} s · limite de 0,5 a 180 s`;
      const words = data.words.filter(w=>w.end>a && w.start<b);
      el('editWords').innerHTML = words.map(w=>`<label class="${w.prob<0.7?'uncertain':''}" title="${fmt(w.start)} s · confiança ${Math.round(w.prob*100)}%"><input aria-label="Palavra ${w.id}: ${esc(w.text)}" data-word="${w.id}" maxlength="100" size="${Math.max(3,Math.min(17,w.text.length+1))}" value="${esc(w.text)}"></label>`).join('') || '<p class="studio-note">Nenhuma palavra neste intervalo.</p>';
    }
    el('editWords').oninput = e => { if (e.target.dataset.word !== undefined) { const id=Number(e.target.dataset.word); wordsById.get(id).text=e.target.value; changes.set(id,e.target.value); } };
    el('editStart').onchange = () => { rangeChanged(); video.currentTime=Number(el('editStart').value); };
    el('editEnd').onchange = rangeChanged;
    el('markStart').onclick = () => { el('editStart').value=video.currentTime.toFixed(2); dirty=true; rangeChanged(); };
    el('markEnd').onclick = () => { el('editEnd').value=video.currentTime.toFixed(2); dirty=true; rangeChanged(); };
    el('playCut').onclick = () => { video.currentTime=Number(el('editStart').value); video.play().catch(()=>{}); };
    video.onloadedmetadata = () => { video.currentTime=clip.source_start; };
    video.onerror = () => error('Não foi possível abrir o original neste navegador. Confira se o formato é compatível.');
    let frame;
    function draw() {
      if (video.readyState>=2) {
        const cfg = readSettings(), w=video.videoWidth, h=video.videoHeight;
        ctx.fillStyle='#000';ctx.fillRect(0,0,270,480);
        const crop = (x,y,width,height,dx,dy,dw,dh) => ctx.drawImage(video,x,y,width,height,dx,dy,dw,dh);
        if (cfg.layout==='fit') { const scale=Math.min(270/w,480/h);ctx.save();ctx.filter='blur(12px)';ctx.drawImage(video,0,0,270,480);ctx.restore();crop(0,0,w,h,(270-w*scale)/2,(480-h*scale)/2,w*scale,h*scale); }
        else if(cfg.layout==='split') { const cw=Math.min(w,h*270/240),ch=cw*240/270;crop((w-cw)*cfg.crop_x,(h-ch)*cfg.crop_y,cw,ch,0,0,270,240);crop((w-cw)*cfg.secondary_x,(h-ch)*cfg.crop_y,cw,ch,0,240,270,240); }
        else { const cw=Math.min(w,h*9/16),ch=cw*16/9;crop((w-cw)*cfg.crop_x,(h-ch)*cfg.crop_y,cw,ch,0,0,270,480); }
        ctx.strokeStyle='#ffffff55';ctx.setLineDash([4,4]);ctx.strokeRect(20,58,218,316);ctx.setLineDash([]);
        if(cfg.captions_enabled) {
          const i=data.words.findIndex(w=>w.start<=video.currentTime && w.end>=video.currentTime);
          if(i>=0) { const n=cfg.caption_max_words, begin=Math.floor(i/n)*n;const text=data.words.slice(begin,begin+n).map(w=>w.text).join(' ');ctx.font=`bold ${cfg.caption_size/4}px sans-serif`;ctx.textAlign='center';ctx.lineWidth=4;const y={top:70,middle:240,bottom:374}[cfg.caption_position];ctx.strokeStyle='#000';ctx.strokeText(text,128,y,215);ctx.fillStyle=cfg.caption_style==='karaoke'?'#ffe500':'#fff';ctx.fillText(text,128,y,215); }
        }
        if (!video.paused && video.currentTime>=Number(el('editEnd').value)) video.pause();
      }
      frame=requestAnimationFrame(draw);
    }
    rangeChanged();draw();
    cleanup=()=>{cancelAnimationFrame(frame);video.pause();video.removeAttribute('src');video.load();};
    el('editForm').onsubmit=async e=>{
      e.preventDefault();const button=el('studioSave');button.disabled=true;error('');
      try { await request(`/api/jobs/${job.id}/editor/${clip.index}`,'POST',{revision:clip.revision||0,start:Number(el('editStart').value),end:Number(el('editEnd').value),settings:readSettings(),words:[...changes].map(([id,text])=>({id,text}))});close();window.refresh?.(); }
      catch(err){error(err.message);button.disabled=false;}
    };
  }

  function publish(job, clip) {
    const pub={title:clip.title,description:clip.description||clip.text,status:'draft',youtube_url:'',scheduled_date:'',notes:'',thumbnail:clip.thumbnail,...job.publication?.[clip.index]};
    const thumbs=clip.thumbnails?.length?clip.thumbnails:[clip.thumbnail].filter(Boolean);
    open('Preparar publicação', `<form id="publishForm"><div class="studio-form"><div class="full">${input('title','Título',pub.title,'maxlength="100" required')}<div class="studio-actions">${(clip.title_options||[]).map(t=>`<button type="button" class="btn btn-outline btn-sm" data-title="${esc(t)}">${esc(t)}</button>`).join('')}</div></div><label class="full">Descrição e hashtags<textarea name="description" maxlength="5000">${esc(pub.description)}</textarea></label><label>Status<select name="status">${options([['draft','Rascunho'],['approved','Aprovado para publicar'],['published','Já publicado']],pub.status)}</select></label>${input('scheduled_date','Data planejada (organização local)',pub.scheduled_date||'','type="date"')}<div class="full">${input('youtube_url','Link do vídeo publicado',pub.youtube_url,'type="url" placeholder="https://www.youtube.com/shorts/..."')}</div><label class="full">Notas de revisão e créditos<textarea name="notes" maxlength="2000">${esc(pub.notes||job.manifest.rights_notes||'')}</textarea></label><div class="full"><h3>Capa</h3><div class="studio-covers">${thumbs.map((t,i)=>`<label><input type="radio" name="thumbnail" value="${esc(t)}" ${t===pub.thumbnail?'checked':''} aria-label="Capa ${i+1}"><img src="${clipURL(job.id,t)}" alt="Opção de capa ${i+1}"></label>`).join('')}</div></div></div><p class="studio-note">A aprovação e a data ficam salvas aqui. Envie o pacote pelo YouTube Studio e registre o link após publicar. A escolha de miniatura de Shorts depende das opções disponíveis no YouTube.</p><div class="studio-actions"><button type="button" class="btn btn-outline btn-sm" id="copyPublication">Copiar título e descrição</button><a class="btn btn-outline btn-sm" href="https://studio.youtube.com/" target="_blank" rel="noopener">Abrir YouTube Studio</a><a class="btn btn-outline btn-sm" href="/api/jobs/${job.id}/download?clip_index=${clip.index}">Baixar pacote salvo</a></div>${footer('Salvar publicação')}</form>`);
    const form=el('publishForm');
    form.querySelectorAll('[data-title]').forEach(b=>b.onclick=()=>{form.elements.title.value=b.dataset.title;dirty=true;});
    el('copyPublication').onclick=async()=>{try{await navigator.clipboard.writeText(form.elements.title.value+'\n\n'+form.elements.description.value);el('copyPublication').textContent='Copiado';}catch{error('Não foi possível copiar. Selecione o texto nos campos.');}};
    form.onsubmit=async e=>{e.preventDefault();el('studioSave').disabled=true;try{const data=Object.fromEntries(new FormData(form));data.revision=clip.revision||0;data.scheduled_date=data.scheduled_date||null;data.thumbnail=data.thumbnail||null;await request(`/api/jobs/${job.id}/publication/${clip.index}`,'PUT',data);close();window.refresh?.();}catch(err){error(err.message);el('studioSave').disabled=false;}};
  }

  async function metrics(job,clip) {
    const version=open('Registrar resultados','<p>Carregando registros…</p><p id="studioError"></p>');
    let all;try{all=await request('/api/analytics');}catch(err){error(err.message);return;}
    if(version!==generation||!dialog.open)return;
    const last=all.clips.find(x=>x.job_id===job.id&&x.clip_index===clip.index&&x.revision===(clip.revision||0))||{};
    const today=new Date();const localDate=`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
    open(`Resultados — clipe ${clip.index}`,`<form id="metricForm"><p class="studio-note">Copie os valores acumulados do YouTube Studio para esta versão do vídeo. O painel usa o registro mais recente, sem somar novamente os dias anteriores. Campos desconhecidos podem ficar vazios.</p><div class="studio-form">${input('date','Data do registro',localDate,`type="date" max="${localDate}" required`)}${input('views','Visualizações acumuladas',last.views??'','type="number" min="0" step="1" required')}${input('engaged_views','Visualizações engajadas',last.engaged_views??'','type="number" min="0" step="1"')}${input('average_percentage','Porcentagem média assistida (%)',last.average_percentage??'','type="number" min="0" max="1000" step="0.01"')}${input('subscribers','Inscritos gerados',last.subscribers??0,'type="number" min="0" step="1" required')}${input('revenue_brl','Receita acumulada (R$)',last.revenue_brl??'','type="number" min="0" step="0.01"')}${input('production_minutes','Tempo total de produção (min)',last.production_minutes??'','type="number" min="0" step="0.1"')}</div>${footer('Salvar resultados')}</form>`);
    el('metricForm').onsubmit=async e=>{e.preventDefault();el('studioSave').disabled=true;try{const data=Object.fromEntries([...new FormData(e.target)].map(([k,v])=>[k,k==='date'?v:v===''?null:Number(v)]));data.revision=clip.revision||0;await request(`/api/jobs/${job.id}/metrics/${clip.index}`,'PUT',data);close();loadAnalytics();}catch(err){error(err.message);el('studioSave').disabled=false;}};
  }

  async function loadAnalytics() {
    try {
      const data=await request('/api/analytics'), t=data.totals;
      el('analyticsPanel').innerHTML=`<p class="studio-note">${esc(data.note)}</p><div class="studio-kpis"><div>Visualizações<strong>${fmt(t.views)}</strong></div><div>Inscritos gerados<strong>${fmt(t.subscribers)}</strong></div><div>Receita informada (R$)<strong>${fmt(t.revenue_brl)}</strong></div><div>Produção (min)<strong>${fmt(t.production_minutes)}</strong></div></div><div class="studio-actions"><a class="btn btn-outline btn-sm" href="/api/analytics/template">Modelo CSV</a><button class="btn btn-outline btn-sm" id="importMetrics">Importar CSV preenchido</button><button class="btn btn-outline btn-sm" id="reloadMetrics">Atualizar painel</button><input id="metricsFile" type="file" accept=".csv" hidden></div><p class="studio-note">Use os identificadores abaixo ao preencher o modelo. Compare registros com tempo de publicação semelhante.</p><div id="metricsMessage" role="status"></div>${data.clips.length?`<div class="studio-table-wrap"><table class="studio-table"><thead><tr><th>Clipe / nicho</th><th>Visualizações</th><th>Média assistida</th><th>Inscritos / 1 mil views</th><th>Registro</th></tr></thead><tbody>${data.clips.map(r=>`<tr><td><a href="#/job/${r.job_id}">${esc(r.clip_title)}</a><br><span class="studio-note">${esc(r.niche||'Nicho não informado')} · ${r.job_id} / ${r.clip_index} / v${r.revision}</span></td><td>${fmt(r.views)}</td><td>${fmt(r.average_percentage)}${r.average_percentage==null?'':'%'}</td><td>${fmt(r.subscribers_per_1000)}</td><td>${esc(r.date)}</td></tr>`).join('')}</tbody></table></div>`:'<p>Abra um clipe e use <b>Registrar resultados</b> para começar.</p>'}`;
      el('reloadMetrics').onclick=loadAnalytics;
      el('importMetrics').onclick=()=>el('metricsFile').click();
      el('metricsFile').onchange=async e=>{if(!e.target.files[0])return;const form=new FormData();form.append('file',e.target.files[0]);try{const result=await request('/api/analytics/import','POST',form);await loadAnalytics();el('metricsMessage').textContent=`${result.imported} registros importados.`;}catch(err){el('metricsMessage').textContent=err.message;}};
    }catch(err){el('analyticsPanel').textContent=`Não foi possível carregar o painel: ${err.message}`;}
  }
  document.addEventListener('click',e=>{const b=e.target.closest('[data-studio]');if(!b)return;const job=window.studioJob;if(!job)return;const clip=job.manifest.clips.find(c=>c.index===Number(b.dataset.index));if(clip)({edit,publish,metrics}[b.dataset.studio])(job,clip);});
  window.Studio={decorate};
  if(window.studioJob)decorate(window.studioJob);
  loadAnalytics();
})();

