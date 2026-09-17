import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { strings } from '../i18n/strings';
import { AppText } from '../components/ui/Primitives';
import { BottomTabBar, PrimaryTabRoute } from '../components/navigation/BottomTabBar';
import { AssistantScreen } from '../screens/AssistantScreen';
import { SettingsScreen } from '../screens/SettingsScreen';
import { DiagnosticScreen } from '../screens/DiagnosticScreen';
import { AccountScreen } from '../screens/AccountScreen';
import { SessionsScreen } from '../screens/SessionsScreen';
import { MemoryScreen } from '../screens/MemoryScreen';
import { TasksScreen } from '../screens/TasksScreen';
import { useAuth } from '../auth/AuthProvider';
import { useVoiceSocket } from '../voice/VoiceSocketProvider';
import { useAppTheme } from '../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../design/tokens';

export type MainRoute =
  | 'assistant'
  | 'settings'
  | 'diagnostics'
  | 'account'
  | 'sessions'
  | 'memory'
  | 'tasks';

export function MainNavigator() {
  const { controller } = useAuth();
  const { socket } = useVoiceSocket();
  const { colors } = useAppTheme();
  const insets = useSafeAreaInsets();
  const [route, setRoute] = useState<MainRoute>('assistant');

  const navigate = (nextRoute: MainRoute) => {
    setRoute(nextRoute);
  };

  const signOut = () => {
    (async () => {
      await socket.stop('logout');
      await controller.logout();
    })().catch(() => undefined);
  };

  const isSecondaryRoute =
    route === 'account' || route === 'sessions' || route === 'diagnostics';

  const getHeaderTitle = () => {
    switch (route) {
      case 'assistant':
        return strings.appName;
      case 'tasks':
        return strings.main.tasks;
      case 'memory':
        return strings.main.memory;
      case 'settings':
        return strings.main.settings;
      case 'account':
        return strings.main.profile;
      case 'sessions':
        return strings.sessions.title;
      case 'diagnostics':
        return strings.settings.diagnostics;
      default:
        return strings.appName;
    }
  };

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      <View
        style={[
          styles.appBar,
          {
            backgroundColor: colors.surface,
            borderBottomColor: colors.borderSubtle,
            paddingTop: insets.top,
          },
          shadows.sm,
        ]}
        testID="main-header"
      >
        <View style={styles.appBarContent}>
          {isSecondaryRoute ? (
            <Pressable
              accessibilityLabel={strings.assistant.cancel || 'Back'}
              accessibilityRole="button"
              hitSlop={spacing.sm}
              onPress={() => navigate('settings')}
              style={styles.backButton}
              testID="nav-back-button"
            >
              <Text style={[styles.backArrow, { color: colors.primary }]}>←</Text>
            </Pressable>
          ) : (
            <View style={styles.brandIconContainer}>
              <Text style={[styles.brandIcon, { color: colors.primary }]}>◉</Text>
            </View>
          )}

          <AppText style={styles.appBarTitle}>{getHeaderTitle()}</AppText>

          {/* Balance spacer or profile shortcut */}
          {route === 'settings' ? (
            <Pressable
              accessibilityLabel={strings.main.signOut}
              accessibilityRole="button"
              hitSlop={spacing.sm}
              onPress={signOut}
              style={styles.headerActionButton}
              testID="header-sign-out"
            >
              <Text style={[styles.headerActionText, { color: colors.error }]}>
                {strings.main.signOut}
              </Text>
            </Pressable>
          ) : (
            <View style={styles.headerRightSpacer} />
          )}
        </View>
      </View>

      <View style={styles.content}>
        {route === 'assistant' ? <AssistantScreen /> : null}
        {route === 'settings' ? (
          <SettingsScreen
            onOpenAccount={() => navigate('account')}
            onOpenDiagnostics={() => navigate('diagnostics')}
            onOpenSessions={() => navigate('sessions')}
            onOpenMemory={() => navigate('memory')}
            onSignOut={signOut}
          />
        ) : null}
        {route === 'diagnostics' ? <DiagnosticScreen /> : null}
        {route === 'account' ? <AccountScreen /> : null}
        {route === 'sessions' ? <SessionsScreen /> : null}
        {route === 'memory' ? <MemoryScreen /> : null}
        {route === 'tasks' ? <TasksScreen /> : null}
      </View>

      <BottomTabBar
        activeRoute={route}
        onNavigate={tab => navigate(tab as MainRoute)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  appBar: {
    borderBottomWidth: 1,
    zIndex: 10,
  },
  appBarContent: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    minHeight: 52,
    paddingHorizontal: spacing.md,
  },
  brandIconContainer: {
    alignItems: 'center',
    height: 36,
    justifyContent: 'center',
    width: 36,
  },
  brandIcon: {
    fontSize: 20,
    lineHeight: 24,
  },
  backButton: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
    minWidth: 44,
  },
  backArrow: {
    fontSize: 22,
    fontWeight: '700',
    lineHeight: 26,
  },
  appBarTitle: {
    fontSize: typography.heading,
    fontWeight: '700',
    letterSpacing: -0.2,
  },
  headerActionButton: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
    paddingHorizontal: spacing.sm,
  },
  headerActionText: {
    fontSize: typography.label,
    fontWeight: '600',
  },
  headerRightSpacer: {
    minHeight: 36,
    minWidth: 36,
  },
  content: {
    flex: 1,
  },
});
