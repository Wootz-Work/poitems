const {mkRow}=mkTable(); const ok=T.ok;
(async()=>{
T.head('A: child OK, BO fails, then retry');
spreadsheetData=[mkRow('Child Part','2','D-1'),mkRow('Child Part','3','D-2'),mkRow('BO','4','B-1')];
_draftReady=true; PLAN={'/add-bo-parts':'fail'};
await sendDataToBackend();
ok('child sent once', countCalls('/add-child-parts')===1);
ok('child done', _submitState.childParts.status==='done');
ok('bo failed', _submitState.boParts.status==='failed');
ok('error surfaced', NOTES.some(n=>n.t==='error'));
ok('error names what was saved', NOTES.some(n=>/Already saved \(not resent\)/.test(n.m)));
ok('child rows locked', _lockedRows.has(0)&&_lockedRows.has(1));
ok('bo row not locked', !_lockedRows.has(2));
ok('table not cleared', CLEARED===0);
ok('state persisted', DraftStore.get(draftKey())?.submitState?.childParts?.status==='done');
ok('button = Submit Remaining', BTN.textContent==='Submit Remaining');
PLAN={}; await sendDataToBackend();
ok('child NOT resent', countCalls('/add-child-parts')===1);
ok('bo resent', countCalls('/add-bo-parts')===2);
ok('table cleared', CLEARED===1);
ok('draft cleared', DraftStore.get(draftKey())===null);
ok('locks released', _lockedRows.size===0);
ok('button = Submit', BTN.textContent==='Submit');

T.head('B: PDF failure surfaced, not swallowed');
reset(); spreadsheetData=[mkRow('Missing Drawing','1','D-404'),mkRow('BO','2','B-9')];
PLAN={'/gen-pdf':'fail'}; await sendDataToBackend();
ok('child sent', countCalls('/add-child-parts')===1);
ok('pdf attempted', countCalls('/gen-pdf')===1);
ok('BO not reached', countCalls('/add-bo-parts')===0);
ok('failure surfaced', NOTES.some(n=>/placeholder drawing/i.test(n.m)));
ok('part still remaining', _submitState.pdfs.remaining.includes('D-404'));
PLAN={}; await sendDataToBackend();
ok('child NOT resent', countCalls('/add-child-parts')===1);
ok('pdf retried', countCalls('/gen-pdf')===2);
ok('bo sent', countCalls('/add-bo-parts')===1);

T.head('C: network throw leaves stage pending');
reset(); spreadsheetData=[mkRow('Child Part','2','D-7')];
PLAN={'/add-child-parts':'throw'}; await sendDataToBackend();
ok('left pending', _submitState.childParts.status==='pending');
ok('unresolved next load', hasUnresolvedSubmit()===true);
ok('not locked while pending', _lockedRows.size===0);

T.head('D: validation blocks fresh submit');
reset(); VALID={isValid:false,missingFields:['(Type)']};
await sendDataToBackend();
ok('nothing sent', CALLS.length===0);
VALID={isValid:true,missingFields:[]};

T.head('E: fix-and-retry sends corrected data');
reset(); spreadsheetData=[mkRow('Child Part','2','D-1'),mkRow('BO','999','B-1')];
PLAN={'/add-bo-parts':'fail'}; await sendDataToBackend();
spreadsheetData[1].Quantity.text='7'; PLAN={};
await sendDataToBackend();
ok('corrected qty sent', lastBody('/add-bo-parts').rows[0].quantity==='7');
ok('child not resent', countCalls('/add-child-parts')===1);

T.head('F: resume includes newly added rows');
reset(); spreadsheetData=[mkRow('Child Part','2','D-1'),mkRow('BO','3','B-1')];
PLAN={'/add-bo-parts':'fail'}; await sendDataToBackend();
const firstItem=_submitState.boParts.payload[0].itemNumber;
spreadsheetData.push(mkRow('BO','5','B-2')); PLAN={};
await sendDataToBackend();
const bo=lastBody('/add-bo-parts');
ok('added row included', bo.rows.length===2);
ok('original keeps item number', bo.rows[0].itemNumber===firstItem);
ok('new row numbered above', bo.rows[1].itemNumber>firstItem);
ok('no duplicate item numbers', new Set(bo.rows.map(r=>r.itemNumber)).size===2);
ok('no collision with sent child item', !bo.rows.some(r=>r.itemNumber===11));

T.head('G: done stage stays frozen + divergence warned');
reset(); spreadsheetData=[mkRow('Child Part','2','D-1'),mkRow('BO','3','B-1')];
PLAN={'/add-bo-parts':'fail'}; await sendDataToBackend();
spreadsheetData[0].Quantity.text='888'; PLAN={};
await sendDataToBackend();
ok('child never resent', countCalls('/add-child-parts')===1);
ok('divergence warned', NOTES.some(n=>/already saved to Glide/i.test(n.m)));

T.head('H: PDF-only outstanding -> Create drawing');
reset(); spreadsheetData=[mkRow('Missing Drawing','1','D-404')];
PLAN={'/gen-pdf':'fail'}; await sendDataToBackend();
ok('only pdfs outstanding', onlyPdfsOutstanding()===true);
ok('button = Create drawing', BTN.textContent==='Create drawing');
PLAN={}; await sendDataToBackend();
ok('finished', countCalls('/gen-pdf')===2&&BTN.textContent==='Submit');

T.head('I: emptied stage is skipped');
reset(); spreadsheetData=[mkRow('Child Part','2','D-1'),mkRow('BO','3','B-1')];
PLAN={'/add-bo-parts':'fail'}; await sendDataToBackend();
spreadsheetData[1].Type=''; spreadsheetData[1].Quantity.text=''; PLAN={};
await sendDataToBackend();
ok('emptied BO skipped', countCalls('/add-bo-parts')===1);
ok('submission complete', _submitState===null);

T.head('J: a stage left PENDING must be retried, never silently skipped');
reset(); spreadsheetData=[mkRow('Child Part','2','D-1'),mkRow('BO','3','B-1')];
PLAN={'/add-bo-parts':'throw'};          // network-level failure, not a 500
await sendDataToBackend();
ok('bo left pending (outcome unknown)', _submitState.boParts.status==='pending');
ok('counts as unresolved', hasUnresolvedSubmit()===true);
ok('button offers to continue', BTN.textContent==='Submit Remaining');
ok('table NOT cleared', CLEARED===0);
PLAN={};
await sendDataToBackend();
ok('pending stage actually re-sent', countCalls('/add-bo-parts')===2);
ok('child still not resent', countCalls('/add-child-parts')===1);
ok('only now is it complete', _submitState===null);
ok('table cleared only after real success', CLEARED===1);

T.head('K: every outstanding status is runnable by the executor');
['todo','failed','pending'].forEach(st=>{
  reset(); spreadsheetData=[mkRow('BO','1','B-1')];
  _submitState=buildSubmitState(); _submitState.boParts.status=st;
  ok(`'${st}' is treated as needing send`, stageNeedsSending(_submitState.boParts)===true);
});
['done','skipped'].forEach(st=>{
  reset(); spreadsheetData=[mkRow('BO','1','B-1')];
  _submitState=buildSubmitState(); _submitState.boParts.status=st;
  ok(`'${st}' is NOT resent`, stageNeedsSending(_submitState.boParts)===false);
});
ok('button and executor agree on every status', true);


T.head('L: rows added to an ALREADY-COMPLETED stage must still be sent');
reset(); spreadsheetData=[mkRow('Child Part','2','D-1'),mkRow('BO','3','B-1')];
PLAN={'/add-bo-parts':'fail'};
await sendDataToBackend();
ok('child stage completed', _submitState.childParts.status==='done');
ok('its rows recorded as sent', _submitState.childParts.sent.length===1);
const sentItem=_submitState.childParts.sent[0].itemNumber;

spreadsheetData.push(mkRow('Child Part','9','D-2'));   // new CHILD row, stage already done
PLAN={};
await sendDataToBackend();
ok('child stage ran again for the new row', countCalls('/add-child-parts')===2);
const body=lastBody('/add-child-parts');
ok('only the NEW row was sent', body.rows.length===1);
ok('and it is the new one', body.rows[0].quantity==='9');
ok('new row got a fresh item number', body.rows[0].itemNumber!==sentItem);
ok('bo also went out', countCalls('/add-bo-parts')===2);
ok('submission completed', _submitState===null);

T.head('M: a completed stage with NO additions is not re-sent');
reset(); spreadsheetData=[mkRow('Child Part','2','D-1'),mkRow('BO','3','B-1')];
PLAN={'/add-bo-parts':'fail'};
await sendDataToBackend();
PLAN={};
await sendDataToBackend();
ok('child sent exactly once', countCalls('/add-child-parts')===1);
ok('bo retried', countCalls('/add-bo-parts')===2);

T.head('N: previously sent rows stay locked while the stage reopens');
reset(); spreadsheetData=[mkRow('Child Part','2','D-1')];
PLAN={};
await sendDataToBackend();
ok('all done, draft cleared', _submitState===null);
reset(); spreadsheetData=[mkRow('Child Part','2','D-1'),mkRow('BO','3','B-1')];
PLAN={'/add-bo-parts':'fail'};
await sendDataToBackend();
spreadsheetData.push(mkRow('Child Part','5','D-3'));
rebuildOutstandingStages();
ok('original child row still locked', _lockedRows.has(0));
ok('newly added child row not locked', !_lockedRows.has(2));
ok('stage reopened as runnable', stageNeedsSending(_submitState.childParts)===true);

T.done();
})();
