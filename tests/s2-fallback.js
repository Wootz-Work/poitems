const MODE=process.env.MODE;
global.window={localStorage:thrower,sessionStorage:MODE==='session'?makeLS():thrower,name:'',
  location:{search:'?project=P&part=N&parentdrgnum=D&rowID=R&item=3'}};
if(MODE==='memory')Object.defineProperty(global.window,'name',{get(){throw new Error('no');},set(){throw new Error('no');}});
global.document={querySelectorAll:()=>[],getElementById:()=>null};
let spreadsheetData=[];
