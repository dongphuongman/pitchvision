import { expect, type Page } from '@playwright/test';

/** Demo accounts of src/mocks/seed.ts (password for all). */
export const PASSWORD = 'correct horse battery';

export async function signIn(page: Page, email = 'owner@pitchvision.test'): Promise<void> {
  await page.goto('/signin');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/library/);
}
