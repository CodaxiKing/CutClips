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
      <button type="button" class="mc-tab" data-pane="sell">Vender (one-shot)</button>
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
      <label class="mc-check"><input type="checkbox" name="refine"> Passe extra do FLUX para corrigir mãos e rótulo (+~40 s)</label>
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
        <span class="mc-seg"><button type="button" data-speech="text" class="on">Escrever texto</button><button type="button" data-speech="reply">Responder comentário</button><button type="button" data-speech="audio">Enviar áudio</button></span>
        <div id="spTextWrap"><label class="mc-input"><span class="mc-sr">Texto falado</span><textarea name="text" maxlength="2000" rows="3" placeholder="Ex.: Esse produto mudou a minha rotina matinal."></textarea></label>
          <label class="mc-input"><span class="mc-sr">Voz</span><select name="voice" id="spVoice"><option value="">Carregando vozes…</option></select></label>
          <small id="spTextHint">Falado offline com a voz salva como a da influencer. Até 1000 caracteres.</small></div>
        <span class="mc-file" id="spAudioDrop" hidden><span>Escolher áudio WAV, MP3 ou M4A</span><input name="audio" type="file" accept=".wav,.mp3,.m4a,.aac,.ogg,.flac,audio/*"></span>
        <small id="spAudioHint" hidden>O áudio guia a boca do vídeo final. De 1 a 60 segundos. Até 30 MB.</small></div>
      <button type="submit" class="mc-generate" id="spGenerate">Gerar vídeo falado</button>
      <p class="mc-note">Lip-sync local (CUTCLIPS_LIPSYNC_WORKFLOW): a foto ganha lábios sincronizados com a fala. Rosto de frente e boa luz rendem melhor.</p>
      <p class="mc-note" id="spBatchNote" hidden></p>
    </form><form class="mc-card mc-pane" id="mcSellForm" data-pane="sell" hidden>
      <h2>Vídeo de vendas completo</h2>
      <span class="mc-seg"><button type="button" data-sl-mode="product" class="on">Produto · apresentar</button><button type="button" data-sl-mode="outfit">Roupa · vestir</button></span>
      <label class="mc-input"><strong>1 · Vídeo de dança</strong><span class="mc-file" id="slVideoDrop"><span>Escolher vídeo MP4, WebM ou M4V</span><input name="video" type="file" accept=".mp4,.webm,.m4v,video/mp4,video/webm" required></span><small>Uma pessoa em cena, de 2 a 30 segundos. Até 150 MB.</small></label>
      <div class="mc-input"><strong>2 · Foto da influencer</strong>
        <span class="mc-seg"><button type="button" data-sl-person="upload" class="on">Enviar foto</button><button type="button" data-sl-person="gallery">Usar da Influencer IA</button></span>
        <span class="mc-file" id="slPersonDrop"><span>Escolher imagem PNG, JPG ou WebP</span><input name="person" type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"></span>
        <div id="slGallery" hidden><div class="mc-picker" id="slPicker">Carregando gerações…</div><input type="hidden" name="person_job" value=""></div>
        <small>Foto de rosto visível, de frente e com boa luz. Até 20 MB.</small></div>
      <label class="mc-input"><strong id="slProductLabel">3 · Foto do produto (opcional)</strong><span class="mc-file" id="slProductDrop"><span>Escolher imagem do produto</span><input name="product" type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"></span><small id="slProductHint">Com a foto, o FLUX coloca o produto em cena antes da animação. Sem ela, o vídeo de dança sai só com a foto da influencer.</small></label>
      <div class="mc-input"><strong>4 · Produto e roteiro</strong>
        <input type="text" name="product_name" maxlength="120" required placeholder="Nome do produto — ex.: Sérum Vitamina C">
        <input type="text" name="benefit" maxlength="200" placeholder="Benefício (opcional) — ex.: acabou com a minha olheira">
        <input type="text" name="cta" maxlength="200" placeholder="Chamada final (opcional) — ex.: o link tá na vitrine">
        <textarea name="script" maxlength="1000" rows="3" placeholder="Roteiro escrito por você (opcional). Vazio: a IA escreve, ou um template fixo quando não há chave."></textarea>
        <small>O roteiro vira a voz da influencer (a salva como a dela) e lip-sync. Benefício e chamada alimentam a IA ou o template.</small></div>
      <label class="mc-check"><input type="checkbox" name="refine"> Passe extra do FLUX para corrigir mãos e rótulo (+~40 s)</label>
      <label class="mc-input"><strong>Preço (opcional)</strong><input name="price" maxlength="40" placeholder="Ex.: R$ 49,90"><small>Card de preço em pílula amarela, queimado no topo de todos os vídeos do job (exige ffmpeg com libass).</small></label>
      <button type="submit" class="mc-generate" id="slGenerate">Gerar os dois vídeos</button>
      <p class="mc-note">Um job, dois vídeos: ela dançando com o produto e ela falando o roteiro de venda. Exige Wan, FLUX (se houver foto do produto) e o fluxo de lip-sync configurado. Com <code>CUTCLIPS_LIPSYNC_VIDEO_WORKFLOW</code>, sai um terceiro vídeo com ela dançando e falando ao mesmo tempo.</p>
    </form><div><div class="mc-card"><h2>Resultado</h2><div class="mc-status" id="mcConfig">Verificando ComfyUI…</div>
      <div class="mc-status" id="mcQueue" hidden></div>
      <label class="mc-check"><input type="checkbox" id="mcNotify"> Avisar no desktop quando uma geração concluir ou falhar</label>
      <div class="mc-result" id="mcResult"><p>Envie uma imagem e um vídeo para começar. O resultado aparecerá aqui.</p></div>
      <p class="mc-error" id="mcError" role="alert" hidden></p></div>
      <div class="mc-card mc-history"><h2>Histórico</h2><div class="mc-history-list" id="mcHistory">Nenhuma geração ainda.</div><p class="mc-note" id="mcStorage" hidden></p></div>
    </div></div>`;
  const form = root.querySelector('#mcForm');
  const tryonForm = root.querySelector('#mcTryonForm');
  const speakForm = root.querySelector('#mcSpeakForm');
  const sellForm = root.querySelector('#mcSellForm');
  const result = root.querySelector('#mcResult');
  const error = root.querySelector('#mcError');
  const history = root.querySelector('#mcHistory');
  let current = null;
  let displayed = '';
  let personSource = 'upload';
  let pickerLoaded = false;
  let tcMode = 'outfit';
  let slMode = 'product';
  let slPersonSource = 'upload';
  let slPickerLoaded = false;
  let speakPersonSource = 'upload';
  let speakPickerLoaded = false;
  let speechMode = 'text';
  let activePane = 'motion';
  let configData = null;
  const ACTIVE = ['queued', 'speaking', 'dressing', 'uploading', 'processing', 'talking'];
  const stateText = {queued:'Na fila', speaking:'Gerando a voz', dressing:'Vestindo a roupa · etapa 1 de 2', uploading:'Enviando ao ComfyUI', processing:'Gerando vídeo', done:'Concluído', error:'Falhou', cancelled:'Cancelado'};

  function stageBase(job) {
    if (job.kind === 'oneshot') {
      const one = {queued:'Na fila', speaking:'Gravando a voz da influencer', dressing:'Aplicando o produto',
                   uploading:'Preparando o vídeo de dança', processing:'Gerando o vídeo de dança',
                   talking:'Sincronizando os lábios', done:'Concluído · 2 vídeos', error:'Falhou',
                   cancelled:'Cancelado'};
      return one[job.status] || job.status;
    }
    let base = stateText[job.status] || job.status;
    if (job.status === 'speaking' && job.reply) base = 'Escrevendo a resposta · gravando voz';
    if (job.kind === 'tryon' && job.mode === 'product') {
      if (job.status === 'dressing') base = 'Aplicando o produto · etapa 1 de 2';
      if (job.status === 'uploading' || job.status === 'processing') base = `${stateText[job.status]} · etapa 2 de 2`;
      return base;
    }
    return job.kind === 'tryon' && (job.status === 'uploading' || job.status === 'processing')
      ? `${base} · etapa 2 de 2` : base;
  }

  function stageLabel(job) {
    // Percentual real da etapa, vindo do WebSocket do ComfyUI.
    const base = stageBase(job);
    return ACTIVE.includes(job.status) && typeof job.progress === 'number'
      ? `${base} · ${job.progress}%` : base;
  }

  function originLabel(job) {
    return job.kind === 'oneshot' ? `Venda · ${job.product_name || 'Produto'}`
      : job.kind === 'tryon' ? `${job.mode === 'product' ? 'Produto' : 'Trocar roupa'} · ${job.outfit_name || 'Imagem'}`
      : job.kind === 'lipsync' ? `Falando · ${job.reply ? 'resposta ao comentário' : job.audio_name || 'texto'}`
      : (job.image_name || 'Imagem');
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

  const slVideo = sellForm.elements.video, slPerson = sellForm.elements.person;
  const slProduct = sellForm.elements.product, slPersonJob = sellForm.elements.person_job;
  slVideo.addEventListener('change', () => preview(slVideo, root.querySelector('#slVideoDrop'), 'video'));
  slPerson.addEventListener('change', () => preview(slPerson, root.querySelector('#slPersonDrop'), 'img'));
  slProduct.addEventListener('change', () => preview(slProduct, root.querySelector('#slProductDrop'), 'img'));

  root.querySelectorAll('.mc-tab').forEach(tab => tab.addEventListener('click', () => {
    root.querySelectorAll('.mc-tab').forEach(other => other.classList.toggle('active', other === tab));
    root.querySelectorAll('.mc-pane').forEach(pane => { pane.hidden = pane.dataset.pane !== tab.dataset.pane; });
    activePane = tab.dataset.pane;
    renderConfig();
    if (tab.dataset.pane === 'tryon') loadPicker();
    if (tab.dataset.pane === 'lipsync') { loadSpeakPicker(); loadVoices(); }
    if (tab.dataset.pane === 'sell') loadSellPicker();
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
    root.querySelector('#spTextWrap').hidden = mode === 'audio';
    root.querySelector('#spAudioDrop').hidden = mode !== 'audio';
    root.querySelector('#spAudioHint').hidden = mode !== 'audio';
    const box = speakForm.elements.text;
    if (mode === 'reply') {
      box.placeholder = 'Cole os comentários, um por linha — a IA responde no tom da influencer.';
      root.querySelector('#spTextHint').textContent = 'A IA escreve a resposta e ela fala com a voz escolhida. Uma linha = um comentário; várias linhas viram um lote de até 10 vídeos. Sem chave de IA, um template agradecido responde.';
    } else {
      box.placeholder = 'Ex.: Esse produto mudou a minha rotina matinal.';
      root.querySelector('#spTextHint').textContent = 'Falado offline com a voz salva como a da influencer. Até 1000 caracteres.';
    }
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

  function setSlPersonMode(mode) {
    slPersonSource = mode;
    sellForm.querySelectorAll('[data-sl-person]').forEach(btn => btn.classList.toggle('on', btn.dataset.slPerson === mode));
    root.querySelector('#slPersonDrop').hidden = mode !== 'upload';
    root.querySelector('#slGallery').hidden = mode === 'upload';
    if (mode === 'upload') {
      slPersonJob.value = '';
    } else {
      slPerson.value = '';
      preview(slPerson, root.querySelector('#slPersonDrop'), 'img');
      loadSellPicker();
    }
  }
  sellForm.querySelectorAll('[data-sl-person]').forEach(btn => btn.addEventListener('click', () => setSlPersonMode(btn.dataset.slPerson)));

  function setSlMode(mode) {
    slMode = mode;
    const outfit = mode === 'outfit';
    sellForm.querySelectorAll('[data-sl-mode]').forEach(btn => btn.classList.toggle('on', btn.dataset.slMode === mode));
    root.querySelector('#slProductLabel').textContent = outfit ? '3 · Foto da roupa (opcional)' : '3 · Foto do produto (opcional)';
    root.querySelector('#slProductDrop').querySelector('span').textContent = outfit ? 'Escolher imagem da peça' : 'Escolher imagem do produto';
    root.querySelector('#slProductHint').textContent = outfit
      ? 'Com a foto, o FLUX veste a peça na influencer antes da animação. Sem ela, o vídeo de dança sai só com a foto da influencer.'
      : 'Com a foto, o FLUX coloca o produto em cena antes da animação. Sem ela, o vídeo de dança sai só com a foto da influencer.';
  }
  sellForm.querySelectorAll('[data-sl-mode]').forEach(btn => btn.addEventListener('click', () => setSlMode(btn.dataset.slMode)));

  async function loadSellPicker() {
    if (slPickerLoaded) return;
    const box = root.querySelector('#slPicker');
    try {
      const data = await fetch('/api/influencers').then(r => r.json());
      const jobs = data.jobs.filter(job => job.status === 'done' && job.kind === 'image');
      box.replaceChildren();
      if (!jobs.length) {
        box.textContent = 'Nenhuma imagem pronta ainda — gere uma na aba Influencer IA.';
        slPickerLoaded = true;
        return;
      }
      for (const job of jobs) {
        const tile = document.createElement('button'); tile.type = 'button'; tile.className = 'mc-pick';
        const img = document.createElement('img'); img.loading = 'lazy';
        img.src = `/api/influencers/${job.id}/file`; img.alt = '';
        const caption = document.createElement('small'); caption.textContent = job.description || 'Influencer IA';
        tile.append(img, caption);
        tile.addEventListener('click', () => {
          slPersonJob.value = job.id;
          box.querySelectorAll('.mc-pick').forEach(other => other.classList.toggle('on', other === tile));
        });
        box.append(tile);
      }
      slPickerLoaded = true;
    } catch {
      box.textContent = 'Não foi possível carregar as gerações da Influencer IA.';
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
    if (activePane === 'lipsync') {
      if (!data.lipsync_ready) {
        problems.push(data.lipsync_missing?.length ? `lip-sync: ${data.lipsync_missing.join(', ')}`
          : 'lip-sync indisponível nesta instalação');
      }
    } else {
      if (!data.ready) {
        problems.push(data.missing?.length ? `Wan Animate incompleto: ${data.missing.join(', ')}`
          : !data.template ? 'o fluxo em CUTCLIPS_WAN_WORKFLOW não pôde ser lido'
          : 'o fluxo Wan usa nós que não existem nesta instalação');
      }
      if ((activePane === 'tryon' || activePane === 'sell') && !data.flux_ready) {
        problems.push(data.flux_missing?.length ? `FLUX incompleto: ${data.flux_missing.join(', ')}`
          : 'FLUX indisponível nesta instalação');
      }
      // O one-shot também entrega o vídeo falando: sem lip-sync pronto, ele nem começa.
      if (activePane === 'sell' && !data.lipsync_ready) {
        problems.push(data.lipsync_missing?.length ? `lip-sync: ${data.lipsync_missing.join(', ')}`
          : 'lip-sync indisponível nesta instalação');
      }
      // A terceira saída (lip-sync sobre a dança) é opcional, mas se o fluxo foi
      // declarado ele precisa estar legível — o job falharia cedo, de qualquer forma.
      if (activePane === 'sell' && data.lipsync_video_configured && !data.lipsync_video_ready) {
        problems.push(data.lipsync_video_missing?.length ? `lip-sync no vídeo: ${data.lipsync_video_missing.join(', ')}`
          : 'o fluxo em CUTCLIPS_LIPSYNC_VIDEO_WORKFLOW não pôde ser lido');
      }
    }
    box.textContent = problems.length
      ? `ComfyUI conectado, mas incompleto — ${problems.join('; ')}.`
      : activePane === 'lipsync' ? 'ComfyUI conectado · fluxo de lip-sync disponível'
      : activePane === 'sell' ? 'ComfyUI conectado · Wan, FLUX e lip-sync prontos'
      : activePane === 'tryon' ? 'ComfyUI conectado · Wan Animate e FLUX prontos (troca de roupa)'
      : 'ComfyUI conectado · Wan Animate local disponível';
    box.className = `mc-status ${problems.length ? 'bad' : 'good'}`;
  }

  const lastStatus = new Map();
  const notifyBox = root.querySelector('#mcNotify');

  function initNotifications() {
    if (!('Notification' in window)) { notifyBox.closest('label').hidden = true; return; }
    // Só liga com permissão já concedida: sem pedido de permissão não solicitado.
    notifyBox.checked = localStorage.getItem('mcNotify') === '1' && Notification.permission === 'granted';
    notifyBox.addEventListener('change', async () => {
      if (notifyBox.checked && Notification.permission !== 'granted') {
        notifyBox.checked = (await Notification.requestPermission()) === 'granted';
      }
      localStorage.setItem('mcNotify', notifyBox.checked ? '1' : '0');
    });
  }

  function watchNotifications(jobs) {
    const enabled = notifyBox.checked && ('Notification' in window) && Notification.permission === 'granted';
    for (const job of jobs) {
      const previous = lastStatus.get(job.id);
      lastStatus.set(job.id, job.status);
      // Só transição de "em andamento" para parado: o primeiro snapshot não avisa.
      if (!enabled || !previous || previous === job.status
          || !ACTIVE.includes(previous) || ACTIVE.includes(job.status)) continue;
      const title = job.status === 'done' ? 'Geração concluída'
        : job.status === 'error' ? 'Geração falhou' : 'Geração cancelada';
      new Notification(title, {body: `${originLabel(job)} · ${stageBase(job)}`, tag: job.id});
    }
  }

  function loadStorage() {
    fetch('/api/motion-control/storage').then(r => r.json()).then(space => {
      const box = root.querySelector('#mcStorage');
      const gb = space.bytes / (1024 ** 3);
      const size = gb >= 1 ? `${gb.toFixed(1).replace('.', ',')} GB`
        : `${Math.max(1, Math.round(space.bytes / (1024 ** 2)))} MB`;
      box.hidden = false;
      box.textContent = gb >= 10
        ? `Histórico ocupando ${size} em ${space.jobs} gerações — vale excluir as antigas (botão Excluir).`
        : `Histórico ocupando ${size} em ${space.jobs} gerações.`;
    }).catch(() => { /* API fora do ar: a linha fica escondida. */ });
  }

  async function refresh() {
    if (location.hash !== '#/motion-control') return;
    try {
      const data = await fetch('/api/motion-control').then(r => r.json());
      history.replaceChildren();
      if (!data.jobs.length) history.textContent = 'Nenhuma geração ainda.';
      for (const job of data.jobs) {
        const row = document.createElement('div'); row.className = 'mc-job';
        const thumb = document.createElement('img');
        thumb.className = 'mc-thumb'; thumb.alt = ''; thumb.loading = 'lazy';
        thumb.src = `/api/motion-control/${job.id}/thumb`;
        thumb.addEventListener('error', () => thumb.remove());
        const label = document.createElement('span');
        label.textContent = `${originLabel(job)} · ${stageLabel(job)}`;
        row.append(thumb, label);
        const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Ver';
        button.addEventListener('click', () => { current = job.id; show(job); }); row.append(button);
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'mc-danger'; remove.textContent = 'Excluir';
        remove.addEventListener('click', async () => {
          if (!confirm(`Excluir “${label.textContent}”? Os arquivos saem do disco.`)) return;
          remove.disabled = true;
          try {
            const response = await fetch(`/api/motion-control/${job.id}`, {method: 'DELETE'});
            const body = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Não foi possível excluir.');
            if (current === job.id) {
              current = null; displayed = '';
              const note = document.createElement('p'); note.textContent = 'Geração excluída.';
              result.replaceChildren(note);
            }
          } catch (exc) { error.textContent = exc.message; error.hidden = false; }
          await refresh();
        });
        row.append(remove);
        history.append(row);
      }
      watchNotifications(data.jobs);
      const selected = data.jobs.find(j => j.id === current);
      if (selected) show(selected);
      // Fila visível: o ComfyUI renderiza um vídeo por vez, e o usuário precisa
      // saber por que o segundo job não começou.
      const running = data.jobs.filter(job => ACTIVE.includes(job.status)).length;
      const queue = root.querySelector('#mcQueue');
      queue.hidden = running === 0;
      queue.textContent = running === 1
        ? '1 geração em andamento · o ComfyUI processa um vídeo por vez.'
        : `${running} gerações em andamento · o ComfyUI processa um vídeo por vez.`;
      loadStorage();
    } catch { /* Keep the previous history while the API is unavailable. */ }
  }

  function actions(job) {
    const wrap = document.createElement('div'); wrap.className = 'mc-actions';
    if (ACTIVE.includes(job.status)) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'mc-mini'; button.textContent = 'Cancelar geração';
      button.addEventListener('click', async () => {
        button.disabled = true; button.textContent = 'Cancelando…';
        try {
          const response = await fetch(`/api/motion-control/${job.id}/cancel`, {method: 'POST'});
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível cancelar.');
        } catch (exc) { error.textContent = exc.message; error.hidden = false; }
        await refresh();
      });
      wrap.append(button);
    } else if (job.status === 'error' || job.status === 'cancelled') {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'mc-mini'; button.textContent = 'Tentar de novo';
      button.addEventListener('click', async () => {
        button.disabled = true; button.textContent = 'Retomando…';
        try {
          const response = await fetch(`/api/motion-control/${job.id}/retry`, {method: 'POST'});
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível tentar de novo.');
        } catch (exc) { error.textContent = exc.message; error.hidden = false; }
        await refresh();
      });
      wrap.append(button);
    }
    return wrap;
  }

  function show(job) {
    const key = `${job.id}:${job.status}:${job.composed || ''}:${job.dance_ready ? 'd' : ''}:${job.done_talk ? 't' : ''}:${job.voiced ? 'o' : ''}:${job.dance_talk ? 'k' : ''}:${job.progress ?? ''}:${job.error || ''}`;
    if (key === displayed) return;
    displayed = key;
    result.replaceChildren(); error.hidden = true;
    if ((job.kind === 'tryon' || job.kind === 'oneshot') && job.composed) {
      const selling = job.kind === 'oneshot';
      const box = document.createElement('div'); box.className = 'mc-composed';
      const img = document.createElement('img');
      img.src = `/api/motion-control/${job.id}/composed`;
      img.alt = selling ? `Influencer já com ${job.product_name || 'o produto'}` : 'Influencer já com a imagem escolhida';
      const caption = document.createElement('span');
      caption.textContent = selling ? `Foto pronta · a influencer com ${job.product_name || 'o produto'}`
        : job.mode === 'product'
          ? (job.status === 'done' ? 'Etapa 1 · a influencer já estava com o produto' : 'Etapa 1 · a influencer está com o produto')
          : (job.status === 'done' ? 'Etapa 1 · a influencer já estava com a roupa' : 'Etapa 1 · a influencer está com a roupa');
      box.append(img, caption); result.append(box);
    }
    if (job.kind === 'oneshot') {
      const output = (src, download, caption) => {
        const wrap = document.createElement('div'); wrap.className = 'mc-sell';
        const player = document.createElement('video');
        player.src = src; player.controls = true; player.preload = 'metadata';
        const label = document.createElement('small'); label.textContent = caption;
        const link = document.createElement('a'); link.href = download; link.textContent = 'Baixar vídeo';
        link.className = 'btn btn-primary'; link.download = `venda-${job.id.slice(0,8)}.mp4`;
        wrap.append(player, label, link); result.append(wrap);
      };
      if (job.status === 'done' || job.dance_ready) {
        output(`/api/motion-control/${job.id}/view`, `/api/motion-control/${job.id}/download`,
               'Vídeo 1 · a influencer dançando com o produto');
      }
      if (job.voiced) {
        output(`/api/motion-control/${job.id}/voiced`, `/api/motion-control/${job.id}/voiced/download`,
               'Vídeo 1 com a voz de venda por cima · voiced.mp4');
      }
      if (job.status === 'done' || job.done_talk) {
        output(`/api/motion-control/${job.id}/talking`, `/api/motion-control/${job.id}/talking/download`,
               'Vídeo 2 · a influencer falando o roteiro de venda');
      }
      if (job.dance_talk) {
        output(`/api/motion-control/${job.id}/dance-talk`, `/api/motion-control/${job.id}/dance-talk/download`,
               'Vídeo 3 · a influencer dançando E falando o roteiro');
      }
      if (job.status !== 'done') {
        const message = document.createElement('p'); message.textContent = stageLabel(job); result.append(message);
      }
      const oneActions = actions(job); if (oneActions.children.length) result.append(oneActions);
      if (job.status === 'error') { error.textContent = job.error || 'A geração falhou.'; error.hidden = false; }
      return;
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
    const bar = actions(job); if (bar.children.length) result.append(bar);
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
      if (tryonForm.elements.refine.checked) body.append('refine', 'true');
      const response = await fetch('/api/motion-control/tryon', {method:'POST', body});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível iniciar a geração.');
      current = data.id; show(data); await refresh();
    } catch (exc) { error.textContent = exc.message; error.hidden = false; }
    finally { button.disabled = false; button.textContent = tcMode === 'product' ? 'Gerar com o produto' : 'Gerar com troca de roupa'; }
  });

  speakForm.addEventListener('submit', async event => {
    event.preventDefault(); error.hidden = true;
    root.querySelector('#spBatchNote').hidden = true;
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
    if (speechMode !== 'audio' && !speech) {
      error.textContent = speechMode === 'reply'
        ? 'Cole pelo menos um comentário para responder.'
        : 'Escreva o que a influencer vai falar.';
      error.hidden = false; return;
    }
    if (speechMode === 'text' && speech.length > 1000) {
      error.textContent = 'O texto pode ter no máximo 1000 caracteres.'; error.hidden = false; return;
    }
    if (speechMode === 'reply' && speech.length > 2000) {
      error.textContent = 'Os comentários podem ter no máximo 2000 caracteres.'; error.hidden = false; return;
    }
    if (speechMode === 'reply' && speech.split('\n').filter(line => line.trim()).length > 10) {
      error.textContent = 'O lote aceita no máximo 10 comentários — cole um por linha.'; error.hidden = false; return;
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
      else if (speechMode === 'reply') {
        body.append('reply_to', speech);
        body.append('voice', speakForm.elements.voice.value);
      } else { body.append('text', speech); body.append('voice', speakForm.elements.voice.value); }
      const response = await fetch('/api/motion-control/lipsync', {method:'POST', body});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível iniciar a geração.');
      current = data.id; show(data); await refresh();
      if (data.batch) {
        // Lote: os N jobs entram no histórico, e este é o primeiro deles.
        const batchNote = root.querySelector('#spBatchNote');
        batchNote.textContent = `${data.batch.length} respostas na fila — uma por comentário colado.`;
        batchNote.hidden = false;
      }
    } catch (exc) { error.textContent = exc.message; error.hidden = false; }
    finally { button.disabled = false; button.textContent = 'Gerar vídeo falado'; }
  });

  sellForm.addEventListener('submit', async event => {
    event.preventDefault(); error.hidden = true;
    const productName = sellForm.elements.product_name.value.trim();
    if (!slVideo.files[0]) { error.textContent = 'Envie o vídeo de dança.'; error.hidden = false; return; }
    if (slPersonSource === 'upload' && !slPerson.files[0]) {
      error.textContent = 'Envie a foto da influencer ou clique em “Usar da Influencer IA”.';
      error.hidden = false; return;
    }
    if (slPersonSource !== 'upload' && !slPersonJob.value) {
      error.textContent = 'Escolha uma imagem gerada na aba Influencer IA.'; error.hidden = false; return;
    }
    if (productName.length < 3) { error.textContent = 'Informe o nome do produto.'; error.hidden = false; return; }
    if (slVideo.files[0].size > 150 * 1024 * 1024 ||
        (slPersonSource === 'upload' && slPerson.files[0].size > 20 * 1024 * 1024) ||
        (slProduct.files[0] && slProduct.files[0].size > 20 * 1024 * 1024)) {
      error.textContent = 'Imagem ou vídeo acima do limite permitido.'; error.hidden = false; return;
    }
    const button = root.querySelector('#slGenerate'); button.disabled = true; button.textContent = 'Enviando…';
    try {
      const body = new FormData();
      body.append('video', slVideo.files[0]);
      if (slPersonSource === 'upload') body.append('person', slPerson.files[0]);
      else body.append('person_job', slPersonJob.value);
      if (slProduct.files[0]) body.append('product', slProduct.files[0]);
      body.append('product_name', productName);
      body.append('benefit', sellForm.elements.benefit.value.trim());
      body.append('cta', sellForm.elements.cta.value.trim());
      body.append('script', sellForm.elements.script.value.trim());
      body.append('mode', slMode);
      if (sellForm.elements.refine.checked) body.append('refine', 'true');
      const price = sellForm.elements.price.value.trim();
      if (price) body.append('price', price);
      const response = await fetch('/api/motion-control/oneshot', {method:'POST', body});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Não foi possível iniciar a geração.');
      current = data.id; show(data); await refresh();
    } catch (exc) { error.textContent = exc.message; error.hidden = false; }
    finally { button.disabled = false; button.textContent = 'Gerar os dois vídeos'; }
  });

  function listen() {
    // SSE empurra a tela na hora em que um status.json muda; o EventSource
    // reconecta sozinho e o heartbeat de 15 s cobre proxy que não repassa o stream.
    let pending = null;
    const bump = () => {
      if (pending) return;
      pending = setTimeout(() => { pending = null; refresh(); }, 250);
    };
    if ('EventSource' in window) {
      const source = new EventSource('/api/motion-control/stream');
      source.onmessage = bump;
      source.onerror = bump;
    }
    setInterval(bump, 15000);
  }

  initNotifications(); config(); refresh(); listen();
  window.addEventListener('hashchange', () => { if (location.hash === '#/motion-control') { config(); refresh(); } });
})();
