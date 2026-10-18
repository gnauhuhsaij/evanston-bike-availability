/* Measured station availability and prospective probability-score charts. */
function grid(width,height,left,right,top,bottom,maxLabel){
  const base=height-bottom,mid=(top+base)/2;
  return `<g class="chart-grid"><line x1="${left}" y1="${top}" x2="${width-right}" y2="${top}"/><line x1="${left}" y1="${mid}" x2="${width-right}" y2="${mid}"/></g><line x1="${left}" y1="${base}" x2="${width-right}" y2="${base}" stroke="#b9c5ca"/><text x="${left-5}" y="${top+4}" text-anchor="end">${maxLabel}</text><text x="${left-5}" y="${base+3}" text-anchor="end">0</text>`;
}
function lineChart(rows, series, empty) {
  if (!rows.length) return `<p class="chart-empty">${empty}</p>`;
  const width=320,height=110,left=32,right=8,top=8,bottom=22;
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
  return `<svg class="line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(series.map(item=>item.label).join(' and '))} trend from ${esc(label(first))} to ${esc(label(last))}">${grid(width,height,left,right,top,bottom,max.toFixed(max<1?2:0))}<text x="${left}" y="${height-3}">${esc(label(first))}</text><text x="${width-right}" y="${height-3}" text-anchor="end">${esc(label(last))}</text>${paths}</svg>`;
}
function stackedAreaChart(rows, empty) {
  const valid=rows.map((row,i)=>({i,bikes:row.bikes,docks:row.docks})).filter(point=>Number.isFinite(point.bikes)&&Number.isFinite(point.docks)&&point.bikes>=0&&point.docks>=0);
  if(!valid.length)return `<p class="chart-empty">${empty}</p>`;
  const width=320,height=118,left=32,right=8,top=8,bottom=22;
  const max=Math.max(1,...valid.map(point=>point.bikes+point.docks));
  const x=i=>left+(width-left-right)*(rows.length===1?.5:i/(rows.length-1));
  const y=value=>top+(height-top-bottom)*(1-value/max);
  const point=(i,value)=>`${x(i).toFixed(1)},${y(value).toFixed(1)}`;
  const chunks=[];let chunk=[];
  rows.forEach((row,i)=>{
    if(Number.isFinite(row.bikes)&&Number.isFinite(row.docks)&&row.bikes>=0&&row.docks>=0)chunk.push({i,bikes:row.bikes,docks:row.docks});
    else if(chunk.length){chunks.push(chunk);chunk=[];}
  });
  if(chunk.length)chunks.push(chunk);
  const areas=chunks.map(points=>{
    if(points.length===1){const p=points[0];return `<circle cx="${x(p.i)}" cy="${y(p.bikes)}" r="2.5" fill="#00a6d8"/><circle cx="${x(p.i)}" cy="${y(p.bikes+p.docks)}" r="2.5" fill="#929da3"/>`;}
    const bikesTop=points.map(p=>point(p.i,p.bikes));
    const totalTop=points.map(p=>point(p.i,p.bikes+p.docks));
    const baseline=points.slice().reverse().map(p=>point(p.i,0));
    const bikesBack=points.slice().reverse().map(p=>point(p.i,p.bikes));
    return `<path d="M${bikesTop.join(' L')} L${baseline.join(' L')} Z" fill="#00a6d8" fill-opacity=".72"/><path d="M${totalTop.join(' L')} L${bikesBack.join(' L')} Z" fill="#929da3" fill-opacity=".78"/><polyline points="${bikesTop.join(' ')}" fill="none" stroke="#007aa8" stroke-width="1.4"/><polyline points="${totalTop.join(' ')}" fill="none" stroke="#69757b" stroke-width="1.2"/>`;
  }).join('');
  const first=rows[0].time||rows[0].date,last=rows[rows.length-1].time||rows[rows.length-1].date;
  const label=value=>state.window==='day'?new Date(value).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}):new Date(value).toLocaleDateString([], {month:'short',day:'numeric'});
  return `<svg class="area-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Stacked reported availability: blue bikes below gray open docks, from ${esc(label(first))} to ${esc(label(last))}; gaps are missing reports">${grid(width,height,left,right,top,bottom,max)}<text x="${left}" y="${height-3}">${esc(label(first))}</text><text x="${width-right}" y="${height-3}" text-anchor="end">${esc(label(last))}</text>${areas}</svg>`;
}
function renderCharts() {
  const host=$('history');if(!host)return;
  const switches=`<div class="chart-switch"><button type="button" data-window="day" class="${state.window==='day'?'active':''}">24 hours</button><button type="button" data-window="month" class="${state.window==='month'?'active':''}">30 days</button></div>`;
  if(!state.history){host.innerHTML=`<h3>Station history</h3>${switches}<p class="chart-empty">${state.historyError?'History unavailable; try again later.':'Loading measured history…'}</p>`;}
  else {
    const rows=state.history.availability||[];
    const availability=stackedAreaChart(rows,'No fresh station reports in this window.');
    const key=state.direction==='pickup'?'no_bike':'no_dock';
    const scores=(state.history.daily_accuracy||[]).map(day=>({date:day.date,model:day.targets[key].model?.brier??null,baseline:(day.targets[key].model_matched_persistence||day.targets[key].persistence)?.brier??null,n:day.targets[key].persistence?.n??0,modelN:day.targets[key].model?.n??0,partial:day.coverage?.initial_partial_day||day.coverage?.fraction<0.9}));
    const scoreRows=scores.filter(day=>day.n>0);
    const scoreChart=lineChart(scoreRows,[{key:'model',label:'Model Brier score',color:'#007aa8'},{key:'baseline',label:'Current-state baseline Brier score',color:'#929da3'}],'No complete daily model assessment for this station yet.');
    const last=scoreRows.at(-1);
    host.innerHTML=`<h3>Station history</h3>${switches}<div class="chart-title">Reported availability</div>${availability}<div class="chart-legend"><span><i style="background:#00a6d8"></i>Bikes (lower area)</span><span><i style="background:#929da3"></i>Open docks (upper area)</span></div><p class="chart-caption">Stacked height is bikes plus open docks, not ride counts. Gaps mean missing or stale reports.</p><div class="chart-title">Daily 30-minute risk score</div>${scoreChart}<div class="chart-legend"><span><i style="background:#007aa8"></i>Model, where available</span><span><i style="background:#929da3"></i>Current-state baseline</span></div><p class="chart-caption">Brier score: lower is better. Each point uses matched 30-minute outcomes.${last?` Latest day: ${last.n} baseline matches, ${last.modelN} model matches${last.partial?' (partial day)':''}.`:''} No model score is shown for stations without a deployed prediction.</p>`;
  }
  host.querySelectorAll('[data-window]').forEach(button=>button.addEventListener('click',()=>{state.window=button.dataset.window;state.history=null;state.historyError=false;renderCharts();loadHistory();}));
}
async function loadHistory() {
  const id=state.selected,windowName=state.window;if(!id)return;
  try{const response=await fetch(`/api/stations/${encodeURIComponent(id)}/history?window=${windowName}`,{cache:'no-store'});if(!response.ok)throw new Error('history');const data=await response.json();if(state.selected===id&&state.window===windowName){state.history=data;state.historyError=false;renderCharts();}}
  catch{if(state.selected===id){state.history=null;state.historyError=true;renderCharts();}}
}
