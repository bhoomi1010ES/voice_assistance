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
import { radii, shadows, spacing, typography } from '../theme';
import { strings } from '../../src/i18n/strings';
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

  const initials = (profile?.name || 'User')
    .split(' ')
    .map(p => p.charAt(0))
    .slice(0, 2)
    .join('')
    .toUpperCase();

  return (
    <Screen testID="profile-screen">
      <ScrollView contentContainerStyle={styles.content}>
        {/* Stitch-inspired Hero Header */}
        <View style={styles.header}>
          <AppText style={[styles.overline, { color: colors.primary }]}>
            ACCOUNT PROFILE
          </AppText>
          <Heading>{strings.account.title}</Heading>
        </View>

        {message ? <StatusBanner>{message}</StatusBanner> : null}
        {error ? <StatusBanner tone="error">{error}</StatusBanner> : null}

        {/* Hero Avatar & Identity Card */}
        <Card
          style={[
            styles.heroCard,
            {
              backgroundColor: colors.surface,
              borderColor: colors.border,
            },
          ]}
        >
          <View
            style={[
              styles.avatarLarge,
              {
                backgroundColor: colors.primaryContainer,
                borderColor: colors.border,
              },
            ]}
          >
            <AppText
              style={[
                styles.avatarText,
                { color: colors.onPrimaryContainer },
              ]}
            >
              {initials}
            </AppText>
          </View>
          <View style={styles.heroText}>
            <AppText style={[styles.heroName, { color: colors.text }]}>
              {profile?.name || strings.account.notSet}
            </AppText>
            <AppText style={[styles.heroEmail, { color: colors.textMuted }]}>
              {profile?.email ?? strings.account.loading}
            </AppText>
            {profile?.status ? (
              <View
                style={[
                  styles.statusBadge,
                  { backgroundColor: colors.successContainer },
                ]}
              >
                <AppText
                  style={[styles.statusText, { color: colors.success }]}
                >
                  {profile.status}
                </AppText>
              </View>
            ) : null}
          </View>
        </Card>

        {/* Profile Details & Inline Editor */}
        <Card
          style={[
            styles.card,
            {
              backgroundColor: colors.surface,
              borderColor: colors.border,
            },
          ]}
        >
          <View style={styles.fieldGroup}>
            <AppText style={[styles.label, { color: colors.text }]}>
              {strings.account.name}
            </AppText>
            {editing ? (
              <View style={styles.editContainer}>
                <TextInput
                  accessibilityLabel={strings.account.name}
                  autoCapitalize="words"
                  autoCorrect={false}
                  onChangeText={setName}
                  placeholder={strings.account.namePlaceholder}
                  placeholderTextColor={colors.textSubtle}
                  style={[
                    styles.input,
                    {
                      color: colors.text,
                      borderColor: colors.border,
                      backgroundColor: colors.surfaceLow,
                    },
                  ]}
                  testID="profile-name-input"
                  textContentType="name"
                  value={name}
                />
                <View style={styles.actions}>
                  <ActionButton
                    disabled={saving}
                    label={
                      saving ? strings.account.saving : strings.account.save
                    }
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
              </View>
            ) : (
              <View style={styles.viewRow}>
                <AppText style={[styles.value, { color: colors.text }]}>
                  {profile?.name || strings.account.notSet}
                </AppText>
                <ActionButton
                  disabled={!profile}
                  label={strings.account.edit}
                  onPress={startEditing}
                  testID="profile-edit"
                  variant="secondary"
                />
              </View>
            )}
          </View>

          <View
            style={[styles.divider, { backgroundColor: colors.borderSubtle }]}
          />

          <View style={styles.fieldGroup}>
            <AppText style={[styles.label, { color: colors.text }]}>
              {strings.account.email}
            </AppText>
            <AppText style={[styles.value, { color: colors.textMuted }]}>
              {profile?.email ?? strings.account.loading}
            </AppText>
          </View>

          <View
            style={[styles.divider, { backgroundColor: colors.borderSubtle }]}
          />

          <View style={styles.fieldGroup}>
            <AppText style={[styles.label, { color: colors.text }]}>
              {strings.account.status}
            </AppText>
            <AppText
              style={[
                styles.value,
                { color: colors.text, textTransform: 'capitalize' },
              ]}
            >
              {profile?.status ?? strings.account.loading}
            </AppText>
          </View>
        </Card>
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
  heroCard: {
    alignItems: 'center',
    borderRadius: radii.md,
    borderWidth: 1,
    flexDirection: 'row',
    gap: spacing.md,
    padding: spacing.lg,
    ...shadows.sm,
  },
  avatarLarge: {
    alignItems: 'center',
    borderRadius: radii.full,
    borderWidth: 2,
    height: 60,
    justifyContent: 'center',
    width: 60,
  },
  avatarText: {
    fontSize: typography.heading,
    fontWeight: '700',
    letterSpacing: 0.5,
  },
  heroText: {
    flex: 1,
    gap: 3,
  },
  heroName: {
    fontSize: typography.heading,
    fontWeight: '700',
    letterSpacing: -0.3,
  },
  heroEmail: {
    fontSize: typography.caption,
  },
  statusBadge: {
    alignSelf: 'flex-start',
    borderRadius: radii.pill,
    marginTop: 2,
    paddingHorizontal: spacing.sm,
    paddingVertical: 1,
  },
  statusText: {
    fontSize: typography.caption,
    fontWeight: '600',
    textTransform: 'capitalize',
  },
  card: {
    borderRadius: radii.md,
    borderWidth: 1,
    gap: spacing.md,
    padding: spacing.lg,
    ...shadows.sm,
  },
  fieldGroup: {
    gap: spacing.xs,
  },
  label: {
    fontSize: typography.caption,
    fontWeight: '700',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  value: {
    fontSize: typography.body,
    fontWeight: '500',
  },
  viewRow: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: 2,
  },
  editContainer: {
    gap: spacing.sm,
    marginTop: 4,
  },
  input: {
    borderRadius: radii.sm,
    borderWidth: 1,
    fontSize: typography.body,
    minHeight: 48,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  actions: {
    flexDirection: 'row',
    gap: spacing.sm,
  },
  action: {
    flex: 1,
  },
  divider: {
    height: 1,
  },
});
