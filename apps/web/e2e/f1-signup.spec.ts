import { expect, test } from '@playwright/test';
import { PASSWORD } from './helpers';

// F1: landing → sign up → verify email (link) → onboarding 3 steps → library.
test('F1 sign up and onboard', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Analyze any broadcast match.' })).toBeVisible();
  await page.getByRole('link', { name: 'Get started' }).click();
  await expect(page).toHaveURL(/\/signup/);

  await page.getByLabel('Name').fill('Ella Coach');
  await page.getByLabel('Email').fill('ella@club.test');
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page).toHaveURL(/\/verify-email/);

  // The verification email's link (mock: token verify-<user id>). Client-side navigation: the
  // mock API's data lives in the page, a reload would forget the new account.
  await page.evaluate(
    "history.pushState({}, '', '/verify-email?token=verify-u-ella'); dispatchEvent(new PopStateEvent('popstate'))",
  );
  await expect(page.getByRole('heading', { name: 'Set up your organization' })).toBeVisible();
  await page.getByLabel('Organization name').fill('Ella FC');
  await page.getByRole('radio', { name: 'Club' }).check();
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByRole('radio', { name: /European Union/ }).check();
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByRole('button', { name: 'Confirm' }).click();
  await page.getByRole('button', { name: 'Finish setup' }).click();
  await expect(page).toHaveURL(/\/library/);
  await expect(page.getByRole('heading', { level: 1, name: 'Library' })).toBeVisible();
});
