import React from 'react';
import { StyleSheet } from 'react-native';
import { AppText, Card, Heading, Screen } from '../components/ui/Primitives';
import { spacing } from '../design/tokens';
import { strings } from '../i18n/strings';
import { useAuth } from '../auth/AuthProvider';

export function AccountScreen() {
  const { profile } = useAuth();

  return (
    <Screen testID="account-screen">
      <Heading>{strings.account.title}</Heading>
      <Card style={styles.card}>
        <AppText style={styles.label}>{strings.account.email}</AppText>
        <AppText>{profile?.email ?? strings.account.loading}</AppText>
        <AppText style={styles.label}>{strings.account.status}</AppText>
        <AppText>{profile?.status ?? strings.account.loading}</AppText>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  card: { marginTop: spacing.lg },
  label: { fontWeight: '700', marginTop: spacing.md },
});
