import { Link, useNavigate, useRouterState } from '@tanstack/react-router';
import {
  Bell,
  Building2,
  CreditCard,
  Keyboard,
  LogOut,
  Moon,
  Plus,
  SlidersHorizontal,
  Sun,
  User,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/cn';
import { useResolvedTheme, useThemeStore } from '@/lib/theme';
import {
  Button,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  IconButton,
  Kbd,
  Tooltip,
} from '@/ui';
import { Logo } from './Logo';

const navLink =
  'focus-ring flex items-center gap-[7px] rounded-md px-3 py-[7px] font-semibold text-fg-2 hover:bg-surface-3 hover:text-fg';
const navActive = 'bg-surface-3 text-fg';

/**
 * Global top bar (UIUX G). The "Live" item (G.E03) is hidden until Phase C; the usage meter
 * (G.E06) appears once the billing API exists.
 */
export function TopBar({ onOpenShortcuts }: { onOpenShortcuts: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const libraryActive = pathname.startsWith('/library') || pathname.startsWith('/videos');
  const theme = useResolvedTheme();
  const toggleTheme = useThemeStore((s) => s.toggle);

  return (
    <header className="sticky top-0 z-30 flex h-[58px] items-center gap-[22px] border-b border-line bg-surface-1 px-5">
      <Link to="/library" aria-label={t('nav.home')} className="rounded-md focus-ring">
        <Logo label={t('appName')} />
      </Link>
      <nav aria-label={t('nav.primary')} className="flex gap-0.5">
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
      <Tooltip content={t('nav.newAnalysis')} shortcut={<Kbd>N</Kbd>}>
        <Button asChild variant="primary" size="sm">
          <Link to="/library" search={{ new: 'youtube' }} state={{ pvDialog: 'new' } as never}>
            <Plus size={16} aria-hidden="true" />
            {t('nav.newAnalysis')}
          </Link>
        </Button>
      </Tooltip>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <IconButton label={t('nav.notifications')} icon={<Bell size={18} />} />
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-[380px]">
          <p className="m-0 px-2.5 py-6 text-center text-control text-fg-3">
            {t('nav.noNotifications')}
          </p>
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label={t('nav.account')}
            className="grid size-8 place-items-center rounded-full bg-[linear-gradient(135deg,#4da3ff,#7c5cff)] text-white focus-ring"
          >
            <User size={16} aria-hidden="true" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-[250px]">
          <DropdownMenuItem
            icon={<User size={16} />}
            onSelect={() =>
              void navigate({ to: '/settings/$section', params: { section: 'profile' } })
            }
          >
            {t('nav.profile')}
          </DropdownMenuItem>
          <DropdownMenuItem
            icon={<Building2 size={16} />}
            onSelect={() =>
              void navigate({ to: '/settings/$section', params: { section: 'organization' } })
            }
          >
            {t('nav.organization')}
          </DropdownMenuItem>
          <DropdownMenuItem
            icon={<CreditCard size={16} />}
            onSelect={() =>
              void navigate({ to: '/settings/$section', params: { section: 'billing' } })
            }
          >
            {t('nav.billing')}
          </DropdownMenuItem>
          <DropdownMenuItem
            icon={<SlidersHorizontal size={16} />}
            onSelect={() =>
              void navigate({ to: '/settings/$section', params: { section: 'preferences' } })
            }
          >
            {t('nav.preferences')}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            icon={<Keyboard size={16} />}
            shortcut={<Kbd>?</Kbd>}
            onSelect={onOpenShortcuts}
          >
            {t('nav.shortcuts')}
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
          <DropdownMenuItem
            icon={<LogOut size={16} />}
            onSelect={() => void navigate({ to: '/signin' })}
          >
            {t('nav.signOut')}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
