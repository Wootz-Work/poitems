const {mkRow}=mkTable(); const ok=T.ok;
const key=draftKey();
const read=()=>DraftStore.get(key);

T.head('untouched session leaves no draft behind');
_draftReady=true;
spreadsheetData=[blankRow(),blankRow(),blankRow()];
saveDraftNow();
ok('nothing written for an empty table', read()===null);

T.head('savedAt means last MODIFIED, not last written');
spreadsheetData=[mkRow('Child Part','2','D-1')];
saveDraftNow();
const firstWrite=read().savedAt;
ok('draft written once there is data', firstWrite>0);
// simulate reload/navigation flushes with no content change
const later=firstWrite+60000;
const realNow=Date.now; Date.now=()=>later;
saveDraftNow(); saveDraftNow(); saveDraftNow();
ok('no-op flushes do not rewrite savedAt', read().savedAt===firstWrite);
ok('age is preserved, not reset', read().savedAt!==later);
// a real edit must update it
spreadsheetData[0].Quantity.text='9';
saveDraftNow();
ok('a genuine change does update savedAt', read().savedAt===later);
Date.now=realNow;

T.head('emptying the table removes the draft');
spreadsheetData=[blankRow()];
saveDraftNow();
ok('draft deleted when data is gone', read()===null);

T.head('fingerprint ignores the timestamp');
const a=draftFingerprint([mkRow('BO','1','X')],null);
const b=draftFingerprint([mkRow('BO','1','X')],null);
ok('same content -> same fingerprint', a===b);
ok('different content -> different fingerprint',
   a!==draftFingerprint([mkRow('BO','2','X')],null));
ok('submitState is part of it',
   a!==draftFingerprint([mkRow('BO','1','X')],{childParts:{status:'done'}}));

T.head('acknowledgement is stored with the content it refers to');
spreadsheetData=[mkRow('Child Part','2','D-1')];
_acknowledgedFingerprint=null; _lastWrittenFingerprint=null;
saveDraftNow();
ok('unacknowledged by default', read().acknowledged===null);
_acknowledgedFingerprint=draftFingerprint(rowsForSave(),_submitState);
_lastWrittenFingerprint=null; saveDraftNow();
ok('acknowledgement persisted', read().acknowledged===_acknowledgedFingerprint);
ok('matches current content', read().acknowledged===draftFingerprint(read().rows,read().submitState));

T.head('editing after acknowledging invalidates it');
spreadsheetData[0].Quantity.text='77';
saveDraftNow();
const d=read();
ok('stored acknowledgement no longer matches content',
   d.acknowledged!==draftFingerprint(d.rows,d.submitState));

T.head('clearDraft resets everything');
clearDraft();
ok('key removed', read()===null);
ok('acknowledgement cleared', _acknowledgedFingerprint===null);
ok('write fingerprint cleared', _lastWrittenFingerprint===null);
T.done();
