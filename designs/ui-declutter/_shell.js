(function(){
  // 时钟
  var clock=document.getElementById('clock');
  if(clock){ (function tick(){var d=new Date();var p=function(n){return String(n).padStart(2,'0')};clock.textContent=d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());})( ); setInterval(function(){var d=new Date();var p=function(n){return String(n).padStart(2,'0')};clock.textContent=d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes())+':'+p(d.getSeconds());},1000); }

  // 标注开关
  var annoSwitch=document.getElementById('annoSwitch');
  if(annoSwitch){ annoSwitch.addEventListener('change',function(){document.body.classList.toggle('show-anno',annoSwitch.checked);}); }

  // 分段控件
  document.querySelectorAll('.seg').forEach(function(seg){
    seg.querySelectorAll('button').forEach(function(b){
      b.addEventListener('click',function(){seg.querySelectorAll('button').forEach(function(x){x.classList.remove('active');});b.classList.add('active');});
    });
  });

  // 菜单
  var openMenu=null;
  function closeMenus(){if(openMenu){openMenu.classList.remove('open');openMenu=null;}}
  document.querySelectorAll('[data-menu]').forEach(function(trigger){
    trigger.addEventListener('click',function(e){
      e.stopPropagation();
      var menu=document.getElementById(trigger.getAttribute('data-menu'));
      if(!menu)return;
      var wasOpen=menu.classList.contains('open');
      closeMenus();
      if(!wasOpen){menu.classList.add('open');openMenu=menu;}
    });
  });
  document.addEventListener('click',function(){closeMenus();});

  // 高级筛选
  var advPanel=document.getElementById('advPanel');
  var advToggle=document.getElementById('advToggle');
  var advChev=document.getElementById('advChev');
  function setAdv(open){ if(!advPanel)return; advPanel.classList.toggle('open',open); if(advChev)advChev.style.transform=open?'rotate(180deg)':''; }
  if(advToggle){ advToggle.addEventListener('click',function(e){e.stopPropagation();setAdv(!advPanel.classList.contains('open'));}); }

  // chips + 标签
  var activeByKind={};
  var activeTags=document.getElementById('activeTags');
  var advCount=document.getElementById('advCount');
  function rebuildTags(){
    if(!activeTags)return; activeTags.innerHTML=''; var total=0;
    Object.keys(activeByKind).forEach(function(kind){
      activeByKind[kind].forEach(function(val){
        total++;
        var tag=document.createElement('span');tag.className='tag';
        tag.innerHTML=(kind==='type'?'类型':(kind==='status'?'状态':kind))+'：'+val+' <button aria-label="移除">&times;</button>';
        tag.querySelector('button').addEventListener('click',function(){activeByKind[kind].delete(val);syncChips();rebuildTags();});
        activeTags.appendChild(tag);
      });
    });
    if(advCount){ if(total>0){advCount.textContent=total;advCount.style.display='';}else{advCount.style.display='none';} }
  }
  function syncChips(){document.querySelectorAll('.chip[data-kind]').forEach(function(chip){var kind=chip.getAttribute('data-kind'),val=chip.getAttribute('data-val');if(!activeByKind[kind])return;chip.classList.toggle('active',activeByKind[kind].has(val));});}
  document.querySelectorAll('.chip[data-kind]').forEach(function(chip){
    var kind=chip.getAttribute('data-kind'); if(!activeByKind[kind])activeByKind[kind]=new Set();
    chip.addEventListener('click',function(){var val=chip.getAttribute('data-val');if(activeByKind[kind].has(val))activeByKind[kind].delete(val);else activeByKind[kind].add(val);syncChips();rebuildTags();});
  });
  var resetChips=document.getElementById('resetChips');
  if(resetChips){ resetChips.addEventListener('click',function(){Object.keys(activeByKind).forEach(function(k){activeByKind[k].clear();});syncChips();rebuildTags();}); }

  // 批量选择
  var checkAll=document.getElementById('checkAll');
  var rowCks=[].slice.call(document.querySelectorAll('.row-ck'));
  var batchDefault=document.getElementById('batchDefault');
  var batchSelected=document.getElementById('batchSelected');
  var selCount=document.getElementById('selCount');
  function refreshBatch(){
    var n=rowCks.filter(function(c){return c.checked;}).length;
    if(selCount)selCount.textContent=n;
    if(batchDefault)batchDefault.style.display=n>0?'none':'flex';
    if(batchSelected)batchSelected.style.display=n>0?'flex':'none';
    rowCks.forEach(function(c){c.closest('tr').classList.toggle('selected',c.checked);});
    if(checkAll){checkAll.checked=n>0&&n===rowCks.length;checkAll.indeterminate=n>0&&n<rowCks.length;}
  }
  if(checkAll){ checkAll.addEventListener('change',function(){rowCks.forEach(function(c){c.checked=checkAll.checked;});refreshBatch();}); }
  rowCks.forEach(function(c){c.addEventListener('change',refreshBatch);});
  var clearSel=document.getElementById('clearSel');
  if(clearSel){ clearSel.addEventListener('click',function(){rowCks.forEach(function(c){c.checked=false;});refreshBatch();}); }

  // toast
  var toast=document.getElementById('toast'); var toastTimer=null;
  function showToast(msg){ if(!toast)return; toast.textContent=msg; toast.classList.add('show'); clearTimeout(toastTimer); toastTimer=setTimeout(function(){toast.classList.remove('show');},1800); }
  document.querySelectorAll('[data-toast]').forEach(function(el){el.addEventListener('click',function(e){e.stopPropagation();closeMenus();showToast(el.getAttribute('data-toast'));});});

  // demo 参数：选中两行
  var params=new URLSearchParams(location.search);
  if(params.get('demo')==='select'){ [1,2].forEach(function(i){if(rowCks[i])rowCks[i].checked=true;}); refreshBatch(); }
  if(params.get('anno')==='1' && annoSwitch){document.body.classList.add('show-anno');annoSwitch.checked=true;}

  refreshBatch();
})();
