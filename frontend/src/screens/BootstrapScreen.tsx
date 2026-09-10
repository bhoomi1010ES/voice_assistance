import React from 'react';
import { ActivityIndicator, StyleSheet, View } from 'react-native';
import { strings } from '../i18n/strings';
import { AppText, Screen } from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';

export function BootstrapScreen() {
  const { colors } = useAppTheme();
  return (
    <Screen testID="bootstrap-screen" style={styles.container}>
      <View accessible accessibilityLabel={strings.bootstrap.loading}>
        <ActivityIndicator color={colors.accent} size="large" />
        <AppText style={styles.text}>{strings.bootstrap.loading}</AppText>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  container: { alignItems: 'center', justifyContent: 'center' },
  text: { marginTop: 16, textAlign: 'center' },
});
