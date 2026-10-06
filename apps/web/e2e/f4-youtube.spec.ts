import { expect, test } from '@playwright/test';
import { signIn } from './helpers';

// F4: YouTube link → preview → analyze → processing.
test('F4 analyze a YouTube match', async ({ page }) => {
  await signIn(page);
  await page.getByRole('link', { name: 'New analysis' }).click();
  const dialog = page.getByRole('dialog', { name: 'New analysis' });
  await dialog.getByRole('tab', { name: 'YouTube' }).click();
  await dialog.getByLabel('YouTube URL').fill('https://www.youtube.com/watch?v=dQw4w9WgXcQ');
  await dialog.getByRole('button', { name: 'Fetch' }).click();
  await expect(dialog.getByTestId('yt-preview')).toBeVisible();
  await dialog.getByRole('checkbox', { name: 'I have the rights to analyze this video' }).check();
  await dialog.getByRole('button', { name: 'Analyze' }).click();
  await expect(page).toHaveURL(/\/videos\/[^/]+\/processing/);
  await expect(page).toHaveURL(/\/videos\/[^/]+\/overview/, { timeout: 45_000 });
});
