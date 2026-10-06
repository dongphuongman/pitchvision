import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { setupIdentityMock } from '@/test/msw';
import { renderApp, testSession } from '@/test/renderApp';

const mock = setupIdentityMock();

function videoFile(name: string, bytes = 1024): File {
  return new File([new Uint8Array(bytes)], name, { type: 'video/mp4', lastModified: 7 });
}

describe('SCR-11 New analysis', () => {
  beforeEach(() => localStorage.clear());

  it('YouTube: fetches a preview, requires rights, then creates the video + job and opens processing', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/library?new=youtube', { auth: testSession() });
    const input = await screen.findByLabelText('YouTube URL');
    await user.type(input, 'https://www.youtube.com/watch?v=BukNW8GRI5A');
    await user.click(screen.getByRole('button', { name: 'Fetch' }));
    expect(await screen.findByTestId('yt-preview')).toHaveTextContent(
      'Columbus Crew vs. Aston Villa',
    );
    expect(screen.getByTestId('yt-preview')).toHaveTextContent('1080p');
    const analyze = screen.getByRole('button', { name: 'Analyze' });
    expect(analyze).toBeDisabled();
    await user.click(
      screen.getByRole('checkbox', { name: 'I have the rights to analyze this video' }),
    );
    await user.click(analyze);
    await waitFor(() =>
      expect(router.state.location.pathname).toMatch(/^\/videos\/[^/]+\/processing$/),
    );
    expect(mock.videos.log.filter((l) => /from-url |POST jobs/.test(l))).toHaveLength(2);
  });

  it('YouTube: rejects non-YouTube links before calling the API and shows server rejections inline', async () => {
    const user = userEvent.setup();
    renderApp('/library?new=youtube', { auth: testSession() });
    const input = await screen.findByLabelText('YouTube URL');
    await user.type(input, 'https://vimeo.com/1{Enter}');
    expect(await screen.findByText('Paste a youtube.com or youtu.be link.')).toBeInTheDocument();
    expect(mock.videos.log.some((l) => l.startsWith('POST preview'))).toBe(false);

    await user.clear(input);
    await user.type(input, 'https://youtu.be/private{Enter}');
    expect(await screen.findByText(/This video is private\./)).toBeInTheDocument();
  });

  it('Upload: validates the format client-side', async () => {
    const user = userEvent.setup({ applyAccept: false });
    renderApp('/library?new=upload', { auth: testSession() });
    await user.upload(await screen.findByTestId('file-input'), videoFile('notes.txt'));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "notes.txt isn't a supported video format.",
    );
    expect(screen.getByRole('button', { name: 'Start upload' })).toBeDisabled();
  });

  it('Upload: sends parts to storage, completes and starts analysis', async () => {
    const user = userEvent.setup();
    const { router } = renderApp('/library?new=upload', { auth: testSession() });
    await user.upload(await screen.findByTestId('file-input'), videoFile('france_switzerland.mp4'));
    expect(await screen.findByDisplayValue('france switzerland')).toBeInTheDocument(); // E08 from file name
    await user.click(
      screen.getByRole('checkbox', { name: 'I own or have permission to use this video' }),
    );
    await user.click(screen.getByRole('button', { name: 'Start upload' }));
    await waitFor(() => expect(router.state.location.pathname).toMatch(/\/processing$/));
    const log = mock.videos.log.join('\n');
    expect(log).toMatch(/POST uploads/);
    expect(log).toMatch(/PUT part 1/);
    expect(log).toMatch(/POST complete/);
    expect(log).toMatch(/POST jobs/);
  });

  it('Upload: with "start analysis" off the video is only stored', async () => {
    const user = userEvent.setup();
    renderApp('/library?new=upload', { auth: testSession() });
    await user.upload(await screen.findByTestId('file-input'), videoFile('training.mov'));
    await user.click(
      screen.getByRole('checkbox', { name: 'I own or have permission to use this video' }),
    );
    await user.click(screen.getByRole('checkbox', { name: 'Start analysis when upload finishes' }));
    await user.click(screen.getByRole('button', { name: 'Start upload' }));
    await waitFor(() =>
      expect(mock.videos.log.some((l) => l.startsWith('POST complete'))).toBe(true),
    );
    expect(mock.videos.log.some((l) => l.startsWith('POST jobs'))).toBe(false);
  });

  it('SCR-12: Fast mode + confidence are sent as the job config', async () => {
    const user = userEvent.setup();
    renderApp('/library?new=youtube', { auth: testSession() });
    await user.type(
      await screen.findByLabelText('YouTube URL'),
      'https://youtu.be/BukNW8GRI5A{Enter}',
    );
    await screen.findByTestId('yt-preview');
    expect(screen.getByText(/Standard mode · confidence 0.35/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Advanced settings' }));
    await user.click(await screen.findByRole('radio', { name: 'Fast' }));
    fireEvent.change(screen.getByRole('slider'), { target: { value: '0.5' } });
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    expect(await screen.findByText(/Fast mode · confidence 0.50/)).toBeInTheDocument();
    await user.click(
      screen.getByRole('checkbox', { name: 'I have the rights to analyze this video' }),
    );
    await user.click(screen.getByRole('button', { name: 'Analyze' }));
    await waitFor(() =>
      expect(mock.videos.log.find((l) => l.startsWith('POST jobs'))).toContain(
        'config={"frame_stride":2,"conf_threshold":0.5}',
      ),
    );
  });

  it('G.B07: the upload tray shows a background upload', async () => {
    const user = userEvent.setup();
    renderApp('/library?new=upload', { auth: testSession() });
    await user.upload(await screen.findByTestId('file-input'), videoFile('bg.mp4'));
    await user.click(
      screen.getByRole('checkbox', { name: 'I own or have permission to use this video' }),
    );
    await user.click(screen.getByRole('checkbox', { name: 'Start analysis when upload finishes' }));
    await user.click(screen.getByRole('button', { name: 'Start upload' }));
    const tray = await screen.findByRole('complementary', { name: 'Uploads' });
    expect(await within(tray).findByText('Uploaded')).toBeInTheDocument();
    expect(within(tray).getByText('bg.mp4')).toBeInTheDocument();
  });
});
