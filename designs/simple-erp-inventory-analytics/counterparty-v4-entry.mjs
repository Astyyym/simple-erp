/* Entry interactions for the v4 prototype. Saves affect memory only. */
import {parseScaled,quoteReturn,roundRatio} from './counterparty-v4-engine.mjs';
export function initEntryFlows({products,parties,getDocs,createDoc,onSave,money,quantity,esc}) {
  const $=selector=>document.querySelector(selector);
  const product=id=>products.find(p=>p.id===id);
  const stocks=()=>products.map(p=>({productId:p.id,quantity:p.stock,costMicro:p.costMicro}));
  const currentCost=p=>p.stock===null ? '未启用' : p.stock===0 ? '无库存' : money(p.movingCost);
  const report=(id,text,error=false)=>{const n=$(id);n.textContent=text;n.classList.toggle('error',error);};
  const effective=d=>['saved','printed'].includes(d.status)&&!d.deleted;
  const priorRows=()=>getDocs().filter(d=>d.type==='purchase_return').flatMap(d=>d.lines.map(l=>({sourceKey:d.sourceKey,sourceLineId:l.sourceLineId,qty:l.qty,amountCents:l.amount,status:d.status})));
  const nextId=type=>Math.max(0,...getDocs().filter(d=>d.type===type).map(d=>d.id))+1;
  const validDate=value=>/^\d{4}-\d{2}-\d{2}$/.test(value)&&new Date(value+'T00:00:00Z').toISOString().slice(0,10)===value;
  function entryDate(id,minimum='2026-09-01') {
    const value=$(id).value;
    if(!validDate(value)||value<minimum||value>'2026-10-02') throw Error('演示业务日期须在期初/原单日期至2026-10-02之间');
    return value;
  }
  function updatePurchaseNo(){const d=$('#purchaseDate').value;$('#purchaseNo').value='NH'+d.replaceAll('-','')+String(nextId('purchase')).padStart(4,'0');}
  function addPurchaseRow() {
    const tr=document.createElement('tr');
    tr.innerHTML=`<td><select class="purchase-product" aria-label="商品及型号"><option value="">请选择具体商品</option>${products.map(p=>`<option value="${p.id}">${esc(p.name)} · ${esc(p.spec||'未填写型号')}${p.stock===null?'（仅草稿）':''}</option>`).join('')}</select></td><td class="purchase-unit">—</td><td><input class="purchase-qty" aria-label="数量" inputmode="decimal" autocomplete="off"></td><td><input class="purchase-price" aria-label="拿货单价" inputmode="decimal" autocomplete="off"></td><td class="number purchase-cost">—</td><td><button class="btn small purchase-delete" type="button">删除</button></td>`;
    tr.querySelector('.purchase-product').addEventListener('change',()=>{
      const p=product(tr.querySelector('select').value);tr.querySelector('.purchase-unit').textContent=p?.unit||'—';tr.querySelector('.purchase-cost').textContent=p?currentCost(p):'—';
      tr.querySelector('.purchase-price').value='';report('#purchaseMessage','拿货单价独立录入，不改销售价或客户价。');
    });
    tr.querySelector('.purchase-delete').addEventListener('click',()=>{tr.remove();if(!$('#purchaseRows').children.length)addPurchaseRow();});
    $('#purchaseRows').append(tr);
  }
  function purchaseQuote() {
    const party=$('#purchaseParty').value,date=entryDate('#purchaseDate'),status=$('#purchaseStatus').value;
    if(!parties.some(p=>p.id===party))throw Error('请选择一个往来对象');
    const work=new Map(stocks().map(s=>[s.productId,{...s}])),items=[],changed=new Set();
    for(const tr of $('#purchaseRows').children) {
      const productId=tr.querySelector('select').value,qtyText=tr.querySelector('.purchase-qty').value,priceText=tr.querySelector('.purchase-price').value;
      if(!productId&&!qtyText&&!priceText)continue;
      const p=product(productId);if(!p)throw Error('请选择具体商品');
      const qty=parseScaled(qtyText,3,'数量'),price=parseScaled(priceText,2,'拿货单价');if(!qty)throw Error('数量必须大于零');
      const amount=roundRatio(BigInt(qty)*BigInt(price),1000);
      items.push({productId,qty,price,amount,cost:null});
      if(status==='saved') {
        const s=work.get(productId);if(s.quantity===null)throw Error('商品仅可保存草稿，须先启用库存');
        const nextQuantity=s.quantity+qty,nextCost=s.costMicro+amount*10000;
        if(!Number.isSafeInteger(nextQuantity)||!Number.isSafeInteger(nextCost))throw Error('库存数值超出原型支持范围');
        s.quantity=nextQuantity;s.costMicro=nextCost;changed.add(productId);
      }
    }
    if(!items.length)throw Error('至少录入一行商品');
    const amount=items.reduce((n,l)=>n+l.amount,0);
    $('#purchaseQuote').innerHTML=`拿货业务金额 <strong>${money(amount)}</strong>${status==='draft'?'草稿不入库':'正式演示入库，不登记实际付款'}`;
    return {party,date,status,items,amount,changes:[...changed].map(id=>work.get(id))};
  }
  function savePurchase() {
    try {
      const q=purchaseQuote(),d=createDoc('purchase',nextId('purchase'),q.party,q.date,q.items);d.status=q.status;d.sourceNotes=$('#purchaseSourceNotes').value;
      onSave(d,q.changes);updatePurchaseNo();report('#purchaseMessage',`${d.no} · ${q.status==='draft'?'草稿':'正式'}仅保存在演示内存，未写业务库。`);
      $('#purchaseRows').replaceChildren();addPurchaseRow();$('#purchaseSourceNotes').value='';
    } catch(e){report('#purchaseMessage',e.message,true);}
  }
  function updateSources() {
    const party=$('#returnParty').value;
    $('#returnSource').disabled=!party;
    $('#returnSource').innerHTML='<option value="">'+(party?'请选择有效原拿货单':'先选择往来对象')+'</option>'+getDocs().filter(d=>d.type==='purchase'&&d.party===party&&effective(d)).map(d=>`<option value="${d.key}">${d.no} · ${money(d.amount)}</option>`).join('');
    renderSource();
  }
  function selectedSource(){return getDocs().find(d=>d.key===$('#returnSource').value);}
  function renderSource() {
    const source=selectedSource(),prior=priorRows();
    $('#returnSave').disabled=!source;
    $('#returnRows').innerHTML=source ? source.lines.map(l=>{
      const p=product(l.productId),returned=prior.filter(r=>r.sourceKey===source.key&&r.sourceLineId===l.id&&['saved','printed'].includes(r.status)).reduce((n,r)=>n+r.qty,0);
      return `<tr data-source-line="${l.id}"><td>${esc(p.name)}<span class="cell-sub">${esc(p.spec||'未填写型号')} · ${p.unit}</span></td><td class="number">${quantity(l.qty)}</td><td class="number">${quantity(returned)}</td><td class="number return-remaining">${quantity(l.qty-returned)}</td><td class="number">${p.stock===null?'未知':quantity(p.stock)}</td><td class="number"><input class="return-qty" aria-label="本次退量 ${esc(p.name)} ${esc(p.spec)}" inputmode="decimal" autocomplete="off" ${returned===l.qty?'disabled':''}></td><td class="number">${money(l.price)}</td><td class="number">${currentCost(p)}</td></tr>`;
    }).join('') : '<tr><td colspan="8" class="empty-state">先选择往来对象和原拿货单。</td></tr>';
    $('#returnQuote').textContent='选择来源后填写本次退量；空白行不退。';report('#returnMessage','');
  }
  function returnQuote() {
    const source=selectedSource(),date=entryDate('#returnDate',source?.date),party=$('#returnParty').value,status=$('#returnStatus').value;
    const requests=[...$('#returnRows').querySelectorAll('[data-source-line]')].flatMap(tr=>{
      const value=tr.querySelector('input').value.trim();return value?[{sourceLineId:tr.dataset.sourceLine,qty:parseScaled(value,3,'退拿货数量')}]:[];
    });
    const q=quoteReturn({source,party,requests,priorReturns:priorRows(),inventory:stocks(),status});
    $('#returnQuote').innerHTML=`对方业务金额 <strong>${money(q.amountCents)}</strong>预计库存出库成本 <strong>${q.stockCostMicro===null?'暂不可计算':money(roundRatio(q.stockCostMicro,10000))}</strong>`;
    report('#returnMessage',status==='draft'?'草稿不扣库存、不占可退额度，正式保存需重新校验。':'两类金额分开，不重算旧销售成本，也不自动收退款。');
    return {...q,source,party,date,status};
  }
  function saveReturn() {
    try {
      const q=returnQuote(),d=createDoc('purchase_return',nextId('purchase_return'),q.party,q.date,q.items);
      d.status=q.status;d.sourceKey=q.source.key;onSave(d,q.inventoryChanges);
      const key=q.source.key;updateSources();$('#returnSource').value=key;renderSource();
      report('#returnMessage',`${d.no} · ${q.status==='draft'?'草稿未占额度':'已扣演示库存、占用原行可退量'}；仅内存保存，刷新复原。`);
    } catch(e){report('#returnMessage',e.message,true);}
  }
  for(const id of ['#purchaseParty','#returnParty']) $(id).insertAdjacentHTML('beforeend',parties.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join(''));
  $('#purchaseAdd').addEventListener('click',addPurchaseRow);
  $('#purchaseClear').addEventListener('click',()=>{$('#purchaseRows').replaceChildren();addPurchaseRow();$('#purchaseParty').value='';$('#purchaseSourceNotes').value='';$('#purchaseDate').value='2026-10-02';$('#purchaseStatus').value='saved';updatePurchaseNo();$('#purchaseQuote').textContent='已清空，恢复一行。';report('#purchaseMessage','');});
  $('#purchaseDate').addEventListener('change',updatePurchaseNo);
  $('#purchaseForm').addEventListener('submit',e=>{e.preventDefault();try{purchaseQuote();report('#purchaseMessage','金额已计算，尚未保存。');}catch(error){report('#purchaseMessage',error.message,true);}});
  $('#purchaseSave').addEventListener('click',savePurchase);
  $('#returnParty').addEventListener('change',updateSources);$('#returnSource').addEventListener('change',renderSource);
  $('#purchaseReturnForm').addEventListener('submit',e=>{e.preventDefault();try{returnQuote();}catch(error){report('#returnMessage',error.message,true);}});
  $('#returnSave').addEventListener('click',saveReturn);
  addPurchaseRow();updatePurchaseNo();
  return {refresh:()=>{updateSources();updatePurchaseNo();},reset:()=>{$('#purchaseRows').replaceChildren();addPurchaseRow();updateSources();updatePurchaseNo();},openSource:(party,key)=>{$('#returnParty').value=party;updateSources();$('#returnSource').value=key;renderSource();}};
}
