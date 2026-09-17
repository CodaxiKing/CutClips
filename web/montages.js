(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const num = (id, fallback) => { const v = el(id).value; return v === '' ? fallback : Number(v); };
  const seconds = v => `${Number(v).toFixed(1).replace('.', ',')}s`;
  const radio = name => document.querySelector(`input[name="${name}"]:checked`).value;
  const setRadio = (name, value) => { const input = document.querySelector(`input[name="${name}"][value="${value}"]`); if (input) input.checked = true; };
  const LETTERS = 'ABCD';

  // Erros do FastAPI chegam com `loc`; traduz o caminho para o campo que a pessoa vê.
  function problem(result, where, fallback) {
    if (typeof result.detail === 'string') return result.detail;
    return (result.detail || []).map(x => {
      const loc = (x.loc || []).slice(1), prefix = where(loc);
      return (prefix ? prefix + ': ' : '') + String(x.msg).replace(/^Value error, /, '');
    }).join('\n') || fallback;
  }

  async function post(url, body, where, fallback) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(problem(result, where, fallback));
    return result;
  }

  async function submit(button, error, url, body, where, fallback) {
    button.disabled = true; error.textContent = '';
    try {
      const result = await post(url, body, where, fallback);
      location.hash = '#/job/' + result.job_id; window.refresh?.();
    } catch (e) { error.textContent = e.message; } finally { button.disabled = false; }
  }

  /* ------------------------------ música ------------------------------ */
  // Um bloco igual no quiz e na reação: envia o arquivo na hora e guarda só o nome gerado.
  function musicWidget(box) {
    const prefix = box.dataset.music;
    box.innerHTML = `<p class="mg-source-title">♪ Música de fundo <small>opcional</small></p>
      <div class="mg-music-row"><label class="btn btn-outline btn-sm mg-file">Enviar música<input type="file" accept=".mp3,.m4a,.aac,.wav,.ogg,.opus,.flac,audio/*" id="${prefix}MusicFile"></label>
      <span class="mg-music-name" id="${prefix}MusicName">Nenhuma música</span><button type="button" class="btn btn-outline btn-sm" id="${prefix}MusicClear" hidden>Remover</button></div>
      <div class="mg-music-options" id="${prefix}MusicOptions" hidden><label>Volume da música <output id="${prefix}MusicVolumeOut">35%</output><input type="range" min="0.05" max="1" step="0.05" value="0.35" id="${prefix}MusicVolume"></label>
      <label class="t5-checkbox"><input type="checkbox" id="${prefix}Duck" checked> Abaixar a música quando o vídeo tiver som próprio</label></div>
      <p class="t5-help">MP3, M4A, WAV, OGG ou FLAC até 50 MB. A música repete se for curta e termina em fade. Use só faixas que você tem direito de usar.</p>`;
    const state = {music: ''};
    const show = (name, label) => {
      state.music = name;
      el(prefix + 'MusicName').textContent = name ? label : 'Nenhuma música';
      el(prefix + 'MusicClear').hidden = !name;
      el(prefix + 'MusicOptions').hidden = !name;
      box.dispatchEvent(new Event('change', {bubbles: true}));
    };
    el(prefix + 'MusicFile').onchange = async event => {
      const file = event.target.files[0];
      if (!file) return;
      el(prefix + 'MusicName').textContent = 'Enviando…';
      const data = new FormData(); data.append('file', file);
      try {
        const response = await fetch('/api/music', {method: 'POST', body: data});
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || 'Não foi possível enviar a música');
        show(result.music, `${result.filename} · ${seconds(result.duration)}`);
      } catch (e) { show('', ''); el(prefix + 'MusicName').textContent = e.message; }
      event.target.value = '';
    };
    el(prefix + 'MusicClear').onclick = () => show('', '');
    el(prefix + 'MusicVolume').oninput = () => { el(prefix + 'MusicVolumeOut').textContent = Math.round(el(prefix + 'MusicVolume').value * 100) + '%'; };
    return {
      get: () => ({music: state.music, music_volume: Number(el(prefix + 'MusicVolume').value), duck: el(prefix + 'Duck').checked}),
      set(spec) {
        el(prefix + 'MusicVolume').value = spec.music_volume ?? 0.35; el(prefix + 'MusicVolume').oninput();
        el(prefix + 'Duck').checked = spec.duck !== false;
        show(spec.music || '', 'Música do projeto anterior');
      },
    };
  }

  /* ------------------------------- quiz ------------------------------- */
  const MIN_Q = 3, MAX_Q = 10;
  const list = el('qzQuestions');
  const quizMusic = musicWidget(document.querySelector('[data-music="qz"]'));
  let aiReady = false;

  const style = () => radio('qzStyle');

  function questions() {
    return [...list.children].map(li => ({
      question: li.querySelector('[data-q]').value.trim(), answer: li.querySelector('[data-a]').value.trim(),
      wrong: [...li.querySelectorAll('[data-w]')].map(input => input.value.trim()).filter(Boolean),
    }));
  }

  function setQuestions(items) {
    list.innerHTML = '';
    items.slice(0, MAX_Q).forEach(item => addRow(item));
    while (list.children.length < MIN_Q) addRow();
    quizChanged();
  }

  function addRow(item = {}) {
    const li = document.createElement('li');
    li.className = 'mg-question';
    li.innerHTML = `<span class="t5-number" aria-hidden="true"></span><div class="mg-question-body"><div class="mg-question-fields">
      <label>Pergunta<input data-q required maxlength="120" placeholder="Qual é a capital da Austrália?"></label>
      <label>Resposta certa<input data-a required maxlength="60" placeholder="Camberra"></label></div>
      <div class="mg-wrong"><span>Alternativas erradas</span>${[0, 1, 2].map(i => `<input data-w maxlength="32" aria-label="Alternativa errada ${i + 1}" placeholder="${['Sydney', 'Melbourne', 'Brisbane'][i]}">`).join('')}</div></div>
      <div class="mg-row-actions"><button type="button" class="btn btn-outline btn-sm" data-swap title="Trocar por outra do banco">Trocar</button><button type="button" class="btn btn-outline btn-sm" data-remove aria-label="Remover pergunta">✕</button></div>`;
    li.querySelector('[data-q]').value = item.question || '';
    li.querySelector('[data-a]').value = item.answer || '';
    li.querySelectorAll('[data-w]').forEach((input, i) => { input.value = item.wrong?.[i] || ''; });
    list.append(li);
  }

  async function suggestions(count, exclude) {
    const query = new URLSearchParams({theme: el('qzTheme').value, count});
    exclude.filter(Boolean).slice(0, 20).forEach(q => query.append('exclude', q));
    const response = await fetch('/api/quiz/suggestions?' + query);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Não foi possível sortear perguntas');
    return result.questions;
  }

  /* ------------------------- pasta de fundos ------------------------- */
  const bg = {videos: [], file: '', touched: false, loaded: false};
  const bgMode = () => radio('qzBgMode');
  const thumbUrl = name => `/api/quiz/backgrounds/${encodeURIComponent(name)}/thumbnail`;
  const usableVideos = () => bg.videos.filter(v => !v.error);

  function bgChanged(fromUser) {
    const mode = bgMode();
    document.querySelectorAll('.mg-bg-pane').forEach(pane => { pane.hidden = pane.dataset.bg !== mode; });
    el('qzBgAudioRow').hidden = mode === 'color';
    // Cada tipo tem seu padrão de som; a pessoa ainda pode mudar depois.
    if (fromUser) el('qzBgAudio').checked = mode === 'url';
  }

  function renderBackgrounds() {
    const videos = bg.videos;
    if (bg.file && !videos.some(v => v.name === bg.file && !v.error)) bg.file = '';
    const tile = (name, inner, extra = '') => `<button type="button" role="radio" class="mg-bg-tile ${extra}" aria-checked="${bg.file === name}" data-bg-file="${esc(name)}">${inner}</button>`;
    el('qzBgGrid').innerHTML = !videos.length
      ? `<div class="mg-bg-empty"><strong>A pasta está vazia</strong><span>Coloque vídeos .mp4, .mov ou .webm nela. Verticais ficam melhores; horizontais são cortados no centro.</span></div>`
      : tile('', `<span class="mg-bg-auto" aria-hidden="true">⇄</span><b>Automático</b><small>${usableVideos().length} ${usableVideos().length === 1 ? 'vídeo' : 'vídeos'} em rodízio</small>`, 'auto') +
        videos.map(v => v.error
          ? `<div class="mg-bg-tile broken" title="${esc(v.error)}"><span class="mg-bg-auto">!</span><b>${esc(v.name)}</b><small>Não foi possível ler</small></div>`
          : tile(v.name, `<img src="${thumbUrl(v.name)}" alt="" loading="lazy"><span class="mg-bg-time">${seconds(v.duration)}</span><b>${esc(v.name)}</b>`)).join('');
    quizPreview();
  }

  async function loadBackgrounds() {
    try {
      const result = await fetch('/api/quiz/backgrounds').then(r => r.json());
      const before = bg.videos.map(v => v.name + v.size + v.modified).join('|');
      bg.videos = result.videos;
      el('qzFolderPath').textContent = result.folder;
      el('qzFolderPath').title = result.folder;
      el('qzFolderOpen').hidden = !result.can_open;
      // Primeira visita: com vídeos na pasta, o quiz já nasce usando a pasta.
      if (!bg.loaded && !bg.touched && usableVideos().length) { setRadio('qzBgMode', 'folder'); bgChanged(true); }
      const first = !bg.loaded;
      bg.loaded = true;
      if (first || before !== bg.videos.map(v => v.name + v.size + v.modified).join('|')) renderBackgrounds();
    } catch { el('qzFolderPath').textContent = 'Não foi possível ler a pasta de fundos'; }
  }

  el('qzBgGrid').addEventListener('click', event => {
    const tile = event.target.closest('[data-bg-file]');
    if (!tile) return;
    bg.file = tile.dataset.bgFile;
    el('qzBgGrid').querySelectorAll('[data-bg-file]').forEach(t => t.setAttribute('aria-checked', String(t === tile)));
    quizChanged();
  });
  document.querySelectorAll('input[name="qzBgMode"]').forEach(input => input.addEventListener('change', () => { bg.touched = true; bgChanged(true); }));
  el('qzFolderRefresh').onclick = loadBackgrounds;
  el('qzFolderOpen').onclick = async () => {
    const response = await fetch('/api/quiz/backgrounds/open', {method: 'POST'}).catch(() => null);
    if (!response?.ok) el('qzError').textContent = (await response?.json().catch(() => ({})))?.detail || 'Não foi possível abrir a pasta';
  };
  // Quem solta arquivos no Explorer volta para a janela: a lista se atualiza sozinha.
  window.addEventListener('focus', () => { if (!el('quiz').hidden) loadBackgrounds(); });
  window.addEventListener('hashchange', () => { if (location.hash === '#/quiz') loadBackgrounds(); });

  /* --------------------------- revelação --------------------------- */
  const reveal = {sound: 'acerto', effect: 'confetti', file: '', localUrl: ''};
  let revealAudio = null;

  function revealChanged() {
    el('qzSoundChips').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.sound === reveal.sound)));
    el('qzEffectChips').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.effect === reveal.effect)));
    el('qzSoundCustom').hidden = reveal.sound !== 'custom';
    el('qzSoundVolumeRow').hidden = reveal.sound === 'none';
    el('qzSoundPlay').disabled = reveal.sound === 'none' || (reveal.sound === 'custom' && !reveal.localUrl);
    el('qzSoundVolumeOut').textContent = Math.round(el('qzSoundVolume').value * 100) + '%';
    renderSummary();
  }

  const SOUND_NAMES = {none: 'sem som', acerto: 'Acerto', ding: 'Ding', tada: 'Tadã', pop: 'Pop', custom: 'som próprio'};
  const EFFECT_NAMES = {none: 'sem animação', pulse: 'pulso', confetti: 'confete'};

  // Resumo do passo 05: o que vai sair, antes de mandar para a fila.
  function renderSummary() {
    if (!el('qzSummary')) return;
    const spec = quizData(), filled = spec.questions.filter(q => q.question && q.answer).length;
    const total = spec.questions.length * (spec.suspense + spec.reveal);
    const backdrop = spec.background === 'folder' ? (bg.file ? `pasta · ${bg.file}` : 'pasta · automático em loop')
      : spec.background === 'url' ? 'vídeo por link' : 'cor lisa';
    const rows = [
      ['Perguntas', `${filled} de ${spec.questions.length} preenchidas · ${spec.style === 'choices' ? 'alternativas A/B/C/D' : 'resposta aberta'}`, filled < spec.questions.length],
      ['Duração', `${seconds(total)} · ${seconds(spec.suspense)} para pensar + ${seconds(spec.reveal)} de resposta`],
      ['Fundo', backdrop],
      ['Áudio', [spec.music ? 'música de fundo' : '', spec.background_audio ? 'som do fundo' : ''].filter(Boolean).join(' + ') || 'sem música'],
      ['Revelação', `${SOUND_NAMES[spec.reveal_sound]} · ${EFFECT_NAMES[spec.reveal_effect]}`],
    ];
    el('qzSummary').innerHTML = rows.map(([label, value, warn]) =>
      `<li class="${warn ? 'warn' : ''}"><span>${label}</span><strong>${esc(value)}</strong></li>`).join('');
  }

  function playSound() {
    if (reveal.sound === 'none') return;
    const src = reveal.sound === 'custom' ? reveal.localUrl : `/api/quiz/sounds/${reveal.sound}`;
    if (!src) return;
    revealAudio?.pause();
    revealAudio = new Audio(src);
    revealAudio.volume = Number(el('qzSoundVolume').value);
    revealAudio.play().catch(() => {});
  }

  // Mesma coreografia do vídeo, em CSS: clarão, "pop" da resposta, onda e confete.
  function playReveal() {
    el('qzPreviewMoment').value = 'answer';
    quizPreview();
    const phone = el('qzPhone'), fx = el('qzFx');
    phone.classList.remove('mg-revealing');
    fx.innerHTML = '';
    void phone.offsetWidth;  // reinicia as animações CSS
    phone.dataset.effect = reveal.effect;
    const choices = style() === 'choices', index = Number(el('qzPreviewIndex').value || 0);
    // Centro da resposta no canvas 1080×1920, igual ao roteiro ASS.
    fx.style.top = (choices ? (700 + (index % 4) * 160 + 65) : 1385) / 1920 * 100 + '%';
    fx.classList.toggle('card', choices);
    if (reveal.effect === 'confetti') {
      const colours = ['#ffdd45', '#80ff80', '#ff6fb5', '#5ec8ff', '#ffffff'];
      for (let i = 0; i < 26; i++) {
        const angle = (i % 3 ? 200 + Math.random() * 140 : Math.random() * 360) * Math.PI / 180;
        const distance = 24 + Math.random() * 30;  // em % da largura do celular
        const piece = document.createElement('span');
        piece.style.setProperty('--x', Math.cos(angle) * distance + 'cqw');
        piece.style.setProperty('--y', Math.sin(angle) * distance + 8 + Math.random() * 12 + 'cqw');
        piece.style.setProperty('--r', (Math.random() > .5 ? 540 : -540) + 'deg');
        piece.style.setProperty('--d', 750 + Math.random() * 300 + 'ms');
        piece.style.background = colours[i % colours.length];
        fx.append(piece);
      }
    }
    if (reveal.effect !== 'none') phone.classList.add('mg-revealing');
    playSound();
  }

  el('qzSoundChips').addEventListener('click', event => {
    const chip = event.target.closest('[data-sound]');
    if (!chip) return;
    reveal.sound = chip.dataset.sound;
    revealChanged();
    if (reveal.sound !== 'custom') playSound();
  });
  el('qzEffectChips').addEventListener('click', event => {
    const chip = event.target.closest('[data-effect]');
    if (!chip) return;
    reveal.effect = chip.dataset.effect;
    revealChanged();
    playReveal();
  });
  el('qzSoundPlay').onclick = playSound;
  el('qzRevealPlay').onclick = playReveal;
  el('qzSoundVolume').oninput = revealChanged;
  el('qzSoundFile').onchange = async event => {
    const file = event.target.files[0];
    if (!file) return;
    el('qzSoundName').textContent = 'Enviando…';
    const data = new FormData(); data.append('file', file);
    try {
      const response = await fetch('/api/music?kind=sound', {method: 'POST', body: data});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Não foi possível enviar o som');
      if (reveal.localUrl) URL.revokeObjectURL(reveal.localUrl);
      reveal.file = result.music;
      reveal.localUrl = URL.createObjectURL(file);
      el('qzSoundName').textContent = `${result.filename} · ${seconds(result.duration)}`;
      revealChanged(); playSound();
    } catch (e) { reveal.file = ''; el('qzSoundName').textContent = e.message; revealChanged(); }
    event.target.value = '';
  };

  function quizSettings() {
    const mode = bgMode();
    return {
      style: style(), suspense: num('qzSuspense', 3), reveal: num('qzReveal', 2), countdown: el('qzCountdown').checked,
      background: mode, background_file: mode === 'folder' ? bg.file : '',
      background_url: mode === 'url' ? el('qzBackground').value.trim() : '', background_start: num('qzBackgroundStart', 0),
      background_color: '0x' + el('qzColor').value.slice(1).toUpperCase(),
      background_audio: mode !== 'color' && el('qzBgAudio').checked, ...quizMusic.get(),
      reveal_sound: reveal.sound, reveal_sound_file: reveal.sound === 'custom' ? reveal.file : '',
      reveal_sound_volume: Number(el('qzSoundVolume').value), reveal_effect: reveal.effect,
    };
  }
  const quizData = () => ({headline: el('qzHeadline').value.trim(), questions: questions(), ...quizSettings()});

  function quizChanged() {
    const rows = [...list.children], spec = quizData(), choices = spec.style === 'choices';
    rows.forEach((li, i) => {
      li.querySelector('.t5-number').textContent = i + 1;
      li.querySelector('[data-remove]').disabled = rows.length <= MIN_Q;
      li.querySelector('[data-a]').maxLength = choices ? 32 : 60;
      li.querySelectorAll('[data-w]').forEach(input => { input.required = choices; });
    });
    list.classList.toggle('mg-choices', choices);
    el('qzAdd').disabled = rows.length >= MAX_Q;
    const block = spec.suspense + spec.reveal;
    timingPreview(spec);
    el('qzDuration').textContent = `${rows.length} perguntas × ${seconds(block)} = ${seconds(rows.length * block)} · 1080 × 1920 · 30 fps`;
    const index = el('qzPreviewIndex'), keep = Math.min(Number(index.value || 0), rows.length - 1);
    index.innerHTML = rows.map((_, i) => `<option value="${i}">${i + 1}ª pergunta</option>`).join('');
    index.value = keep;
    batchSummary();
    renderSummary();
    quizPreview();
  }

  // Os números seguem a regra do render: um por segundo inteiro, e a fração que
  // sobra fica somada ao primeiro número em vez de virar um número cortado.
  function timingPreview(spec) {
    for (const input of [el('qzSuspense'), el('qzReveal')]) {
      document.querySelectorAll(`[data-chips="${input.id}"] button`).forEach(chip => {
        chip.setAttribute('aria-pressed', String(Number(chip.dataset.value) === Number(input.value)));
      });
    }
    const suspense = spec.suspense, ticks = Math.max(1, Math.floor(suspense)), extra = suspense - ticks;
    const numbers = spec.countdown
      ? Array.from({length: ticks}, (_, i) => `<b>${ticks - i}</b>`).join('<i>›</i>')
      : `<span class="mg-countdown-quiet">sem números · ${seconds(suspense)} de silêncio</span>`;
    el('qzCountdownPreview').innerHTML = `<span class="mg-countdown-label">Na tela</span>${numbers}<i>›</i><em>Resposta</em>` +
      (spec.countdown && extra > 0 ? `<small>o ${ticks} fica ${seconds(1 + extra)} na tela</small>` : '');
  }

  function stepValue(input, value) {
    const min = Number(input.min), max = Number(input.max);
    input.value = String(Math.min(max, Math.max(min, Math.round(value * 2) / 2)));
    input.dispatchEvent(new Event('input', {bubbles: true}));
  }
  document.querySelectorAll('[data-chips] button').forEach(chip => chip.addEventListener('click', () =>
    stepValue(el(chip.parentElement.dataset.chips), Number(chip.dataset.value))));
  document.querySelectorAll('[data-step]').forEach(button => button.addEventListener('click', () => {
    const input = el(button.dataset.step);
    stepValue(input, Number(input.value || input.min) + Number(button.dataset.delta));
  }));

  function quizPreview() {
    const spec = quizData(), i = Number(el('qzPreviewIndex').value || 0), item = spec.questions[i] || {wrong: []};
    const answering = el('qzPreviewMoment').value === 'answer', choices = spec.style === 'choices';
    const phone = el('qzPhone');
    const shown = spec.background === 'folder' ? (bg.file || usableVideos()[0]?.name) : '';
    phone.style.background = spec.background === 'color' ? el('qzColor').value : '';
    phone.style.backgroundImage = shown ? `linear-gradient(#0006,#0006),url("${thumbUrl(shown)}")` : '';
    phone.classList.toggle('mg-has-video', spec.background !== 'color' && !shown);
    phone.classList.toggle('mg-choices', choices);
    el('qzPreviewHead').textContent = spec.headline || 'Sua frase aparece aqui';
    el('qzPreviewAsk').textContent = item.question || 'A pergunta aparece aqui';
    el('qzPreviewTick').textContent = !answering && spec.countdown ? Math.max(1, Math.floor(spec.suspense)) : '';
    el('qzPreviewAnswer').textContent = !choices && answering ? (item.answer || 'Resposta') : '';
    // A posição real da certa é sorteada na montagem; a prévia só mostra o efeito.
    const slot = i % 4, wrong = [0, 1, 2].map(k => item.wrong[k] || `Alternativa errada ${k + 1}`);
    const options = [...wrong.slice(0, slot), item.answer || 'Resposta certa', ...wrong.slice(slot)];
    el('qzPreviewCards').innerHTML = choices ? options.map((text, k) =>
      `<div class="mg-card ${answering ? (k === slot ? 'right' : 'dim') : ''}"><b>${LETTERS[k]})</b><span>${esc(text)}</span></div>`).join('') : '';
    el('qzPreviewStep').textContent = `${i + 1} de ${spec.questions.length}`;
    el('qzPreviewNote').textContent = choices
      ? 'Simulação. Na montagem, a letra da resposta certa é sorteada e distribuída entre A, B, C e D.'
      : spec.background === 'folder' && !bg.file ? 'Simulação. No automático, o fundo mostrado é só um exemplo da pasta.' : 'Simulação de texto e posição.';
  }

  list.addEventListener('click', async event => {
    const li = event.target.closest('li');
    if (!li) return;
    if (event.target.closest('[data-remove]') && list.children.length > MIN_Q) { li.remove(); quizChanged(); }
    const swap = event.target.closest('[data-swap]');
    if (swap) {
      swap.disabled = true; el('qzError').textContent = '';
      try {
        const [item] = await suggestions(1, questions().map(q => q.question));
        li.querySelector('[data-q]').value = item.question; li.querySelector('[data-a]').value = item.answer;
        li.querySelectorAll('[data-w]').forEach((input, i) => { input.value = item.wrong[i] || ''; });
        quizChanged();
      } catch (e) { el('qzError').textContent = e.message; } finally { swap.disabled = false; }
    }
  });

  const hasContent = () => questions().some(q => q.question || q.answer);
  el('qzAdd').onclick = () => { if (list.children.length < MAX_Q) { addRow(); quizChanged(); list.lastElementChild.querySelector('input').focus(); } };
  el('qzDraw').onclick = async () => {
    if (hasContent() && !confirm('Substituir as perguntas atuais pelas sorteadas?')) return;
    el('qzDraw').disabled = true; el('qzError').textContent = '';
    try { setQuestions(await suggestions(Number(el('qzCount').value), [])); }
    catch (e) { el('qzError').textContent = e.message; } finally { el('qzDraw').disabled = false; }
  };
  el('qzGenerate').onclick = async () => {
    const topic = el('qzTopic').value.trim();
    if (topic.length < 2) { el('qzTopic').focus(); el('qzAiStatus').textContent = 'Escreva o tema do quiz para a IA.'; return; }
    if (hasContent() && !confirm('Substituir as perguntas atuais pelas geradas?')) return;
    const button = el('qzGenerate');
    button.disabled = true; el('qzError').textContent = '';
    button.textContent = 'Gerando…';
    el('qzAiStatus').textContent = 'A IA escreve as perguntas e depois revisa cada resposta. Pode levar até um minuto.';
    try {
      const result = await post('/api/quiz/generate', {topic, count: Number(el('qzCount').value), style: style(),
        difficulty: el('qzDifficulty').value}, () => '', 'Não foi possível gerar as perguntas');
      setQuestions(result.questions);
      if (!el('qzHeadline').value.trim()) el('qzHeadline').value = `Você acerta ${result.questions.length} sobre ${topic}?`.slice(0, 60);
      quizChanged();
      el('qzAiStatus').textContent = [`${result.questions.length} perguntas geradas e revisadas por ${result.model}. Confira o gabarito antes de publicar.`, ...result.warnings].join(' ');
    } catch (e) { el('qzAiStatus').textContent = e.message; }
    finally { button.disabled = !aiReady; button.textContent = 'Gerar perguntas'; }
  };

  el('quizForm').addEventListener('input', quizChanged);
  el('quizForm').addEventListener('change', quizChanged);
  el('qzPreviewIndex').onchange = quizPreview;
  el('qzPreviewMoment').onchange = quizPreview;
  el('quizForm').onsubmit = async event => {
    event.preventDefault();
    const side = el('qzCreateSide');
    side.disabled = true;
    await submit(el('qzCreate'), el('qzError'), '/api/quiz', quizData(), loc =>
      loc[0] === 'questions' && Number.isInteger(loc[1]) ? `Pergunta ${loc[1] + 1}` :
      loc[0] === 'questions' ? 'Perguntas' : '', 'Não foi possível criar o quiz');
    side.disabled = false;
    // Quem clicou no botão ao lado da prévia pode estar longe da mensagem de erro.
    if (el('qzError').textContent) el('qzError').scrollIntoView({behavior: 'smooth', block: 'center'});
  };

  /* ------------------------------- lote ------------------------------- */
  const today = new Date(); today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
  el('qbStart').value = today.toISOString().slice(0, 10);
  const shortDate = iso => new Date(iso + 'T12:00:00').toLocaleDateString('pt-BR', {weekday: 'short', day: '2-digit', month: '2-digit'});

  function batchSource() {
    const source = el('qbSource').value;
    document.querySelectorAll('#batchForm [data-source]').forEach(label => { label.hidden = label.dataset.source !== source; });
    batchSummary();
  }

  function batchSummary() {
    const videos = num('qbVideos', 0), per = num('qbPerVideo', 0), every = Number(el('qbEvery').value);
    if (!videos || !per || !el('qbStart').value) { el('qbSummary').textContent = ''; return; }
    const last = new Date(el('qbStart').value + 'T12:00:00'); last.setDate(last.getDate() + (videos - 1) * every);
    const end = last.toISOString().slice(0, 10);
    el('qbSummary').textContent = `${videos} vídeos · ${videos * per} perguntas sem repetir · ${shortDate(el('qbStart').value)} → ${shortDate(end)} · formato: ${style() === 'choices' ? 'A/B/C/D' : 'resposta aberta'}`;
  }

  const STATUS = {queued: 'Na fila', running: 'Montando', done: 'Pronto para revisar', error: 'Erro'};
  async function loadBatches() {
    try {
      const {batches} = await fetch('/api/quiz/batches').then(r => r.json());
      el('qbList').innerHTML = batches.length ? batches.map(batch => {
        const done = batch.items.filter(item => item.status === 'done').length;
        return `<div class="mg-batch-card"><div class="mg-batch-head"><strong>${esc(batch.label)}</strong><span>${batch.source === 'ai' ? '✦ IA' : 'Banco'} · ${done}/${batch.items.length} prontos</span></div>
          ${batch.items.map(item => `<a class="mg-batch-row ${esc(item.status)}" href="#/job/${esc(item.job_id)}"><time>${item.publish_on ? esc(shortDate(item.publish_on)) : ''}</time><span>${esc(item.title)}</span><em title="${esc(item.error || '')}">${item.status === 'running' ? `${esc(item.stage)} · ${Math.round(item.progress * 100)}%` : STATUS[item.status] || esc(item.status)}</em></a>`).join('')}</div>`;
      }).join('') : '<p class="t5-help">Nenhum lote ainda.</p>';
    } catch { /* a lista volta na próxima atualização */ }
  }

  el('batchForm').addEventListener('input', batchSummary);
  el('batchForm').addEventListener('change', batchSummary);
  el('qbSource').addEventListener('change', batchSource);
  el('batchForm').onsubmit = async event => {
    event.preventDefault();
    const button = el('qbCreate'); button.disabled = true; el('qbError').textContent = '';
    try {
      const result = await post('/api/quiz/batch', {
        base: quizSettings(), headline: el('qbHeadline').value.trim(), videos: num('qbVideos', 0),
        per_video: num('qbPerVideo', 0), source: el('qbSource').value, theme: el('qbTheme').value,
        topic: el('qbTopic').value.trim(), difficulty: el('qbDifficulty').value,
        start_date: el('qbStart').value, every_days: Number(el('qbEvery').value),
      }, loc => ({videos: 'Vídeos', per_video: 'Perguntas por vídeo', headline: 'Frase', start_date: 'Data'})[loc[0]] || '', 'Não foi possível criar o lote');
      await loadBatches(); window.refresh?.();
      el('qbList').scrollIntoView({behavior: 'smooth', block: 'start'});
      button.textContent = `Lote criado (${result.jobs.length} vídeos)`;
      setTimeout(() => { button.textContent = 'Criar lote'; }, 3000);
    } catch (e) { el('qbError').textContent = e.message; } finally { button.disabled = false; }
  };

  fetch('/api/quiz/themes').then(r => r.json()).then(({themes}) => {
    const options = themes.map(t => `<option value="${esc(t.id)}">${esc(t.label)} (${t.size})</option>`).join('');
    el('qzTheme').insertAdjacentHTML('beforeend', options);
    el('qbTheme').insertAdjacentHTML('beforeend', options);
  }).catch(() => {});
  fetch('/api/quiz/ai').then(r => r.json()).then(status => {
    aiReady = status.ready;
    el('qzGenerate').disabled = !aiReady;
    el('qzAiStatus').textContent = aiReady ? `Usando ${status.model} (${status.provider}). As respostas passam por revisão automática.` : status.message;
    if (!aiReady) { el('qbSource').value = 'bank'; batchSource(); }
  }).catch(() => { el('qzAiStatus').textContent = 'Não foi possível verificar a IA.'; });
  setQuestions([]);
  revealChanged();
  bgChanged(false); loadBackgrounds();
  batchSource(); loadBatches();
  setInterval(() => { if (!el('quiz').hidden) loadBatches(); }, 4000);

  function fillQuiz(spec) {
    el('qzHeadline').value = spec.headline || '';
    setRadio('qzStyle', spec.style || 'open');
    el('qzSuspense').value = spec.suspense ?? 3; el('qzReveal').value = spec.reveal ?? 2;
    el('qzCountdown').checked = spec.countdown !== false;
    el('qzBackground').value = spec.background_url || ''; el('qzBackgroundStart').value = spec.background_start || 0;
    el('qzColor').value = '#' + String(spec.background_color || '0x12161C').slice(2).toLowerCase();
    setRadio('qzBgMode', spec.background || (spec.background_url ? 'url' : 'color')); bgChanged(false);
    bg.touched = true; bg.file = spec.background_file || ''; renderBackgrounds();
    el('qzBgAudio').checked = spec.background_audio ?? !!spec.background_url;
    quizMusic.set(spec);
    reveal.sound = spec.reveal_sound || 'acerto'; reveal.effect = spec.reveal_effect || 'confetti';
    reveal.file = spec.reveal_sound_file || ''; reveal.localUrl = '';
    el('qzSoundName').textContent = reveal.file ? 'Som do projeto anterior' : 'Nenhum arquivo · até 30 segundos';
    el('qzSoundVolume').value = spec.reveal_sound_volume ?? 0.8;
    revealChanged();
    setQuestions(spec.questions || []);
  }

  /* ------------------------------ reação ------------------------------ */
  const reactionMusic = musicWidget(document.querySelector('[data-music="rc"]'));
  const NAMES = {stacked: {top: ['Vídeo de cima', 'O original, que está sendo reagido', 'Original'], bottom: ['Vídeo de baixo', 'Quem reage', '@quemreage']},
                 side: {top: ['Vídeo da esquerda', 'O antes', 'Antes'], bottom: ['Vídeo da direita', 'O depois', 'Depois']}};
  el('rcSides').innerHTML = ['top', 'bottom'].map(key => `<fieldset class="t5-entry"><legend id="rc_${key}_legend"></legend><span class="t5-number" aria-hidden="true" id="rc_${key}_icon"></span><div class="t5-entry-fields">
    <p class="mg-side-title"><span id="rc_${key}_title"></span> <small id="rc_${key}_hint"></small></p>
    <label>Link do vídeo<input id="rc_${key}_url" type="url" required maxlength="600" placeholder="https://…"></label>
    <label>Rótulo na tela (opcional)<input id="rc_${key}_label" maxlength="32"></label>
    <div class="t5-trim"><label>Começar em (segundos)<input id="rc_${key}_start" type="number" min="0" max="3600" step="0.1" value="0"></label>
    <label>Volume <output id="rc_${key}_volume_out">100%</output><input id="rc_${key}_volume" type="range" min="0" max="2" step="0.05" value="1"></label></div>
  </div></fieldset>`).join('');

  const side = key => ({url: el(`rc_${key}_url`).value.trim(), label: el(`rc_${key}_label`).value.trim(),
    start: num(`rc_${key}_start`, 0), volume: Number(el(`rc_${key}_volume`).value)});
  function setSide(key, value = {}) {
    el(`rc_${key}_url`).value = value.url || ''; el(`rc_${key}_label`).value = value.label || '';
    el(`rc_${key}_start`).value = value.start || 0; el(`rc_${key}_volume`).value = value.volume ?? 1;
  }
  function reactionData() {
    return {headline: el('rcHeadline').value.trim(), layout: radio('rcLayout'), top: side('top'), bottom: side('bottom'),
      duration: num('rcDuration', null), fit: el('rcFit').value, normalize_audio: el('rcNormalize').checked,
      captions: el('rcCaptions').value, ...reactionMusic.get()};
  }

  let lastLayout = 'stacked';
  function layoutChanged() {
    const layout = radio('rcLayout');
    if (layout !== lastLayout) {
      // Rótulos padrão acompanham o arranjo, mas nunca apagam o que a pessoa escreveu.
      for (const key of ['top', 'bottom']) {
        const input = el(`rc_${key}_label`);
        if (layout === 'side' && !input.value) input.value = NAMES.side[key][2];
        else if (layout === 'stacked' && input.value === NAMES.side[key][2]) input.value = '';
      }
      lastLayout = layout;
    }
    const names = NAMES[layout];
    for (const key of ['top', 'bottom']) {
      el(`rc_${key}_legend`).textContent = names[key][0];
      el(`rc_${key}_title`).textContent = names[key][0];
      el(`rc_${key}_hint`).textContent = names[key][1];
      el(`rc_${key}_icon`).textContent = layout === 'side' ? (key === 'top' ? '←' : '→') : (key === 'top' ? '↑' : '↓');
      el(`rc_${key}_label`).placeholder = names[key][2];
      el('rcCaptions').querySelector(`option[value="${key}"]`).textContent = `Legendar o ${names[key][0].toLowerCase()}`;
    }
    el('rcPreviewFirst').textContent = names.top[0];
    el('rcPreviewSecond').textContent = names.bottom[0];
    el('rcSwap').textContent = layout === 'side' ? '⇄ Inverter esquerda e direita' : '⇅ Inverter cima e baixo';
  }

  function reactionPreview() {
    layoutChanged();
    const spec = reactionData();
    const phone = el('rcPhone');
    phone.classList.toggle('mg-side', spec.layout === 'side');
    phone.classList.toggle('mg-has-title', !!spec.headline);
    el('rcPreviewTitle').textContent = spec.headline;
    el('rcPreviewTagTop').textContent = spec.top.label;
    el('rcPreviewTagBottom').textContent = spec.bottom.label;
    el('rcPreviewSpeech').hidden = spec.captions === 'none';
    el('rcCaptionsHelp').hidden = spec.captions === 'none';
    for (const key of ['top', 'bottom']) el(`rc_${key}_volume_out`).textContent = Math.round(spec[key].volume * 100) + '%';
  }
  el('rcSwap').onclick = () => { const top = side('top'), bottom = side('bottom'); setSide('top', bottom); setSide('bottom', top); reactionPreview(); };
  el('reactionForm').addEventListener('input', reactionPreview);
  el('reactionForm').addEventListener('change', reactionPreview);
  el('reactionForm').onsubmit = event => {
    event.preventDefault();
    const names = NAMES[radio('rcLayout')];
    submit(el('rcCreate'), el('rcError'), '/api/reaction', reactionData(), loc =>
      loc[0] === 'top' ? names.top[0] : loc[0] === 'bottom' ? names.bottom[0] : loc[0] === 'duration' ? 'Duração' : '',
      'Não foi possível criar o vídeo');
  };
  reactionPreview();

  function fillReaction(spec) {
    el('rcHeadline').value = spec.headline || ''; el('rcDuration').value = spec.duration ?? '';
    setRadio('rcLayout', spec.layout || 'stacked'); lastLayout = spec.layout || 'stacked';
    el('rcFit').value = spec.fit || 'cover'; el('rcNormalize').checked = spec.normalize_audio !== false;
    el('rcCaptions').value = spec.captions || 'none';
    setSide('top', spec.top); setSide('bottom', spec.bottom);
    reactionMusic.set(spec); reactionPreview();
  }

  /* --------------------------- página do projeto --------------------------- */
  const KIND = {
    quiz: {eyebrow: 'QUIZ', steps: 'Perguntas → fundo → texto e contagem → verificação', ready: 'QUIZ PRONTO', hash: '#/quiz'},
    reaction: {eyebrow: 'REAÇÃO', steps: 'Download dos dois vídeos → normalização → junção', ready: 'VÍDEO PRONTO', hash: '#/reaction'},
  };

  function quizPanel(job) {
    const m = job.manifest, block = m.timing.block, generated = m.generated;
    const batch = m.batch ? `<p class="mg-batch-note">Lote “${esc(m.batch.label)}” · vídeo ${m.batch.index} de ${m.batch.total}${m.batch.publish_on ? ` · publicar em ${esc(shortDate(m.batch.publish_on))}` : ''}</p>` : '';
    const back = m.background || {};
    const backdrop = back.mode === 'folder' ? `<p class="mg-batch-note mg-dim">Fundo da pasta: ${esc(back.file)}${back.start ? ` · a partir de ${seconds(back.start)}` : ''} · em loop</p>` : '';
    return `${batch}${backdrop}<h2>Gabarito</h2><p class="t5-help">${m.style === 'choices' ? 'Alternativas A/B/C/D. ' : ''}Cada pergunta ocupa ${seconds(block)}: ${seconds(m.timing.suspense)} de suspense e ${seconds(m.timing.reveal)} com a resposta.${generated ? ` Perguntas geradas por ${esc(generated.model)} sobre “${esc(generated.topic)}”.` : ''}${m.music ? ' Com música de fundo.' : ''}</p>` +
      m.questions.map((q, i) => `<div class="t5-timeline-row"><b>${i + 1}</b><div><strong>${esc(q.question)}</strong><p>Resposta em ${seconds(i * block + m.timing.suspense)} · <span class="mg-answer-text">${q.correct ? `${esc(q.correct)}) ` : ''}${esc(q.answer)}</span>${q.options ? ` <span class="mg-options-text">(${q.options.map((o, k) => `${LETTERS[k]}) ${esc(o)}`).join(' · ')})</span>` : ''}</p></div></div>`).join('');
  }

  function reactionPanel(job) {
    const m = job.manifest, sides = m.sides, layout = m.layout || 'stacked', names = NAMES[layout];
    const extras = [m.captions && m.captions !== 'none' ? `legenda da fala do ${names[m.captions][0].toLowerCase()}` : '', m.music ? 'música de fundo' : ''].filter(Boolean);
    return `<h2>${layout === 'side' ? 'Antes e depois' : 'As duas metades'}</h2><p class="t5-help">Áudio das duas fontes somado${job.settings.reaction.normalize_audio ? ' e normalizado' : ''}${extras.length ? ' · ' + extras.join(' · ') : ''}.</p>` +
      ['top', 'bottom'].map(key => {
        const s = sides[key], icon = layout === 'side' ? (key === 'top' ? '←' : '→') : (key === 'top' ? '↑' : '↓');
        return `<div class="t5-timeline-row"><b>${icon}</b><div><strong>${esc(s.label || names[key][0])}</strong><p>${names[key][0]} · começa em ${seconds(s.start)} · volume ${Math.round(s.volume * 100)}% · <a href="${esc(s.url)}" target="_blank" rel="noopener">original ↗</a></p></div></div>`;
      }).join('');
  }

  window.Montages = {renderProject(job, box, back) {
    const kind = job.settings.kind, info = KIND[kind];
    if (job.status !== 'done') {
      box.dataset.key = '';
      const pct = Math.round((job.progress || 0) * 100);
      box.innerHTML = `${back}<div class="state-card"><p class="t5-eyebrow">MONTAGEM · ${info.eyebrow}</p><h1>${esc(job.title)}</h1><p class="state-sub">${job.status === 'queued' ? 'Na fila. A montagem começa assim que o worker estiver livre.' : esc(job.stage)}</p><div class="big-progress"><i style="width:${pct}%"></i></div><p>${pct}% · ${info.steps}</p><p class="note">O processamento continua mesmo se você fechar esta página.</p></div>`;
      return;
    }
    const key = `${job.id}:${kind}:done:${job.finished_at}`;
    if (box.dataset.key === key) return;
    box.dataset.key = key;
    const clip = job.manifest.clips[0], file = name => `/api/jobs/${job.id}/clips/${encodeURIComponent(name)}`;
    const warnings = (clip.warnings || []).map(w => `<p class="mg-warning">${esc(w)}</p>`).join('');
    box.innerHTML = `${back}<header class="t5-result-header"><div><p class="t5-eyebrow">${info.ready}</p><h1>${esc(job.title)}</h1><p class="t5-help">${esc(job.manifest.source_resolution || '1080 × 1920')} · ${Number(clip.actual_duration).toFixed(1).replace('.', ',')} segundos</p></div><div class="t5-result-actions"><a href="${file(clip.file)}" download class="btn btn-primary">Baixar MP4</a><a href="/api/jobs/${job.id}/download" class="btn btn-outline">Pacote + ${kind === 'quiz' ? 'gabarito' : 'créditos'}</a><button class="btn btn-outline" id="mgReuse">Usar configuração</button><button class="btn btn-outline" data-delete-project>Excluir</button></div></header><div class="t5-result-grid"><video src="${file(clip.file)}" poster="${file(clip.thumbnail)}" controls playsinline preload="metadata"></video><div class="t5-panel">${warnings}${kind === 'quiz' ? quizPanel(job) : reactionPanel(job)}<p class="t5-help">Para mudar algo, use a configuração e gere uma nova versão como outro projeto.</p></div></div>`;
    el('mgReuse').onclick = () => {
      if (kind === 'quiz') { fillQuiz(job.settings.quiz); el('qzError').textContent = ''; }
      else { fillReaction(job.settings.reaction); el('rcError').textContent = ''; }
      location.hash = info.hash;
    };
  }};
})();
