import React, { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { toClientError } from '../api/errors';
import {
  ActionButton,
  AppText,
  Card,
  Heading,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';
import { spacing, typography } from '../design/tokens';
import { strings } from '../i18n/strings';
import { useAuth } from '../auth/AuthProvider';

export function AccountScreen() {
  const { controller, profile } = useAuth();
  const { colors } = useAppTheme();
  const [name, setName] = useState('');
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    setName(profile?.name ?? '');
  }, [profile?.id, profile?.name]);

  const startEditing = () => {
    setName(profile?.name ?? '');
    setError(null);
    setMessage(null);
    setEditing(true);
  };

  const cancelEditing = () => {
    setName(profile?.name ?? '');
    setError(null);
    setEditing(false);
  };

  const saveProfile = async () => {
    const normalizedName = name.trim();
    if (!normalizedName) {
      setError(strings.account.invalidName);
      return;
    }

    setError(null);
    setSaving(true);
    try {
      await controller.updateProfile(normalizedName);
      setEditing(false);
      setMessage(strings.account.updated);
    } catch (cause) {
      const clientError = toClientError(cause);
      setError(
        clientError.status === 422
          ? strings.account.invalidName
          : strings.account.updateError,
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Screen testID="profile-screen">
      <ScrollView contentContainerStyle={styles.content}>
        <Heading>{strings.account.title}</Heading>
        {message ? <StatusBanner>{message}</StatusBanner> : null}
        {error ? <StatusBanner tone="error">{error}</StatusBanner> : null}
        <Card style={styles.card}>
          <AppText style={styles.label}>{strings.account.name}</AppText>
          {editing ? (
            <TextInput
              accessibilityLabel={strings.account.name}
              autoCapitalize="words"
              autoCorrect={false}
              onChangeText={setName}
              placeholder={strings.account.namePlaceholder}
              placeholderTextColor={colors.textMuted}
              style={[
                styles.input,
                { color: colors.text, borderColor: colors.border },
              ]}
              testID="profile-name-input"
              textContentType="name"
              value={name}
            />
          ) : (
            <AppText>{profile?.name || strings.account.notSet}</AppText>
          )}
          <AppText style={styles.label}>{strings.account.email}</AppText>
          <AppText>{profile?.email ?? strings.account.loading}</AppText>
          <AppText style={styles.label}>{strings.account.status}</AppText>
          <AppText>{profile?.status ?? strings.account.loading}</AppText>
          {editing ? (
            <View style={styles.actions}>
              <ActionButton
                disabled={saving}
                label={saving ? strings.account.saving : strings.account.save}
                onPress={saveProfile}
                style={styles.action}
                testID="profile-save"
              />
              <ActionButton
                disabled={saving}
                label={strings.account.cancel}
                onPress={cancelEditing}
                style={styles.action}
                testID="profile-cancel"
                variant="secondary"
              />
            </View>
          ) : (
            <ActionButton
              disabled={!profile}
              label={strings.account.edit}
              onPress={startEditing}
              style={styles.editButton}
              variant="secondary"
              testID="profile-edit"
            />
          )}
        </Card>
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: { paddingBottom: spacing.lg },
  card: { marginTop: spacing.lg },
  label: { fontWeight: '700', marginTop: spacing.md },
  input: {
    borderRadius: 8,
    borderWidth: 1,
    fontSize: typography.body,
    minHeight: 48,
    paddingHorizontal: spacing.md,
  },
  actions: { gap: spacing.sm, marginTop: spacing.lg },
  action: { flex: 1 },
  editButton: { marginTop: spacing.lg },
});
