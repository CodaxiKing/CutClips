(() => {
  const root = document.getElementById('motion-control');
  if (!root) return;
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  root.innerHTML = `
    <div class="mc-heading"><span>Vídeo com IA</span><h1>Motion Control</h1>
      <p>Escolha uma foto da pessoa e um vídeo com os movimentos. O Wan Animate local gera um novo vídeo com a pessoa da foto seguindo a atuação do vídeo. Na aba <strong>Trocar roupa</strong>, a influencer já entra vestindo a peça que você escolher, e na aba <strong>Falar</strong> ela sincroniza os lábios com um áudio ou com um texto falado pela voz dela.</p></div>
    <div class="mc-tabs">
      <button type="button" class="mc-tab active" data-pane="motion">Motion Control</button>
      <button type="button" class="mc-tab" data-pane="tryon">Trocar roupa</button>
      <button type="button" class="mc-tab" data-pane="lipsync">Falar (lip-sync)</button>
    </div>
    <div class="mc-grid"><form class="mc-card mc-pane" id="mcForm" data-pane="motion">
      <h2>Crie seu vídeo</h2>
      <label class="mc-input"><strong>1 · Pessoa da imagem</strong><span class="mc-file" id="mcImageDrop"><span>Escolher imagem PNG, JPG ou WebP</span><input name="image" type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" required></span><small>Prefira uma pessoa visível, com rosto e corpo claros. Até 15 MB.</small></label>
      <label class="mc-input"><strong>2 · Vídeo de movimento</strong><span class="mc-file" id="mcVideoDrop"><span>Escolher vídeo MP4, WebM ou M4V</span><input name="video" type="file" accept=".mp4,.webm,.m4v,video/mp4,video/webm" required></span><small>Uma pessoa em cena, de 2 a 30 segundos. Os primeiros ~5 segundos são animados. Até 150 MB.</small></label>
      <label class="mc-input"><strong>Descrição opcional</strong><textarea name="prompt" maxlength="2500">A pessoa da imagem executa os movimentos do vídeo de referência.</textarea></label>
      <button type="submit" class="mc-generate" id="mcGenerate">Gerar Motion Control</button>
      <p class="mc-note">Geração local com Wan 2.2 Animate, sem créditos. Em GPUs de 8 GB cada vídeo leva vários minutos. O fundo vem da foto; o áudio vem do vídeo de movimento.</p>
    </form><form class="mc-card mc-pane" id="mcTryonForm" data-pane="tryon" hidden>
      <h2 id="tcTitle">Influencer dançando com a sua roupa</h2>
      <span class="mc-seg"><button type="button" data-tc-mode="outfit" class="on">Roupa · vestir</button><button type="button" data-tc-mode="product">Produto · apresentar</button></span>
      <label class="mc-input"><strong>1 · Vídeo de dança</strong><span class="mc-file" id="tcVideoDrop"><span>Escolher vídeo MP4, WebM ou M4V</span><input name="video" type="file" accept=".mp4,.webm,.m4v,video/mp4,video/webm" required></span><small>Uma pessoa em cena, de 2 a 30 segundos. Os primeiros ~5 segundos são animados. Até 150 MB.</small></label>
      <div class="mc-input"><strong>2 · Foto da influencer</strong>
        <span class="mc-seg"><button type="button" data-person="upload" class="on">Enviar foto</button><button type="button" data-person="gallery">Usar da Influencer IA</button></span>
        <span class="mc-file" id="tcPersonDrop"><span>Escolher imagem PNG, JPG ou WebP</span><input name="person" type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"></span>
        <div id="tcGallery" hidden><div class="mc-picker" id="tcPicker">Carregando gerações…</div><input type="hidden" name="person_job" value=""></div>
        <small>Prefira uma pessoa visível, com rosto e corpo claros. Também dá para usar uma imagem pronta da aba Influencer IA. Até 20 MB.</small></div>
      <label class="mc-input"><strong id="tcOutfitLabel">3 · Foto da roupa</strong><span class="mc-file" id="tcOutfitDrop"><span>Escolher imagem da peça</span><input name="outfit" type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" required></span><small id="tcOutfitHint">A peça de frente e bem iluminada: é ela que a influencer vai vestir. Até 20 MB.</small></label>
      <label class="mc-input"><strong id="tcPromptLabel">Descrição da roupa (opcional)</strong><textarea name="prompt" maxlength="2500">A pessoa veste a roupa da foto, mantendo rosto, cabelo e corpo.</textarea></label>
      <button type="submit" class="mc-generate" id="tcGenerate">Gerar com troca de roupa</button>
      <p class="mc-note" id="tcNote">Duas etapas locais, sem créditos: o FLUX veste a peça na foto e o Wan Animate anima com o vídeo. Primeiro a foto vestida (etapa 1), depois o vídeo (etapa 2).</p>
    </form><form class="mc-card mc-pane" id="mcSpeakForm" data-pane="lipsync" hidden>
      <h2>Influencer falando</h2>
      <div class="mc-input"><strong>1 · Foto da influencer</strong>
        <span class="mc-seg"><button type="button" data-speak-person="upload" class="on">Enviar foto</button><button type="button" data-speak-person="gallery">Usar da Influencer IA</button></span>
        <span class="mc-file" id="spPersonDrop"><span>Escolher imagem PNG, JPG ou WebP</span><input name="person" type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"></span>
        <div id="spGallery" hidden><div class="mc-picker" id="spPicker">Carregando gerações…</div><input type="hidden" name="person_job" value=""></div>
        <small>Foto de rosto visível, de frente para a câmera e com boa luz: é ela que ganha os lábios. Até 20 MB.</small></div>
      <div class="mc-input"><strong>2 · O que ela vai dizer</strong>
        <span class="mc-seg"><button type="button" data-speech="text" class="on">Escrever texto</button><button type="button" data-speech="audio">Enviar áudio</button></span>
        <div id="spTextWrap"><label class="mc-input"><span class="mc-sr">Texto falado</span><textarea name="text" maxlength="1000" rows="3" placeholder="Ex.: Esse produto mudou a minha rotina matinal."></textarea></label>
          <label class="mc-input"><span class="mc-sr">Voz</span><select name="voice" id="spVoice"><option value="">Carregando vozes…</option></select></label>
          <small id="spTextHint">Falado offline com a voz salva como a da influencer. Até 1000 caracteres.</small></div>
        <span class="mc-file" id="spAudioDrop" hidden><span>Escolher áudio WAV, MP3 ou M4A</span><input name="audio" type="file" accept=".wav,.mp3,.m4a,.aac,.ogg,.flac,audio/*"></span>
        <small id="spAudioHint" hidden>O áudio guia a boca do vídeo final. De 1 a 60 segundos. Até 30 MB.</small></div>
      <button type="submit" class="mc-generate" id="spGenerate">Gerar vídeo falado</button>
      <p class="mc-note">Lip-sync local (CUTCLIPS_LIPSYNC_WORKFLOW): a foto ganha lábios sincronizados com a fala. Rosto de frente e boa luz rendem melhor.</p>
    </form><div><div class="mc-card"><h2>Resultado</h2><div class="mc-status" id="mcConfig">Verificando ComfyUI…</div>
      <div class="mc-result" id="mcResult"><p>Envie uma imagem e um vídeo para começar. O resultado aparecerá aqui.</p></div>
      <p class="mc-error" id="mcError" role="alert" hidden></p></div>
      <div class="mc-card mc-history"><h2>Histórico</h2><div class="mc-history-list" id="mcHistory">Nenhuma geração ainda.</div></div>
    </div></div>`;
  const form = root.querySelector('#mcForm');
  const tryonForm = root.querySelector('#mcTryonForm');
  const speakForm = root.querySelector('#mcSpeakForm');
  const result = root.querySelector('#mcResult');
  const error = root.querySelector('#mcError');
  const history = root.querySelector('#mcHistory');
  let current = null;
  let displayed = '';
  let personSource = 'upload';
  let pickerLoaded = false;
  let tcMode = 'outfit';
  let speakPersonSource = 'upload';
  let speakPickerLoaded = false;
  let speechMode = 'text';
  let activePane = 'motion';
  let configData = null;
  const stateText = {queued:'Na fila', speaking:'Gerando a voz', dressing:'Vestindo a roupa · etapa 1 de 2', uploading:'Enviando ao ComfyUI', processing:'Gerando vídeo', done:'Concluído', error:'Falhou'};

  function stageLabel(job) {
    let base = stateText[job.status] || job.status;
    if (job.kind === 'tryon' && job.mode === 'product') {
      if (job.status === 'dressing') base = 'Aplicando o produto · etapa 1 de 2';
      if (job.status === 'uploading' || job.status === 'processing') base = `${stateText[job.status]} · etapa 2 de 2`;
      return base;
    }
    return job.kind === 'tryon' && (job.status === 'uploading' || job.status === 'processing')
      ? `${base} · etapa 2 de 2` : base;
  }

  function preview(input, holder, tag) {
    const file = input.files[0];
    holder.querySelectorAll('img,video').forEach(el => el.remove());
    if (!file) return;
    const previous = holder.dataset.previewUrl;
    if (previous) URL.revokeObjectURL(previous);
    const url = URL.createObjectURL(file); holder.dataset.previewUrl = url;
    const el = document.createElement(tag);
    el.src = url; if (tag === 'video') { el.controls = true; el.muted = true; }
    holder.insertBefore(el, input);
    holder.querySelector('span').textContent = file.name;
  }
  const image = form.elements.image, video = form.elements.video;
  image.addEventListener('change', () => preview(image, root.querySelector('#mcImageDrop'), 'img'));
  video.addEventListener('change', () => preview(video, root.querySelector('#mcVideoDrop'), 'video'));

  const tcVideo = tryonForm.elements.video, tcPerson = tryonForm.elements.person;
  const tcOutfit = tryonForm.elements.outfit, tcPersonJob = tryonForm.elements.person_job;
  tcVideo.addEventListener('change', () => preview(tcVideo, root.querySelector('#tcVideoDrop'), 'video'));
  tcPerson.addEventListener('change', () => preview(tcPerson, root.querySelector('#tcPersonDrop'), 'img'));
  tcOutfit.addEventListener('change', () => preview(tcOutfit, root.querySelector('#tcOutfitDrop'), 'img'));

  const spPerson = speakForm.elements.person, spAudio = speakForm.elements.audio;
  const spPersonJob = speakForm.elements.person_job;
  spPerson.addEventListener('change', () => preview(spPerson, root.querySelector('#spPersonDrop'), 'img'));
  spAudio.addEventListener('change', () => preview(spAudio, root.querySelector('#spAudioDrop'), 'img'));

  root.querySelectorAll('.mc-tab').forEach(tab => tab.addEventListener('click', () => {
    root.querySelectorAll('.mc-tab').forEach(other => other.classList.toggle('active', other === tab));
    root.querySelectorAll('.mc-pane').forEach(pane => { pane.hidden = pane.dataset.pane !== tab.dataset.pane; });
    activePane = tab.dataset.pane;
    renderConfig();
    if (tab.dataset.pane === 'tryon') loadPicker();
    if (tab.dataset.pane === 'lipsync') { loadSpeakPicker(); loadVoices(); }
  }));

  function setPersonMode(mode) {
    personSource = mode;
    tryonForm.querySelectorAll('[data-person]').forEach(btn => btn.classList.toggle('on', btn.dataset.person === mode));
    root.querySelector('#tcPersonDrop').hidden = mode !== 'upload';
    root.querySelector('#tcGallery').hidden = mode === 'upload';
    if (mode === 'upload') {
      tcPersonJob.value = '';
    } else {
      tcPerson.value = '';
      preview(tcPerson, root.querySelector('#tcPersonDrop'), 'img');
      loadPicker();
    }
  }
  tryonForm.querySelectorAll('[data-person]').forEach(btn => btn.addEventListener('click', () => setPersonMode(btn.dataset.person)));

  function setTcMode(mode) {
    tcMode = mode;
    const product = mode === 'product';
    tryonForm.querySelectorAll('[data-tc-mode]').forEach(btn => btn.classList.toggle('on', btn.dataset.tcMode === mode));
    root.querySelector('#tcTitle').textContent = product ? 'Influencer apresentando o seu produto' : 'Influencer dançando com a sua roupa';
    root.querySelector('#tcOutfitLabel').textContent = product ? '3 · Foto do produto' : '3 · Foto da roupa';
    root.querySelector('#tcOutfitDrop').querySelector('span').textContent = product ? 'Escolher imagem do produto' : 'Escolher imagem da peça';
    root.querySelector('#tcOutfitHint').textContent = product
      ? 'O produto de frente, com rótulo legível: é ele que a influencer vai mostrar na câmera. Até 20 MB.'
      : 'A peça de frente e bem iluminada: é ela que a influencer vai vestir. Até 20 MB.';
    root.querySelector('#tcPromptLabel').textContent = product ? 'Descrição do produto (opcional)' : 'Descrição da roupa (opcional)';
    tryonForm.elements.prompt.value = product
      ? 'A pessoa apresenta o produto da foto para a câmera, segurando-o naturalmente, mantendo rosto e cabelo.'
      : 'A pessoa veste a roupa da foto, mantendo rosto, cabelo e corpo.';
    root.querySelector('#tcGenerate').textContent = product ? 'Gerar com o produto' : 'Gerar com troca de roupa';
    root.querySelector('#tcNote').textContent = product
      ? 'Duas etapas locais, sem créditos: o FLUX coloca o produto na mão da influencer e o Wan Animate anima com o vídeo.'
      : 'Duas etapas locais, sem créditos: o FLUX veste a peça na foto e o Wan Animate anima com o vídeo. Primeiro a foto vestida (etapa 1), depois o vídeo (etapa 2).';
  }
  tryonForm.querySelectorAll('[data-tc-mode]').forEach(btn => btn.addEventListener('click', () => setTcMode(btn.dataset.tcMode)));

  async function loadPicker() {
    if (pickerLoaded) return;
    const box = root.querySelector('#tcPicker');
    try {
      const data = await fetch('/api/influencers').then(r => r.json());
      const jobs = data.jobs.filter(job => job.status === 'done' && job.kind === 'image');
      box.replaceChildren();
      if (!jobs.length) {
        box.textContent = 'Nenhuma imagem pronta ainda — gere uma na aba Influencer IA.';
        pickerLoaded = true;
        return;
      }
      for (const job of jobs) {
        const tile = document.createElement('button'); tile.type = 'button'; tile.className = 'mc-pick';
        const img = document.createElement('img'); img.loading = 'lazy';
        img.src = `/api/influencers/${job.id}/file`; img.alt = '';
        const caption = document.createElement('small'); caption.textContent = job.description || 'Influencer IA';
        tile.append(img, caption);
        tile.addEventListener('click', () => {
          tcPersonJob.value = job.id;
          box.querySelectorAll('.mc-pick').forEach(other => other.classList.toggle('on', other === tile));
        });
        box.append(tile);
      }
      pickerLoaded = true;
    } catch {
      box.textContent = 'Não foi possível carregar as gerações da Influencer IA.';
    }
  }

  function setSpeakPersonMode(mode) {
    speakPersonSource = mode;
    speakForm.querySelectorAll('[data-speak-person]').forEach(btn => btn.classList.toggle('on', btn.dataset.speakPerson === mode));
    root.querySelector('#spPersonDrop').hidden = mode !== 'upload';
    root.querySelector('#spGallery').hidden = mode === 'upload';
    if (mode === 'upload') {
      spPersonJob.value = '';
    } else {
      spPerson.value = '';
      preview(spPerson, root.querySelector('#spPersonDrop'), 'img');
      loadSpeakPicker();
    }
  }
  speakForm.querySelectorAll('[data-speak-person]').forEach(btn => btn.addEventListener('click', () => setSpeakPersonMode(btn.dataset.speakPerson)));

  function setSpeechMode(mode) {
    speechMode = mode;
    speakForm.querySelectorAll('[data-speech]').forEach(btn => btn.classList.toggle('on', btn.dataset.speech === mode));
    root.querySelector('#spTextWrap').hidden = mode !== 'text';
    root.querySelector('#spAudioDrop').hidden = mode !== 'audio';
    root.querySelector('#spAudioHint').hidden = mode !== 'audio';
  }
  speakForm.querySelectorAll('[data-speech]').forEach(btn => btn.addEventListener('click', () => setSpeechMode(btn.dataset.speech)));

  async function loadSpeakPicker() {
    if (speakPickerLoaded) return;
    const box = root.querySelector('#spPicker');
    try {
      const data = await fetch('/api/influencers').then(r => r.json());
      const jobs = data.jobs.filter(job => job.status === 'done' && job.kind === 'image');
      box.replaceChildren();
      if (!jobs.length) {
        box.textContent = 'Nenhuma imagem pronta ainda — gere uma na aba Influencer IA.';
        speakPickerLoaded = true;
        return;
      }
      for (const job of jobs) {
        const tile = document.createElement('button'); tile.type = 'button'; tile.className = 'mc-pick';
        const img = document.createElement('img'); img.loading = 'lazy';
        img.src = `/api/influencers/${job.id}/file`; img.alt = '';
        const caption = document.createElement('small'); caption.textContent = job.description || 'Influencer IA';
        tile.append(img, caption);
        tile.addEventListener('click', () => {
          spPersonJob.value = job.id;
          box.querySelectorAll('.mc-pick').forEach(other => other.classList.toggle('on', other === tile));
        });
        box.append(tile);
      }
      speakPickerLoaded = true;
    } catch {
      box.textContent = 'Não foi possível carregar as gerações da Influencer IA.';
    }
  }

  let voicesLoaded = false;
  async function loadVoices() {
    if (voicesLoaded) return;
    const select = root.querySelector('#spVoice');
    try {
      const data = await fetch('/api/narration/voices').then(r => r.json());
      const profile = data.influencer || {};
      let voices = data.voices || [];
      // A voz salva como a da influencer vem primeiro e vira o padrão do campo.
      if (profile.voice && !voices.includes(profile.voice)) voices = [profile.voice, ...voices];
      if (!voices.length) {
        select.innerHTML = '<option value="">Nenhuma voz no sistema — envie um áudio</option>';
        return;
      }
      const chosen = profile.voice || data.default || voices[0];
      select.innerHTML = voices.map(v => `<option value="${esc(v)}"${v === chosen ? ' selected' : ''}>${esc(v)}${profile.voice === v ? ' · voz da influencer' : ''}</option>`).join('');
      voicesLoaded = true;
    } catch {
      select.innerHTML = '<option value="">Não foi possível listar as vozes — envie um áudio</option>';
    }
  }

  async function config() {
    try {
      configData = await fetch('/api/motion-control/config').then(r => r.json());
    } catch { configData = {connected: false}; }
    renderConfig();
  }

  function renderConfig() {
    const box = root.querySelector('#mcConfig');
    const data = configData;
    if (!data) return;
    if (!data.connected) {
      box.textContent = `ComfyUI offline. Inicie o ComfyUI em ${data.url || 'http://127.0.0.1:8188'} (o Iniciar CutClips.cmd faz isso quando CUTCLIPS_COMFYUI_DIR está configurado).`;
      box.className = 'mc-status bad';
      return;
    }
    // Cada aba só precisa do que ela mesma usa: Wan/FLUX para animar, o fluxo
    // exportado para o lip-sync. Exigir tudo em toda aba esconderia o que está pronto.
    const problems = [];
    if (activePane !== 'lipsync') {
      if (!data.ready) {
        problems.push(data.missing?.length ? `Wan Animate incompleto: ${data.missing.join(', ')}`
          : !data.template ? 'o fluxo em CUTCLIPS_WAN_WORKFLOW não pôde ser lido'
          : 'o fluxo Wan usa nós que não existem nesta instalação');
      }
      if (activePane === 'tryon' && !data.flux_ready) {
        problems.push(data.flux_missing?.length ? `FLUX incompleto: ${data.flux_missing.join(', ')}`
          : 'FLUX indisponível nesta instalação');
      }
    } else if (!data.lipsync_ready) {
      problems.push(data.lipsync_missing?.length ? `lip-sync: ${data.lipsync_missing.join(', ')}`
        : 'lip-sync indisponível nesta instalação');
    }
    box.textContent = problems.length
      ? `ComfyUI conectado, mas incompleto — ${problems.join('; ')}.`
      : activePane === 'lipsync' ? 'ComfyUI conectado · fluxo de lip-sync disponível'
      : activePane === 'tryon' ? 'ComfyUI conectado · Wan Animate e FLUX prontos (troca de roupa)'
      : 'ComfyUI conectado · Wan Animate local disponível';
    box.className = `mc-status ${problems.length ? 'bad' : 'good'}`;
  }

  async function refresh() {
    if (location.hash !== '#/motion-control') return;
    try {
      const data = await fetch('/api/motion-control').then(r => r.json());
      history.replaceChildren();
      if (!data.jobs.length) history.textContent = 'Nenhuma geração ainda.';
      for (const job of data.jobs) {
        const row = document.createElement('div'); row.className = 'mc-job';
        const label = document.createElement('span');
        const origin = job.kind === 'tryon' ? `${job.mode === 'product' ? 'Produto' : 'Trocar roupa'} · ${job.outfit_name || 'Imagem'}`
          : job.kind === 'lipsync' ? `Falando · ${job.audio_name || 'texto'}`
          : (job.image_name || 'Imagem');
        label.textContent = `${origin} · ${stageLabel(job)}`;
        row.append(label);
        const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Ver';
        button.addEventListener('click', () => { current = job.id; show(job); }); row.append(button);
        history.append(row);
      }
      const selected = data.jobs.find(j => j.id === current);
      if (selected) show(selected);
    } catch { /* Keep the previous history while the API is unavailable. */ }
  }

  function show(job) {
    const key = `${job.id}:${job.status}:${job.composed || ''}:${job.error || ''}`;
    if (key === displayed) return;
    displayed = key;
    result.replaceChildren(); error.hidden = true;
    if (job.kind === 'tryon' && job.composed) {
      const product = job.mode === 'product';
      const box = document.createElement('div'); box.className = 'mc-composed';
      const img = document.createElement('img');
      img.src = `/api/motion-control/${job.id}/composed`;
      img.alt = product ? 'Influencer já com o produto escolhido' : 'Influencer já com a roupa escolhida';
      const caption = document.createElement('span');
      caption.textContent = product
        ? (job.status === 'done' ? 'Etapa 1 · a influencer já estava com o produto' : 'Etapa 1 · a influencer está com o produto')
        : (job.status === 'done' ? 'Etapa 1 · a influencer já estava com a roupa' : 'Etapa 1 · a influencer está com a roupa');
      box.append(img, caption); result.append(box);
    }
    if (job.status === 'done') {
      const player = document.createElement('video');
      player.src = `/api/motion-control/${job.id}/view`; player.controls = true; player.preload = 'metadata';
      result.append(player);
      const link = document.createElement('a'); link.href = `/api/motion-control/${job.id}/download`; link.textContent = 'Baixar vídeo';
      link.className = 'btn btn-primary'; link.download = `motion-control-${job.id.slice(0,8)}.mp4`;
      result.append(link);
    } else {
      const message = document.createElement('p'); message.textContent = stageLabel(job); result.append(message);
      if (job.status === 'error') { error.textContent = job.error || 'A geração falhou.'; error.hidden = false; }
    }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault(); error.hidden = true;
    if (!image.files[0] || !video.files[0]) return;
    if (image.files[0].size > 15 * 1024 * 1024 || video.files[0].size > 150 * 1024 * 1024) {
      error.textContent = 'Imagem ou vídeo acima do limite permitido.'; error.hidden = false; return;
    }
    const button = root.querySelector('#mcGenerate'); button.disabled = true; button.textContent = 'Enviando…';
    try {
      const body = new FormData(form);
      const response = await fetch('/api/motion-control', {method:'POST', body});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível iniciar a geração.');
      current = data.id; show(data); await refresh();
    } catch (exc) { error.textContent = exc.message; error.hidden = false; }
    finally { button.disabled = false; button.textContent = 'Gerar Motion Control'; }
  });

  tryonForm.addEventListener('submit', async event => {
    event.preventDefault(); error.hidden = true;
    if (!tcVideo.files[0] || !tcOutfit.files[0]) {
      error.textContent = 'Envie o vídeo de dança e a foto da roupa.'; error.hidden = false; return;
    }
    if (personSource === 'upload' && !tcPerson.files[0]) {
      error.textContent = 'Envie a foto da influencer ou clique em “Usar da Influencer IA”.'; error.hidden = false; return;
    }
    if (personSource !== 'upload' && !tcPersonJob.value) {
      error.textContent = 'Escolha uma imagem gerada na aba Influencer IA.'; error.hidden = false; return;
    }
    if (tcVideo.files[0].size > 150 * 1024 * 1024 || tcOutfit.files[0].size > 20 * 1024 * 1024 ||
        (personSource === 'upload' && tcPerson.files[0].size > 20 * 1024 * 1024)) {
      error.textContent = 'Imagem ou vídeo acima do limite permitido.'; error.hidden = false; return;
    }
    const button = root.querySelector('#tcGenerate'); button.disabled = true; button.textContent = 'Enviando…';
    try {
      const body = new FormData();
      body.append('video', tcVideo.files[0]);
      body.append('outfit', tcOutfit.files[0]);
      if (personSource === 'upload') body.append('person', tcPerson.files[0]);
      else body.append('person_job', tcPersonJob.value);
      body.append('prompt', tryonForm.elements.prompt.value);
      body.append('mode', tcMode);
      const response = await fetch('/api/motion-control/tryon', {method:'POST', body});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível iniciar a geração.');
      current = data.id; show(data); await refresh();
    } catch (exc) { error.textContent = exc.message; error.hidden = false; }
    finally { button.disabled = false; button.textContent = tcMode === 'product' ? 'Gerar com o produto' : 'Gerar com troca de roupa'; }
  });

  speakForm.addEventListener('submit', async event => {
    event.preventDefault(); error.hidden = true;
    if (speakPersonSource === 'upload' && !spPerson.files[0]) {
      error.textContent = 'Envie a foto da influencer ou clique em “Usar da Influencer IA”.'; error.hidden = false; return;
    }
    if (speakPersonSource !== 'upload' && !spPersonJob.value) {
      error.textContent = 'Escolha uma imagem gerada na aba Influencer IA.'; error.hidden = false; return;
    }
    const speech = speakForm.elements.text.value.trim();
    if (speechMode === 'audio' && !spAudio.files[0]) {
      error.textContent = 'Escolha o áudio da fala ou volte para “Escrever texto”.'; error.hidden = false; return;
    }
    if (speechMode === 'text' && !speech) {
      error.textContent = 'Escreva o que a influencer vai falar.'; error.hidden = false; return;
    }
    if ((speakPersonSource === 'upload' && spPerson.files[0].size > 20 * 1024 * 1024) ||
        (speechMode === 'audio' && spAudio.files[0].size > 30 * 1024 * 1024)) {
      error.textContent = 'Imagem ou áudio acima do limite permitido.'; error.hidden = false; return;
    }
    const button = root.querySelector('#spGenerate'); button.disabled = true; button.textContent = 'Enviando…';
    try {
      const body = new FormData();
      if (speakPersonSource === 'upload') body.append('person', spPerson.files[0]);
      else body.append('person_job', spPersonJob.value);
      if (speechMode === 'audio') body.append('audio', spAudio.files[0]);
      else { body.append('text', speech); body.append('voice', speakForm.elements.voice.value); }
      const response = await fetch('/api/motion-control/lipsync', {method:'POST', body});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível iniciar a geração.');
      current = data.id; show(data); await refresh();
    } catch (exc) { error.textContent = exc.message; error.hidden = false; }
    finally { button.disabled = false; button.textContent = 'Gerar vídeo falado'; }
  });

  config(); refresh(); setInterval(refresh, 4000);
  window.addEventListener('hashchange', () => { if (location.hash === '#/motion-control') { config(); refresh(); } });
})();
