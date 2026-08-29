# Browser tests

Manual tests for the things the headless suites can't reach: Handsontable
rendering, the read-only lock, the modals, and the real Glide round-trip.

> **These write to the live Glide tables.** The backend's table IDs are hardcoded,
> so it does not matter which Render service is called — a submit is a real
> submit. **Use a disposable test part number for everything below**, and keep the
> Glide Child Parts / BO Parts / Drawing tables open in another tab to verify.

---

## Setup: getting a console inside the embed

The app runs in an iframe inside Glide. Commands must run **in the iframe**, not
the Glide page that hosts it — otherwise you are inspecting Glide's storage, not
ours.

**Brave / Chrome / Edge**

1. Open your Glide app and go to the screen containing the Web Embed.
2. Open DevTools — `Cmd+Option+I` (Mac) or `F12` (Windows).
3. Go to the **Network** tab and tick **Preserve log**, so the record survives
   Glide reloading the embed. Then go to the **Console** tab.
4. At the top of the Console there is a dropdown that says **`top`**. Click it and
   choose the entry showing the `veenu-wootz.github.io` URL.

Everything you type now runs inside the app. If you skip step 4, the snippets
below will appear to do nothing.

**Safari**

1. Safari → Settings → Advanced → tick **"Show features for web developers"**
   (older versions: "Show Develop menu in menu bar").
2. Open the app, then `Cmd+Option+I`.
3. Console tab → use the frame/context picker and select the `github.io` frame.

---

## The failure-injection snippet

Paste this into the iframe console. It makes one endpoint fail while everything
else works normally.

Set `__breakUrl` to whichever you want to break: `/add-bo-parts`,
`/add-child-parts` or `/generate-missing-childpart-pdf`.

> No `//` comments below, deliberately. Some consoles collapse newlines when you
> paste, which turns everything after the first `//` into a comment and silently
> swallows the rest of the snippet.

```js
window.__origFetch = window.__origFetch || window.fetch;
window.__breakUrl = '/add-bo-parts';
window.fetch = (url, opts) => {
  if (String(url).includes(window.__breakUrl)) {
    return Promise.resolve(new Response('forced failure', { status: 500 }));
  }
  return window.__origFetch(url, opts);
};
console.log('[test] breaking ' + window.__breakUrl);
```

**To stop breaking it** — use this exact snippet, which is safe to run at any time:

```js
if (typeof window.__origFetch === 'function') {
  window.fetch = window.__origFetch;
  console.log('[test] restored');
} else {
  console.log('[test] nothing to restore — fetch is already the real one');
}
```

> ⚠️ **Never run a bare `window.fetch = window.__origFetch;`.**
> If the page has reloaded since you injected the failure, `__origFetch` is
> `undefined`, and that line assigns `undefined` to `window.fetch` — **deleting the
> browser's fetch entirely.** Every request then fails with *"fetch is not a
> function"*, including ones you never meant to break. If it happens, just reload
> the page.

### Reading `undefined` in the console

Two different meanings, worth separating:

| You ran | Console shows `undefined` means |
|---|---|
| `window.fetch = window.__origFetch;` | the assigned value was `undefined` — **fetch is now broken** |
| The `if (...) { } else { }` snippet | nothing at all; an if-statement always evaluates to `undefined`. Read the printed `[test] …` line instead |
| `hot.refreshDimensions()` | nothing; that method returns no value. It still ran |

Only the first is a problem. If a call had genuinely failed you would see an
error such as *"Cannot read properties of undefined"*, not a plain `undefined`.

Three things to know:

- **Reloading the page undoes the injection by itself.** A reload gives a fresh
  `window`, so `fetch` is already normal and `__origFetch` is gone. After any
  reload you must paste the injection snippet again — and you do *not* need the
  undo.
- **A 500 is different from a blocked request.** This snippet returns a real HTTP
  error, so the stage is recorded as `failed`. Blocking the request in the Network
  panel makes fetch *throw*, which the app records as `pending` — "sent, outcome
  unknown" — and the modal warns you to check Glide before resending. Both paths
  retry correctly; the wording differs because the risk does.
- **Tick "Preserve log"** at the top of the Network panel before you start.
  Without it, the log is wiped every time Glide reloads the embed, and it will
  look as though no requests were made.

---

## Test A — staged submit does not duplicate rows

**The point:** after a partial failure, a retry must send only the missing part.

1. Open the app with a disposable test part.
2. Console → paste the snippet with `__breakUrl = '/add-bo-parts'`.
3. Fill in at least **one Child Part row** and **one BO row**. Each needs Type,
   Quantity and Part Number, or submit-time validation will stop you.
4. Press **Submit**.

Expected:

- Notification names what already saved: *"Already saved (not resent): Child
  Parts."* Full detail goes to the console, not the bar.
- The **Submit button now reads "Submit Remaining"**.
- The Child Part rows are dimmed with a green edge and **read-only** — click one
  and try to type; it should refuse.
- Network tab: `add-child-parts` → 200, `add-bo-parts` → 500.
- In Glide: Child Parts has your rows, BO Parts has nothing.

5. Console → run the **restore snippet above** (not a bare assignment).
6. Press **Submit Remaining**.

Expected — **this is the assertion that matters**:

- In the Network tab, filter for `add-` and count. You should see
  **`add-child-parts` ×1** and **`add-bo-parts` ×2** for the whole session.
  A second `add-child-parts` would mean the fix is broken.
- Success notification, table clears, Submit reads "Submit" again.
- In Glide: Child Parts still has **exactly** the original rows — no duplicates.
  BO Parts now has its rows.

### A2 — the placeholder drawing

Same shape, for the non-idempotent PDF step.

1. Fresh test part. Set one row's Type to **Missing Drawing**.
2. Snippet with `__breakUrl = '/generate-missing-childpart-pdf'`.
3. Submit. Expect an error naming the part whose placeholder failed, and Child
   Parts already saved.
4. Run the restore snippet, press the button again.
5. In Glide's **Drawing** table: exactly **one** placeholder row for that part.
   Two would mean the per-part tracking is broken.

---

## Test B — fix-and-retry, and adding rows

**The point:** a retry must send your corrections and any rows you added, not a
stale copy of the original attempt.

1. Get into the failed state from Test A (BO parts failed, not yet retried).
2. **Correct** the BO row that failed — change its Quantity to something
   distinctive, e.g. `77`.
3. **Add a new BO row** — Type BO, a quantity, a part number.
4. Run the restore snippet, press **Submit Remaining**.
5. Network tab → click the `add-bo-parts` request → **Payload** tab (Chrome/Brave)
   or **Request** tab (Safari).

Expected in the JSON:

- `rows` has **2** entries — the corrected one and the new one.
- The first shows `"quantity": "77"` — your correction, not the original value.
- The new row has a **higher `itemNumber`**, and no `itemNumber` repeats.
- No `itemNumber` collides with the ones the Child Parts request already used
  (scroll back to that earlier request to compare).

---

## Test C — Safari storage reality check

You have already run the practical version of this; these are the precise steps
if you want to confirm.

In the **iframe console** on Safari:

```js
localStorage.setItem('t', String(Date.now()));
localStorage.getItem('t');   // note the number
```

Then check `localStorage.getItem('t')` again after each of:

1. Reloading the embed
2. Navigating away in Glide and coming back
3. **Quitting Safari entirely** (`Cmd+Q`) and reopening

Whatever returns `null` marks the boundary of what Safari keeps. Expected from
earlier testing: 1 and 2 survive, 3 does not — which is why the
**"Autosave: session only"** pill appears on Safari.

---

## Test D — draft restore behaviour

1. Enter some data. Reload. → Modal: *Unsaved work found*. Press **Restore**.
2. Reload again **without changing anything**. → No modal; a brief
   *"Restored N rows"* notification instead.
3. Note the age in the modal, wait a couple of minutes, reload. → The age should
   have **grown**. If it says "just now" every time, the timestamp fix regressed.
4. Change a cell, then reload. → Modal returns, because there is a new decision
   to make.
5. Open the app fresh, touch nothing, leave, come back. → **No modal at all**;
   an untouched session must not leave a draft behind.

---

## Test E — row styling

1. Set a row's Type to **BO** → the row goes italic.
2. Change it back to **Child Part** → the italic must clear.

This one regressed before: cell metadata persists between renders, so the class
was applied but never removed.

---

## Troubleshooting: "the table only responds when DevTools is open"

Opening DevTools is itself a change — it resizes the viewport, which makes
Handsontable recalculate. So the console cannot diagnose this; you have to rule
things out from the outside in.

### Step 1 — what is actually receiving the click?

The most decisive test, and it needs no code.

**Right-click directly on a cell you cannot edit → Inspect.**

Look at which element highlights in the Elements panel:

- **`<div id="draft-modal-backdrop">`** → an invisible full-screen overlay is
  swallowing your clicks. That is the bug, and it is ours.
- **the `<td>` itself** → clicks are reaching the cell, so the problem is inside
  Handsontable (dimensions or focus), not an overlay.

### Step 2 — separate "DevTools open" from "viewport resized"

1. Open DevTools, then undock it into its own window
   (**⋮ menu → Dock side → Undock into separate window**).
2. Reload and reproduce the failed state.
3. Without touching the DevTools window, try to edit a BO cell.

- **Still broken while DevTools is open but undocked** → the console is not the
  factor; the *resize* was. Points at stale Handsontable dimensions.
- **Works fine undocked** → it is about focus moving between windows, not size.

### Step 3 — one-shot state dump

Paste this when the table is unresponsive. No `//` comments, safe to paste.

```js
(function(){
  var r = {};
  var b = document.getElementById('draft-modal-backdrop');
  r.modalBackdrop = b ? getComputedStyle(b).display : 'missing';
  r.modalPointerEvents = b ? getComputedStyle(b).pointerEvents : '-';
  try { r.lockedRows = Array.from(_lockedRows); } catch (e) { r.lockedRows = 'unreachable'; }
  try {
    r.rowCount = hot.countRows();
    r.readOnlyFirst6 = [];
    for (var i = 0; i < Math.min(r.rowCount, 6); i++) {
      r.readOnlyFirst6.push(i + ':' + !!hot.getCellMeta(i, 0).readOnly);
    }
    r.containerHeight = document.getElementById('hot').clientHeight;
    r.hotHeight = hot.rootElement.clientHeight;
  } catch (e) { r.hot = 'unreachable: ' + e.message; }
  console.log(JSON.stringify(r, null, 2));
  return r;
})()
```

How to read it:

| Result | Meaning |
|---|---|
| `modalBackdrop: "block"` | the overlay never closed — it is eating every click |
| `readOnlyFirst6` shows `true` on a row that is not a saved Child Part | the row-locking is wrong |
| `containerHeight` and `hotHeight` differ noticeably | Handsontable's cached size is stale; `hot.refreshDimensions()` should fix it |
| everything looks right | the problem is focus, not layout or locking |
