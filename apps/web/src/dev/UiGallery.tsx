/*
 * Dev-only component gallery (route /__ui). Not shipped in production builds and not translated:
 * the strings below are fixtures, not product copy.
 */
import {
  Bell,
  Download,
  LogOut,
  Ellipsis,
  Plus,
  Search,
  Settings,
  Share2,
  Trash2,
  Upload,
  Video,
} from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { passwordStrength } from '@/lib/password';
import { useThemeStore, type ThemePreference } from '@/lib/theme';
import {
  Badge,
  Banner,
  Button,
  Card,
  CardTitle,
  Checkbox,
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
  PasswordInput,
  SegmentedControl,
  StrengthMeter,
  EmptyState,
  Field,
  IconButton,
  Input,
  JOB_STATUS_TONE,
  Kbd,
  Progress,
  Select,
  SHOT_TYPE_TONE,
  Skeleton,
  Spinner,
  Switch,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Textarea,
  toast,
  Tooltip,
  VIDEO_STATUS_TONE,
  type JobStatus,
  type ShotType,
  type VideoStatus,
} from '@/ui';
import { YoutubeIcon } from '@/ui/icons';

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <div className="flex flex-wrap items-center gap-2.5">{children}</div>
    </Card>
  );
}

function Specimens() {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [sw, setSw] = useState(true);
  const [loading, setLoading] = useState(false);
  const [pw, setPw] = useState('Correct-Horse-9');
  const [units, setUnits] = useState('metric');

  return (
    <div className="flex flex-col gap-4">
      <Section title="Typography">
        <div className="flex flex-col gap-1">
          <h1>Heading 24 / 750</h1>
          <h2>Heading 20 / 750</h2>
          <h3>Heading 17 / 700</h3>
          <p className="m-0 text-fg-2">Body 14 / 1.5 secondary text</p>
          <p className="m-0 text-caption text-fg-3">Caption 12.5 tertiary</p>
          <span className="font-mono text-md tabular">00:42:17.08 · 52.4 FPS · 105 × 68 m</span>
        </div>
      </Section>

      <Section title="Buttons">
        <Button variant="primary" leadingIcon={<Plus size={16} />}>
          New analysis
        </Button>
        <Button>Secondary</Button>
        <Button variant="ghost">Ghost</Button>
        <Button variant="danger">Delete</Button>
        <Button variant="danger-solid">Delete forever</Button>
        <Button size="sm">Small</Button>
        <Button size="lg" variant="primary" leadingIcon={<YoutubeIcon size={18} />}>
          Paste a YouTube link
        </Button>
        <Button disabled>Disabled</Button>
        <Button
          variant="primary"
          loading={loading}
          onClick={() => {
            setLoading(true);
            window.setTimeout(() => setLoading(false), 1500);
          }}
        >
          {loading ? 'Analyzing…' : 'Click to load'}
        </Button>
      </Section>

      <Section title="Icon buttons + tooltip">
        <IconButton label="Notifications" icon={<Bell size={18} />} badge={3} />
        <IconButton label="Settings" icon={<Settings size={18} />} />
        <IconButton label="Download" icon={<Download size={16} />} variant="secondary" />
        <IconButton label="Share" icon={<Share2 size={15} />} variant="secondary" size="sm" />
        <Tooltip content="New analysis" shortcut={<Kbd>N</Kbd>}>
          <Button variant="primary" size="sm" leadingIcon={<Plus size={15} />}>
            New
          </Button>
        </Tooltip>
      </Section>

      <Section title="Form controls">
        <div className="grid w-full grid-cols-2 gap-4">
          <Field label="Match title" help="Shown in the library.">
            <Input placeholder="France vs Switzerland" />
          </Field>
          <Field label="YouTube URL" error="That doesn't look like a YouTube link.">
            <Input defaultValue="https://youtu.be/x" />
          </Field>
          <Field label="Search">
            <Input leading={<Search size={16} />} placeholder="Search matches…" />
          </Field>
          <Field label="Competition">
            <Select
              placeholder="Choose…"
              options={[
                { value: 'league', label: 'League' },
                { value: 'cup', label: 'Cup' },
                { value: 'friendly', label: 'Friendly' },
                { value: 'youth', label: 'Youth (disabled)', disabled: true },
              ]}
            />
          </Field>
          <Field label="Notes" className="col-span-2">
            <Textarea placeholder="Anything the analyst should know…" rows={3} />
          </Field>
          <div className="flex flex-col gap-3">
            <Checkbox label="I have the rights to analyze this video" defaultChecked />
            <Checkbox
              label="Start analysis when upload finishes"
              description="Off: the video is only stored."
            />
            <Checkbox label="Indeterminate" checked="indeterminate" />
          </div>
          <div className="flex flex-col gap-3">
            <Switch label="Show ball trail" checked={sw} onCheckedChange={setSw} />
            <Switch label="Disabled" disabled />
          </div>
          <Field
            label="Password (show / hide + strength)"
            help={`${['Too short', 'Weak', 'Fair', 'Good', 'Strong'][passwordStrength(pw)] ?? ''} · at least 10 characters`}
          >
            <PasswordInput value={pw} onChange={(e) => setPw(e.target.value)} />
          </Field>
          <div className="flex flex-col justify-center gap-2">
            {([0, 1, 2, 3, 4] as const).map((s) => (
              <StrengthMeter key={s} score={s} />
            ))}
          </div>
          <div className="col-span-2 flex flex-wrap items-center gap-3">
            <SegmentedControl
              aria-label="Units"
              value={units}
              onValueChange={setUnits}
              options={[
                { value: 'metric', label: 'Metric (m, km/h)' },
                { value: 'imperial', label: 'Imperial (yd, mph)' },
              ]}
            />
            <SegmentedControl
              aria-label="Type (disabled)"
              value="club"
              disabled
              options={[
                { value: 'club', label: 'Club' },
                { value: 'academy', label: 'Academy' },
              ]}
            />
          </div>
        </div>
      </Section>

      <Section title="Tabs">
        <Tabs defaultValue="overview" className="w-full">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="players">Players</TabsTrigger>
            <TabsTrigger value="data" disabled soon="Soon">
              Data
            </TabsTrigger>
          </TabsList>
          <TabsContent value="overview" className="text-fg-2">
            Overview content
          </TabsContent>
          <TabsContent value="players" className="text-fg-2">
            Players content
          </TabsContent>
        </Tabs>
        <Tabs defaultValue="both" variant="segmented">
          <TabsList aria-label="Layout">
            <TabsTrigger value="video">Video</TabsTrigger>
            <TabsTrigger value="map">Map</TabsTrigger>
            <TabsTrigger value="both">Both</TabsTrigger>
          </TabsList>
        </Tabs>
      </Section>

      <Section title="Badges — job / video / shot status">
        {(Object.keys(JOB_STATUS_TONE) as JobStatus[]).map((s) => (
          <Badge key={s} tone={JOB_STATUS_TONE[s]}>
            {s}
          </Badge>
        ))}
        <div className="basis-full" />
        {(Object.keys(VIDEO_STATUS_TONE) as VideoStatus[]).map((s) => (
          <Badge key={s} tone={VIDEO_STATUS_TONE[s]}>
            {s}
          </Badge>
        ))}
        <div className="basis-full" />
        {(Object.keys(SHOT_TYPE_TONE) as ShotType[]).map((s) => (
          <Badge key={s} tone={SHOT_TYPE_TONE[s]}>
            {s}
          </Badge>
        ))}
        <Badge tone="pro" size="sm">
          PRO
        </Badge>
        <Badge tone="err" pulse>
          LIVE
        </Badge>
      </Section>

      <Section title="Progress / spinner / skeleton">
        <div className="flex w-full flex-col gap-3">
          <Progress value={42} label="Analysis progress" />
          <Progress value={68} label="Upload progress" tone="info" size="sm" />
          <Progress value={null} label="Preparing" tone="info" size="xs" />
          <div className="flex items-center gap-3">
            <Spinner />
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-4 w-24" />
            <Skeleton className="size-8 rounded-full" />
          </div>
        </div>
      </Section>

      <Section title="Banners">
        <Banner tone="warn" actions={<Button size="sm">Calibrate</Button>}>
          Low pitch quality in 23% of the match. Tactical map is hidden there.
        </Banner>
        <Banner tone="info">Analysis runs in the background — you can leave this page.</Banner>
        <Banner tone="err">Analysis failed at “Detect teams”.</Banner>
      </Section>

      <Section title="Overlays">
        <Dialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          trigger={<Button>Open dialog</Button>}
          title="Delete match?"
          description="This removes the video and all analysis results."
          size="sm"
          footer={
            <>
              <DialogClose asChild>
                <Button>Cancel</Button>
              </DialogClose>
              <Button
                variant="danger-solid"
                onClick={() => {
                  setDialogOpen(false);
                  toast({ title: 'Match deleted', action: { label: 'Undo', onClick: () => {} } });
                }}
              >
                Delete
              </Button>
            </>
          }
        >
          <p className="m-0 text-fg-2">You can undo this for 10 seconds.</p>
        </Dialog>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <IconButton label="More actions" icon={<Ellipsis size={18} />} variant="secondary" />
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuLabel>France vs Switzerland</DropdownMenuLabel>
            <DropdownMenuItem icon={<Share2 size={16} />}>Share</DropdownMenuItem>
            <DropdownMenuItem icon={<Download size={16} />} shortcut={<Kbd>E</Kbd>}>
              Export
            </DropdownMenuItem>
            <DropdownMenuItem icon={<Upload size={16} />} disabled>
              Re-upload
            </DropdownMenuItem>
            <DropdownMenuSub>
              <DropdownMenuSubTrigger icon={<Video size={16} />}>Move to…</DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                <DropdownMenuItem>Riverside FC</DropdownMenuItem>
                <DropdownMenuItem>City Academy</DropdownMenuItem>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
            <DropdownMenuItem icon={<Trash2 size={16} />} danger>
              Delete
            </DropdownMenuItem>
            <DropdownMenuItem icon={<LogOut size={16} />}>Sign out</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Button onClick={() => toast({ title: 'Settings saved', tone: 'success' })}>
          Success toast
        </Button>
        <Button
          onClick={() =>
            toast({
              title: 'Analysis failed',
              description: 'TEAM_SEPARATION_FAILED',
              tone: 'error',
              action: { label: 'Details', onClick: () => {} },
            })
          }
        >
          Error toast
        </Button>
      </Section>

      <EmptyState
        icon={<Video size={30} />}
        title="Analyze your first match"
        description="Paste a YouTube link to a broadcast match, or upload the recording."
        actions={
          <>
            <Button variant="primary" size="lg" leadingIcon={<YoutubeIcon size={18} />}>
              Paste a YouTube link
            </Button>
            <Button size="lg" leadingIcon={<Upload size={18} />}>
              Upload a file
            </Button>
          </>
        }
      />
    </div>
  );
}

const THEMES: ThemePreference[] = ['system', 'dark', 'light'];

export default function UiGallery() {
  const preference = useThemeStore((s) => s.preference);
  const setPreference = useThemeStore((s) => s.setPreference);
  return (
    <div className="mx-auto max-w-[1600px] px-5 py-6">
      <div className="mb-5 flex items-center gap-3">
        <h1>UI gallery</h1>
        <span className="text-fg-3">dev only · overlays follow the app theme</span>
        <div className="flex-1" />
        <Tabs
          variant="segmented"
          value={preference}
          onValueChange={(v) => setPreference(v as ThemePreference)}
        >
          <TabsList aria-label="App theme">
            {THEMES.map((t) => (
              <TabsTrigger key={t} value={t}>
                {t}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {(['dark', 'light'] as const).map((t) => (
          <div key={t} data-theme={t} className="rounded-3xl bg-bg p-4 text-fg">
            <p className="m-0 mb-3 font-mono text-xs tracking-[.14em] text-accent uppercase">{t}</p>
            <Specimens />
          </div>
        ))}
      </div>
    </div>
  );
}
