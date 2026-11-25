const $=id=>document.getElementById(id);
const S={lead:null,call:null,live:false,mode:null,room:null,busy:false,logKey:'',wasLive:false};
// textContent only: transcripts and model output are never treated as HTML
function el(tag,cls,...kids){const e=document.createElement(tag);if(cls)e.className=cls;for(const k of kids)if(k!=null)e.append(k);return e}
async function api(path,body,method){const r=await fetch('/api'+path,body===undefined&&!method?{}:{method:method||'POST',headers:{'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  const j=await r.json().catch(()=>({}));if(!r.ok&&j.detail&&j.detail.findings){const e=new Error(j.detail.verdict==='block'?'Not saved: the safety check stopped it.':'Not saved yet: the safety check needs your decision.');e.safety=j.detail;throw e}if(!r.ok)throw new Error(typeof j.detail==='string'?j.detail:Array.isArray(j.detail)?j.detail.map(d=>(d.loc||[]).slice(-1)+': '+d.msg).join('. '):'Request failed ('+r.status+')');return j}
const fail=e=>{$('msg').textContent=e?(e.message||e):''};
// product colours: a circle always beside the product's name, never instead of it
const PC={};
function dot(id){const i=el('i','dot');i.style.background=PC[id]||'#9aa39d';i.setAttribute('aria-hidden','true');return i}
function tag(id,...t){return el('span','ptag',dot(id),...t)}
function tint(s){const f=()=>{const c=PC[s.value];s.classList.toggle('tint',!!c);s.style.setProperty('--pc',c||'transparent')};s.addEventListener('change',f);f();return f}
// saves that reach a prompt: shows why the safety check stopped it, and lets a person decide on warnings
async function safeSave(send){
  try{return await send(false)}
  catch(e){if(!e.safety)throw e;if(await safetyDialog(e.safety))return await send(true);throw e}
}
function safetyDialog(s){return new Promise(done=>{const d=el('dialog','safety'),end=v=>{d.close();d.remove();done(v)},block=s.verdict==='block';
  d.append(el('h2','',block?'Not saved: this could harm customers':'Check this before saving'),
    ...s.findings.map(f=>el('div','sf '+f.level,el('b','',f.title),el('div','mute','Found in '+f.field),el('blockquote','',f.quote),el('div','','Why it matters: '+f.why),el('div','','What to do: '+f.fix))),
    s.note?el('p','note',s.note):'',el('div','row'));
  const go=el('button',block?'primary':'','Go back and edit');go.type='button';go.onclick=()=>end(false);d.lastChild.append(go);
  if(!block){const y=el('button','stop','Save anyway');y.type='button';y.onclick=()=>end(true);d.lastChild.append(y)}
  d.oncancel=()=>end(false);document.body.append(d);d.showModal()})}
const list=a=>Array.isArray(a)?a:a?[a]:[];

const L={on:false,ids:new Set(),shown:[]};
let last=null,leadKey='';
const stage=s=>s==='Visit Scheduled'?'Booked':s;
async function refresh(){last=await api('/state');drawState()}
function drawState(){
  const d=last,s=d.stats;
  $('stats').replaceChildren(...[[s.leads,'leads'],[s.hot,'hot'],[s.visits,'upcoming bookings']]
    .map(([v,l])=>el('div','stat',el('b','',String(v)),el('span','',l))));
  const q=$('find').value.trim().toLowerCase(),digits=q.replace(/\D/g,'');
  const shown=sortLeads(d.leads.filter(l=>(!$('flt').value||l.product_id===$('flt').value)&&leadMatches(l)&&(!q||l.name.toLowerCase().includes(q)||(digits.length>2&&l.phone.includes(digits)))));
  $('clr').hidden=!['find','flt','fst','fbd'].some(i=>$(i).value);
  L.shown=shown;selBar();const top=shown.slice(0,100),key=JSON.stringify([top,S.lead,shown.length,L.on,[...L.ids]]);
  if(key!==leadKey){leadKey=key; // rebuild only when something changed, so focus and scroll stay put
    $('leads').replaceChildren(...top.map(l=>{const b=el('button','lead'+(l.id===S.lead?' on':''),el('div','row',el('span','',l.name),el('span',l.band,l.score+' '+l.band)),el('div','mute',tag(l.product_id,l.company+', '+(l.do_not_call?'Do not call':stage(l.stage)))));
      b.onclick=()=>{if(L.on)return toggleSel(l.id);if(S.live)return fail('End the current call first.');pick(l.id)};return leadRow(l,b)}),
      shown.length>100?el('p','mute','Showing 100 of '+shown.length+'. Search to narrow.'):shown.length?'':el('p','mute',d.leads.length?'No leads match.':'No leads yet. Add or import some.'))}
}
const leadMatches=l=>{const v=$('fst').value,now=l.do_not_call?'Do not call':stage(l.stage);
  return(!$('fbd').value||l.band===$('fbd').value)&&(!v||(v==='never'?!l.last_call:v==='cb'?/^CALL BACK/.test(l.next_action)&&!l.do_not_call:now===v))};
const sortLeads=a=>{const by={score:(x,y)=>y.score-x.score,new:(x,y)=>(y.created_at||'').localeCompare(x.created_at||''),recent:(x,y)=>(y.last_call||'').localeCompare(x.last_call||''),
  // people never called wait from the day they were added
  old:(x,y)=>(x.last_call||x.created_at||'').localeCompare(y.last_call||y.created_at||''),name:(x,y)=>x.name.localeCompare(y.name)};return[...a].sort((x,y)=>by[$('srt').value](x,y)||x.id-y.id)};
function clearLeadFilters(){for(const i of['find','flt','fst','fbd'])$(i).value='';retint()}
$('find').oninput=$('fst').onchange=$('fbd').onchange=$('srt').onchange=()=>{if(last)drawState();drawGone().catch(()=>{})};
$('clr').onclick=()=>{clearLeadFilters();if(last)drawState();drawGone().catch(()=>{})};
$('addt').onclick=()=>{$('add').hidden=!$('add').hidden;$('addt').setAttribute('aria-expanded',String(!$('add').hidden));if(!$('add').hidden)$('nm').focus()};
const FACT={item_interest:'Likes',objections:'Concerns',personal_notes:'Remember'};
const EVT={visit_booked:'Booked',visit_rescheduled:'Booking moved',visit_cancelled:'Booking cancelled',human_requested:'Asked for a person',opted_out:'Asked not to be called',pipeline_error:'Connection problem'};
async function poll(){
  if(!S.lead)return;
  const id=S.lead,l=await api('/leads/'+id);if(id!==S.lead)return;
  if(!S.call&&l.calls.length)S.call=l.calls[0].id;
  $('who').textContent=l.name;$('voice').disabled=$('text').disabled=S.live||S.busy||!!l.do_not_call;
  const c=S.call?await api('/calls/'+S.call):null;if(id!==S.lead)return;
  side(l,c);if(c)log(c);else $('log').replaceChildren(el('p','mute','No calls yet. Start one above.'));
  if(S.live&&c&&c.status!=='active')stopped();
}
function log(c){
  const key=c.id+'/'+c.turns.length+'/'+c.events.length+'/'+c.turns.map(t=>t.sentiment).join();if(key===S.logKey)return;S.logKey=key;
  const near=$('log').scrollHeight-$('log').scrollTop-$('log').clientHeight<140;
  const items=[...c.turns.map(t=>({at:t.created_at,o:0,n:el('div','t '+t.role,t.text)})),
    ...c.events.filter(e=>EVT[e.type]).map(e=>{const d=JSON.parse(e.detail);return{at:e.created_at,o:-1,n:el('div','ev'+(/^visit_(booked|res)/.test(e.type)?' good':/failed|error/.test(e.type)?' bad':''),(EVT[e.type]||e.type)+(d.when?': '+d.project+', '+d.when:d.reason?': '+d.reason:''))}})];
  items.sort((a,b)=>a.at<b.at?-1:a.at>b.at?1:a.o-b.o);
  $('log').replaceChildren(...items.map(i=>i.n));if(near)$('log').scrollTop=1e9;
}
function side(l,c){ // only what a salesperson needs before or during the call
  const n=[el('h2','',l.name),el('div','',l.phone),el('div','mute',tag(l.product_id,l.product.company+', '+stage(l.stage).toLowerCase())),l.caller?el('div','mute','AI caller: '+l.caller):''];
  if(l.do_not_call)n.push(el('p','err','Asked not to be called.'));
  if(/^CALL BACK/.test(l.next_action)&&!l.do_not_call)n.push(el('div','visit',el('b','','Call back requested'),el('div','mute',l.next_action.replace(/^CALL BACK:?\s*/,''))));
  if(l.visit){const a=el('a','','Add to calendar');a.href='/api/bookings/'+l.visit.id+'.ics';n.push(el('div','visit',el('b','',l.product.appointment[0].toUpperCase()+l.product.appointment.slice(1)),el('div','',l.visit.project+', '+l.visit.when),el('div','mute',l.visit.rep?'With '+l.visit.rep:'Nobody assigned yet'),a))}
  const why=el('details','',el('summary','mute','Why this score'));why.open=S.why;why.ontoggle=()=>S.why=why.open;
  for(const p of l.score_parts){const r=el('div','row',el('span','mute',p.label),el('span','',p.points+' of '+p.max));why.append(r)}
  n.push(el('h3','','Score'),el('div','row',el('span','big',String(l.score)),el('span',l.band,l.band)),l.score_parts.length?why:'');
  const f=Object.entries(l.facts).filter(([k])=>l.product.labels[k]||FACT[k]);
  if(f.length)n.push(el('h3','','What we know'),el('dl','',...f.flatMap(([k,v])=>[el('dt','',l.product.labels[k]||FACT[k]),el('dd','',Array.isArray(v)?v.join('; '):String(v))])));
  if(l.context)n.push(el('h3','','Notes'),el('div','',l.context));
  if(c&&c.status==='ended')n.push(el('h3','','Call summary'),el('div','mute','Writing the summary…'));
  if(c&&c.status==='failed'){const b=el('button','','Try again');b.onclick=()=>api('/calls/'+c.id+'/reanalyze',{}).then(poll).catch(fail);n.push(el('h3','','Call summary'),el('div','mute','Could not write the summary.'),b)}
  if(c&&c.analysis&&c.analysis.summary)n.push(el('h3','','Call summary'),el('div','',c.analysis.summary));
  if(l.calls.length>1){const s=el('select');s.setAttribute('aria-label','Choose a call');s.disabled=S.live;for(const k of l.calls){const o=el('option','',new Date(k.started_at).toLocaleString('en-IN',{day:'numeric',month:'short',hour:'numeric',minute:'2-digit'})+', '+k.mode+(k.caller?', '+k.caller:''));o.value=k.id;s.append(o)}s.value=S.call;s.onchange=()=>{S.call=+s.value;S.logKey='';poll().catch(fail)};n.push(el('h3','','Calls'),s)}
  const key=JSON.stringify([l,c&&c.status,c&&c.analysis,S.call,S.live]);if(key===S.sideKey)return;S.sideKey=key;
  $('side').replaceChildren(...n);
}
function ui(live,mode){S.live=live;S.mode=mode;$('end').hidden=!live;$('mute').hidden=!(live&&mode==='voice');$('voice').hidden=$('text').hidden=live;$('send').style.display=live&&mode==='text'?'flex':'none';if(!live)$('status').textContent=''}
function stopped(){const r=S.room;S.room=null;ui(false,null);if(r)r.disconnect();document.querySelectorAll('audio.agent').forEach(a=>a.remove());refresh().catch(()=>{})}
async function start(mode){
  if(S.busy||S.live)return;S.busy=true;fail();$('status').textContent='Connecting…';$('voice').disabled=$('text').disabled=true;
  let d;
  try{
    if(mode==='voice'){if(!window.LivekitClient)throw new Error('Voice library did not load. Check the internet connection.');
      const mic=await navigator.mediaDevices.getUserMedia({audio:true}).catch(()=>{throw new Error('Allow microphone access to start a voice call.')});mic.getTracks().forEach(t=>t.stop())}
    d=await api('/calls',{lead_id:S.lead,mode});S.lead=d.lead_id;S.call=d.call_id;S.logKey='';
    ui(true,mode);
    if(mode==='voice'){
      const L=LivekitClient,room=S.room=new L.Room({audioCaptureDefaults:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      room.on(L.RoomEvent.TrackSubscribed,t=>{if(t.kind==='audio'){const a=t.attach();a.className='agent';document.body.append(a)}});
      room.on(L.RoomEvent.ParticipantAttributesChanged,(_,p)=>{const s=p.attributes['lk.agent.state'];if(s)$('status').textContent={listening:'Listening',thinking:'Thinking',speaking:'Speaking',initializing:'Connecting…'}[s]||s});
      room.on(L.RoomEvent.Disconnected,()=>{if(S.room===room){api('/calls/'+d.call_id+'/end',{}).catch(()=>{});stopped()}});
      await room.connect(d.url,d.token);await room.localParticipant.setMicrophoneEnabled(true);await room.startAudio();$('mute').textContent='Mute';
      setTimeout(()=>{if(S.room===room&&room.remoteParticipants.size===0)fail('The voice agent has not joined. Check that the worker is running.')},12000);
    }else{$('status').textContent='Text chat';$('say').focus()}
  }catch(e){fail(e);if(d)api('/calls/'+d.call_id+'/end',{}).catch(()=>{});stopped();$('status').textContent=''}
  finally{S.busy=false;poll().catch(fail);refresh().catch(fail)}
}
$('voice').onclick=()=>start('voice');$('text').onclick=()=>start('text');
$('end').onclick=async()=>{const id=S.call;stopped();try{await api('/calls/'+id+'/end',{})}catch(e){fail(e)}poll().catch(fail)};
$('mute').onclick=async()=>{const p=S.room.localParticipant,on=p.isMicrophoneEnabled;await p.setMicrophoneEnabled(!on);$('mute').textContent=on?'Unmute':'Mute'};
$('send').onsubmit=async e=>{e.preventDefault();const v=$('say').value.trim(),b=$('send').querySelector('button');if(!v||b.disabled)return;b.disabled=true;$('say').value='';fail();$('status').textContent='Thinking';
  try{await api('/calls/'+S.call+'/message',{text:v})}catch(x){fail(x)}b.disabled=false;if(S.live)$('status').textContent='Text chat';poll().catch(fail)};
function pick(id){S.lead=id;S.call=null;S.logKey='';fail();poll().catch(fail);refresh().catch(fail)}
async function loadProducts(){const ps=await api('/products'),o=(v,t)=>{const e=el('option','',t);e.value=v;return e},keep=$('prod').value,f=$('flt').value;
  $('prod').replaceChildren(...ps.map(p=>o(p.id,p.company+': '+p.topic)));if(ps.some(p=>p.id===keep))$('prod').value=keep;
  $('flt').replaceChildren(o('','All products'),...ps.map(p=>o(p.id,p.company)));if(ps.some(p=>p.id===f))$('flt').value=f;$('addb').disabled=!ps.length;for(const p of ps)PC[p.id]=p.color;retint();leadKey=S.sideKey='';if(last)drawState()}
const tintA=tint($('prod')),tintB=tint($('flt'));function retint(){tintA();tintB()}
$('flt').onchange=()=>{if(last)drawState();drawGone().catch(()=>{})};
$('add').onsubmit=async e=>{e.preventDefault();if(S.live)return fail('End the current call first.');fail();$('addb').disabled=true;
  try{const p=$('prod').value,r=await safeSave(confirm=>api('/leads',{name:$('nm').value,phone:$('ph').value,product_id:p,context:$('ctx').value,confirm}));e.target.reset();$('prod').value=p;clearLeadFilters();pick(r.id)}catch(x){fail(x)}
  $('addb').disabled=false};
addEventListener('load',()=>{loadProducts().catch(fail);refresh().catch(fail);drawGone().catch(fail)});
setInterval(()=>{if(document.hidden)return;poll().catch(()=>{});},1200);setInterval(()=>{if(!document.hidden)refresh().catch(()=>{})},4000);
