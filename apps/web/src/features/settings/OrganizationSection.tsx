import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { identityApi } from '@/api/identity';
import { useApiErrorMessage } from '@/api/useApiErrorMessage';
import { refreshSession, useActiveMembership } from '@/auth/session';
import { Button, Card, Field, Input, toast } from '@/ui';
import { SavedTeamsCard } from './SavedTeamsCard';

const NAME_MAX = 120;

/** SCR-40 Organization (admin+): O01 name; O02 data region read-only with the reason; O03 saved teams. */
export function OrganizationSection() {
  const { t } = useTranslation('settings');
  const { t: tc } = useTranslation();
  const active = useActiveMembership();
  const [name, setName] = useState<string | null>(null);
  const value = name ?? active?.org_name ?? '';
  const save = useMutation({
    mutationFn: () => {
      if (!active) throw new Error('No active organization');
      return identityApi.updateOrg(active.org_id, value.trim());
    },
    onSuccess: async () => {
      await refreshSession(); // memberships carry the org name (top bar, switcher)
      setName(null);
      toast({ title: t('organization.saved'), tone: 'success' });
    },
  });
  const error = useApiErrorMessage(save.error);
  if (!active) return null;

  return (
    <>
      <Card className="flex flex-col gap-4">
        <Field label={t('organization.name')} error={error}>
          <Input value={value} maxLength={NAME_MAX} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t('organization.region')} help={t('organization.regionHelp')}>
          <Input value={tc(`regions.${active.region}`)} readOnly />
        </Field>
        <div>
          <Button
            variant="primary"
            disabled={!value.trim() || value.trim() === active.org_name}
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            {t('profile.save')}
          </Button>
        </div>
      </Card>
      <SavedTeamsCard />
    </>
  );
}
