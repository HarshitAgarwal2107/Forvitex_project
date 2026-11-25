// Calendar tab: bookings, free spots, closures, booking rules and team. Uses $, el, api, btn, ask, stage, pick, showTab, tag, dot, tint.
// Times are stored in IST, so they are read from the text and never shifted by the browser's time zone.
const C = {start: null, product: '', show: '', q: '', data: null, seq: 0};
const ST = {confirmed: 'Booked', completed: 'Needs outcome', attended: 'Attended', no_show: 'No-show'};
const iso = d => new Date(Date.now() + 198e5 + d * 864e5).toISOString().slice(0, 10);   // today in IST plus d days
const plus = (day, n) => new Date(Date.parse(day + 'T00:00Z') + n * 864e5).toISOString().slice(0, 10);
const dayName = (day, o) => new Date(day + 'T00:00Z').toLocaleDateString('en-IN', {timeZone: 'UTC', ...o});
const dayLong = day => dayName(day, {weekday: 'long', day: 'numeric', month: 'short'});
const at = s => dayLong(s.slice(0, 10)) + ', ' + hour(+s.slice(11, 13));
const company = id => (C.data.products.find(p => p.id === id) || {}).company || '';
const cerr = e => { if ($('cerr')) $('cerr').textContent = e ? e.message || e : ''; };

async function drawCalendar() {
  C.start = C.start || iso(0);
  const seq = ++C.seq, d = await api('/calendar?start=' + C.start + '&product=' + encodeURIComponent(C.product));
  if (seq !== C.seq) return;
  C.data = d; for (const p of d.products) PC[p.id] = p.color;
  const go = n => { C.start = n ? plus(C.start, n) : iso(0); drawCalendar().catch(cerr); };
  const sel = el('select'), o = (v, t) => { const x = el('option', '', t); x.value = v; return x; };
  sel.append(o('', 'All products'), ...d.products.map(p => o(p.id, p.company))); sel.value = C.product; tint(sel);
  sel.setAttribute('aria-label', 'Product'); sel.onchange = () => { C.product = sel.value; drawCalendar().catch(cerr); };
  const prev = btn('‹', () => go(-7)), next = btn('›', () => go(7)); prev.setAttribute('aria-label', 'Previous week'); next.setAttribute('aria-label', 'Next week');
  const show = el('select'); show.setAttribute('aria-label', 'Show');
  [['', 'Everything'], ['free', 'Free times only'], ['book', 'Bookings only'], ['none', 'Unassigned bookings'], ['due', 'Needs an outcome'], ['attended', 'Attended'], ['no_show', 'No-shows'], ...d.team.map(m => ['m' + m.id, 'Assigned to ' + m.name])].forEach(([v, t]) => show.append(o(v, t)));
  if (![...show.options].some(x => x.value === C.show)) C.show = '';
  show.value = C.show; show.onchange = () => { C.show = show.value; drawCalendar().catch(cerr); };
  const q = el('input'); q.type = 'search'; q.placeholder = 'Find a customer this week'; q.setAttribute('aria-label', 'Find a customer this week'); q.value = C.q; q.onchange = () => { C.q = q.value; drawCalendar().catch(cerr); };
  const needle = C.q.trim().toLowerCase(), due = b => b.past && (b.status === 'confirmed' || b.status === 'completed');
  const wanted = d.bookings.filter(b => (!needle || (b.name + ' ' + b.phone + ' ' + b.project).toLowerCase().includes(needle)) && (C.show === '' || C.show === 'book' || (C.show === 'none' ? !b.past && !b.member_id : C.show === 'due' ? due(b) : C.show[0] === 'm' ? 'm' + b.member_id === C.show : b.status === C.show)));
  const freeShown = needle ? [] : C.show === '' ? d.times : C.show === 'free' ? d.times.filter(t => t.state === 'open') : [];
  const narrowed = needle || C.show;
  const days = [...Array(7)].map((_, i) => plus(C.start, i)), err = el('p', 'err'); err.id = 'cerr'; err.setAttribute('role', 'alert');
  const hours = [...new Set([...freeShown, ...wanted].map(x => +x.start.slice(11, 13)))].sort((a, b) => a - b);
  const by = (list, k) => list.filter(x => x.start.slice(0, 13) === k);
  const grid = el('div', 'week', el('div', 'h'), ...days.map(x => {
    const off = d.closures.filter(c => c.day === x && c.hour == null), h = el('div', 'h' + (x === iso(0) ? ' now' : ''), dayName(x, {weekday: 'short', day: 'numeric', month: 'short'}));
    for (const c of off) h.append(el('div', 'cl', c.product_id ? dot(c.product_id) : '', 'Closed' + (c.reason ? ': ' + c.reason : '')));
    if (x >= iso(0)) { const b = btn(off.length ? 'Closures' : 'Close day', () => closeDialog(x, null), 'mini'); b.setAttribute('aria-label', (off.length ? 'Closures on ' : 'Close ') + dayLong(x)); h.append(el('div', '', b)); }
    return h; }));
  for (const h of hours) {
    grid.append(el('div', 'hr', hour(h)));
    for (const day of days) {
      const k = day + 'T' + String(h).padStart(2, '0'), cell = el('div', 'cell');
      for (const b of by(wanted, k)) {
        const n = btn('', () => bookingDialog(b), 'bk ' + b.status + (b.status === 'confirmed' && b.past ? ' due' : ''));
        n.append(el('b', '', b.name), el('span', '', dot(b.product_id), b.project), el('span', 'mute', b.past && b.status === 'confirmed' ? 'Needs outcome' : b.status !== 'confirmed' ? ST[b.status] : b.rep || 'Unassigned'));
        cell.append(n);
      }
      for (const t of by(freeShown, k)) {
        const text = t.state === 'open' ? t.left + ' of ' + t.spots + ' free' : t.state === 'full' ? 'Full' : t.state === 'closed' ? 'Closed' : 'Too soon';
        const n = btn('', () => timeDialog(t).catch(cerr), 'op ' + t.state); n.append(dot(t.product_id), text);
        n.setAttribute('aria-label', company(t.product_id) + ', ' + at(t.start) + ': ' + text); n.title = company(t.product_id); cell.append(n);
      }
      grid.append(cell);
    }
  }
  const unset = d.products.filter(p => !p.rules_set && (!C.product || p.id === C.product));
  $('calendarView').replaceChildren(el('section', '', el('div', 'chead', el('h2', '', dayName(days[0], {day: 'numeric', month: 'short'}) + ' to ' + dayName(days[6], {day: 'numeric', month: 'short', year: 'numeric'})), prev, btn('Today', () => go(0)), next),
    el('div', 'chead', q, sel, show, narrowed || C.product ? btn('Clear', () => { C.q = C.show = C.product = ''; drawCalendar().catch(cerr); }) : '', el('span', 'grow'),
      btn('Booking rules', () => rulesDialog().catch(cerr)), btn('Team (' + d.team.length + ')', teamDialog)), err,
    unset.length ? el('p', 'note', 'Using default booking rules for ', ...unset.flatMap((p, i) => [i ? ', ' : '', tag(p.id, p.company)]), '. ', btn('Set your own', () => rulesDialog(unset[0].id).catch(cerr), 'linkb')) : '',
    hours.length ? el('div', 'wrap', grid) : narrowed ? el('p', 'mute', 'Nothing this week matches.') : el('p', 'mute', 'No booking times or bookings in this week. ' + (d.products.length ? 'Booking rules set how far ahead times open.' : 'Create a product first.')),
    el('p', 'mute', '"2 of 3 free" means the product allows 3 bookings at that time and 2 are left. "Too soon" is inside the notice period, so the AI does not offer it.')));
}

function dialog(title, ...kids) {
  const err = el('p', 'err'); err.id = 'derr'; err.setAttribute('role', 'alert');
  $('dlg').oncancel = null;
  $('dlg').replaceChildren(el('h2', '', title), ...kids, err, el('div', 'row', btn('Close', () => $('dlg').close())));
  if (!$('dlg').open) $('dlg').showModal();
}
async function act(path, body, method, after) {
  const all = [...$('dlg').querySelectorAll('button,select,input')], was = all.map(b => b.disabled); all.forEach(b => b.disabled = true); $('derr').textContent = '';
  try { await api(path, method === 'DELETE' ? undefined : body || {}, method); if (after) await after(); else { $('dlg').close(); await drawCalendar(); } }
  catch (e) { all.forEach((b, i) => b.disabled = was[i]); if ($('derr')) $('derr').textContent = e.message; drawCalendar().catch(() => {}); }
}
const pickList = (label, items, text) => { const s = el('select'); s.append(...items.map((x, i) => { const o = el('option', '', text(x)); o.value = i; return o; })); const l = el('label', 'mute', label, s); l.get = () => items[+s.value]; l.sel = s; return l; };

// a picker for long lists: type to narrow it
function findList(label, items, text, hint) {
  const q = el('input'), s = el('select'), l = el('div', 'findl', el('label', 'mute', label, q), s); let shown = items;
  q.type = 'search'; q.placeholder = hint; s.setAttribute('aria-label', label);
  const fill = () => { const w = q.value.trim().toLowerCase().split(/\s+/); shown = items.filter(x => w.every(p => text(x).toLowerCase().includes(p))); s.replaceChildren(...shown.slice(0, 200).map((x, i) => { const o = el('option', '', text(x)); o.value = i; return o; })); s.disabled = !shown.length; if (!shown.length) { const o = el('option', '', 'No match'); s.append(o); } if (l.onpick) l.onpick(); };
  q.oninput = fill; fill(); l.get = () => shown[+s.value]; l.sel = s; return l;
}

// one product at one time
async function timeDialog(t) {
  const kids = [el('p', '', tag(t.product_id, company(t.product_id)))];
  if (t.state === 'closed') {
    const c = C.data.closures.find(c => c.id === t.closure);
    kids.push(el('p', 'mute', 'Closed' + (c && c.reason ? ': ' + c.reason : '') + (c && c.hour == null ? ' (whole day' + (c.product_id ? '' : ', all products') + ')' : '')));
    if (c) kids.push(btn('Reopen', () => act('/calendar/closures/' + c.id, null, 'DELETE')));
    return dialog(at(t.start), ...kids);
  }
  kids.push(el('p', 'mute', t.state === 'full' ? 'All ' + t.spots + ' allowed bookings are taken, here or at an overlapping time. Change the limit in Booking rules.' : t.left + ' of ' + t.spots + ' free.' + (t.state === 'soon' ? ' Inside the notice period: you can book it, the AI will not offer it.' : '')));
  if (t.left > 0) {
    const leads = await api('/calendar/leads?product=' + encodeURIComponent(t.product_id));
    if (!leads.length) kids.push(el('p', 'mute', 'No lead for this product is free to book. Add one in Leads.'));
    else { const what = pickList('Offering', t.slots, s => s.project), who = findList('Lead', leads, x => x.name + ', ' + x.phone, 'Type a name or phone'); kids.push(what, who, el('div', 'acts', btn('Book', () => who.get() ? act('/calendar/slots/' + what.get().id + '/book', {lead_id: who.get().id}) : $('derr').textContent = 'Choose a lead.', 'primary'))); }
  }
  kids.push(btn('Close this time', () => closeDialog(t.start.slice(0, 10), +t.start.slice(11, 13), t.product_id)));
  dialog(at(t.start), ...kids);
}
function closeDialog(day, h, pid) {
  const live = C.data.closures.filter(c => c.day === day && (h == null ? c.hour == null : c.hour === h)), reason = el('input'); reason.maxLength = 60; reason.placeholder = 'e.g. Diwali (optional)';
  const who = pickList('For', h == null ? [{id: '', company: 'All products'}, ...C.data.products] : C.data.products, p => p.company); if (pid) who.sel.value = C.data.products.findIndex(p => p.id === pid);
  dialog(h == null ? dayLong(day) : at(day + 'T' + String(h).padStart(2, '0')), ...live.map(c => el('div', 'line', el('span', '', c.product_id ? tag(c.product_id, company(c.product_id)) : 'All products', c.reason ? ', ' + c.reason : ''), btn('Reopen', () => act('/calendar/closures/' + c.id, null, 'DELETE')))),
    who, el('label', 'mute', 'Reason', reason), el('p', 'mute', 'No new bookings will be taken. Existing ones stay until you move or cancel them.'),
    el('div', 'acts', btn(h == null ? 'Close the day' : 'Close this time', () => act('/calendar/closures', {day, hour: h, product_id: who.get().id, reason: reason.value}), 'primary')));
}

async function bookingDialog(b) {
  const p = '/calendar/bookings/' + b.id + '/', kids = [el('p', '', dot(b.product_id), b.project + ', ' + at(b.start)), el('p', 'mute', b.company + '. Score ' + b.score + ' ' + b.band + '. ' + b.phone + (b.caller ? '. Booked by ' + b.caller + ' (AI)' : '. Booked by the team'))];
  if (b.summary) kids.push(el('p', '', b.summary));
  if (b.past) {
    kids.push(el('h3', '', b.status === 'attended' || b.status === 'no_show' ? 'Outcome: ' + ST[b.status] : 'What happened?'), b.rep ? el('p', 'mute', 'Assigned to ' + b.rep) : '',
      el('div', 'acts', btn('Attended', () => act(p + 'outcome', {status: 'attended'}), b.status === 'attended' ? 'primary' : ''), btn('No-show', () => act(p + 'outcome', {status: 'no_show'}), b.status === 'no_show' ? 'primary' : '')));
  } else {
    if (b.people.length) {
      const a = pickList('Assigned to', [{id: 0, name: 'Nobody yet'}, ...b.people], x => x.name + (x.busy ? ' (busy then)' : '') + (x.phone ? ', ' + x.phone : ''));
      [...a.sel.options].forEach((o, i) => o.disabled = i > 0 && b.people[i - 1].busy); a.sel.value = b.member_id ? 1 + b.people.findIndex(x => x.id === b.member_id) : 0;
      a.sel.onchange = () => act(p + 'assign', {member_id: a.get().id}); kids.push(a);
    } else kids.push(el('p', 'mute', 'Unassigned. Nobody on your team handles ' + b.company + ' yet. ', btn('Add someone', () => memberForm({products: [b.product_id]}), 'linkb')));
    const mv = el('div');
    kids.push(mv, el('div', 'acts', btn('Move', async () => {
      try {
        const open = await api('/calendar/open?product=' + encodeURIComponent(b.product_id));
        if (!open.length) return mv.replaceChildren(el('p', 'mute', 'There are no free times.'));
        const l = findList('Move to', open.filter(s => s.start !== b.start || s.project !== b.project), s => at(s.start) + ', ' + s.project, 'Type a day, time or offering');
        mv.replaceChildren(l, el('div', 'acts', btn('Confirm move', () => l.get() ? act(p + 'move', {slot_id: l.get().id}) : $('derr').textContent = 'Choose a time.', 'primary')));
      } catch (e) { $('derr').textContent = e.message; }
    }), btn('Cancel booking', async () => { if (await ask('Cancel this booking?', b.name + ', ' + at(b.start) + '. Nobody is notified, so tell them yourself.', 'Cancel booking', true)) api(p + 'cancel', {}).then(drawCalendar).catch(cerr); }, 'stop')),
      el('p', 'mute', 'Changes here are not sent to the customer or your team.'));
  }
  kids.push(btn('Open lead', async () => { $('dlg').close(); await showTab('L'); if (S.live) return fail('End the current call first.'); clearLeadFilters(); pick(b.lead_id); }));
  dialog(b.name, ...kids);
}

// ---------- booking rules ----------
async function rulesDialog(pid) {
  const list = C.data.products; if (!list.length) return cerr('Create a product first.');
  const id = pid || C.product || list[0].id, p = await api('/products/' + id), f = {days: [...p.days], hours: [...p.hours], minutes: p.minutes, spots: p.spots, horizon: p.horizon, notice: p.notice, version: p.version};
  const which = pickList('Product', list, x => x.company); which.sel.value = list.findIndex(x => x.id === id); which.sel.onchange = () => rulesDialog(which.get().id).catch(e => $('derr').textContent = e.message);
  const chips = (all, key, text) => el('div', 'chips', ...all.map(v => { const c = el('input'); c.type = 'checkbox'; c.checked = f[key].includes(v); c.onchange = () => f[key] = c.checked ? [...f[key], v].sort((a, b) => a - b) : f[key].filter(x => x !== v); return el('label', '', c, text(v)); }));
  const num = (label, key, min, max) => { const n = el('input'); n.type = 'number'; n.min = min; n.max = max; n.value = f[key]; n.oninput = () => f[key] = n.value === '' ? null : Math.round(+n.value); return el('label', 'mute', label, n); };
  dialog('Booking rules', which, p.rules_set ? '' : el('p', 'note', 'These are defaults. Save to make them yours.'), el('h3', '', 'Days'), chips([0, 1, 2, 3, 4, 5, 6], 'days', v => DAY[v]), el('h3', '', 'Start times'), chips([8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20], 'hours', hour),
    el('div', 'g2', num('Bookings at the same time', 'spots', 1, 50), num('Length in minutes', 'minutes', 10, 240), num('Open how many days ahead', 'horizon', 1, 60), num('Least notice, in hours', 'notice', 0, 168)),
    el('p', 'mute', 'The AI and your team use the same rules. Existing bookings never change.'), el('div', 'acts', btn('Save rules', () => act('/products/' + id + '/rules', f, 'PUT'), 'primary')));
}

// ---------- team ----------
function teamDialog() {
  const t = C.data.team;
  dialog('Team', el('p', 'mute', t.length ? 'People you can assign bookings to.' : 'Nobody yet. Add the people who take appointments. Bookings stay unassigned until you choose someone.'),
    ...t.map(m => el('div', 'line', el('div', '', el('div', '', m.name), el('div', 'mute ptags', m.phone || '', ...m.products.map(id => tag(id, company(id))))), btn('Edit', () => memberForm(m)))), btn('Add person', () => memberForm({products: C.data.products.length === 1 ? [C.data.products[0].id] : []}), 'primary'));
}
const reTeam = async () => { await drawCalendar(); teamDialog(); };
function memberForm(m) {
  const name = el('input'), phone = el('input'), has = new Set(m.products); name.value = m.name || ''; name.maxLength = 60; phone.value = m.phone || ''; phone.maxLength = 20;
  const body = () => ({name: name.value, phone: phone.value, products: [...has]});
  dialog(m.id ? 'Edit person' : 'Add person', el('label', 'mute', 'Name', name), el('label', 'mute', 'Phone (optional)', phone), el('p', 'mute', 'Handles'),
    el('div', 'chips', ...C.data.products.map(p => { const c = el('input'); c.type = 'checkbox'; c.checked = has.has(p.id); c.onchange = () => c.checked ? has.add(p.id) : has.delete(p.id); return el('label', '', c, tag(p.id, p.company)); })),
    el('div', 'acts', btn('Save', () => m.id ? act('/team/' + m.id, body(), 'PUT', reTeam) : act('/team', body(), 'POST', reTeam), 'primary'),
      m.id ? btn('Remove', async () => { if (await ask('Remove ' + m.name + '?', 'Their upcoming bookings become unassigned. Past bookings keep the name.', 'Remove', true)) api('/team/' + m.id, undefined, 'DELETE').then(reTeam).catch(cerr); }, 'stop') : ''));
}
setInterval(() => { if (!document.hidden && !$('calendarView').hidden && !$('dlg').open && document.activeElement?.type !== 'search') drawCalendar().catch(() => {}); }, 8000);
