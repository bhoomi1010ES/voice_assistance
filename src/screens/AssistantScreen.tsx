import React from 'react';
import { StyleSheet } from 'react-native';
import { strings } from '../i18n/strings';
import {
  AppText,
  Card,
  Heading,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';

export function AssistantScreen() {
  return (
    <Screen testID="assistant-screen">
      <Heading>{strings.assistant.title}</Heading>
      <AppText style={styles.subtitle}>{strings.assistant.ready}</AppText>
      <StatusBanner>{strings.assistant.phaseNotice}</StatusBanner>
      <Card style={styles.card}>
        <AppText>
          Voice controls will connect to the existing native audio and WebSocket
          layers in the next UI phase.
        </AppText>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  subtitle: { marginBottom: 20, marginTop: 8 },
  card: { marginTop: 16 },
});
