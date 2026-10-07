/* eslint-env jest */

jest.mock('react-native-safe-area-context', () => {
  const React = require('react');
  const { View } = require('react-native');

  return {
    SafeAreaProvider: ({ children }) => children,
    SafeAreaView: props => React.createElement(View, props),
    useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
  };
});

jest.mock('@react-native-firebase/app', () => ({
  initializeApp: jest.fn(),
  apps: [],
}));

jest.mock('@react-native-firebase/messaging', () => {
  const mockMessagingInstance = {
    getToken: jest.fn().mockResolvedValue('mock-fcm-token'),
    onTokenRefresh: jest.fn().mockReturnValue(jest.fn()),
    onMessage: jest.fn().mockReturnValue(jest.fn()),
    onNotificationOpenedApp: jest.fn().mockReturnValue(jest.fn()),
    getInitialNotification: jest.fn().mockResolvedValue(null),
    setBackgroundMessageHandler: jest.fn(),
  };

  const messaging = () => mockMessagingInstance;
  const getMessaging = jest.fn(() => mockMessagingInstance);
  const getToken = jest.fn().mockResolvedValue('mock-fcm-token');
  const onTokenRefresh = jest.fn().mockReturnValue(jest.fn());
  const onMessage = jest.fn().mockReturnValue(jest.fn());
  const onNotificationOpenedApp = jest.fn().mockReturnValue(jest.fn());
  const getInitialNotification = jest.fn().mockResolvedValue(null);
  const setBackgroundMessageHandler = jest.fn();

  return {
    __esModule: true,
    default: messaging,
    getMessaging,
    getToken,
    onTokenRefresh,
    onMessage,
    onNotificationOpenedApp,
    getInitialNotification,
    setBackgroundMessageHandler,
  };
});

jest.mock('@notifee/react-native', () => ({
  createChannel: jest.fn(async channel => channel?.id ?? 'reminders'),
  displayNotification: jest.fn().mockResolvedValue('notification-id'),
  onForegroundEvent: jest.fn().mockReturnValue(jest.fn()),
  onBackgroundEvent: jest.fn().mockReturnValue(jest.fn()),
  getInitialNotification: jest.fn().mockResolvedValue(null),
  AndroidImportance: {
    HIGH: 4,
    DEFAULT: 3,
  },
  EventType: {
    PRESS: 1,
  },
}));

