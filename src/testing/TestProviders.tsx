import React from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ThemeMode } from '../design/tokens';
import { ThemeProvider } from '../design/ThemeProvider';

export function TestProviders({
  children,
  forcedMode = 'light',
}: {
  children: React.ReactNode;
  forcedMode?: ThemeMode;
}) {
  return (
    <SafeAreaProvider>
      <ThemeProvider forcedMode={forcedMode}>{children}</ThemeProvider>
    </SafeAreaProvider>
  );
}
