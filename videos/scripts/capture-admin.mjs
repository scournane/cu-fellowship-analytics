// Captures REAL screens from the running Civic Innovators stack (origin/main)
// for the admin walkthrough video:  node scripts/capture-admin.mjs
// Needs the console on http://127.0.0.1:8000 (fake-Google demo mode), the fake
// Slack workspace on :3001 and the bot's status page on :3000.
// Writes full-page PNGs to public/admin/ and the page-coordinate boxes of the
// controls each scene highlights to public/admin/boxes.json.
// It never deletes anything. It connects the fake Google account, adds one demo
// session and one assignment, records a review decision, links a shoutout,
// acknowledges a help request and drives the #q-and-a buttons in the fake Slack.
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

const link = (name) => page.getByRole('link', { name, exact: false });
const text = (t) => page.getByText(t, { exact: false });
const TITLE = 'Session 11 — Demo day rehearsal';

// ---------------------------------------------------------------- sign in
await step('signin', async () => {
  await go('/signin');
  await shot('01-signin', { email: 'input[name=email]', dev: 'form[action="/signin/dev"] button', password: 'input[name=password]' });
  await page.fill('input[name=email]', 'adiah@civicsunplugged.org');
  await btn('without Google').first().click().catch(() => page.locator('form[action="/signin/dev"] button').click());
  await settle();
});

// ---------------------------------------------------------------- connect
await step('connect', async () => {
  await go('/');
  const connectBtn = page.locator('form[action="/google/connect"] button');
  await shot('03-connect-before', { connect: connectBtn });
  await connectBtn.click();
  await settle();
  await shot('04-connect-after', { disconnect: page.locator('form[action="/google/disconnect"] button') });
});

// ---------------------------------------------------------------- templates
await step('template', async () => {
  await go('/template');
  await shot('06-template', {
    manual: text('Collect email addresses'),
    verifyA: btn('Verify the Part A template'),
    verifyB: btn('Verify the Part B template'),
    replaceA: page.locator('form[action="/template/replace"] button').first(),
  });
  await btn('Verify the Part A template').click();
  await settle();
  await shot('07-template-verified', { verifyA: btn('Verify the Part A template') });
});

// ---------------------------------------------------------------- rotation
await step('rotation', async () => {
  await go('/rotation');
  await shot('08-rotation', { week11: page.getByRole('row').filter({ hasText: /teacher/i }).last(), table: 'table' });
});

// ---------------------------------------------------------------- sessions
await step('sessions', async () => {
  await go('/sessions');
  await shot('09-sessions', {
    newBtn: link('New session'),
    csv: link('template'),
    upload: 'form[action="/sessions/upload"]',
    table: 'table',
  });
});

let newId = null;
await step('new-session', async () => {
  await go('/sessions/new');
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
  await btn('Save session').click();
  await settle();
  const m = page.url().match(/[0-9a-f-]{36}/);
  if (m) newId = m[0];
});
console.log('new session', newId);

if (newId) {
  await step('detail', async () => {
    await go(`/sessions/${newId}`);
    await shot('14-detail-before', {
      passphrase: heading("Today's passphrase"),
      provisionA: btn('Provision Part A'),
      blocked: text('cannot be provisioned yet'),
    });
    await btn('Provision Part A').click();
    await settle();
    await go(`/sessions/${newId}/edit`);
    const q = page.locator('textarea[name=teacher_question], input[name=teacher_question]');
    await q.fill('What would you change about your pitch after today’s rehearsal?');
    await shot('17-edit-question', { question: q, save: btn('Save session') });
    await btn('Save session').click();
    await settle();
    await go(`/sessions/${newId}`);
    const provB = page.locator(`form[action="/sessions/${newId}/provision"]`).filter({ has: page.locator('input[value=b]') }).locator('button').first();
    await provB.click();
    await settle();
    await go(`/sessions/${newId}`);
    await shot('19-mid-lesson', {
      passphrase: heading("Today's passphrase"),
      verified: text('published and verified'),
      qr: 'img[alt*="QR"], svg[role=img], canvas',
      partB: heading('Part B'),
      announce: btn('Announce now'),
      count: heading('Responses'),
    });
    await btn('Announce now').click();
    await settle();
    await shot('20-announced', { announce: btn('Announce again'), count: heading('Responses') });
  });
}

const S1 = '5fd196f7-5c93-4926-9b0b-8280cc4f594e';
const S2 = '46b927aa-8ba2-4a16-af4f-50041a6f25d5';
await step('pull', async () => {
  await go(`/sessions/${S1}`);
  await btn('Pull responses').first().click();
  await settle();
  await page.waitForTimeout(1200);
  await shot('22-pull-after', { pull: btn('Pull responses'), count: heading('Responses'), pullB: btn('Pull Part B responses'), transcript: `form[action="/sessions/${S1}/transcript"]` });
});

await step('responses', async () => {
  await go(`/sessions/${S2}/responses`);
  await shot('23-responses', { confidence: heading('Confidence'), themes: heading(/unclear/i), regen: btn('Regenerate themes'), takeaways: heading('Takeaways') });
});

await step('review', async () => {
  await go('/review?tab=needs_review');
  const firstRow = page.locator('form[action^="/review/"]').filter({ has: page.locator('input[name=note]') }).first();
  await firstRow.locator('input[name=note]').fill('Joined late, confirmed with the facilitator');
  await shot('26-review-note', {
    tabs: page.getByRole('link', { name: 'Needs review' }),
    row: firstRow,
    attended: firstRow.getByRole('button', { name: 'Attended', exact: true }),
    cohort: text('All cohorts'),
  });
  await firstRow.getByRole('button', { name: 'Attended', exact: true }).click();
  await settle();
  await shot('27-review-decided', {});
  await go('/review?tab=ai');
  await shot('28-review-ai', { tab: page.getByRole('link', { name: 'AI decisions' }) });
  await go('/review?tab=straightlining');
  await shot('29-review-straight', { tab: page.getByRole('link', { name: 'Straight-lining' }), table: 'table' });
  await go('/review?tab=identities');
  await shot('30-review-identities', { tab: page.getByRole('link', { name: 'Unresolved addresses' }), table: 'table' });
});

await step('shoutouts', async () => {
  await go('/shoutouts');
  const form = page.locator('form[action^="/shoutouts/"]').first();
  await shot('31-shoutouts', { form, candidate: form.locator('button').first(), cohort: text('All cohorts') });
  await form.locator('button').first().click();
  await settle();
  await shot('32-shoutout-linked', {});
});

await step('help', async () => {
  await go('/help-requests');
  const form = page.locator('form[action^="/help-requests/"]').first();
  await form.locator('textarea, input[type=text]').first().fill('Emailed to set up a call this week.');
  await shot('34-help-note', { form, ack: btn("I'm picking this up"), close: form.getByRole('button', { name: 'Close' }) });
  await btn("I'm picking this up").first().click();
  await settle();
  await go('/help-requests?status=acknowledged');
  const f2 = page.locator('form[action^="/help-requests/"]').first();
  await shot('36-help-acknowledged', { form: f2, close: f2.getByRole('button', { name: 'Close' }) });
});

// ---------------------------------------------------------------- new on main
await step('dashboard', async () => {
  await go('/dashboard');
  await shot('50-dashboard', {
    export: link('Export CSV'),
    attendance: text('Overall attendance'),
    reached: btn('Reached out'),
    newAsg: link('New assignment'),
  });
  const fellowLink = page.locator('a[href^="/dashboard/fellow/"]').first();
  const href = await fellowLink.getAttribute('href');
  await go(href);
  await shot('51-fellow', { mark: btn('reached out') });
});

await step('assignments', async () => {
  await go('/assignments');
  await shot('52-assignments', { newBtn: link('New assignment'), table: 'table' });
  await go('/assignments/new');
  await page.fill('input[name=title]', 'Case brief: the district budget');
  await page.fill('input[name=link]', 'https://docs.example.invalid/case-brief').catch(() => {});
  await page.fill('textarea[name=description]', 'Two pages on one line item and who decided it.').catch(() => {});
  await shot('53-assignment-new', { title: 'input[name=title]', save: btn('Save assignment') });
});

await step('roster', async () => {
  await go('/roster');
  await shot('54-roster', { load: heading('Load a roster'), table: 'table', tz: page.locator('form[action$="/timezone"]').first() });
});

// ---------------------------------------------------------------- Slack
await step('slack', async () => {
  await page.goto('http://127.0.0.1:3000/');
  await page.waitForTimeout(1500);
  await shot('60-bot-status', { sub: '#sub', tiles: '#tiles' });
  await page.goto('http://127.0.0.1:3001/');
  await page.waitForTimeout(2500);
  await page.selectOption('#user', { label: 'Corvin Cinderwick' }).catch(() => {});
  await page.fill('#qatext', 'Is the Solvathon pitch graded on the slides or the talk?');
  await page.getByRole('button', { name: 'Ask a question' }).click();
  await page.waitForTimeout(800);
  await page.selectOption('#user', { label: 'CU Staff (demo)' }).catch(() => {});
  await page.fill('#qatext', 'On the talk — slides are optional. See the rubric pinned in #announcements.');
  await page.getByRole('button', { name: 'Answer it (in thread)' }).click();
  await page.waitForTimeout(800);
  await page.getByRole('button', { name: 'Mark the answer' }).click();
  await page.waitForTimeout(800);
  await page.selectOption('#user', { label: 'Delphine Dunmore' }).catch(() => {});
  await page.getByRole('button', { name: 'Ask the first question again' }).click();
  await page.waitForTimeout(1500);
  await page.selectOption('#user', { label: 'CU Staff (demo)' }).catch(() => {});
  await page.getByRole('button', { name: '@bot summary' }).click();
  await page.waitForTimeout(2500);
  await page.selectOption('#user', { label: 'Ardith Aldergrove' }).catch(() => {});
  await page.getByRole('button', { name: 'Vote in latest poll' }).click();
  await page.waitForTimeout(2500);
  await shot('61-fake-slack', {
    qa: text('Q&A in #q-and-a'),
    posted: '#posted',
    dms: '#dms',
    log: '#log',
    guarantees: text('Prove the guarantees'),
  });
});

await browser.close();
console.log('done');
