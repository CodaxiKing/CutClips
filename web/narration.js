(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  if (!el('narrationForm')) return;

  const FORMATS = { vertical: '1080 × 1920 · 9:16', feed: '1080 × 1350 · 4:5', square: '1080 × 1080 · 1:1', horizontal: '1920 × 1080 · 16:9' };

  /* As vozes vêm do Windows: oferecer uma lista fixa levaria a escolher voz que
     não existe na máquina e narrar com outra sem avisar. */
  async function loadVoices() {
    const select = el('nrVoice');
    try {
      const answer = await fetch('/api/narration/voices');
      const data = await answer.json();
      const profile = data.influencer || {};
      let voices = data.voices || [];
      // A voz salva como a da influencer vira o padrão do campo, mesmo que o
      // Windows tenha parado de listá-la (a máquina pode ter mudado).
      if (profile.voice && !voices.includes(profile.voice)) voices = [profile.voice, ...voices];
      if (!voices.length) {
        select.innerHTML = '<option value="">Nenhuma voz encontrada no sistema</option>';
        el('nrVoiceHelp').hidden = false;
        return;
      }
      const chosen = profile.voice || data.default || voices[0];
      select.innerHTML = voices.map(v => `<option value="${esc(v)}"${v === chosen ? ' selected' : ''}>${esc(v)}${profile.voice === v ? ' · voz da influencer' : ''}</option>`).join('');
      if (profile.saved_at) el('nrRate').value = profile.rate || 0;
      showProfile(profile);
    } catch {
      select.innerHTML = '<option value="">Não foi possível listar as vozes</option>';
      el('nrVoiceHelp').hidden = false;
    }
  }

  function showProfile(profile) {
    el('nrVoiceProfile').textContent = profile.voice
      ? `Voz atual da influencer: ${profile.voice} (andamento ${profile.rate || 0}). A narração e o lip-sync usam ela por padrão.`
      : '';
  }

  el('nrSaveVoice').addEventListener('click', async () => {
    el('nrVoiceProfile').textContent = '';
    try {
      const answer = await fetch('/api/narration/voice', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({voice: el('nrVoice').value, rate: Number(el('nrRate').value) || 0}),
      });
      const body = await answer.json();
      if (!answer.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Não foi possível salvar a voz');
      showProfile(body);
    } catch (error) {
      el('nrVoiceProfile').textContent = error.message;
    }
  });

  function data() {
    return {
      subject: el('nrSubject').value.trim(),
      script: el('nrScript').value.trim(),
      sentences: Number(el('nrSentences').value),
      voice: el('nrVoice').value,
      voice_rate: Number(el('nrRate').value) || 0,
      footage: el('nrFootage').value,
      footage_terms: el('nrTerms').value.trim(),
      background_color: el('nrColor').value.replace('#', '0x'),
      orientation: el('nrOrientation').value,
      captions: el('nrCaptions').checked,
    };
  }

  function preview() {
    const spec = data();
    el('nrFormatNote').textContent = `MP4 ${FORMATS[spec.orientation]} · 30 fps · voz do sistema`;
    el('nrPhone').className = 'mg-phone mg-' + spec.orientation;
    el('nrPreviewTitle').textContent = spec.subject || 'Seu tema aparece aqui';
    // O roteiro escrito à mão desliga a IA: a interface precisa dizer isso.
    const manual = !!spec.script;
    el('nrSentencesRow').hidden = manual;
    el('nrAiNote').hidden = manual;
    el('nrManualNote').hidden = !manual;
    el('nrTermsRow').hidden = spec.footage === 'color' || spec.footage === 'folder';
    el('nrColorRow').hidden = spec.footage !== 'color';
    const linha = manual ? spec.script.split(/(?<=[.!?])\s+/)[0] : 'A primeira frase é o gancho.';
    el('nrPreviewLine').textContent = linha.slice(0, 90) || 'A primeira frase é o gancho.';
    el('nrCreate').disabled = spec.subject.length < 3;
  }

  el('narrationForm').addEventListener('input', preview);
  el('narrationForm').addEventListener('change', preview);

  el('narrationForm').onsubmit = async event => {
    event.preventDefault();
    el('nrCreate').disabled = true;
    el('nrError').textContent = '';
    try {
      const answer = await fetch('/api/narration', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data()),
      });
      const result = await answer.json();
      if (!answer.ok) {
        throw new Error(typeof result.detail === 'string' ? result.detail
          : (result.detail || []).map(x => x.msg).join('\n') || 'Não foi possível criar o vídeo');
      }
      location.hash = '#/job/' + result.job_id;
      window.refresh?.();
    } catch (error) {
      el('nrError').textContent = error.message;
    } finally {
      el('nrCreate').disabled = false;
    }
  };

  loadVoices();
  preview();

  window.Narration = {
    renderProject(job, box, back) {
      if (job.status !== 'done') {
        const pct = Math.round((job.progress || 0) * 100);
        box.innerHTML = `${back}<div class="state-card"><p class="t5-eyebrow">VÍDEO NARRADO</p>
          <h1>${esc(job.title)}</h1>
          <p class="state-sub">${job.status === 'queued' ? 'Na fila.' : esc(job.stage)}</p>
          <div class="big-progress"><i style="width:${pct}%"></i></div>
          <p>${pct}% · roteiro → voz → imagens → legenda</p></div>`;
        return;
      }
      const key = job.id + ':narration:' + job.finished_at;
      if (box.dataset.key === key) return;
      box.dataset.key = key;
      const clip = job.manifest.clips[0];
      const url = `/api/jobs/${job.id}/clips/${encodeURIComponent(clip.file)}`;
      const roteiro = (job.manifest.script || []).map(s => `<li>${esc(s)}</li>`).join('');
      box.innerHTML = `${back}<header class="t5-result-header">
        <div><p class="t5-eyebrow">VÍDEO NARRADO PRONTO</p><h1>${esc(clip.title)}</h1>
        <p class="t5-help">${esc(job.manifest.source_resolution)} · ${Number(clip.actual_duration).toFixed(1)} segundos · ${job.manifest.footage_count || 0} trecho(s) de imagem</p></div>
        <div class="t5-result-actions"><a href="${url}" download class="btn btn-primary">Baixar MP4</a>
        <a href="/api/jobs/${job.id}/download" class="btn btn-outline">Pacote</a>
        <button class="btn btn-outline" data-delete-project>Excluir</button></div></header>
        <div class="t5-result-grid"><video src="${url}" poster="/api/jobs/${job.id}/clips/${encodeURIComponent(clip.thumbnail)}" controls playsinline preload="metadata"></video>
        <div class="t5-panel"><h2>Roteiro narrado</h2><ol class="nr-script">${roteiro}</ol>
        ${(clip.warnings || []).map(w => `<p class="t5-help">${esc(w)}</p>`).join('')}</div></div>`;
    },
  };
})();
