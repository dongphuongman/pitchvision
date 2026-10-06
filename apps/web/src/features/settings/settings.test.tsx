import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { mockOrg, mockUser } from '@/mocks/identity';
import { setupIdentityMock } from '@/test/msw';
import { renderApp } from '@/test/renderApp';
import { parseEmails } from './model';

const mock = setupIdentityMock();

function seed(role: 'owner' | 'admin' | 'editor' = 'owner') {
  const me = mockUser({ email: 'dm@riversidefc.com', name: 'Dong Man' });
  const boss = mockUser({ email: 'boss@riversidefc.com', name: 'Boss' });
  const ed = mockUser({ email: 'ed@riversidefc.com', name: 'Ed' });
  mock.db.users.push(me, boss, ed);
  mock.db.orgs.push(mockOrg({ id: 'org-1', name: 'Riverside FC' }));
  mock.db.memberships.push({ user_id: me.id, org_id: 'org-1', role });
  if (role !== 'owner')
    mock.db.memberships.push({ user_id: boss.id, org_id: 'org-1', role: 'owner' });
  mock.db.memberships.push({ user_id: ed.id, org_id: 'org-1', role: 'editor' });
  mock.db.cookie = { userId: me.id, activeOrgId: 'org-1' };
  return me;
}

const logged = (prefix: string) => mock.db.log.filter((l) => l.startsWith(prefix));

describe('settings helpers', () => {
  it('parses invite emails', () => {
    expect(parseEmails(' a@x.com, B@x.com\nc@y.com;a@x.com ')).toEqual([
      'a@x.com',
      'b@x.com',
      'c@y.com',
    ]);
    expect(parseEmails('  ')).toEqual([]);
  });
});

describe('SCR-40 Settings', () => {
  it('hides sections by role (editor: no organization / members / billing / API keys)', async () => {
    seed('editor');
    renderApp('/settings/billing', { auth: 'bootstrap' });
    expect(await screen.findByText("You don't have access to this section.")).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Settings sections' });
    expect(within(nav).getByRole('link', { name: 'Profile' })).toBeInTheDocument();
    for (const name of ['Organization', 'Members', 'Billing', 'API keys']) {
      expect(within(nav).queryByRole('link', { name })).not.toBeInTheDocument();
    }
  });

  it('profile: renames, changes the password, signs out other sessions', async () => {
    const me = seed();
    const user = userEvent.setup();
    renderApp('/settings/profile', { auth: 'bootstrap' });
    const name = await screen.findByRole('textbox', { name: 'Name' });
    await waitFor(() => expect(name).toHaveValue('Dong Man'));
    await user.clear(name);
    await user.type(name, 'Dong Coach');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Profile saved')).toBeInTheDocument();
    expect(me.name).toBe('Dong Coach');

    await user.type(screen.getByLabelText('Current password'), 'nope nope nope');
    await user.type(screen.getByLabelText('New password'), 'another long password');
    await user.click(screen.getByRole('button', { name: 'Change password' }));
    expect(await screen.findByText('Your current password is wrong.')).toBeInTheDocument();
    await user.clear(screen.getByLabelText('Current password'));
    await user.type(screen.getByLabelText('Current password'), 'correct horse battery');
    await user.click(screen.getByRole('button', { name: 'Change password' }));
    expect(
      await screen.findByText('Password changed. Other sessions were signed out.'),
    ).toBeInTheDocument();
    expect(me.password).toBe('another long password');

    await user.click(screen.getByRole('button', { name: 'Sign out of all other sessions' }));
    expect(await screen.findByText('Other sessions signed out')).toBeInTheDocument();
  });

  it('organization: renames the org; the region is read-only', async () => {
    seed();
    const user = userEvent.setup();
    renderApp('/settings/organization', { auth: 'bootstrap' });
    const name = await screen.findByRole('textbox', { name: 'Organization name' });
    expect(screen.getByRole('textbox', { name: 'Data region' })).toHaveAttribute('readonly');
    await user.clear(name);
    await user.type(name, 'Riverside Academy');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Organization saved')).toBeInTheDocument();
    expect(mock.db.orgs[0]?.name).toBe('Riverside Academy');
  });

  it('members: invites, changes a role, removes a member, revokes an invite', async () => {
    seed();
    const user = userEvent.setup();
    renderApp('/settings/members', { auth: 'bootstrap' });
    expect(await screen.findByText('ed@riversidefc.com')).toBeInTheDocument();
    expect(screen.getByText('No pending invites.')).toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: 'Email addresses' }), 'a@x.com, b@x.com');
    await user.click(screen.getByRole('button', { name: 'Send invites' }));
    expect(await screen.findByText('2 invites sent')).toBeInTheDocument();
    expect(await screen.findByText('a@x.com')).toBeInTheDocument();
    expect(logged('POST /api/v1/orgs/org-1/invites')[0]).toContain('"role":"editor"');

    await user.click(screen.getByRole('combobox', { name: 'Role of Ed' }));
    await user.click(await screen.findByRole('option', { name: 'Admin' }));
    await waitFor(() => expect(logged('PATCH /api/v1/orgs/org-1/members/u-ed')).toHaveLength(1));
    expect(await screen.findByText('Role updated')).toBeInTheDocument();

    const edRow = screen.getByText('ed@riversidefc.com').closest('li') as HTMLElement;
    await user.click(within(edRow).getByRole('button', { name: 'Remove' }));
    const dialog = await screen.findByRole('dialog', { name: 'Remove Ed?' });
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(screen.queryByText('ed@riversidefc.com')).not.toBeInTheDocument());

    const inv = screen.getByText('a@x.com').closest('li') as HTMLElement;
    await user.click(within(inv).getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(screen.queryByText('a@x.com')).not.toBeInTheDocument());
  });

  it('billing: plan, minutes and Change plan → SCR-41; owner joins the Pro waitlist', async () => {
    seed();
    mock.account.usage.minutes_used = 118;
    const user = userEvent.setup();
    renderApp('/settings/billing', { auth: 'bootstrap' });
    expect(await screen.findByText('118 of 120 min')).toBeInTheDocument();
    expect(screen.getByText(/Exports up to 720p, with a watermark/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Change plan' }));
    const dialog = await screen.findByRole('dialog', { name: 'Upgrade your plan' });
    const pro = within(dialog).getByRole('listitem', { name: 'Pro' });
    await user.click(within(dialog).getByRole('radio', { name: 'Annual · save 20%' }));
    await user.click(within(pro).getByRole('button', { name: 'Choose Pro' }));
    expect(await screen.findByRole('dialog', { name: "You're on the list" })).toBeInTheDocument();
    expect(mock.db.upgradeRequests).toEqual([
      { org_id: 'org-1', user_id: 'u-dm', plan: 'pro', reason: null },
    ]);
    expect(logged('POST /api/v1/orgs/org-1/upgrade-requests')[0]).toContain('"period":"annual"');
  });

  it('preferences: email notification toggles save at once', async () => {
    const me = seed('editor');
    const user = userEvent.setup();
    renderApp('/settings/preferences', { auth: 'bootstrap' });
    const sw = await screen.findByRole('switch', { name: 'An export is ready' });
    await waitFor(() => expect(sw).toBeChecked());
    await user.click(sw);
    expect(await screen.findByText('Notification settings saved')).toBeInTheDocument();
    expect(me.email_prefs).toEqual({ export_ready: false });
    expect(screen.getByRole('switch', { name: 'An export is ready' })).not.toBeChecked();
  });
});

describe('SCR-41 for non-owners', () => {
  it('the minutes meter opens the plans with the quota reason and Notify owner', async () => {
    seed('editor');
    mock.account.usage.minutes_used = 118;
    const user = userEvent.setup();
    renderApp('/library', { auth: 'bootstrap' });
    await user.click(
      await screen.findByRole('button', { name: 'Analysis minutes this month: 118 of 120' }),
    );
    const dialog = await screen.findByRole('dialog', { name: 'Upgrade your plan' });
    expect(
      await within(dialog).findByText("You've used 118 of 120 minutes this month."),
    ).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Choose Pro' })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Notify owner' }));
    expect(await screen.findByText("We emailed your organization's owner.")).toBeInTheDocument();
    expect(mock.db.upgradeRequests[0]?.reason).toBe('quota');
    await user.click(within(dialog).getByRole('button', { name: 'Maybe later' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
