import { Link, useNavigate, useRouterState } from '@tanstack/react-router';
import {
  ArrowLeftRight,
  Building2,
  Check,
  CircleHelp,
  CreditCard,
  Download,
  Keyboard,
  Film,
  LogOut,
  Menu,
  Moon,
  Plus,
  SlidersHorizontal,
  Sun,
  User,
} from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { Membership } from '@/api/identity';
import { can } from '@/auth/roles';
import { selectActiveMembership, useAuthStore } from '@/auth/session';
import { useSignOut, useSignOutGuard, useSwitchOrganization } from '@/auth/useAuthActions';
import { cn } from '@/lib/cn';
import { useResolvedTheme, useThemeStore } from '@/lib/theme';
import {
  Button,
  Dialog,
  DialogClose,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
  Kbd,
  toast,
  Tooltip,
} from '@/ui';
import { Logo } from './Logo';
import { NotificationMenu } from './NotificationMenu';
import { UsageMeter } from './UsageMeter';

const navLink =
  'focus-ring flex items-center gap-[7px] rounded-md px-3 py-[7px] font-semibold text-fg-2 hover:bg-surface-3 hover:text-fg';
const navActive = 'bg-surface-3 text-fg';

const HELP_URL = 'https://docs.pitchvision.ai';

function initials(name: string | undefined): string {
  if (!name) return '';
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? '')
    .join('');
}

/**
 * Global top bar (UIUX G). Role-based visibility (hidden, not disabled — UIUX §3.1):
 * G.E05 New analysis editor+, G.E10 Organization admin+, G.E11 Billing owner, G.E13 Switch
 * organization only with ≥ 2 memberships. G.E03 Live is hidden until Phase C. G.E06 usage meter and
 * G.E07 notifications (SCR-51) are their own components.
 */
export function TopBar({ onOpenShortcuts }: { onOpenShortcuts: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const libraryActive = pathname.startsWith('/library') || pathname.startsWith('/videos');
  const theme = useResolvedTheme();
  const toggleTheme = useThemeStore((s) => s.toggle);

  const user = useAuthStore((s) => s.user);
  const memberships = useAuthStore((s) => s.memberships);
  const active = useAuthStore(selectActiveMembership);
  const role = active?.role ?? null;

  const signOut = useSignOut();
  const switchOrg = useSwitchOrganization();
  const uploadsInProgress = useSignOutGuard((s) => s.uploadsInProgress);
  const [confirmSignOut, setConfirmSignOut] = useState(false);

  function requestSignOut() {
    if (uploadsInProgress > 0) setConfirmSignOut(true);
    else void signOut();
  }

  async function onSwitch(m: Membership) {
    if (m.org_id === active?.org_id) return;
    const ok = await switchOrg(m);
    toast(
      ok
        ? { title: t('nav.orgSwitched', { org: m.org_name }), tone: 'success' }
        : { title: t('nav.orgSwitchFailed'), tone: 'error' },
    );
  }

  return (
    <header className="sticky top-0 z-30 flex h-[58px] items-center gap-2 border-b border-line bg-surface-1 px-3 sm:gap-3 sm:px-5 lg:gap-[22px]">
      {/* < 1024 px (UIUX §09): Library / Exports collapse into ☰ */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={t('nav.menu')}
            className="grid size-8 place-items-center rounded-md text-fg-2 focus-ring hover:bg-surface-3 hover:text-fg lg:hidden"
          >
            <Menu size={18} aria-hidden="true" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-[220px]">
          <DropdownMenuItem
            icon={<Film size={16} />}
            aria-current={libraryActive ? 'page' : undefined}
            onSelect={() => void navigate({ to: '/library' })}
          >
            {t('nav.library')}
          </DropdownMenuItem>
          <DropdownMenuItem
            icon={<Download size={16} />}
            aria-current={pathname.startsWith('/exports') ? 'page' : undefined}
            onSelect={() => void navigate({ to: '/exports' })}
          >
            {t('nav.exports')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Link to="/library" aria-label={t('nav.home')} className="rounded-md focus-ring">
        <Logo label={t('appName')} compact />
      </Link>
      <nav aria-label={t('nav.primary')} className="hidden gap-0.5 lg:flex">
        <Link
          to="/library"
          className={cn(navLink, libraryActive && navActive)}
          aria-current={libraryActive ? 'page' : undefined}
        >
          {t('nav.library')}
        </Link>
        <Link to="/exports" className={navLink} activeProps={{ className: navActive }}>
          {t('nav.exports')}
        </Link>
      </nav>
      <div className="flex-1" />
      {can(role, 'editor') && (
        <Tooltip content={t('nav.newAnalysis')} shortcut={<Kbd>N</Kbd>}>
          <Button asChild variant="primary" size="sm">
            <Link to="/library" search={{ new: 'youtube' }} state={{ pvDialog: 'new' } as never}>
              <Plus size={16} aria-hidden="true" />
              <span className="sr-only sm:not-sr-only">{t('nav.newAnalysis')}</span>
            </Link>
          </Button>
        </Tooltip>
      )}
      <UsageMeter />
      <NotificationMenu />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={t('nav.account')}
            className="grid size-8 place-items-center rounded-full bg-[linear-gradient(135deg,#4da3ff,#7c5cff)] text-xs font-bold text-white focus-ring"
          >
            {initials(user?.name) || <User size={16} aria-hidden="true" />}
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-[260px]">
          {user && (
            <DropdownMenuLabel className="flex flex-col gap-0.5 pb-2">
              <span className="truncate text-control font-semibold text-fg">{user.name}</span>
              <span className="truncate">{user.email}</span>
              {active && (
                <span className="truncate">
                  {active.org_name} · {t(`roles.${active.role}`)}
                </span>
              )}
            </DropdownMenuLabel>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            icon={<User size={16} />}
            onSelect={() =>
              void navigate({ to: '/settings/$section', params: { section: 'profile' } })
            }
          >
            {t('nav.profile')}
          </DropdownMenuItem>
          {can(role, 'admin') && (
            <DropdownMenuItem
              icon={<Building2 size={16} />}
              onSelect={() =>
                void navigate({ to: '/settings/$section', params: { section: 'organization' } })
              }
            >
              {t('nav.organization')}
            </DropdownMenuItem>
          )}
          {can(role, 'owner') && (
            <DropdownMenuItem
              icon={<CreditCard size={16} />}
              onSelect={() =>
                void navigate({ to: '/settings/$section', params: { section: 'billing' } })
              }
            >
              {t('nav.billing')}
            </DropdownMenuItem>
          )}
          <DropdownMenuItem
            icon={<SlidersHorizontal size={16} />}
            onSelect={() =>
              void navigate({ to: '/settings/$section', params: { section: 'preferences' } })
            }
          >
            {t('nav.preferences')}
          </DropdownMenuItem>
          {memberships.length >= 2 && (
            <DropdownMenuSub>
              <DropdownMenuSubTrigger icon={<ArrowLeftRight size={16} />}>
                {t('nav.switchOrg')}
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="w-[240px]">
                {memberships.map((m) => {
                  const current = m.org_id === active?.org_id;
                  return (
                    <DropdownMenuItem
                      key={m.org_id}
                      icon={<Check size={16} className={current ? 'text-accent' : 'invisible'} />}
                      aria-current={current ? 'true' : undefined}
                      onSelect={() => void onSwitch(m)}
                      shortcut={<span className="text-xs text-fg-3 uppercase">{m.region}</span>}
                    >
                      {m.org_name}
                    </DropdownMenuItem>
                  );
                })}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem
            icon={<Keyboard size={16} />}
            shortcut={<Kbd>?</Kbd>}
            onSelect={onOpenShortcuts}
          >
            {t('nav.shortcuts')}
          </DropdownMenuItem>
          <DropdownMenuItem
            icon={<CircleHelp size={16} />}
            onSelect={() => window.open(HELP_URL, '_blank', 'noopener,noreferrer')}
          >
            {t('nav.help')}
          </DropdownMenuItem>
          <DropdownMenuItem
            icon={theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
            onSelect={(e) => {
              e.preventDefault(); // keep the menu open so the change is visible
              toggleTheme();
            }}
          >
            {t('nav.theme')}: {t(`theme.${theme}`)}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem icon={<LogOut size={16} />} onSelect={requestSignOut}>
            {t('nav.signOut')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {/* DLG-03: an upload is running — signing out would cancel it. */}
      <Dialog
        open={confirmSignOut}
        onOpenChange={setConfirmSignOut}
        size="sm"
        title={t('signOutDialog.title')}
        footer={
          <>
            <DialogClose asChild>
              <Button variant="ghost">{t('signOutDialog.stay')}</Button>
            </DialogClose>
            <Button
              variant="danger"
              onClick={() => {
                setConfirmSignOut(false);
                void signOut();
              }}
            >
              {t('signOutDialog.leave')}
            </Button>
          </>
        }
      >
        <p className="m-0 text-fg-2">{t('signOutDialog.body')}</p>
      </Dialog>
    </header>
  );
}
