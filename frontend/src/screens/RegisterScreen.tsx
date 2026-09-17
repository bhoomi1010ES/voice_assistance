import React, { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import { ClientError, safeUserMessage, toClientError } from '../api/errors';
import { useAuth } from '../auth/AuthProvider';
import { AuthBrandHeader } from '../components/auth/AuthBrandHeader';
import { AuthCard } from '../components/auth/AuthCard';
import { AuthField } from '../components/auth/AuthField';
import {
  ActionButton,
  AppText,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';
import { spacing, typography } from '../design/tokens';
import { strings } from '../i18n/strings';

const MIN_PASSWORD_LENGTH = 12;

export function RegisterScreen({
  onSignIn,
  onRegistered,
}: {
  onSignIn: () => void;
  onRegistered: (email: string) => void;
}) {
  const { controller } = useAuth();
  const { colors } = useAppTheme();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    const normalizedName = name.trim();
    const normalizedEmail = email.trim();

    if (!normalizedName) {
      setError(strings.auth.invalidName);
      return;
    }
    if (!/^\S+@\S+\.\S+$/.test(normalizedEmail)) {
      setError(strings.auth.invalidEmail);
      return;
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(strings.auth.passwordTooShort);
      return;
    }
    if (password !== confirmPassword) {
      setError(strings.auth.passwordMismatch);
      return;
    }

    setError(null);
    setSubmitting(true);
    try {
      await controller.register(normalizedName, normalizedEmail, password);
      onRegistered(normalizedEmail);
    } catch (cause) {
      setError(registerErrorMessage(toClientError(cause)));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Screen testID="register-screen">
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.container}>
          <AuthBrandHeader
            subtitle={strings.auth.registerBody}
            title={strings.auth.registerTitle}
          />

          <AuthCard>
            {error ? (
              <View style={styles.bannerSpacing}>
                <StatusBanner tone="error">{error}</StatusBanner>
              </View>
            ) : null}

            <AuthField
              accessibilityLabel={strings.auth.name}
              autoCapitalize="words"
              autoComplete="name"
              autoCorrect={false}
              icon="👤"
              label={strings.auth.name}
              onChangeText={setName}
              placeholder={strings.auth.name}
              textContentType="name"
              value={name}
            />

            <AuthField
              accessibilityLabel={strings.auth.email}
              autoCapitalize="none"
              autoComplete="email"
              autoCorrect={false}
              icon="✉"
              keyboardType="email-address"
              label={strings.auth.email}
              onChangeText={setEmail}
              placeholder={strings.auth.email}
              textContentType="emailAddress"
              value={email}
            />

            <AuthField
              accessibilityLabel={strings.auth.password}
              autoCapitalize="none"
              autoComplete="new-password"
              icon="🔒"
              label={strings.auth.password}
              onChangeText={setPassword}
              placeholder={strings.auth.password}
              rightElement={
                <Pressable
                  accessibilityLabel={
                    showPassword
                      ? strings.auth.hidePassword
                      : strings.auth.showPassword
                  }
                  accessibilityRole="button"
                  hitSlop={spacing.xs}
                  onPress={() => setShowPassword(current => !current)}
                  style={styles.visibilityButton}
                >
                  <AppText style={[styles.visibilityText, { color: colors.primary }]}>
                    {showPassword
                      ? strings.auth.hidePassword
                      : strings.auth.showPassword}
                  </AppText>
                </Pressable>
              }
              secureTextEntry={!showPassword}
              textContentType="newPassword"
              value={password}
            />

            <AuthField
              accessibilityLabel={strings.auth.confirmPassword}
              autoCapitalize="none"
              autoComplete="new-password"
              icon="🔒"
              label={strings.auth.confirmPassword}
              onChangeText={setConfirmPassword}
              placeholder={strings.auth.confirmPassword}
              rightElement={
                <Pressable
                  accessibilityLabel={
                    showConfirmPassword
                      ? strings.auth.hidePassword
                      : strings.auth.showPassword
                  }
                  accessibilityRole="button"
                  hitSlop={spacing.xs}
                  onPress={() => setShowConfirmPassword(current => !current)}
                  style={styles.visibilityButton}
                >
                  <AppText style={[styles.visibilityText, { color: colors.primary }]}>
                    {showConfirmPassword
                      ? strings.auth.hidePassword
                      : strings.auth.showPassword}
                  </AppText>
                </Pressable>
              }
              secureTextEntry={!showConfirmPassword}
              textContentType="newPassword"
              value={confirmPassword}
            />

            <ActionButton
              disabled={submitting}
              label={
                submitting
                  ? strings.auth.creatingAccount
                  : strings.auth.createAccount
              }
              onPress={submit}
              style={styles.submit}
            />

            {/* Ceramic divider */}
            <View style={styles.dividerRow}>
              <View
                style={[
                  styles.dividerLine,
                  { backgroundColor: colors.borderSubtle },
                ]}
              />
            </View>

            <View style={styles.accountPrompt}>
              <AppText style={{ color: colors.textMuted }}>
                {strings.auth.alreadyHaveAccount}
              </AppText>
              <Pressable
                accessibilityLabel={strings.auth.signIn}
                accessibilityRole="button"
                hitSlop={spacing.xs}
                onPress={onSignIn}
                testID="sign-in-link"
              >
                <AppText style={[styles.link, { color: colors.primary }]}>
                  {strings.auth.signIn}
                </AppText>
              </Pressable>
            </View>
          </AuthCard>
        </View>
      </ScrollView>
    </Screen>
  );
}

function registerErrorMessage(error: ClientError): string {
  if (error.status === 409 || error.code === 'ACCOUNT_ALREADY_EXISTS') {
    return 'An account with this email already exists.';
  }
  if (error.status === 422) {
    return 'Check your details and try again.';
  }
  if (error.status === 503) {
    return 'The sign-up service is unavailable. Please try again later.';
  }
  return safeUserMessage(error);
}

const styles = StyleSheet.create({
  scrollContent: {
    flexGrow: 1,
    justifyContent: 'center',
    paddingVertical: spacing.lg,
  },
  container: {
    alignSelf: 'center',
    maxWidth: 440,
    paddingHorizontal: spacing.md,
    width: '100%',
  },
  bannerSpacing: {
    marginBottom: spacing.md,
  },
  visibilityButton: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 48,
    minWidth: 48,
    paddingHorizontal: spacing.xs,
  },
  visibilityText: {
    fontSize: typography.caption,
    fontWeight: '600',
  },
  submit: {
    marginTop: spacing.sm,
  },
  dividerRow: {
    alignItems: 'center',
    marginVertical: spacing.md,
  },
  dividerLine: {
    height: 1,
    width: '100%',
  },
  accountPrompt: {
    alignItems: 'center',
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    minHeight: 44,
  },
  link: {
    fontWeight: '700',
    marginLeft: spacing.xs,
  },
});
