import { getRouteApi, Link } from '@tanstack/react-router';
import { ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useFormat } from '@/i18n/format';
import { Card, Tabs, TabsContent, TabsList, TabsTrigger } from '@/ui';
import { WORKSPACE_TABS, type WorkspaceTab } from './tabs';

const route = getRouteApi('/_app/videos/$videoId/$tab');

/** SCR-20 Analysis workspace (stub). Tabs are routes: /videos/:id/:tab?t=<s>. */
export function WorkspacePage() {
  const { videoId, tab } = route.useParams();
  const { t: seconds } = route.useSearch();
  const navigate = route.useNavigate();
  const { t } = useTranslation('video');
  const fmt = useFormat();

  return (
    <div className="mx-auto max-w-[1600px] px-5 pt-4 pb-[60px]">
      <nav
        aria-label={t('workspace.breadcrumb')}
        className="mb-3 flex items-center gap-2.5 text-md"
      >
        <Link to="/library" className="rounded-xs font-semibold text-fg-2 focus-ring hover:text-fg">
          {t('workspace.crumb')}
        </Link>
        <ChevronRight size={16} className="text-fg-3" aria-hidden="true" />
        <b className="font-mono text-lg font-bold">{videoId}</b>
      </nav>
      <Tabs
        value={tab}
        onValueChange={(v) =>
          void navigate({ params: { videoId, tab: v as WorkspaceTab }, search: (s) => s })
        }
      >
        <TabsList aria-label={t('workspace.tabs.label')}>
          {WORKSPACE_TABS.map((key) => (
            <TabsTrigger key={key} value={key}>
              {t(`workspace.tabs.${key}`)}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="overview">
          <Card>
            <p className="m-0 text-fg-2">{t('workspace.placeholder')}</p>
            {seconds !== undefined && (
              <p className="m-0 mt-2 text-caption text-fg-3">
                {t('workspace.position', { time: fmt.timecode(seconds * 1000) })}
              </p>
            )}
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
