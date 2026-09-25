// Captures REAL, read-only screens of the console for the database walkthrough:
//   node scripts/capture-database.mjs
// Needs the console running against the demo database in fake-Google mode, e.g.
//   CUFA_DATABASE_URL=postgresql://postgres:postgres@localhost:64322/cufa_demo_pa \
//   CUFA_FAKE_GOOGLE=1 CUFA_FAKE_GOOGLE_STATE=<a copy of fixtures/fake_google_state.json> \
//   .venv/bin/cufa serve --port 8200
// Only GET pages are visited after the developer sign-in; nothing is written.
// Writes PNGs and page-coordinate boxes to public/database/.
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const BASE = process.env.CUFA_CONSOLE_URL || 'http://127.0.0.1:8200';
const here = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(here, '..', 'public', 'database');
fs.mkdirSync(OUT, { recursive: true });
const boxes = {};

const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell',
});
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

const go = async (p) => {
  await page.goto(BASE + p);
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(500);
};

async function shot(name, targets = {}) {
  const entry = { h: 1080, boxes: {} };
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
  boxes[name] = entry;
  console.log(`captured ${name}`, JSON.stringify(entry.boxes));
}

const text = (t) => page.getByText(t, { exact: false });
const link = (name) => page.getByRole('link', { name, exact: false });

await go('/signin');
await page.fill('input[name=email]', 'staff.demo@example.invalid');
await page.locator('form[action="/signin/dev"] button').click();
await page.waitForLoadState('networkidle').catch(() => {});

await go('/dashboard');
await shot('dashboard', {
  export: link('Export CSV'),
  attendance: text('Overall attendance'),
  table: 'table',
});

await go('/review?tab=needs_review');
await shot('review', {
  tabs: page.getByRole('link', { name: 'Needs review' }),
  row: page.locator('form[action^="/review/"]').first(),
  table: 'table',
});

fs.writeFileSync(path.join(OUT, 'boxes.json'), JSON.stringify(boxes, null, 1));
await browser.close();
