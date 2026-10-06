import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Modal,
  ScrollView,
  StyleSheet,
  View,
} from 'react-native';
import { useAuth } from '../../auth/AuthProvider';
import { archivePlan, getPlan, listPlanTasks } from '../../plans/api';
import { PlanContextItem, PlanDetail } from '../../plans/types';
import { completeTask, deleteTask } from '../../tasks/api';
import { Task } from '../../tasks/types';
import { strings } from '../../i18n/strings';
import { ActionButton, AppText, Card } from '../ui/Primitives';
import { useAppTheme } from '../../design/ThemeProvider';
import { radii, shadows, spacing, typography } from '../../design/tokens';

export type PlanDetailModalProps = {
  planId: string | null;
  visible: boolean;
  onClose: () => void;
  onPlanArchived?: (planId: string) => void;
};

export function PlanDetailModal({
  planId,
  visible,
  onClose,
  onPlanArchived,
}: PlanDetailModalProps) {
  const { controller } = useAuth();
  const { colors } = useAppTheme();

  const [detail, setDetail] = useState<PlanDetail | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!planId) return;
    setLoading(true);
    setError(null);
    try {
      const [fetchedDetail, fetchedTasks] = await Promise.all([
        getPlan(controller, planId),
        listPlanTasks(controller, planId),
      ]);
      setDetail(fetchedDetail);
      setTasks(fetchedTasks);
    } catch (err: any) {
      setError(err?.message ?? strings.planning.loadError);
    } finally {
      setLoading(false);
    }
  }, [controller, planId]);

  useEffect(() => {
    if (visible && planId) {
      loadData().catch(() => undefined);
    }
  }, [visible, planId, loadData]);

  if (!visible) return null;

  // Group tasks into Today, Up next, Scheduled, No deadline, Completed
  const now = new Date();
  const todayStr = now.toISOString().slice(0, 10);
  const in7Days = new Date(now.getTime() + 7 * 86400000).toISOString().slice(0, 10);

  const completedTasks = tasks.filter(t => t.status === 'completed');
  const activeTasks = tasks.filter(t => t.status !== 'completed');

  const todayTasks: Task[] = [];
  const upNextTasks: Task[] = [];
  const scheduledTasks: Task[] = [];
  const undatedTasks: Task[] = [];

  for (const t of activeTasks) {
    if (!t.due_at) {
      undatedTasks.push(t);
    } else {
      const dueDay = t.due_at.slice(0, 10);
      if (dueDay <= todayStr) {
        todayTasks.push(t);
      } else if (dueDay <= in7Days) {
        upNextTasks.push(t);
      } else {
        scheduledTasks.push(t);
      }
    }
  }

  const totalTasksCount = tasks.length;
  const completedCount = completedTasks.length;
  const progressPercent = totalTasksCount > 0 ? Math.round((completedCount / totalTasksCount) * 100) : 0;

  const handleToggleComplete = async (task: Task) => {
    try {
      if (task.status === 'completed') {
        // Already completed
        return;
      }
      await completeTask(controller, task.id);
      await loadData();
    } catch {
      // Refresh to ensure state is authoritative
      await loadData();
    }
  };

  const handleDeleteTask = (task: Task) => {
    Alert.alert(
      strings.tasks.confirmTitle,
      `${strings.tasks.confirmBody} "${task.title}"`,
      [
        { text: strings.tasks.cancel, style: 'cancel' },
        {
          text: strings.tasks.delete,
          style: 'destructive',
          onPress: async () => {
            try {
              await deleteTask(controller, task.id);
              await loadData();
            } catch {
              await loadData();
            }
          },
        },
      ],
    );
  };

  const handleArchivePlan = () => {
    if (!detail) return;
    Alert.alert(
      strings.planning.confirmArchiveTitle,
      strings.planning.confirmArchiveBody,
      [
        { text: strings.tasks.cancel, style: 'cancel' },
        {
          text: strings.planning.archivePlan,
          style: 'destructive',
          onPress: async () => {
            try {
              await archivePlan(controller, detail.plan.id, detail.plan.revision);
              Alert.alert(strings.planning.planArchived);
              onPlanArchived?.(detail.plan.id);
              onClose();
            } catch (err: any) {
              Alert.alert(
                err?.code === 'REVISION_CONFLICT'
                  ? strings.planning.staleUndoNotice
                  : 'Failed to archive plan',
              );
            }
          },
        },
      ],
    );
  };

  const renderTaskSection = (title: string, sectionTasks: Task[], testIdPrefix: string) => {
    if (sectionTasks.length === 0) return null;
    return (
      <View style={styles.sectionContainer} testID={`section-${testIdPrefix}`}>
        <AppText style={[styles.sectionHeading, { color: colors.textSubtle }]}>
          {title} ({sectionTasks.length})
        </AppText>
        <View style={styles.taskList}>
          {sectionTasks.map(task => (
            <View
              key={task.id}
              style={[styles.taskItem, { backgroundColor: colors.surfaceMuted }]}
              testID={`task-${task.id}`}
            >
              <View style={styles.taskInfo}>
                <AppText
                  style={[
                    styles.taskTitle,
                    task.status === 'completed' && styles.taskTitleCompleted,
                  ]}
                >
                  {task.title}
                </AppText>
                {task.due_at ? (
                  <AppText style={[styles.taskDue, { color: colors.textSubtle }]}>
                    {task.due_at.replace('T', ' ').slice(0, 16)}
                  </AppText>
                ) : null}
              </View>

              <View style={styles.taskActions}>
                {task.status !== 'completed' ? (
                  <ActionButton
                    label="✓"
                    variant="quiet"
                    onPress={() => handleToggleComplete(task)}
                    accessibilityLabel={`${strings.tasks.complete}: ${task.title}`}
                    testID={`complete-task-${task.id}`}
                  />
                ) : null}
                <ActionButton
                  label="🗑"
                  variant="quiet"
                  onPress={() => handleDeleteTask(task)}
                  accessibilityLabel={`${strings.tasks.delete}: ${task.title}`}
                  testID={`delete-task-${task.id}`}
                />
              </View>
            </View>
          ))}
        </View>
      </View>
    );
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <View style={styles.modalOverlay}>
        <View style={[styles.modalContent, { backgroundColor: colors.surface }]}>
          {/* Header */}
          <View style={styles.header}>
            <View style={styles.headerTitleGroup}>
              <AppText style={styles.modalTitle} testID="plan-detail-title">
                {detail?.plan.name ?? strings.planning.planDetails}
              </AppText>
              {detail ? (
                <AppText style={[styles.revisionText, { color: colors.textSubtle }]}>
                  rev {detail.plan.revision} • {detail.plan.timezone}
                </AppText>
              ) : null}
            </View>
            <ActionButton label="✕" variant="quiet" onPress={onClose} testID="close-plan-detail-btn" />
          </View>

          {loading && !detail ? (
            <AppText style={[styles.loadingText, { color: colors.textSubtle }]}>
              {strings.planning.loading}
            </AppText>
          ) : error ? (
            <AppText accessibilityRole="alert" style={[styles.errorText, { color: colors.error }]}>
              {error}
            </AppText>
          ) : detail ? (
            <ScrollView style={styles.scrollArea} contentContainerStyle={styles.scrollContent}>
              {/* Goal & Deadline Card */}
              {detail.plan.goal || detail.plan.deadline_at ? (
                <Card style={styles.infoCard}>
                  {detail.plan.goal ? (
                    <View style={styles.infoRow}>
                      <AppText style={[styles.infoLabel, { color: colors.textSubtle }]}>
                        {strings.planning.goal}:
                      </AppText>
                      <AppText style={styles.infoValue}>{detail.plan.goal}</AppText>
                    </View>
                  ) : null}
                  {detail.plan.deadline_at ? (
                    <View style={styles.infoRow}>
                      <AppText style={[styles.infoLabel, { color: colors.textSubtle }]}>
                        {strings.planning.deadline}:
                      </AppText>
                      <AppText style={styles.infoValue}>
                        {detail.plan.deadline_at.replace('T', ' ').slice(0, 16)}
                      </AppText>
                    </View>
                  ) : null}
                </Card>
              ) : null}

              {/* Progress Summary */}
              <Card style={styles.progressCard} testID="plan-progress-card">
                <View style={styles.progressHeader}>
                  <AppText style={styles.progressTitle}>{strings.planning.progress}</AppText>
                  <AppText style={styles.progressPercent}>{progressPercent}%</AppText>
                </View>
                <AppText style={[styles.progressSubtitle, { color: colors.textSubtle }]}>
                  {completedCount} of {totalTasksCount} tasks completed
                </AppText>
                <View style={[styles.progressBarBackground, { backgroundColor: colors.surfaceMuted }]}>
                  <View
                    style={[
                      styles.progressBarFill,
                      {
                        backgroundColor: colors.primary,
                        width: `${progressPercent}%`,
                      },
                    ]}
                  />
                </View>
              </Card>

              {/* Grouped Tasks */}
              {renderTaskSection(strings.planning.today, todayTasks, 'today')}
              {renderTaskSection(strings.planning.upNext, upNextTasks, 'up-next')}
              {renderTaskSection(strings.planning.scheduled, scheduledTasks, 'scheduled')}
              {renderTaskSection(strings.planning.noDeadline, undatedTasks, 'undated')}
              {renderTaskSection(strings.planning.completed, completedTasks, 'completed')}

              {/* Context Notes */}
              {detail.context.length > 0 ? (
                <View style={styles.sectionContainer} testID="plan-context-section">
                  <AppText style={[styles.sectionHeading, { color: colors.textSubtle }]}>
                    {strings.planning.contextNotes} ({detail.context.length})
                  </AppText>
                  <View style={styles.contextList}>
                    {detail.context.map(item => (
                      <View
                        key={item.id}
                        style={[styles.contextItem, { backgroundColor: colors.surfaceMuted }]}
                        testID={`context-${item.id}`}
                      >
                        <AppText style={styles.contextContent}>{item.content}</AppText>
                        <AppText style={[styles.contextMeta, { color: colors.textSubtle }]}>
                          {item.kind} • {item.created_at.slice(0, 10)}
                        </AppText>
                      </View>
                    ))}
                  </View>
                </View>
              ) : null}

              {/* Archive Plan Button */}
              {detail.plan.status === 'active' ? (
                <View style={styles.archiveContainer}>
                  <ActionButton
                    label={strings.planning.archivePlan}
                    variant="secondary"
                    onPress={handleArchivePlan}
                    testID="archive-plan-btn"
                  />
                </View>
              ) : null}
            </ScrollView>
          ) : null}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalOverlay: {
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    flex: 1,
    justifyContent: 'flex-end',
  },
  modalContent: {
    borderTopLeftRadius: radii.lg,
    borderTopRightRadius: radii.lg,
    maxHeight: '90%',
    minHeight: '60%',
    padding: spacing.md,
    ...shadows.lg,
  },
  header: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: spacing.sm,
  },
  headerTitleGroup: {
    flex: 1,
  },
  modalTitle: {
    fontSize: typography.heading,
    fontWeight: '700',
  },
  revisionText: {
    fontSize: typography.caption,
    marginTop: 2,
  },
  loadingText: {
    alignSelf: 'center',
    marginTop: spacing.xl,
  },
  errorText: {
    alignSelf: 'center',
    marginTop: spacing.xl,
  },
  scrollArea: {
    flex: 1,
  },
  scrollContent: {
    gap: spacing.md,
    paddingBottom: spacing.xl,
  },
  infoCard: {
    gap: spacing.xs,
    padding: spacing.sm,
  },
  infoRow: {
    flexDirection: 'row',
    gap: spacing.xs,
  },
  infoLabel: {
    fontSize: typography.body,
    fontWeight: '600',
  },
  infoValue: {
    flex: 1,
    fontSize: typography.body,
  },
  progressCard: {
    gap: spacing.xs,
    padding: spacing.sm,
  },
  progressHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  progressTitle: {
    fontSize: typography.label,
    fontWeight: '600',
  },
  progressPercent: {
    fontSize: typography.label,
    fontWeight: '700',
  },
  progressSubtitle: {
    fontSize: typography.caption,
  },
  progressBarBackground: {
    borderRadius: radii.full,
    height: 8,
    marginTop: 4,
    overflow: 'hidden',
    width: '100%',
  },
  progressBarFill: {
    borderRadius: radii.full,
    height: '100%',
  },
  sectionContainer: {
    gap: spacing.xs,
  },
  sectionHeading: {
    fontSize: typography.caption,
    fontWeight: '700',
    textTransform: 'uppercase',
  },
  taskList: {
    gap: spacing.xs,
  },
  taskItem: {
    alignItems: 'center',
    borderRadius: radii.md,
    flexDirection: 'row',
    justifyContent: 'space-between',
    padding: spacing.sm,
  },
  taskInfo: {
    flex: 1,
    gap: 2,
  },
  taskTitle: {
    fontSize: typography.body,
    fontWeight: '500',
  },
  taskTitleCompleted: {
    opacity: 0.6,
    textDecorationLine: 'line-through',
  },
  taskDue: {
    fontSize: typography.caption,
  },
  taskActions: {
    flexDirection: 'row',
    gap: spacing.xs,
  },
  contextList: {
    gap: spacing.xs,
  },
  contextItem: {
    borderRadius: radii.md,
    gap: 2,
    padding: spacing.sm,
  },
  contextContent: {
    fontSize: typography.body,
  },
  contextMeta: {
    fontSize: typography.caption,
  },
  archiveContainer: {
    marginTop: spacing.md,
  },
});
