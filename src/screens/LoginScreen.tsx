import React, { useState } from 'react';
import { Modal, Pressable, StyleSheet, TextInput, View } from 'react-native';
import { ClientError, safeUserMessage, toClientError } from '../api/errors';
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

export function LoginScreen({
  sessionExpired,
  onDismissSessionExpired,
}: {
  sessionExpired: boolean;
  onDismissSessionExpired: () => void;
}) {
  const { controller } = useAuth();
  const { colors } = useAppTheme();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    const normalizedEmail = email.trim();
    if (!/^\S+@\S+\.\S+$/.test(normalizedEmail)) {
      setError(strings.auth.invalidEmail);
      return;
    }
    if (!password) {
      setError(strings.auth.missingPassword);
      return;
    }

    setError(null);
    setSubmitting(true);
    try {
      await controller.login(normalizedEmail, password);
      setPassword('');
    } catch (cause) {
      const clientError = toClientError(cause);
      setError(loginErrorMessage(clientError));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Screen testID="auth-screen">
      <View style={styles.content}>
        <Heading>{strings.auth.title}</Heading>
        <AppText style={styles.body}>{strings.auth.body}</AppText>
        <Card>
          {error ? <StatusBanner tone="error">{error}</StatusBanner> : null}
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
              autoComplete="password"
              onChangeText={setPassword}
              placeholder={strings.auth.password}
              placeholderTextColor={colors.textMuted}
              secureTextEntry={!showPassword}
              style={[
                styles.input,
                styles.passwordInput,
                { color: colors.text, borderColor: colors.border },
              ]}
              textContentType="password"
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
          <ActionButton
            disabled={submitting}
            label={submitting ? strings.auth.signingIn : strings.auth.signIn}
            onPress={submit}
            style={styles.submit}
          />
        </Card>
      </View>
      <Modal
        accessibilityViewIsModal
        animationType="fade"
        onRequestClose={onDismissSessionExpired}
        transparent
        visible={sessionExpired}
      >
        <View style={styles.modalBackdrop}>
          <Card style={styles.modalCard}>
            <Heading>{strings.auth.sessionExpired}</Heading>
            <ActionButton
              label={strings.auth.dismiss}
              onPress={onDismissSessionExpired}
              style={styles.submit}
            />
          </Card>
        </View>
      </Modal>
    </Screen>
  );
}

function loginErrorMessage(error: ClientError): string {
  if (error.status === 401 || error.code === 'AUTHENTICATION_FAILED') {
    return 'The email or password is incorrect.';
  }
  if (error.status === 429) {
    return 'Too many attempts. Please wait a moment and try again.';
  }
  if (error.status === 503) {
    return 'The sign-in service is unavailable. Please try again later.';
  }
  return safeUserMessage(error);
}

const styles = StyleSheet.create({
  content: { flex: 1, justifyContent: 'center' },
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
  modalBackdrop: {
    alignItems: 'center',
    backgroundColor: '#00000088',
    flex: 1,
    justifyContent: 'center',
    padding: spacing.lg,
  },
  modalCard: { width: '100%' },
});
