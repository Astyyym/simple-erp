import assert from 'node:assert/strict';
import test from 'node:test';

const moduleURL = new URL('../../designs/simple-erp-inventory-analytics/counterparty-v4-engine.mjs', import.meta.url);

test('purchase return inherits supplier amount but consumes current average stock cost', async () => {
  const { quoteReturn } = await import(moduleURL);
  const source={key:'purchase:1',type:'purchase',party:'1',status:'saved',date:'2026-09-04',lines:[{id:'purchase:1:l1',productId:'p1',qty:10000,price:1000,amount:10000}]};
  const inventory=[{productId:'p1',quantity:20000,costMicro:300000000}];
  const before=structuredClone(inventory);
  const quote=quoteReturn({source,party:'1',requests:[{sourceLineId:'purchase:1:l1',qty:2000}],inventory});
  assert.equal(quote.amountCents,2000);
  assert.equal(quote.stockCostMicro,30000000);
  assert.equal(quote.items[0].price,1000);
  assert.deepEqual(quote.inventoryChanges,[{productId:'p1',quantity:18000,costMicro:270000000}]);
  assert.deepEqual(inventory,before,'A quote must not post inventory');
});

test('successive partial returns conserve original cents and final stock micro-units', async () => {
  const {quoteReturn}=await import(moduleURL);
  const source={key:'purchase:1',type:'purchase',party:'1',status:'saved',lines:[{id:'l1',productId:'p1',qty:1500,price:1,amount:2}]};
  let inventory=[{productId:'p1',quantity:1500,costMicro:10000003}];
  const priorReturns=[],amounts=[],costs=[];
  for(let n=0;n<3;n++) {
    const q=quoteReturn({source,party:'1',requests:[{sourceLineId:'l1',qty:500}],priorReturns,inventory});
    amounts.push(q.amountCents);costs.push(q.stockCostMicro);inventory=q.inventoryChanges;
    priorReturns.push({sourceKey:source.key,sourceLineId:'l1',qty:500,amountCents:q.amountCents,status:'saved'});
  }
  assert.deepEqual(amounts,[1,0,1]);
  assert.equal(costs.reduce((a,b)=>a+b,0),10000003);
  assert.deepEqual(inventory,[{productId:'p1',quantity:0,costMicro:0}]);
});

test('later-line shortage leaves all input inventory unchanged', async () => {
  const {quoteReturn}=await import(moduleURL);
  const source={key:'purchase:1',type:'purchase',party:'1',status:'saved',lines:[{id:'l1',productId:'p1',qty:1000,price:100,amount:100},{id:'l2',productId:'p2',qty:1000,price:100,amount:100}]};
  const inventory=[{productId:'p1',quantity:1000,costMicro:1000000},{productId:'p2',quantity:0,costMicro:0}],before=structuredClone(inventory);
  assert.throws(()=>quoteReturn({source,party:'1',requests:[{sourceLineId:'l1',qty:500},{sourceLineId:'l2',qty:500}],inventory}),/库存不足/);
  assert.deepEqual(inventory,before);
});

test('draft return does not reserve quantity or consume stock',async()=>{
  const {quoteReturn}=await import(moduleURL);
  const source={key:'purchase:1',type:'purchase',party:'1',status:'saved',lines:[{id:'l1',productId:'p1',qty:1000,price:100,amount:100}]};
  const q=quoteReturn({source,party:'1',requests:[{sourceLineId:'l1',qty:1000}],inventory:[{productId:'p1',quantity:0,costMicro:0}],status:'draft'});
  assert.equal(q.amountCents,100);assert.equal(q.stockCostMicro,null);assert.deepEqual(q.inventoryChanges,[]);
});

test('foreign party, duplicate source lines and excessive remaining quantity are rejected',async()=>{
  const {quoteReturn}=await import(moduleURL);
  const base={source:{key:'purchase:1',type:'purchase',party:'1',status:'saved',lines:[{id:'l1',productId:'p1',qty:1000,price:100,amount:100}]},party:'1',requests:[{sourceLineId:'l1',qty:1000}],inventory:[{productId:'p1',quantity:2000,costMicro:2000000}]};
  assert.throws(()=>quoteReturn({...base,party:'2'}),/同一往来对象/);
  assert.throws(()=>quoteReturn({...base,requests:[base.requests[0],base.requests[0]]}),/不能重复/);
  assert.throws(()=>quoteReturn({...base,priorReturns:[{sourceKey:'purchase:1',sourceLineId:'l1',qty:500,amountCents:50,status:'saved'}]}),/剩余可退/);
});
