const AREA={className:'notification-hidden',classList:{
  contains(c){return AREA.className.split(/\s+/).includes(c);},
  remove(c){AREA.className=AREA.className.split(/\s+/).filter(x=>x!==c).join(' ');},
  add(c){AREA.className=(AREA.className+' '+c).trim();}}};
const ICON={innerHTML:''},TEXT={textContent:''};
global.document={getElementById:id=>({'notification-area':AREA,'notification-icon':ICON,'notification-text':TEXT}[id]||null),
  querySelectorAll:()=>[]};
function clearNotificationActions(){}
const _notifGate={active:false};
const DEFAULT_INSTRUCTIONS='To Bulk Add Upload Screenshot in table header ';
function showDefaultInstructions(){showNotification(DEFAULT_INSTRUCTIONS,'instructions',0);}
// mirrors the real resetNotificationGate(), called at the top of afterChange
function resetNotificationGate(){_notifGate.active=false;_notifSticky=false;}
