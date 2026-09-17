import React from 'react';
import { StyleSheet, TextInput, View } from 'react-native';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../theme';
import { strings } from '../../i18n/strings';

interface MemorySearchBarProps {
  query: string;
  onQueryChange: (query: string) => void;
  onSearch: () => void;
  searching: boolean;
  enabled: boolean;
}

export function MemorySearchBar({
  query,
  onQueryChange,
  onSearch,
  searching,
  enabled,
}: MemorySearchBarProps) {
  const { colors } = useAppTheme();

  return (
    <Card
      style={[
        styles.card,
        {
          backgroundColor: colors.surface,
          borderColor: colors.border,
        },
      ]}
      testID="memory-search-card"
    >
      <AppText style={[styles.title, { color: colors.text }]}>
        {strings.memory.searchTitle}
      </AppText>

      <View style={styles.inputContainer}>
        <TextInput
          accessibilityLabel={strings.memory.searchLabel}
          editable={enabled && !searching}
          onChangeText={onQueryChange}
          onSubmitEditing={onSearch}
          placeholder={strings.memory.searchPlaceholder}
          placeholderTextColor={colors.textSubtle}
          returnKeyType="search"
          style={[
            styles.input,
            {
              color: colors.text,
              borderColor: colors.border,
              backgroundColor: colors.surfaceLow,
            },
          ]}
          testID="memory-search-input"
          value={query}
        />
      </View>

      <ActionButton
        disabled={!enabled || searching}
        label={searching ? strings.memory.searching : strings.memory.search}
        onPress={onSearch}
        style={styles.searchButton}
        testID="memory-search"
        variant="primary"
      />
    </Card>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: radii.md,
    borderWidth: 1,
    gap: spacing.sm,
    padding: spacing.md,
    ...shadows.sm,
  },
  title: {
    fontSize: typography.body,
    fontWeight: '700',
  },
  inputContainer: {
    marginTop: spacing.xs,
  },
  input: {
    borderRadius: radii.sm,
    borderWidth: 1,
    fontSize: typography.body,
    minHeight: 48,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  searchButton: {
    marginTop: spacing.xs,
  },
});
