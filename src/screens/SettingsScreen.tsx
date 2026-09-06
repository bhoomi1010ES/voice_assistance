import React from 'react';
import { StyleSheet } from 'react-native';
import { strings } from '../i18n/strings';
import {
  ActionButton,
  AppText,
  Card,
  Heading,
  Screen,
} from '../components/ui/Primitives';

export function SettingsScreen({
  onOpenDiagnostics,
  onOpenAccount,
  onOpenSessions,
}: {
  onOpenDiagnostics: () => void;
  onOpenAccount: () => void;
  onOpenSessions: () => void;
}) {
  return (
    <Screen testID="settings-screen">
      <Heading>{strings.settings.title}</Heading>
      <Card style={styles.card}>
        <AppText>{strings.settings.theme}</AppText>
      </Card>
      <ActionButton
        label={strings.settings.account}
        onPress={onOpenAccount}
        style={styles.card}
        variant="secondary"
      />
      <ActionButton
        label={strings.settings.sessions}
        onPress={onOpenSessions}
        style={styles.card}
        variant="secondary"
      />
      {__DEV__ ? (
        <ActionButton
          label={strings.settings.diagnostics}
          onPress={onOpenDiagnostics}
          variant="secondary"
          style={styles.card}
        />
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  card: { marginTop: 16 },
});
