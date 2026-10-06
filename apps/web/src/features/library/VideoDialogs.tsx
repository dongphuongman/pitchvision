import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { isApiError } from '@/api/errors';
import { findVideoInCache, patchVideoInCache, renameVideo, type Video } from '@/api/videos';
import { Button, Dialog, DialogClose, Field, Input, toast } from '@/ui';
import { TITLE_MAX, videoTitle } from './model';
import { scheduleDelete } from './pendingDeletes';

/**
 * DLG-04 Rename: one title field. Cancel → close · Save → stays on the underlying screen.
 * Optimistic: the new title shows immediately and rolls back if the PATCH fails.
 */
export function RenameVideoDialog({
  video,
  onOpenChange,
}: {
  video: Video | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation('library');
  return (
    <Dialog open={video !== null} onOpenChange={onOpenChange} title={t('rename.title')} size="sm">
      {video && <RenameForm key={video.id} video={video} onDone={() => onOpenChange(false)} />}
    </Dialog>
  );
}

function RenameForm({ video, onDone }: { video: Video; onDone: () => void }) {
  const { t } = useTranslation('library');
  const { t: tc } = useTranslation();
  const qc = useQueryClient();
  const [title, setTitle] = useState(video.title ?? '');
  const [touched, setTouched] = useState(false);
  const trimmed = title.trim();
  const error = !trimmed
    ? t('rename.required')
    : trimmed.length > TITLE_MAX
      ? t('rename.tooLong', { max: TITLE_MAX })
      : undefined;

  const mutation = useMutation({
    mutationFn: (next: string) => renameVideo(video.id, next),
    onMutate: (next) => {
      const before = findVideoInCache(qc, video.id)?.title ?? video.title;
      patchVideoInCache(qc, video.id, { title: next });
      return { before };
    },
    onError: (_e, _next, ctx) => {
      patchVideoInCache(qc, video.id, { title: ctx?.before ?? video.title });
      toast({ title: t('rename.failed'), tone: 'error' });
    },
    onSuccess: (saved) => patchVideoInCache(qc, video.id, saved),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (error) return;
    if (trimmed !== (video.title ?? '')) mutation.mutate(trimmed);
    onDone();
  };

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-5">
      <Field label={t('rename.label')} error={touched ? error : undefined}>
        <Input
          autoFocus
          value={title}
          maxLength={TITLE_MAX + 50}
          onChange={(e) => setTitle(e.target.value)}
          onFocus={(e) => e.currentTarget.select()}
        />
      </Field>
      <div className="flex justify-end gap-2.5">
        <DialogClose asChild>
          <Button>{tc('actions.cancel')}</Button>
        </DialogClose>
        <Button type="submit" variant="primary">
          {t('rename.save')}
        </Button>
      </div>
    </form>
  );
}

/**
 * DLG-01 Delete video: Cancel → close · Delete (danger, right) → back to the library with a 10 s
 * Undo toast; the DELETE is sent when the window ends.
 */
export function DeleteVideoDialog({
  video,
  onOpenChange,
}: {
  video: Video | null;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation('library');
  const { t: tc } = useTranslation();
  const qc = useQueryClient();
  const { t: te } = useTranslation('errors');
  const title = video ? videoTitle(video, t('card.untitled')) : '';

  const confirm = () => {
    if (!video) return;
    const name = title;
    const pending = scheduleDelete(qc, video.id, {
      onError: (e) =>
        toast({
          title: t('delete.failed', { title: name }),
          description: te(`codes.${isApiError(e) ? e.code : 'UNKNOWN'}`),
          tone: 'error',
        }),
    });
    toast({
      title: t('delete.deleted', { title: name }),
      action: {
        label: t('delete.undo'),
        onClick: () => {
          pending.undo();
          toast({ title: t('delete.restored', { title: name }), tone: 'success' });
        },
      },
      duration: 10_000,
    });
    onOpenChange(false);
  };

  return (
    <Dialog
      open={video !== null}
      onOpenChange={onOpenChange}
      title={t('delete.title', { title })}
      size="sm"
      footer={
        <>
          <DialogClose asChild>
            <Button>{tc('actions.cancel')}</Button>
          </DialogClose>
          <Button variant="danger-solid" onClick={confirm}>
            {t('delete.confirm')}
          </Button>
        </>
      }
    >
      <p className="m-0 text-fg-2">{t('delete.body')}</p>
    </Dialog>
  );
}
