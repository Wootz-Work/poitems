const TYPE_DISPLAY_NAMES={"Missing Childpart":"Missing Drawing"};
let lastOcrBomItem=10, rowID='R1', _submitState=null, _lockedRows=new Set();
let spreadsheetData=[]; const getRowWarning=()=>'OCR(Q)';
let _rowMap=null;
const hot={countRows:()=>(_rowMap?_rowMap.length:spreadsheetData.length),
           toPhysicalRow:v=>(_rowMap?_rowMap[v]:v),render:()=>{}};
