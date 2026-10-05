import { Upload } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchDialog } from '@/lib/useSearchDialog';
import type { NewAnalysisSource } from '@/router';
import {
  Button,
  Checkbox,
  Dialog,
  DialogClose,
  Field,
  Input,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/ui';
import { YoutubeIcon } from '@/ui/icons';

/**
 * SCR-11 New analysis (modal lg) bound to `?new=youtube|upload`. Tab order per UIUX:
 * YouTube (default) · Upload · Import (Soon). The Live tab is Phase C and not rendered.
 * Submission is wired in M1-FE-09.
 */
export function NewAnalysisDialog() {
  const { t } = useTranslation('library');
  const { t: tc } = useTranslation();
  const dialog = useSearchDialog<NewAnalysisSource>('new');
  const [rights, setRights] = useState(false);

  return (
    <Dialog
      open={dialog.isOpen}
      onOpenChange={dialog.onOpenChange}
      title={t('newAnalysis.title')}
      description={t('newAnalysis.description')}
      size="lg"
      footer={
        <>
          <DialogClose asChild>
            <Button>{tc('actions.cancel')}</Button>
          </DialogClose>
          <Button variant="primary" disabled={!rights}>
            {t('newAnalysis.submit')}
          </Button>
        </>
      }
    >
      <Tabs
        value={dialog.value ?? 'youtube'}
        onValueChange={(v) => void dialog.open(v as NewAnalysisSource)}
      >
        <TabsList aria-label={t('newAnalysis.tabs.label')}>
          <TabsTrigger value="youtube" icon={<YoutubeIcon size={16} />}>
            {t('newAnalysis.tabs.youtube')}
          </TabsTrigger>
          <TabsTrigger value="upload" icon={<Upload size={16} />}>
            {t('newAnalysis.tabs.upload')}
          </TabsTrigger>
          <TabsTrigger value="import" disabled soon={tc('soon')}>
            {t('newAnalysis.tabs.import')}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="youtube" className="flex flex-col gap-4">
          <Field label={t('newAnalysis.youtube.label')} help={t('newAnalysis.youtube.help')}>
            <Input
              type="url"
              inputMode="url"
              autoFocus
              placeholder={t('newAnalysis.youtube.placeholder')}
              leading={<YoutubeIcon size={16} />}
            />
          </Field>
        </TabsContent>
        <TabsContent value="upload">
          <div className="rounded-2xl border-[1.5px] border-dashed border-line-strong bg-surface-2 p-7 text-center text-fg-2">
            <Upload size={28} className="mx-auto mb-2.5 text-accent" aria-hidden="true" />
            <p className="m-0 font-semibold text-fg">{t('newAnalysis.upload.drop')}</p>
            <p className="m-0 text-caption text-fg-3">
              {t('newAnalysis.upload.formats', { size: '20 GB' })}
            </p>
          </div>
        </TabsContent>
      </Tabs>
      <Checkbox
        className="mt-5"
        label={t('newAnalysis.rights')}
        checked={rights}
        onCheckedChange={(c) => setRights(c === true)}
      />
    </Dialog>
  );
}
