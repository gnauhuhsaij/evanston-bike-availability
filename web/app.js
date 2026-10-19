const state = { stations: [], direction: 'pickup', selected: null, markers: new Map(), map: null, window: 'day', history: null, historyError: false, expanded: false, fitted: false };
const $ = id => document.getElementById(id);
const downtown = {lat:42.0482,lon:-87.6835};
const mobileQuery = window.matchMedia('(max-width:800px)');
const isMobile = () => mobileQuery.matches;
function savedMapTheme(){try{return localStorage.getItem('dockside-map-theme');}catch{return null;}}
function setMapTheme(theme,save=false){
  const night=theme==='night';
  document.querySelector('.map-area').classList.toggle('map-night',night);
  document.querySelector('.layout').classList.toggle('night',night);
  $('map-day').setAttribute('aria-pressed',String(!night));
  $('map-night').setAttribute('aria-pressed',String(night));
  if(save)try{localStorage.setItem('dockside-map-theme',night?'night':'day');}catch{}
}
function distanceM(a,b){const lat=(a.lat-b.lat)*111200;const lon=(a.lon-b.lon)*111200*Math.cos(a.lat*Math.PI/180);return Math.hypot(lat,lon);}
function countFor(station) { return state.direction === 'pickup' ? station.bikes : station.docks; }
function usable(station) { return station.fresh && (state.direction === 'pickup' ? station.renting : station.returning); }
function classFor(station) { const n = countFor(station); return !usable(station) || !Number.isFinite(n) ? 'heat-stale' : n <= 0 ? 'heat-zero' : n <= 2 ? 'heat-low' : 'heat-ok'; }
function unitFor(n) { return state.direction==='pickup' ? (n===1?'bike':'bikes') : (n===1?'dock':'docks'); }
function stockTag(station) {
  if(!usable(station)||!Number.isFinite(countFor(station)))return '';
  const n=countFor(station);
  if(n<=0)return `<span class="tag tag-zero">${state.direction==='pickup'?'Empty':'Full'}</span>`;
  if(n<=2)return `<span class="tag tag-low">${state.direction==='pickup'?'Low':'Nearly full'}</span>`;
  return '';
}
function age(ts) { if (!ts) return 'No recent report'; const mins = Math.max(0, Math.floor((Date.now()/1000 - ts)/60)); return mins < 1 ? 'Updated just now' : `Updated ${mins} min ago`; }
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function record(event) { fetch('/api/event', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({event}), keepalive:true}).catch(()=>{}); }
function sheetPadding() { return isMobile() ? [0,0] : [Math.min(440, document.querySelector('.map-area').clientWidth*0.45), 0]; }
function panelPadding() { return isMobile() ? 0 : document.querySelector('.panel').getBoundingClientRect().width + 32; }
function setDirection(direction) { state.direction=direction; $('pickup').classList.toggle('active',direction==='pickup'); $('return').classList.toggle('active',direction==='return'); $('pickup').setAttribute('aria-pressed',direction==='pickup'); $('return').setAttribute('aria-pressed',direction==='return'); $('main-title').textContent=direction==='pickup'?'Find your next bike.':'Find an open dock.'; record(direction==='pickup'?'direction_pickup':'direction_return'); render(); }
function selectStation(id, track=true) {
  state.selected=id; state.history=null; state.historyError=false; state.expanded=false;
  if(track) record('station_select');
  render();
  const s=state.stations.find(x=>x.id===id);
  if(s&&state.map) state.map.panInside([s.lat,s.lon],{paddingTopLeft:[panelPadding()+60,70],paddingBottomRight:[sheetPadding()[0]+40,60]});
  if(track&&isMobile()) requestAnimationFrame(()=>$('detail').scrollIntoView({behavior:'smooth',block:'start'}));
}
function renderList() {
  const sorted=[...state.stations].sort((a,b)=>distanceM(a,downtown)-distanceM(b,downtown));
  $('station-list').innerHTML=sorted.map(s=>{
    const ok=usable(s)&&Number.isFinite(countFor(s));
    const n=ok?countFor(s):null;
    const sub=!s.fresh?'Status delayed':!usable(s)?'Currently unavailable':age(s.last_reported);
    return `<button type="button" class="station ${s.id===state.selected?'selected':''}" aria-pressed="${s.id===state.selected}" data-id="${esc(s.id)}"><i class="station-dot ${classFor(s)}" aria-hidden="true"></i><span class="station-text"><span class="station-name">${esc(s.name)}</span><span class="station-sub">${stockTag(s)}${sub}</span></span><span class="station-count ${classFor(s)}">${ok?n:'—'}<small>${unitFor(n)}</small></span></button>`;
  }).join('');
  document.querySelectorAll('.station').forEach(el=>el.addEventListener('click',()=>selectStation(el.dataset.id)));
}
function renderMapKey() { const unit=state.direction==='pickup'?'bikes':'docks'; $('map-key').innerHTML=`<span><i class="key-dot heat-zero"></i>0 ${unit}</span><span><i class="key-dot heat-low"></i>1–2 ${unit}</span><span><i class="key-dot heat-ok"></i>3+ ${unit}</span><span><i class="key-dot heat-stale"></i>No report</span>`; }
function renderMap() {
  if(!state.map)return;
  state.markers.forEach(marker=>state.map.removeLayer(marker));state.markers.clear();
  for(const s of state.stations){
    const n=usable(s)&&Number.isFinite(countFor(s))?countFor(s):'—';
    const klass=classFor(s)+(s.id===state.selected?' selected':'');
    const icon=L.divIcon({className:'',html:`<span class="station-marker ${klass}"><span class="station-core">${n}</span></span>`,iconSize:[36,36],iconAnchor:[18,18]});
    const marker=L.marker([s.lat,s.lon],{icon,zIndexOffset:s.id===state.selected?1000:0,title:s.name}).addTo(state.map);
    marker.getElement()?.setAttribute('aria-label',`${s.name}: ${n} ${unitFor(n)}`);
    marker.on('click',()=>selectStation(s.id));
    state.markers.set(s.id,marker);
  }
}
function fitToStations() {
  if(state.fitted||!state.map||!state.stations.length)return;
  state.map.invalidateSize();
  const bounds=L.latLngBounds(state.stations.map(s=>[s.lat,s.lon]));
  state.map.fitBounds(bounds,isMobile()?{paddingTopLeft:[50,56],paddingBottomRight:[20,24],maxZoom:16}:{paddingTopLeft:[panelPadding()+70,80],paddingBottomRight:[sheetPadding()[0]+40,60],maxZoom:16});
  state.fitted=true;
}
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
  const riskText=risk
    ?`<div class="risk"><span class="risk-num">${pct}</span><span class="risk-label">chance of ${event} in 30 minutes <span class="badge">Experimental</span></span></div>`
    :`<div class="risk risk-missing"><span class="risk-label">No 30-minute estimate for this station yet.</span></div>`;
  const modelInfo=state.model||{label:'Experimental model',caveat:'Prospective validation in progress'};
  const alternate=state.stations.filter(item=>item.id!==s.id&&usable(item)&&countFor(item)>=2&&distanceM(s,item)<1200).sort((a,b)=>distanceM(a,s)-distanceM(b,s))[0];
  const altHtml=alternate?`<button class="alternative" data-alt-id="${esc(alternate.id)}" type="button">See nearby option: ${esc(alternate.name)} (${Math.round(distanceM(s,alternate))} m away)</button>`:'';
  const previousScroll=$('detail').scrollTop;
  $('detail').innerHTML=`<h2>${esc(s.name)}</h2><div class="stock-labels"><span><i class="stock-key stock-key-bike"></i><strong>${bikes??'—'}</strong> bikes now</span><span><i class="stock-key stock-key-dock"></i><strong>${docks??'—'}</strong> open docks now</span></div>${bar}${riskText}<p class="detail-updated">${esc(age(s.last_reported))}${!s.fresh?' · Status delayed':''}</p><details class="station-more" ${state.expanded?'open':''}><summary>More station details</summary><div class="station-more-content"><p class="detail-caveat">${esc(modelInfo.label)} · ${esc(modelInfo.caveat)}. Check the official app before riding.</p><section id="history" class="history"></section>${altHtml}</div></details>`;
  $('detail').scrollTop=previousScroll;
  renderCharts();
  const more=$('detail').querySelector('.station-more');
  more.addEventListener('toggle',()=>{state.expanded=more.open;if(more.open&&!state.history&&!state.historyError)loadHistory();});
  const alt=$('detail').querySelector('.alternative');
  if(alt)alt.addEventListener('click',()=>selectStation(alt.dataset.altId));
}
function render(){renderList();renderMapKey();renderMap();renderDetail();$('station-total').textContent=`${state.stations.length} stations`;}
/* Desktop: map beside the list, station details as a sheet on the map's right.
   Mobile: toggle, then map, then selected station, then the full list. */
function placeBlocks(){
  const detail=$('detail'),mapArea=document.querySelector('.map-area'),layout=document.querySelector('.layout'),list=$('station-list');
  if(isMobile()){
    if(mapArea.previousElementSibling!==document.querySelector('.switch'))document.querySelector('.switch').after(mapArea);
    if(mapArea.nextElementSibling!==detail)mapArea.after(detail);
    detail.classList.add('detail-in-panel');
  }else{
    if(mapArea.parentElement!==layout)layout.append(mapArea);
    if(detail.parentElement!==mapArea)mapArea.append(detail);
    detail.classList.remove('detail-in-panel');
  }
  if(state.map)state.map.invalidateSize();
}
async function load(){try{const res=await fetch('/api/stations',{cache:'no-store'});if(!res.ok)throw new Error('feed');const data=await res.json();state.stations=data.stations;state.model=data.model||null;$('feed-status').textContent=data.error?'Feed update delayed; showing last report':data.last_success?`Feed checked ${new Date(data.last_success).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}`:'Waiting for station data';if(!state.selected&&state.stations.length){const preferred=state.stations.find(s=>s.name==='Benson Ave & Church St');state.selected=(preferred||state.stations[0]).id;}render();fitToStations();}catch{$('feed-status').textContent='Could not update stations; retrying';}}
if(window.L){state.map=L.map('map',{zoomControl:false,zoomSnap:0.25}).setView([42.046,-87.688],13);L.control.zoom({position:'topleft'}).addTo(state.map);L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,detectRetina:true,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'}).addTo(state.map);}else{$('map-fallback').hidden=false;}
$('pickup').setAttribute('aria-pressed','true');$('return').setAttribute('aria-pressed','false');$('pickup').addEventListener('click',()=>setDirection('pickup'));$('return').addEventListener('click',()=>setDirection('return'));
$('map-day').addEventListener('click',()=>setMapTheme('day',true));$('map-night').addEventListener('click',()=>setMapTheme('night',true));setMapTheme(savedMapTheme()||'day');
window.addEventListener('resize',placeBlocks);placeBlocks();record('page_view');load();setInterval(load,60000);
