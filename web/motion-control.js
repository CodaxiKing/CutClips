(() => {
  const root = document.getElementById('motion-control');
  if (!root) return;
  root.innerHTML = `
    <div class="mc-heading"><span>Vídeo com IA</span><h1>Motion Control</h1>
      <p>Escolha uma foto da pessoa e um vídeo com os movimentos. O Wan Animate local gera um novo vídeo com a pessoa da foto seguindo a atuação do vídeo.</p></div>
    <div class="mc-grid"><form class="mc-card" id="mcForm">
      <h2>Crie seu vídeo</h2>
      <label class="mc-input"><strong>1 · Pessoa da imagem</strong><span class="mc-file" id="mcImageDrop"><span>Escolher imagem PNG, JPG ou WebP</span><input name="image" type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" required></span><small>Prefira uma pessoa visível, com rosto e corpo claros. Até 15 MB.</small></label>
      <label class="mc-input"><strong>2 · Vídeo de movimento</strong><span class="mc-file" id="mcVideoDrop"><span>Escolher vídeo MP4, WebM ou M4V</span><input name="video" type="file" accept=".mp4,.webm,.m4v,video/mp4,video/webm" required></span><small>Uma pessoa em cena, de 2 a 30 segundos. Os primeiros ~5 segundos são animados. Até 150 MB.</small></label>
      <label class="mc-input"><strong>Descrição opcional</strong><textarea name="prompt" maxlength="2500">A pessoa da imagem executa os movimentos do vídeo de referência.</textarea></label>
      <button type="submit" class="mc-generate" id="mcGenerate">Gerar Motion Control</button>
      <p class="mc-note">Geração local com Wan 2.2 Animate, sem créditos. Em GPUs de 8 GB cada vídeo leva vários minutos. O fundo vem da foto; o áudio vem do vídeo de movimento.</p>
    </form><div><div class="mc-card"><h2>Resultado</h2><div class="mc-status" id="mcConfig">Verificando ComfyUI…</div>
      <div class="mc-result" id="mcResult"><p>Envie uma imagem e um vídeo para começar. O resultado aparecerá aqui.</p></div>
      <p class="mc-error" id="mcError" role="alert" hidden></p></div>
      <div class="mc-card mc-history"><h2>Histórico</h2><div class="mc-history-list" id="mcHistory">Nenhuma geração ainda.</div></div>
    </div></div>`;
  const form = root.querySelector('#mcForm');
  const result = root.querySelector('#mcResult');
  const error = root.querySelector('#mcError');
  const history = root.querySelector('#mcHistory');
  let current = null;
  let displayed = '';
  const stateText = {queued:'Na fila', uploading:'Enviando ao ComfyUI', processing:'Gerando vídeo', done:'Concluído', error:'Falhou'};

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

  async function config() {
    const box = root.querySelector('#mcConfig');
    try {
      const data = await fetch('/api/motion-control/config').then(r => r.json());
      box.textContent = data.ready ? 'ComfyUI conectado · fluxo Wan Animate local disponível'
        : !data.connected ? `ComfyUI offline. Inicie o ComfyUI em ${data.url} (o Iniciar CutClips.cmd faz isso quando CUTCLIPS_COMFYUI_DIR está configurado).`
        : data.missing?.length ? `ComfyUI conectado, mas o Wan Animate está incompleto. Faltando: ${data.missing.join(', ')}.`
        : !data.template ? 'ComfyUI conectado, mas o fluxo em CUTCLIPS_WAN_WORKFLOW não pôde ser lido.'
        : 'ComfyUI conectado, mas o fluxo Wan usa nós que não existem nesta instalação. Atualize o ComfyUI ou confira os nós personalizados.';
      box.className = `mc-status ${data.ready ? 'good' : 'bad'}`;
    } catch { box.textContent = 'Não foi possível verificar o ComfyUI.'; box.className = 'mc-status bad'; }
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
        label.textContent = `${job.image_name || 'Imagem'} · ${stateText[job.status] || job.status}`;
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
    const key = `${job.id}:${job.status}:${job.error || ''}`;
    if (key === displayed) return;
    displayed = key;
    result.replaceChildren(); error.hidden = true;
    if (job.status === 'done') {
      const player = document.createElement('video');
      player.src = `/api/motion-control/${job.id}/view`; player.controls = true; player.preload = 'metadata';
      result.append(player);
      const link = document.createElement('a'); link.href = `/api/motion-control/${job.id}/download`; link.textContent = 'Baixar vídeo';
      link.className = 'btn btn-primary'; link.download = `motion-control-${job.id.slice(0,8)}.mp4`;
      result.append(link);
    } else {
      const message = document.createElement('p'); message.textContent = stateText[job.status] || job.status; result.append(message);
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
  config(); refresh(); setInterval(refresh, 4000);
  window.addEventListener('hashchange', () => { if (location.hash === '#/motion-control') { config(); refresh(); } });
})();
