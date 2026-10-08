import React from 'react';
import { strings } from '../i18n/strings';
import { useAppTheme } from '../design/ThemeProvider';
import { useAuth } from '../auth/AuthProvider';
import { useOptionalVoiceSocket } from '../voice/VoiceSocketProvider';
import { SettingsSection } from '../components/settings/SettingsSection';
import { SettingsRow } from '../components/settings/SettingsRow';
import { ProfileSummaryCard } from '../components/settings/ProfileSummaryCard';
import {
  SettingsCanvas,
  SettingsHero,
  SettingsIcon,
} from '../components/settings/SettingsPresentation';

interface SettingsScreenProps {
  onOpenDiagnostics: () => void;
  onOpenConnectionDiagnostics: () => void;
  onOpenAccount: () => void;
  onOpenSessions: () => void;
  onSignOut?: () => void;
}

export function SettingsScreen({
  onOpenDiagnostics,
  onOpenConnectionDiagnostics,
  onOpenAccount,
  onOpenSessions,
  onSignOut,
}: SettingsScreenProps) {
  const { colors, mode } = useAppTheme();
  const { profile } = useAuth();
  const voiceSocket = useOptionalVoiceSocket();

  return (
    <SettingsCanvas testID="settings-screen">
      <SettingsHero
        title={strings.settings.title}
        subtitle="Manage account, devices, and diagnostics."
      />
      <ProfileSummaryCard
        email={profile?.email}
        name={profile?.name || 'Voice Assistant User'}
        onPress={onOpenAccount}
        status={profile?.status}
        testID="settings-account"
      />
      <SettingsSection title="Account & devices">
        <SettingsRow
          icon={<SettingsIcon kind="device" />}
          onPress={onOpenSessions}
          subtitle="Active logins and registered devices"
          testID="settings-sessions"
          title={strings.settings.sessions}
        />
      </SettingsSection>
      {__DEV__ || voiceSocket ? (
        <SettingsSection title="Developer diagnostics">
          {__DEV__ ? (
            <SettingsRow
              icon={
                <SettingsIcon
                  kind="diagnostics"
                  color={mode === 'light' ? '#0A84FF' : colors.primary}
                />
              }
              iconTone="blue"
              onPress={onOpenDiagnostics}
              showDivider={Boolean(voiceSocket)}
              subtitle="Audio engine, VAD, and latency tracing"
              testID="settings-diagnostics"
              title={strings.settings.diagnostics}
            />
          ) : null}
          {voiceSocket ? (
            <SettingsRow
              icon={<SettingsIcon kind="connection" />}
              onPress={onOpenConnectionDiagnostics}
              subtitle="Connection, session events, and message timing"
              testID="settings-connection-diagnostics"
              title="Connection diagnostics"
            />
          ) : null}
        </SettingsSection>
      ) : null}
      {onSignOut ? (
        <SettingsSection title="Session">
          <SettingsRow
            destructive
            icon={<SettingsIcon kind="sign-out" color={colors.error} />}
            onPress={onSignOut}
            subtitle="Safely disconnect this device from your account"
            testID="settings-sign-out"
            title={strings.main.signOut}
          />
        </SettingsSection>
      ) : null}
    </SettingsCanvas>
  );
}
