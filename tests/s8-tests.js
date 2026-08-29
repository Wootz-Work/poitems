const ok=T.ok;
const cell=t=>({text:t,confidence:0.9,ocr:true});
const row=(type,part)=>({PartNumber:cell(part||''),Type:type||'',"Matched Childpart":part||'',
  Quantity:cell('1'),Description:cell('d'),Material:cell('m')});
const dupes=()=>Array.from(duplicateChildPartRows()).sort((a,b)=>a-b);

T.head('marks the later occurrence, leaves the first clean');
spreadsheetData=[row('Child Part','D-1'),row('Child Part','D-1')];
ok('row 1 flagged, not row 0', JSON.stringify(dupes())==='[1]');
spreadsheetData=[row('Child Part','D-1'),row('Child Part','D-2'),row('Child Part','D-1')];
ok('non-adjacent duplicate flagged', JSON.stringify(dupes())==='[2]');
spreadsheetData=[row('Child Part','D-1'),row('Child Part','D-1'),row('Child Part','D-1')];
ok('all repeats after the first flagged', JSON.stringify(dupes())==='[1,2]');

T.head('scope matches the submit-time check');
spreadsheetData=[row('BO','X-1'),row('BO','X-1')];
ok('BO rows never flagged', dupes().length===0);
spreadsheetData=[row('Child Part','D-1'),row('BO','D-1')];
ok('child part vs BO not a duplicate', dupes().length===0);
spreadsheetData=[row('Missing Drawing','M-1'),row('Missing Drawing','M-1')];
ok('missing drawing repeats flagged', JSON.stringify(dupes())==='[1]');
spreadsheetData=[row('Child Part','D-1'),row('Missing Drawing','D-1')];
ok('child part and missing drawing collide', JSON.stringify(dupes())==='[1]');

T.head('normalisation matches what submit sends');
spreadsheetData=[row('Child Part','D.1.0'),row('Child Part','D10')];
ok('dots stripped before comparing', JSON.stringify(dupes())==='[1]');

T.head('self-clearing: fix the row and it stops being reported');
spreadsheetData=[row('Child Part','D-1'),row('Child Part','D-1')];
ok('flagged while duplicated', dupes().length===1);
spreadsheetData[1]["Matched Childpart"]='D-9';
ok('cleared once the value is changed', dupes().length===0);
spreadsheetData=[row('Child Part','D-1'),row('Child Part','D-1')];
spreadsheetData.splice(1,1);
ok('cleared once the row is deleted', dupes().length===0);
spreadsheetData=[row('Child Part','D-1'),row('Child Part','D-1')];
spreadsheetData[1].Type='BO';
ok('cleared once the row is retyped as BO', dupes().length===0);

T.head('no false positives');
spreadsheetData=[row('Child Part','D-1'),row('Child Part','')];
ok('blank part number ignored', dupes().length===0);
spreadsheetData=[row('','D-1'),row('','D-1')];
ok('rows with no Type ignored', dupes().length===0);
spreadsheetData=[row('Child Part','D-1'),null,row('Child Part','D-2')];
ok('null row survives the scan', dupes().length===0);
spreadsheetData=[];
ok('empty table', dupes().length===0);
T.done();
