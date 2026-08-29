// Shared browser stubs used by the suites.
global.makeLS = function () {
  const m = new Map(); let failNext = false;
  return { get length(){return m.size;}, key:i=>Array.from(m.keys())[i],
    getItem:k=>(m.has(k)?m.get(k):null),
    setItem:(k,v)=>{ if(failNext){failNext=false;const e=new Error('quota');e.name='QuotaExceededError';throw e;} m.set(k,String(v)); },
    removeItem:k=>m.delete(k), __failNext:()=>{failNext=true;} };
};
global.thrower = { get length(){return 0;}, key:()=>null,
  getItem:()=>{throw new Error('blocked');}, setItem:()=>{throw new Error('blocked');},
  removeItem:()=>{throw new Error('blocked');} };
global.BTN = { disabled:false, textContent:'Submit' };
global.mkTable = function () {
  const cell = t => ({ text:t, confidence:0.9, ocr:true });
  return { cell, mkRow: (type,qty,part) => ({ PartNumber:cell(part||''), Type:type||'',
    "Matched Childpart":part||'', Quantity:cell(qty||''), Description:cell('d'), Material:cell('m') }) };
};
global.T = (()=>{ let pass=0,fail=0; return {
  ok(n,c){ if(c){pass++;console.log('  ok  '+n);} else {fail++;console.log('  FAIL '+n);} },
  head(s){ console.log('\n== '+s+' =='); },
  done(){ console.log(`\n${pass} passed, ${fail} failed`); process.exit(fail?1:0); } };})();
