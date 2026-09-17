import { Platform } from 'react-native';

export const shadows = {
  none: {},
  sm: Platform.select({
    ios: {
      shadowColor: '#161618',
      shadowOffset: { width: 0, height: 1 },
      shadowOpacity: 0.05,
      shadowRadius: 3,
    },
    android: {
      elevation: 1.5,
    },
    default: {},
  }),
  md: Platform.select({
    ios: {
      shadowColor: '#161618',
      shadowOffset: { width: 0, height: 4 },
      shadowOpacity: 0.08,
      shadowRadius: 10,
    },
    android: {
      elevation: 4,
    },
    default: {},
  }),
  lg: Platform.select({
    ios: {
      shadowColor: '#161618',
      shadowOffset: { width: 0, height: 8 },
      shadowOpacity: 0.12,
      shadowRadius: 20,
    },
    android: {
      elevation: 8,
    },
    default: {},
  }),
  glow: Platform.select({
    ios: {
      shadowColor: '#D95C23',
      shadowOffset: { width: 0, height: 0 },
      shadowOpacity: 0.4,
      shadowRadius: 24,
    },
    android: {
      elevation: 12,
    },
    default: {},
  }),
} as const;
