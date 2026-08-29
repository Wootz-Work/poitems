import re, sys, os
SRC = sys.argv[1]; OUT = sys.argv[2]
src = open(SRC, encoding='utf-8').read()
def grab(a, b, name):
    open(os.path.join(OUT, name), 'w', encoding='utf-8').write(src[src.index(a):src.index(b)])
grab('const DRAFT_PREFIX', '// === One-shot notification gate', 'draftcore.js')
grab('        // Payload rows carry a _pRow marker', '        function announceUnresolvedSubmit', 'submitcore.js')
grab('        function rebuildOutstandingStages()', '        function announceUnresolvedSubmit', 'rebuild.js')
grab('        async function sendDataToBackend(opts = {})', '        // --- Ensure OCR upload header areas', 'sendfn.js')
grab('// Physical row indices that repeat a drawing', 'window.addEventListener("DOMContentLoaded"', 'dupes.js')
grab('      function validateRequiredFields', '        function clearTableToDefault', 'validate.js')
grab('        // Global ranking across ALL notification callers',
     '// --- Default instructions text (single source of truth) ---', 'notif.js')
grab('        function refreshNotifications', '      function validateRequiredFields', 'notifscan.js')
blocks = re.findall(r'<script>(.*?)</script>', re.sub(r'<!--.*?-->', '', src, flags=re.S), re.S)
open(os.path.join(OUT, 'all.js'), 'w', encoding='utf-8').write(blocks[0])
print(f"extracted 8 fragments from {len(blocks[0].splitlines())} script lines")
