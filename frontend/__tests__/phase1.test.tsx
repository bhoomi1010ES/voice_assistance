import React from 'react';
import ReactTestRenderer, {
  ReactTestRenderer as Renderer,
} from 'react-test-renderer';
import App from '../App';
import { AppErrorBoundary } from '../src/components/ErrorBoundary';
import { TestProviders } from '../src/testing/TestProviders';
import { ThemeProbe } from '../src/testing/ThemeProbe';

async function renderApp(bootstrap: () => Promise<{ id: string } | null>) {
  let renderer: Renderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<App bootstrap={bootstrap} />);
    await Promise.resolve();
  });
  return renderer!;
}

test('bootstrap does not render a protected screen before it resolves', async () => {
  let resolveBootstrap: (session: null) => void = () => undefined;
  const bootstrap = () =>
    new Promise<null>(resolve => {
      resolveBootstrap = resolve;
    });

  let renderer: Renderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(<App bootstrap={bootstrap} />);
  });

  expect(
    renderer!.root.findByProps({ testID: 'bootstrap-screen' }),
  ).toBeTruthy();
  expect(
    renderer!.root.findAllByProps({ testID: 'assistant-screen' }),
  ).toHaveLength(0);

  await ReactTestRenderer.act(async () => {
    resolveBootstrap(null);
    await Promise.resolve();
  });

  expect(renderer!.root.findByProps({ testID: 'auth-screen' })).toBeTruthy();
});

test('bootstrap failure exposes safe retry UI without the raw error', async () => {
  const bootstrap = jest
    .fn<Promise<null>, []>()
    .mockRejectedValueOnce(
      new Error('secret provider token should never render'),
    );
  const renderer = await renderApp(bootstrap);

  expect(
    renderer.root.findByProps({ testID: 'bootstrap-failure-screen' }),
  ).toBeTruthy();
  expect(JSON.stringify(renderer.toJSON())).not.toContain(
    'secret provider token',
  );
});

test('theme providers expose readable light and dark token sets', async () => {
  let light: Renderer;
  let dark: Renderer;
  await ReactTestRenderer.act(async () => {
    light = ReactTestRenderer.create(
      <TestProviders forcedMode="light">
        <ThemeProbe />
      </TestProviders>,
    );
    dark = ReactTestRenderer.create(
      <TestProviders forcedMode="dark">
        <ThemeProbe />
      </TestProviders>,
    );
  });

  expect(
    light!.root.findByProps({ testID: 'theme-probe' }).props.accessibilityLabel,
  ).toBe('light');
  expect(
    dark!.root.findByProps({ testID: 'theme-probe' }).props.accessibilityLabel,
  ).toBe('dark');
});

test('render failures show a usable sanitized fallback', async () => {
  const ThrowingChild = () => {
    throw new Error('private stack detail');
  };
  let renderer: Renderer;
  await ReactTestRenderer.act(async () => {
    renderer = ReactTestRenderer.create(
      <TestProviders>
        <AppErrorBoundary>
          <ThrowingChild />
        </AppErrorBoundary>
      </TestProviders>,
    );
  });

  expect(
    renderer!.root.findByProps({ testID: 'error-boundary-screen' }),
  ).toBeTruthy();
  expect(JSON.stringify(renderer!.toJSON())).not.toContain(
    'private stack detail',
  );
});
