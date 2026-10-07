import {
  getInitialNotification,
  getMessaging,
  getToken,
  onMessage,
  onNotificationOpenedApp,
  onTokenRefresh,
  type RemoteMessage,
} from '@react-native-firebase/messaging';
import notifee, { AndroidImportance, EventType } from '@notifee/react-native';
import { PermissionsAndroid, Platform } from 'react-native';
import { AuthController } from '../auth/AuthController';

export const REMINDERS_CHANNEL_ID = 'reminders';
export const TASKS_CHANNEL_ID = 'tasks';

// Bounded in-memory set to prevent duplicate presentation if FCM redelivers
const SEEN_DELIVERY_IDS = new Set<string>();
const MAX_SEEN_DELIVERY_IDS = 1000;

export function recordAndCheckDeliveryId(
  deliveryId: string | undefined | null,
): boolean {
  if (!deliveryId) {
    return false;
  }
  if (SEEN_DELIVERY_IDS.has(deliveryId)) {
    return true; // Already processed
  }
  if (SEEN_DELIVERY_IDS.size >= MAX_SEEN_DELIVERY_IDS) {
    const oldestKey = SEEN_DELIVERY_IDS.keys().next().value;
    if (oldestKey) {
      SEEN_DELIVERY_IDS.delete(oldestKey);
    }
  }
  SEEN_DELIVERY_IDS.add(deliveryId);
  return false;
}

export function clearSeenDeliveryIds(): void {
  SEEN_DELIVERY_IDS.clear();
}

export type NotificationTapData = {
  kind?: string;
  [key: string]: any;
};

export type NotificationTapHandler = (data?: NotificationTapData) => void;
let globalNotificationTapHandler: NotificationTapHandler | null = null;
let pendingNotificationTapData: NotificationTapData | null = null;

export function setNotificationTapHandler(
  handler: NotificationTapHandler | null,
): void {
  globalNotificationTapHandler = handler;
  if (handler && pendingNotificationTapData !== null) {
    const data = pendingNotificationTapData;
    pendingNotificationTapData = null;
    handler(data);
  }
}

export function triggerNotificationTap(data?: NotificationTapData): void {
  if (globalNotificationTapHandler) {
    globalNotificationTapHandler(data);
  } else {
    pendingNotificationTapData = data ?? {};
  }
}

export async function createRemindersNotificationChannel(): Promise<string> {
  try {
    return await notifee.createChannel({
      id: REMINDERS_CHANNEL_ID,
      name: 'Reminders',
      importance: AndroidImportance.HIGH,
      sound: 'default',
      vibration: true,
    });
  } catch {
    return REMINDERS_CHANNEL_ID;
  }
}

export async function createTasksNotificationChannel(): Promise<string> {
  try {
    return await notifee.createChannel({
      id: TASKS_CHANNEL_ID,
      name: 'Tasks',
      importance: AndroidImportance.HIGH,
      sound: 'default',
      vibration: true,
    });
  } catch {
    return TASKS_CHANNEL_ID;
  }
}

export async function displayLocalTaskCreatedNotification(options?: {
  title?: string;
  body?: string;
}): Promise<void> {
  const hasPermission = await requestNotificationPermission();
  if (!hasPermission) {
    return;
  }

  const title = options?.title || 'Task created';
  const body = options?.body || '';

  await createTasksNotificationChannel();

  try {
    await notifee.displayNotification({
      title,
      body,
      android: {
        channelId: TASKS_CHANNEL_ID,
        importance: AndroidImportance.HIGH,
        pressAction: {
          id: 'default',
        },
      },
      data: {
        kind: 'task_created',
      },
    });
  } catch {
    // Best-effort local notification display
  }
}

export async function requestNotificationPermission(): Promise<boolean> {
  if (Platform.OS !== 'android') {
    return true;
  }
  if (Number(Platform.Version) < 33) {
    return true;
  }
  try {
    const granted = await PermissionsAndroid.request(
      PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS,
    );
    return granted === PermissionsAndroid.RESULTS.GRANTED;
  } catch {
    return false;
  }
}

export async function syncPushToken(
  authController: AuthController,
): Promise<string | null> {
  const deviceId = authController.getDeviceId();
  if (!deviceId) {
    return null;
  }

  const hasPermission = await requestNotificationPermission();
  if (!hasPermission) {
    return null;
  }

  try {
    const messaging = getMessaging();
    const token = await getToken(messaging);
    if (token) {
      await authController.updatePushToken(token);
      return token;
    }
  } catch {
    // Graceful fallback if Play services is unavailable
  }
  return null;
}

export function subscribeToTokenRefresh(
  authController: AuthController,
): () => void {
  try {
    const messaging = getMessaging();
    return onTokenRefresh(messaging, async (newToken: string) => {
      try {
        await authController.updatePushToken(newToken);
      } catch {
        // Ignored if network or session unavailable
      }
    });
  } catch {
    return () => {};
  }
}

export async function handleForegroundRemoteMessage(
  remoteMessage: RemoteMessage,
): Promise<void> {
  const deliveryId = remoteMessage.data?.delivery_id as string | undefined;
  if (deliveryId && recordAndCheckDeliveryId(deliveryId)) {
    return; // Duplicate delivery ignored
  }

  const title =
    remoteMessage.notification?.title ||
    (remoteMessage.data?.title as string | undefined) ||
    'Reminder';
  const body =
    remoteMessage.notification?.body ||
    (remoteMessage.data?.body as string | undefined) ||
    '';

  await createRemindersNotificationChannel();

  try {
    await notifee.displayNotification({
      title,
      body,
      android: {
        channelId: REMINDERS_CHANNEL_ID,
        importance: AndroidImportance.HIGH,
        pressAction: {
          id: 'default',
        },
      },
      data: remoteMessage.data,
    });
  } catch {
    // Best-effort banner display
  }
}

export async function handleBackgroundRemoteMessage(
  remoteMessage: RemoteMessage,
): Promise<void> {
  const deliveryId = remoteMessage.data?.delivery_id as string | undefined;
  if (deliveryId) {
    recordAndCheckDeliveryId(deliveryId);
  }
}

export function setupPushNotificationListeners(
  authController: AuthController,
  onNavigate?: NotificationTapHandler,
): () => void {
  if (onNavigate) {
    setNotificationTapHandler(onNavigate);
  }

  // Ensure high-importance notification channels exist
  createRemindersNotificationChannel().catch(() => undefined);
  createTasksNotificationChannel().catch(() => undefined);

  // Sync token if already authenticated
  if (authController.getState().status === 'authenticated') {
    syncPushToken(authController).catch(() => undefined);
  }

  // Handle token refresh
  const unsubscribeRefresh = subscribeToTokenRefresh(authController);

  // Foreground notification handler: display banner via Notifee
  let unsubscribeMessage: () => void = () => {};
  try {
    const messaging = getMessaging();
    unsubscribeMessage = onMessage(
      messaging,
      async (remoteMessage: RemoteMessage) => {
        await handleForegroundRemoteMessage(remoteMessage);
      },
    );
  } catch {
    // Messaging listener initialization fallback
  }

  // Notification tap while in background
  let unsubscribeOpenedApp: () => void = () => {};
  try {
    const messaging = getMessaging();
    unsubscribeOpenedApp = onNotificationOpenedApp(
      messaging,
      (_remoteMessage: RemoteMessage) => {
        triggerNotificationTap(_remoteMessage?.data);
      },
    );
  } catch {
    // Ignored
  }

  // Notifee notification press events (both foreground and background)
  let unsubscribeNotifee: () => void = () => {};
  try {
    unsubscribeNotifee = notifee.onForegroundEvent(({ type, detail }) => {
      if (type === EventType.PRESS) {
        triggerNotificationTap(detail?.notification?.data);
      }
    });
  } catch {
    // Ignored
  }

  // Check initial notification that launched the app from killed state
  try {
    const messaging = getMessaging();
    getInitialNotification(messaging)
      .then((initialMessage: RemoteMessage | null) => {
        if (initialMessage) {
          triggerNotificationTap(initialMessage?.data);
        }
      })
      .catch(() => undefined);

    notifee
      .getInitialNotification()
      .then(initialNotifee => {
        if (initialNotifee) {
          triggerNotificationTap(initialNotifee?.notification?.data);
        }
      })
      .catch(() => undefined);
  } catch {
    // Ignored
  }

  return () => {
    unsubscribeRefresh();
    unsubscribeMessage();
    unsubscribeOpenedApp();
    unsubscribeNotifee();
  };
}
