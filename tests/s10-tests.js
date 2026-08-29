const ok=T.ok;
const cell=t=>({text:t,confidence:0.9,ocr:true});
const mk=(type,part)=>({PartNumber:cell(part),Type:type,"Matched Childpart":part,
  Quantity:cell('1'),Description:cell('d'),Material:cell('m')});
const partial = () => {
  _submitState = buildSubmitState();
  _submitState.childParts.sent = _submitState.childParts.payload;
  _submitState.childParts.payload = [];
  _submitState.childParts.status='done';
  _submitState.boParts.status='failed';
  recomputeLockedRows();
};
const toSend = () => _submitState.childParts.payload.map(r=>r.drawingNumber);

T.head('row INSERTED ABOVE a saved row');
spreadsheetData=[mk('Child Part','CP-A'), mk('BO','BO-1')]; partial();
ok('saved row locked at index 0', _lockedRows.has(0));
spreadsheetData.unshift(mk('Child Part','CP-NEW'));
recomputeLockedRows();
ok('lock followed the row to index 1', _lockedRows.has(1) && !_lockedRows.has(0));
rebuildOutstandingStages();
ok('saved row NOT re-sent', !toSend().includes('CP-A'));
ok('new row IS sent', toSend().includes('CP-NEW'));

T.head('row DELETED above a saved row');
spreadsheetData=[mk('Child Part','CP-X'), mk('Child Part','CP-A'), mk('BO','BO-1')];
_submitState = buildSubmitState();
_submitState.childParts.sent = _submitState.childParts.payload.filter(r=>r.drawingNumber==='CP-A');
_submitState.childParts.payload = [];
_submitState.childParts.status='done'; _submitState.boParts.status='failed';
recomputeLockedRows();
ok('CP-A locked at index 1', _lockedRows.has(1));
spreadsheetData.splice(0,1);
recomputeLockedRows();
ok('lock followed CP-A to index 0', _lockedRows.has(0) && !_lockedRows.has(1));
rebuildOutstandingStages();
ok('CP-A not re-sent', !toSend().includes('CP-A'));
ok('deleted row is not queued', !toSend().includes('CP-X'));
ok('nothing left to send for child parts', toSend().length === 0);

T.head('append still behaves (regression guard)');
spreadsheetData=[mk('Child Part','CP-A'), mk('BO','BO-1')]; partial();
spreadsheetData.push(mk('Child Part','CP-END'));
rebuildOutstandingStages();
ok('saved row not re-sent', !toSend().includes('CP-A'));
ok('appended row sent', toSend().includes('CP-END'));

T.head('internal markers never reach the backend');
ok('_sentKey stripped', stripInternal(_submitState.childParts.payload).every(r=>!('_sentKey' in r)));
ok('_pRow stripped',    stripInternal(_submitState.childParts.payload).every(r=>!('_pRow' in r)));

T.head('fingerprint ignores render-written fields');
const base = mk('Child Part','D-1');
const a = draftFingerprint([base], null);
ok('_similarityScore is not a change', a === draftFingerprint([Object.assign({},base,{_similarityScore:0.87})], null));
const stored = Object.assign({}, base, {_missingDrawing:false});
ok('stored vs normalised agree', draftFingerprint([stored], null) === draftFingerprint([normalizeRow(stored)], null));
ok('a real edit still counts', a !== draftFingerprint([Object.assign({},base,{Quantity:cell('9')})], null));
T.done();
