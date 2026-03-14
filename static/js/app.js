// ── Thinking toggle (cost table) ──────────────────────────────────────────
function toggleThinking(rowId, btn) {
  const row = document.getElementById(rowId);
  if (!row) return;
  const hidden = row.style.display === 'none';
  row.style.display = hidden ? 'table-row' : 'none';
  btn.classList.toggle('active', hidden);
}

// ── Auto-resize textareas ──────────────────────────────────────────────────
function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}

document.addEventListener('input', function(e) {
  if (e.target.tagName === 'TEXTAREA') autoResize(e.target);
});

// ── Scroll helper ──────────────────────────────────────────────────────────
function scrollToSection(id) {
  const el = document.getElementById(id);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Section lock/unlock ────────────────────────────────────────────────────
function unlockSection(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('stage-locked');
}

// ── Inline status ──────────────────────────────────────────────────────────
function setStatusSpinner(type, msg) {
  const el = document.getElementById(type + '-status');
  if (!el) return;
  el.innerHTML = `<div class="spinner"></div><span>${msg || 'Working\u2026'}</span>`;
}

function setStatusDone(type, msg) {
  const el = document.getElementById(type + '-status');
  if (!el) return;
  el.innerHTML = `<span class="ok-mark">\u2713</span><span>${msg || 'Done'}</span>`;
}

function setStatusError(type, msg) {
  const el = document.getElementById(type + '-status');
  if (!el) return;
  el.innerHTML = `<span class="err-mark">\u2717</span><span class="status-err-text">${msg || 'Error'}</span>`;
}

function clearStatus(type) {
  const el = document.getElementById(type + '-status');
  if (el) el.innerHTML = '';
}

// ── Show/hide revision + action rows ──────────────────────────────────────
function showRevisionRow(type) {
  const el = document.getElementById(type + '-revision');
  if (el) el.style.display = 'flex';
}

function showActionRow(type) {
  const el = document.getElementById(type + '-action-row');
  if (el) el.style.display = 'block';
}

// ── Poll until status ──────────────────────────────────────────────────────
function pollUntilStatus(projectId, targets) {
  return new Promise((resolve, reject) => {
    const interval = setInterval(async () => {
      try {
        const resp = await fetch(`/projects/${projectId}/status/json`);
        const data = await resp.json();
        if (targets.includes(data.status)) {
          clearInterval(interval);
          resolve(data.status);
        } else if (data.status === 'error') {
          clearInterval(interval);
          reject(new Error(data.error || 'Project error'));
        }
      } catch (e) {
        clearInterval(interval);
        reject(e);
      }
    }, 1500);
  });
}


// ── Load artifact into textarea ────────────────────────────────────────────
async function loadArtifact(projectId, type, textareaId) {
  const artifactType = type === 'brief' ? 'research_brief' : 'script';
  try {
    const resp = await fetch(`/projects/${projectId}/artifacts/${artifactType}`);
    if (!resp.ok) return;
    const data = await resp.json();
    const ta = document.getElementById(textareaId);
    if (ta && data.content) {
      ta.value = data.content;
      autoResize(ta);
    }
  } catch (e) {
    console.error('Failed to load artifact:', e);
  }
}

// ── Shared fetch-based SSE reader ─────────────────────────────────────────
async function _readSseStream(url, type, taId, onDone) {
  const ta = document.getElementById(taId);
  if (ta) ta.value = '';

  let resp;
  try {
    resp = await fetch(url);
  } catch (e) {
    setStatusError(type, 'Connection failed: ' + e.message);
    return;
  }
  if (!resp.ok) {
    setStatusError(type, 'HTTP ' + resp.status);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let msg;
        try { msg = JSON.parse(line.slice(6)); } catch { continue; }

        if (msg.type === 'wait') {
          // Another stream is active; poll until ready then load artifact
          setStatusSpinner(type, 'Generation in progress on another tab\u2026');
          const projectId = document.body.dataset.projectId;
          const readyStatus = type === 'brief' ? 'brief_ready' : 'script_ready';
          const taId = type + '-textarea';
          pollUntilStatus(projectId, [readyStatus])
            .then(() => loadArtifact(projectId, type, taId))
            .then(() => onDone())
            .catch(e => setStatusError(type, e.message));
          return;
        } else if (msg.type === 'thinking') {
          setStatusSpinner(type, 'Model is thinking\u2026');
          const td = document.getElementById(type + '-thinking');
          if (td) td.style.display = 'block';
        } else if (msg.type === 'thinking_token') {
          const tc = document.getElementById(type + '-thinking-content');
          if (tc) { tc.textContent += msg.text; tc.scrollTop = tc.scrollHeight; }
        } else if (msg.type === 'content_start') {
          setStatusSpinner(type, type === 'brief' ? 'Writing brief\u2026' : 'Writing script\u2026');
          const td = document.getElementById(type + '-thinking');
          if (td) td.style.display = 'none';
        } else if (msg.type === 'token') {
          if (ta) { ta.value += msg.text; autoResize(ta); }
        } else if (msg.type === 'final_content') {
          if (ta) { ta.value = msg.text; autoResize(ta); }
        } else if (msg.type === 'done') {
          onDone();
          return;
        } else if (msg.type === 'error') {
          setStatusError(type, msg.text);
          return;
        }
      }
    }
  } catch (e) {
    setStatusError(type, 'Stream error: ' + e.message);
  }
}

// ── Open brief stream ──────────────────────────────────────────────────────
async function openBriefStream(projectId) {
  setStatusSpinner('brief', 'Starting\u2026 (first output may take 15\u201330s)');
  scrollToSection('stage-brief');
  await _readSseStream(
    `/projects/${projectId}/stream/brief`,
    'brief',
    'brief-textarea',
    () => {
      setStatusDone('brief', 'Brief ready');
      showRevisionRow('brief');
      showActionRow('brief');
    }
  );
}

// ── Open script stream ─────────────────────────────────────────────────────
async function openScriptStream(projectId) {
  unlockSection('stage-script');
  setStatusSpinner('script', 'Starting\u2026 (first output may take 15\u201330s)');
  scrollToSection('stage-script');
  await _readSseStream(
    `/projects/${projectId}/stream/script`,
    'script',
    'script-textarea',
    () => {
      setStatusDone('script', 'Script ready');
      showRevisionRow('script');
      showActionRow('script');
    }
  );
}

// ── Revision: fetch + ReadableStream ──────────────────────────────────────
async function submitRevision(type) {
  const inputEl = document.getElementById(type + '-revision-input');
  const instruction = inputEl ? inputEl.value.trim() : '';
  if (!instruction) return;

  const projectId = document.body.dataset.projectId;
  const ta = document.getElementById(type + '-textarea');
  if (ta) ta.value = '';
  setStatusSpinner(type, 'Revising\u2026');

  try {
    const resp = await fetch(`/projects/${projectId}/stream/revise-${type}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let msg;
        try { msg = JSON.parse(line.slice(6)); } catch { continue; }

        if (msg.type === 'token') {
          if (ta) { ta.value += msg.text; autoResize(ta); }
        } else if (msg.type === 'final_content') {
          if (ta) { ta.value = msg.text; autoResize(ta); }
        } else if (msg.type === 'done') {
          setStatusDone(type, 'Revision applied');
          if (inputEl) inputEl.value = '';
        } else if (msg.type === 'error') {
          setStatusError(type, msg.text);
        }
      }
    }
  } catch (e) {
    setStatusError(type, e.message);
  }
}

// ── Main init ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async function() {
  const body = document.body;
  const projectId = body.dataset.projectId;
  const status = body.dataset.projectStatus;

  if (!projectId) return; // Not a project page

  switch (status) {
    case 'pending':
      scrollToSection('stage-brief');
      setStatusSpinner('brief', 'Starting up\u2026');
      pollUntilStatus(projectId, ['ingesting', 'brief_pending', 'brief_streaming'])
        .then(s => {
          if (s === 'ingesting') {
            setStatusSpinner('brief', 'Fetching & reading source\u2026');
            return pollUntilStatus(projectId, ['brief_pending', 'brief_streaming']);
          }
        })
        .then(() => openBriefStream(projectId))
        .catch(e => setStatusError('brief', e.message));
      break;

    case 'ingesting':
      scrollToSection('stage-brief');
      setStatusSpinner('brief', 'Fetching & reading source\u2026');
      pollUntilStatus(projectId, ['brief_pending', 'brief_streaming'])
        .then(() => openBriefStream(projectId))
        .catch(e => setStatusError('brief', e.message));
      break;

    case 'brief_pending':
      scrollToSection('stage-brief');
      openBriefStream(projectId);
      break;

    case 'brief_streaming':
      // Stream already in progress — poll until done, then load artifact
      setStatusSpinner('brief', 'Generation in progress\u2026');
      pollUntilStatus(projectId, ['brief_ready'])
        .then(() => loadArtifact(projectId, 'brief', 'brief-textarea'))
        .then(() => { clearStatus('brief'); showRevisionRow('brief'); showActionRow('brief'); scrollToSection('stage-brief'); })
        .catch(e => setStatusError('brief', e.message));
      break;

    case 'brief_ready':
      await loadArtifact(projectId, 'brief', 'brief-textarea');
      clearStatus('brief');
      showRevisionRow('brief');
      showActionRow('brief');
      scrollToSection('stage-brief');
      break;

    case 'scripting':
      await loadArtifact(projectId, 'brief', 'brief-textarea');
      showRevisionRow('brief');
      showActionRow('brief');
      unlockSection('stage-script');
      setStatusSpinner('script', 'Generating outline\u2026');
      scrollToSection('stage-script');
      pollUntilStatus(projectId, ['script_outline', 'script_streaming'])
        .then(() => openScriptStream(projectId))
        .catch(e => setStatusError('script', e.message));
      break;

    case 'script_outline':
      await loadArtifact(projectId, 'brief', 'brief-textarea');
      showRevisionRow('brief');
      showActionRow('brief');
      openScriptStream(projectId);
      break;

    case 'script_streaming':
      await loadArtifact(projectId, 'brief', 'brief-textarea');
      showRevisionRow('brief');
      showActionRow('brief');
      unlockSection('stage-script');
      setStatusSpinner('script', 'Generation in progress\u2026');
      pollUntilStatus(projectId, ['script_ready'])
        .then(() => loadArtifact(projectId, 'script', 'script-textarea'))
        .then(() => { clearStatus('script'); showRevisionRow('script'); showActionRow('script'); scrollToSection('stage-script'); })
        .catch(e => setStatusError('script', e.message));
      break;

    case 'script_ready':
      await loadArtifact(projectId, 'brief', 'brief-textarea');
      await loadArtifact(projectId, 'script', 'script-textarea');
      showRevisionRow('brief');
      showActionRow('brief');
      unlockSection('stage-script');
      showRevisionRow('script');
      showActionRow('script');
      scrollToSection('stage-script');
      break;

    case 'rendering':
      await loadArtifact(projectId, 'brief', 'brief-textarea');
      await loadArtifact(projectId, 'script', 'script-textarea');
      showRevisionRow('brief');
      showActionRow('brief');
      unlockSection('stage-script');
      showRevisionRow('script');
      showActionRow('script');
      unlockSection('stage-audio');
      scrollToSection('stage-audio');
      break;

    case 'done':
      await loadArtifact(projectId, 'brief', 'brief-textarea');
      await loadArtifact(projectId, 'script', 'script-textarea');
      showRevisionRow('brief');
      showActionRow('brief');
      unlockSection('stage-script');
      showRevisionRow('script');
      showActionRow('script');
      unlockSection('stage-audio');
      scrollToSection('stage-audio');
      break;

    case 'error': {
      const errMsg = body.dataset.projectError || 'An error occurred';
      setStatusError('brief', errMsg);
      loadArtifact(projectId, 'brief', 'brief-textarea');
      loadArtifact(projectId, 'script', 'script-textarea');
      break;
    }
  }
});

// ── htmx JSON body extension for PUT requests ─────────────────────────────
htmx.defineExtension && htmx.defineExtension('json-enc', {
  onEvent: function(name, evt) {
    if (name === 'htmx:configRequest') {
      evt.detail.headers['Content-Type'] = 'application/json';
    }
  },
  encodeParameters: function(xhr, parameters, elt) {
    xhr.overrideMimeType('text/json');
    return JSON.stringify(parameters);
  }
});
