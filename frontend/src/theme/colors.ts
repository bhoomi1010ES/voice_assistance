export const colors = {
  light: {
    // Canvas & Surfaces
    background: '#F6F8FC',
    surface: '#FFFFFF',
    surfaceMuted: '#EEF3F9',
    surfaceLow: '#F2F6FB',
    surfaceHigh: '#E7EDF5',
    border: '#E5EBF3',
    borderSubtle: '#EDF1F7',

    // Primary Brand (Blue)
    primary: '#0969F5',
    primaryDark: '#0753C7',
    primaryContainer: '#E3EFFF',
    onPrimary: '#FFFFFF',
    onPrimaryContainer: '#124687',

    // Secondary Accent (Cyan)
    secondary: '#09A8AD',
    secondaryLight: '#35CED5',
    secondaryContainer: '#E0F7F8',
    onSecondary: '#FFFFFF',
    onSecondaryContainer: '#087780',

    // Tertiary (Slate)
    tertiary: '#697994',
    tertiaryContainer: '#E8EEF6',
    onTertiary: '#FFFFFF',

    // Content / Text
    text: '#10152E',
    textMuted: '#748096',
    textSubtle: '#8A96AA',
    textInverse: '#F8FAFD',

    // Backward-compatible aliases for existing screens
    accent: '#0969F5',
    accentText: '#FFFFFF',

    // Semantic States
    success: '#177850',
    successContainer: '#DDF5E9',
    warning: '#A65608',
    warningContainer: '#FFF1D5',
    error: '#D92336',
    errorContainer: '#FDE8EC',
    disabled: '#BDCADC',

    // Voice Orb Identity
    orbGradientStart: '#0863BD',
    orbGradientMiddle: '#008D9C',
    orbGradientEnd: '#27E0E5',
    orbGlow: 'rgba(9, 168, 173, 0.32)',
    orbRipple: 'rgba(53, 206, 213, 0.16)',
  },
  dark: {
    // Canvas & Surfaces
    background: '#0B1220',
    surface: '#142033',
    surfaceMuted: '#1B2A40',
    surfaceLow: '#17263A',
    surfaceHigh: '#24364F',
    border: '#2C405D',
    borderSubtle: '#20324B',

    // Primary Brand (Blue)
    primary: '#78ADFF',
    primaryDark: '#A3C8FF',
    primaryContainer: '#173D70',
    onPrimary: '#07182F',
    onPrimaryContainer: '#D6E7FF',

    // Secondary Accent
    secondary: '#45D4DC',
    secondaryLight: '#79E7EC',
    secondaryContainer: '#0A3F49',
    onSecondary: '#002E34',
    onSecondaryContainer: '#B7F4F6',

    // Tertiary
    tertiary: '#AFBED4',
    tertiaryContainer: '#2C3D57',
    onTertiary: '#102037',

    // Content / Text
    text: '#EFF5FF',
    textMuted: '#AFBDD2',
    textSubtle: '#8497B2',
    textInverse: '#0B1220',

    // Backward-compatible aliases for existing screens
    accent: '#78ADFF',
    accentText: '#07182F',

    // Semantic States
    success: '#78D7AA',
    successContainer: '#104C37',
    warning: '#F5CB7B',
    warningContainer: '#664310',
    error: '#FF9CA8',
    errorContainer: '#652535',
    disabled: '#42546F',

    // Voice Orb Identity
    orbGradientStart: '#0755A2',
    orbGradientMiddle: '#008D9C',
    orbGradientEnd: '#35DCE5',
    orbGlow: 'rgba(53, 206, 213, 0.30)',
    orbRipple: 'rgba(53, 206, 213, 0.16)',
  },
} as const;

export type ThemeMode = keyof typeof colors;
export type ColorTokens = (typeof colors)[ThemeMode];
