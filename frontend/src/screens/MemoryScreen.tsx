import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { ScrollView, StyleSheet, TextInput, View } from 'react-native';
import { safeUserMessage, toClientError } from '../api/errors';
import {
  ActionButton,
  AppText,
  Card,
  Heading,
  Screen,
  StatusBanner,
} from '../components/ui/Primitives';
import { useAppTheme } from '../design/ThemeProvider';
import { spacing, typography } from '../design/tokens';
import { strings } from '../i18n/strings';
import { useAuth } from '../auth/AuthProvider';
import { useVoiceSocket } from '../voice/VoiceSocketProvider';
import {
  deleteAllMemories,
  deleteMemory,
  createMemory,
  getMemorySettings,
  getVoiceSession,
  listMemories,
  searchMemories,
  setSessionMemoryExclusion,
  updateMemory,
  updateMemorySettings,
} from '../memory/api';
import { MemoryItem, MemorySettings } from '../memory/types';

type Confirmation = 'delete' | 'delete-all' | null;

export function MemoryScreen() {
  const { controller, status } = useAuth();
  const { colors } = useAppTheme();
  const { sessionId } = useVoiceSocket();
  const [settings, setSettings] = useState<MemorySettings | null>(null);
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [selected, setSelected] = useState<MemoryItem | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [newContent, setNewContent] = useState('');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [excluding, setExcluding] = useState(false);
  const [sessionExcluded, setSessionExcluded] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const searchSequence = useRef(0);
  const hasSearched = useRef(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [settingsResult, memoriesResult] = await Promise.allSettled([
      getMemorySettings(controller),
      listMemories(controller),
    ]);
    if (settingsResult.status === 'fulfilled') {
      setSettings(settingsResult.value);
    }
    if (memoriesResult.status === 'fulfilled') {
      setMemories(memoriesResult.value);
    }
    const failure = [settingsResult, memoriesResult].find(
      result => result.status === 'rejected',
    );
    if (failure?.status === 'rejected') {
      setError(safeUserMessage(toClientError(failure.reason)));
    }
    setLoading(false);
  }, [controller]);

  useEffect(() => {
    if (status !== 'authenticated') {
      return;
    }
    load().catch(cause => setError(safeUserMessage(toClientError(cause))));
  }, [load, status]);

  useEffect(() => {
    if (status !== 'authenticated' || !sessionId) {
      setSessionExcluded(false);
      return;
    }
    getVoiceSession(controller, sessionId)
      .then(session => {
        setSessionExcluded(session.client_metadata?.memory_excluded === true);
      })
      .catch(cause => setError(safeUserMessage(toClientError(cause))));
  }, [controller, sessionId, status]);

  const search = useCallback(
    async (normalizedQuery: string, sequence: number) => {
      hasSearched.current = true;
      setSearching(true);
      setError(null);
      try {
        const result = await searchMemories(controller, normalizedQuery);
        if (sequence === searchSequence.current) {
          setMemories(result);
        }
      } catch (cause) {
        if (sequence === searchSequence.current) {
          setError(safeUserMessage(toClientError(cause)));
        }
      } finally {
        if (sequence === searchSequence.current) {
          setSearching(false);
        }
      }
    },
    [controller],
  );

  const runSearch = useCallback(() => {
    const normalizedQuery = query.trim();
    if (searchTimer.current) {
      clearTimeout(searchTimer.current);
      searchTimer.current = null;
    }
    const sequence = ++searchSequence.current;
    if (!normalizedQuery) {
      hasSearched.current = false;
      setSearching(false);
      load().catch(cause => setError(safeUserMessage(toClientError(cause))));
      return;
    }
    search(normalizedQuery, sequence).catch(() => undefined);
  }, [load, query, search]);

  useEffect(() => {
    if (!settings?.enabled || !query.trim()) {
      setSearching(false);
      if (hasSearched.current && !query.trim()) {
        const sequence = ++searchSequence.current;
        hasSearched.current = false;
        load().catch(cause => {
          if (sequence === searchSequence.current) {
            setError(safeUserMessage(toClientError(cause)));
          }
        });
      }
      return;
    }
    const sequence = ++searchSequence.current;
    searchTimer.current = setTimeout(() => {
      searchTimer.current = null;
      search(query.trim(), sequence).catch(() => undefined);
    }, 300);
    return () => {
      if (searchTimer.current) {
        clearTimeout(searchTimer.current);
        searchTimer.current = null;
      }
    };
  }, [load, query, search, settings?.enabled]);

  const selectMemory = (memory: MemoryItem) => {
    setSelected(memory);
    setDraft(memory.content);
    setEditing(false);
    setMessage(null);
    setError(null);
  };

  const save = async () => {
    if (!selected || !draft.trim()) {
      setError(strings.memory.invalidContent);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const replacement = await updateMemory(controller, selected.id, {
        content: draft.trim(),
      });
      setMemories(items =>
        items.map(item => (item.id === selected.id ? replacement : item)),
      );
      setSelected(replacement);
      setDraft(replacement.content);
      setEditing(false);
      setMessage(strings.memory.updated);
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const removeSelected = async () => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      await deleteMemory(controller, selected.id);
      setMemories(items => items.filter(item => item.id !== selected.id));
      setSelected(null);
      setConfirmation(null);
      setMessage(strings.memory.deleted);
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const removeAll = async () => {
    setSaving(true);
    setError(null);
    try {
      await deleteAllMemories(controller);
      setMemories([]);
      setSelected(null);
      setConfirmation(null);
      setMessage(strings.memory.deletedAll);
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const toggleMemory = async () => {
    if (!settings) return;
    setSaving(true);
    setError(null);
    try {
      setSettings(await updateMemorySettings(controller, !settings.enabled));
      setMessage(
        settings.enabled ? strings.memory.disabled : strings.memory.enabled,
      );
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const saveNewMemory = async () => {
    const content = newContent.trim();
    if (!content) {
      setError(strings.memory.invalidContent);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await createMemory(controller, { content });
      setMemories(items => [
        created,
        ...items.filter(item => item.id !== created.id),
      ]);
      setNewContent('');
      setSelected(created);
      setMessage(strings.memory.created);
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setSaving(false);
    }
  };

  const toggleSessionExclusion = async () => {
    if (!sessionId) return;
    setExcluding(true);
    setError(null);
    try {
      const nextValue = !sessionExcluded;
      await setSessionMemoryExclusion(controller, sessionId, nextValue);
      setSessionExcluded(nextValue);
      setMessage(
        nextValue
          ? strings.memory.sessionExcluded
          : strings.memory.sessionIncluded,
      );
    } catch (cause) {
      setError(safeUserMessage(toClientError(cause)));
    } finally {
      setExcluding(false);
    }
  };

  const visibleState = useMemo(() => {
    if (loading) return 'loading';
    if (memories.length === 0) return 'empty';
    return 'success';
  }, [loading, memories.length]);

  return (
    <Screen testID="memory-screen">
      <ScrollView contentContainerStyle={styles.content}>
        <Heading>{strings.memory.title}</Heading>
        <AppText style={styles.body}>{strings.memory.body}</AppText>
        {error ? <StatusBanner tone="error">{error}</StatusBanner> : null}
        {message ? <StatusBanner>{message}</StatusBanner> : null}

        <Card style={styles.card} testID="memory-settings-card">
          <AppText style={styles.sectionTitle}>
            {strings.memory.settings}
          </AppText>
          <AppText>
            {settings?.enabled
              ? strings.memory.enabledDescription
              : settings
              ? strings.memory.disabledDescription
              : strings.memory.loading}
          </AppText>
          <ActionButton
            disabled={!settings || saving}
            label={
              settings?.enabled ? strings.memory.turnOff : strings.memory.turnOn
            }
            onPress={toggleMemory}
            style={styles.action}
            variant="secondary"
            testID="memory-toggle"
          />
        </Card>

        <Card style={styles.card} testID="memory-search-card">
          <AppText style={styles.sectionTitle}>
            {strings.memory.searchTitle}
          </AppText>
          <TextInput
            accessibilityLabel={strings.memory.searchLabel}
            editable={Boolean(settings?.enabled) && !searching}
            onChangeText={setQuery}
            onSubmitEditing={runSearch}
            placeholder={strings.memory.searchPlaceholder}
            placeholderTextColor={colors.textMuted}
            returnKeyType="search"
            style={[
              styles.input,
              { color: colors.text, borderColor: colors.border },
            ]}
            testID="memory-search-input"
            value={query}
          />
          <ActionButton
            disabled={!settings?.enabled || searching}
            label={searching ? strings.memory.searching : strings.memory.search}
            onPress={runSearch}
            style={styles.action}
            testID="memory-search"
          />
        </Card>

        <Card style={styles.card} testID="memory-create-card">
          <AppText style={styles.sectionTitle}>
            {strings.memory.createTitle}
          </AppText>
          <TextInput
            accessibilityLabel={strings.memory.contentLabel}
            editable={Boolean(settings?.enabled) && !saving}
            multiline
            onChangeText={setNewContent}
            placeholder={strings.memory.createPlaceholder}
            placeholderTextColor={colors.textMuted}
            style={[
              styles.input,
              styles.multilineInput,
              { color: colors.text, borderColor: colors.border },
            ]}
            testID="memory-create-input"
            value={newContent}
          />
          <ActionButton
            disabled={!settings?.enabled || saving || !newContent.trim()}
            label={saving ? strings.memory.creating : strings.memory.create}
            onPress={saveNewMemory}
            style={styles.action}
            testID="memory-create"
          />
        </Card>

        {sessionId ? (
          <Card style={styles.card} testID="memory-session-card">
            <AppText style={styles.sectionTitle}>
              {strings.memory.sessionTitle}
            </AppText>
            <AppText>{strings.memory.sessionBody}</AppText>
            <ActionButton
              disabled={excluding}
              label={
                sessionExcluded
                  ? strings.memory.includeSession
                  : strings.memory.excludeSession
              }
              onPress={toggleSessionExclusion}
              style={styles.action}
              variant="secondary"
              testID="memory-session-exclusion"
            />
          </Card>
        ) : null}

        <View style={styles.listHeader}>
          <AppText style={styles.sectionTitle}>
            {strings.memory.listTitle}
          </AppText>
          <ActionButton
            disabled={saving || memories.length === 0}
            label={strings.memory.deleteAll}
            onPress={() => setConfirmation('delete-all')}
            variant="quiet"
            testID="memory-delete-all"
          />
        </View>

        {visibleState === 'loading' ? (
          <AppText>{strings.memory.loading}</AppText>
        ) : null}
        {visibleState === 'empty' ? (
          <Card style={styles.card} testID="memory-empty">
            <AppText>{strings.memory.empty}</AppText>
          </Card>
        ) : null}
        {memories.map(memory => (
          <Card key={memory.id} style={styles.card} testID="memory-item">
            <AppText style={styles.memoryType}>
              {memoryTypeLabel(memory.memory_type)}
            </AppText>
            {memory.supersedes_id ? (
              <AppText style={styles.replacementNotice}>
                {strings.memory.replacementNotice}
              </AppText>
            ) : null}
            <AppText
              accessibilityLabel={`${strings.memory.itemLabel}: ${memory.content}`}
              style={styles.memoryContent}
              testID="memory-item-content"
            >
              {memory.content}
            </AppText>
            <ActionButton
              label={strings.memory.view}
              onPress={() => selectMemory(memory)}
              style={styles.action}
              variant="secondary"
              testID="memory-view"
            />
          </Card>
        ))}

        {selected ? (
          <Card style={styles.card} testID="memory-detail">
            <AppText style={styles.sectionTitle}>
              {strings.memory.detailTitle}
            </AppText>
            <AppText style={styles.memoryType}>
              {memoryTypeLabel(selected.memory_type)}
            </AppText>
            {selected.supersedes_id ? (
              <AppText style={styles.replacementNotice}>
                {strings.memory.replacementNotice}
              </AppText>
            ) : null}
            {editing ? (
              <TextInput
                accessibilityLabel={strings.memory.contentLabel}
                multiline
                onChangeText={setDraft}
                style={[
                  styles.input,
                  styles.multilineInput,
                  { color: colors.text, borderColor: colors.border },
                ]}
                testID="memory-edit-input"
                value={draft}
              />
            ) : (
              <AppText style={styles.memoryContent}>{selected.content}</AppText>
            )}
            <View style={styles.actions}>
              {editing ? (
                <>
                  <ActionButton
                    disabled={saving}
                    label={saving ? strings.memory.saving : strings.memory.save}
                    onPress={save}
                    style={styles.actionHalf}
                    testID="memory-save"
                  />
                  <ActionButton
                    disabled={saving}
                    label={strings.memory.cancel}
                    onPress={() => {
                      setDraft(selected.content);
                      setEditing(false);
                    }}
                    style={styles.actionHalf}
                    variant="secondary"
                    testID="memory-cancel"
                  />
                </>
              ) : (
                <>
                  <ActionButton
                    label={strings.memory.edit}
                    onPress={() => setEditing(true)}
                    style={styles.actionHalf}
                    variant="secondary"
                    testID="memory-edit"
                  />
                  <ActionButton
                    disabled={saving}
                    label={strings.memory.delete}
                    onPress={() => setConfirmation('delete')}
                    style={styles.actionHalf}
                    variant="quiet"
                    testID="memory-delete"
                  />
                </>
              )}
            </View>
          </Card>
        ) : null}

        {confirmation ? (
          <Card style={styles.confirmation} testID="memory-confirmation">
            <AppText style={styles.sectionTitle}>
              {confirmation === 'delete'
                ? strings.memory.deleteTitle
                : strings.memory.deleteAllTitle}
            </AppText>
            <AppText>
              {confirmation === 'delete'
                ? strings.memory.deleteBody
                : strings.memory.deleteAllBody}
            </AppText>
            <View style={styles.actions}>
              <ActionButton
                disabled={saving}
                label={strings.memory.cancel}
                onPress={() => setConfirmation(null)}
                style={styles.actionHalf}
                variant="secondary"
                testID="memory-confirm-cancel"
              />
              <ActionButton
                disabled={saving}
                label={strings.memory.confirmDelete}
                onPress={confirmation === 'delete' ? removeSelected : removeAll}
                style={styles.actionHalf}
                variant="quiet"
                testID="memory-confirm-delete"
              />
            </View>
          </Card>
        ) : null}
      </ScrollView>
    </Screen>
  );
}

function memoryTypeLabel(type: MemoryItem['memory_type']): string {
  return type.charAt(0).toUpperCase() + type.slice(1);
}

const styles = StyleSheet.create({
  content: { paddingBottom: spacing.xl },
  body: { marginTop: spacing.sm },
  card: { marginTop: spacing.md },
  sectionTitle: { fontWeight: '700' },
  action: { marginTop: spacing.md },
  input: {
    borderRadius: 8,
    borderWidth: 1,
    fontSize: typography.body,
    minHeight: 48,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  multilineInput: { minHeight: 110, textAlignVertical: 'top' },
  searchTitle: { marginBottom: spacing.sm },
  sessionTitle: { marginBottom: spacing.sm },
  listHeader: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: spacing.lg,
  },
  memoryType: {
    color: '#5D6875',
    fontSize: typography.label,
    fontWeight: '700',
  },
  memoryContent: { marginTop: spacing.xs },
  replacementNotice: { color: '#805A00', fontSize: typography.label },
  actions: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.md },
  actionHalf: { flex: 1 },
  confirmation: { borderColor: '#B42318' },
});
