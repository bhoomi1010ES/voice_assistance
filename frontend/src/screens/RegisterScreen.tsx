import React, { useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  TextInput,
  View,
} from 'react-native';
import { ClientError, safeUserMessage, toClientError } from '../api/errors';
import { useAuth } from '../auth/AuthProvider';
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
      >
        <View style={styles.content}>
          <Heading>{strings.auth.registerTitle}</Heading>
          <AppText style={styles.body}>{strings.auth.registerBody}</AppText>
          <Card>
            {error ? <StatusBanner tone="error">{error}</StatusBanner> : null}
            <AppText style={styles.label}>{strings.auth.name}</AppText>
            <TextInput
              accessibilityLabel={strings.auth.name}
              autoCapitalize="words"
              autoComplete="name"
              autoCorrect={false}
              onChangeText={setName}
              placeholder={strings.auth.name}
              placeholderTextColor={colors.textMuted}
              style={[
                styles.input,
                { color: colors.text, borderColor: colors.border },
              ]}
              textContentType="name"
              value={name}
            />
            <AppText style={styles.label}>{strings.auth.email}</AppText>
            <TextInput
              accessibilityLabel={strings.auth.email}
              autoCapitalize="none"
              autoComplete="email"
              autoCorrect={false}
              keyboardType="email-address"
              onChangeText={setEmail}
              placeholder={strings.auth.email}
              placeholderTextColor={colors.textMuted}
              style={[
                styles.input,
                { color: colors.text, borderColor: colors.border },
              ]}
              textContentType="emailAddress"
              value={email}
            />
            <AppText style={styles.label}>{strings.auth.password}</AppText>
            <View style={styles.passwordRow}>
              <TextInput
                accessibilityLabel={strings.auth.password}
                autoCapitalize="none"
                autoComplete="new-password"
                onChangeText={setPassword}
                placeholder={strings.auth.password}
                placeholderTextColor={colors.textMuted}
                secureTextEntry={!showPassword}
                style={[
                  styles.input,
                  styles.passwordInput,
                  { color: colors.text, borderColor: colors.border },
                ]}
                textContentType="newPassword"
                value={password}
              />
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={
                  showPassword
                    ? strings.auth.hidePassword
                    : strings.auth.showPassword
                }
                onPress={() => setShowPassword(current => !current)}
                style={styles.visibilityButton}
              >
                <AppText>
                  {showPassword
                    ? strings.auth.hidePassword
                    : strings.auth.showPassword}
                </AppText>
              </Pressable>
            </View>
            <AppText style={styles.label}>
              {strings.auth.confirmPassword}
            </AppText>
            <View style={styles.passwordRow}>
              <TextInput
                accessibilityLabel={strings.auth.confirmPassword}
                autoCapitalize="none"
                autoComplete="new-password"
                onChangeText={setConfirmPassword}
                placeholder={strings.auth.confirmPassword}
                placeholderTextColor={colors.textMuted}
                secureTextEntry={!showConfirmPassword}
                style={[
                  styles.input,
                  styles.passwordInput,
                  { color: colors.text, borderColor: colors.border },
                ]}
                textContentType="newPassword"
                value={confirmPassword}
              />
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={
                  showConfirmPassword
                    ? strings.auth.hidePassword
                    : strings.auth.showPassword
                }
                onPress={() => setShowConfirmPassword(current => !current)}
                style={styles.visibilityButton}
              >
                <AppText>
                  {showConfirmPassword
                    ? strings.auth.hidePassword
                    : strings.auth.showPassword}
                </AppText>
              </Pressable>
            </View>
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
            <View style={styles.accountPrompt}>
              <AppText>{strings.auth.alreadyHaveAccount}</AppText>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={strings.auth.signIn}
                onPress={onSignIn}
                testID="sign-in-link"
              >
                <AppText style={[styles.link, { color: colors.accent }]}>
                  {strings.auth.signIn}
                </AppText>
              </Pressable>
            </View>
          </Card>
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
  scrollContent: { flexGrow: 1, justifyContent: 'center' },
  content: { paddingVertical: spacing.md },
  body: { marginBottom: spacing.lg, marginTop: spacing.sm },
  label: { marginBottom: spacing.xs, marginTop: spacing.md },
  input: {
    borderRadius: 8,
    borderWidth: 1,
    fontSize: typography.body,
    minHeight: 48,
    paddingHorizontal: spacing.md,
  },
  passwordRow: { alignItems: 'center', flexDirection: 'row' },
  passwordInput: { flex: 1 },
  visibilityButton: { marginLeft: spacing.sm, maxWidth: 92 },
  submit: { marginTop: spacing.lg },
  accountPrompt: {
    alignItems: 'center',
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
    marginTop: spacing.md,
  },
  link: { fontWeight: '700', marginLeft: spacing.xs },
});
