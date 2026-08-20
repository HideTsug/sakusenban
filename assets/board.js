(function(){
  var KEY='__BOARD_KEY__';
  var REPORT_HEAD='__REPORT_HEAD__';
  var store={};
  try{store=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){store={}}
  var boxes=Array.prototype.slice.call(document.querySelectorAll('input[data-ck]'));
  function save(){try{localStorage.setItem(KEY,JSON.stringify(store))}catch(e){}}
  function pending(){
    return boxes.filter(function(b){return b.checked&&!b.disabled});
  }
  function setBox(btn, open){
    var body=document.getElementById(btn.getAttribute('aria-controls'));
    btn.setAttribute('aria-expanded',open?'true':'false');
    if(body)body.hidden=!open;
    var box=btn.closest('.flowbox');
    if(box)box.classList.toggle('expanded',open);
  }
  function updateCounts(){
    document.querySelectorAll('[data-count]').forEach(function(el){
      var n=el.getAttribute('data-count');
      var subs=boxes.filter(function(b){return b.getAttribute('data-ck').indexOf(n+'.')===0});
      var done=subs.filter(function(b){return b.checked}).length;
      el.textContent=done+'/'+subs.length;
    });
    document.querySelectorAll('[data-boxprog]').forEach(function(el){
      var body=document.getElementById(el.getAttribute('data-boxprog'));
      var all=body?Array.prototype.slice.call(body.querySelectorAll('input[data-ck]')):[];
      if(all.length){
        var done=all.filter(function(b){return b.checked}).length;
        el.textContent='☑ '+done+'/'+all.length;
        var box=el.closest('.flowbox');
        if(box)box.classList.toggle('alldone',done===all.length);
      }else{
        el.textContent=(el.getAttribute('data-task-done')||'0')+'/'+(el.getAttribute('data-task-total')||'0');
      }
    });
    var p=pending().length;
    var bar=document.getElementById('syncbar');
    document.getElementById('pn').textContent=p;
    bar.classList.toggle('show',p>0);
  }
  boxes.forEach(function(b){
    var id=b.getAttribute('data-ck');
    if(!b.disabled&&store[id])b.checked=true;
    b.addEventListener('change',function(){
      if(b.checked)store[id]=new Date().toISOString();else delete store[id];
      save();updateCounts();
    });
  });
  document.querySelectorAll('.boxhead').forEach(function(btn){
    btn.addEventListener('click',function(){
      setBox(btn,btn.getAttribute('aria-expanded')!=='true');
    });
    setBox(btn,btn.getAttribute('aria-expanded')==='true');
  });
  document.querySelectorAll('[data-open-box]').forEach(function(a){
    a.addEventListener('click',function(ev){
      ev.preventDefault();
      var target=document.getElementById(a.getAttribute('data-open-box'));
      if(!target)return;
      var btn=target.querySelector('.boxhead');
      if(btn)setBox(btn,true);
      var smooth=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: no-preference)').matches;
      target.scrollIntoView({behavior:smooth?'smooth':'auto',block:'start'});
    });
  });
  function report(){
    var ids=pending().map(function(b){return b.getAttribute('data-ck')});
    var by={};
    ids.forEach(function(id){
      var head=id.split('.')[0];
      (by[head]=by[head]||[]).push(id.indexOf('.')>-1?id.split('.').slice(1).join('.'):'完了');
    });
    var lines=[REPORT_HEAD+new Date().toISOString()];
    Object.keys(by).forEach(function(h){
      var label=/^\d+$/.test(h)?'#'+h:h;
      lines.push(label+': '+by[h].join(', '));
    });
    lines.push('(このままClaudeに貼ってください。issue反映と作戦盤の再生成を行います)');
    return lines.join('\n');
  }
  document.getElementById('copybtn').addEventListener('click',function(){
    var txt=report();
    var done=function(){
      document.getElementById('copied').textContent='コピーしました — Claudeのチャットに貼ってください';
    };
    if(navigator.clipboard&&navigator.clipboard.writeText){
      navigator.clipboard.writeText(txt).then(done,function(){fallback(txt)});
    }else{fallback(txt)}
    function fallback(t){
      var ta=document.getElementById('fallback');
      ta.style.display='block';ta.value=t;ta.select();
      document.getElementById('copied').textContent='下のテキストを選択してコピーしてください';
    }
  });
  updateCounts();
})();
