import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../app/erp/templates/orders/new.html',import.meta.url),'utf8');
const show = source.slice(source.indexOf('function showSuggestions('),source.indexOf('function bindAutocomplete('));
const scroll = source.slice(source.indexOf('function closeFloatingProductSuggestions('),source.indexOf("document.addEventListener('scroll',",source.indexOf('function closeFloatingProductSuggestions(')));

function fixture(kind) {
  let shown=false, closed=0;
  const inside={};
  const box={id:kind==='customer'?'customer_suggestions':'product_suggestions_1',innerHTML:'',style:{},offsetWidth:280,offsetHeight:180,
    classList:{contains:name=>name=== (kind==='customer'?'suggest-list':'product-suggestions'),remove(){}},
    appendChild(){},setAttribute(){},matches:()=>shown,
    showPopover(){shown=true;},contains:target=>target===inside};
  const input={getBoundingClientRect:()=>({left:50,top:200,bottom:240,width:280}),setAttribute(){},removeAttribute(){},_closeSuggestions(){closed++;shown=false;}};
  const document={createElement:()=>({className:'',setAttribute(){},appendChild(){},addEventListener(){}}),
    querySelectorAll:selector=>selector.startsWith('.product-suggestions')&&kind==='customer'?[]:[box],
    querySelector:()=>input};
  const context=vm.createContext({document,window:{innerWidth:1440,innerHeight:900}});
  vm.runInContext(show+scroll,context);
  return {context,input,box,inside,get shown(){return shown;},get closed(){return closed;},open(){shown=true;}};
}

for (const kind of ['customer','product']) {
  test(`${kind} suggestions escape card stacking through the top layer`,()=>{
    const f=fixture(kind);
    f.context.showSuggestions(f.input,f.box,[{name:'fixture'}],()=>({}),()=>{});
    assert.equal(f.shown,true,'Suggestions remained underneath the sibling card');
    assert.equal(f.box.style.left,'50px');
    assert.equal(f.box.style.top,'244px');
  });
}

test('page scroll closes an open customer list, not only product lists',()=>{
  const f=fixture('customer');f.open();
  f.context.closeFloatingProductSuggestions({type:'scroll',target:{}});
  assert.equal(f.closed,1);
  assert.equal(f.shown,false);
});

test('scrolling inside the customer menu preserves it; resizing closes it',()=>{
  const f=fixture('customer');f.open();
  f.context.closeFloatingProductSuggestions({type:'scroll',target:f.inside});
  assert.equal(f.closed,0);
  f.context.closeFloatingProductSuggestions({type:'resize',target:{}});
  assert.equal(f.closed,1);
});
