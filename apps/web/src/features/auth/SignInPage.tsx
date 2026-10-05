import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { Logo } from '@/app/Logo';
import { Button, Field, Input } from '@/ui';

/** SCR-02 Sign in (layout stub; auth flow lands in M1-FE-04). Prototype `.auth` split layout. */
export function SignInPage() {
  const { t } = useTranslation('auth');
  const { t: tc } = useTranslation();
  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <div className="relative hidden overflow-hidden bg-pitch lg:block">
        <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(6,10,14,.2),rgba(6,10,14,.85))]" />
        <div className="absolute right-12 bottom-12 left-12 z-[2] text-white">
          <Logo label={tc('appName')} size="lg" />
          <h2 className="mt-[18px] mb-2 text-[34px] leading-[1.1] font-extrabold tracking-[-.03em]">
            {t('tagline')}
          </h2>
          <p className="m-0 text-md text-white/75">{t('taglineBody')}</p>
        </div>
      </div>
      <div className="grid place-items-center bg-bg p-10">
        <form
          className="flex w-full max-w-[400px] flex-col gap-4"
          onSubmit={(e) => e.preventDefault()}
          noValidate
        >
          <h1 className="text-[26px] tracking-[-.02em]">{t('signIn.title')}</h1>
          <p className="-mt-2 mb-1 text-fg-2">{t('signIn.subtitle')}</p>
          <Field label={t('signIn.email')}>
            <Input type="email" autoComplete="email" required />
          </Field>
          <Field label={t('signIn.password')}>
            <Input type="password" autoComplete="current-password" required />
          </Field>
          <Button type="submit" variant="primary" size="lg" className="w-full">
            {t('signIn.submit')}
          </Button>
          <p className="m-0 text-center text-control text-fg-2">
            {t('signIn.noAccount')}{' '}
            <Link to="/library" className="font-semibold text-accent hover:underline">
              {t('signIn.create')}
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
