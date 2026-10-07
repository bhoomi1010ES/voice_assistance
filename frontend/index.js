/**
 * @format
 */

import { AppRegistry } from 'react-native';
import {
  getMessaging,
  setBackgroundMessageHandler,
} from '@react-native-firebase/messaging';
import App from './App';
import { name as appName } from './app.json';
import { handleBackgroundRemoteMessage } from './src/notifications/PushNotificationService';

try {
  setBackgroundMessageHandler(getMessaging(), handleBackgroundRemoteMessage);
} catch {
  // Graceful fallback
}

AppRegistry.registerComponent(appName, () => App);
