import { AppIcon } from '../components/ui/AppIcon';
import React, { useEffect, useLayoutEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { strings } from '../i18n/strings';
import { AppText } from '../components/ui/Primitives';
import { BottomTabBar } from '../components/navigation/BottomTabBar';
import { AssistantScreen } from '../screens/AssistantScreen';
import { SettingsScreen } from '../screens/SettingsScreen';
import { DiagnosticScreen } from '../screens/DiagnosticScreen';
import { ConnectionDiagnosticsScreen } from '../screens/ConnectionDiagnosticsScreen';
import { AccountScreen } from '../screens/AccountScreen';
import { SessionsScreen } from '../screens/SessionsScreen';
import { MemoryScreen } from '../screens/MemoryScreen';
import { TasksScreen } from '../screens/TasksScreen';
import { useAuth } from '../auth/AuthProvider';
import { useVoiceSocket } from '../voice/VoiceSocketProvider';
import { useAppTheme } from '../design/ThemeProvider';
import { shadows, spacing, typography } from '../design/tokens';
import { setupPushNotificationListeners } from '../notifications/PushNotificationService';

export type MainRoute =
  | 'assistant'
  | 'settings'
  | 'diagnostics'
  | 'connection-diagnostics'
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
  useLayoutEffect(() => {
    socket.setAssistantVisible(route === 'assistant').catch(() => undefined);
  }, [route, socket]);

  useEffect(() => {
    socket.connect().catch(() => undefined);
  }, [socket]);
  const [tasksInitialPage, setTasksInitialPage] = useState<
    'tasks' | 'reminders'
  >('tasks');

  useEffect(() => {
    const unsubscribe = setupPushNotificationListeners(controller, data => {
      setRoute('tasks');
      if (data?.kind === 'task_created') {
        setTasksInitialPage('tasks');
      } else {
        setTasksInitialPage('reminders');
      }
    });
    return unsubscribe;
  }, [controller]);

  const navigate = (nextRoute: MainRoute) => {
    socket
      .setAssistantVisible(nextRoute === 'assistant')
      .catch(() => undefined);
    setRoute(nextRoute);
  };

  const signOut = () => {
    (async () => {
      await socket.stop('logout');
      await controller.logout();
    })().catch(() => undefined);
  };

  const isSecondaryRoute =
    route === 'account' ||
    route === 'sessions' ||
    route === 'diagnostics' ||
    route === 'connection-diagnostics';
  const isSettingsPresentationRoute =
    route === 'settings' ||
    route === 'sessions' ||
    route === 'diagnostics' ||
    route === 'connection-diagnostics';

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
      case 'connection-diagnostics':
        return 'Connection diagnostics';
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
          isSettingsPresentationRoute
            ? [styles.settingsAppBar, { backgroundColor: colors.background }]
            : null,
        ]}
        testID="main-header"
      >
        <View style={styles.appBarContent}>
          {isSettingsPresentationRoute ? (
            <>
              {route === 'settings' ? (
                <View style={styles.brandIconContainer}>
                  <AppIcon name="Settings" size={28} color={colors.primary} />
                </View>
              ) : (
                <Pressable
                  accessibilityLabel="Back to Settings"
                  accessibilityRole="button"
                  hitSlop={spacing.sm}
                  onPress={() => navigate('settings')}
                  style={styles.backButton}
                  testID="nav-back-button"
                >
                  <AppIcon
                    name="ChevronLeft"
                    size={22}
                    color={colors.primary}
                  />
                </Pressable>
              )}
            </>
          ) : route === 'memory' || route === 'tasks' ? (
            <>
              <View style={styles.brandIconContainer}>
                <AppIcon
                  name={route === 'tasks' ? 'CalendarCheck' : 'Brain'}
                  size={30}
                  color={route === 'memory' ? colors.secondary : colors.primary}
                />
              </View>

              <View
                style={[
                  styles.titleColumn,
                  route === 'tasks' ? styles.taskTitleColumn : null,
                ]}
              >
                <AppText style={styles.appBarTitle}>
                  {route === 'tasks'
                    ? strings.tasks.title
                    : strings.memory.title}
                </AppText>
                <Text
                  style={[styles.appBarSubtitle, { color: colors.textMuted }]}
                >
                  {route === 'tasks' ? strings.tasks.body : strings.memory.body}
                </Text>
              </View>
            </>
          ) : route === 'assistant' ? (
            <>
              <View style={styles.brandIconContainer}>
                <AppIcon name="AudioLines" size={30} color={colors.primary} />
              </View>

              <View style={styles.titleColumn}>
                <AppText style={styles.appBarTitle}>{strings.appName}</AppText>
                <Text
                  style={[styles.appBarSubtitle, { color: colors.textMuted }]}
                >
                  {strings.assistant.tagline}
                </Text>
              </View>

              <View style={styles.headerRightSpacer} />
            </>
          ) : (
            <>
              {isSecondaryRoute ? (
                <Pressable
                  accessibilityLabel={strings.assistant.cancel || 'Back'}
                  accessibilityRole="button"
                  hitSlop={spacing.sm}
                  onPress={() => navigate('settings')}
                  style={styles.backButton}
                  testID="nav-back-button"
                >
                  <AppIcon
                    name="ChevronLeft"
                    size={22}
                    color={colors.primary}
                  />
                </Pressable>
              ) : (
                <View style={styles.brandIconContainer}>
                  <AppIcon name="AudioLines" size={22} color={colors.primary} />
                </View>
              )}

              <AppText style={styles.appBarTitle}>{getHeaderTitle()}</AppText>

              <View style={styles.headerRightSpacer} />
            </>
          )}
        </View>
      </View>

      <View style={styles.content}>
        {route === 'assistant' ? <AssistantScreen /> : null}
        {route === 'settings' ? (
          <SettingsScreen
            onOpenAccount={() => navigate('account')}
            onOpenDiagnostics={() => navigate('diagnostics')}
            onOpenConnectionDiagnostics={() =>
              navigate('connection-diagnostics')
            }
            onOpenSessions={() => navigate('sessions')}
            onSignOut={signOut}
          />
        ) : null}
        {route === 'diagnostics' ? <DiagnosticScreen /> : null}
        {route === 'connection-diagnostics' ? (
          <ConnectionDiagnosticsScreen />
        ) : null}
        {route === 'account' ? <AccountScreen /> : null}
        {route === 'sessions' ? <SessionsScreen /> : null}
        {route === 'memory' ? <MemoryScreen /> : null}
        {route === 'tasks' ? (
          <TasksScreen initialPage={tasksInitialPage} />
        ) : null}
      </View>

      <BottomTabBar
        activeRoute={route}
        onNavigate={tab => {
          if (tab === 'tasks') {
            setTasksInitialPage('tasks');
          }
          navigate(tab as MainRoute);
        }}
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
  settingsAppBar: {
    borderBottomWidth: 0,
    elevation: 0,
    shadowOpacity: 0,
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
  sparkleIcon: {
    fontSize: 28,
    lineHeight: 32,
  },
  titleColumn: {
    alignItems: 'center',
    flex: 1,
    justifyContent: 'center',
  },
  taskTitleColumn: {
    alignItems: 'flex-start',
    paddingLeft: spacing.sm,
    paddingVertical: spacing.sm,
  },
  appBarSubtitle: {
    fontSize: 12,
    fontWeight: '500',
    letterSpacing: 0.2,
    marginTop: 1,
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
  headerRightSpacer: {
    minHeight: 38,
    minWidth: 38,
  },
  content: {
    flex: 1,
  },
});
