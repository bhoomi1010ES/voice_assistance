import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet } from 'react-native';
import { toClientError, safeUserMessage } from '../api/errors';
import {
  ActionButton,
  AppText,
  Card,
  Heading,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';
import { spacing } from '../design/tokens';
import { strings } from '../i18n/strings';
import { useAuth } from '../auth/AuthProvider';
import { AuthSession, Device } from '../auth/types';

export function SessionsScreen() {
  const { controller } = useAuth();
  const [sessions, setSessions] = useState<AuthSession[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sessionResult, deviceResult] = await Promise.all([
        controller.listSessions(),
        controller.listDevices(),
      ]);
      setSessions(sessionResult);
      setDevices(deviceResult);
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setLoading(false);
    }
  }, [controller]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  const revokeSession = async (id: string) => {
    setRevoking(id);
    try {
      await controller.revokeSession(id);
      await load();
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setRevoking(null);
    }
  };

  const revokeDevice = async (id: string) => {
    setRevoking(id);
    try {
      await controller.revokeDevice(id);
      await load();
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setRevoking(null);
    }
  };

  return (
    <Screen testID="sessions-screen">
      <Heading>{strings.sessions.title}</Heading>
      <AppText style={styles.body}>{strings.sessions.body}</AppText>
      {error ? <StatusBanner tone="error">{error}</StatusBanner> : null}
      {loading ? <AppText>{strings.sessions.loading}</AppText> : null}
      {!loading && sessions.length === 0 ? (
        <AppText>{strings.sessions.empty}</AppText>
      ) : null}
      {sessions.map(session => (
        <Card key={session.id} style={styles.card}>
          <AppText>
            {strings.sessions.device}: {deviceName(session.device_id, devices)}
          </AppText>
          <AppText style={styles.caption}>
            {formatDate(session.last_used_at)}
          </AppText>
          {session.revoked_at ? (
            <AppText>{strings.sessions.revoked}</AppText>
          ) : (
            <ActionButton
              disabled={revoking === session.id}
              label={
                revoking === session.id
                  ? strings.sessions.revoked
                  : strings.sessions.revoke
              }
              onPress={() => revokeSession(session.id)}
              variant="secondary"
              style={styles.action}
            />
          )}
        </Card>
      ))}
      {devices.length > 0 ? (
        <AppText style={styles.devicesTitle}>
          {strings.sessions.devices}
        </AppText>
      ) : null}
      {devices.map(device => (
        <Card key={device.id} style={styles.card}>
          <AppText>{device.name || device.platform}</AppText>
          <AppText style={styles.caption}>{device.device_identifier}</AppText>
          {device.revoked_at ? (
            <AppText>{strings.sessions.revoked}</AppText>
          ) : (
            <ActionButton
              disabled={revoking === device.id}
              label={
                revoking === device.id
                  ? strings.sessions.revoked
                  : strings.sessions.revoke
              }
              onPress={() => revokeDevice(device.id)}
              variant="secondary"
              style={styles.action}
            />
          )}
        </Card>
      ))}
    </Screen>
  );
}

function deviceName(deviceId: string, devices: Device[]): string {
  const device = devices.find(item => item.id === deviceId);
  return device?.name || device?.platform || 'Registered device';
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
}

const styles = StyleSheet.create({
  body: { marginBottom: spacing.md, marginTop: spacing.sm },
  card: { marginTop: spacing.md },
  caption: { marginTop: spacing.xs, opacity: 0.75 },
  action: { marginTop: spacing.md },
  devicesTitle: { fontWeight: '700', marginTop: spacing.lg },
});
