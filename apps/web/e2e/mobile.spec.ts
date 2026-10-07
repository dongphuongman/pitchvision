import { expect, test, type Page } from '@playwright/test';
import { signIn } from './helpers';

// UIUX §09 < 768 px: the supported screens fit a phone (no horizontal scroll), ☰ menu, view-only workspace.
test.use({ viewport: { width: 390, height: 844 } });

async function fits(page: Page) {
  await page.waitForLoadState('networkidle');
  const width = Number(await page.evaluate('document.documentElement.scrollWidth'));
  expect(width, `horizontal overflow on ${page.url()}`).toBeLessThanOrEqual(390);
}

test('mobile: supported screens fit 390 px', async ({ page }) => {
  await page.goto('/');
  await fits(page);
  await signIn(page);
  await fits(page);
  const done = page.getByRole('link', { name: /Riverside FC vs Harbor City/ }).nth(1);
  const href = (await done.getAttribute('href')) ?? '';

  // menus are bottom sheets: pinned to the bottom edge, full width, over a scrim that closes them
  await expect(page.locator('[data-pv-sheet]')).toHaveCount(0);
  await page
    .getByRole('button', { name: /^Actions for Riverside FC vs Harbor City/ })
    .first()
    .click();
  const sheet = page.getByRole('menu');
  await expect(sheet).toBeVisible();
  const box = await sheet.boundingBox();
  if (!box) throw new Error('menu has no box');
  expect(Math.round(box.x)).toBe(0);
  expect(Math.round(box.width)).toBe(390);
  expect(Math.round(box.y + box.height)).toBe(844);
  await page.mouse.click(195, 100); // the scrim
  await expect(sheet).toBeHidden();

  await page.getByRole('button', { name: 'Menu', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Exports' }).click();
  await expect(page).toHaveURL(/\/exports/);
  await fits(page);

  for (const section of ['profile', 'members', 'billing', 'preferences', 'privacy']) {
    await page.goto(`/settings/${section}`);
    await fits(page);
  }

  await page.goto(href);
  await expect(page.getByRole('radiogroup', { name: 'Show' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'More actions' })).toBeHidden(); // view-only: no corrections menu
  await fits(page);

  await page.goto(href.replace(/\/[^/]+$/, '/teams'));
  await expect(page.getByText('Best on a larger screen.', { exact: false })).toBeVisible();
  await fits(page);
});
