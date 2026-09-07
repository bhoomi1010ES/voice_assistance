import React, { useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { strings } from '../i18n/strings';
import { AppText } from '../components/ui/Primitives';
import { AssistantScreen } from '../screens/AssistantScreen';
import { SettingsScreen } from '../screens/SettingsScreen';
import { DiagnosticScreen } from '../screens/DiagnosticScreen';
import { AccountScreen } from '../screens/AccountScreen';
import { SessionsScreen } from '../screens/SessionsScreen';
import { useAuth } from '../auth/AuthProvider';
import { useVoiceSocket } from '../voice/VoiceSocketProvider';
import { useAppTheme } from '../design/ThemeProvider';
import { radii, spacing, typography } from '../design/tokens';

type MainRoute =
  | 'assistant'
  | 'settings'
  | 'diagnostics'
  | 'account'
  | 'sessions';

export function MainNavigator() {
  const { controller } = useAuth();
  const { socket } = useVoiceSocket();
  const { colors } = useAppTheme();
  const insets = useSafeAreaInsets();
  const [route, setRoute] = useState<MainRoute>('assistant');
  const [menuOpen, setMenuOpen] = useState(false);

  const navigate = (nextRoute: MainRoute) => {
    setMenuOpen(false);
    setRoute(nextRoute);
  };

  const signOut = () => {
    setMenuOpen(false);
    (async () => {
      await socket.stop('logout');
      await controller.logout();
    })().catch(() => undefined);
  };

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      <View
        style={[
          styles.appBar,
          { backgroundColor: colors.surface, borderBottomColor: colors.border },
          { paddingTop: insets.top },
        ]}
      >
        <View style={styles.appBarContent}>
          <AppText style={styles.appBarTitle}>{strings.appName}</AppText>
          <Pressable
            accessibilityLabel={
              menuOpen ? strings.main.closeMenu : strings.main.openMenu
            }
            accessibilityRole="button"
            hitSlop={spacing.sm}
            onPress={() => setMenuOpen(open => !open)}
            style={styles.menuButton}
            testID="main-menu-button"
          >
            <Text style={[styles.menuDots, { color: colors.text }]}>⋮</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.content}>
        {route === 'assistant' ? <AssistantScreen /> : null}
        {route === 'settings' ? (
          <SettingsScreen
            onOpenAccount={() => navigate('account')}
            onOpenDiagnostics={() => navigate('diagnostics')}
            onOpenSessions={() => navigate('sessions')}
          />
        ) : null}
        {route === 'diagnostics' ? <DiagnosticScreen /> : null}
        {route === 'account' ? <AccountScreen /> : null}
        {route === 'sessions' ? <SessionsScreen /> : null}
      </View>

      <Modal
        animationType="fade"
        onRequestClose={() => setMenuOpen(false)}
        transparent
        visible={menuOpen}
      >
        <View style={styles.menuOverlay}>
          <Pressable
            accessibilityLabel={strings.main.closeMenu}
            onPress={() => setMenuOpen(false)}
            style={StyleSheet.absoluteFill}
          />
          <Pressable
            accessibilityRole="menu"
            onPress={() => undefined}
            style={[
              styles.menu,
              { backgroundColor: colors.surface, borderColor: colors.border },
              { marginTop: insets.top + 56 },
            ]}
            testID="main-menu"
          >
            <MenuItem
              label={strings.main.assistant}
              onPress={() => navigate('assistant')}
              testID="menu-assistant"
            />
            <MenuItem
              label={strings.main.settings}
              onPress={() => navigate('settings')}
              testID="menu-settings"
            />
            <MenuItem
              label={strings.main.profile}
              onPress={() => navigate('account')}
              testID="menu-profile"
            />
            <MenuItem
              destructive
              label={strings.main.signOut}
              onPress={signOut}
              testID="menu-sign-out"
            />
          </Pressable>
        </View>
      </Modal>
    </View>
  );
}

function MenuItem({
  destructive = false,
  label,
  onPress,
  testID,
}: {
  destructive?: boolean;
  label: string;
  onPress: () => void;
  testID: string;
}) {
  const { colors } = useAppTheme();

  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="menuitem"
      onPress={onPress}
      style={({ pressed }) => [
        styles.menuItem,
        { opacity: pressed ? 0.65 : 1 },
      ]}
      testID={testID}
    >
      <AppText
        style={[
          styles.menuItemText,
          destructive ? { color: colors.error } : null,
        ]}
      >
        {label}
      </AppText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  appBar: { borderBottomWidth: 1 },
  appBarContent: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    minHeight: 56,
    paddingHorizontal: spacing.lg,
  },
  appBarTitle: { fontSize: typography.heading, fontWeight: '700' },
  menuButton: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
    minWidth: 44,
  },
  menuDots: { fontSize: 30, fontWeight: '700', lineHeight: 34 },
  content: { flex: 1 },
  menuOverlay: {
    alignItems: 'flex-end',
    flex: 1,
    paddingHorizontal: spacing.lg,
  },
  menu: {
    borderRadius: radii.md,
    borderWidth: 1,
    elevation: 8,
    minWidth: 220,
    paddingVertical: spacing.sm,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.18,
    shadowRadius: 10,
  },
  menuItem: {
    minHeight: 52,
    justifyContent: 'center',
    paddingHorizontal: spacing.lg,
  },
  menuItemText: { fontWeight: '600' },
});
