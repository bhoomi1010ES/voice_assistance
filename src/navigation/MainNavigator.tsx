import React, { useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { strings } from '../i18n/strings';
import { ActionButton } from '../components/ui/Primitives';
import { AssistantScreen } from '../screens/AssistantScreen';
import { SettingsScreen } from '../screens/SettingsScreen';
import { DiagnosticScreen } from '../screens/DiagnosticScreen';
import { AccountScreen } from '../screens/AccountScreen';
import { SessionsScreen } from '../screens/SessionsScreen';
import { useAuth } from '../auth/AuthProvider';

type MainRoute =
  | 'assistant'
  | 'settings'
  | 'diagnostics'
  | 'account'
  | 'sessions';

export function MainNavigator() {
  const { controller } = useAuth();
  const [route, setRoute] = useState<MainRoute>('assistant');

  return (
    <View style={styles.container}>
      {route === 'assistant' ? <AssistantScreen /> : null}
      {route === 'settings' ? (
        <SettingsScreen
          onOpenAccount={() => setRoute('account')}
          onOpenDiagnostics={() => setRoute('diagnostics')}
          onOpenSessions={() => setRoute('sessions')}
        />
      ) : null}
      {route === 'diagnostics' ? <DiagnosticScreen /> : null}
      {route === 'account' ? <AccountScreen /> : null}
      {route === 'sessions' ? <SessionsScreen /> : null}
      <View style={styles.navigation} accessibilityRole="tablist">
        <ActionButton
          label={strings.main.assistant}
          onPress={() => setRoute('assistant')}
          variant={route === 'assistant' ? 'primary' : 'secondary'}
          accessibilityRole="tab"
          accessibilityState={{ selected: route === 'assistant' }}
        />
        <ActionButton
          label={strings.main.settings}
          onPress={() => setRoute('settings')}
          variant={route === 'settings' ? 'primary' : 'secondary'}
          accessibilityRole="tab"
          accessibilityState={{ selected: route === 'settings' }}
        />
        <ActionButton
          label={strings.main.signOut}
          onPress={() => {
            controller.logout().catch(() => undefined);
          }}
          variant="quiet"
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  navigation: { gap: 8, padding: 16 },
});
