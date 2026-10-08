import React, { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { toClientError, safeUserMessage } from '../api/errors';
import { AppText, StatusBanner } from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';
import { spacing, typography } from '../theme';
import { strings } from '../i18n/strings';
import { useAuth } from '../auth/AuthProvider';
import { AuthSession, Device } from '../auth/types';

import {
  SettingsCanvas,
  SettingsHero,
  SettingsIcon,
} from '../components/settings/SettingsPresentation';
import { SessionDeviceCard } from '../components/settings/SessionDeviceCard';

export function SessionsScreen() {
  const { controller, status } = useAuth();
  const { colors } = useAppTheme();
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
    if (status !== 'authenticated') return;
    load().catch(() => undefined);
  }, [load, status]);

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
    <SettingsCanvas testID="sessions-screen">
      <SettingsHero
        title={strings.sessions.title}
        subtitle={strings.sessions.body}
        eyebrow="Security & devices"
      />

      {error ? <StatusBanner tone="error">{error}</StatusBanner> : null}

      {loading ? (
        <AppText style={styles.loadingText}>{strings.sessions.loading}</AppText>
      ) : null}

      {!loading && sessions.length === 0 ? (
        <AppText style={styles.emptyText}>{strings.sessions.empty}</AppText>
      ) : null}

      {/* Sessions Section */}
      {sessions.length > 0 ? (
        <View style={styles.section}>
          <AppText style={[styles.sectionTitle, { color: colors.textMuted }]}>
            Active Sessions
          </AppText>
          {sessions.map(session => (
            <SessionDeviceCard
              icon={<SettingsIcon kind="device" />}
              isRevoked={Boolean(session.revoked_at)}
              key={session.id}
              onRevoke={() => revokeSession(session.id)}
              revoking={revoking === session.id}
              subtitle={formatDate(session.last_used_at)}
              title={`${strings.sessions.device}: ${deviceName(
                session.device_id,
                devices,
              )}`}
            />
          ))}
        </View>
      ) : null}

      {/* Devices Section */}
      {devices.length > 0 ? (
        <View style={styles.section}>
          <AppText style={[styles.sectionTitle, { color: colors.textMuted }]}>
            {strings.sessions.devices}
          </AppText>
          {devices.map(device => (
            <SessionDeviceCard
              icon={<SettingsIcon kind="device" />}
              isRevoked={Boolean(device.revoked_at)}
              key={device.id}
              onRevoke={() => revokeDevice(device.id)}
              revoking={revoking === device.id}
              subtitle={device.device_identifier}
              title={device.name || device.platform}
            />
          ))}
        </View>
      ) : null}
    </SettingsCanvas>
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
  section: {
    gap: spacing.xs,
    marginTop: spacing.sm,
  },
  sectionTitle: {
    fontSize: typography.caption,
    fontWeight: '700',
    letterSpacing: 1.6,
    paddingHorizontal: 2,
    textTransform: 'uppercase',
  },
  loadingText: {
    fontSize: typography.body,
    paddingVertical: spacing.md,
  },
  emptyText: {
    fontSize: typography.body,
    paddingVertical: spacing.md,
  },
});
