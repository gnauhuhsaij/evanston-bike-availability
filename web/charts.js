/* Measured station availability and prospective probability-score charts. */
function lineChart(rows, series, empty) {
  if (!rows.length) return `<p class="chart-empty">${empty}</p>`;
  const width=320,height=105,left=25,right=7,top=8,bottom=20;
  const values=rows.flatMap(row=>series.map(item=>row[item.key]).filter(Number.isFinite));
  const max=Math.max(0.01,...values);
  const x=i=>left+(width-left-right)*(rows.length===1?.5:i/(rows.length-1));
  const y=v=>top+(height-top-bottom)*(1-v/max);
  const paths=series.map(item=>{
    const chunks=[];let points=[];
    rows.forEach((row,i)=>{const value=row[item.key];if(Number.isFinite(value)){points.push(`${x(i).toFixed(1)},${y(value).toFixed(1)}`);}else if(points.length){chunks.push(points);points=[];}});
    if(points.length)chunks.push(points);
    return chunks.map(chunk=>chunk.length===1?`<circle cx="${chunk[0].split(',')[0]}" cy="${chunk[0].split(',')[1]}" r="3" fill="${item.color}"/>`:`<polyline fill="none" stroke="${item.color}" stroke-width="2" points="${chunk.join(' ')}"/>`).join('');
  }).join('');
  const first=rows[0].time||rows[0].date,last=rows[rows.length-1].time||rows[rows.length-1].date;
  const label=value=>new Date(value).toLocaleDateString([], {month:'short',day:'numeric'});
  return `<svg class="line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(series.map(item=>item.label).join(' and '))} trend from ${esc(label(first))} to ${esc(label(last))}"><line x1="${left}" y1="${top}" x2="${left}" y2="${height-bottom}" stroke="#c8d5d0"/><line x1="${left}" y1="${height-bottom}" x2="${width-right}" y2="${height-bottom}" stroke="#c8d5d0"/><text x="1" y="${top+5}">${max.toFixed(max<1?2:0)}</text><text x="${left}" y="${height-3}">${esc(label(first))}</text><text x="${width-right}" y="${height-3}" text-anchor="end">${esc(label(last))}</text>${paths}</svg>`;
}
function renderCharts() {
  const host=$('history');if(!host)return;
  const switches=`<div class="chart-switch"><button type="button" data-window="day" class="${state.window==='day'?'active':''}">24 hours</button><button type="button" data-window="month" class="${state.window==='month'?'active':''}">30 days</button></div>`;
  if(!state.history){host.innerHTML=`<h3>Station history</h3>${switches}<p class="chart-empty">${state.historyError?'History unavailable; try again later.':'Loading measured history…'}</p>`;}
  else {
    const rows=state.history.availability||[];
    const availability=lineChart(rows,[{key:'bikes',label:'Bikes',color:'#0b7180'},{key:'docks',label:'Open docks',color:'#ba6c31'}],'No fresh station reports in this window.');
    const key=state.direction==='pickup'?'no_bike':'no_dock';
    const scores=(state.history.daily_accuracy||[]).map(day=>({date:day.date,model:day.targets[key].model?.brier??null,baseline:(day.targets[key].model_matched_persistence||day.targets[key].persistence)?.brier??null,n:day.targets[key].persistence?.n??0,modelN:day.targets[key].model?.n??0,partial:day.coverage?.initial_partial_day||day.coverage?.fraction<0.9}));
    const scoreRows=scores.filter(day=>day.n>0);
    const scoreChart=lineChart(scoreRows,[{key:'model',label:'Model Brier score',color:'#0b7180'},{key:'baseline',label:'Current-state baseline Brier score',color:'#a34238'}],'No complete daily model assessment for this station yet.');
    const last=scoreRows.at(-1);
    host.innerHTML=`<h3>Station history</h3>${switches}<div class="chart-title">Reported availability</div>${availability}<div class="chart-legend"><span><i style="background:#0b7180"></i>Bikes</span><span><i style="background:#ba6c31"></i>Open docks</span></div><p class="chart-caption">Available bikes and open docks, not ride counts. Gaps mean missing or stale reports.</p><div class="chart-title">Daily 30-minute risk score</div>${scoreChart}<div class="chart-legend"><span><i style="background:#0b7180"></i>Model, where available</span><span><i style="background:#a34238"></i>Current-state baseline</span></div><p class="chart-caption">Brier score: lower is better. Each point uses matched 30-minute outcomes.${last?` Latest day: ${last.n} baseline matches, ${last.modelN} model matches${last.partial?' (partial day)':''}.`:''} No model score is shown for stations without a deployed prediction.</p>`;
  }
  host.querySelectorAll('[data-window]').forEach(button=>button.addEventListener('click',()=>{state.window=button.dataset.window;state.history=null;state.historyError=false;renderCharts();loadHistory();}));
}
async function loadHistory() {
  const id=state.selected,windowName=state.window;if(!id)return;
  try{const response=await fetch(`/api/stations/${encodeURIComponent(id)}/history?window=${windowName}`,{cache:'no-store'});if(!response.ok)throw new Error('history');const data=await response.json();if(state.selected===id&&state.window===windowName){state.history=data;state.historyError=false;renderCharts();}}
  catch{if(state.selected===id){state.history=null;state.historyError=true;renderCharts();}}
}
