const state = { stations: [], direction: 'pickup', selected: null, markers: new Map(), map: null, window: 'day', history: null, historyError: false, expanded: false };
const $ = id => document.getElementById(id);
const downtown = {lat:42.0482,lon:-87.6835};
function distanceM(a,b){const lat=(a.lat-b.lat)*111200;const lon=(a.lon-b.lon)*111200*Math.cos(a.lat*Math.PI/180);return Math.hypot(lat,lon);}
function countFor(station) { return state.direction === 'pickup' ? station.bikes : station.docks; }
function usable(station) { return station.fresh && (state.direction === 'pickup' ? station.renting : station.returning); }
function classFor(station) { const n = countFor(station); return !usable(station) || n === 0 ? 'unavailable' : n <= 2 ? 'limited' : 'available'; }
function age(ts) { if (!ts) return 'No recent report'; const mins = Math.max(0, Math.floor(Date.now()/1000 - ts)/60); return mins < 1 ? 'Updated just now' : `Updated ${Math.round(mins)} min ago`; }
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function record(event) { fetch('/api/event', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({event}), keepalive:true}).catch(()=>{}); }
function setDirection(direction) { state.direction=direction; $('pickup').classList.toggle('active',direction==='pickup'); $('return').classList.toggle('active',direction==='return'); record(direction==='pickup'?'direction_pickup':'direction_return'); render(); }
function selectStation(id, track=true) { state.selected=id; state.history=null; state.historyError=false; state.expanded=false; if(track) record('station_select'); render(); const s=state.stations.find(x=>x.id===id); if(s&&state.map) state.map.panTo([s.lat,s.lon]); }
function renderList() { const sorted=[...state.stations].sort((a,b)=>distanceM(a,downtown)-distanceM(b,downtown)); $('station-list').innerHTML=sorted.map(s=>`<button type="button" class="station ${s.id===state.selected?'selected':''}" data-id="${esc(s.id)}"><span><span class="station-name">${esc(s.name)}</span><span class="station-sub">${!s.fresh?'Status delayed':!usable(s)?'Currently unavailable':age(s.last_reported)}</span></span><span class="station-count ${countFor(s)<=2?'low':''}">${usable(s)?countFor(s):'—'}<small>${state.direction==='pickup'?(countFor(s)===1?'bike':'bikes'):(countFor(s)===1?'dock':'docks')}</small></span></button>`).join(''); document.querySelectorAll('.station').forEach(el=>el.addEventListener('click',()=>selectStation(el.dataset.id))); }
function renderMap() { if(!state.map)return; state.markers.forEach(marker=>state.map.removeLayer(marker));state.markers.clear(); for(const s of state.stations){const n=usable(s)?countFor(s):'—'; const klass=classFor(s)+(s.id===state.selected?' selected':'');const icon=L.divIcon({className:'',html:`<span class="station-marker ${klass}">${n}</span>`,iconSize:[45,45],iconAnchor:[22,22]}); const marker=L.marker([s.lat,s.lon],{icon}).addTo(state.map);marker.on('click',()=>selectStation(s.id));state.markers.set(s.id,marker);} }
function renderDetail() {
  const s=state.stations.find(item=>item.id===state.selected);
  if(!s){$('detail').innerHTML='<p class="detail-hint">Choose a station on the map or list.</p>';return;}
  const bikes=s.fresh&&s.renting&&Number.isFinite(s.bikes)?s.bikes:null;
  const docks=s.fresh&&s.returning&&Number.isFinite(s.docks)?s.docks:null;
  const total=(bikes??0)+(docks??0);
  const bikeWidth=total?100*(bikes??0)/total:0;
  const dockWidth=total?100*(docks??0)/total:0;
  const barLabel=`${bikes??'unavailable'} bikes and ${docks??'unavailable'} open docks`;
  const bar=`<div class="stock-bar" role="img" aria-label="${esc(barLabel)}"><span class="stock-bikes" style="width:${bikeWidth}%"></span><span class="stock-docks" style="width:${dockWidth}%"></span></div>`;
  const risk=s.experimental_risk;
  const key=state.direction==='pickup'?'no_bike_30m':'no_dock_30m';
  const event=state.direction==='pickup'?'no bikes':'no open docks';
  const pct=risk?(risk[key]>0&&risk[key]<.01?'<1%':`${Math.round(risk[key]*100)}%`):null;
  const riskText=risk?`<p class="risk-compact"><strong>${pct}</strong> estimated chance of ${event} in 30 minutes <span>· experimental</span></p>`:`<p class="risk-compact risk-missing">No 30-minute estimate for this station yet.</p>`;
  const caveat=state.direction==='pickup'?'2026 validation pending':'Very few historical no-dock events';
  const alternate=state.stations.filter(item=>item.id!==s.id&&usable(item)&&countFor(item)>=2&&distanceM(s,item)<1200).sort((a,b)=>distanceM(a,s)-distanceM(b,s))[0];
  const altHtml=alternate?`<button class="alternative" data-alt-id="${esc(alternate.id)}" type="button">Nearby option: ${esc(alternate.name)} · ${Math.round(distanceM(s,alternate))} m away →</button>`:'';
  const previousScroll=$('detail').scrollTop;
  $('detail').innerHTML=`<h2>${esc(s.name)}</h2><div class="stock-labels"><span><i class="stock-key stock-key-bike"></i><strong>${bikes??'—'}</strong> bikes now</span><span><i class="stock-key stock-key-dock"></i><strong>${docks??'—'}</strong> open docks now</span></div>${bar}${riskText}<p class="detail-updated">${esc(age(s.last_reported))}${!s.fresh?' · Status delayed':''}</p><details class="station-more" ${state.expanded?'open':''}><summary>More station details</summary><div class="station-more-content"><p class="detail-caveat">Experimental 2022 model · ${caveat}. Check the official app before riding.</p><section id="history" class="history"></section>${altHtml}</div></details>`;
  $('detail').scrollTop=previousScroll;
  renderCharts();
  const more=$('detail').querySelector('.station-more');
  more.addEventListener('toggle',()=>{state.expanded=more.open;if(more.open&&!state.history&&!state.historyError)loadHistory();});
  const alt=$('detail').querySelector('.alternative');
  if(alt)alt.addEventListener('click',()=>selectStation(alt.dataset.altId));
}
function render(){renderList();renderMap();renderDetail();$('station-total').textContent=`${state.stations.length} stations`;}
async function load(){try{const res=await fetch('/api/stations',{cache:'no-store'});const data=await res.json();state.stations=data.stations;$('feed-status').textContent=data.last_success?`Evanston · Updated ${new Date(data.last_success).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}`:'Waiting for live station data';if(data.error&&!data.last_success)$('feed-status').textContent='Live feed unavailable; retrying';if(!state.selected&&state.stations.length){const preferred=state.stations.find(s=>s.name==='Benson Ave & Church St');state.selected=(preferred||state.stations[0]).id;}render();}catch{$('feed-status').textContent='Could not load station data; retrying';}}
if(window.L){state.map=L.map('map',{zoomControl:false}).setView([42.046,-87.688],13);L.control.zoom({position:'bottomright'}).addTo(state.map);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,attribution:'© OpenStreetMap contributors'}).addTo(state.map);}else{$('map-fallback').hidden=false;}
$('pickup').addEventListener('click',()=>setDirection('pickup'));$('return').addEventListener('click',()=>setDirection('return'));record('page_view');load();setInterval(load,60000);
