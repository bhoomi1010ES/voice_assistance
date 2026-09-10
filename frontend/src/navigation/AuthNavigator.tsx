import React, { useState } from 'react';
import { strings } from '../i18n/strings';
import { LoginScreen } from '../screens/LoginScreen';
import { RegisterScreen } from '../screens/RegisterScreen';

export function AuthNavigator({
  sessionExpired,
  onDismissSessionExpired,
}: {
  sessionExpired: boolean;
  onDismissSessionExpired: () => void;
}) {
  const [screen, setScreen] = useState<'login' | 'register'>('login');
  const [registeredEmail, setRegisteredEmail] = useState('');
  const [registrationMessage, setRegistrationMessage] = useState<string | null>(
    null,
  );

  if (screen === 'register') {
    return (
      <RegisterScreen
        onRegistered={email => {
          setRegisteredEmail(email);
          setRegistrationMessage(strings.auth.accountCreated);
          setScreen('login');
        }}
        onSignIn={() => {
          setRegistrationMessage(null);
          setScreen('login');
        }}
      />
    );
  }

  return (
    <LoginScreen
      initialEmail={registeredEmail}
      onCreateAccount={() => {
        setRegistrationMessage(null);
        setScreen('register');
      }}
      onDismissSessionExpired={onDismissSessionExpired}
      registrationMessage={registrationMessage}
      sessionExpired={sessionExpired}
    />
  );
}
