import React from 'react';
import { LoginScreen } from '../screens/LoginScreen';

export function AuthNavigator({
  sessionExpired,
  onDismissSessionExpired,
}: {
  sessionExpired: boolean;
  onDismissSessionExpired: () => void;
}) {
  return (
    <LoginScreen
      sessionExpired={sessionExpired}
      onDismissSessionExpired={onDismissSessionExpired}
    />
  );
}
