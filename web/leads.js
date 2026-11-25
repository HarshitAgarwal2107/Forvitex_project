// Lead settings, select mode, delete and restore. L holds the selection; uses $, el, api, S, pick, refresh, fail, btn, ask, dialog.
function leadRow(l, b) {
  const row = el('div', 'lrow');
  if (L.on) { const c = el('input'); c.type = 'checkbox'; c.checked = L.ids.has(l.id); c.setAttribute('aria-label', 'Select ' + l.name); c.onchange = () => toggleSel(l.id); row.append(c, b); return row; }
  const g = el('button', 'gear', '\u2699'); g.type = 'button'; g.title = 'Edit or delete'; g.setAttribute('aria-label', 'Settings for ' + l.name); g.onclick = () => leadSettings(l.id).catch(fail);
  row.append(b, g); return row;
}
function toggleSel(id) { L.ids.has(id) ? L.ids.delete(id) : L.ids.add(id); drawState(); }
function selBar() {
  for (const id of [...L.ids]) if (!last.leads.some(l => l.id === id)) L.ids.delete(id);
  $('selbar').hidden = !L.on; $('selb').setAttribute('aria-pressed', String(L.on)); $('selb').textContent = L.on ? 'Done' : 'Select';
  if (!L.on) return;
  const all = L.shown.length && L.shown.every(l => L.ids.has(l.id)), n = L.ids.size, hidden = [...L.ids].filter(id => !L.shown.some(l => l.id === id)).length;
  const del = btn('Delete ' + n, () => removeLeads([...L.ids]), 'stop'); del.disabled = !n;
  $('selbar').replaceChildren(btn(all ? 'Clear' : 'Select all ' + L.shown.length, () => { all ? L.shown.forEach(l => L.ids.delete(l.id)) : L.shown.forEach(l => L.ids.add(l.id)); drawState(); }), del,
    hidden ? el('span', 'mute', hidden + ' selected outside this search or filter') : '');
}
$('selb').onclick = () => { L.on = !L.on; L.ids.clear(); if (last) drawState(); };

async function removeLeads(ids) {
  if (S.live && ids.includes(S.lead)) return fail('End the current call first.');
  const one = ids.length === 1, name = one ? (last.leads.find(l => l.id === ids[0]) || {}).name : '';
  if (!await ask(one ? 'Delete ' + name + '?' : 'Delete ' + ids.length + ' leads?', (one ? 'This lead and their' : 'These leads and their') + ' calls will leave every list and report. You can restore them under Deleted. People who asked not to be called stay protected.', one ? 'Delete lead' : 'Delete ' + ids.length + ' leads', true)) return;
  try {
    const r = await api('/leads/delete', {ids});
    if (r.deleted.includes(S.lead)) { S.lead = S.call = null; S.logKey = S.sideKey = ''; $('who').textContent = 'Choose a lead'; $('voice').disabled = $('text').disabled = true; $('log').replaceChildren(); $('side').replaceChildren(el('p', 'mute', 'Choose a lead to see their details.')); }
    L.ids.clear(); L.on = false; fail(); await refresh(); await drawGone();
    const n = r.deleted.length;
    $('undo').replaceChildren(el('p', '', n ? n + (n === 1 ? ' lead deleted. ' : ' leads deleted. ') : 'Nothing was deleted. ', n ? btn('Undo', () => bringBack(r.deleted)) : ''),
      ...(r.kept.length ? [el('p', 'mute', 'Kept: ' + r.kept.slice(0, 8).map(k => k.name + ' (' + k.why.toLowerCase() + ')').join(', ') + (r.kept.length > 8 ? ' and ' + (r.kept.length - 8) + ' more' : ''))] : []));
  } catch (e) { fail(e); }
}
async function bringBack(ids) { try { await api('/leads/restore', {ids}); $('undo').replaceChildren(); await refresh(); await drawGone(); } catch (e) { fail(e); } }
async function drawGone() {
  const all = await api('/leads/deleted'), [sum, box] = $('gone').children, q = $('find').value.trim().toLowerCase(), dg = q.replace(/\D/g, '');
  const g = all.filter(l => (!q || l.name.toLowerCase().includes(q) || (dg.length > 2 && l.phone.includes(dg))) && (!$('flt').value || l.product_id === $('flt').value));
  $('gone').hidden = !g.length; sum.textContent = 'Deleted (' + g.length + ')';
  box.replaceChildren(...g.slice(0, 50).map(l => el('div', 'line', el('span', 'mute', tag(l.product_id, l.name + ', ' + l.company)), btn('Restore', () => bringBack([l.id])))), g.length > 50 ? el('p', 'mute', 'Showing the 50 most recent.') : '');
}

async function leadSettings(id) {
  const l = await api('/leads/' + id), inp = (v, max) => { const n = el('input'); n.value = v; n.maxLength = max; return n; };
  const name = inp(l.name, 60), phone = inp(l.phone, 20), notes = el('textarea'), dnc = el('input');
  notes.value = l.context; notes.rows = 4; notes.maxLength = 1500; dnc.type = 'checkbox'; dnc.checked = !!l.do_not_call;
  dnc.onchange = () => { $('derr').textContent = l.do_not_call && !dnc.checked ? l.name + ' asked not to be called. Save only if they have agreed to calls again.' : ''; };
  const save = btn('Save', async () => {
    if (S.live && S.lead === id) return $('derr').textContent = 'End the current call first.';
    save.disabled = true; $('derr').textContent = '';
    try { await safeSave(confirm => api('/leads/' + id, {name: name.value, phone: phone.value, context: notes.value, do_not_call: dnc.checked, confirm}, 'PUT')); $('dlg').close(); S.sideKey = ''; await refresh(); if (S.lead === id) await poll(); }
    catch (e) { $('derr').textContent = e.message; save.disabled = false; }
  }, 'primary');
  dialog('Lead settings', el('p', 'mute', tag(l.product_id, l.product.company)), el('label', 'mute', 'Name', name), el('label', 'mute', 'Phone', phone), el('label', 'mute', 'Notes the caller can use', notes),
    el('label', 'chk', dnc, 'Do not call this person'), el('div', 'acts', save, btn('Delete lead', () => { $('dlg').close(); removeLeads([id]); }, 'stop')));
}
