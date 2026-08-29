const ok=T.ok;
const cell=t=>({text:t,confidence:0.9,ocr:true});
const row=(type,qty,part,flags)=>Object.assign({
  PartNumber:cell(part||''),Type:type||'',"Matched Childpart":part||'',
  Quantity:cell(qty||''),Description:cell('d'),Material:cell('m')}, flags||{});
const dupMsg = () => (validateRequiredFields().missingFields||[]).find(m=>/Duplicate/.test(m)) || '';

T.head('existing validation is unchanged');
spreadsheetData=[row('Child Part','2','D-1')];
ok('a good row passes', validateRequiredFields().isValid===true);
spreadsheetData=[row('','2','D-1')];
ok('missing Type still blocks', validateRequiredFields().isValid===false);
spreadsheetData=[row('Child Part','','D-1')];
ok('missing Quantity still blocks', validateRequiredFields().isValid===false);
spreadsheetData=[row('Child Part','2','')];
ok('missing Part Number still blocks', validateRequiredFields().isValid===false);
spreadsheetData=[row('Child Part','abc','D-1')];
ok('non-numeric Quantity still blocks', validateRequiredFields().isValid===false);
spreadsheetData=[row('Child Part','2','D-1',{_missingDrawing:true})];
ok('_missingDrawing still blocks', validateRequiredFields().isValid===false);
spreadsheetData=[];
ok('empty table is not submittable', validateRequiredFields().isValid===false);

T.head('duplicate child parts are blocked');
spreadsheetData=[row('Child Part','1','D-100'),row('Child Part','2','D-100')];
ok('two identical child parts blocked', validateRequiredFields().isValid===false);
ok('message names the part', /D-100/.test(dupMsg()));
spreadsheetData=[row('Child Part','1','D-100'),row('Child Part','2','D-200')];
ok('different parts pass', validateRequiredFields().isValid===true);

T.head('normalisation matches what submit actually sends');
spreadsheetData=[row('Child Part','1','D.1.0'),row('Child Part','2','D10')];
ok('same drawing after dot-stripping is a duplicate', validateRequiredFields().isValid===false);

T.head('scope: only rows that reach the Child Parts table');
spreadsheetData=[row('BO','1','X-1'),row('BO','2','X-1')];
ok('duplicate BO rows are NOT blocked', validateRequiredFields().isValid===true);
spreadsheetData=[row('Child Part','1','D-1'),row('BO','2','D-1')];
ok('child part vs BO with same text is NOT blocked', validateRequiredFields().isValid===true);
spreadsheetData=[row('Missing Drawing','1','M-1'),row('Missing Drawing','2','M-1')];
ok('duplicate Missing Drawing rows blocked', validateRequiredFields().isValid===false);
spreadsheetData=[row('Child Part','1','D-1'),row('Missing Drawing','2','D-1')];
ok('child part and missing drawing collide', validateRequiredFields().isValid===false);

T.head('no false positives');
spreadsheetData=[row('Child Part','1','D-1'),row('Child Part','','D-1')];
ok('incomplete duplicate row not counted', /Duplicate/.test(dupMsg())===false);
spreadsheetData=[row('Child Part','1','D-1'),row('','','')];
ok('blank rows ignored', validateRequiredFields().isValid===true);
spreadsheetData=[row('Child Part','1','D-1'),row('Child Part','2','')];
ok('empty part number reported as missing, not duplicate', /Duplicate/.test(dupMsg())===false);
spreadsheetData=[row('Child Part','1','D-1'),row('Child Part','2','D-2'),row('Child Part','3','D-1')];
ok('non-adjacent duplicate caught', validateRequiredFields().isValid===false);
spreadsheetData=[row('Child Part','1','D-1'),row('Child Part','2','D-1'),row('Child Part','3','D-1')];
ok('three of the same reported once', (dupMsg().match(/D-1/g)||[]).length===1);

T.head('resume: rows already in Glide must not block the rows still to send');
spreadsheetData=[row('Child Part','1','D-1',{_missingDrawing:true}),row('BO','2','B-1')];
ok('unscoped, the sent row blocks everything', validateRequiredFields().isValid===false);
ok('scoped, the outstanding BO row can still go',
   validateRequiredFields({skipRows:new Set([0]),resume:true}).isValid===true);

T.head('resume: a NEW row duplicating an already-sent one is caught');
spreadsheetData=[row('Child Part','1','D-100'),row('Child Part','2','D-100')];
ok('blocked even though row 0 is locked',
   validateRequiredFields({skipRows:new Set([0]),resume:true}).isValid===false);

T.head('resume: a duplicate wholly among sent rows does not strand the user');
spreadsheetData=[row('Child Part','1','D-100'),row('Child Part','2','D-100'),row('BO','3','B-9')];
ok('both locked -> not reported, resume can proceed',
   validateRequiredFields({skipRows:new Set([0,1]),resume:true}).isValid===true);

T.head('resume: field problems on NEW rows still block');
spreadsheetData=[row('Child Part','1','D-1'),row('Child Part','abc','D-2')];
ok('bad quantity on an unsent row blocks',
   validateRequiredFields({skipRows:new Set([0]),resume:true}).isValid===false);
spreadsheetData=[row('Child Part','1','D-1'),row('Child Part','2','D-2',{_missingDrawing:true})];
ok('_missingDrawing on an unsent row blocks',
   validateRequiredFields({skipRows:new Set([0]),resume:true}).isValid===false);

T.head('resume: PDF-only retry has no outstanding rows and must still pass');
spreadsheetData=[row('Missing Drawing','1','M-1')];
ok('all rows sent, nothing outstanding -> valid',
   validateRequiredFields({skipRows:new Set([0]),resume:true}).isValid===true);
ok('same table on a FRESH submit is still validated normally',
   validateRequiredFields().isValid===true);

T.head('fresh submit is byte-identical to before');
spreadsheetData=[row('Child Part','1','D-1')];
ok('no-arg call still works', validateRequiredFields().isValid===true);
spreadsheetData=[];
ok('empty table still invalid without resume', validateRequiredFields().isValid===false);
ok('empty table with resume is not blocked', validateRequiredFields({resume:true}).isValid===true);

T.done();
