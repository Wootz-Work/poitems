const {mkRow}=mkTable(); const ok=T.ok;
T.head('storage adapter');
ok('picks localStorage', DraftStore.kind()==='localStorage');
ok('durable', DraftStore.durable()===true);
T.head('key');
ok('key = context only', draftKey()==='ocrdraft:v1:P1|PN1|PD1|R1');
ok('item excluded from key', !draftKey().includes('10'));
ok('currentItemParam', currentItemParam()===10);
T.head('roundtrip + TTL');
DraftStore.set(draftKey(),{savedAt:Date.now(),rows:[{Type:'BO'}]});
ok('roundtrip', DraftStore.get(draftKey()).rows[0].Type==='BO');
DraftStore.set('ocrdraft:v1:old',{savedAt:Date.now()-25*3600*1000,rows:[]});
DraftStore.set('ocrdraft:v1:new',{savedAt:Date.now(),rows:[]});
DraftStore.sweep(24*3600*1000);
ok('stale swept', DraftStore.get('ocrdraft:v1:old')===null);
ok('fresh kept', DraftStore.get('ocrdraft:v1:new')!==null);
T.head('spare-row growth');
ok('blankRow blank', isRowBlank(blankRow()));
ok('typed row not blank', !isRowBlank(mkRow('BO','2','X')));
spreadsheetData=[mkRow('BO','2','X'),blankRow(),blankRow(),blankRow()];
ok('trailing blanks trimmed', rowsForSave().length===1);
spreadsheetData=[blankRow(),blankRow()];
ok('all blank -> 0', rowsForSave().length===0);
T.head('normalizeRow');
const n=normalizeRow({Type:'Child Part','Matched Childpart':'D-1',
  Quantity:{text:'5',confidence:0,ocr:true},Description:{text:'x',confidence:0.97,ocr:true},
  _missingDrawing:true,_similarityScore:0.8});
ok('Type kept', n.Type==='Child Part');
ok('ocr flag kept', n.Quantity.ocr===true);
ok('confidence kept', n.Description.confidence===0.97);
ok('_missingDrawing kept', n._missingDrawing===true);
ok('_similarityScore kept', n._similarityScore===0.8);
ok('absent flag stays absent', !('_needsReview' in n));
const n2=normalizeRow({Quantity:'corrupt',Type:42});
ok('corrupt cell -> shape', n2.Quantity.text===''&&n2.Quantity.confidence===0);
ok('bad Type -> empty', n2.Type==='');
ok('null -> blank row', isRowBlank(normalizeRow(null)));
T.head('locks / unresolved');
// Locks are now derived from _sentKey on the row objects, so the table has to
// contain the rows the submission refers to.
spreadsheetData=[mkRow('Child Part','1','A'),mkRow('BO','1','B'),
                 mkRow('Child Part','1','C'),mkRow('BO','1','D')];
spreadsheetData[0]._sentKey='k0'; spreadsheetData[2]._sentKey='k2'; spreadsheetData[3]._sentKey='k3';
_submitState={childParts:{status:'done',sent:[{_sentKey:'k0'},{_sentKey:'k2'}],payload:[]},
  pdfs:{status:'skipped'},boParts:{status:'failed',sent:[],payload:[{_sentKey:'k3'}]}};
recomputeLockedRows();
ok('done rows locked', _lockedRows.has(0)&&_lockedRows.has(2));
ok('failed rows not locked', !_lockedRows.has(3));
ok('unresolved', hasUnresolvedSubmit()===true);
_submitState.boParts.sent=_submitState.boParts.payload; _submitState.boParts.payload=[];
_submitState.boParts.status='done'; recomputeLockedRows();
ok('lock follows the row when one is inserted above',
   (spreadsheetData.unshift(blankRow()), recomputeLockedRows(),
    _lockedRows.has(1) && _lockedRows.has(3) && _lockedRows.has(4)));
spreadsheetData.shift(); recomputeLockedRows();
ok('all done -> resolved', hasUnresolvedSubmit()===false);
ok('bo rows now locked', _lockedRows.has(3));
_submitState=null; ok('no state -> resolved', hasUnresolvedSubmit()===false);
T.head('quota eviction');
DraftStore.set('ocrdraft:v1:a',{savedAt:1000,rows:[]});
DraftStore.set('ocrdraft:v1:b',{savedAt:2000,rows:[]});
window.localStorage.__failNext();
ok('write retried after purge', DraftStore.set('ocrdraft:v1:c',{savedAt:3000,rows:[]})===true);
ok('oldest evicted', DraftStore.get('ocrdraft:v1:a')===null);
ok('newer kept', DraftStore.get('ocrdraft:v1:b')!==null);
T.head('describeAge');
ok('just now', describeAge(5000)==='just now');
ok('12 min', describeAge(12*60000)==='12 minutes ago');
ok('1 hour', describeAge(3600000)==='1 hour ago');
T.done();
