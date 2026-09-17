import React from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { strings } from '../i18n/strings';
import { AppText, Heading, Screen } from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';
import { spacing, typography } from '../theme';
import { useAuth } from '../auth/AuthProvider';

import { SettingsSection } from '../components/settings/SettingsSection';
import { SettingsRow } from '../components/settings/SettingsRow';
import { ProfileSummaryCard } from '../components/settings/ProfileSummaryCard';

interface SettingsScreenProps {
  onOpenDiagnostics: () => void;
  onOpenAccount: () => void;
  onOpenSessions: () => void;
  onOpenMemory: () => void;
  onSignOut?: () => void;
}

export function SettingsScreen({
  onOpenDiagnostics,
  onOpenAccount,
  onOpenSessions,
  onOpenMemory,
  onSignOut,
}: SettingsScreenProps) {
  const { colors } = useAppTheme();
  const { profile } = useAuth();

  return (
    <Screen testID="settings-screen">
      <ScrollView contentContainerStyle={styles.content}>
        {/* Stitch-inspired Hero Header */}
        <View style={styles.header}>
          <AppText style={[styles.overline, { color: colors.primary }]}>
            PREFERENCES & ENGINE
          </AppText>
          <Heading>{strings.settings.title}</Heading>
          <AppText style={[styles.subtitle, { color: colors.textMuted }]}>
            Configure account, active sessions, and personal memory context.
          </AppText>
        </View>

        {/* Profile Card */}
        <ProfileSummaryCard
          email={profile?.email}
          name={profile?.name || 'Voice Assistant User'}
          onPress={onOpenAccount}
          status={profile?.status}
        />

        {/* Account & Devices Section */}
        <SettingsSection title="Account & Devices">
          <SettingsRow
            icon="👤"
            onPress={onOpenAccount}
            showDivider
            subtitle="Manage name and account details"
            testID="settings-account"
            title={strings.settings.account}
          />
          <SettingsRow
            icon="📱"
            onPress={onOpenSessions}
            subtitle="Active logins and registered devices"
            testID="settings-sessions"
            title={strings.settings.sessions}
          />
        </SettingsSection>

        {/* Knowledge & Memory Section */}
        <SettingsSection title="Knowledge & Intelligence">
          <SettingsRow
            icon="🧠"
            onPress={onOpenMemory}
            subtitle="Personal context learned from conversations"
            testID="settings-memory"
            title={strings.main.memory}
          />
        </SettingsSection>

        {/* Preferences Section */}
        <SettingsSection title="Preferences">
          <SettingsRow
            icon="🎨"
            subtitle={strings.settings.theme}
            title="Appearance"
          />
        </SettingsSection>

        {/* Developer Section (__DEV__ only) */}
        {__DEV__ ? (
          <SettingsSection title="Developer Diagnostics">
            <SettingsRow
              icon="🛠"
              onPress={onOpenDiagnostics}
              subtitle="Audio engine, VAD, and latency tracing"
              testID="settings-diagnostics"
              title={strings.settings.diagnostics}
            />
          </SettingsSection>
        ) : null}

        {/* Session Sign Out */}
        {onSignOut ? (
          <SettingsSection title="Session">
            <SettingsRow
              destructive
              icon="🚪"
              onPress={onSignOut}
              subtitle="Safely disconnect this device from your account"
              testID="settings-sign-out"
              title={strings.main.signOut}
            />
          </SettingsSection>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: {
    gap: spacing.md,
    paddingBottom: spacing.xxl,
  },
  header: {
    gap: 2,
    marginBottom: spacing.xs,
  },
  overline: {
    fontSize: typography.caption,
    fontWeight: '700',
    letterSpacing: 1,
  },
  subtitle: {
    fontSize: typography.caption,
    lineHeight: 18,
    marginTop: 2,
  },
});
