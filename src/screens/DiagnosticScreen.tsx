import React, {useCallback, useEffect, useState} from 'react';
import {
  Button,
  DeviceEventEmitter,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';
import {
  AudioProcessingCalibrationMode,
  AudioPipelineStatus,
  AudioProcessingStatus,
  beginWakeWordCalibrationTrial,
  getAudioPipelineStatus,
  getAudioProcessingStatus,
  getMicrophoneStatus,
  getWakeWordStatus,
  getWakeWordDiagnosticPcmCaptureStatus,
  MicrophoneStatus,
  requestMicrophonePermission,
  resetWakeWordAcousticDiagnostics,
  replayWakeWordDiagnosticPcm,
  setAudioProcessingCalibrationMode,
  setWakeWordCalibrationMode,
  SileroVadErrorEvent,
  SileroVadEvent,
  startMicrophone,
  startWakeWordDiagnosticPcmCapture,
  stopMicrophone,
  stopWakeWordDiagnosticPcmCapture,
  deleteWakeWordDiagnosticData,
  VadEvent,
  WakeWordDetectionEvent,
  WakeWordDiagnosticCaptureStatus,
  WakeWordReplayBatchResult,
  WakeWordStatus,
  WakeWordStatusEvent,
} from '../native/VoiceModule';

const WAKE_CALIBRATION_CONDITIONS = [
  'QUIET_25CM',
  'QUIET_50CM',
  'QUIET_1M',
  'DEVICE_ORIENTATION',
  'TV_MUSIC',
  'BACKGROUND_NOISE',
  'SPEAKER_PLAYBACK',
  'DIFFERENT_VOICE',
  'AEC_NS_MATRIX',
] as const;

const INITIAL_STATUS: MicrophoneStatus = {
  permissionGranted: false,
  permissionStatus: 'UNKNOWN',
  state: 'IDLE',
  isRecording: false,
  audioRecordInitialized: false,
  sampleRateHz: 16_000,
  channelCount: 1,
  encoding: 'PCM_16BIT_LE_SIGNED',
  bufferSizeBytes: 0,
  minBufferSizeBytes: 0,
  audioSessionId: -1,
  pcmFramesCaptured: 0,
  captureDurationMs: 0,
  microphoneErrorCount: 0,
  lastError: null,
};

const INITIAL_AUDIO_PROCESSING_STATUS: AudioProcessingStatus = {
  audioSessionId: -1,
  aec: {
    supported: false,
    available: false,
    requested: true,
    created: false,
    enabled: false,
    lastError: null,
  },
  noiseSuppression: {
    supported: false,
    available: false,
    requested: true,
    created: false,
    enabled: false,
    lastError: null,
  },
  manufacturer: 'UNKNOWN',
  model: 'UNKNOWN',
  androidSdk: 0,
};

const INITIAL_AUDIO_PIPELINE_STATUS: AudioPipelineStatus = {
  recording: false,
  state: 'IDLE',
  captureStarted: false,
  captureStopped: false,
  sampleRateHz: 16_000,
  channelCount: 1,
  pcmFormat: 'PCM_16BIT_LE_SIGNED',
  frameDurationMs: 20,
  frameSizeSamples: 320,
  frameSizeBytes: 640,
  bufferedFrames: 0,
  bufferedBytes: 0,
  bufferCapacityFrames: 25,
  bufferCapacityBytes: 16_000,
  maxBufferedDurationMs: 500,
  maxObservedBufferedFrames: 0,
  totalPcmFramesCaptured: 0,
  totalPcmBytesProcessed: 0,
  framesWrittenToRingBuffer: 0,
  framesConsumedFromRingBuffer: 0,
  totalFramesProcessed: 0,
  overflowCount: 0,
  invalidReadCount: 0,
  readErrorCount: 0,
  pipelineErrorCount: 0,
  partialFrameSamples: 0,
  vad: {
    enabled: true,
    sessionActive: false,
    state: 'SILENCE',
    thresholdDbFs: -42,
    lastEnergyDbFs: -120,
    lastFrameClassification: 'NON_SPEECH',
    frameDurationMs: 20,
    frameSizeSamples: 320,
    minimumSpeechDurationMs: 100,
    minimumSilenceDurationMs: 300,
    configuredSpeechStartConfirmationFrames: 5,
    configuredSpeechEndConfirmationFrames: 15,
    effectiveSpeechStartConfirmationFrames: 5,
    effectiveSpeechEndConfirmationFrames: 15,
    consecutiveSpeechFrames: 0,
    consecutiveSilenceFrames: 0,
    vadFramesProcessed: 0,
    speechFrames: 0,
    nonSpeechFrames: 0,
    speechSegments: 0,
    currentSpeechDurationMs: 0,
    currentSilenceDurationMs: 0,
    lastSpeechStartedFrameIndex: 0,
    lastSpeechStoppedFrameIndex: 0,
    vadErrorCount: 0,
  },
  sileroVad: {
    enabled: true,
    available: false,
    modelPresent: false,
    modelLoaded: false,
    modelName: 'silero_vad.onnx',
    modelVersion: '6.2.1',
    modelGitTag: 'v6.2.1',
    modelGitCommit: '7e30209',
    modelAssetPath: 'silero_vad/silero_vad.onnx',
    modelFormat: 'ONNX',
    modelSizeBytes: 0,
    modelSha256: null,
    modelSha256Verified: false,
    modelOnnxOpset: 16,
    modelError: 'Approved Silero VAD model asset is missing.',
    runtimeName: 'ONNX_RUNTIME_ANDROID_CPU',
    runtimeVersion: '1.24.3',
    runtimeAvailable: false,
    runtimeInitialized: false,
    inferenceAvailable: false,
    sessionActive: false,
    running: false,
    workerThreadAlive: false,
    lifecycleState: 'IDLE',
    state: 'SILENCE',
    speechProbabilityThreshold: 0.5,
    speechStartConfirmationMs: 96,
    speechStartConfirmationChunks: 3,
    speechStopHangoverMs: 320,
    speechStopConfirmationChunks: 10,
    inputFrameDurationMs: 20,
    inputFrameSizeSamples: 320,
    inferenceChunkDurationMs: 32,
    inferenceChunkSamples: 512,
    modelContextSamples: 64,
    queueDepthFrames: 0,
    queueCapacityFrames: 8,
    queueHighWaterMarkFrames: 0,
    framesOffered: 0,
    framesConsumed: 0,
    droppedFrames: 0,
    malformedFrames: 0,
    inferenceCount: 0,
    successfulInferenceCount: 0,
    failedInferenceCount: 0,
    averageInferenceDurationMs: 0,
    maximumInferenceDurationMs: 0,
    lastInferenceTimestampMs: 0,
    currentProbability: null,
    speechStartCount: 0,
    speechStopCount: 0,
    resetCount: 0,
    errorCount: 0,
    lastErrorCode: null,
    lastErrorMessage: null,
  },
};

const INITIAL_WAKE_WORD_STATUS: WakeWordStatus = {
  enabled: true,
  available: false,
  modelPresent: false,
  modelName: 'hey_mycroft',
  modelVersion: 'v0.1',
  modelReleaseTag: 'v0.5.1',
  modelGitCommit: '1eec2158c5c54150ac5f4c15065adacb1003b1e7',
  modelLicense: 'CC BY-NC-SA 4.0',
  modelFormat: 'ONNX',
  modelAssetDirectory: 'openwakeword',
  missingModelAssets:
    'openwakeword/melspectrogram.onnx, openwakeword/embedding_model.onnx, openwakeword/hey_mycroft_v0.1.onnx',
  modelHashVerified: false,
  classifierSha256: null,
  runtimeName: 'ONNX_RUNTIME_ANDROID_CPU',
  runtimeVersion: '1.24.3',
  runtimeAvailable: true,
  runtimeInitialized: false,
  tensorContractVerified: false,
  sessionActive: false,
  running: false,
  workerThreadAlive: false,
  state: 'IDLE',
  detectionThreshold: 0.5,
  cooldownMs: 2_000,
  cooldownRemainingMs: 0,
  inputFrameDurationMs: 20,
  inputFrameSizeSamples: 320,
  inferenceWindowDurationMs: 80,
  inferenceWindowSamples: 1_280,
  queuedFrames: 0,
  queueCapacityFrames: 8,
  queueHighWaterMarkFrames: 0,
  framesOffered: 0,
  framesConsumed: 0,
  inferenceCount: 0,
  averageInferenceLatencyMs: 0,
  maximumInferenceLatencyMs: 0,
  detectionCount: 0,
  duplicateSuppressionCount: 0,
  droppedFrameCount: 0,
  malformedFrameCount: 0,
  runtimeErrorCount: 0,
  lastDetectionTimestampMs: 0,
  lastConfidence: null,
  pcmContextSamples: 480,
  melHistoryFrames: 76,
  melBins: 32,
  embeddingHistoryFrames: 16,
  embeddingFeatureSize: 96,
  classifierOutputSemantics: 'RAW_SIGMOID_PROBABILITY',
  acousticDiagnostics: {
    available: true,
    enabled: false,
    pcmByteOrder: 'ANDROID_SHORT_ARRAY_SIGNED_PCM16',
    pcmScaling: 'RAW_PCM16_TO_FLOAT32_NO_SCALING',
    byteSwapApplied: false,
    normalizationApplied: false,
    inferenceWindowCount: 0,
    scoreMinimum: null,
    scoreMaximum: null,
    scoreAverage: 0,
    scoreP50: null,
    scoreP90: null,
    scoreP95: null,
    scoreP99: null,
    thresholdCounts: [0.1, 0.2, 0.3, 0.35, 0.4, 0.45, 0.5].map(threshold => ({
      threshold,
      count: 0,
    })),
    lastInferenceTimestampMs: 0,
    lastInferenceIndex: 0,
    lastClassifierScore: null,
    peakClassifierScore: null,
    lastPcmMinimum: null,
    lastPcmMaximum: null,
    lastPcmPeak: 0,
    lastPcmRms: 0,
    lastPcmDbFs: -120,
    maximumObservedPcmRms: 0,
    maximumObservedPcmDbFs: -120,
    clippedSampleCount: 0,
    lastQueueDepthFrames: 0,
    lastInferenceLatencyMs: 0,
    lastAecEnabled: false,
    lastNoiseSuppressionEnabled: false,
    activeTrialLabel: null,
    activeTrialCondition: null,
    activeTrialAttemptNumber: null,
    activeTrialExpectedPositive: null,
    completedPositiveTrials: 0,
    completedNegativeTrials: 0,
    positiveScoreMedian: null,
    positiveScoreMaximum: null,
    negativeScoreMedian: null,
    negativeScoreMaximum: null,
    medianDetectionLatencyMs: null,
    maximumDetectionLatencyMs: null,
    thresholdAnalysis: [0.1, 0.2, 0.3, 0.35, 0.4, 0.45, 0.5].map(
      threshold => ({
        threshold,
        positiveTrials: 0,
        negativeTrials: 0,
        trueAccepts: 0,
        falseRejects: 0,
        falseAccepts: 0,
        trueNegatives: 0,
        duplicateDetections: 0,
        trueAcceptRate: 0,
        falseRejectRate: 0,
        falseAcceptRate: 0,
        duplicateRate: 0,
        medianDetectionLatencyMs: null,
        maximumDetectionLatencyMs: null,
      }),
    ),
    calibrationTrials: [],
  },
  lastErrorCode: null,
  lastErrorMessage: null,
};

const INITIAL_WAKE_CAPTURE_STATUS: WakeWordDiagnosticCaptureStatus = {
  diagnosticOnly: true,
  active: false,
  captureId: null,
  label: null,
  targetDurationMs: 0,
  targetInferenceWindows: 0,
  inferenceWindowsAccepted: 0,
  inferenceWindowsWritten: 0,
  queueDepthWindows: 0,
  queueCapacityWindows: 8,
  queueHighWaterMarkWindows: 0,
  droppedWindows: 0,
  completedCaptureCount: 0,
  lastError: null,
  records: [],
};

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  return String(error);
}

export function DiagnosticScreen() {
  const [status, setStatus] = useState(INITIAL_STATUS);
  const [audioProcessing, setAudioProcessing] = useState(
    INITIAL_AUDIO_PROCESSING_STATUS,
  );
  const [audioPipeline, setAudioPipeline] = useState(
    INITIAL_AUDIO_PIPELINE_STATUS,
  );
  const [wakeWord, setWakeWord] = useState(INITIAL_WAKE_WORD_STATUS);
  const [wakeCapture, setWakeCapture] = useState(INITIAL_WAKE_CAPTURE_STATUS);
  const [wakeReplay, setWakeReplay] =
    useState<WakeWordReplayBatchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [uiError, setUiError] = useState<string | null>(null);
  const [lastVadStartedEvent, setLastVadStartedEvent] =
    useState<VadEvent | null>(null);
  const [lastVadStoppedEvent, setLastVadStoppedEvent] =
    useState<VadEvent | null>(null);
  const [lastSileroStartedEvent, setLastSileroStartedEvent] =
    useState<SileroVadEvent | null>(null);
  const [lastSileroStoppedEvent, setLastSileroStoppedEvent] =
    useState<SileroVadEvent | null>(null);
  const [lastSileroErrorEvent, setLastSileroErrorEvent] =
    useState<SileroVadErrorEvent | null>(null);
  const [lastWakeDetection, setLastWakeDetection] =
    useState<WakeWordDetectionEvent | null>(null);
  const [lastWakeEngineEvent, setLastWakeEngineEvent] = useState('NONE');
  const [wakeCalibrationConditionIndex, setWakeCalibrationConditionIndex] =
    useState(0);
  const wakeCalibrationCondition =
    WAKE_CALIBRATION_CONDITIONS[wakeCalibrationConditionIndex];

  const refreshDiagnostics = useCallback(async () => {
    try {
      const [
        microphoneStatus,
        audioProcessingStatus,
        audioPipelineStatus,
        wakeWordStatus,
        wakeCaptureStatus,
      ] = await Promise.all([
        getMicrophoneStatus(),
        getAudioProcessingStatus(),
        getAudioPipelineStatus(),
        getWakeWordStatus(),
        getWakeWordDiagnosticPcmCaptureStatus(),
      ]);
      setStatus(microphoneStatus);
      setAudioProcessing(audioProcessingStatus);
      setAudioPipeline(audioPipelineStatus);
      setWakeWord(wakeWordStatus);
      setWakeCapture(wakeCaptureStatus);
    } catch (error) {
      setUiError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    refreshDiagnostics();

    const subscriptions = [
      DeviceEventEmitter.addListener(
        'AUDIO_ENGINE_STARTED',
        refreshDiagnostics,
      ),
      DeviceEventEmitter.addListener(
        'AUDIO_ENGINE_STOPPED',
        refreshDiagnostics,
      ),
      DeviceEventEmitter.addListener(
        'AUDIO_ENGINE_ERROR',
        refreshDiagnostics,
      ),
      DeviceEventEmitter.addListener(
        'VAD_SPEECH_STARTED',
        (event: VadEvent) => {
          setLastVadStartedEvent(event);
          refreshDiagnostics();
        },
      ),
      DeviceEventEmitter.addListener(
        'VAD_SPEECH_STOPPED',
        (event: VadEvent) => {
          setLastVadStoppedEvent(event);
          refreshDiagnostics();
        },
      ),
      DeviceEventEmitter.addListener(
        'SILERO_VAD_SPEECH_STARTED',
        (event: SileroVadEvent) => {
          setLastSileroStartedEvent(event);
          refreshDiagnostics();
        },
      ),
      DeviceEventEmitter.addListener(
        'SILERO_VAD_SPEECH_STOPPED',
        (event: SileroVadEvent) => {
          setLastSileroStoppedEvent(event);
          refreshDiagnostics();
        },
      ),
      DeviceEventEmitter.addListener(
        'SILERO_VAD_ERROR',
        (event: SileroVadErrorEvent) => {
          setLastSileroErrorEvent(event);
          refreshDiagnostics();
        },
      ),
      DeviceEventEmitter.addListener(
        'WAKE_WORD_DETECTED',
        (event: WakeWordDetectionEvent) => {
          setLastWakeDetection(event);
          refreshDiagnostics();
        },
      ),
      DeviceEventEmitter.addListener(
        'WAKE_ENGINE_STARTED',
        (event: WakeWordStatusEvent) => {
          setWakeWord(event);
          setLastWakeEngineEvent(event.event);
        },
      ),
      DeviceEventEmitter.addListener(
        'WAKE_ENGINE_STOPPED',
        (event: WakeWordStatusEvent) => {
          setWakeWord(event);
          setLastWakeEngineEvent(event.event);
        },
      ),
      DeviceEventEmitter.addListener(
        'WAKE_ENGINE_ERROR',
        (event: WakeWordStatusEvent) => {
          setWakeWord(event);
          setLastWakeEngineEvent(event.event);
        },
      ),
    ];

    return () => {
      subscriptions.forEach(subscription => subscription.remove());
    };
  }, [refreshDiagnostics]);

  useEffect(() => {
    if (!status.isRecording) {
      return undefined;
    }

    const interval = setInterval(() => {
      refreshDiagnostics();
    }, 1000);

    return () => clearInterval(interval);
  }, [refreshDiagnostics, status.isRecording]);

  const handleRefresh = async () => {
    setBusy(true);
    setUiError(null);
    await refreshDiagnostics();
    setBusy(false);
  };

  const handleStart = async () => {
    setBusy(true);
    setUiError(null);
    setLastVadStartedEvent(null);
    setLastVadStoppedEvent(null);
    setLastSileroStartedEvent(null);
    setLastSileroStoppedEvent(null);
    setLastSileroErrorEvent(null);
    setLastWakeDetection(null);
    setLastWakeEngineEvent('NONE');

    try {
      const permission = await requestMicrophonePermission();
      if (permission !== PermissionsAndroidResult.GRANTED) {
        await refreshDiagnostics();
        setUiError(`Microphone permission ${permission}.`);
        return;
      }

      setStatus(await startMicrophone());
      await refreshDiagnostics();
    } catch (error) {
      setUiError(errorMessage(error));
      await refreshDiagnostics();
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    setUiError(null);

    try {
      setStatus(await stopMicrophone());
      await refreshDiagnostics();
    } catch (error) {
      setUiError(errorMessage(error));
      await refreshDiagnostics();
    } finally {
      setBusy(false);
    }
  };

  const handleBeginWakeTrial = async (expectedPositive: boolean) => {
    setBusy(true);
    setUiError(null);
    try {
      setWakeWord(
        await beginWakeWordCalibrationTrial(
          expectedPositive,
          wakeCalibrationCondition,
        ),
      );
      await new Promise<void>(resolve => {
        setTimeout(() => resolve(), 3_250);
      });
      await refreshDiagnostics();
    } catch (error) {
      setUiError(errorMessage(error));
      await refreshDiagnostics();
    } finally {
      setBusy(false);
    }
  };

  const handleWakeCalibrationMode = async () => {
    setBusy(true);
    setUiError(null);
    try {
      setWakeWord(
        await setWakeWordCalibrationMode(
          !wakeWord.acousticDiagnostics.enabled,
        ),
      );
    } catch (error) {
      setUiError(errorMessage(error));
      await refreshDiagnostics();
    } finally {
      setBusy(false);
    }
  };

  const handleNextWakeCalibrationCondition = () => {
    setWakeCalibrationConditionIndex(
      current => (current + 1) % WAKE_CALIBRATION_CONDITIONS.length,
    );
  };

  const handleResetWakeDiagnostics = async () => {
    setBusy(true);
    setUiError(null);
    try {
      setWakeWord(await resetWakeWordAcousticDiagnostics());
    } catch (error) {
      setUiError(errorMessage(error));
      await refreshDiagnostics();
    } finally {
      setBusy(false);
    }
  };

  const handleStartDiagnosticCapture = async (
    expectedPositive: boolean,
  ) => {
    setBusy(true);
    setUiError(null);
    try {
      const prefix = expectedPositive ? 'POSITIVE' : 'NEGATIVE';
      const count = wakeCapture.records.filter(record =>
        record.label.startsWith(prefix),
      ).length;
      setWakeCapture(
        await startWakeWordDiagnosticPcmCapture(
          `${prefix}_${String(count + 1).padStart(2, '0')}`,
          5120,
        ),
      );
    } catch (error) {
      setUiError(errorMessage(error));
      await refreshDiagnostics();
    } finally {
      setBusy(false);
    }
  };

  const handleStopDiagnosticCapture = async () => {
    setBusy(true);
    setUiError(null);
    try {
      setWakeCapture(await stopWakeWordDiagnosticPcmCapture());
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleReplayDiagnosticCaptures = async () => {
    setBusy(true);
    setUiError(null);
    try {
      setWakeReplay(await replayWakeWordDiagnosticPcm(2));
      setWakeCapture(await getWakeWordDiagnosticPcmCaptureStatus());
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteDiagnosticData = async () => {
    setBusy(true);
    setUiError(null);
    try {
      await deleteWakeWordDiagnosticData();
      setWakeCapture(await getWakeWordDiagnosticPcmCaptureStatus());
      setWakeReplay(null);
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleAudioCalibrationMode = async (
    mode: AudioProcessingCalibrationMode,
  ) => {
    setBusy(true);
    setUiError(null);
    try {
      setAudioProcessing(await setAudioProcessingCalibrationMode(mode));
    } catch (error) {
      setUiError(errorMessage(error));
      await refreshDiagnostics();
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safeArea}>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.card}>
          <Text style={styles.title}>Voice AI POC</Text>
          <Text style={styles.subtitle}>
            Native audio, Silero VAD, and openWakeWord diagnostics
          </Text>

          <View style={styles.controls}>
            <Button
              title="Refresh Audio Diagnostics"
              onPress={handleRefresh}
              disabled={busy}
            />
            <Button
              title="Start Microphone"
              onPress={handleStart}
              disabled={busy || status.isRecording}
            />
            <Button
              title="Stop Microphone"
              onPress={handleStop}
              disabled={busy || !status.isRecording}
            />
          </View>

          <Text style={styles.sectionTitle}>CALIBRATION CONTROLS</Text>
          <View style={styles.controls}>
            <Button
              title={
                wakeWord.acousticDiagnostics.enabled
                  ? 'Disable Calibration Mode'
                  : 'Enable Calibration Mode'
              }
              onPress={handleWakeCalibrationMode}
              disabled={busy}
            />
            <Button
              title={`Condition: ${wakeCalibrationCondition}`}
              onPress={handleNextWakeCalibrationCondition}
              disabled={
                busy ||
                wakeWord.acousticDiagnostics.activeTrialLabel !== null
              }
            />
            <Button
              title="Begin 3s Positive Trial"
              onPress={() => handleBeginWakeTrial(true)}
              disabled={
                busy ||
                !status.isRecording ||
                !wakeWord.acousticDiagnostics.enabled ||
                wakeWord.acousticDiagnostics.activeTrialLabel !== null
              }
            />
            <Button
              title="Begin 3s Negative Trial"
              onPress={() => handleBeginWakeTrial(false)}
              disabled={
                busy ||
                !status.isRecording ||
                !wakeWord.acousticDiagnostics.enabled ||
                wakeWord.acousticDiagnostics.activeTrialLabel !== null
              }
            />
            <Button
              title="Reset Wake Statistics"
              onPress={handleResetWakeDiagnostics}
              disabled={busy}
            />
            <Button
              title="Effects: AEC + NS"
              onPress={() => handleAudioCalibrationMode('AEC_NS')}
              disabled={busy || status.isRecording}
            />
            <Button
              title="Effects: AEC only"
              onPress={() => handleAudioCalibrationMode('AEC_ONLY')}
              disabled={busy || status.isRecording}
            />
            <Button
              title="Effects: NS only"
              onPress={() => handleAudioCalibrationMode('NS_ONLY')}
              disabled={busy || status.isRecording}
            />
            <Button
              title="Effects: disabled"
              onPress={() => handleAudioCalibrationMode('DISABLED')}
              disabled={busy || status.isRecording}
            />
          </View>

          <Text style={styles.sectionTitle}>TEMPORARY PCM REPLAY DIAGNOSTICS</Text>
          <Text style={styles.subtitle}>
            Explicit 5.12-second app-private captures only. PCM never crosses
            React Native and must be deleted after validation.
          </Text>
          <View style={styles.controls}>
            <Button
              title="Capture Positive PCM"
              onPress={() => handleStartDiagnosticCapture(true)}
              disabled={busy || !status.isRecording || wakeCapture.active}
            />
            <Button
              title="Capture Negative PCM"
              onPress={() => handleStartDiagnosticCapture(false)}
              disabled={busy || !status.isRecording || wakeCapture.active}
            />
            <Button
              title="Stop Diagnostic Capture"
              onPress={handleStopDiagnosticCapture}
              disabled={busy || !wakeCapture.active}
            />
            <Button
              title="Replay Captures Twice"
              onPress={handleReplayDiagnosticCaptures}
              disabled={
                busy || status.isRecording || wakeCapture.completedCaptureCount === 0
              }
            />
            <Button
              title="Delete Diagnostic PCM/Traces"
              onPress={handleDeleteDiagnosticData}
              disabled={busy || status.isRecording || wakeCapture.active}
            />
          </View>
          <StatusRow
            label="Capture active / label"
            value={`${yesNo(wakeCapture.active)} / ${wakeCapture.label ?? 'NONE'}`}
          />
          <StatusRow
            label="Capture windows"
            value={`${wakeCapture.inferenceWindowsWritten}/${wakeCapture.targetInferenceWindows}`}
          />
          <StatusRow
            label="Capture queue / drops"
            value={`${wakeCapture.queueDepthWindows}/${wakeCapture.queueCapacityWindows} / ${Math.floor(
              wakeCapture.droppedWindows,
            )}`}
          />
          <StatusRow
            label="Valid captures"
            value={`${wakeCapture.records.filter(record => record.valid).length}`}
          />
          <StatusRow
            label="Native replay"
            value={
              wakeReplay
                ? `${wakeReplay.replayCount} runs, ${wakeReplay.runtimeName} ${wakeReplay.runtimeVersion}`
                : 'NOT RUN'
            }
          />
          {wakeCapture.records.slice(-10).map(record => (
            <StatusRow
              key={record.captureId}
              label={record.label}
              value={`${record.valid ? 'VALID' : 'INVALID'}, ${Math.floor(
                record.samplesWritten,
              )} samples, drops=${Math.floor(record.droppedWindows)}`}
            />
          ))}

          <Text style={styles.sectionTitle}>DEVICE</Text>
          <StatusRow label="Manufacturer" value={audioProcessing.manufacturer} />
          <StatusRow label="Model" value={audioProcessing.model} />
          <StatusRow
            label="Android SDK"
            value={String(audioProcessing.androidSdk)}
          />

          <Text style={styles.sectionTitle}>AUDIO</Text>
          <StatusRow label="Sample rate" value={`${status.sampleRateHz} Hz`} />
          <StatusRow label="Channels" value="Mono (1)" />
          <StatusRow label="PCM format" value="PCM16 LE signed" />
          <StatusRow
            label="Audio session ID"
            value={String(audioProcessing.audioSessionId)}
          />

          <Text style={styles.sectionTitle}>AEC</Text>
          <StatusRow
            label="Supported"
            value={yesNo(audioProcessing.aec.supported)}
          />
          <StatusRow
            label="Available"
            value={yesNo(audioProcessing.aec.available)}
          />
          <StatusRow
            label="Requested"
            value={yesNo(audioProcessing.aec.requested)}
          />
          <StatusRow
            label="Created"
            value={yesNo(audioProcessing.aec.created)}
          />
          <StatusRow
            label="Enabled"
            value={yesNo(audioProcessing.aec.enabled)}
          />

          <Text style={styles.sectionTitle}>NOISE SUPPRESSION</Text>
          <StatusRow
            label="Supported"
            value={yesNo(audioProcessing.noiseSuppression.supported)}
          />
          <StatusRow
            label="Available"
            value={yesNo(audioProcessing.noiseSuppression.available)}
          />
          <StatusRow
            label="Requested"
            value={yesNo(audioProcessing.noiseSuppression.requested)}
          />
          <StatusRow
            label="Created"
            value={yesNo(audioProcessing.noiseSuppression.created)}
          />
          <StatusRow
            label="Enabled"
            value={yesNo(audioProcessing.noiseSuppression.enabled)}
          />

          <Text style={styles.sectionTitle}>MICROPHONE CAPTURE</Text>
          <StatusRow
            label="Microphone permission"
            value={status.permissionStatus}
          />
          <StatusRow label="Audio engine status" value={status.state} />
          <StatusRow
            label="AudioRecord initialized"
            value={String(status.audioRecordInitialized)}
          />
          <StatusRow
            label="Minimum buffer"
            value={`${status.minBufferSizeBytes} bytes`}
          />
          <StatusRow
            label="Buffer size"
            value={`${status.bufferSizeBytes} bytes`}
          />
          <StatusRow
            label="PCM frames captured"
            value={String(Math.floor(status.pcmFramesCaptured))}
          />
          <StatusRow
            label="Capture duration"
            value={`${Math.floor(status.captureDurationMs / 1000)} s`}
          />
          <StatusRow
            label="Microphone errors"
            value={String(status.microphoneErrorCount)}
          />

          <Text style={styles.sectionTitle}>NATIVE PCM PIPELINE</Text>
          <StatusRow label="Pipeline state" value={audioPipeline.state} />
          <StatusRow
            label="PCM format"
            value={audioPipeline.pcmFormat}
          />
          <StatusRow
            label="Sample rate"
            value={`${audioPipeline.sampleRateHz} Hz`}
          />
          <StatusRow
            label="Frame duration"
            value={`${audioPipeline.frameDurationMs} ms`}
          />
          <StatusRow
            label="Frame size"
            value={`${audioPipeline.frameSizeSamples} samples / ${audioPipeline.frameSizeBytes} bytes`}
          />
          <StatusRow
            label="Ring capacity"
            value={`${audioPipeline.bufferCapacityFrames} frames / ${audioPipeline.maxBufferedDurationMs} ms`}
          />
          <StatusRow
            label="Current buffered frames"
            value={String(audioPipeline.bufferedFrames)}
          />
          <StatusRow
            label="Maximum observed frames"
            value={String(audioPipeline.maxObservedBufferedFrames)}
          />
          <StatusRow
            label="PCM frames captured"
            value={String(Math.floor(audioPipeline.totalPcmFramesCaptured))}
          />
          <StatusRow
            label="PCM bytes processed"
            value={String(Math.floor(audioPipeline.totalPcmBytesProcessed))}
          />
          <StatusRow
            label="Frames written"
            value={String(
              Math.floor(audioPipeline.framesWrittenToRingBuffer),
            )}
          />
          <StatusRow
            label="Frames consumed"
            value={String(
              Math.floor(audioPipeline.framesConsumedFromRingBuffer),
            )}
          />
          <StatusRow
            label="Ring overflows"
            value={String(Math.floor(audioPipeline.overflowCount))}
          />
          <StatusRow
            label="Invalid reads"
            value={String(Math.floor(audioPipeline.invalidReadCount))}
          />
          <StatusRow
            label="Read errors"
            value={String(Math.floor(audioPipeline.readErrorCount))}
          />
          <StatusRow
            label="Pipeline errors"
            value={String(Math.floor(audioPipeline.pipelineErrorCount))}
          />

          <Text style={styles.sectionTitle}>NATIVE VAD</Text>
          <StatusRow label="Enabled" value={yesNo(audioPipeline.vad.enabled)} />
          <StatusRow label="State" value={audioPipeline.vad.state} />
          <StatusRow
            label="Threshold"
            value={`${audioPipeline.vad.thresholdDbFs.toFixed(1)} dBFS`}
          />
          <StatusRow
            label="Last measured energy"
            value={`${audioPipeline.vad.lastEnergyDbFs.toFixed(1)} dBFS`}
          />
          <StatusRow
            label="Last frame"
            value={audioPipeline.vad.lastFrameClassification}
          />
          <StatusRow
            label="VAD frames processed"
            value={String(Math.floor(audioPipeline.vad.vadFramesProcessed))}
          />
          <StatusRow
            label="Speech frames"
            value={String(Math.floor(audioPipeline.vad.speechFrames))}
          />
          <StatusRow
            label="Non-speech frames"
            value={String(Math.floor(audioPipeline.vad.nonSpeechFrames))}
          />
          <StatusRow
            label="Speech segments"
            value={String(Math.floor(audioPipeline.vad.speechSegments))}
          />
          <StatusRow
            label="Current speech duration"
            value={`${Math.floor(
              audioPipeline.vad.currentSpeechDurationMs,
            )} ms`}
          />
          <StatusRow
            label="Current silence duration"
            value={`${Math.floor(
              audioPipeline.vad.currentSilenceDurationMs,
            )} ms`}
          />
          <StatusRow
            label="Speech confirmation"
            value={`${audioPipeline.vad.effectiveSpeechStartConfirmationFrames} frames / ${audioPipeline.vad.minimumSpeechDurationMs} ms`}
          />
          <StatusRow
            label="Silence confirmation"
            value={`${audioPipeline.vad.effectiveSpeechEndConfirmationFrames} frames / ${audioPipeline.vad.minimumSilenceDurationMs} ms`}
          />
          <StatusRow
            label="VAD errors"
            value={String(Math.floor(audioPipeline.vad.vadErrorCount))}
          />
          <StatusRow
            label="Last speech-start event"
            value={formatVadEvent(lastVadStartedEvent)}
          />
          <StatusRow
            label="Last speech-stop event"
            value={formatVadEvent(lastVadStoppedEvent)}
          />

          <Text style={styles.sectionTitle}>SILERO VAD</Text>
          <StatusRow
            label="Model"
            value={
              audioPipeline.sileroVad.modelPresent ? 'PRESENT' : 'MISSING'
            }
          />
          <StatusRow
            label="Model asset"
            value={audioPipeline.sileroVad.modelAssetPath}
          />
          <StatusRow
            label="Model version"
            value={`${audioPipeline.sileroVad.modelGitTag} (${audioPipeline.sileroVad.modelGitCommit})`}
          />
          <StatusRow
            label="Model integrity"
            value={
              audioPipeline.sileroVad.modelSha256Verified
                ? 'SHA-256 VERIFIED'
                : 'NOT VERIFIED'
            }
          />
          <StatusRow
            label="Model SHA-256"
            value={audioPipeline.sileroVad.modelSha256 ?? 'NOT AVAILABLE'}
          />
          <StatusRow
            label="ONNX opset"
            value={String(audioPipeline.sileroVad.modelOnnxOpset)}
          />
          <StatusRow
            label="Model loaded"
            value={yesNo(audioPipeline.sileroVad.modelLoaded)}
          />
          <StatusRow
            label="Runtime"
            value={
              audioPipeline.sileroVad.runtimeAvailable
                ? 'AVAILABLE'
                : 'UNAVAILABLE'
            }
          />
          <StatusRow
            label="Runtime version"
            value={audioPipeline.sileroVad.runtimeVersion}
          />
          <StatusRow
            label="Inference"
            value={
              audioPipeline.sileroVad.inferenceAvailable
                ? 'ACTIVE'
                : 'UNAVAILABLE'
            }
          />
          <StatusRow
            label="Lifecycle"
            value={audioPipeline.sileroVad.lifecycleState}
          />
          <StatusRow label="State" value={audioPipeline.sileroVad.state} />
          <StatusRow
            label="Probability"
            value={formatProbability(
              audioPipeline.sileroVad.currentProbability,
            )}
          />
          <StatusRow
            label="Threshold"
            value={audioPipeline.sileroVad.speechProbabilityThreshold.toFixed(
              2,
            )}
          />
          <StatusRow
            label="Inference chunk"
            value={`${audioPipeline.sileroVad.inferenceChunkDurationMs} ms / ${audioPipeline.sileroVad.inferenceChunkSamples} samples`}
          />
          <StatusRow
            label="Speech confirmation"
            value={`${audioPipeline.sileroVad.speechStartConfirmationMs} ms / ${audioPipeline.sileroVad.speechStartConfirmationChunks} chunks`}
          />
          <StatusRow
            label="Stop hangover"
            value={`${audioPipeline.sileroVad.speechStopHangoverMs} ms / ${audioPipeline.sileroVad.speechStopConfirmationChunks} chunks`}
          />
          <StatusRow
            label="Inference count"
            value={`${Math.floor(
              audioPipeline.sileroVad.successfulInferenceCount,
            )} successful / ${Math.floor(
              audioPipeline.sileroVad.inferenceCount,
            )} total`}
          />
          <StatusRow
            label="Errors"
            value={`${Math.floor(
              audioPipeline.sileroVad.errorCount,
            )} engine / ${Math.floor(
              audioPipeline.sileroVad.failedInferenceCount,
            )} inference`}
          />
          <StatusRow
            label="Average inference latency"
            value={`${audioPipeline.sileroVad.averageInferenceDurationMs.toFixed(
              3,
            )} ms`}
          />
          <StatusRow
            label="Maximum inference latency"
            value={`${audioPipeline.sileroVad.maximumInferenceDurationMs.toFixed(
              3,
            )} ms`}
          />
          <StatusRow
            label="Queue"
            value={`${audioPipeline.sileroVad.queueDepthFrames} / ${audioPipeline.sileroVad.queueCapacityFrames} frames`}
          />
          <StatusRow
            label="Queue high-water mark"
            value={String(audioPipeline.sileroVad.queueHighWaterMarkFrames)}
          />
          <StatusRow
            label="Dropped frames"
            value={String(
              Math.floor(audioPipeline.sileroVad.droppedFrames),
            )}
          />
          <StatusRow
            label="Speech transitions"
            value={`${Math.floor(
              audioPipeline.sileroVad.speechStartCount,
            )} start / ${Math.floor(
              audioPipeline.sileroVad.speechStopCount,
            )} stop`}
          />
          <StatusRow
            label="Last speech-start event"
            value={formatSileroEvent(lastSileroStartedEvent)}
          />
          <StatusRow
            label="Last speech-stop event"
            value={formatSileroEvent(lastSileroStoppedEvent)}
          />
          {audioPipeline.sileroVad.modelError ? (
            <Text style={styles.errorText}>
              Silero model: {audioPipeline.sileroVad.modelError}
            </Text>
          ) : null}
          {audioPipeline.sileroVad.lastErrorMessage ? (
            <Text style={styles.errorText}>
              Silero error: {audioPipeline.sileroVad.lastErrorCode}:{' '}
              {audioPipeline.sileroVad.lastErrorMessage}
            </Text>
          ) : null}
          {lastSileroErrorEvent ? (
            <Text style={styles.errorText}>
              Last Silero event: {lastSileroErrorEvent.lastErrorCode}
            </Text>
          ) : null}

          <Text style={styles.sectionTitle}>OPENWAKEWORD</Text>
          <StatusRow label="Enabled" value={yesNo(wakeWord.enabled)} />
          <StatusRow label="Engine available" value={yesNo(wakeWord.available)} />
          <StatusRow
            label="Model present"
            value={yesNo(wakeWord.modelPresent)}
          />
          <StatusRow label="Model" value={wakeWord.modelName} />
          <StatusRow
            label="Model version"
            value={`${wakeWord.modelVersion} / ${wakeWord.modelReleaseTag}`}
          />
          <StatusRow label="Model commit" value={wakeWord.modelGitCommit} />
          <StatusRow label="Model license" value={wakeWord.modelLicense} />
          <StatusRow label="Model format" value={wakeWord.modelFormat} />
          <StatusRow
            label="Model integrity"
            value={
              wakeWord.modelHashVerified
                ? 'SHA-256 VERIFIED'
                : 'NOT VERIFIED'
            }
          />
          <StatusRow
            label="Classifier SHA-256"
            value={wakeWord.classifierSha256 ?? 'NOT AVAILABLE'}
          />
          <StatusRow label="Runtime" value={wakeWord.runtimeName} />
          <StatusRow label="Runtime version" value={wakeWord.runtimeVersion} />
          <StatusRow
            label="Runtime available"
            value={yesNo(wakeWord.runtimeAvailable)}
          />
          <StatusRow
            label="Tensor contract"
            value={
              wakeWord.tensorContractVerified ? 'VERIFIED' : 'NOT VERIFIED'
            }
          />
          <StatusRow label="Engine state" value={wakeWord.state} />
          <StatusRow label="Worker running" value={yesNo(wakeWord.running)} />
          <StatusRow
            label="Worker thread alive"
            value={yesNo(wakeWord.workerThreadAlive)}
          />
          <StatusRow
            label="Detection threshold"
            value={wakeWord.detectionThreshold.toFixed(2)}
          />
          <StatusRow
            label="Cooldown"
            value={`${Math.floor(wakeWord.cooldownMs)} ms`}
          />
          <StatusRow
            label="Inference window"
            value={`${wakeWord.inferenceWindowDurationMs} ms / ${wakeWord.inferenceWindowSamples} samples`}
          />
          <StatusRow
            label="PCM context"
            value={`${wakeWord.pcmContextSamples} samples`}
          />
          <StatusRow
            label="Mel history"
            value={`${wakeWord.melHistoryFrames} x ${wakeWord.melBins}`}
          />
          <StatusRow
            label="Embedding history"
            value={`${wakeWord.embeddingHistoryFrames} x ${wakeWord.embeddingFeatureSize}`}
          />
          <StatusRow
            label="Classifier output"
            value={wakeWord.classifierOutputSemantics}
          />
          <StatusRow
            label="Wake queue"
            value={`${wakeWord.queuedFrames} / ${wakeWord.queueCapacityFrames} frames`}
          />
          <StatusRow
            label="Wake queue high-water"
            value={`${wakeWord.queueHighWaterMarkFrames} / ${wakeWord.queueCapacityFrames} frames`}
          />
          <StatusRow
            label="Frames offered / consumed"
            value={`${Math.floor(wakeWord.framesOffered)} / ${Math.floor(
              wakeWord.framesConsumed,
            )}`}
          />
          <StatusRow
            label="Inference count"
            value={String(Math.floor(wakeWord.inferenceCount))}
          />
          <StatusRow
            label="Inference latency avg / max"
            value={`${wakeWord.averageInferenceLatencyMs.toFixed(
              3,
            )} / ${wakeWord.maximumInferenceLatencyMs.toFixed(3)} ms`}
          />
          <StatusRow
            label="Detection count"
            value={String(Math.floor(wakeWord.detectionCount))}
          />
          <StatusRow
            label="Suppressed duplicates"
            value={String(Math.floor(wakeWord.duplicateSuppressionCount))}
          />
          <StatusRow
            label="Dropped wake frames"
            value={String(Math.floor(wakeWord.droppedFrameCount))}
          />
          <StatusRow
            label="Malformed frames"
            value={String(Math.floor(wakeWord.malformedFrameCount))}
          />
          <StatusRow
            label="Runtime errors"
            value={String(Math.floor(wakeWord.runtimeErrorCount))}
          />
          <StatusRow
            label="Last confidence"
            value={formatConfidence(wakeWord.lastConfidence)}
          />
          <StatusRow
            label="Last detection"
            value={formatTimestamp(wakeWord.lastDetectionTimestampMs)}
          />
          <StatusRow label="Last engine event" value={lastWakeEngineEvent} />
          <StatusRow
            label="Last detection event"
            value={formatWakeDetection(lastWakeDetection)}
          />

          <Text style={styles.sectionTitle}>WAKE ACOUSTIC CALIBRATION</Text>
          <StatusRow
            label="Diagnostic mode"
            value={yesNo(wakeWord.acousticDiagnostics.enabled)}
          />
          <StatusRow
            label="Selected condition"
            value={wakeCalibrationCondition}
          />
          <StatusRow
            label="Last AEC / NS enabled"
            value={`${yesNo(
              wakeWord.acousticDiagnostics.lastAecEnabled,
            )} / ${yesNo(
              wakeWord.acousticDiagnostics.lastNoiseSuppressionEnabled,
            )}`}
          />
          <StatusRow
            label="PCM conversion"
            value={wakeWord.acousticDiagnostics.pcmScaling}
          />
          <StatusRow
            label="Byte order / swap"
            value={`${wakeWord.acousticDiagnostics.pcmByteOrder} / swap=${yesNo(
              wakeWord.acousticDiagnostics.byteSwapApplied,
            )}`}
          />
          <StatusRow
            label="Normalization applied"
            value={yesNo(wakeWord.acousticDiagnostics.normalizationApplied)}
          />
          <StatusRow
            label="Last PCM min / max"
            value={`${wakeWord.acousticDiagnostics.lastPcmMinimum ?? 'N/A'} / ${
              wakeWord.acousticDiagnostics.lastPcmMaximum ?? 'N/A'
            }`}
          />
          <StatusRow
            label="Last PCM RMS / peak"
            value={`${wakeWord.acousticDiagnostics.lastPcmRms.toFixed(
              2,
            )} / ${wakeWord.acousticDiagnostics.lastPcmPeak}`}
          />
          <StatusRow
            label="Last / maximum PCM dBFS"
            value={`${wakeWord.acousticDiagnostics.lastPcmDbFs.toFixed(
              2,
            )} / ${wakeWord.acousticDiagnostics.maximumObservedPcmDbFs.toFixed(
              2,
            )}`}
          />
          <StatusRow
            label="Clipped samples"
            value={String(
              Math.floor(wakeWord.acousticDiagnostics.clippedSampleCount),
            )}
          />
          <StatusRow
            label="Score min / max / average"
            value={`${formatConfidence(
              wakeWord.acousticDiagnostics.scoreMinimum,
            )} / ${formatConfidence(
              wakeWord.acousticDiagnostics.scoreMaximum,
            )} / ${wakeWord.acousticDiagnostics.scoreAverage.toFixed(4)}`}
          />
          <StatusRow
            label="Score P50 / P90 / P95 / P99"
            value={`${formatConfidence(
              wakeWord.acousticDiagnostics.scoreP50,
            )} / ${formatConfidence(
              wakeWord.acousticDiagnostics.scoreP90,
            )} / ${formatConfidence(
              wakeWord.acousticDiagnostics.scoreP95,
            )} / ${formatConfidence(wakeWord.acousticDiagnostics.scoreP99)}`}
          />
          <StatusRow
            label="Scores above thresholds"
            value={wakeWord.acousticDiagnostics.thresholdCounts
              .map(
                item =>
                  `${item.threshold.toFixed(2)}=${Math.floor(item.count)}`,
              )
              .join(' / ')}
          />
          <StatusRow
            label="Last inference metadata"
            value={`#${Math.floor(
              wakeWord.acousticDiagnostics.lastInferenceIndex,
            )}, score=${formatConfidence(
              wakeWord.acousticDiagnostics.lastClassifierScore,
            )}, queue=${wakeWord.acousticDiagnostics.lastQueueDepthFrames}, ${wakeWord.acousticDiagnostics.lastInferenceLatencyMs.toFixed(
              3,
            )} ms`}
          />
          <StatusRow
            label="Active calibration trial"
            value={
              wakeWord.acousticDiagnostics.activeTrialLabel
                ? `${wakeWord.acousticDiagnostics.activeTrialLabel} / ${
                    wakeWord.acousticDiagnostics.activeTrialCondition
                  } / #${
                    wakeWord.acousticDiagnostics.activeTrialAttemptNumber
                  }`
                : 'NONE'
            }
          />
          <StatusRow
            label="Completed positive / negative"
            value={`${wakeWord.acousticDiagnostics.completedPositiveTrials} / ${wakeWord.acousticDiagnostics.completedNegativeTrials}`}
          />
          <StatusRow
            label="Positive median / maximum score"
            value={`${formatConfidence(
              wakeWord.acousticDiagnostics.positiveScoreMedian,
            )} / ${formatConfidence(
              wakeWord.acousticDiagnostics.positiveScoreMaximum,
            )}`}
          />
          <StatusRow
            label="Negative median / maximum score"
            value={`${formatConfidence(
              wakeWord.acousticDiagnostics.negativeScoreMedian,
            )} / ${formatConfidence(
              wakeWord.acousticDiagnostics.negativeScoreMaximum,
            )}`}
          />
          <StatusRow
            label="Median / maximum detection latency"
            value={`${formatMilliseconds(
              wakeWord.acousticDiagnostics.medianDetectionLatencyMs,
            )} / ${formatMilliseconds(
              wakeWord.acousticDiagnostics.maximumDetectionLatencyMs,
            )}`}
          />
          {wakeWord.acousticDiagnostics.thresholdAnalysis.map(item => (
            <StatusRow
              key={`threshold-${item.threshold}`}
              label={`Threshold ${item.threshold.toFixed(2)}`}
              value={`TAR=${formatRate(item.trueAcceptRate)}, FRR=${formatRate(
                item.falseRejectRate,
              )}, FAR=${formatRate(item.falseAcceptRate)}, dup=${Math.floor(
                item.duplicateDetections,
              )}`}
            />
          ))}
          {wakeWord.acousticDiagnostics.calibrationTrials
            .slice(-10)
            .map(trial => (
              <StatusRow
                key={trial.label}
                label={trial.label}
                value={`max=${formatConfidence(
                  trial.maximumScore,
                )}, peak=${trial.peakPcmAmplitude}/${trial.peakPcmDbFs.toFixed(
                  1,
                )} dBFS, ${trial.audioProcessingMode}, detected=${
                  trial.detectionCount > 0 ? 'YES' : 'NO'
                }, latency=${formatMilliseconds(
                  trial.firstDetectionLatencyMs,
                )}`}
              />
            ))}
          {!wakeWord.modelPresent ? (
            <Text style={styles.errorText}>
              Missing model assets: {wakeWord.missingModelAssets}
            </Text>
          ) : null}
          {wakeWord.lastErrorMessage ? (
            <Text style={styles.errorText}>
              Wake error: {wakeWord.lastErrorCode}: {wakeWord.lastErrorMessage}
            </Text>
          ) : null}

          {status.lastError ? (
            <Text style={styles.errorText}>Native error: {status.lastError}</Text>
          ) : null}
          {audioProcessing.aec.lastError ? (
            <Text style={styles.errorText}>
              AEC error: {audioProcessing.aec.lastError}
            </Text>
          ) : null}
          {audioProcessing.noiseSuppression.lastError ? (
            <Text style={styles.errorText}>
              NS error: {audioProcessing.noiseSuppression.lastError}
            </Text>
          ) : null}
          {uiError ? <Text style={styles.errorText}>{uiError}</Text> : null}

        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function yesNo(value: boolean): string {
  return value ? 'YES' : 'NO';
}

function formatVadEvent(event: VadEvent | null): string {
  if (!event) {
    return 'NONE';
  }

  return `${event.event} @ frame ${Math.floor(event.frameIndex)}`;
}

function formatConfidence(confidence: number | null): string {
  return confidence === null ? 'NOT AVAILABLE' : confidence.toFixed(4);
}

function formatTimestamp(timestampMs: number): string {
  return timestampMs > 0 ? new Date(timestampMs).toLocaleTimeString() : 'NONE';
}

function formatRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

function formatMilliseconds(milliseconds: number | null): string {
  return milliseconds === null ? 'N/A' : `${milliseconds.toFixed(1)} ms`;
}

function formatWakeDetection(event: WakeWordDetectionEvent | null): string {
  if (!event) {
    return 'NONE';
  }

  return `${event.modelName} (${event.confidence.toFixed(4)})`;
}

function formatProbability(probability: number | null): string {
  return probability === null ? 'NOT AVAILABLE' : probability.toFixed(4);
}

function formatSileroEvent(event: SileroVadEvent | null): string {
  if (!event) {
    return 'NONE';
  }

  return `${event.event} (${event.probability.toFixed(4)})`;
}

function StatusRow({label, value}: {label: string; value: string}) {
  return (
    <View style={styles.statusRow}>
      <Text style={styles.statusLabel}>{label}</Text>
      <Text style={styles.statusValue}>{value}</Text>
    </View>
  );
}

const PermissionsAndroidResult = {
  GRANTED: 'granted',
} as const;

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: '#101827',
  },
  content: {
    flexGrow: 1,
    justifyContent: 'center',
    padding: 24,
  },
  card: {
    borderRadius: 16,
    backgroundColor: '#1b2638',
    padding: 24,
  },
  title: {
    color: '#f8fafc',
    fontSize: 28,
    fontWeight: '700',
  },
  subtitle: {
    color: '#94a3b8',
    fontSize: 15,
    marginTop: 8,
    marginBottom: 20,
  },
  controls: {
    gap: 12,
    marginBottom: 18,
  },
  sectionTitle: {
    color: '#f8fafc',
    fontSize: 18,
    fontWeight: '700',
    marginTop: 16,
    marginBottom: 10,
  },
  statusRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 12,
    paddingVertical: 6,
  },
  statusLabel: {
    color: '#cbd5e1',
    flex: 1,
    fontSize: 15,
  },
  statusValue: {
    color: '#fbbf24',
    flex: 1,
    fontSize: 15,
    fontWeight: '700',
    textAlign: 'right',
  },
  errorText: {
    color: '#fca5a5',
    fontSize: 14,
    marginTop: 12,
  },
});
