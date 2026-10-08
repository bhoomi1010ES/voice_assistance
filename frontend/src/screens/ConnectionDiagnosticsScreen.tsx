import React from 'react';
import { StyleSheet, View } from 'react-native';
import { AppText } from '../components/ui/Primitives';
import { SettingsSection } from '../components/settings/SettingsSection';
import {
  SettingsCanvas,
  SettingsHero,
} from '../components/settings/SettingsPresentation';
import { useAppTheme } from '../design/ThemeProvider';
import { useOptionalVoiceSocket } from '../voice/VoiceSocketProvider';

function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'NONE';
  return `${Math.round(value)}ms`;
}

function shortId(value: string | null | undefined): string {
  if (!value) return 'NONE';
  if (value.length <= 10) return value;
  return `${value.slice(0, 6)}…${value.slice(-4)}`;
}

function DiagnosticRow({ label, value }: { label: string; value: string }) {
  const { colors } = useAppTheme();
  return (
    <View style={styles.diagnosticRow}>
      <AppText style={[styles.diagnosticLabel, { color: colors.textMuted }]}>
        {label}
      </AppText>
      <AppText style={[styles.diagnosticValue, { color: colors.text }]}>
        {value}
      </AppText>
    </View>
  );
}

export function ConnectionDiagnosticsScreen() {
  const voiceSocket = useOptionalVoiceSocket();
  return (
    <SettingsCanvas testID="connection-diagnostics-screen">
      <SettingsHero
        title="Connection diagnostics"
        subtitle="Inspect the voice connection, session events, and message timing."
      />
      {voiceSocket ? (
        <>
          <SettingsSection title="Connection status">
            <View style={styles.diagnosticsCard}>
              <DiagnosticRow
                label="Connection"
                value={voiceSocket.connection}
              />
              <DiagnosticRow label="Session" value={voiceSocket.session} />
            </View>
          </SettingsSection>
          <SettingsSection title="Live connection details">
            <View style={styles.diagnosticsCard} testID="voice-diagnostics">
              <DiagnosticRow
                label="Last event"
                value={voiceSocket.lastEvent ?? 'NONE'}
              />
              <DiagnosticRow
                label="Event sequence"
                value={String(voiceSocket.eventSequence)}
              />
              <DiagnosticRow label="Heartbeat" value={voiceSocket.heartbeat} />
              <DiagnosticRow
                label="Reconnect attempt"
                value={String(voiceSocket.reconnectAttempt)}
              />
              <DiagnosticRow
                label="Session ID"
                value={shortId(voiceSocket.sessionId)}
              />
              <DiagnosticRow
                label="Turn ID"
                value={shortId(voiceSocket.turnId)}
              />
              <DiagnosticRow
                label="Response ID"
                value={shortId(voiceSocket.responseId)}
              />
              <DiagnosticRow
                label="Dropped events"
                value={String(voiceSocket.droppedEventCount)}
              />
              <DiagnosticRow
                label="First visible text"
                value={formatMs(voiceSocket.firstTextAtMs)}
              />
              <DiagnosticRow
                label="Message render complete"
                value={formatMs(voiceSocket.conversationRenderCompletedAtMs)}
              />
            </View>
          </SettingsSection>
        </>
      ) : (
        <AppText>Connection diagnostics are unavailable.</AppText>
      )}
    </SettingsCanvas>
  );
}

const styles = StyleSheet.create({
  diagnosticsCard: { padding: 16, gap: 6 },
  diagnosticRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 16,
    paddingVertical: 7,
  },
  diagnosticLabel: { flex: 1, fontSize: 13, lineHeight: 20 },
  diagnosticValue: {
    flex: 1,
    fontSize: 13,
    lineHeight: 20,
    fontWeight: '600',
    textAlign: 'right',
  },
});
