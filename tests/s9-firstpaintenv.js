const TYPE_DISPLAY_NAMES = { "Missing Childpart": "Missing Drawing" };
let spreadsheetData = [];
let hasWarningNotificationShown = false;
let hot;                       // exactly the state during the first paint
const NOTES = [];
function showNotification(m, t) { NOTES.push({ m, t }); }
function showDefaultInstructions() { NOTES.push({ m: 'instructions', t: 'instructions' }); }
function duplicateChildPartRows() { return new Set(); }
const document = { getElementById: () => null };
