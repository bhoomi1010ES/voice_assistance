import React from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeProvider } from '../design/ThemeProvider';
import { AuthController } from '../auth/AuthController';
import { AuthProvider } from '../auth/AuthProvider';
import { BootstrapDependency } from './bootstrap';
import { VoiceSocketProvider } from '../voice/VoiceSocketProvider';

export function AppProviders({
  children,
  authController,
  bootstrap,
}: {
  children: React.ReactNode;
  authController?: AuthController;
  bootstrap?: BootstrapDependency;
}) {
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <AuthProvider controller={authController} bootstrap={bootstrap}>
          <VoiceSocketProvider>{children}</VoiceSocketProvider>
        </AuthProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}
