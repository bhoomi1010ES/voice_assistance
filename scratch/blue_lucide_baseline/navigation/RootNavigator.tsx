import React from 'react';
import { useAuth } from '../auth/AuthProvider';
import { BootstrapFailureScreen } from '../screens/BootstrapFailureScreen';
import { BootstrapScreen } from '../screens/BootstrapScreen';
import { AuthNavigator } from './AuthNavigator';
import { MainNavigator } from './MainNavigator';

export function RootNavigator() {
  const {
    status,
    restoreError,
    retryRestore,
    sessionExpired,
    clearSessionExpired,
  } = useAuth();

  if (status === 'unknown' && restoreError) {
    return <BootstrapFailureScreen onRetry={retryRestore} />;
  }

  if (status === 'unknown') {
    return <BootstrapScreen />;
  }

  return status === 'authenticated' ? (
    <MainNavigator />
  ) : (
    <AuthNavigator
      sessionExpired={sessionExpired}
      onDismissSessionExpired={clearSessionExpired}
    />
  );
}
