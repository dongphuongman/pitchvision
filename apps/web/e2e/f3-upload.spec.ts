import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import { signIn } from './helpers';

const FIXTURE = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures', 'match.mp4');

// F3: upload → processing (live progress) → workspace.
test('F3 upload, analyze, open the result', async ({ page }) => {
  await signIn(page);
  await page.getByRole('link', { name: 'New analysis' }).click();
  const dialog = page.getByRole('dialog', { name: 'New analysis' });
  await dialog.getByRole('tab', { name: 'Upload' }).click();
  await dialog.locator('input[type="file"]').setInputFiles(FIXTURE);
  await dialog
    .getByRole('checkbox', { name: 'I own or have permission to use this video' })
    .check();
  await dialog.getByRole('button', { name: 'Start upload' }).click();
  await expect(page).toHaveURL(/\/videos\/[^/]+\/processing/, { timeout: 30_000 });
  // The simulated job runs to done and the processing page opens the workspace.
  await expect(page).toHaveURL(/\/videos\/[^/]+\/overview/, { timeout: 45_000 });
  await expect(page.getByRole('button', { name: 'Export' })).toBeVisible();
});
