// Callers tab: the AI people who make calls. Uses $, el, api, btn, ask, tiles, statBlocks, compare, showTab, open from other files.
const K = {list: [], gone: [], voices: {}, id: null, view: 'stats', dirty: false, busy: false, seq: 0, audio: null};
const PRESETS = [['Warm', 'Warm and sharp. Friendly, curious about people, quick to put them at ease.'], ['Direct', 'Calm and direct. Gets to the point, gives straight answers, never hurries anyone.'],
  ['Playful', 'Light and playful. Easy humour, relaxed, never silly when someone is serious or upset.'], ['Expert', 'Thoughtful and knowledgeable. Explains simply, like a trusted adviser, with no jargon.'], ['Formal', 'Polite and formal. Measured, respectful, precise, with little small talk.']];
const cap = s => s[0].toUpperCase() + s.slice(1);

async function leaveCaller() {
  if (K.busy) return false;
  if (!K.dirty) return true;
  if (!await ask('Discard your changes?', 'This caller has changes you have not saved.', 'Discard changes', true)) return false;
  K.dirty = false; return true;
}
addEventListener('beforeunload', e => { if (K.dirty) e.preventDefault(); });

async function drawCallers() {
  for (const p of await api('/products')) PC[p.id] = p.color;
  const d = await api('/callers'); K.list = d.active; K.gone = d.deleted; K.voices = d.voices;
  if (!K.list.some(c => c.id === K.id)) { K.id = K.list[0] ? K.list[0].id : null; K.view = 'stats'; }
  drawCallerList(); await openCaller(K.id, K.view === 'new' ? 'stats' : K.view);
}
function drawCallerList() {
  if (!$('klist')) { $('callersView').replaceChildren(el('section'), el('section')); const [a, b] = $('callersView').children; a.id = 'klist'; b.id = 'kbody'; b.className = 'pbody'; }
  const add = btn('New caller', async () => { if (await leaveCaller()) openCaller(null, 'new'); }, 'primary'); add.style.cssText = 'width:100%;margin-bottom:12px';
  const out = [el('h2', '', 'Callers'), add, ...listSearch(K, K.list.length, 'Search callers', drawCallerList), ...K.list.filter(c => !K.q || (c.name + ' ' + c.personality).toLowerCase().includes(K.q.toLowerCase())).map(c => { const b = btn('', async () => { if (await leaveCaller()) openCaller(c.id, 'stats'); }, 'lead' + (c.id === K.id && K.view !== 'new' ? ' on' : ''));
    b.append(el('div', '', c.name), el('div', 'mute', cap(c.gender) + ', ' + cap(c.voice) + ' voice'), el('div', 'mute ptags', ...(c.used.length ? c.used.map(u => tag(u.id, u.company + ' ' + u.share + '%')) : ['Not assigned']))); return b; })];
  if (K.gone.length) { const d = el('details', '', el('summary', '', 'Deleted (' + K.gone.length + ')')); for (const c of K.gone) d.append(el('div', 'row', el('span', 'mute', c.name), btn('Restore', async () => { try { await api('/callers/' + c.id + '/restore', {}); await drawCallers(); } catch (e) { kerr(e); } }))); out.push(d); }
  $('klist').replaceChildren(...out);
}
const kerr = e => { const n = $('kerr'); if (n) { n.textContent = e ? e.message || e : ''; if (e) n.scrollIntoView({block: 'nearest'}); } };
function kmsg() { const e = el('p', 'err'); e.id = 'kerr'; e.setAttribute('role', 'alert'); return e; }

async function openCaller(id, view) {
  const seq = ++K.seq; K.id = id; K.view = view; K.dirty = false; if (K.audio) K.audio.pause(); drawCallerList();
  if (view === 'new') return callerForm({name: '', gender: 'female', voice: 'priya', pace: 1.05, personality: PRESETS[0][1]});
  const c = K.list.find(x => x.id === id); if (!c) return;
  if (view === 'edit') return callerForm(c);
  try {
    const s = await api('/callers/' + id + '/stats'); if (seq !== K.seq) return;
    $('kbody').replaceChildren(callerHead(c), el('div', 'pscroll', kmsg(), el('p', '', c.personality), tiles(s),
      compare('By product', 'Product', s.by_product, async r => { await showTab('P'); open(r.id, 'stats'); }), el('div', 'cols', ...statBlocks(s))));
  } catch (e) { if (seq === K.seq) $('kbody').replaceChildren(el('div', 'pscroll', el('p', 'err', e.message))); }
}
function callerHead(c) {
  const tab = (v, t) => { const b = btn(t, async () => { if (K.view !== v && await leaveCaller()) openCaller(c.id, v); }); if (K.view === v) b.setAttribute('aria-current', 'page'); return b; };
  const nav = el('nav', '', tab('stats', 'Overview'), tab('edit', 'Edit')); nav.style.marginRight = 0;
  return el('div', 'phead', el('div', 'grow', el('h2', '', c.name), el('div', 'mute', cap(c.gender) + ', ' + cap(c.voice) + ' voice')), nav, btn('Delete', async () => {
    if (K.busy || !await ask('Delete ' + c.name + '?', 'Past calls and results are kept. You can restore this caller later.', 'Delete caller', true)) return;
    try { await api('/callers/' + c.id, undefined, 'DELETE'); K.dirty = false; K.id = null; await drawCallers(); } catch (e) { kerr(e); }
  }));
}
function callerForm(src) {
  const f = {...src}, isNew = !src.id, status = el('span', '', isNew ? 'Not saved yet' : ''), greet = el('p', 'greet');
  const touch = () => { K.dirty = true; status.textContent = 'Unsaved changes'; status.className = ''; hi(); };
  const hi = () => greet.textContent = `Hi Rahul! This is ${f.name.trim() || '…'}, an AI assistant from your company. Is this an okay time?`;
  const name = el('input'); name.value = f.name; name.maxLength = 30; name.oninput = () => { f.name = name.value; touch(); };
  const sel = (opts, v, set) => { const s = el('select'); opts.forEach(([k, t]) => { const o = el('option', '', t); o.value = k; s.append(o); }); s.value = v; s.onchange = () => { set(s.value); touch(); }; return s; };
  const vbox = el('label', '', 'Voice'), voices = () => { if (!K.voices[f.gender].includes(f.voice)) f.voice = K.voices[f.gender][0]; vbox.replaceChildren('Voice', sel(K.voices[f.gender].map(v => [v, cap(v)]), f.voice, v => f.voice = v)); };
  const gender = sel([['female', 'Female'], ['male', 'Male']], f.gender, v => { f.gender = v; voices(); });
  const pace = el('input'), pl = el('span', 'mute'); pace.type = 'range'; pace.min = .8; pace.max = 1.3; pace.step = .05; pace.value = f.pace;
  const showPace = () => pl.textContent = ' ' + (f.pace < .95 ? 'Slower' : f.pace > 1.12 ? 'Faster' : 'Natural') + ' (' + f.pace.toFixed(2) + ')'; pace.oninput = () => { f.pace = +pace.value; showPace(); touch(); };
  const per = el('textarea'); per.rows = 4; per.maxLength = 400; per.value = f.personality; per.oninput = () => { f.personality = per.value; touch(); };
  const hear = lang => btn(lang === 'hi' ? 'Hear in Hindi' : 'Hear in English', async ev => {
    const b = ev.target, t = b.textContent; b.disabled = true; b.textContent = 'Loading…'; kerr('');
    try {
      const r = await fetch('/api/callers/preview', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({voice: f.voice, pace: f.pace, language: lang})});
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Could not play that voice');
      if (K.audio) K.audio.pause(); K.audio = new Audio(URL.createObjectURL(await r.blob())); await K.audio.play();
    } catch (e) { kerr(e); }
    b.disabled = false; b.textContent = t;
  });
  const saveB = btn(isNew ? 'Create caller' : 'Save changes', async () => {
    if (K.busy) return; K.busy = true; saveB.disabled = true; kerr('');
    try {
      status.textContent = 'Checking for safety…';
      const c = await safeSave(confirm => isNew ? api('/callers', {...f, confirm}) : api('/callers/' + f.id, {...f, confirm}, 'PUT'));
      K.dirty = false; K.busy = false; K.id = c.id; K.view = 'edit'; await drawCallers();
      const s = document.querySelector('#kbody .save span'); if (s) { s.textContent = 'Saved. New calls use this version.'; s.className = 'ok'; }
    } catch (e) { status.textContent = 'Not saved'; kerr(e); }
    K.busy = false; saveB.disabled = false;
  }, 'primary');
  const used = (src.used || []).length ? src.used.map(u => tag(u.id, u.company + ' ' + u.share + '%')) : ['Not assigned yet. Choose callers in Products, Edit.'];
  $('kbody').replaceChildren(isNew ? el('div', 'phead', el('div', 'grow', el('h2', '', 'New caller'))) : callerHead(src), el('div', 'pscroll', el('div', 'form', kmsg(),
    el('fieldset', '', el('legend', '', 'Who they are'), el('div', 'g2', el('label', '', 'Name', name), el('label', '', 'Gender', gender)), greet, el('p', 'help', 'Every caller says they are an AI assistant.')),
    el('fieldset', '', el('legend', '', 'Voice'), el('div', 'g2', vbox, el('label', '', 'Pace', pl, pace)), el('div', 'acts', hear('en'), hear('hi')), el('p', 'help', 'A new voice or pace uses a little Sarvam credit the first time you hear it.')),
    el('fieldset', '', el('legend', '', 'Personality'), el('p', 'help', 'How they come across. It changes the tone, never the facts or the rules.'), el('div', 'acts', ...PRESETS.map(([t, v]) => btn(t, () => { per.value = f.personality = v; touch(); }))), per),
    isNew ? null : el('p', 'help ptags', 'Used by:', ...used))),
    el('div', 'save', status, btn('Cancel', async () => { if (await leaveCaller()) openCaller(isNew ? (K.list[0] || {}).id : f.id, 'stats'); }), saveB));
  voices(); showPace(); hi();
}
