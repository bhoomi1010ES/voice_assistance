import React, { useState } from 'react';
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { ClientError, safeUserMessage, toClientError } from '../api/errors';
import { useAuth } from '../auth/AuthProvider';
import { AuthBrandHeader } from '../components/auth/AuthBrandHeader';
import { AuthCard } from '../components/auth/AuthCard';
import { AuthField } from '../components/auth/AuthField';
import {
  ActionButton,
  AppText,
  Card,
  Heading,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';
import { radii, spacing, typography } from '../design/tokens';
import { strings } from '../i18n/strings';

export function LoginScreen({
  sessionExpired,
  onDismissSessionExpired,
  onCreateAccount,
  initialEmail = '',
  registrationMessage,
}: {
  sessionExpired: boolean;
  onDismissSessionExpired: () => void;
  onCreateAccount: () => void;
  initialEmail?: string;
  registrationMessage?: string | null;
}) {
  const { controller } = useAuth();
  const { colors } = useAppTheme();
  const [email, setEmail] = useState(initialEmail);
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
      <ScrollView
        contentContainerStyle={styles.scrollContent}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.container}>
          <AuthBrandHeader
            subtitle={strings.auth.body}
            title={strings.auth.title}
          />

          <AuthCard>
            {registrationMessage ? (
              <View style={styles.bannerSpacing}>
                <StatusBanner tone="success">
                  {registrationMessage}
                </StatusBanner>
              </View>
            ) : null}

            {error ? (
              <View style={styles.bannerSpacing}>
                <StatusBanner tone="error">{error}</StatusBanner>
              </View>
            ) : null}

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
              autoComplete="password"
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
              textContentType="password"
              value={password}
            />

            <ActionButton
              disabled={submitting}
              label={submitting ? strings.auth.signingIn : strings.auth.signIn}
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
                {strings.auth.noAccount}
              </AppText>
              <Pressable
                accessibilityLabel={strings.auth.createAccount}
                accessibilityRole="button"
                hitSlop={spacing.xs}
                onPress={onCreateAccount}
                testID="create-account-link"
              >
                <AppText style={[styles.link, { color: colors.primary }]}>
                  {strings.auth.createAccount}
                </AppText>
              </Pressable>
            </View>
          </AuthCard>
        </View>
      </ScrollView>

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
  modalBackdrop: {
    alignItems: 'center',
    backgroundColor: '#00000088',
    flex: 1,
    justifyContent: 'center',
    padding: spacing.lg,
  },
  modalCard: {
    borderRadius: radii.lg,
    width: '100%',
  },
});
