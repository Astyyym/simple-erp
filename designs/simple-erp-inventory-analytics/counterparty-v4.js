import {initEntryFlows} from './counterparty-v4-entry.mjs';
import {roundRatio} from './counterparty-v4-engine.mjs';
/* v4 prototype only. No fetch, ERP API, database or persistent business writes.
   Amounts are integer cents; quantities are integer thousandths. */
(() => {
  "use strict";
  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];
  const esc = v => String(v).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
  const money = c => `${c < 0 ? "−" : ""}¥${(Math.abs(c) / 100).toLocaleString("zh-CN", {minimumFractionDigits:2, maximumFractionDigits:2})}`;
  const quantity = q => (q / 1000).toLocaleString("zh-CN", {maximumFractionDigits:3});
  const sum = (rows, fn) => rows.reduce((n, row) => n + fn(row), 0);
  const types = {
    sale: {name:"销售单", direction:"本店卖出", sign:1},
    sale_return: {name:"客户退货单", direction:"对方退给本店", sign:-1},
    purchase: {name:"拿货单", direction:"本店买入", sign:-1},
    purchase_return: {name:"退拿货单", direction:"本店退给对方", sign:1}
  };
  const parties = [{id:"1", name:"演示往来对象甲"}, {id:"2", name:"演示往来对象乙"}, {id:"3", name:"演示往来对象丙（无交易）"}];
  const products = [
    {id:"p1", name:"消防水带", spec:"DN65", unit:"卷", stock:22000, safe:10000, movingCost:8000},
    {id:"p2", name:"消防水带", spec:"DN80", unit:"卷", stock:8000, safe:10000, movingCost:9600},
    {id:"p3", name:"室内消火栓", spec:"SN65", unit:"个", stock:0, safe:5000, movingCost:null},
    {id:"p4", name:"消防应急灯", spec:"", unit:"套", stock:null, safe:3000, movingCost:null},
    {id:"p5", name:"耐火电缆", spec:"NH-BV", unit:"米", stock:135500, safe:50000, movingCost:120}
  ];
  const product = id => products.find(p => p.id === id);
  const partyName = id => parties.find(p => p.id === id)?.name || "全部往来对象";
  const line = (id, qty, price, cost = null) => ({productId:id, qty, price, amount:Math.round(qty * price / 1000), cost:cost === null ? null : Math.round(qty * cost / 1000)});
  const doc = (type, id, party, date, lines) => ({
    key:`${type}:${id}`, type, id, party:String(party), date, lines,
    no:`${{sale:"MD", sale_return:"MD", purchase:"NH", purchase_return:"TN"}[type]}${date.replaceAll("-", "")}${String(type === "sale_return" ? id + 20 : id).padStart(4,"0")}`,
    amount:sum(lines, l => l.amount), status:"saved"
  });
  // sale:1 and purchase:1 deliberately collide numerically, never by typed key.
  const coreDocs = [
    doc("sale",1,1,"2026-09-05",[line("p1",10000,12000,7200),line("p3",2000,8600,6000)]),
    doc("purchase",1,1,"2026-09-04",[line("p1",20000,7200),line("p2",10000,9600)]),
    doc("sale_return",1,1,"2026-09-08",[line("p1",2000,12000,7200)]),
    doc("purchase_return",1,1,"2026-09-09",[line("p1",2000,7200)]),
    doc("sale",2,1,"2026-09-10",[line("p5",12500,200,120)]),
    doc("purchase",2,1,"2026-09-11",[line("p5",50000,120)]),
    doc("sale",3,1,"2026-09-17",[line("p2",2000,15000,9600)]),
    doc("sale_return",2,1,"2026-09-18",[line("p3",1000,8600,6000)]),
    doc("purchase_return",2,1,"2026-09-20",[line("p5",10000,120)]),
    doc("sale",4,1,"2026-10-01",[line("p1",1000,12000,7200)]),
    doc("sale",5,2,"2026-09-12",[line("p2",3000,15000,9600)]),
    doc("purchase",3,2,"2026-09-14",[line("p3",10000,6000)]),
    doc("sale_return",3,2,"2026-10-01",[line("p2",1000,15000,9600)])
  ];
  const unknownDoc = doc("sale",6,1,"2026-09-23",[line("p4",2000,3800)]);
  const fractionDoc = doc("sale",7,1,"2026-09-25",[line("p3",1500,8600,6000)]);
  for(let i=0;i<54;i++)coreDocs.push(doc('sale',100+i,1,'2026-10-02',[line(i%2?'p2':'p1',1000,12000,i%2?9600:7200)]));
  coreDocs.push(doc('purchase',99,'','2026-09-02',[line('p1',1000,7200)]));
  for(const d of [...coreDocs,unknownDoc,fractionDoc])d.lines.forEach((l,i)=>l.id=`${d.key}:l${i+1}`);
  for(const d of coreDocs.filter(d=>d.type==='purchase_return')) {
    d.sourceKey=`purchase:${d.id}`;
    d.lines.forEach((l,i)=>l.sourceLineId=`${d.sourceKey}:l${i+1}`);
  }
  products.forEach(p=>p.costMicro=p.stock===null?null:p.stock===0?0:p.stock*p.movingCost*10);
  const baselineProducts=structuredClone(products);
  const defaults = {start:"2026-09-01", end:"2026-10-02", party:"", name:"", spec:""};
  const pageSize = 50;
  const state = {
    sample:"standard", analysis:{...defaults}, tab:"heat", heatDay:"", rows:[], sales:[],extraDocs:[],
    recon:null, scope:[], selected:new Set(), page:1, dirty:false, preview:[],
    scroll:{analytics:0, reconciliation:0, pdf:0,purchase:0,purchaseReturn:0}, screen:"analytics"
  };
  let entryFlows=null;
  function allDocuments(){return [...(state.sample==='empty'?[]:coreDocs),...(state.sample==='unknown'?[unknownDoc]:[]),...(state.sample==='fraction'?[fractionDoc]:[]),...state.extraDocs];}
  function docs() {
    return allDocuments().filter(d=>['saved','printed'].includes(d.status)&&!d.deleted);
  }
  function stockStatus(p) { return p.stock === null ? "未启用 / 未知" : p.stock === 0 ? "缺货" : p.stock <= p.safe ? "库存告急" : "正常"; }
  function statusClass(p) { return p.stock === null ? "warning" : p.stock <= p.safe ? "negative" : "good"; }
  function selectedDocs() { return state.scope.filter(d => state.selected.has(d.key)); }
  function selectedProducts() { return products.filter(p => (!state.analysis.name || p.name === state.analysis.name) && (!state.analysis.spec || (state.analysis.spec === "__empty" ? p.spec === "" : p.spec === state.analysis.spec))); }
  function salesLines() {
    const ids = new Set(selectedProducts().map(p => p.id));
    return docs().filter(d => ["sale","sale_return"].includes(d.type) && (!state.analysis.start || d.date >= state.analysis.start) && (!state.analysis.end || d.date <= state.analysis.end) && (!state.analysis.party || d.party === state.analysis.party))
      .flatMap(d => d.lines.filter(l => ids.has(l.productId)).map(l => ({...l, date:d.date, sign:types[d.type].sign, type:d.type, docKey:d.key})));
  }
  function stats(lines) {
    const sales = sum(lines.filter(l => l.sign === 1), l => l.amount);
    const returns = sum(lines.filter(l => l.sign === -1), l => l.amount);
    const unknown = lines.filter(l => l.cost === null).length;
    const net = sales - returns;
    const cost = sum(lines, l => (l.cost || 0) * l.sign);
    const profit = unknown ? null : net - cost;
    const margin = profit !== null && net > 0 ? profit / net * 100 : null;
    const units = new Set(lines.map(l => product(l.productId).unit));
    return {sales, returns, net, unknown, cost, profit, margin, units, qty:sum(lines,l => l.qty * l.sign)};
  }
  function quantitySummary(s) {
    if (!s.units.size) return "—";
    return s.units.size > 1 ? "混合单位 · 不合计" : `${quantity(s.qty)} ${[...s.units][0]}`;
  }
  function populateFilters() {
    const options = parties.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
    $("#analysisParty").insertAdjacentHTML("beforeend",options);
    $("#reconParty").insertAdjacentHTML("beforeend",options);
    $("#analysisProduct").insertAdjacentHTML("beforeend",[...new Set(products.map(p => p.name))].map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join(""));
  }
  function updateSpecs() {
    const name = $("#analysisProduct").value;
    $("#analysisSpec").disabled = !name;
    $("#analysisSpec").innerHTML = '<option value="">全部型号</option>' + products.filter(p => p.name === name).map(p => `<option value="${esc(p.spec || "__empty")}">${esc(p.spec || "未填写型号")}</option>`).join("");
  }
  function validateDates(start,end) {
    for(const value of [start,end])if(value&&(!/^\d{4}-\d{2}-\d{2}$/.test(value)||new Date(value+'T00:00:00Z').toISOString().slice(0,10)!==value))return '业务日期格式无效。';
    if (start && end && start > end) return "开始日期不能晚于结束日期；结果保持上次已查询范围。";
    return "";
  }
  function message(id,text,error=false) { $(id).textContent=text; $(id).classList.toggle("error",error); }
  function queryAnalysis() {
    const next = {start:$("#analysisStart").value,end:$("#analysisEnd").value,party:$("#analysisParty").value,name:$("#analysisProduct").value,spec:$("#analysisSpec").value};
    const error = validateDates(next.start,next.end);
    if (error) { message("#analysisMessage",error,true); return; }
    state.analysis=next; state.heatDay=""; renderAnalysis();
    message("#analysisMessage",`已查询 ${next.start} — ${next.end} · ${partyName(next.party)} · 原型·虚构数据`);
  }
  function resetAnalysis() {
    state.analysis={...defaults};
    $("#analysisStart").value=defaults.start; $("#analysisEnd").value=defaults.end;
    $("#analysisParty").value=""; $("#analysisProduct").value=""; updateSpecs();
    state.heatDay=""; renderAnalysis();
    message("#analysisMessage","已重置为默认日期、全部往来对象与全部商品。原型·虚构数据");
  }
  function renderAnalysis() {
    state.sales=salesLines();
    state.rows=selectedProducts().map(p => ({p, ...stats(state.sales.filter(l => l.productId === p.id))}));
    const s=stats(state.sales);
    const metrics = [
      ["净销售额",money(s.net),"销售 − 客户退货"], ["销售金额",money(s.sales),"有效销售单"],
      ["客户退货金额",money(s.returns),"展示绝对金额"],
      ["毛利润",s.profit === null ? "不可计算" : money(s.profit),s.unknown ? `缺少 ${s.unknown} 行历史成本` : !state.sales.length ? "没有销售 / 退货记录" : "按历史成本快照"],
      ["毛利率",s.margin === null ? "不适用" : `${s.margin.toFixed(1)}%`,s.unknown ? "成本覆盖不完整" : s.net <= 0 ? "净销售额 ≤ 0" : "毛利润 ÷ 净销售额"]
    ];
    $("#analysisMetrics").innerHTML=metrics.map(([label,value,note]) => `<article class="metric"><div class="metric-label">${label}</div><div class="metric-value">${value}</div><div class="metric-note">${note}</div></article>`).join("");
    $("#analysisScope").textContent=`${state.analysis.start} — ${state.analysis.end} · ${partyName(state.analysis.party)} · 销售 − 客户退货，拿货不计入净销售`;
    renderProductRows(); renderHeat(); renderRank(); renderHealth();
    $("#analysisTableNote").textContent=`净销售数量：${quantitySummary(s)}。当前库存是当前状态，非历史回溯；成本缺失不按零处理。`;
    $("#reconCarryHint").textContent=`带入${state.analysis.party ? partyName(state.analysis.party) : "日期，进入后请选择一个往来对象"}；商品 / 型号条件不带入，始终核对完整单据。`;
    $("#openRecon").href=reconRoute({party:state.analysis.party,start:state.analysis.start,end:state.analysis.end,type:""});
  }
  function renderProductRows() {
    const sort=$("#analysisSort").value;
    const rows=[...state.rows].sort((a,b) => sort === "stock" ? (a.p.stock === null ? 9 : a.p.stock === 0 ? 0 : a.p.stock <= a.p.safe ? 1 : 2) - (b.p.stock === null ? 9 : b.p.stock === 0 ? 0 : b.p.stock <= b.p.safe ? 1 : 2) : sort === "profit" ? (b.profit ?? -Infinity) - (a.profit ?? -Infinity) : b.net-a.net);
    $("#analysisRows").innerHTML=rows.length ? rows.map(r => `<tr data-product-id="${r.p.id}"><td><span class="product-name">${r.p.name}</span><span class="cell-sub">${r.p.spec || "未填写型号"}</span></td><td class="number ${r.qty < 0 ? "negative" : ""}">${quantity(r.qty)} ${r.p.unit}</td><td class="number ${r.net < 0 ? "negative" : ""}">${money(r.net)}</td><td class="number">${r.unknown ? "不可计算" : money(r.cost)}</td><td class="number">${r.profit === null ? "不可计算" : money(r.profit)}</td><td class="number">${r.margin === null ? "不适用" : r.margin.toFixed(1)+"%"}</td><td class="number">${r.p.stock === null ? "未知" : quantity(r.p.stock)+" "+r.p.unit}</td><td class="number">${r.p.movingCost===null?(r.p.stock===0?'无库存':'未知'):money(r.p.movingCost)}</td><td class="${statusClass(r.p)}">${stockStatus(r.p)}</td></tr>`).join("") : '<tr><td class="empty-state" colspan="9">没有符合条件的商品。</td></tr>';
  }
  function datesInScope() {
    const observed=state.sales.map(l=>l.date).sort();
    const start=state.analysis.start||observed[0]||state.analysis.end||defaults.start,finish=state.analysis.end||observed.at(-1)||state.analysis.start||defaults.end;
    const years=Array.from({length:Number(finish.slice(0,4))-Number(start.slice(0,4))+1},(_,i)=>String(Number(start.slice(0,4))+i));
    const previous=$('#heatYear').value;
    $('#heatYear').innerHTML=years.map(y=>`<option value="${y}">${y}</option>`).join('');
    $('#heatYear').value=years.includes(previous)?previous:years.at(-1);
    const year=$('#heatYear').value;
    const begin=start>year+'-01-01'?start:year+'-01-01',last=finish<year+'-12-31'?finish:year+'-12-31';
    $('#heatScope').textContent=`图示${year}年；顶部汇总仍按完整查询范围。点击日期看销售/退货。`;
    const dates=[]; let d=new Date(begin+"T00:00:00Z"), end=new Date(last+"T00:00:00Z");
    while(d<=end) { dates.push(d.toISOString().slice(0,10)); d.setUTCDate(d.getUTCDate()+1); }
    return dates;
  }
  function renderHeat() {
    const mixed=new Set(selectedProducts().map(p=>p.unit)).size>1;
    $('#heatMetric option[value="quantity"]').disabled=mixed;
    if(mixed&&$('#heatMetric').value==='quantity')$('#heatMetric').value='amount';
    $('#heatMetricHint').textContent=mixed?'当前范围有不同单位，净数量不能混合计量；按具体商品筛选后可切换。':'';
    const metric=$("#heatMetric").value;
    const days=datesInScope().map(date => ({date,s:stats(state.sales.filter(l=>l.date===date))}));
    const max=Math.max(1,...days.map(d=>Math.abs(metric==='amount'?d.s.net:d.s.qty)));
    const first=days[0]?.date,offset=first?(new Date(first+'T00:00:00Z').getUTCDay()+6)%7:0;
    $("#heatGrid").innerHTML='<span aria-hidden="true"></span>'.repeat(offset)+days.map(({date,s}) => {
      const numeric=metric==='amount'?s.net:s.qty,level=numeric===0 ? 0 : Math.max(1,Math.ceil(Math.abs(numeric)/max*4));
      const hasLines=state.sales.some(l=>l.date===date);
      const value=metric === "amount" ? money(s.net) : s.units.size > 1 ? "单位不同" : s.units.size ? `${quantity(s.qty)} ${[...s.units][0]}` : "—";
      return `<button class="heat-cell level-${level} ${numeric < 0 ? "negative" : ""} ${hasLines?'':'no-activity'}" type="button" data-heat-day="${date}" aria-pressed="${state.heatDay===date}" title="${date} · ${hasLines?value:'无交易'}"><strong>${date.slice(5)}</strong><span>${hasLines?value:'无交易'}</span></button>`;
    }).join("");
    if(state.heatDay) showHeatDay(state.heatDay); else $("#heatDetail").textContent=`${state.sales.length ? "选择日期查看当天销售、客户退货与历史成本。" : "当前日期范围内没有有效销售或客户退货。"} 热力图与主表使用同一已查询筛选范围。`;
  }
  function showHeatDay(date) {
    state.heatDay=date; const s=stats(state.sales.filter(l=>l.date===date));
    $$("[data-heat-day]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.heatDay===date)));
    $("#heatDetail").innerHTML=`<h3>${date}</h3>${[["销售金额",money(s.sales)],["客户退货",money(s.returns)],["净销售额",money(s.net)],["净销售数量",quantitySummary(s)],["毛利润",s.profit===null ? "不可计算" : money(s.profit)]].map(([a,b])=>`<div class="detail-line"><span>${a}</span><strong>${b}</strong></div>`).join("")}`;
  }
  function renderRank() {
    const mode=$("#rankingMode").value;
    let rows=[...state.rows];
    if(mode==="hot" || mode==="low") rows=rows.filter(r=>r.qty>0&&r.sales>0).sort((a,b)=>mode==="hot" ? b.qty-a.qty : a.qty-b.qty);
    if(mode==='returns')rows=rows.filter(r=>r.qty<=0&&r.returns>0);
    if(mode==="unsold") rows=rows.filter(r=>r.sales===0);
    if(mode==="profit") rows=rows.filter(r=>r.profit!==null).sort((a,b)=>b.profit-a.profit);
    if(mode==="stock") rows=rows.filter(r=>r.p.stock!==null && r.p.stock<=r.p.safe).sort((a,b)=>a.p.stock-b.p.stock);
    const grouped=['hot','low','returns'].includes(mode),groups=grouped?[...new Set(rows.map(r=>r.p.unit))]:['全部'];
    $('#rankingRows').innerHTML=rows.length?groups.map(unit=>{
      const items=(grouped?rows.filter(r=>r.p.unit===unit):rows).slice(0,10);
      const max=Math.max(1,...items.map(r=>Math.abs(grouped?r.qty:mode==='profit'?r.profit:r.net)));
      return `<h3>${grouped?esc(unit)+' · 同单位前10':'当前筛选结果'}</h3>`+items.map((r,i)=>`<div class="rank-line"><span>${i+1}</span><div><strong>${r.p.name}</strong><span class="cell-sub">${r.p.spec||'未填写型号'}</span></div><div class="rank-bar"><span style="width:${Math.abs(grouped?r.qty:mode==='profit'?r.profit:r.net)/max*100}%"></span></div><strong>${grouped?quantity(r.qty)+' '+r.p.unit:mode==='stock'?stockStatus(r.p):mode==='unsold'?'期间未销售':money(r.profit)}</strong></div>`).join('');
    }).join(''):'<p class="empty-state">当前筛选范围无符合此排行的具体产品。</p>';
  }
  function renderHealth() {
    const s=stats(state.sales);
    $("#costHealth").innerHTML=`<div class="health-lines"><p>历史成本覆盖：<strong>${state.sales.length ? `${state.sales.length-s.unknown} / ${state.sales.length} 行` : "无销售 / 退货"}</strong></p><p>全范围毛利润：<strong>${s.unknown ? "不可计算（不能以零补齐）" : s.profit===null ? "不可计算" : money(s.profit)}</strong></p><p>净销售数量：<strong>${quantitySummary(s)}</strong></p><p>当前库存：<strong>与历史日期筛选分开</strong></p></div><p class="form-message">以下移动平均成本只读，属于内部信息；不进入对外样张。历史利润按保存时的成本快照计算。</p><div class="table-wrap"><table class="data-table"><thead><tr><th>商品 / 型号</th><th class="number">当前库存</th><th class="number">安全库存</th><th class="number">移动平均成本（只读）</th><th class="number">库存金额</th><th>库存状态</th></tr></thead><tbody>${selectedProducts().map(p=>`<tr><td>${p.name}<span class="cell-sub">${p.spec || "未填写型号"}</span></td><td class="number">${p.stock===null ? "未知" : quantity(p.stock)+" "+p.unit}</td><td class="number">${quantity(p.safe)} ${p.unit}</td><td class="number">${p.movingCost===null ? p.stock===0 ? "无库存" : "未知" : money(p.movingCost)+" / "+p.unit}</td><td class="number">${p.stock===0 ? money(0) : p.stock===null || p.movingCost===null ? "未知" : money(Math.round(p.stock*p.movingCost/1000))}</td><td class="${statusClass(p)}">${stockStatus(p)}</td></tr>`).join("")}</tbody></table></div>`;
  }
  function reconRoute(f) {
    const q=new URLSearchParams({start:f.start,end:f.end,sample:state.sample});
    if(f.party) q.set("counterparty",f.party); if(f.type) q.set("type",f.type);
    return "#reconciliation?"+q.toString();
  }
  function queryRecon(updateURL=true) {
    const f={party:$("#reconParty").value,start:$("#reconStart").value,end:$("#reconEnd").value,type:$("#reconType").value};
    const error=!f.party ? "必须选择一个往来对象，不能合并不同对象核对。" : validateDates(f.start,f.end);
    if(error) { message("#reconMessage",error,true); return false; }
    state.recon=f; state.dirty=false; state.page=1; state.preview=[];
    state.scope=docs().filter(d=>d.party===f.party && (!f.start||d.date>=f.start) && (!f.end||d.date<=f.end) && (!f.type || d.type===f.type)).sort((a,b)=>a.date.localeCompare(b.date)||a.key.localeCompare(b.key));
    state.selected=new Set(state.scope.map(d=>d.key)); $("#documentDetail").hidden=true;
    if(updateURL) history.replaceState(null,"",reconRoute(f));
    message("#reconMessage",`已重建查询范围，默认全选 ${state.scope.length} 笔完整单据。翻页保留选择；条件变化需重新查询。`);
    renderRecon(); return true;
  }
  function resetRecon(updateURL=true) {
    state.recon=null; state.scope=[]; state.selected.clear(); state.page=1; state.dirty=false; state.preview=[];
    $("#reconParty").value=""; $("#reconStart").value=defaults.start; $("#reconEnd").value=defaults.end; $("#reconType").value=""; $("#documentDetail").hidden=true;
    if(updateURL) history.replaceState(null,"",reconRoute({...defaults,type:""}));
    message("#reconMessage","请选择一个往来对象并查询。不会预设固定买方 / 卖方身份。"); renderRecon();
  }
  function renderRecon() {
    const pages=Math.max(1,Math.ceil(state.scope.length/pageSize)); state.page=Math.min(state.page,pages);
    const slice=state.scope.slice((state.page-1)*pageSize,state.page*pageSize);
    $("#reconScope").textContent=state.recon ? `${partyName(state.recon.party)} · ${state.recon.start} — ${state.recon.end} · ${state.recon.type ? types[state.recon.type].name : "全部四类交易"}` : "请选择一个往来对象并查询。";
    $("#selectionSummary").textContent=`已选 ${state.selected.size} / 符合条件 ${state.scope.length} 笔 · 按整单选择，含全部商品明细`;
    $("#reconRows").innerHTML=slice.length ? slice.map(d=>`<tr data-doc-key="${d.key}"><td class="check-cell"><input type="checkbox" data-select-key="${d.key}" aria-label="选择 ${types[d.type].name} ${d.no}" ${state.selected.has(d.key) ? "checked" : ""} ${state.dirty ? "disabled" : ""}></td><td>${d.date}<span class="cell-sub">${d.no}</span></td><td>${types[d.type].name}</td><td>${types[d.type].direction}</td><td class="number ${types[d.type].sign < 0 ? "negative" : ""}">${money(d.amount*types[d.type].sign)}</td><td><button class="btn small" type="button" data-detail-key="${d.key}">查看 ${d.lines.length} 行</button></td></tr>`).join("") : `<tr><td class="empty-state" colspan="6">${state.recon ? "该对象在此日期 / 类型范围内没有可核对的有效单据。" : "先选择一个往来对象，再查询完整单据。"}</td></tr>`;
    $('#unlinkedNote').textContent=`${docs().filter(d=>d.type==='purchase'&&!d.party).length}笔历史拿货未关联对象，不自动从货源备注推断，未纳入对象对账。`;
    $("#reconPageInfo").textContent=`第 ${state.page} / ${pages} 页 · 每页 ${pageSize} 笔 · 全结果选择跨页保留`;
    $("#reconPagination").innerHTML=Array.from({length:pages},(_,i)=>`<button type="button" class="btn small" data-page="${i+1}" ${state.page===i+1 ? 'aria-current="page"' : ""}>${i+1}</button>`).join("");
    const selected=selectedDocs();
    $("#typeTotals").innerHTML=Object.entries(types).map(([type,t])=>{const rows=selected.filter(d=>d.type===type); return `<div class="type-total"><span>${t.name} · ${rows.length} 笔</span><strong class="${t.sign<0 ? "negative" : ""}">${money(sum(rows,d=>d.amount)*t.sign)}</strong><small>${t.direction} · ${t.sign===1 ? "加项" : "减项"}</small></div>`;}).join("");
    const net=sum(selected,d=>d.amount*types[d.type].sign);
    $("#reconNet").textContent=money(net); $("#reconNet").classList.toggle("negative",net<0);
    $("#selectAll").disabled=!(state.scope.length && !state.dirty); $("#clearAll").disabled=!(state.selected.size && !state.dirty);
    $("#previewButton").disabled=!(state.selected.size && !state.dirty);
    $("#backRecon").href=state.recon ? reconRoute(state.recon) : "#reconciliation";
  }
  function showDetail(key) {
    const d=state.scope.find(d=>d.key===key); if(!d) return;
    $("#documentDetail").innerHTML=`<div class="section-heading"><div><h2 id="documentDetailTitle">${types[d.type].name} · ${d.no}</h2><p>${partyName(d.party)} · ${types[d.type].direction} · ${d.key} · 原型·虚构数据</p></div><button class="btn small" type="button" id="closeDetail">收起明细</button></div><p class="form-message">本单全部 ${d.lines.length} 行随整单选择；不可只选某一商品。交易单价不是内部移动平均成本。</p><div class="table-wrap"><table class="data-table"><thead><tr><th>商品 / 型号</th><th class="number">数量</th><th class="number">交易单价</th><th class="number">交易金额</th></tr></thead><tbody>${d.lines.map(l=>{const p=product(l.productId);return `<tr><td>${p.name} · ${p.spec || "未填写型号"}</td><td class="number">${quantity(l.qty)} ${p.unit}</td><td class="number">${money(l.price)}</td><td class="number">${money(l.amount)}</td></tr>`;}).join("")}</tbody></table></div>`;
    $("#documentDetail").hidden=false; $("#documentDetail").scrollIntoView({behavior:"smooth",block:"nearest"});
    $("#closeDetail").addEventListener("click",()=>$("#documentDetail").hidden=true);
  }
  // Explicit external-field projection. Internal cost/profit/stock/source notes are
  // never serialized to preview or download, rather than being hidden with CSS.
  function externalDocs(rows) {
    return rows.map(d=>({key:d.key,date:d.date,no:d.no,type:types[d.type].name,direction:types[d.type].direction,amount:d.amount,contribution:d.amount*types[d.type].sign,
      items:d.lines.map(l=>{const p=product(l.productId);return {name:p.name,spec:p.spec,unit:p.unit,quantity:l.qty,unitPrice:l.price,amount:l.amount};})}));
  }
  function buildPreview() {
    const rows=selectedDocs();
    if(!state.recon || state.dirty || !rows.length) return false;
    state.preview=externalDocs(rows); renderPreview();
    location.hash="pdf"; return true;
  }
  function renderPreview() {
    if(!state.recon || !state.preview.length) return;
    const net=sum(state.preview,d=>d.contribution);
    $("#externalPreview").innerHTML=`<h2>往来交易核对清单</h2><p class="paper-mark">原型·虚构数据 · A4 纵向版式样张 · 非正式 PDF</p><div class="paper-meta"><strong>往来对象：${esc(partyName(state.recon.party))}</strong><span>业务日期：${state.recon.start} — ${state.recon.end}</span><span>完整单据：${state.preview.length} 笔 · 同一对象的双向交易</span></div><table><thead><tr><th>商品 / 型号</th><th>数量</th><th>交易单价</th><th>交易金额</th></tr></thead><tbody>${state.preview.map(d=>`<tr class="doc-break"><td colspan="4">${d.date} · ${d.no} · ${d.type} · ${d.direction}<br>整单金额 ${money(d.amount)} · 净额贡献 ${money(d.contribution)}</td></tr>${d.items.map(i=>`<tr><td>${esc(i.name)} · ${esc(i.spec || "未填写型号")}</td><td>${quantity(i.quantity)} ${esc(i.unit)}</td><td>${money(i.unitPrice)}</td><td>${money(i.amount)}</td></tr>`).join("")}`).join("")}</tbody></table><div class="paper-total"><strong>交易对账净额</strong><strong>${money(net)}</strong></div><p>净额 = 销售 − 客户退货 − 拿货 + 退拿货。</p><p>此清单仅核对选中单据的业务交易，不等于当前欠款；不含期初、收款、付款或调整，预览与保存不产生实际结算。</p><div class="signatures"><span>本店确认：____________</span><span>对方确认：____________</span></div>`;
  }
  function exportPreview() {
    if(!state.preview.length) return;
    const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>原型·虚构数据 往来交易核对样张</title><style>body{font:12px/1.7 "Microsoft YaHei UI",sans-serif;margin:12mm;color:#111}h2,.paper-mark{text-align:center}table{border-collapse:collapse;width:100%}td,th{border:1px solid #999;padding:7px;text-align:left}.paper-meta{display:grid;gap:4px;margin:18px 0}.doc-break{background:#f3f5f8}.paper-total,.signatures{display:flex;justify-content:space-between;margin:24px 0}@page{size:A4 portrait;margin:12mm}</style><body>${$("#externalPreview").innerHTML}</body></html>`;
    const url=URL.createObjectURL(new Blob([html],{type:"text/html;charset=utf-8"}));
    const a=document.createElement("a"); a.href=url; a.download="原型-虚构数据-往来交易核对样张.html"; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function switchTab(name,focus=false) {
    state.tab=name;
    $$("[data-tab]").forEach(b=>{const active=b.dataset.tab===name;b.classList.toggle("active",active);b.setAttribute("aria-selected",String(active));b.tabIndex=active ? 0 : -1;if(active && focus)b.focus();});
    ["heat","rank","health"].forEach(n=>$("#"+n+"Panel").hidden=n!==name);
  }
  function setTheme(choice) {
    document.documentElement.dataset.uiTheme=choice;
    document.documentElement.dataset.resolvedTheme=choice==="system" ? matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light" : choice;
  }
  function setSample(value) {
    state.extraDocs=[];products.forEach((p,i)=>Object.assign(p,structuredClone(baselineProducts[i])));$('#demoReceipt').hidden=true;$('#demoReceipt').textContent='';
    state.sample=["standard","unknown","empty","fraction"].includes(value) ? value : "standard";
    entryFlows?.reset();
    $("#sampleSelect").value=state.sample; resetAnalysis(); resetRecon(false);
  }
  function route() {
    const [raw,query=""]=location.hash.slice(1).split("?"); const params=new URLSearchParams(query);
    let screen=["analytics","reconciliation","pdf","purchase","purchaseReturn"].includes(raw) ? raw : "analytics";
    state.scroll[state.screen]=$("#pageBody").scrollTop;
    if(screen==="reconciliation" && query) {
      if(params.has("sample") && params.get("sample")!==state.sample) setSample(params.get("sample"));
      const f={party:params.get("counterparty")||"",start:params.has('start')?params.get("start"):defaults.start,end:params.has('end')?params.get("end"):defaults.end,type:params.get("type")||""};
      if(!parties.some(p=>p.id===f.party)) f.party=""; if(f.type && !types[f.type]) f.type="";
      $("#reconParty").value=f.party; $("#reconStart").value=f.start; $("#reconEnd").value=f.end; $("#reconType").value=f.type;
      if(JSON.stringify(f)!==JSON.stringify(state.recon)) {
        if(f.party) queryRecon(false); else { resetRecon(false); $("#reconStart").value=f.start; $("#reconEnd").value=f.end; message("#reconMessage","已带入分析日期。请选择一个往来对象；商品条件不带入对账。"); }
      }
    }
    if(screen==="pdf" && !state.preview.length) { screen="reconciliation"; history.replaceState(null,"",state.recon ? reconRoute(state.recon) : "#reconciliation"); }
    ["analytics","reconciliation","pdf","purchase","purchaseReturn"].forEach(n=>$("#"+n+"Screen").hidden=n!==screen);
    $("#screenTitle").textContent={analytics:"数据分析",reconciliation:"往来对账",pdf:"对账单预览",purchase:'拿货单',purchaseReturn:'退拿货单'}[screen];
    for(const name of ['analytics','reconciliation','purchase','purchaseReturn']){const nav=$('#'+name+'Nav');nav.classList.toggle('active',screen===name||(screen==='pdf'&&name==='reconciliation'));if(nav.classList.contains('active'))nav.setAttribute('aria-current','page');else nav.removeAttribute('aria-current');}
    $('#demoReceipt').hidden=!['purchase','purchaseReturn'].includes(screen)||!$('#demoReceipt').textContent;
    state.screen=screen; $("#pageBody").scrollTop=state.scroll[screen];
    $('#screenSelect').value=screen==='pdf'?'reconciliation':screen;
    if(screen==='purchaseReturn'&&params.has('counterparty'))entryFlows.openSource(params.get('counterparty'),params.get('source')||'');
  }
  populateFilters();
  $("#analyticsFilters").addEventListener("submit",e=>{e.preventDefault();queryAnalysis();});
  $("#analysisProduct").addEventListener("change",updateSpecs);
  $("#analysisReset").addEventListener("click",resetAnalysis);
  $("#analyticsFilters").addEventListener("change",()=>message("#analysisMessage","筛选条件尚未查询；当前指标、明细与对账入口仍按上次已查询范围。"));
  $("#analysisSort").addEventListener("change",renderProductRows);
  $("#heatMetric").addEventListener("change",renderHeat);$('#heatYear').addEventListener('change',()=>{state.heatDay='';renderHeat();}); $("#rankingMode").addEventListener("change",renderRank);
  $("#heatGrid").addEventListener("click",e=>{const b=e.target.closest("[data-heat-day]"); if(b)showHeatDay(b.dataset.heatDay);});
  $$("[data-tab]").forEach(b=>b.addEventListener("click",()=>switchTab(b.dataset.tab)));
  $(".tabs").addEventListener("keydown",e=>{if(!["ArrowLeft","ArrowRight","Home","End"].includes(e.key))return;e.preventDefault();const names=["heat","rank","health"],idx=names.indexOf(state.tab);switchTab(names[e.key==="Home" ? 0 : e.key==="End" ? 2 : (idx+(e.key==="ArrowRight" ? 1 : 2))%3],true);});
  $("#reconFilters").addEventListener("submit",e=>{e.preventDefault();queryRecon();});
  $("#reconReset").addEventListener("click",()=>resetRecon());
  $("#reconFilters").addEventListener("change",()=>{if(state.recon){state.dirty=true;renderRecon();$("#documentDetail").hidden=true;}message("#reconMessage","条件已改变。请重新查询，新结果将默认全选；旧选择暂不可预览。");});
  $("#reconRows").addEventListener("change",e=>{const key=e.target.dataset.selectKey;if(key && !state.dirty){e.target.checked ? state.selected.add(key) : state.selected.delete(key);renderRecon();}});
  $("#reconRows").addEventListener("click",e=>{const b=e.target.closest("[data-detail-key]");if(b)showDetail(b.dataset.detailKey);});
  $("#reconPagination").addEventListener("click",e=>{const b=e.target.closest("[data-page]");if(b){state.page=Number(b.dataset.page);renderRecon();}});
  $("#selectAll").addEventListener("click",()=>{state.selected=new Set(state.scope.map(d=>d.key));renderRecon();});
  $("#clearAll").addEventListener("click",()=>{state.selected.clear();renderRecon();});
  $("#previewButton").addEventListener("click",buildPreview); $("#savePreview").addEventListener("click",exportPreview);
  $("#themeSelect").addEventListener("change",e=>setTheme(e.target.value));
  $('#screenSelect').addEventListener('change',e=>location.hash=e.target.value);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change",()=>{if($("#themeSelect").value==="system")setTheme("system");});
  $("#sampleSelect").addEventListener("change",e=>{setSample(e.target.value);location.hash="analytics";route();});
  addEventListener("hashchange",route);
  entryFlows=initEntryFlows({products,parties,getDocs:allDocuments,createDoc:doc,money,quantity,esc,onSave:(d,changes)=>{
    d.lines.forEach((l,i)=>l.id=`${d.key}:l${i+1}`);state.extraDocs.push(d);
    for(const change of changes){const p=product(change.productId);p.stock=change.quantity;p.costMicro=change.costMicro;p.movingCost=p.stock?roundRatio(BigInt(p.costMicro)*1000n,BigInt(p.stock)*10000n):null;}
    if(state.recon&&d.status==='saved'){state.dirty=true;renderRecon();message('#reconMessage','演示交易有变化，请重新查询；旧选择不可直接预览。',true);}
    renderAnalysis();$('#demoReceipt').hidden=false;$('#demoReceipt').innerHTML=`<strong>${types[d.type].name} ${d.no} · ${d.status==='draft'?'草稿':'正式演示'}</strong><span>仅内存保存，未写任何业务库；刷新或切换样例复原。旧销售成本和实际资金记录均未改变。</span>`;
  }});
  setTheme("light"); resetAnalysis(); resetRecon(false); route();
})();
