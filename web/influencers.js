(() => {
  const root = document.getElementById('influencers');
  if (!root) return;
  root.innerHTML = `
    <header class="if-head"><span class="if-kicker">Estúdio de criação</span><h1>Influencer IA</h1>
      <p>Crie imagens ultrarrealistas de uma personagem, mantenha sua aparência com uma foto de referência, experimente uma roupa ou coloque-a em outra cena. Gere também um modelo 3D para girar e aproximar.</p></header>
    <div class="if-layout"><form id="ifForm" class="if-card">
      <h2>O que você quer criar?</h2><div class="if-mode-list" role="group" aria-label="Tipo de criação">
        <button type="button" class="if-mode" data-mode="text" aria-pressed="true"><strong>Nova influencer</strong><small>Comece só com texto</small></button>
        <button type="button" class="if-mode" data-mode="reference" aria-pressed="false"><strong>Com referência</strong><small>Use uma foto como identidade</small></button>
        <button type="button" class="if-mode" data-mode="outfit" aria-pressed="false"><strong>Trocar roupa</strong><small>Envie a foto da peça</small></button>
        <button type="button" class="if-mode" data-mode="scene" aria-pressed="false"><strong>Nova cena</strong><small>Descreva um local e ação</small></button>
        <button type="button" class="if-mode" data-mode="model" aria-pressed="false"><strong>Modelo 3D</strong><small>Gere um GLB rotacionável</small></button>
      </div><input type="hidden" name="mode" value="text">
      <label class="if-label" id="ifPersonWrap">Imagem da pessoa
        <small>Envie uma foto ou escolha uma imagem gerada na galeria.</small><span class="if-file"><span id="ifPersonName">Selecionar imagem</span><input type="file" name="person" accept=".png,.jpg,.jpeg,.webp,image/*"></span></label>
      <div class="if-source" id="ifSource" hidden>Usando imagem da galeria <button type="button" id="ifClearSource">Remover</button></div>
      <input type="hidden" name="source_job">
      <label class="if-label" id="ifSecondaryWrap" hidden><span id="ifSecondaryTitle">Imagem da roupa</span><small id="ifSecondaryHint">Fotografe a peça com boa luz e fundo simples.</small><span class="if-file"><span id="ifSecondaryName">Selecionar imagem</span><input type="file" name="secondary" accept=".png,.jpg,.jpeg,.webp,image/*"></span></label>
      <div id="ifViews" hidden><p class="if-note">Fotos laterais e de costas são opcionais, mas melhoram a forma 3D. Uma foto frontal sozinha exige que o modelo invente o lado oculto.</p>
        <label class="if-label">Lado esquerdo<span class="if-file"><input type="file" name="left" accept=".png,.jpg,.jpeg,.webp,image/*"></span></label>
        <label class="if-label">Lado direito<span class="if-file"><input type="file" name="right" accept=".png,.jpg,.jpeg,.webp,image/*"></span></label>
        <label class="if-label">Costas<span class="if-file"><input type="file" name="back" accept=".png,.jpg,.jpeg,.webp,image/*"></span></label></div>
      <label class="if-label" id="ifPromptWrap">Descrição<textarea name="description" maxlength="1800" placeholder="Ex.: influencer adulta de cabelos castanhos, retrato editorial ultra realista, luz natural, câmera profissional"></textarea><small id="ifPromptHint">Descreva aparência, pose, luz e estilo. O acabamento ultrarrealista é aplicado automaticamente.</small></label>
      <div class="if-settings" id="ifSettings"><label class="if-label">Formato<select name="aspect"><option value="3:4">Retrato 3:4</option><option value="9:16">Vertical 9:16</option><option value="1:1">Quadrado 1:1</option><option value="4:5">Social 4:5</option><option value="16:9">Horizontal 16:9</option><option value="2:3">Retrato 2:3</option></select></label>
        <label class="if-label">Resolução<select name="resolution"><option value="1K">1K · mais rápido</option><option value="2K">2K · exige mais memória</option></select></label></div>
      <button type="submit" class="if-generate" id="ifGenerate">Gerar imagem</button>
      <p class="if-note">Geração local no ComfyUI com FLUX.2 Klein 4B: sem créditos por imagem. Requer os modelos instalados e usa GPU, energia e espaço em disco. A troca de roupa pode alterar detalhes da foto.</p>
    </form><div><div class="if-card"><h2>Prévia</h2><div class="if-setup" id="ifSetup">Verificando ComfyUI…</div>
      <div class="if-result" id="ifResult"><p>Escolha um modo e gere a primeira imagem.</p></div><p class="if-error" id="ifError" role="alert" hidden></p></div>
      <div class="if-card if-gallery"><h2>Galeria</h2><div class="if-gallery-grid" id="ifGallery">Nenhuma criação ainda.</div></div>
    </div></div>`;
  const form = root.querySelector('#ifForm');
  const result = root.querySelector('#ifResult');
  const error = root.querySelector('#ifError');
  const gallery = root.querySelector('#ifGallery');
  let selected = null, displayed = '';
  const labels = {text:'Nova influencer',reference:'Com referência',outfit:'Roupa',scene:'Cena',model:'Modelo 3D'};
  const states = {queued:'Na fila',uploading:'Enviando ao ComfyUI',processing:'Gerando',done:'Pronto',error:'Falhou'};

  function mode(value) {
    form.elements.mode.value = value;
    root.querySelectorAll('.if-mode').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === value)));
    root.querySelector('#ifPersonWrap').hidden = value === 'text';
    root.querySelector('#ifSecondaryWrap').hidden = !['outfit','scene'].includes(value);
    root.querySelector('#ifViews').hidden = value !== 'model';
    root.querySelector('#ifPromptWrap').hidden = value === 'model';
    root.querySelector('#ifSettings').hidden = value === 'model';
    root.querySelector('#ifGenerate').textContent = value === 'model' ? 'Gerar modelo 3D' : 'Gerar imagem';
    root.querySelector('#ifSecondaryTitle').textContent = value === 'outfit' ? 'Imagem da roupa' : 'Imagem do local (opcional)';
    root.querySelector('#ifSecondaryHint').textContent = value === 'outfit' ? 'Fotografe a peça com boa luz e fundo simples.' : 'Se tiver uma foto do local, envie-a como referência da cena.';
    root.querySelector('#ifPromptHint').textContent = value === 'scene' ? 'Descreva o local, a ação e a iluminação.' : value === 'outfit' ? 'Descreva apenas ajustes desejados; a roupa vem da segunda imagem.' : 'Descreva aparência, pose, luz e estilo.';
    form.elements.description.required = value !== 'model';
  }
  root.querySelectorAll('.if-mode').forEach(button => button.addEventListener('click', () => mode(button.dataset.mode)));
  form.elements.person.addEventListener('change', () => {
    form.elements.source_job.value = '';
    root.querySelector('#ifSource').hidden = true;
    root.querySelector('#ifPersonName').textContent = form.elements.person.files[0]?.name || 'Selecionar imagem';
  });
  form.elements.secondary.addEventListener('change', () => {root.querySelector('#ifSecondaryName').textContent = form.elements.secondary.files[0]?.name || 'Selecionar imagem';});
  root.querySelector('#ifClearSource').addEventListener('click', () => {form.elements.source_job.value=''; root.querySelector('#ifSource').hidden=true;});

  function useImage(job, nextMode) {
    form.elements.person.value = '';
    form.elements.source_job.value = job.id;
    root.querySelector('#ifPersonName').textContent = 'Selecionar outra imagem';
    root.querySelector('#ifSource').hidden = false;
    mode(nextMode);
    root.querySelector('#ifForm').scrollIntoView({behavior:'smooth', block:'start'});
  }

  function action(text, onClick) {
    const button = document.createElement('button'); button.type='button'; button.textContent=text; button.addEventListener('click', onClick); return button;
  }

  function show(job) {
    selected = job.id;
    const key = `${job.id}:${job.status}:${job.error || ''}`;
    if (key === displayed) return;
    displayed = key; result.replaceChildren(); error.hidden = true;
    if (job.status === 'done') {
      const url = `/api/influencers/${job.id}/file`;
      if (job.kind === 'model') {
        const viewer = document.createElement('model-viewer');
        viewer.setAttribute('src', url); viewer.setAttribute('alt','Modelo 3D da influencer');
        viewer.setAttribute('camera-controls',''); viewer.setAttribute('shadow-intensity','1');
        viewer.setAttribute('touch-action','pan-y'); viewer.setAttribute('auto-rotate','');
        result.append(viewer);
        const note=document.createElement('small'); note.textContent='Arraste para girar · use a roda do mouse ou gesto de pinça para aproximar'; result.append(note);
      } else {
        const image=document.createElement('img'); image.src=url; image.alt='Influencer IA gerada'; result.append(image);
      }
      const actions=document.createElement('div'); actions.className='if-actions';
      const link=document.createElement('a'); link.href=url+'?download=true'; link.textContent=job.kind==='model'?'Baixar GLB':'Baixar imagem'; actions.append(link);
      if (job.kind==='image') {
        actions.append(action('Usar como referência',()=>useImage(job,'reference')),
          action('Trocar roupa',()=>useImage(job,'outfit')),
          action('Criar cena',()=>useImage(job,'scene')),
          action('Gerar 3D',()=>useImage(job,'model')));
      }
      result.append(actions);
    } else {
      const message=document.createElement('p'); message.textContent=states[job.status] || job.status; result.append(message);
      if (job.status==='error') {error.textContent=job.error||'A geração falhou.'; error.hidden=false;}
    }
  }

  async function config() {
    const box=root.querySelector('#ifSetup');
    try {
      const data=await fetch('/api/influencers/config').then(r=>r.json());
      box.textContent = !data.connected ? 'ComfyUI offline. Inicie o servidor local configurado no CutClips.' :
        `ComfyUI conectado · imagens ${data.images?'prontas':'sem FLUX.2 Klein local'} · 3D ${data.models?'pronto':'configure o fluxo local Hunyuan3D'}`;
      box.className=`if-setup ${data.images&&data.models?'good':'bad'}`;
    } catch {box.textContent='Não foi possível verificar o ComfyUI.'; box.className='if-setup bad';}
  }

  async function refresh() {
    if (location.hash !== '#/influencers') return;
    try {
      const data=await fetch('/api/influencers').then(r=>r.json());
      gallery.replaceChildren();
      if (!data.jobs.length) gallery.textContent='Nenhuma criação ainda.';
      for (const job of data.jobs) {
        const tile=document.createElement('button'); tile.type='button'; tile.className='if-tile';
        if (job.status==='done'&&job.kind==='image') {
          const img=document.createElement('img'); img.loading='lazy'; img.src=`/api/influencers/${job.id}/file`; img.alt=''; tile.append(img);
        }
        const label=document.createElement('span'); label.textContent=`${labels[job.mode]||job.mode} · ${states[job.status]||job.status}`; tile.append(label);
        tile.addEventListener('click',()=>show(job)); gallery.append(tile);
      }
      const chosen=data.jobs.find(job=>job.id===selected);
      if (chosen) show(chosen);
    } catch { /* Keep the last gallery while the server is unavailable. */ }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault(); error.hidden=true;
    const button=root.querySelector('#ifGenerate'), oldText=button.textContent;
    button.disabled=true; button.textContent='Enviando…';
    try {
      const payload=new FormData(form);
      const response=await fetch('/api/influencers',{method:'POST',body:payload});
      const data=await response.json();
      if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Não foi possível iniciar a geração.');
      show(data); await refresh();
    } catch (exc) {error.textContent=exc.message; error.hidden=false;}
    finally {button.disabled=false; button.textContent=oldText;}
  });
  mode('text'); config(); refresh(); setInterval(refresh,4000);
  window.addEventListener('hashchange',()=>{if(location.hash==='#/influencers'){config();refresh();}});
})();
