// Captures REAL screens from the running Civic Innovators console for the
// admin walkthrough video. Re-runnable:  node scripts/capture-admin.mjs
// Needs the console on http://127.0.0.1:8000 (fake-Google demo mode).
// Writes full-page PNGs to public/admin/ and the page-coordinate boxes of the
// controls each scene highlights to public/admin/boxes.json.
// It never deletes anything: it adds one demo session, records decisions,
// links one shoutout and acknowledges help requests.
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const BASE = process.env.CUFA_CONSOLE_URL || 'http://127.0.0.1:8000';
const here = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(here, '..', 'public', 'admin');
fs.mkdirSync(OUT, { recursive: true });
const boxesFile = path.join(OUT, 'boxes.json');
const boxes = fs.existsSync(boxesFile) ? JSON.parse(fs.readFileSync(boxesFile, 'utf8')) : {};

const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell',
});
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
page.on('dialog', (d) => d.accept());

const settle = async () => {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(500);
};
const go = async (p) => {
  await page.goto(BASE + p);
  await settle();
};

/** Full-page screenshot plus page-coordinate boxes of named locators. */
async function shot(name, targets = {}) {
  // The console scrolls inside an app frame, so fullPage stops at the
  // viewport. Grow the viewport to the tallest scroll height instead.
  await page.evaluate(() => {
    for (const e of document.querySelectorAll('*')) if (e.scrollTop) e.scrollTop = 0;
    window.scrollTo(0, 0);
  });
  const tall = await page.evaluate(() =>
    Math.max(...[...document.querySelectorAll('*')].map((e) => e.scrollHeight)),
  );
  const h = Math.min(Math.max(1080, tall + 200), 5000);
  await page.setViewportSize({ width: 1920, height: h });
  await page.waitForTimeout(400);
  const entry = { h, boxes: {} };
  for (const [key, loc] of Object.entries(targets)) {
    try {
      const l = typeof loc === 'string' ? page.locator(loc).first() : loc.first();
      const b = await l.boundingBox({ timeout: 1500 });
      if (b) entry.boxes[key] = [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)];
      else console.warn(`  ${name}.${key}: not visible`);
    } catch {
      console.warn(`  ${name}.${key}: not found`);
    }
  }
  await page.screenshot({ path: path.join(OUT, `${name}.png`) });
  await page.setViewportSize({ width: 1920, height: 1080 });
  boxes[name] = entry;
  fs.writeFileSync(boxesFile, JSON.stringify(boxes, null, 1));
  console.log(`captured ${name} (h=${entry.h})`, Object.keys(entry.boxes).join(','));
}

const btn = (label) => page.getByRole('button', { name: label, exact: false });
const heading = (t) => page.getByRole('heading', { name: t, exact: false });
// ONLY=signin,review ... runs a subset (the stateful steps are not repeatable).
const ONLY = process.env.ONLY ? process.env.ONLY.split(',') : null;
const step = async (label, fn) => {
  if (ONLY && !ONLY.includes(label) && label !== 'signin') return;
  try {
    await fn();
  } catch (e) {
    console.warn(`STEP FAILED ${label}: ${e.message.split('\n')[0]}`);
  }
};

// ---------------------------------------------------------------- sign in
await step('signin', async () => {
  await go('/signin');
  await shot('01-signin', { google: btn('Sign in with Google'), dev: btn('Sign in without Google'), email: 'input[name=email]' });
  await page.fill('input[name=email]', 'adiah@civicsunplugged.org');
  await shot('02-signin-filled', { dev: btn('Sign in without Google'), email: 'input[name=email]' });
  await btn('Sign in without Google').click();
  await settle();
});

// ---------------------------------------------------------------- connect
await step('connect', async () => {
  await go('/');
  const connectBtn = page.locator('form[action="/google/connect"] button');
  await shot('03-connect-before', { connect: connectBtn, status: heading('Connect') });
  // The demo server runs without CUFA_ENCRYPTION_KEY, so a simulated connect
  // is refused with a real, explanatory error. Capture that too.
  await connectBtn.click();
  await settle();
  await shot('04-connect-after', { error: '[role=alert]', connect: page.locator('form[action="/google/connect"] button') });
});

// ---------------------------------------------------------------- templates
await step('template', async () => {
  await go('/template');
  await shot('06-template', {
    partA: heading('Part A'),
    partB: heading('Part B'),
    manual: page.getByText('Collect email addresses', { exact: false }),
    verifyA: btn('Verify the Part A template'),
    verifyB: btn('Verify the Part B template'),
    replace: page.locator('form[action="/template/replace"] button'),
  });
  await btn('Verify the Part A template').click();
  await settle();
  await shot('07-template-verified', {
    verifyA: btn('Verify the Part A template'),
    verifyB: btn('Verify the Part B template'),
    replace: page.locator('form[action="/template/replace"] button'),
  });
});

// ---------------------------------------------------------------- rotation
await step('rotation', async () => {
  await go('/rotation');
  await shot('08-rotation', {
    week11: page.getByRole('row').filter({ hasText: /teacher/i }).last(),
    table: 'table',
  });
});

// ---------------------------------------------------------------- sessions
await step('sessions', async () => {
  await go('/sessions');
  await shot('09-sessions', { newBtn: page.getByRole('link', { name: /new session/i }), table: 'table', cohort: page.getByText('Cohort', { exact: true }) });
});

// ---------------------------------------------------------------- new session
const TITLE = 'Session 11 — Demo day rehearsal';
let newId = null;
await step('new-session', async () => {
  await go('/sessions/new');
  await shot('10-new-empty', { title: 'input[name=title]', save: btn('Save session') });
  await page.fill('input[name=title]', TITLE);
  const date = page.getByPlaceholder('Select a date');
  await date.click();
  await date.fill('12/07/2026');
  await date.press('Tab');
  const time = page.getByLabel('Scheduled at time');
  await time.click();
  await time.fill('7:00 PM');
  await time.press('Tab');
  await page.keyboard.press('Escape');
  await page.fill('input[name=timezone]', 'America/New_York');
  await page.fill('input[name=week_index]', '11');
  await page.locator('input[name=week_index]').press('Tab');
  await settle();
  const hidden = await page.locator('input[name=scheduled_at]').inputValue();
  console.log('  scheduled_at =', hidden);
  if (!hidden) {
    await page.evaluate(() => {
      document.querySelector('input[name=scheduled_at]').value = '2026-12-07T19:00';
    });
  }
  await shot('11-new-filled', {
    title: 'input[name=title]',
    when: page.getByPlaceholder('Select a date'),
    week: 'input[name=week_index]',
    question: 'textarea, input[name=teacher_question]',
    suggest: btn('Suggest a passphrase'),
  });
  await btn('Suggest a passphrase').click();
  await page.waitForTimeout(1200);
  await shot('12-new-passphrase', {
    passphrase: 'input[name=passphrase]',
    suggest: btn('Suggest a passphrase'),
    save: btn('Save session'),
  });
  // Save WITHOUT the teacher question first so the block is real.
  await btn('Save session').click();
  await settle();
  await shot('13-new-saved', { save: btn('Save session'), error: '[role=alert]' });
  if (/\/sessions\/[0-9a-f-]{36}/.test(page.url())) {
    newId = page.url().match(/[0-9a-f-]{36}/)[0];
  }
});
console.log('new session id', newId);

if (newId) {
  // ------------------------------------------------------------ session detail
  await step('detail', async () => {
    await go(`/sessions/${newId}`);
    await shot('14-detail-before', {
      provisionA: btn('Provision Part A'),
      provisionB: page.locator(`form[action="/sessions/${newId}/provision"]`).filter({ has: page.locator('input[value=b]') }).locator('button').first(),
      passphrase: heading("Today's passphrase"),
      announce: btn('Announce now'),
    });
    await btn('Provision Part A').click();
    await settle();
    await shot('15-provisioned-a', {
      notice: '[role=status], [role=alert]',
      recheck: btn('Re-check provisioning'),
      partA: heading('Part A'),
    });
    const provB = page.locator(`form[action="/sessions/${newId}/provision"]`).filter({ has: page.locator('input[value=b]') }).locator('button').first();
    await provB.click();
    await settle();
    await shot('16-provision-b-blocked', { notice: '[role=alert], [role=status]', partB: heading('Part B') });
  });

  // ------------------------------------------------------------ edit, add question
  await step('edit', async () => {
    await go(`/sessions/${newId}/edit`);
    const q = page.locator('textarea[name=teacher_question], input[name=teacher_question]');
    await q.fill('What would you change about your pitch after today’s rehearsal?');
    await shot('17-edit-question', { question: q, save: btn('Save session') });
    await btn('Save session').click();
    await settle();
    const provB = page.locator(`form[action="/sessions/${newId}/provision"]`).filter({ has: page.locator('input[value=b]') }).locator('button').first();
    await provB.click();
    await settle();
    await shot('18-provisioned-b', { notice: '[role=status], [role=alert]', partB: heading('Part B') });
    await go(`/sessions/${newId}`);
    await shot('19-mid-lesson', {
      passphrase: heading("Today's passphrase"),
      announce: btn('Announce now'),
      count: heading('Responses'),
    });
    await btn('Announce now').click();
    await settle();
    await shot('20-announced', {
      announce: btn('Announce again'),
      notice: '[role=status], [role=alert]',
      count: heading('Responses'),
    });
  });
}

// ---------------------------------------------------------------- pull on a live session
const S1 = 'a8e404b4-5a0c-47af-9454-27a026255c2f';
await step('pull', async () => {
  await go(`/sessions/${S1}`);
  await shot('21-pull-before', { pull: btn('Pull responses'), count: heading('Responses'), pullB: btn('Pull Part B responses') });
  await btn('Pull responses').first().click();
  await settle();
  await page.waitForTimeout(1500);
  await shot('22-pull-after', { pull: btn('Pull responses'), count: heading('Responses'), notice: '[role=status], [role=alert]' });
});

// ---------------------------------------------------------------- part B responses
await step('responses', async () => {
  const S2 = '6999b6ac-429c-4f14-b56f-f445d403dd14'; // week 2: muddiest point
  await go(`/sessions/${S2}/responses`);
  await shot('23-responses', {
    confidence: page.getByText(/confidence/i),
    themes: heading(/theme/i),
    regen: btn('Regenerate themes'),
  });
  await go(`/sessions/${S1}/responses`);
  await shot('24-responses-s1', { confidence: page.getByText(/confidence/i) });
});

// ---------------------------------------------------------------- review queue
await step('review', async () => {
  await go('/review?tab=needs_review');
  const firstRow = page.locator('form[action^="/review/"]').filter({ has: page.locator('input[name=note]') }).first();
  await shot('25-review-needs', {
    tabs: page.getByRole('link', { name: 'Needs review' }),
    row: firstRow,
    note: firstRow.locator('input[name=note]'),
    attended: firstRow.getByRole('button', { name: 'Attended', exact: true }),
    cohort: page.getByText('Cohort', { exact: true }),
  });
  await firstRow.locator('input[name=note]').fill('Joined late, confirmed with the facilitator');
  await shot('26-review-note', {
    row: firstRow,
    attended: firstRow.getByRole('button', { name: 'Attended', exact: true }),
  });
  await firstRow.getByRole('button', { name: 'Attended', exact: true }).click();
  await settle();
  await shot('27-review-decided', { notice: '[role=status], [role=alert]' });
  await go('/review?tab=ai');
  const aiRow = page.locator('form[action^="/review/"]').first();
  await shot('28-review-ai', { row: aiRow, tabs: page.getByRole('link', { name: 'AI decisions' }) });
  await go('/review?tab=straightlining');
  await shot('29-review-straight', { tabs: page.getByRole('link', { name: 'Straight-lining' }), table: 'table' });
  await go('/review?tab=identities');
  await shot('30-review-identities', { tabs: page.getByRole('link', { name: 'Unresolved addresses' }), table: 'table' });
});

// ---------------------------------------------------------------- shoutouts
await step('shoutouts', async () => {
  await go('/shoutouts');
  const form = page.locator('form[action^="/shoutouts/"]').first();
  await shot('31-shoutouts', { form, candidate: form.locator('button').first() });
  await form.locator('button').first().click();
  await settle();
  await shot('32-shoutout-linked', { notice: '[role=status], [role=alert]' });
});

// ---------------------------------------------------------------- help requests
await step('help', async () => {
  await go('/help-requests');
  const form = page.locator('form[action^="/help-requests/"]').first();
  await shot('33-help', { form, ack: btn("I'm picking this up"), close: form.getByRole('button', { name: 'Close' }) });
  await form.locator('textarea, input[type=text]').first().fill('Emailed to set up a call this week.');
  await shot('34-help-note', { form, ack: btn("I'm picking this up") });
  await btn("I'm picking this up").first().click();
  await settle();
  await shot('35-help-acked', { notice: '[role=status], [role=alert]' });
  await go('/help-requests?status=acknowledged');
  const f2 = page.locator('form[action^="/help-requests/"]').first();
  await shot('36-help-acknowledged', { form: f2, close: f2.getByRole('button', { name: 'Close' }) });
});

// ---------------------------------------------------------------- new-session form, filled but NOT saved
// Re-runnable: nothing is written. Overwrites 10/11/12 with full-height shots.
await step('newform', async () => {
  await go('/sessions/new');
  await shot('10-new-empty', { title: 'input[name=title]', save: btn('Save session') });
  await page.fill('input[name=title]', TITLE);
  const date = page.getByPlaceholder('Select a date');
  await date.click();
  await date.fill('12/07/2026');
  await date.press('Tab');
  const time = page.getByLabel('Scheduled at time');
  await time.click();
  await time.fill('7:00 PM');
  await time.press('Tab');
  await page.keyboard.press('Escape');
  await page.fill('input[name=timezone]', 'America/New_York');
  await page.fill('input[name=week_index]', '11');
  await page.locator('input[name=week_index]').press('Tab');
  await settle();
  const targets = () => ({
    title: 'input[name=title]',
    when: page.getByPlaceholder('Select a date'),
    tz: 'input[name=timezone]',
    week: 'input[name=week_index]',
    question: 'textarea[name=teacher_question], input[name=teacher_question]',
    passphrase: 'input[name=passphrase]',
    suggest: btn('Suggest a passphrase'),
    save: btn('Save session'),
  });
  await shot('11-new-filled', targets());
  await btn('Suggest a passphrase').click();
  await page.waitForTimeout(1200);
  await shot('12-new-passphrase', targets());
});

// ---------------------------------------------------------------- read-only full-height views
await step('views', async () => {
  await go('/sessions');
  const link = page.getByRole('link', { name: TITLE });
  const href = await link.first().getAttribute('href');
  const id = href.match(/[0-9a-f-]{36}/)[0];
  await go(`/sessions/${id}`);
  await shot('40-detail-full', {
    passphrase: heading("Today's passphrase"),
    partA: heading('Part A'),
    verified: page.getByText('published and verified').first(),
    qr: 'img, svg[role=img]',
    announce: btn('Announce again'),
    count: heading('Responses'),
    partB: heading('Part B'),
    pull: btn('Pull responses'),
  });
});

await browser.close();
console.log('done');
