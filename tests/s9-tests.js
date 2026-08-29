const ok=T.ok;
T.head('first paint: hot is not assigned yet');
let threw=null;
try { refreshNotifications(); } catch (e) { threw = e; }
ok('refreshNotifications does not throw', threw===null);
ok('and writes nothing', NOTES.length===0);

T.head('once hot exists it scans normally');
hot = { countRows: () => 0, getSourceDataAtRow: () => null };
threw=null;
try { refreshNotifications(); } catch (e) { threw = e; }
ok('still no throw', threw===null);
ok('falls back to the default instructions', NOTES.some(n=>n.m==='instructions'));
T.done();
