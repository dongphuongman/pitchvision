import { useMutation } from '@tanstack/react-query';
import { getRouteApi, useNavigate } from '@tanstack/react-router';
import { ChevronLeft, ChevronRight, Lock } from 'lucide-react';
import { Popover, RadioGroup } from 'radix-ui';
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { identityApi, ORG_KINDS, REGIONS, type Region } from '@/api/identity';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { safeReturnTo } from '@/auth/redirects';
import { switchOrganization, useActiveMembership } from '@/auth/session';
import { changeLanguage } from '@/i18n';
import { SUPPORTED_LANGUAGES, type Language } from '@/i18n/resources';
import { cn } from '@/lib/cn';
import { usePreferences, type UnitSystem } from '@/lib/preferences';
import { suggestRegion } from '@/lib/region';
import { Badge, Banner, Button, Field, Input, SegmentedControl, Select, toast } from '@/ui';
import { CenteredCard } from '../auth/AuthLayout';
import { ONBOARDING_STEPS, useOnboardingDraft, type OnboardingStep } from './draft';

const route = getRouteApi('/onboarding/$step');

const ROLE_TITLES = [
  'head_coach',
  'coach',
  'analyst',
  'scout',
  'director',
  'journalist',
  'player',
  'other',
] as const;

/** Only languages with a shipped catalogue are offered (same rule as Settings → Preferences). */
const AVAILABLE_LANGUAGES: readonly Language[] = SUPPORTED_LANGUAGES.filter((l) => l === 'en');

/** SCR-07 Onboarding wizard: /onboarding/organization → /region → /preferences. */
export function OnboardingPage() {
  const { t } = useTranslation('onboarding');
  const { step } = route.useParams();
  const index = ONBOARDING_STEPS.indexOf(step);
  // Focus the step heading on every step change (not on first load: keep the name field focus).
  const headingRef = useRef<HTMLHeadingElement>(null);
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    headingRef.current?.focus();
  }, [step]);

  const progress = (
    <div className="flex items-center gap-2 text-caption font-semibold text-fg-3">
      {ONBOARDING_STEPS.map((s, i) => (
        <i
          key={s}
          aria-hidden="true"
          className={cn('h-1 w-7 rounded-[2px] bg-line-strong', i <= index && 'bg-accent')}
        />
      ))}
      <span className="ml-2">{t('progress', { step: index + 1, total: 3 })}</span>
    </div>
  );

  return (
    <CenteredCard header={progress}>
      {step === 'organization' && <OrganizationStep headingRef={headingRef} />}
      {step === 'region' && <RegionStep headingRef={headingRef} />}
      {step === 'preferences' && <PreferencesStep headingRef={headingRef} />}
    </CenteredCard>
  );
}

type HeadingRef = React.RefObject<HTMLHeadingElement>;

function StepHeading({
  headingRef,
  title,
  sub,
}: {
  headingRef: HeadingRef;
  title: ReactNode;
  sub: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <h1 ref={headingRef} tabIndex={-1} className="text-[26px] outline-none">
        {title}
      </h1>
      <p className="m-0 text-fg-2">{sub}</p>
    </div>
  );
}

function useGoToStep() {
  const navigate = useNavigate();
  const { return_to } = route.useSearch();
  return (step: OnboardingStep) =>
    navigate({
      to: '/onboarding/$step',
      params: { step },
      search: { return_to: safeReturnTo(return_to) },
    });
}

/* ---------- Step 1: organization ---------- */

function OrganizationStep({ headingRef }: { headingRef: HeadingRef }) {
  const { t } = useTranslation('onboarding');
  const draft = useOnboardingDraft();
  const goTo = useGoToStep();
  const [name, setName] = useState(draft.name);
  const [nameError, setNameError] = useState<string>();
  const nameRef = useRef<HTMLInputElement>(null);
  const kindLabelId = useId();
  const created = draft.createdOrgId !== null;

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  // After creation only the name can still change (PATCH /orgs/{id}; region/kind are fixed).
  const rename = useMutation({
    mutationFn: (newName: string) => identityApi.updateOrg(draft.createdOrgId ?? '', newName),
  });
  const error = useApiErrorMessage(rename.error);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError(t('org.nameRequired'));
      nameRef.current?.focus();
      return;
    }
    setNameError(undefined);
    if (created && trimmed !== draft.name) {
      try {
        await rename.mutateAsync(trimmed);
      } catch {
        return;
      }
    }
    draft.set({ name: trimmed });
    void goTo('region');
  }

  return (
    <form className="flex flex-col gap-[18px]" onSubmit={(e) => void onSubmit(e)} noValidate>
      <StepHeading headingRef={headingRef} title={t('org.title')} sub={t('org.subtitle')} />
      {error && <Banner tone="err">{error}</Banner>}
      <Field label={t('org.name')} error={nameError}>
        <Input
          ref={nameRef}
          name="organization"
          autoComplete="organization"
          placeholder={t('org.namePlaceholder')}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <div className="grid gap-1.5">
        <span id={kindLabelId} className="text-caption font-semibold text-fg-2">
          {t('org.kind')}
        </span>
        <SegmentedControl
          aria-labelledby={kindLabelId}
          value={draft.kind}
          disabled={created}
          onValueChange={(kind) => draft.set({ kind })}
          options={ORG_KINDS.map((k) => ({ value: k, label: t(`org.kinds.${k}`) }))}
          className="self-start"
        />
        {created && <p className="m-0 text-caption text-fg-3">{t('org.kindLocked')}</p>}
      </div>
      <Field label={t('org.role')}>
        <Select
          value={draft.roleTitle || undefined}
          placeholder={t('org.rolePlaceholder')}
          onValueChange={(roleTitle) => draft.set({ roleTitle })}
          disabled={created}
          options={ROLE_TITLES.map((r) => ({ value: r, label: t(`org.roles.${r}`) }))}
        />
      </Field>
      <div className="flex justify-end">
        <Button type="submit" variant="primary" loading={rename.isPending}>
          <ContinueLabel />
        </Button>
      </div>
    </form>
  );
}

function ContinueLabel() {
  const { t } = useTranslation();
  return (
    <>
      {t('actions.continue')}
      <ChevronRight size={16} aria-hidden="true" />
    </>
  );
}

function BackLabel() {
  const { t } = useTranslation();
  return (
    <>
      <ChevronLeft size={16} aria-hidden="true" />
      {t('actions.back')}
    </>
  );
}

/* ---------- Step 2: region ---------- */

function RegionStep({ headingRef }: { headingRef: HeadingRef }) {
  const { t } = useTranslation('onboarding');
  const { t: tc } = useTranslation();
  const draft = useOnboardingDraft();
  const goTo = useGoToStep();
  const active = useActiveMembership();
  const created = draft.createdOrgId !== null;
  const [suggested] = useState<Region | null>(() => suggestRegion());
  const region: Region | null = created ? (active?.region ?? draft.region) : draft.region;
  const [confirming, setConfirming] = useState(false);
  const [regionError, setRegionError] = useState<string>();
  const confirmRef = useRef<HTMLButtonElement>(null);
  const labelId = useId();

  // Pre-select the suggestion once (UIUX E05 "pre-suggested"); the user still confirms it.
  const suggestionApplied = useRef(false);
  useEffect(() => {
    if (suggestionApplied.current) return;
    suggestionApplied.current = true;
    const state = useOnboardingDraft.getState();
    if (state.createdOrgId === null && state.region === null && suggested) {
      state.set({ region: suggested });
    }
  }, [suggested]);

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
  }, [confirming]);

  const create = useMutation({
    mutationFn: async (r: Region) => {
      const org = await identityApi.createOrg({
        name: draft.name,
        kind: draft.kind,
        region: r,
        ...(draft.roleTitle ? { my_role_title: draft.roleTitle } : {}),
      });
      // New token whose org / role / region claims point at the new org.
      await switchOrganization(org.id);
      return org;
    },
    onSuccess: (org) => {
      draft.set({ createdOrgId: org.id, region: org.region });
      void goTo('preferences');
    },
  });
  const error = useApiErrorMessage(create.error);

  function onContinue() {
    if (created) return void goTo('preferences');
    if (!region) {
      setRegionError(t('region.required'));
      return;
    }
    setRegionError(undefined);
    setConfirming(true);
  }

  return (
    <div className="flex flex-col gap-[18px]">
      <StepHeading headingRef={headingRef} title={t('region.title')} sub={t('region.subtitle')} />
      <span id={labelId} className="sr-only">
        {t('region.title')}
      </span>
      <RadioGroup.Root
        aria-labelledby={labelId}
        aria-describedby={regionError ? `${labelId}-err` : undefined}
        aria-required="true"
        value={region ?? ''}
        disabled={created}
        onValueChange={(v) => {
          draft.set({ region: v as Region });
          setConfirming(false);
          setRegionError(undefined);
        }}
        className="grid gap-3 sm:grid-cols-2"
      >
        {REGIONS.map((r) => (
          <RadioGroup.Item
            key={r}
            value={r}
            className={cn(
              'group flex gap-3 rounded-xl border-[1.5px] border-line-strong bg-surface-2 p-4 text-left focus-ring',
              'data-[state=checked]:border-accent data-[state=checked]:bg-[color-mix(in_srgb,var(--ac)_7%,var(--s2))]',
              'disabled:cursor-not-allowed data-[disabled]:opacity-70',
            )}
          >
            <span
              aria-hidden="true"
              className="mt-0.5 grid size-[18px] flex-none place-items-center rounded-full border-[1.5px] border-line-strong group-data-[state=checked]:border-accent"
            >
              <RadioGroup.Indicator className="size-2 rounded-full bg-accent" />
            </span>
            <span className="flex flex-col gap-0.5">
              <b className="text-fg">{t(`region.options.${r}.name`)}</b>
              <span className="text-sm text-fg-3">{t(`region.options.${r}.detail`)}</span>
              {suggested === r && !created && (
                <Badge tone="ok" size="sm" className="mt-2 self-start">
                  {t('region.suggested')}
                </Badge>
              )}
            </span>
          </RadioGroup.Item>
        ))}
      </RadioGroup.Root>
      {regionError && (
        <p id={`${labelId}-err`} role="alert" className="m-0 text-caption text-danger">
          {regionError}
        </p>
      )}
      {created && region ? (
        <Banner tone="info">
          <span className="inline-flex items-center gap-1.5">
            <Lock size={14} aria-hidden="true" />
            {t('region.locked', { region: tc(`regions.${region}`) })}
          </span>
        </Banner>
      ) : (
        <Banner tone="warn">
          {t('region.permanent')}{' '}
          <Popover.Root>
            <Popover.Trigger className="rounded-sm font-semibold text-accent focus-ring hover:underline">
              {t('region.learnMore')}
            </Popover.Trigger>
            <Popover.Portal>
              <Popover.Content
                sideOffset={6}
                className="z-[85] max-w-[300px] rounded-xl border border-line-strong bg-surface-1 p-3 text-control text-fg shadow-pop"
              >
                {t('region.learnMoreBody')}
              </Popover.Content>
            </Popover.Portal>
          </Popover.Root>
        </Banner>
      )}
      {error && <Banner tone="err">{error}</Banner>}
      {confirming && region && !created && (
        <div
          role="group"
          aria-label={t('region.confirm', { region: tc(`regions.${region}`) })}
          className="flex flex-wrap items-center gap-3 rounded-lg border border-line-strong bg-surface-2 px-3.5 py-2.5"
        >
          <span className="flex-1 text-control">
            {t('region.confirm', { region: tc(`regions.${region}`) })}
          </span>
          <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
            {tc('actions.cancel')}
          </Button>
          <Button
            ref={confirmRef}
            variant="primary"
            size="sm"
            loading={create.isPending}
            onClick={() => create.mutate(region)}
          >
            {t('region.confirmCta')}
          </Button>
        </div>
      )}
      <div className="flex items-center">
        <Button variant="ghost" onClick={() => void goTo('organization')}>
          <BackLabel />
        </Button>
        <div className="flex-1" />
        {!confirming && (
          <Button variant="primary" onClick={onContinue}>
            <ContinueLabel />
          </Button>
        )}
      </div>
    </div>
  );
}

/* ---------- Step 3: preferences ---------- */

function PreferencesStep({ headingRef }: { headingRef: HeadingRef }) {
  const { t, i18n } = useTranslation('onboarding');
  const { t: tc } = useTranslation();
  const navigate = useNavigate();
  const { return_to } = route.useSearch();
  const draft = useOnboardingDraft();
  const goTo = useGoToStep();
  const active = useActiveMembership();
  const units = usePreferences((s) => s.units);
  const setUnits = usePreferences((s) => s.setUnits);
  const unitsLabelId = useId();
  const languageName = new Intl.DisplayNames([i18n.language], { type: 'language' });
  // Joined through an invitation: steps 1–2 don't apply (the org already existed).
  const ownWizard = draft.createdOrgId !== null;

  function finish() {
    draft.reset();
    toast({ title: t('prefs.welcome'), tone: 'success' });
    // TODO(SCR-41 / tour): plan=pro opens Upgrade, otherwise the 4-step coach-mark tour.
    void navigate({ href: safeReturnTo(return_to) ?? '/library', replace: true });
  }

  return (
    <div className="flex flex-col gap-[18px]">
      <StepHeading headingRef={headingRef} title={t('prefs.title')} sub={t('prefs.subtitle')} />
      <Field label={t('prefs.language')}>
        <Select
          value={i18n.language}
          onValueChange={(v) => void changeLanguage(v as Language)}
          options={AVAILABLE_LANGUAGES.map((l) => ({ value: l, label: languageName.of(l) ?? l }))}
        />
      </Field>
      <div className="grid gap-1.5">
        <span id={unitsLabelId} className="text-caption font-semibold text-fg-2">
          {t('prefs.units')}
        </span>
        <SegmentedControl<UnitSystem>
          aria-labelledby={unitsLabelId}
          value={units}
          onValueChange={setUnits}
          options={[
            { value: 'metric', label: t('prefs.metric') },
            { value: 'imperial', label: t('prefs.imperial') },
          ]}
          className="self-start"
        />
      </div>
      {active && (
        <Field label={t('prefs.region')}>
          <Input
            readOnly
            value={`${tc(`regions.${active.region}`)} · ${t(`region.options.${active.region}.detail`)}`}
            leading={<Lock size={14} />}
            className="bg-surface-2"
          />
        </Field>
      )}
      <div className="flex items-center">
        {ownWizard && (
          <Button variant="ghost" onClick={() => void goTo('region')}>
            <BackLabel />
          </Button>
        )}
        <div className="flex-1" />
        <Button variant="primary" onClick={finish}>
          {t('prefs.finish')}
        </Button>
      </div>
    </div>
  );
}
