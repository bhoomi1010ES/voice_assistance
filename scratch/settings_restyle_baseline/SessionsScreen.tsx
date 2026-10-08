import React, { useCallback, useEffect, useState } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { toClientError, safeUserMessage } from '../api/errors';
import {
  AppText,
  Heading,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';
import { spacing, typography } from '../theme';
import { strings } from '../i18n/strings';
import { useAuth } from '../auth/AuthProvider';
import { AuthSession, Device } from '../auth/types';

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
    <Screen testID="sessions-screen">
      <ScrollView contentContainerStyle={styles.content}>
        {/* Stitch-inspired Hero Header */}
        <View style={styles.header}>
          <AppText style={[styles.overline, { color: colors.primary }]}>
            SECURITY & DEVICES
          </AppText>
          <Heading>{strings.sessions.title}</Heading>
          <AppText style={[styles.body, { color: colors.textMuted }]}>
            {strings.sessions.body}
          </AppText>
        </View>

        {error ? <StatusBanner tone="error">{error}</StatusBanner> : null}

        {loading ? (
          <AppText style={styles.loadingText}>
            {strings.sessions.loading}
          </AppText>
        ) : null}

        {!loading && sessions.length === 0 ? (
          <AppText style={styles.emptyText}>{strings.sessions.empty}</AppText>
        ) : null}

        {/* Sessions Section */}
        {sessions.length > 0 ? (
          <View style={styles.section}>
            <AppText style={[styles.sectionTitle, { color: colors.primary }]}>
              Active Sessions
            </AppText>
            {sessions.map(session => (
              <SessionDeviceCard
                icon="🔐"
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
            <AppText style={[styles.sectionTitle, { color: colors.primary }]}>
              {strings.sessions.devices}
            </AppText>
            {devices.map(device => (
              <SessionDeviceCard
                icon="📱"
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
      </ScrollView>
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
  content: {
    gap: spacing.md,
    paddingBottom: spacing.xxl,
  },
  header: {
    gap: 2,
    marginBottom: spacing.xs,
  },
  overline: {
    fontSize: typography.caption,
    fontWeight: '700',
    letterSpacing: 1,
  },
  body: {
    fontSize: typography.caption,
    lineHeight: 18,
    marginTop: 2,
  },
  section: {
    gap: spacing.xs,
    marginTop: spacing.sm,
  },
  sectionTitle: {
    fontSize: typography.caption,
    fontWeight: '700',
    letterSpacing: 0.8,
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
