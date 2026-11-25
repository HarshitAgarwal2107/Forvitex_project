// Products tab: list, performance, creator, editor. Uses $, el, api, S, pick, loadProducts, refresh from index.html.
const P = {f: {}, calls: {q: '', sort: 'newest', page: 0}, callers: [], list: [], gone: [], id: null, view: 'stats', form: null, dirty: false, stats: null, busy: false, seq: 0};
const DAY = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const hour = h => (h % 12 || 12) + (h < 12 ? ' AM' : ' PM');
const btn = (text, fn, cls) => { const b = el('button', cls || '', text); b.type = 'button'; b.onclick = fn; return b; };
const pct = v => v == null ? 'n/a' : Math.round(v * 100) + '%';
const when = s => new Date(s).toLocaleString('en-IN', {weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Kolkata'});

function ask(title, text, yes, danger) {
  return new Promise(done => {
    const d = $('dlg'), answer = v => { d.close(); done(v); };
    d.replaceChildren(el('h2', '', title), el('div', '', text), el('div', 'row', btn('Keep as is', () => answer(false)), btn(yes, () => answer(true), danger ? 'stop' : 'primary')));
    d.oncancel = () => done(false);
    d.showModal();
  });
}
async function leaveOk() {
  if (P.busy) return false;
  if (!P.dirty) return true;
  if (!await ask('Discard your changes?', 'This product has changes you have not saved.', 'Discard changes', true)) return false;
  P.dirty = false;
  return true;
}
addEventListener('beforeunload', e => { if (P.dirty) e.preventDefault(); });

async function showTab(which) {
  if (which !== 'P' && !await leaveOk()) return;
  if (which !== 'K' && !await leaveCaller()) return;
  for (const [k, view, tab] of [['L', 'leadsView', 'tabL'], ['C', 'calendarView', 'tabC'], ['P', 'productsView', 'tabP'], ['K', 'callersView', 'tabK']]) {
    $(view).hidden = k !== which;
    k === which ? $(tab).setAttribute('aria-current', 'page') : $(tab).removeAttribute('aria-current');
  }
  if (which === 'P') { await loadList(); if (!P.id && P.view !== 'new' && P.list[0]) P.id = P.list[0].id; await open(P.id, P.view === 'new' ? 'new' : P.view); }
  else if (which === 'C') await drawCalendar();
  else if (which === 'K') await drawCallers();
  else { await loadProducts(); await refresh(); }
}
$('tabL').onclick = () => showTab('L'); $('tabP').onclick = () => showTab('P'); $('tabC').onclick = () => showTab('C').catch(fail); $('tabK').onclick = () => showTab('K').catch(fail);

async function loadList() {
  let k; [P.list, P.gone, k] = await Promise.all([api('/products'), api('/products?deleted=true'), api('/callers')]); P.callers = k.active; for (const p of [...P.list, ...P.gone]) PC[p.id] = p.color;
  drawList();
}
// short lists need no search box
function listSearch(state, n, label, draw) {
  if (n < 6) { state.q = ''; return []; }
  const q = el('input'); q.type = 'search'; q.placeholder = label; q.setAttribute('aria-label', label); q.value = state.q || '';
  q.oninput = () => { state.q = q.value.trim(); draw(); const x = document.querySelector('input[aria-label="' + label + '"]'); x.value = q.value; x.focus(); };
  return [q];
}
function drawList() {
  if (!$('plist')) { $('productsView').replaceChildren(el('section', 'plist'), el('section')); const [a, b] = $('productsView').children; a.id = 'plist'; b.id = 'pbody'; }
  const add = btn('New product', async () => { if (await leaveOk()) open(null, 'new'); }, 'primary');
  add.style.cssText = 'width:100%;margin-bottom:12px';
  const rows = P.list.filter(p => !P.q || (p.company + ' ' + p.topic).toLowerCase().includes(P.q.toLowerCase())).map(p => {
    const b = btn('', async () => { if (await leaveOk()) open(p.id, 'stats'); }, 'lead' + (p.id === P.id && P.view !== 'new' ? ' on' : ''));
    b.append(el('div', '', tag(p.id, p.company)), el('div', 'mute', p.topic), el('div', 'mute', p.leads + (p.leads === 1 ? ' lead, ' : ' leads, ') + p.upcoming + ' upcoming'));
    return b;
  });
  const out = [el('h2', '', 'Products'), add, ...listSearch(P, P.list.length, 'Search products', drawList), ...(rows.length ? rows : [el('p', 'mute', P.list.length ? 'No products match.' : 'No products yet. Create one to start calling leads.')])];
  if (P.gone.length) {
    const d = el('details', '', el('summary', '', 'Deleted (' + P.gone.length + ')'));
    for (const p of P.gone) d.append(el('div', 'row', el('span', 'mute', tag(p.id, p.company)), btn('Restore', async () => { try { await api('/products/' + p.id + '/restore', undefined, 'POST'); await loadList(); if (await leaveOk()) open(p.id, 'stats'); } catch (e) { problem(e); } })));
    out.push(d);
  }
  $('plist').replaceChildren(...out);
}
function problem(e) { const n = $('perr'); if (n) { n.textContent = e ? (e.message || e) : ''; if (e) n.scrollIntoView({block: 'nearest'}); } }

async function open(id, view) {
  const seq = ++P.seq;
  if (id !== P.id) { P.f = {}; P.calls = {q: '', sort: 'newest', page: 0}; }
  P.id = id; P.view = view; P.dirty = false; drawList();
  if (!id && view !== 'new') return $('pbody').replaceChildren(el('div', 'pscroll', el('p', 'mute', 'Choose a product.')));
  if (view === 'new') return drawNew();
  try {
    const [p, st] = await Promise.all([api('/products/' + id), view === 'stats' ? api('/products/' + id + '/stats?' + qs()) : view === 'calls' ? api('/products/' + id + '/calls?' + qs(P.calls)) : null]);
    if (seq !== P.seq) return;
    P.form = p; P.stats = st;
    view === 'stats' ? drawStats(p, st) : view === 'calls' ? drawCalls(p, st) : drawForm(p);
  } catch (e) { if (seq === P.seq) $('pbody').replaceChildren(el('div', 'pscroll', el('p', 'err', e.message))); }
}
function head(p) {
  const tab = (v, t) => { const b = btn(t, async () => { if (P.view !== v && await leaveOk()) open(p.id, v); }); if (P.view === v) b.setAttribute('aria-current', 'page'); return b; };
  const nav = el('nav', '', tab('stats', 'Overview'), tab('calls', 'Calls'), tab('edit', 'Edit')); nav.style.marginRight = 0;
  return el('div', 'phead', el('div', 'grow', el('h2', '', tag(p.id, p.company)), el('div', 'mute', p.topic)), nav, btn('Delete', () => remove(p)));
}
async function remove(p) {
  if (P.busy) return;
  const n = (P.list.find(x => x.id === p.id) || {}).leads || 0;
  if (!await ask('Delete ' + p.company + '?', (n ? 'Its ' + n + (n === 1 ? ' lead' : ' leads') + ' and call history will be hidden. ' : '') + 'You can restore it later from the Deleted list.', 'Delete product', true)) return;
  try { await api('/products/' + p.id, undefined, 'DELETE'); P.dirty = false; P.id = null; await loadList(); open(P.list[0] ? P.list[0].id : null, 'stats'); } catch (e) { problem(e); }
}

// ---------- performance ----------
const qs = extra => new URLSearchParams(Object.entries({...P.f, ...(extra || {})}).filter(([, v]) => v !== '' && v != null)).toString();
const RESULT = {booked: 'Booked', moved: 'Moved booking', cancelled: 'Cancelled booking', opted_out: 'Asked not to be called', handover: 'Asked for a person', talked: 'Talked, nothing booked', no_answer: 'Did not speak'};
// one filter row for Overview and Calls; choices stay when switching between them
function filterBar(p, callersHere) {
  const set = (k, v) => { if (v) P.f[k] = v; else delete P.f[k]; P.calls.page = 0; open(p.id, P.view); };
  const pickF = (k, label, opts) => { const s = el('select'); s.setAttribute('aria-label', label); [['', label], ...opts].forEach(([v, t]) => { const o = el('option', '', t); o.value = v; s.append(o); }); s.value = P.f[k] || ''; if (P.f[k]) s.className = 'on'; s.onchange = () => set(k, s.value); return s; };
  const n = Object.keys(P.f).length;
  return el('div', 'fbar', pickF('caller', 'All callers', callersHere.map(c => [c.id, c.name])), pickF('days', 'All time', [['1', 'Today'], ['7', 'Last 7 days'], ['30', 'Last 30 days'], ['90', 'Last 90 days']]),
    pickF('mode', 'Voice and text', [['voice', 'Voice calls'], ['text', 'Text chats']]), pickF('language', 'All languages', [['en', 'English'], ['hi', 'Hindi/Hinglish']]),
    pickF('result', 'All results', Object.entries(RESULT)), pickF('band', 'All lead quality', [['Hot', 'Hot'], ['Warm', 'Warm'], ['Cold', 'Cold']]), n ? btn('Clear filters', () => { P.f = {}; P.calls.page = 0; open(p.id, P.view); }) : '');
}
function bars(rows) {
  const top = Math.max(1, ...rows.map(r => r[1]));
  return rows.map(([l, n]) => { const i = el('i', n ? '' : 'z'); i.style.width = 100 * n / top + '%'; const r = el('div', 'hb', el('span', '', l), el('div', '', i), el('b', '', String(n))); r.title = l + ': ' + n; return r; });
}
function block(title, ...kids) { return el('div', '', el('h3', '', title), ...kids); }
const mood = v => v == null ? 'n/a' : v > .2 ? 'Positive' : v < -.2 ? 'Negative' : 'Neutral';
const secs = v => v == null ? 'n/a' : v.toFixed(1) + ' s';
const table = (cols, rows) => { const t = el('table', '', el('tr', '', ...cols.map(c => el('th', '', c)))); rows.forEach(r => t.append(el('tr', '', ...r.map(c => el('td', '', c))))); return t; };
function tiles(s) {
  return el('div', 'tiles', ...[[s.calls, 'calls'], [pct(s.booked_rate), 'of contacted leads booked'], [pct(s.attendance), 'attended their booking'], [s.avg_score == null ? 'n/a' : Math.round(s.avg_score), 'average score after a call'],
    [mood(s.avg_mood), 'average customer mood'], [secs(s.median_reply), 'median voice reply'],
    [s.median_call_seconds == null ? 'n/a' : Math.floor(s.median_call_seconds / 60) + 'm ' + Math.round(s.median_call_seconds % 60) + 's', 'median voice call']].map(([v, l]) => el('div', '', el('b', '', String(v)), el('span', '', l))));
}
// one row per caller or product; rates carry their counts so small samples are obvious
function compare(title, first, rows, go) {
  if (!rows.length) return el('span');
  const cols = [[first, r => r.name.toLowerCase()], ['Share', r => r.share ?? -1], ['Calls', r => r.calls], ['Spoke with', r => r.spoke], ['Booked', r => r.booked_rate ?? -1], ['Attended', r => r.attendance ?? -1], ['Avg score', r => r.avg_score ?? -1], ['Mood', r => r.avg_mood ?? -9], ['Reply', r => r.median_reply ?? 999], ['Opt-outs', r => r.opt_outs]];
  const line = r => { const b = btn(r.name, () => go(r), 'linkb'); return [first === 'Product' ? tag(r.id, b) : b, r.share == null ? 'Not assigned' : r.share + '%', String(r.calls), String(r.spoke), r.booked + (r.spoke ? ' (' + pct(r.booked_rate) + ')' : ''), pct(r.attendance), r.avg_score == null ? 'n/a' : String(Math.round(r.avg_score)), mood(r.avg_mood), secs(r.median_reply), String(r.opt_outs)]; };
  let by = -1, up = false; const box = el('div');
  const draw = () => {
    const list = by < 0 ? rows : [...rows].sort((a, b) => { const x = cols[by][1](a), y = cols[by][1](b); return (x < y ? -1 : x > y ? 1 : 0) * (up ? 1 : -1); });
    const t = table(cols.map(([name], i) => rows.length < 2 ? name : btn(name + (by === i ? (up ? ' ↑' : ' ↓') : ''), () => { up = by === i ? !up : i === 0 || name === 'Reply' || name === 'Opt-outs'; by = i; draw(); }, 'thb')), list.map(line));
    if (by >= 0) t.querySelectorAll('th')[by].setAttribute('aria-sort', up ? 'ascending' : 'descending');
    box.replaceChildren(t);
  };
  draw();
  return el('div', 'cmp', el('h3', '', title), box, rows.some(r => !r.enough) ? el('p', 'mute', 'Too early to compare: each needs at least 20 leads spoken with before differences mean much.') : '');
}
function insightBlock(x) {
  const open1 = async id => { await showTab('L'); if (S.live) return fail('End the current call first.'); clearLeadFilters(); pick(id); };
  const kids = !x.ready ? [el('p', 'mute', 'Not enough calls yet: ' + x.have + ' of ' + x.need + ' customers spoken with.')] : !x.items.length ? [el('p', 'mute', 'No clear pattern yet across ' + x.have + ' customers.')] :
    x.items.map(i => { const d = el('details', 'insight', el('summary', '', i.text)); const u = el('div', 'names', ...i.customers.slice(0, 40).map(c => btn(c.name + (c.booked ? ' (booked)' : ''), () => open1(c.id), 'linkb'))); d.append(u, i.customers.length > 40 ? el('p', 'mute', 'and ' + (i.customers.length - 40) + ' more') : ''); return d; });
  return block('Insights, counted across customers', ...kids);
}
function statBlocks(s, extra) {
  const tip = el('div', 'tip', s.calls ? 'Point at a day for its count.' : 'No calls yet.'), top = Math.max(1, ...s.daily.map(d => d[1]));
  const day = d => new Date(d + 'T00:00').toLocaleDateString('en-IN', {day: 'numeric', month: 'short'});
  const days = el('div', 'days'); days.style.gridTemplateColumns = 'repeat(' + s.daily.length + ',1fr)'; days.append(...s.daily.map(([d, n]) => {
    const i = el('i'); i.style.height = 100 * n / top + '%'; const b = el('button', '', i), t = day(d) + ': ' + n + (n === 1 ? ' call' : ' calls');
    b.type = 'button'; b.setAttribute('aria-label', t); b.onmouseenter = b.onfocus = () => tip.textContent = t; return b; }));
  const leadRows = s.leads.slice(0, 12).map(l => [btn(l.name, async () => { await showTab('L'); if (S.live) return fail('End the current call first.'); clearLeadFilters(); pick(l.id); }, 'linkb'), stage(l.stage), el('span', l.band, l.score + ' ' + l.band)]);
  return [block('From lead to booking', ...bars(s.funnel)),
    block('Calls, last ' + s.daily.length + ' days', days, el('div', 'dlab', el('span', '', day(s.daily[0][0])), el('span', '', 'Today')), tip),
    block('Lead quality', ...bars(Object.entries(s.bands))),
    block('Bookings by offering', ...(s.offerings.length ? bars(s.offerings) : [el('p', 'mute', 'No bookings yet.')])),
    block('What customers object to', ...(s.objections.length ? bars(s.objections) : [el('p', 'mute', 'No objections recorded.')])),
    block('Trust and safety', table(['Event', 'Count'], [['Asked for a person', String(s.handoffs)], ['Asked not to be called', String(s.opt_outs)], ['Unsupported claims stopped', String(s.claims_stopped)]])),
    ...(extra || []),
    block('Leads', s.leads.length ? table(['Name', 'Stage', 'Score'], leadRows) : el('p', 'mute', 'No leads yet.')),
    insightBlock(s.insights)];
}
function drawStats(p, s) {
  const err = el('p', 'err'); err.id = 'perr';
  const up = block('Upcoming', s.upcoming.length ? table(['Who', 'What', 'When', ''], s.upcoming.map(b => [b.name, b.project, when(b.start), btn('Cancel', async () => {
    if (!await ask('Cancel this booking?', b.name + ', ' + b.project + ', ' + when(b.start) + '. Nobody is notified, so tell ' + b.name + ' yourself.', 'Cancel booking', true)) return;
    try { await api('/leads/' + b.lead_id + '/booking', undefined, 'DELETE'); await loadList(); open(p.id, 'stats'); } catch (e) { problem(e); } })])) : el('p', 'mute', 'Nothing booked.'));
  const n = Object.keys(P.f).length;
  $('pbody').replaceChildren(head(p), filterBar(p, s.by_caller), el('div', 'pscroll', err, n && !s.calls ? el('p', 'mute', 'No calls match these filters.') : n ? el('p', 'mute', 'Showing only calls that match, the people on them, and the bookings they led to.') : '', tiles(s),
    P.f.caller ? '' : compare('By AI caller', 'Caller', s.by_caller, r => { P.f.caller = r.id; open(p.id, 'stats'); }), el('div', 'cols', ...statBlocks(s, [up]))));
}
const mins = v => v == null ? 'n/a' : Math.floor(v / 60) + 'm ' + Math.round(v % 60) + 's';
const calls_at = s => new Date(s).toLocaleString('en-IN', {day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Kolkata'});
function drawCalls(p, r) {
  const err = el('p', 'err'); err.id = 'perr';
  const q = el('input'); q.type = 'search'; q.placeholder = 'Search customer, phone or summary'; q.setAttribute('aria-label', 'Search calls'); q.value = P.calls.q;
  q.onchange = () => { P.calls.q = q.value; P.calls.page = 0; open(p.id, 'calls'); };
  const so = el('select'); so.setAttribute('aria-label', 'Sort calls'); [['newest', 'Newest first'], ['longest', 'Longest first'], ['unhappiest', 'Unhappiest first'], ['slowest', 'Slowest replies first'], ['score', 'Highest score first']].forEach(([v, t]) => { const o = el('option', '', t); o.value = v; so.append(o); });
  so.value = P.calls.sort; so.onchange = () => { P.calls.sort = so.value; P.calls.page = 0; open(p.id, 'calls'); };
  const csv = el('a', 'linkb', 'Download CSV'); csv.href = '/api/products/' + p.id + '/calls.csv?' + qs({q: P.calls.q, sort: P.calls.sort});
  const rows = r.rows.map(c => [btn(c.name, () => callDialog(p, c), 'linkb'), calls_at(c.started_at), c.caller, cap(c.mode), c.status === 'active' ? 'In progress' : RESULT[c.result] + (c.offering ? ': ' + c.offering : ''), mins(c.seconds), String(c.said), mood(c.mood), secs(c.reply), el('span', c.band, c.score + ' ' + c.band)]);
  const pages = Math.ceil(r.total / 50), go = d => { P.calls.page += d; open(p.id, 'calls'); };
  const prev = btn('Previous', () => go(-1)), next = btn('Next', () => go(1)); prev.disabled = P.calls.page === 0; next.disabled = P.calls.page + 1 >= pages;
  const order = Object.keys(RESULT).filter(k => r.counts[k]);
  $('pbody').replaceChildren(head(p), filterBar(p, P.callers.concat()), el('div', 'pscroll', err, el('div', 'ctools', q, so, r.total ? csv : ''),
    el('p', '', r.total + (r.total === 1 ? ' call. ' : ' calls. ') + order.map(k => RESULT[k] + ' ' + r.counts[k]).join(', ')),
    r.total ? el('div', 'cmp', table(['Customer', 'When', 'AI caller', 'Channel', 'Result', 'Length', 'Replies', 'Mood', 'Reply time', 'Score now'], rows)) : el('p', 'mute', Object.keys(P.f).length || P.calls.q ? 'No calls match.' : 'No calls yet.'),
    pages > 1 ? el('div', 'acts', prev, el('span', 'mute', 'Page ' + (P.calls.page + 1) + ' of ' + pages), next) : ''));
}
async function callDialog(p, c) {
  let d; try { d = await api('/calls/' + c.id); } catch (e) { return problem(e); }
  const a = d.analysis || {}, log = el('div', 'clog', ...d.turns.map(t => el('div', 't ' + t.role, t.text)));
  const sec = (t, v) => list(v).length ? [el('h3', '', t), el('ul', '', ...list(v).map(x => el('li', '', String(x))))] : [];
  dialog(c.name, el('p', 'mute', [calls_at(c.started_at), c.caller, cap(c.mode), mins(c.seconds), RESULT[c.result]].filter(Boolean).join(', ')),
    el('p', '', a.summary || (d.status === 'failed' ? 'The summary could not be written.' : d.status === 'done' ? 'No summary.' : 'Summary not ready yet.')), ...sec('Objections', a.objections), ...sec('Buying signals', a.buying_signals),
    a.coaching ? el('div', '', el('h3', '', 'Coaching, suggested by AI'), el('p', '', a.coaching)) : '', el('h3', '', 'Transcript'), d.turns.length ? log : el('p', 'mute', 'Nothing was said.'),
    el('div', 'acts', btn('Open lead', async () => { $('dlg').close(); await showTab('L'); if (S.live) return fail('End the current call first.'); clearLeadFilters(); pick(c.lead_id); })));
}

// ---------- creator ----------
const BLANK = {company: '', topic: '', callers: [], appointment: '', catalogue: '', knowledge: '', rules: [], items: [], facts: [], hours: [10, 11, 12, 15, 16, 17], days: [0, 1, 2, 3, 4, 5, 6], minutes: 45, spots: 1, horizon: 14, notice: 2, keyterms: [], glossary: {}};
function drawNew() {
  P.form = null;
  const co = el('input'), br = el('textarea'), file = el('input'), err = el('p', 'err'); err.id = 'perr';
  co.maxLength = 60; br.rows = 14; br.maxLength = 20000; file.type = 'file'; file.accept = '.txt,.md';
  br.placeholder = 'What it is, the options and prices, policies such as refunds or delivery, what you must never promise, and how a customer takes the next step.';
  co.oninput = br.oninput = () => P.dirty = !!(co.value || br.value);
  file.onchange = async () => { const f = file.files[0]; if (!f) return; if (f.size > 60000) return problem('Choose a text file under 60 KB.'); br.value = (await f.text()).slice(0, 20000); br.oninput(); };
  const go = btn('Suggest settings', async () => {
    if (br.value.trim().length < 80) return problem('Add at least a few sentences of product information.');
    const seq = P.seq; P.busy = true; go.disabled = self.disabled = true; go.textContent = 'Reading your information…'; problem('');
    try { const d = await api('/products/draft', {company: co.value, brief: br.value}); P.busy = false; if (seq === P.seq) drawForm({...BLANK, ...d, id: null}, true); }
    catch (e) { P.busy = false; go.disabled = self.disabled = false; go.textContent = 'Suggest settings'; problem(e); }
  }, 'primary');
  const self = btn('Fill in myself', () => drawForm({...BLANK, company: co.value, knowledge: br.value, catalogue: br.value.slice(0, 2000)}, true));
  $('pbody').replaceChildren(el('div', 'phead', el('div', 'grow', el('h2', '', 'New product'))), el('div', 'pscroll', el('div', 'form', err,
    el('label', '', 'Company name', co), el('label', '', 'Product information', br), el('label', '', 'Or load a .txt or .md file', file),
    el('p', 'help', 'The caller may only state facts and prices found here. AI suggests the rest, and you review it before anything is saved.'), el('div', 'row', go, self))));
  document.querySelector('#pbody .row').style.justifyContent = 'flex-start'; co.focus();
}

// ---------- editor ----------
function drawForm(src, fresh) {
  const f = P.form = JSON.parse(JSON.stringify(src)), err = el('p', 'err'); err.id = 'perr';
  P.dirty = !!fresh;
  const status = el('span', '', fresh ? 'Not saved yet' : ''), saveB = btn(fresh ? 'Create product' : 'Save changes', save, 'primary');
  const touch = () => { P.dirty = true; status.textContent = 'Unsaved changes'; status.className = ''; greet(); };
  const field = (label, get, set, o = {}) => {
    const n = el(o.rows ? 'textarea' : 'input'); if (o.rows) n.rows = o.rows; if (o.max) n.maxLength = o.max; if (o.type) n.type = o.type; if (o.ph) n.placeholder = o.ph;
    n.value = get() ?? ''; n.oninput = () => { set(n.value); touch(); }; return el('label', '', label, n);
  };
  const choose = (label, opts, value, set) => { const n = el('select'); opts.forEach(([v, t]) => { const x = el('option', '', t); x.value = v; n.append(x); }); n.value = value; n.onchange = () => { set(n.value); touch(); }; return el('label', '', label, n); };
  const prop = (o, k) => [() => o[k], v => o[k] = v];
  const lines = (o, k, sep) => [() => (o[k] || []).join(sep), v => o[k] = v.split(sep === '\n' ? '\n' : ',').map(x => x.trim()).filter(Boolean)];
  const set = (title, help, ...kids) => el('fieldset', '', el('legend', '', title), help ? el('p', 'help', help) : null, ...kids);
  const greetBox = el('p', 'greet'), greet = () => greetBox.textContent = `Hi Rahul! This is ${(P.callers.find(c => c.id === (f.callers[0] || {}).id) || {name: '…'}).name}, an AI assistant from ${f.company || '…'}. You'd asked about ${f.topic || '…'}. Is this an okay time?`;

  const swatches = el('div', 'sw');
  api('/products/colors').then(c => { if (!f.color) f.color = c.next; swatches.replaceChildren(...c.palette.map((x, i) => { const b = btn('', () => { f.color = x; [...swatches.children].forEach(n => n.setAttribute('aria-pressed', String(n === b))); touch(); }); b.style.background = x; b.setAttribute('aria-label', ['Blue', 'Orange', 'Aqua', 'Yellow', 'Pink', 'Green', 'Violet', 'Red'][i]); b.title = b.getAttribute('aria-label'); b.setAttribute('aria-pressed', String(x === f.color)); return b; })); }).catch(problem);
  if (!f.callers.length && P.callers[0]) f.callers = [{id: P.callers[0].id, share: 100}];
  const who = el('div'), whoTotal = el('p', 'mute'), total = () => { const t = f.callers.reduce((a, c) => a + (+c.share || 0), 0); whoTotal.textContent = 'Total ' + t + '%' + (t === 100 ? '' : ', must be 100%'); whoTotal.className = t === 100 ? 'mute' : 'err'; };
  const drawWho = () => { who.replaceChildren(...f.callers.map((c, i) => el('div', 'split', choose('Caller', P.callers.map(x => [x.id, x.name]), c.id, v => { c.id = v; }),
    field('Share %', () => c.share, v => { c.share = Math.round(+v) || 0; total(); }, {type: 'number'}), btn('Remove', () => { f.callers.splice(i, 1); if (f.callers.length === 1) f.callers[0].share = 100; drawWho(); touch(); })))); total(); };
  const items = el('div'), drawItems = () => items.replaceChildren(...f.items.map((it, i) => el('div', 'item', field('Offering', ...prop(it, 'name'), {max: 60}),
    field('Other names customers use', ...lines(it, 'aliases', ', '), {ph: 'separated by commas'}), btn('Remove', () => { f.items.splice(i, 1); drawItems(); touch(); }))));
  const share = () => { const t = f.facts.reduce((a, q) => a + (+q.points || 0), 0) || 1; shares.forEach((n, i) => n.textContent = 'Worth ' + Math.round(75 * (+f.facts[i].points || 0) / t) + ' of 100'); };
  let shares = [];
  const ruleOf = q => Object.keys(q.full_if || {})[0] || '';
  const qs = el('div'), drawQs = () => { shares = []; qs.replaceChildren(...f.facts.map((q, i) => {
    const s = el('span', 'mute'); shares.push(s); const k = ruleOf(q);
    const rule = choose('Best fit', [['', 'Any answer'], ...(q.type === 'number' ? [['min', 'At least'], ['max', 'At most']] : []), ['any_of', 'Mentions any of']], k, v => { q.full_if = v ? {[v]: v === 'any_of' ? [] : null} : {}; drawQs(); });
    const val = k ? field('Value', () => k === 'any_of' ? (q.full_if.any_of || []).join(', ') : q.full_if[k], v => q.full_if[k] = k === 'any_of' ? v.split(',').map(x => x.trim()).filter(Boolean) : v === '' ? null : +v, {type: k === 'any_of' ? 'text' : 'number'}) : el('span');
    return el('div', 'item q', field('Name', ...prop(q, 'label'), {max: 40}), field('What to find out', ...prop(q, 'ask'), {max: 120}),
      choose('Answer is', [['text', 'Words'], ['number', 'A number']], q.type, v => { q.type = v; q.full_if = {}; drawQs(); }),
      field('Importance', () => q.points, v => { q.points = +v || 1; share(); }, {type: 'number'}), btn('Remove', () => { f.facts.splice(i, 1); drawQs(); touch(); }),
      el('div', 'wide', rule, val, field('How to read the answer', ...prop(q, 'hint'), {max: 200, ph: 'e.g. in thousand rupees'})), s);
  })); share(); };
  const chips = (all, key, label) => el('div', 'chips', ...all.map(v => { const c = el('input'); c.type = 'checkbox'; c.checked = f[key].includes(v); c.onchange = () => { f[key] = c.checked ? [...f[key], v].sort((a, b) => a - b) : f[key].filter(x => x !== v); touch(); }; return el('label', '', c, label(v)); }));
  const gloss = [() => Object.entries(f.glossary || {}).map(([k, v]) => k + ' = ' + v).join('\n'), v => f.glossary = Object.fromEntries(v.split('\n').map(l => l.split('=').map(x => x.trim())).filter(x => x[0] && x[1]).map(x => [x[0], x[1]]))];

  async function save() {
    if (P.busy) return;
    P.busy = true; saveB.disabled = true; status.textContent = 'Saving…'; problem('');
    for (const q of f.facts) if (!q.key) { let k = (q.label || '').toLowerCase().replace(/\W+/g, '_').replace(/^_|_$/g, '') || 'q', n = 2, b = k; while (f.facts.some(x => x !== q && x.key === k)) k = b + '_' + n++; q.key = k; }
    try {
      status.textContent = 'Checking for safety…';
      const p = await safeSave(confirm => f.id ? api('/products/' + f.id, {...f, confirm}, 'PUT') : api('/products', {...f, confirm}));
      P.dirty = false; P.busy = false; await loadList(); await loadProducts();
      await open(p.id, fresh ? 'stats' : 'edit');
      const s = document.querySelector('.save span'); if (s) { s.textContent = 'Saved. New calls use this version.'; s.className = 'ok'; }
    } catch (e) { if (fresh) f.facts.forEach(q => { if (!src.facts.some(x => x.key === q.key)) delete q.key; }); status.textContent = 'Not saved'; problem(e); }
    P.busy = false; saveB.disabled = false;
  }

  const q = el('input'), ans = el('div'); q.placeholder = 'e.g. can I get a refund?'; q.setAttribute('aria-label', 'Test question');
  const test = btn('Test', async () => { if (!q.value.trim()) return; try { const r = await api('/products/' + f.id + '/ask?q=' + encodeURIComponent(q.value)); ans.replaceChildren(...(r.length ? r.map(x => el('div', 'fact', el('div', 'mute', x.title), x.text)) : [el('p', 'mute', 'Nothing found. The caller would say the team should confirm it.')])); } catch (e) { problem(e); } });
  const form = el('div', 'form', err,
    set('Basics', '', el('div', 'g2', field('Company name', ...prop(f, 'company'), {max: 60}), field('What you sell', ...prop(f, 'topic'), {max: 80, ph: 'e.g. rooftop solar panels'}),
      field('What to book', ...prop(f, 'appointment'), {max: 40, ph: 'e.g. free demo'})), el('div', 'mute', 'How a first call opens'), greetBox),
    set('Colour', 'A circle in this colour marks the product everywhere.', swatches),
    set('Who calls', 'One AI caller, or a split of new leads. A lead always keeps the same caller.', who, whoTotal, el('div', 'acts', btn('Add caller', () => { const c = P.callers.find(c => !f.callers.some(x => x.id === c.id)); if (!c) return problem('Every caller is already added. Create more in Callers.'); f.callers.push({id: c.id, share: f.callers.length ? 1 : 100}); drawWho(); touch(); }), btn('Split evenly', () => { const n = f.callers.length; f.callers.forEach((c, i) => c.share = Math.floor(100 / n) + (i < 100 % n ? 1 : 0)); drawWho(); touch(); }))),
    set('What the caller knows', 'They may only state facts and prices found here.', field('Key facts, always in mind', ...prop(f, 'catalogue'), {rows: 5, max: 2500}),
      field('Full information, searched during calls. Use # and ## headings for long documents', ...prop(f, 'knowledge'), {rows: 12, max: 60000}),
      field('Never promise or say, one per line', ...lines(f, 'rules', '\n'), {rows: 3})),
    set('Offerings', 'What a customer can book.', items, btn('Add offering', () => { f.items.push({name: '', aliases: []}); drawItems(); touch(); })),
    set('Qualifying questions', 'The caller learns these naturally, in this order. They make up 75 of the 100 score points; a booking adds 25.', qs, btn('Add question', () => { f.facts.push({label: '', type: 'text', ask: '', hint: '', points: 10, full_if: {}}); drawQs(); touch(); })),
    el('p', 'help', 'Days, times, limits and notice are set in Calendar, Booking rules.'),
    el('details', '', el('summary', '', 'Speech and search hints'), field('Names to listen for, separated by commas', ...lines(f, 'keyterms', ', ')), field('Hindi words to match, one per line: English = Hindi words', ...gloss, {rows: 4})),
    f.id ? set('Test the knowledge', 'Shows the saved facts the caller would be given.', el('div', 'row', q, test), ans) : null);
  $('pbody').replaceChildren(fresh ? el('div', 'phead', el('div', 'grow', el('h2', '', 'Review new product'))) : head(f), el('div', 'pscroll', form),
    el('div', 'save', status, btn('Cancel', async () => { if (await leaveOk()) open(f.id || (P.list[0] && P.list[0].id) || null, 'stats'); }), saveB));
  drawWho(); drawItems(); drawQs(); greet();
}
