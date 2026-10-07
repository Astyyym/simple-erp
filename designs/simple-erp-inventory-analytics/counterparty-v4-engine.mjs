/* Pure, in-memory prototype math. NOT the production inventory service. */
const effective = status => ['saved','printed'].includes(status);
const integer = (v,label) => { if (!Number.isSafeInteger(v) || v < 0) throw Error(`${label}无效`); return v; };
export function roundRatio(numerator, denominator) {
  const n=BigInt(numerator),d=BigInt(denominator);
  if(n<0n || d<=0n) throw Error('分摊参数无效');
  const result=Number((n+d/2n)/d);
  if(!Number.isSafeInteger(result)) throw Error('金额超出范围');
  return result;
}
export function parseScaled(text, digits, label) {
  const raw=String(text).trim();
  if(!new RegExp(`^\\d+(?:\\.\\d{1,${digits}})?$`).test(raw)) throw Error(`${label}最多${digits}位小数，且不能为负数`);
  const [whole,fraction='']=raw.split('.');
  const value=Number(BigInt(whole)*10n**BigInt(digits)+BigInt(fraction.padEnd(digits,'0')));
  return integer(value,label);
}
export function quoteReturn({source,party,requests,priorReturns=[],inventory,status='saved'}) {
  if(!source || source.type!=='purchase' || !effective(source.status) || source.deleted) throw Error('来源必须是有效原拿货单');
  if(!party || source.party!==party) throw Error('来源必须属于同一往来对象');
  if(!['saved','draft'].includes(status)) throw Error('状态无效');
  if(!requests.length) throw Error('至少录入一行退拿货数量');
  const requestMap=new Map();
  for(const request of requests) {
    integer(request.qty,'退拿货数量');
    if(!request.qty || requestMap.has(request.sourceLineId)) throw Error('数量须大于零，原行不能重复');
    if(!source.lines.some(l=>l.id===request.sourceLineId)) throw Error('原拿货明细不存在');
    requestMap.set(request.sourceLineId,request.qty);
  }
  const stocks=new Map(inventory.map(s=>[s.productId,{...s}]));
  const items=[],changed=new Set();
  for(const line of source.lines) {
    if(!requestMap.has(line.id)) continue;
    const qty=requestMap.get(line.id);
    const prior=priorReturns.filter(r=>r.sourceKey===source.key && r.sourceLineId===line.id && effective(r.status));
    const oldQty=prior.reduce((n,r)=>n+r.qty,0),oldAmount=prior.reduce((n,r)=>n+r.amountCents,0);
    if(oldQty+qty>line.qty) throw Error('超过原行剩余可退数量');
    const cumulative=roundRatio(BigInt(line.amount)*BigInt(oldQty+qty),line.qty);
    const amount=cumulative-oldAmount;
    if(amount<0) throw Error('原单累计退货金额异常');
    const stock=stocks.get(line.productId);
    if(status==='saved' && (!stock || stock.quantity===null || stock.costMicro===null)) throw Error('商品尚未启用库存');
    if(status==='saved' && stock.quantity<qty) throw Error('库存不足，整单未保存');
    const stockCostMicro=stock?.quantity>0 && stock.costMicro!==null ? (qty===stock.quantity ? stock.costMicro : roundRatio(BigInt(stock.costMicro)*BigInt(qty),stock.quantity)) : null;
    if(status==='saved') {
      stock.quantity-=qty;stock.costMicro-=stockCostMicro;changed.add(line.productId);
      if(stock.quantity===0 && stock.costMicro!==0) throw Error('库存成本尾差异常');
    }
    items.push({id:`return:${line.id}`,sourceLineId:line.id,productId:line.productId,qty,price:line.price,amount,stockCostMicro});
  }
  const amountCents=items.reduce((n,l)=>n+l.amount,0);
  const stockCostMicro=items.some(l=>l.stockCostMicro===null) ? null : items.reduce((n,l)=>n+l.stockCostMicro,0);
  return {amountCents,stockCostMicro,items,inventoryChanges:[...changed].map(id=>stocks.get(id)),varianceMicro:stockCostMicro===null ? null : amountCents*10000-stockCostMicro};
}
