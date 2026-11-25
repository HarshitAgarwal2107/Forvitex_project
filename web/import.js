// Batch lead import: choose or paste -> match columns -> check -> import. Uses $, el, api, loadProducts, refresh, fail.
const I = {data: null, seq: 0, checked: null, problemsOnly: false};
const FIELDS = [['name', 'Name'], ['phone', 'Phone'], ['notes', 'Notes'], ['source', 'Source'], ['product', 'Product']];

async function openImport() {
  I.data = null; I.seq++;
  const ps = await api('/products');
  if (!ps.length) return fail('Create a product first.');
  I.products = ps; I.product = $('flt').value || (ps.length === 1 ? ps[0].id : '');
  stepFile(); $('imp').showModal();
}
$('importb').onclick = () => openImport().catch(fail);
$('imp').addEventListener('close', () => I.seq++);
const ibtn = (t, f, c) => { const b = el('button', c || '', t); b.type = 'button'; b.onclick = f; return b; };
function frame(title, body, foot) {
  const err = el('p', 'err'); err.id = 'ierr'; err.setAttribute('role', 'alert');
  $('imp').replaceChildren(el('h2', '', title), err, body, el('div', 'row foot', ...foot));
}
const ierr = e => { if ($('ierr')) $('ierr').textContent = e ? e.message || e : ''; };
const close = () => $('imp').close();

function stepFile() {
  const drop = el('label', 'drop', el('b', '', 'Choose an Excel or CSV file'), el('span', 'mute', 'or drop it here')), file = el('input'), paste = el('textarea');
  file.type = 'file'; file.accept = '.csv,.xlsx,.tsv,.txt'; drop.append(file);
  file.onchange = () => file.files[0] && read(file.files[0], file.files[0].name);
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); };
  drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); const f = e.dataTransfer.files[0]; if (f) read(f, f.name); };
  paste.rows = 6; paste.placeholder = 'Name\tPhone\tNotes'; paste.setAttribute('aria-label', 'Pasted rows');
  const tpl = el('a', '', 'Download a template');
  tpl.href = URL.createObjectURL(new Blob(['﻿Name,Phone,Notes,Source,Product\r\nAsha Rao,+91 98765 43210,Asked about weekend timings,Website,\r\n'], {type: 'text/csv'})); tpl.download = 'leads-template.csv';
  frame('Import leads', el('div', '', drop, el('p', 'mute', 'Or copy rows from Excel or Google Sheets and paste them here'), paste, el('p', 'mute', 'Name and phone are required. Up to 5,000 leads at a time. '), tpl),
    [ibtn('Cancel', close), ibtn('Continue with pasted rows', () => paste.value.trim() ? read(new Blob([paste.value]), 'pasted.txt') : ierr('Paste some rows first, or choose a file.'), 'primary')]);
}
async function read(blob, name) {
  ierr('');
  if (blob.size > 5e6) return ierr('Use a file under 5 MB.');
  const seq = ++I.seq;
  try {
    const r = await fetch('/api/leads/import/read?filename=' + encodeURIComponent(name), {method: 'POST', body: blob, headers: {'content-type': 'application/octet-stream'}});
    const j = await r.json(); if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'That file could not be read');
    if (seq !== I.seq) return;
    I.data = j; I.extra = true; I.problemsOnly = false; stepCheck();
  } catch (e) { if (seq === I.seq) ierr(e); }
}
const payload = commit => ({headers: I.data.headers, rows: I.data.rows, mapping: I.data.mapping, product_id: I.product, extra_notes: I.extra, commit});

async function stepCheck() {
  const opt = (v, t) => { const o = el('option', '', t); o.value = v; return o; };
  const map = el('div', 'map', ...FIELDS.map(([k, label]) => {
    const s = el('select'); s.append(opt('', 'None'), ...I.data.headers.map((h, i) => opt(i, h))); s.value = I.data.mapping[k] ?? '';
    s.onchange = () => { if (s.value === '') delete I.data.mapping[k]; else I.data.mapping[k] = +s.value; check(); };
    return el('label', '', label, s);
  }));
  const prod = el('select'); prod.append(opt('', 'Choose a product'), ...I.products.map(p => opt(p.id, p.company + ': ' + p.topic))); prod.value = I.product; tint(prod); prod.onchange = () => { I.product = prod.value; check(); };
  const ex = el('input'); ex.type = 'checkbox'; ex.checked = I.extra; ex.onchange = () => { I.extra = ex.checked; check(); };
  const sum = el('div', 'sum'), tbl = el('div', 'prev'); sum.id = 'isum'; tbl.id = 'iprev';
  const go = ibtn('Import', commit, 'primary'); go.id = 'igo'; go.disabled = true;
  frame('Check your leads', el('div', '', el('label', '', I.data.mapping.product != null ? 'Product, when the row does not name one' : 'Product', prod), el('p', 'mute', 'Which column holds what?'), map,
    el('label', 'chk', ex, 'Add the remaining columns to each lead’s notes, so the caller knows them'), sum, tbl), [ibtn('Back', () => { I.seq++; stepFile(); }), go]);
  check();
}
async function check() {
  const seq = ++I.seq; $('igo').disabled = true; ierr('');
  try { const r = await api('/leads/import', payload(false)); if (seq !== I.seq || !$('isum')) return; I.checked = r; show(); }
  catch (e) { if (seq === I.seq && $('isum')) { $('igo').textContent = 'Import'; $('isum').replaceChildren(); $('iprev').replaceChildren(); ierr(e); } }
}
function show() {
  const r = I.checked, bad = r.skip + r.problem;
  const only = el('input'); only.type = 'checkbox'; only.checked = I.problemsOnly; only.onchange = () => { I.problemsOnly = only.checked; show(); };
  $('isum').replaceChildren(el('b', 'ok', r.new + ' ready to import'), el('span', 'mute', r.skip + ' skipped'), el('span', r.problem ? 'err0' : 'mute', r.problem + ' with problems'), bad ? el('label', 'chk', only, 'Show only these') : '');
  const rows = r.rows.filter(x => !I.problemsOnly || x.status !== 'new'), t = el('table', '', el('tr', '', ...['Row', 'Name', 'Phone', 'Product', ''].map(h => el('th', '', h))));
  rows.slice(0, 50).forEach(x => t.append(el('tr', '', el('td', '', String(x.row)), el('td', '', x.name), el('td', '', x.phone), el('td', '', x.product ? tag(x.product_id, x.product) : ''), el('td', x.status === 'new' ? 'ok' : x.status === 'skip' ? 'mute' : 'err0', x.why || 'Ready'))));
  $('iprev').replaceChildren(t, rows.length > 50 ? el('p', 'mute', 'Showing the first 50 of ' + rows.length) : '');
  $('igo').textContent = r.new ? 'Import ' + r.new + (r.new === 1 ? ' lead' : ' leads') : 'Nothing to import'; $('igo').disabled = !r.new;
}
async function commit() {
  $('igo').disabled = true; $('igo').textContent = 'Importing…';
  try {
    const r = await api('/leads/import', payload(true)); await loadProducts(); await refresh();
    const bad = r.rows.filter(x => x.status !== 'new'), body = el('div', '', el('p', '', r.saved + (r.saved === 1 ? ' lead' : ' leads') + ' added. ' + (bad.length ? bad.length + ' left out.' : '')));
    if (bad.length) {
      const q = v => { v = String(v); if (/^[=+@\t\r-]/.test(v)) v = "'" + v; return '"' + v.replace(/"/g, '""') + '"'; };  // safe to open in Excel
      const a = el('a', '', 'Download the rows left out'); a.download = 'leads-left-out.csv';
      a.href = URL.createObjectURL(new Blob(['﻿' + [[...I.data.headers, 'Reason'], ...bad.map(x => [...I.data.rows[x.row - 1], x.why])].map(l => l.map(q).join(',')).join('\r\n')], {type: 'text/csv'}));
      body.append(a);
    }
    frame('Import finished', body, [ibtn('Done', close, 'primary')]);
  } catch (e) { ierr(e); show(); }
}
