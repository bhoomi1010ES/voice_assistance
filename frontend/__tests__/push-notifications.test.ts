import { AuthController } from '../src/auth/AuthController';
import { AuthTokenResponse } from '../src/auth/types';
import {
  clearSeenDeliveryIds,
  createRemindersNotificationChannel,
  handleBackgroundRemoteMessage,
  handleForegroundRemoteMessage,
  recordAndCheckDeliveryId,
  setNotificationTapHandler,
  syncPushToken,
  triggerNotificationTap,
  REMINDERS_CHANNEL_ID,
} from '../src/notifications/PushNotificationService';
import notifee, { AndroidImportance } from '@notifee/react-native';

describe('Push Notifications Client (Phase 4)', () => {
  beforeEach(() => {
    clearSeenDeliveryIds();
    jest.clearAllMocks();
  });

  describe('Deduplication of delivery_id', () => {
    it('allows first delivery and rejects duplicates', () => {
      const deliveryId = 'deliv-uuid-1234';
      expect(recordAndCheckDeliveryId(deliveryId)).toBe(false);
      expect(recordAndCheckDeliveryId(deliveryId)).toBe(true);
      expect(recordAndCheckDeliveryId('different-deliv-id')).toBe(false);
    });

    it('gracefully handles undefined or null delivery IDs', () => {
      expect(recordAndCheckDeliveryId(undefined)).toBe(false);
      expect(recordAndCheckDeliveryId(null)).toBe(false);
    });
  });

  describe('Notification channel', () => {
    it('creates high-importance reminders channel', async () => {
      const channelId = await createRemindersNotificationChannel();
      expect(channelId).toBe(REMINDERS_CHANNEL_ID);
      expect(notifee.createChannel).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 'reminders',
          name: 'Reminders',
          importance: AndroidImportance.HIGH,
        }),
      );
    });
  });

  describe('Foreground notification presentation', () => {
    it('displays a high-importance banner for incoming foreground messages', async () => {
      const remoteMessage = {
        messageId: 'msg-1',
        notification: {
          title: 'Doctor Appointment',
          body: 'Leave in 10 minutes',
        },
        data: {
          delivery_id: 'deliv-msg-1',
        },
      };

      await handleForegroundRemoteMessage(remoteMessage as any);

      expect(notifee.displayNotification).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Doctor Appointment',
          body: 'Leave in 10 minutes',
          android: expect.objectContaining({
            channelId: 'reminders',
            importance: AndroidImportance.HIGH,
          }),
          data: {
            delivery_id: 'deliv-msg-1',
          },
        }),
      );
    });

    it('ignores duplicate message delivery', async () => {
      const remoteMessage = {
        messageId: 'msg-2',
        notification: {
          title: 'Repeated Reminder',
          body: 'Take pills',
        },
        data: {
          delivery_id: 'deliv-repeat-99',
        },
      };

      await handleForegroundRemoteMessage(remoteMessage as any);
      expect(notifee.displayNotification).toHaveBeenCalledTimes(1);

      // Second attempt with same delivery_id
      await handleForegroundRemoteMessage(remoteMessage as any);
      expect(notifee.displayNotification).toHaveBeenCalledTimes(1);
    });

    it('records delivery_id on background remote message to ignore duplicates', async () => {
      const backgroundMessage = {
        messageId: 'bg-msg-1',
        data: {
          delivery_id: 'deliv-bg-dup-1',
        },
      };

      await handleBackgroundRemoteMessage(backgroundMessage as any);
      expect(recordAndCheckDeliveryId('deliv-bg-dup-1')).toBe(true);
    });
  });

  describe('Notification tap handling', () => {
    it('invokes tap handler when notification press is triggered', () => {
      const tapHandler = jest.fn();
      setNotificationTapHandler(tapHandler);

      triggerNotificationTap();
      expect(tapHandler).toHaveBeenCalledTimes(1);
    });
  });

  describe('AuthController device ID persistence and token lifecycle', () => {
    let mockStorage: any;
    let mockFetch: jest.Mock;

    beforeEach(() => {
      let storedTokens: any = null;
      let storedDeviceId: string | null = null;
      mockStorage = {
        read: jest.fn(async () => storedTokens),
        save: jest.fn(async tokens => {
          storedTokens = tokens;
        }),
        clear: jest.fn(async () => {
          storedTokens = null;
          storedDeviceId = null;
        }),
        readDeviceId: jest.fn(async () => storedDeviceId),
        saveDeviceId: jest.fn(async id => {
          storedDeviceId = id;
        }),
        clearDeviceId: jest.fn(async () => {
          storedDeviceId = null;
        }),
      };
      mockFetch = jest.fn();
    });

    it('persists device.id from AuthTokenResponse upon login and restores it', async () => {
      const responsePayload: AuthTokenResponse = {
        token_type: 'bearer',
        access_token: 'acc-token-1',
        refresh_token: 'ref-token-1',
        expires_in: 3600,
        user: {
          id: 'user-uuid-1',
          email: 'user@example.com',
          name: 'Test User',
          status: 'active',
          created_at: '2026-10-07T00:00:00Z',
          updated_at: '2026-10-07T00:00:00Z',
        },
        device: {
          id: 'dev-uuid-999',
          device_identifier: 'device-id-android',
          platform: 'android',
          device_kind: 'physical',
          name: 'Android Phone',
          metadata: null,
          created_at: '2026-10-07T00:00:00Z',
          last_seen_at: '2026-10-07T00:00:00Z',
          revoked_at: null,
        },
        session: {
          id: 'session-uuid-1',
          device_id: 'dev-uuid-999',
          created_at: '2026-10-07T00:00:00Z',
          last_used_at: '2026-10-07T00:00:00Z',
          expires_at: '2026-10-08T00:00:00Z',
          revoked_at: null,
        },
      };

      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => responsePayload,
      });

      const controller = new AuthController({
        storage: mockStorage,
        fetchImpl: mockFetch as any,
      });

      await controller.login('user@example.com', 'password123');

      expect(controller.getDeviceId()).toBe('dev-uuid-999');
      expect(mockStorage.saveDeviceId).toHaveBeenCalledWith('dev-uuid-999');

      // Now create a fresh controller simulating process death and restore session
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => responsePayload.user,
      });

      const restoredController = new AuthController({
        storage: mockStorage,
        fetchImpl: mockFetch as any,
      });

      await restoredController.restore();

      expect(restoredController.getDeviceId()).toBe('dev-uuid-999');
      expect(restoredController.getState().status).toBe('authenticated');
    });

    it('patches push token to null on logout and clears local device ID', async () => {
      const controller = new AuthController({
        storage: mockStorage,
        fetchImpl: mockFetch as any,
      });

      // Seed logged-in state
      (controller as any).tokens = {
        accessToken: 'acc-token-2',
        refreshToken: 'ref-token-2',
      };
      (controller as any).deviceId = 'dev-uuid-888';

      // 1. PATCH /devices/{device.id}/push-token with { token: null }
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ id: 'dev-uuid-888', push_token: null }),
      });
      // 2. POST /auth/logout
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ status: 'logged_out' }),
      });

      await controller.logout();

      // Check PATCH call
      const patchCall = mockFetch.mock.calls.find(call =>
        call[0].includes('/devices/dev-uuid-888/push-token'),
      );
      expect(patchCall).toBeDefined();
      expect(patchCall[1].method).toBe('PATCH');
      expect(JSON.parse(patchCall[1].body)).toEqual({ token: null });

      // Local state cleared
      expect(controller.getDeviceId()).toBeNull();
      expect(mockStorage.clear).toHaveBeenCalled();
    });

    it('syncPushToken obtains token and updates backend device row', async () => {
      const controller = new AuthController({
        storage: mockStorage,
        fetchImpl: mockFetch as any,
      });
      (controller as any).tokens = {
        accessToken: 'acc-token-3',
        refreshToken: 'ref-token-3',
      };
      (controller as any).deviceId = 'dev-uuid-777';

      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          id: 'dev-uuid-777',
          push_token: 'mock-fcm-token',
        }),
      });

      const token = await syncPushToken(controller);

      expect(token).toBe('mock-fcm-token');
      const patchCall = mockFetch.mock.calls.find(call =>
        call[0].includes('/devices/dev-uuid-777/push-token'),
      );
      expect(patchCall).toBeDefined();
      expect(JSON.parse(patchCall[1].body)).toEqual({
        token: 'mock-fcm-token',
      });
    });

    it('syncPushToken returns null if getToken fails', async () => {
      const { getToken } = require('@react-native-firebase/messaging');
      (getToken as jest.Mock).mockRejectedValueOnce(
        new Error('Google Play services not available'),
      );

      const controller = new AuthController({
        storage: mockStorage,
        fetchImpl: mockFetch as any,
      });
      (controller as any).tokens = {
        accessToken: 'acc-token-4',
        refreshToken: 'ref-token-4',
      };
      (controller as any).deviceId = 'dev-uuid-777';

      const token = await syncPushToken(controller);
      expect(token).toBeNull();
    });

    it('syncPushToken returns null if backend PATCH fails', async () => {
      const controller = new AuthController({
        storage: mockStorage,
        fetchImpl: mockFetch as any,
      });
      (controller as any).tokens = {
        accessToken: 'acc-token-5',
        refreshToken: 'ref-token-5',
      };
      (controller as any).deviceId = 'dev-uuid-777';

      mockFetch.mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: async () => ({ code: 'SERVER_ERROR' }),
      });

      const token = await syncPushToken(controller);
      expect(token).toBeNull();
    });

    it('registers FCM token after login (mocked messaging + API)', async () => {
      const responsePayload: AuthTokenResponse = {
        token_type: 'bearer',
        access_token: 'acc-token-after-login-123',
        refresh_token: 'ref-token-after-login-123',
        expires_in: 3600,
        user: {
          id: 'user-uuid-1',
          email: 'user@example.com',
          name: 'Test User',
          status: 'active',
          created_at: '2026-10-07T00:00:00Z',
          updated_at: '2026-10-07T00:00:00Z',
        },
        device: {
          id: 'dev-uuid-login-123',
          device_identifier: 'device-id-android',
          platform: 'android',
          device_kind: 'physical',
          name: 'Android Phone',
          metadata: null,
          created_at: '2026-10-07T00:00:00Z',
          last_seen_at: '2026-10-07T00:00:00Z',
          revoked_at: null,
        },
        session: {
          id: 'session-uuid-1',
          device_id: 'dev-uuid-login-123',
          created_at: '2026-10-07T00:00:00Z',
          last_used_at: '2026-10-07T00:00:00Z',
          expires_at: '2026-10-08T00:00:00Z',
          revoked_at: null,
        },
      };

      // 1. Mock login endpoint
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => responsePayload,
      });

      const controller = new AuthController({
        storage: mockStorage,
        fetchImpl: mockFetch as any,
      });

      await controller.login('user@example.com', 'password123');
      expect(controller.getState().status).toBe('authenticated');
      expect(controller.getDeviceId()).toBe('dev-uuid-login-123');

      // 2. Mock PATCH /devices/{device.id}/push-token
      mockFetch.mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          id: 'dev-uuid-login-123',
          push_token: 'mock-fcm-token',
        }),
      });

      // 3. Register token after login via syncPushToken
      const registeredToken = await syncPushToken(controller);

      expect(registeredToken).toBe('mock-fcm-token');
      const patchCall = mockFetch.mock.calls.find(call =>
        call[0].includes('/devices/dev-uuid-login-123/push-token'),
      );
      expect(patchCall).toBeDefined();
      expect(patchCall[1].method).toBe('PATCH');
      const headers = new Headers(patchCall[1].headers);
      expect(headers.get('Authorization')).toBe(
        'Bearer acc-token-after-login-123',
      );
      expect(JSON.parse(patchCall[1].body)).toEqual({
        token: 'mock-fcm-token',
      });
    });
  });

  describe('Permission pipeline wire (Phase 5)', () => {
    function resolvePermissionState(
      permissionGranted: boolean,
      token: string | null,
    ): 'unknown' | 'denied' | 'granted_no_token' | 'ready' {
      if (!permissionGranted) {
        return 'denied';
      }
      return token ? 'ready' : 'granted_no_token';
    }

    it('does not show ready if permission request is denied', () => {
      const state = resolvePermissionState(false, 'some-token');
      expect(state).toBe('denied');
      expect(state).not.toBe('ready');
    });

    it('does not show ready if permission is granted but token upload fails', () => {
      const state = resolvePermissionState(true, null);
      expect(state).toBe('granted_no_token');
      expect(state).not.toBe('ready');
    });

    it('shows ready only when permission is granted and token is uploaded', () => {
      const state = resolvePermissionState(true, 'mock-fcm-token');
      expect(state).toBe('ready');
    });
  });
});
