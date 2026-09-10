import { StatusBar } from 'react-native';
import { AppProviders } from './src/app/AppProviders';
import { BootstrapDependency } from './src/app/bootstrap';
import { AuthController } from './src/auth/AuthController';
import { AppErrorBoundary } from './src/components/ErrorBoundary';
import { useAppTheme } from './src/design/ThemeProvider';
import { RootNavigator } from './src/navigation/RootNavigator';

export type AppProps = {
  bootstrap?: BootstrapDependency;
  authController?: AuthController;
};

function AppContent() {
  const { mode } = useAppTheme();

  return (
    <>
      <StatusBar
        barStyle={mode === 'dark' ? 'light-content' : 'dark-content'}
      />
      <AppErrorBoundary>
        <RootNavigator />
      </AppErrorBoundary>
    </>
  );
}

function App({ bootstrap, authController }: AppProps) {
  return (
    <AppProviders authController={authController} bootstrap={bootstrap}>
      <AppContent />
    </AppProviders>
  );
}

export default App;
