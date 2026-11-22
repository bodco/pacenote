// Pacenote 0.26.3: tests of the tips window in a real browser engine (app/Resources/tips: index.html + tips.js).
// feed.test.js covers the feed adapter; this file covers the window itself: what the PM clicks and sees.
// Run: node app/WebTests/tips-window.test.js   (Node 22+, no packages; needs Google Chrome or Chromium)
//   CHROME=<path to the browser>   another browser binary
//   TIPS_DIR=<folder>              test another copy of app/Resources/tips (the gate uses it to prove a test catches a bug)
//   PACENOTE_STRICT_WEB=1          a missing browser is a failure, not a skip (the gate sets it on the Mac)
// The page runs on its built-in demo data (opened outside the app). Native messages are caught by a stand-in for
// window.webkit.messageHandlers.liveTips, so the test sees what the window would send to Swift.
'use strict';
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const TIPS = process.env.TIPS_DIR || path.join(__dirname, '../Resources/tips');
const STRICT = process.env.PACENOTE_STRICT_WEB === '1';
const CANDIDATES = [process.env.CHROME, '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium', '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
const CHROME = CANDIDATES.find((p) => p && fs.existsSync(p));

let failures = 0, passed = 0;
async function test(name, fn) { try { await fn(); passed++; } catch (e) { failures++; console.log('FAIL', name, '\n ', e.message); } }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function launch() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pn-tips-'));
  const p = spawn(CHROME, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + dir, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--allow-file-access-from-files', 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  return new Promise((resolve, reject) => {
    let err = '';
    const t = setTimeout(() => reject(new Error('browser did not start: ' + err.slice(-300))), 20000);
    p.stderr.on('data', (d) => {
      err += d;
      const m = err.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (m) { clearTimeout(t); resolve({ proc: p, port: new URL(m[1]).port, dir }); }
    });
  });
}

async function connect(port) {
  const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const page = list.find((x) => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0; const wait = new Map(); const events = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && wait.has(d.id)) { wait.get(d.id)(d); wait.delete(d.id); } else if (d.method) events.push(d);
  };
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; wait.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const evaluate = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result.exceptionDetails) throw new Error('page: ' + r.result.exceptionDetails.text + ' ' + JSON.stringify(r.result.exceptionDetails.exception || ''));
    return r.result.result.value;
  };
  return { ws, send, evaluate, events };
}

// What a check needs from the page, read in one call.
const SNAPSHOT = `(() => {
  const $ = (id) => document.getElementById(id);
  const pane = $('pane');
  return {
    askForm: !!$('ask') && $('ask').tagName === 'FORM',
    askInput: !!$('askInput'), askLbl: !!$('askLbl') && $('askLbl').textContent.trim().length > 0,
    paneTabs: pane ? pane.querySelectorAll('.tabs .tab').length : 0,
    paneBody: pane && pane.querySelector('.panebody') ? pane.querySelector('.panebody').textContent.trim().length : 0,
    paneClose: !!(pane && pane.querySelector('[data-act="pane-close"]')),
    selectedTab: (pane && pane.querySelector('.tab[aria-selected="true"]') || {}).dataset ? pane.querySelector('.tab[aria-selected="true"]').dataset.tab : null,
    paneOff: $('app').classList.contains('pane-off'),
    wide: $('app').clientWidth,
    posts: (window.__posts || []).map((m) => ({ action: m.action, on: m.on, where: m.where, msg: m.msg }))
  };
})()`;
const CATCH = `window.__posts = []; window.webkit = { messageHandlers: { liveTips: { postMessage: (m) => window.__posts.push(m) } } }; true`;

async function open(b, width) {
  await b.send('Emulation.setDeviceMetricsOverride', { width, height: 820, deviceScaleFactor: 1, mobile: false });
  await b.send('Page.navigate', { url: 'file://' + path.resolve(TIPS, 'index.html') });
  for (let i = 0; i < 50; i++) { await sleep(100); if (await b.evaluate('document.readyState') === 'complete') break; }
  await sleep(400);
  await b.evaluate(CATCH);             // after the demo data is in: the page keeps running as "in a browser"
}
const jsErrors = (b, s) => s.posts.filter((m) => m.action === 'jserror').map((m) => m.where + ': ' + m.msg)
  .concat(b.events.filter((e) => e.method === 'Runtime.exceptionThrown').map((e) => e.params.exceptionDetails.text))
  .concat(b.events.filter((e) => e.method === 'Runtime.consoleAPICalled' && JSON.stringify(e.params.args).includes('jserror'))
    .map((e) => JSON.stringify(e.params.args).slice(0, 200)));

(async () => {
  if (!CHROME) {
    console.log('tips window tests: no Chrome found' + (STRICT ? '' : ' (skipped)'));
    process.exit(STRICT ? 1 : 0);
  }
  const br = await launch();
  const b = await connect(br.port);
  await b.send('Page.enable'); await b.send('Runtime.enable');
  try {
    // Regression 20.11 (0.26.0-0.26.2): the inline question strip took the id of the question form, so the form was
    // emptied, render stopped on askLbl, and the side panel (Transcript, On me, Brief) stayed empty.
    await open(b, 1100);
    const s0 = await b.evaluate(SNAPSHOT);
    await test('wide window: the question form at the bottom is intact', () => {
      assert.ok(s0.askForm, 'form#ask exists'); assert.ok(s0.askInput, 'the question field exists'); assert.ok(s0.askLbl, 'the field has its label');
    });
    await test('wide window: the side panel has tabs, content and a Close button', () => {
      assert.ok(s0.paneTabs >= 2, 'tabs: ' + s0.paneTabs); assert.ok(s0.paneBody > 0, 'panel body is not empty'); assert.ok(s0.paneClose, 'Close button');
    });
    await test('wide window: no script errors after the first render', () => {
      assert.deepStrictEqual(jsErrors(b, s0), []);
    });
    await test('wide window: On me opens the On me tab, Close hides the panel', async () => {
      await b.evaluate(`document.getElementById('onmeBtn').click(); true`); await sleep(150);
      const s1 = await b.evaluate(SNAPSHOT);
      assert.strictEqual(s1.selectedTab, 'onme');
      await b.evaluate(`document.querySelector('#pane [data-act="pane-close"]').click(); true`); await sleep(150);
      const s2 = await b.evaluate(SNAPSHOT);
      assert.ok(s2.paneOff, 'panel hidden after Close');
      await b.evaluate(`document.getElementById('onmeBtn').click(); true`); await sleep(150);
      assert.ok(!(await b.evaluate(SNAPSHOT)).paneOff, 'On me shows the panel again');
    });

    // Narrow window: the panel asks the native side to widen the window, and to give the width back on Close.
    await open(b, 600);
    await test('narrow window: On me asks to widen the window, a second click gives the width back', async () => {
      await b.evaluate(`document.getElementById('onmeBtn').click(); true`); await sleep(150);
      const s1 = await b.evaluate(SNAPSHOT);
      assert.deepStrictEqual(s1.posts.filter((m) => m.action === 'transcript').map((m) => m.on), [true]);
      await b.evaluate(`document.getElementById('onmeBtn').click(); true`); await sleep(150);
      const s2 = await b.evaluate(SNAPSHOT);
      assert.deepStrictEqual(s2.posts.filter((m) => m.action === 'transcript').map((m) => m.on), [true, false]);
      assert.deepStrictEqual(jsErrors(b, s2), []);
    });

    // PB-49: the Transcript button, the call-language chip and menu, the two rows below 760 px.
    const rect = (id) => `(() => { const r = document.getElementById('${id}').getBoundingClientRect(); return { top: Math.round(r.top), left: Math.round(r.left), w: Math.round(r.width), h: Math.round(r.height) }; })()`;
    const openQ = async (width, qs) => {
      await b.send('Emulation.setDeviceMetricsOverride', { width, height: 820, deviceScaleFactor: 1, mobile: false });
      await b.send('Page.navigate', { url: 'file://' + path.resolve(TIPS, 'index.html') + (qs || '') });
      for (let i = 0; i < 50; i++) { await sleep(100); if (await b.evaluate('document.readyState') === 'complete') break; }
      await sleep(400); await b.evaluate(CATCH);
    };
    const posted = () => b.evaluate(`window.__posts.filter((m) => m.action === 'setting').map((m) => m.key + '=' + m.value)`);
    await open(b, 1100);
    await test('wide window: Transcript button sits left of On me, is pressed while the panel shows it, a second click closes the panel', async () => {
      const tb = await b.evaluate(rect('trBtn')), om = await b.evaluate(rect('onmeBtn'));
      assert.ok(tb.left < om.left, 'left of On me'); assert.strictEqual(tb.w, 28);
      assert.strictEqual(await b.evaluate(`document.getElementById('trBtn').getAttribute('aria-expanded')`), 'true');
      await b.evaluate(`document.getElementById('trBtn').click(); true`); await sleep(150);
      assert.ok((await b.evaluate(SNAPSHOT)).paneOff, 'closed by the second click');
      assert.strictEqual(await b.evaluate(`document.getElementById('trBtn').getAttribute('aria-expanded')`), 'false');
      await b.evaluate(`document.getElementById('trBtn').click(); true`); await sleep(150);
      assert.strictEqual((await b.evaluate(SNAPSHOT)).selectedTab, 'transcript');
    });
    await test('wide window: a click on Transcript while the panel shows On me switches to Transcript and does not close', async () => {
      await b.evaluate(`document.getElementById('onmeBtn').click(); true`); await sleep(150);
      await b.evaluate(`document.getElementById('trBtn').click(); true`); await sleep(150);
      const s1 = await b.evaluate(SNAPSHOT); assert.strictEqual(s1.selectedTab, 'transcript'); assert.ok(!s1.paneOff);
    });
    await test('Cmd+Shift+T toggles the transcript panel', async () => {
      await b.evaluate(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'T', metaKey: true, shiftKey: true, bubbles: true })); true`); await sleep(150);
      assert.ok((await b.evaluate(SNAPSHOT)).paneOff, 'closed');
      await b.evaluate(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 't', metaKey: true, shiftKey: true, bubbles: true })); true`); await sleep(150);
      assert.ok(!(await b.evaluate(SNAPSHOT)).paneOff, 'open again');
    });
    await test('language chip: UK/EN code at 12 px, a menu with one row per language plus mixed, a pick sends call_lang', async () => {
      assert.strictEqual(await b.evaluate(`document.querySelector('#langBtn .lc').textContent`), 'EN');
      assert.strictEqual(await b.evaluate(`getComputedStyle(document.getElementById('langBtn')).fontSize`), '12px');
      await b.evaluate(`document.getElementById('langBtn').click(); true`); await sleep(150);
      const rows = await b.evaluate(`Array.from(document.querySelectorAll('#langMenu [role=menuitemradio]')).map((x) => x.dataset.v + ':' + x.getAttribute('aria-checked'))`);
      assert.deepStrictEqual(rows, ['en:true', 'uk:false', 'en+uk:false']);
      assert.strictEqual(await b.evaluate(`document.querySelectorAll('#langMenu .tipsin').length`), 0, 'no Tips in row for a one-language call');
      await b.evaluate(`document.querySelector('#langMenu [data-v="en+uk"]').click(); true`); await sleep(150);
      const s1 = await b.evaluate(SNAPSHOT);
      assert.deepStrictEqual(await posted(), ['call_lang=en+uk']);
      assert.strictEqual(await b.evaluate(`document.querySelector('#langBtn .lc').textContent`), 'EN+UK', 'the chip follows at once, main language first');
      assert.strictEqual(await b.evaluate(`document.getElementById('langBtn').getAttribute('aria-busy')`), 'true');
      assert.deepStrictEqual(jsErrors(b, s1), []);
    });
    await test('language chip: a recorder that never echoes puts the chip back after 4 s and says so', async () => {
      await sleep(4400);
      assert.strictEqual(await b.evaluate(`document.querySelector('#langBtn .lc').textContent`), 'EN');
      assert.strictEqual(await b.evaluate(`document.getElementById('langBtn').getAttribute('aria-busy')`), null);
      assert.ok(/Could not change the language/.test(await b.evaluate(`document.getElementById('toast').textContent`)));
    });
    await test('language chip: a status echo confirms the switch; a mixed call has the Tips in row; Esc closes the menu', async () => {
      await b.evaluate(`document.getElementById('langBtn').click(); document.querySelector('#langMenu [data-v="en+uk"]').click(); true`); await sleep(150);
      await b.evaluate(`window.setStatus({ state: 'recording', call_lang: 'en+uk', me_lang: 'en', langs: ['en', 'uk'] }); true`); await sleep(200);
      assert.strictEqual(await b.evaluate(`document.getElementById('langBtn').getAttribute('aria-busy')`), null, 'echo cleared the pending state');
      assert.strictEqual(await b.evaluate(`document.querySelector('#langBtn .lc').textContent`), 'EN+UK');
      await b.evaluate(`document.getElementById('langBtn').click(); true`); await sleep(150);
      assert.strictEqual(await b.evaluate(`document.querySelectorAll('#langMenu .tipsin button').length`), 2);
      await b.evaluate(`document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); true`); await sleep(100);
      assert.strictEqual(await b.evaluate(`document.getElementById('langMenu').hidden`), true, 'Esc closes the menu');
      assert.strictEqual(await b.evaluate(`document.activeElement.id`), 'langBtn', 'focus returns to the chip');
    });
    await openQ(520, '?call_lang=uk+en');
    await test('520 px: two deliberate rows; Transcript and the chip on row 2, the exit group on row 1, no overflow', async () => {
      const home = await b.evaluate(rect('moreBtn')), tr = await b.evaluate(rect('trBtn')), ch = await b.evaluate(rect('langBtn')), rec = await b.evaluate(rect('rec'));
      assert.ok(home.top < tr.top, 'the menu button (exit group) is above the Transcript button');
      assert.ok(tr.top > rec.top && ch.top > rec.top, 'views and language controls on the second row ' + JSON.stringify({ home, tr, ch, rec }));
      assert.ok(ch.left + ch.w <= 520, 'chip inside the window');
      assert.strictEqual(await b.evaluate(`document.querySelector('#langBtn .lc').textContent`), 'UK+EN');
      assert.ok(await b.evaluate(`document.documentElement.scrollWidth <= 520`), 'no horizontal overflow');
    });
    await openQ(1100, '?call_lang=uk+en');
    await test('a mixed call: the menu has the Tips in segment and the mixed row is selected', async () => {
      await b.evaluate(`document.getElementById('langBtn').click(); true`); await sleep(150);
      assert.strictEqual(await b.evaluate(`document.querySelector('#langMenu [aria-checked=true]').dataset.v`), 'uk+en');
      await b.evaluate(`document.querySelector('#langMenu [data-l="en"]').click(); true`); await sleep(100);
      assert.ok((await posted()).includes('tips_lang=en'));
    });
    await openQ(1100, '?call_lang=uk+en');
    await test('a single-language pick keeps the mixed row of the Start Call set and changes tips only after the recorder confirms', async () => {
      await b.evaluate(`document.getElementById('langBtn').click(); document.querySelector('#langMenu [data-v="en"]').click(); true`); await sleep(150);
      assert.ok((await posted()).includes('call_lang=en'), 'call_lang sent at once');
      assert.ok(!(await posted()).some((x) => x === 'tips_lang=en'), 'no tips change before the echo');
      await b.evaluate(`window.setStatus({ state: 'recording', call_lang: 'en', me_lang: 'en', langs: ['en'] }); true`); await sleep(200);
      assert.ok((await posted()).includes('tips_lang=en'), 'tips follow after the echo');
      await b.evaluate(`document.getElementById('langBtn').click(); true`); await sleep(150);
      const rows = await b.evaluate(`Array.from(document.querySelectorAll('#langMenu [role=menuitemradio]')).map((x) => x.dataset.v)`);
      assert.ok(rows.includes('en') && rows.includes('uk+en'), rows.join());
    });
    await test('a failed single-language pick never changes the tips language', async () => {
      await openQ(1100, '?call_lang=uk+en');
      await b.evaluate(`document.getElementById('langBtn').click(); document.querySelector('#langMenu [data-v="en"]').click(); true`); await sleep(4400);
      assert.ok(!(await posted()).some((x) => x.indexOf('tips_lang') === 0), await posted());
    });
    await openQ(1100, '?state=ready&call_lang=uk+en');
    await test('Ready: the language chip is hidden', async () => {
      assert.strictEqual(await b.evaluate(`document.getElementById('langBtn').hidden`), true);
    });
    await openQ(600, '?mode=card');
    await test('compact card: a transcript icon before Expand and no language chip', async () => {
      const order = await b.evaluate(`Array.from(document.querySelectorAll('.tc-bar [data-act]')).map((x) => x.dataset.act)`);
      assert.ok(order.indexOf('card-transcript') >= 0 && order.indexOf('card-transcript') === order.indexOf('card-expand') - 1, order.join());
      assert.strictEqual(await b.evaluate(`document.querySelectorAll('.tc-bar .langbtn').length`), 0);
      await b.evaluate(`document.querySelector('[data-act=card-transcript]').click(); true`); await sleep(100);
      assert.ok((await b.evaluate(`window.__posts.map((m) => m.action)`)).includes('expand'));
    });
  } finally {
    try { b.ws.close(); } catch (e) {}
    br.proc.kill('SIGKILL');
  }
  console.log(`tips window tests: ${passed} passed, ${failures} failed`);
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.log('tips window tests: crashed:', e.message); process.exit(1); });
