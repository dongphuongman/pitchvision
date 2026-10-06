import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getRouteApi, Link } from '@tanstack/react-router';
import { ArrowLeftRight, TriangleAlert } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Page } from '@/app/Page';
import {
  patchTeams,
  teamsQuery,
  type AttackSide,
  type SampleCrop,
  type SampleSheet,
  type Team,
} from '@/api/corrections';
import { can } from '@/auth/roles';
import { useRole } from '@/auth/session';
import { cn } from '@/lib/cn';
import {
  Banner,
  Button,
  Card,
  Field,
  IconButton,
  Input,
  SegmentedControl,
  Skeleton,
  toast,
} from '@/ui';
import { BackLink, CorrectionError } from '@/features/video/corrections';
import { KeyRoles } from './KeyRoles';
import { ManualTeams } from './ManualTeams';

const route = getRouteApi('/_app/videos/$videoId/teams');

/** Suggested display colours (E03); the cluster's own shirt colour is offered first. */
const PRESETS = ['#ffffff', '#111827', '#e5484d', '#3e8bff', '#3ddc84', '#f2c94c', '#f97316'];
const DEFAULT_COLORS = ['#ffffff', '#3e8bff'];
const SPRITE_H = 76;
const NAME_MAX = 40;
/** Euclidean RGB distance under which two team colours are "too close" (E03 warning). */
const MIN_COLOR_DISTANCE = 80;

interface Draft {
  cluster: number;
  name: string;
  color: string;
}

function rgb(hex: string): [number, number, number] {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function colorDistance(a: string, b: string): number {
  const [r1, g1, b1] = rgb(a);
  const [r2, g2, b2] = rgb(b);
  return Math.hypot(r1 - r2, g1 - g2, b1 - b2);
}

function toDrafts(items: Team[]): Draft[] {
  return [...items]
    .sort((a, b) => (a.cluster ?? 0) - (b.cluster ?? 0))
    .map((team, i) => ({
      cluster: team.cluster ?? i,
      name: team.name ?? `Team ${String.fromCharCode(65 + i)}`,
      color: team.color ?? DEFAULT_COLORS[i] ?? '#ffffff',
    }));
}

function validName(name: string): boolean {
  const n = name.trim().length;
  return n >= 1 && n <= NAME_MAX;
}

/** SCR-14 Team confirmation (`?mode=confirm`, default) and manual player picking (`?mode=manual`). */
export function TeamsPage() {
  const { videoId } = route.useParams();
  const { mode, return_to } = route.useSearch();
  const { t } = useTranslation('video');
  const back = return_to ?? `/videos/${encodeURIComponent(videoId)}/overview`;
  const manual = mode === 'manual';

  return (
    <Page className="max-w-[1180px]">
      <BackLink href={back} />
      <h1 className="mt-2.5 mb-1">{manual ? t('teams.titleManual') : t('teams.title')}</h1>
      <p className="mt-0 mb-5 text-fg-2">{manual ? t('teams.introManual') : t('teams.intro')}</p>
      {manual ? <ManualTeams videoId={videoId} /> : <ConfirmTeams videoId={videoId} back={back} />}
    </Page>
  );
}

function ConfirmTeams({ videoId, back }: { videoId: string; back: string }) {
  const { t } = useTranslation('video');
  const navigate = route.useNavigate();
  const qc = useQueryClient();
  const canEdit = can(useRole(), 'editor');
  const teams = useQuery(teamsQuery(videoId));
  const [edited, setEdited] = useState<Draft[] | null>(null);
  const [attackEdit, setAttackEdit] = useState<AttackSide | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [announce, setAnnounce] = useState('');

  const save = useMutation({
    mutationFn: ({ drafts, attack }: { drafts: Draft[]; attack: AttackSide | null }) =>
      patchTeams(
        videoId,
        drafts.map((d) => ({ cluster: d.cluster, name: d.name.trim(), color: d.color })),
        attack,
      ),
    onSuccess: (data) => {
      qc.setQueryData(teamsQuery(videoId).queryKey, data);
      void qc.invalidateQueries({ queryKey: ['results', videoId, 'manifest'] });
      toast({ title: t('teams.confirmed'), tone: 'success' });
      void navigate({ href: back });
    },
  });

  if (teams.isPending) return <Skeleton className="h-72" />;
  if (teams.isError) {
    return <CorrectionError error={teams.error} videoId={videoId} />;
  }
  const items = teams.data.items;
  if (items.length < 2) return <Banner tone="info">{t('teams.noTeams')}</Banner>;

  const drafts = edited ?? toDrafts(items);
  const first = items.find((it) => it.cluster === 0);
  const attack = attackEdit ?? first?.attack_h1 ?? null;
  const attackAuto = attackEdit === null && first?.attack_source === 'auto';
  const [a, b] = drafts;
  const update = (cluster: number, patch: Partial<Draft>) =>
    setEdited(drafts.map((d) => (d.cluster === cluster ? { ...d, ...patch } : d)));
  const swap = () => {
    if (!a || !b) return;
    setEdited([
      { cluster: a.cluster, name: b.name, color: b.color },
      { cluster: b.cluster, name: a.name, color: a.color },
    ]);
    setAnnounce(t('teams.swapped'));
  };
  const tooClose = a && b ? colorDistance(a.color, b.color) < MIN_COLOR_DISTANCE : false;
  const invalid = drafts.some((d) => !validName(d.name));
  const sheet = teams.data.sample_sheet ?? null;

  return (
    <div className="flex flex-col gap-4">
      {!canEdit && <Banner tone="info">{t('teams.readOnly')}</Banner>}
      <div className="grid items-stretch gap-3.5 md:grid-cols-[1fr_auto_1fr]">
        {drafts.map((draft, i) => (
          <TeamCard
            key={draft.cluster}
            n={i + 1}
            draft={draft}
            suggested={items.find((it) => it.cluster === draft.cluster)?.color ?? null}
            samples={items.find((it) => it.cluster === draft.cluster)?.samples ?? []}
            sheet={sheet}
            readOnly={!canEdit}
            showError={submitted}
            onChange={(patch) => update(draft.cluster, patch)}
            extra={
              draft.cluster === 0 ? (
                <AttackField
                  value={attack}
                  auto={attackAuto}
                  other={b?.name ?? ''}
                  readOnly={!canEdit}
                  onChange={setAttackEdit}
                />
              ) : null
            }
            between={
              i === 0 && canEdit ? (
                <div className="grid place-items-center">
                  <IconButton
                    label={t('teams.swap')}
                    icon={<ArrowLeftRight size={18} />}
                    onClick={swap}
                  />
                </div>
              ) : i === 0 ? (
                <div aria-hidden="true" />
              ) : null
            }
          />
        ))}
      </div>
      <p aria-live="polite" className="sr-only">
        {announce}
      </p>
      <KeyRoles videoId={videoId} teams={drafts} readOnly={!canEdit} />
      {tooClose && (
        <Banner tone="warn">
          <span aria-live="polite">{t('teams.colorsTooClose')}</span>
        </Banner>
      )}
      {save.isError && (
        <CorrectionError
          error={save.error}
          videoId={videoId}
          reasons={(r) => (r === 'no_teams' ? t('teams.noTeams') : undefined)}
        />
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {canEdit && (
          <Link
            to="/videos/$videoId/teams"
            params={{ videoId }}
            search={(s) => ({ ...s, mode: 'manual' as const })}
            className="inline-flex items-center gap-1.5 rounded-xs text-sm text-fg-2 focus-ring hover:text-fg"
          >
            <TriangleAlert size={14} aria-hidden="true" />
            {t('teams.mixed')}
          </Link>
        )}
        <div className="flex-1" />
        {canEdit && (
          <>
            <Button variant="ghost" onClick={() => void navigate({ href: back })}>
              {t('teams.skip')}
            </Button>
            <Button
              variant="primary"
              disabled={save.isPending}
              onClick={() => {
                setSubmitted(true);
                if (!invalid) save.mutate({ drafts, attack });
              }}
            >
              {t('teams.confirm')}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function TeamCard({
  n,
  draft,
  suggested,
  samples,
  sheet,
  readOnly,
  showError,
  onChange,
  extra,
  between,
}: {
  n: number;
  draft: Draft;
  suggested: string | null;
  samples: SampleCrop[];
  sheet: SampleSheet | null;
  readOnly: boolean;
  showError: boolean;
  onChange: (patch: Partial<Draft>) => void;
  extra?: React.ReactNode;
  between: React.ReactNode;
}) {
  const { t } = useTranslation('video');
  const headingId = useId();
  const colorLabelId = useId();
  const swatches = [...new Set([suggested, ...PRESETS].filter((c): c is string => !!c))];
  const nameError = showError && !validName(draft.name) ? t('teams.nameError') : undefined;

  return (
    <>
      <Card role="region" aria-labelledby={headingId} className="flex min-w-0 flex-col gap-4">
        <div className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="h-3 w-3 rounded-full ring-1 ring-line-strong"
            style={{ background: draft.color }}
          />
          <h2 id={headingId} className="m-0 text-md font-bold">
            {t('teams.group', { n })}
          </h2>
        </div>
        <SampleStrip n={n} samples={samples} sheet={sheet} />
        <Field label={t('teams.name')} error={nameError}>
          <Input
            value={draft.name}
            maxLength={NAME_MAX}
            readOnly={readOnly}
            onChange={(e) => onChange({ name: e.target.value })}
          />
        </Field>
        <div className="grid gap-1.5">
          <span id={colorLabelId} className="text-caption font-semibold text-fg-2">
            {t('teams.color')}
          </span>
          <div role="group" aria-labelledby={colorLabelId} className="flex flex-wrap gap-2">
            {swatches.map((c) => (
              <button
                key={c}
                type="button"
                disabled={readOnly}
                aria-pressed={draft.color.toLowerCase() === c.toLowerCase()}
                aria-label={t('teams.swatch', { color: c })}
                onClick={() => onChange({ color: c })}
                className={cn(
                  'h-7 w-7 rounded-full border border-line-strong focus-ring disabled:cursor-not-allowed',
                  draft.color.toLowerCase() === c.toLowerCase() &&
                    'ring-2 ring-accent ring-offset-2 ring-offset-surface-1',
                )}
                style={{ background: c }}
              />
            ))}
            <input
              type="color"
              aria-label={t('teams.customColor')}
              disabled={readOnly}
              value={draft.color}
              onChange={(e) => onChange({ color: e.target.value.toLowerCase() })}
              className="h-7 w-9 cursor-pointer rounded-md border border-line-strong bg-transparent p-0 focus-ring disabled:cursor-not-allowed"
            />
          </div>
        </div>
        {extra}
      </Card>
      {between}
    </>
  );
}

/** E05: side this team attacks in the first half (the other team gets the opposite). */
function AttackField({
  value,
  auto,
  other,
  readOnly,
  onChange,
}: {
  value: AttackSide | null;
  auto: boolean;
  other: string;
  readOnly: boolean;
  onChange: (v: AttackSide) => void;
}) {
  const { t } = useTranslation('video');
  const labelId = useId();
  return (
    <div className="grid gap-1.5">
      <span id={labelId} className="text-caption font-semibold text-fg-2">
        {t('teams.attack')}
      </span>
      <SegmentedControl<AttackSide>
        aria-labelledby={labelId}
        value={value ?? undefined}
        disabled={readOnly}
        onValueChange={onChange}
        options={[
          { value: 'left', label: t('teams.attackLeft') },
          { value: 'right', label: t('teams.attackRight') },
        ]}
      />
      <p className="m-0 text-caption text-fg-3">
        {value === null
          ? t('teams.attackUnknown')
          : `${auto ? `${t('teams.attackAuto')} ` : ''}${t('teams.attackOther', { name: other })}`}
      </p>
    </div>
  );
}

/** Up to 8 player crops of a cluster, drawn as CSS sprites from the one presigned sample sheet. */
function SampleStrip({
  n,
  samples,
  sheet,
}: {
  n: number;
  samples: SampleCrop[];
  sheet: SampleSheet | null;
}) {
  const { t } = useTranslation('video');
  if (!sheet || samples.length === 0) {
    return <p className="m-0 text-caption text-fg-3">{t('teams.noSamples')}</p>;
  }
  return (
    <ul aria-label={t('teams.samples', { n })} className="m-0 flex list-none flex-wrap gap-1.5 p-0">
      {samples.slice(0, 8).map((s, i) => {
        const k = SPRITE_H / s.h;
        return (
          <li
            key={`${s.x}:${s.y}`}
            role="img"
            aria-label={t('teams.sample', { n: i + 1 })}
            className="overflow-hidden rounded-md bg-surface-3"
            style={{
              width: Math.round(s.w * k),
              height: SPRITE_H,
              backgroundImage: `url("${sheet.url}")`,
              backgroundRepeat: 'no-repeat',
              backgroundSize: `${sheet.width * k}px ${sheet.height * k}px`,
              backgroundPosition: `${-s.x * k}px ${-s.y * k}px`,
            }}
          />
        );
      })}
    </ul>
  );
}
