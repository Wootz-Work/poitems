const TYPE_DISPLAY_NAMES={"Missing Childpart":"Missing Drawing"};
let lastOcrBomItem=10, rowID='R1';
const childPartsData=[], boData=[];
const ENDPOINTS={addChildParts:'/add-child-parts',addBoParts:'/add-bo-parts',generateMissingChildpartPdf:'/gen-pdf'};
const getRowWarning=()=>'OCR(Q)';
let VALID={isValid:true,missingFields:[]};
const validateRequiredFields=()=>VALID;
const NOTES=[]; const showNotification=(m,t)=>NOTES.push({m,t});
let CLEARED=0; const clearTableToDefault=()=>{CLEARED++;};
const hot={countRows:()=>spreadsheetData.length,toPhysicalRow:v=>v,render:()=>{}};
const CALLS=[],BODIES=[]; let PLAN={};
global.fetch=async(url,opts)=>{const u=url.split('?')[0];CALLS.push(u);
  if(opts&&opts.body)BODIES.push({u,body:JSON.parse(opts.body)});
  if(PLAN[u]==='throw')throw new Error('net');
  return {ok:PLAN[u]!=='fail',text:async()=>'nope'};};
const countCalls=u=>CALLS.filter(c=>c===u).length;
const lastBody=u=>[...BODIES].reverse().find(b=>b.u===u)?.body;
function onlyPdfsOutstanding(){ if(!_submitState)return false;
  const u=['childParts','pdfs','boParts'].filter(k=>['pending','failed','todo'].includes(_submitState[k]?.status));
  return u.length===1&&u[0]==='pdfs'; }
function refreshSubmitButtonState(){ BTN.textContent=!hasUnresolvedSubmit()?'Submit'
  :(onlyPdfsOutstanding()?'Create drawing':'Submit Remaining'); }
function reset(){CALLS.length=0;BODIES.length=0;NOTES.length=0;CLEARED=0;_submitState=null;_lockedRows=new Set();}
