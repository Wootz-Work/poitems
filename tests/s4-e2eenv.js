global.window={localStorage:makeLS(),sessionStorage:makeLS(),name:'',
  location:{search:'?project=P&part=PN&parentdrgnum=PD&rowID=R1&item=10'}};
global.document={querySelectorAll:()=>[],getElementById:id=>(id==='submit-btn'?BTN:null)};
let spreadsheetData=[];
