global.window={localStorage:makeLS(),sessionStorage:makeLS(),name:'',
  location:{search:'?project=T&part=P&parentdrgnum=D&rowID=R&item=10'}};
global.document={querySelectorAll:()=>[],getElementById:()=>null};
global.navigator={userAgent:'Chrome'};
let spreadsheetData=[];
const TYPE_DISPLAY_NAMES={"Missing Childpart":"Missing Drawing"};
let lastOcrBomItem=10, rowID='R1';
const getRowWarning=()=>'OCR(Q)';
const hot={countRows:()=>spreadsheetData.length,toPhysicalRow:v=>v,render:()=>{}};
