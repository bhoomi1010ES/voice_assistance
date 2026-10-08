import React, { useState } from 'react';
import {
  StyleProp,
  StyleSheet,
  Text,
  TextInput,
  TextInputProps,
  View,
  ViewStyle,
} from 'react-native';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, spacing, typography } from '../../design/tokens';

export type AuthFieldProps = TextInputProps & {
  label: string;
  icon?: string;
  rightElement?: React.ReactNode;
  containerStyle?: StyleProp<ViewStyle>;
  error?: string | null;
};

export function AuthField({
  label,
  icon,
  rightElement,
  containerStyle,
  error,
  style,
  onFocus,
  onBlur,
  ...inputProps
}: AuthFieldProps) {
  const { colors } = useAppTheme();
  const [isFocused, setIsFocused] = useState(false);

  return (
    <View style={[styles.wrapper, containerStyle]}>
      <Text style={[styles.label, { color: colors.textMuted }]}>{label}</Text>

      <View
        style={[
          styles.inputContainer,
          {
            backgroundColor: isFocused ? colors.surface : colors.surfaceLow,
            borderColor: error
              ? colors.error
              : isFocused
              ? colors.primary
              : colors.borderSubtle,
          },
        ]}
      >
        {icon ? (
          <View style={styles.iconContainer}>
            <Text style={[styles.iconText, { color: colors.textSubtle }]}>
              {icon}
            </Text>
          </View>
        ) : null}

        <TextInput
          {...inputProps}
          onBlur={e => {
            setIsFocused(false);
            onBlur?.(e);
          }}
          onFocus={e => {
            setIsFocused(true);
            onFocus?.(e);
          }}
          placeholderTextColor={colors.textSubtle}
          style={[
            styles.textInput,
            { color: colors.text },
            icon ? styles.textInputWithIcon : null,
            rightElement ? styles.textInputWithRight : null,
            style,
          ]}
        />

        {rightElement ? (
          <View style={styles.rightContainer}>{rightElement}</View>
        ) : null}
      </View>

      {error ? (
        <Text style={[styles.errorText, { color: colors.error }]}>{error}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    marginBottom: spacing.md,
  },
  label: {
    fontSize: typography.caption,
    fontWeight: '600',
    letterSpacing: 0.6,
    marginBottom: spacing.xs,
    paddingHorizontal: spacing.xxs,
    textTransform: 'uppercase',
  },
  inputContainer: {
    alignItems: 'center',
    borderRadius: radii.md,
    borderWidth: 1.5,
    flexDirection: 'row',
    minHeight: 52,
    position: 'relative',
  },
  iconContainer: {
    alignItems: 'center',
    alignSelf: 'stretch',
    justifyContent: 'center',
    paddingLeft: spacing.sm,
    width: 36,
  },
  iconText: {
    fontSize: 16,
    lineHeight: 20,
  },
  textInput: {
    flex: 1,
    fontSize: typography.body,
    minHeight: 52,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  textInputWithIcon: {
    paddingLeft: spacing.xs,
  },
  textInputWithRight: {
    paddingRight: spacing.xs,
  },
  rightContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingRight: spacing.xs,
  },
  errorText: {
    fontSize: typography.caption,
    marginTop: spacing.xxs,
    paddingHorizontal: spacing.xxs,
  },
});
