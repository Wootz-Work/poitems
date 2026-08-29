const ok=T.ok;
T.head('critical message is NOT wiped by the follow-up refresh');
showNotification('"D-1" is already used in another Child Part row.','error',0);
ok('error displayed', /already used/.test(TEXT.textContent));
showDefaultInstructions();                    // what refreshNotifications() does next tick
ok('survives instructions fallback', /already used/.test(TEXT.textContent));
showNotification('ok!','success',0);
ok('survives a success message too', /already used/.test(TEXT.textContent));

T.head('it persists indefinitely - no timer');
for(let i=0;i<50;i++) showDefaultInstructions();
ok('still there after 50 refreshes', /already used/.test(TEXT.textContent));

T.head('resolved only by a real signal');
resetNotificationGate();                      // user makes another edit
showDefaultInstructions();
ok('cleared after the user edits again', TEXT.textContent===DEFAULT_INSTRUCTIONS);

T.head('an uncorrected issue simply reappears');
showNotification('Part Number Not Found','error',0);
resetNotificationGate();                      // user edits something unrelated
showNotification('Part Number Not Found','error',0);   // scan re-detects it
ok('still shown while the row is wrong', TEXT.textContent==='Part Number Not Found');

T.head('ranking while sticky');
hideNotification();
showNotification('warn','warning',0);  ok('warning shows', TEXT.textContent==='warn');
showNotification('boom','error',0);    ok('error replaces warning', TEXT.textContent==='boom');
showNotification('yay','success',0);   ok('success cannot replace error', TEXT.textContent==='boom');
showNotification('worse','error',0);   ok('error replaces error', TEXT.textContent==='worse');
showNotification('warn2','warning',0); ok('warning cannot replace error', TEXT.textContent==='worse');

T.head('dismissal resolves');
hideNotification(); ok('hidden', AREA.className==='notification-hidden');
showNotification(DEFAULT_INSTRUCTIONS,'instructions',0);
ok('instructions show after dismissal', TEXT.textContent===DEFAULT_INSTRUCTIONS);

T.head('non-sticky messages behave normally');
showNotification('done','success',0);
ok('success displaces instructions', TEXT.textContent==='done');
showDefaultInstructions();
ok('instructions may follow a success', TEXT.textContent===DEFAULT_INSTRUCTIONS);

T.head('force escape hatch');
showNotification('sticky','error',0);
showNotification('override','success',0,{force:true});
ok('force bypasses stickiness', TEXT.textContent==='override');
T.done();
