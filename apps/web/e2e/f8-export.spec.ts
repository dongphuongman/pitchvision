import { expect, test } from '@playwright/test';
import { signIn } from './helpers';

// F8: workspace → Export (SCR-25) → toast → Exports (SCR-26) → ready → download.
test('F8 export a clip and download it', async ({ page }) => {
  await signIn(page);
  // The first finished demo match (cards 8+ of the seed are done).
  await page.goto('/library');
  await page
    .getByRole('link', { name: /Riverside FC vs Harbor City/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/videos\/[^/]+\/overview/);
  await page.getByRole('button', { name: 'Export' }).click();
  const dialog = page.getByRole('dialog', { name: 'Export' });
  await expect(dialog.getByText('Free plan exports include a watermark.')).toBeVisible();
  await dialog.getByRole('button', { name: 'Start export' }).click();
  await page.getByRole('button', { name: 'View exports' }).click();
  await expect(page).toHaveURL(/\/exports/);
  const row = page.getByRole('row', { name: /riverside_fc_vs_harbor_city_full\.mp4/ });
  await expect(row.getByText('Ready')).toBeVisible({ timeout: 20_000 });
  const download = page.waitForRequest(/storage\.test\/exp-/);
  await row.getByRole('button', { name: 'Download' }).click();
  await download;
});
