import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  ButtonProps,
  Pressable,
  DeviceEventEmitter,
  StyleSheet,
  Switch,
  Text,
  TouchableOpacity,
  View,
} from 'react-native';
import {
  SettingsCanvas,
  SettingsHero,
} from '../components/settings/SettingsPresentation';
import { SettingsSection } from '../components/settings/SettingsSection';
import { useAppTheme } from '../design/ThemeProvider';
import {
  AudioProcessingCalibrationMode,
  AudioCaptureSource,
  AudioPipelineStatus,
  AudioProcessingStatus,
  BargeInSemanticEvent,
  VoiceGatewayStatus,
  connectVoiceGateway,
  disconnectVoiceGateway,
  endVoiceSession,
  storeAuthTokens,
  getVoiceGatewayStatus,
  exportDiagnosticEvidence,
  getAudioPipelineStatus,
  getAudioProcessingStatus,
  getMicrophoneStatus,
  getManualWakeWordTrialStatus,
  getWakeWordStatus,
  getWakeWordDiagnosticPcmCaptureStatus,
  MicrophoneStatus,
  requestMicrophonePermission,
  startVoiceSession,
  startVoiceTurn,
  commitVoiceAudio,
  cancelVoiceResponse,
  resetWakeWordAcousticDiagnostics,
  replayWakeWordDiagnosticPcm,
  setAudioProcessingCalibrationMode,
  setAudioCaptureSource,
  setWakeWordCalibrationMode,
  subscribeVoiceBargeInEvent,
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
  ManualWakeWordTrialStatus,
  MAX_DIAGNOSTIC_PCM_DURATION_MS,
  WakeWordReplayBatchResult,
  WakeWordStatus,
  WakeWordStatusEvent,
} from '../native/VoiceModule';
import { publicApiConfig } from '../config/environment';

const DEFAULT_VOICE_GATEWAY_URL = `${publicApiConfig.websocketBaseUrl}/v1/voice`;

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
  requestedCaptureSource: 'VOICE_COMMUNICATION',
  actualCaptureSource: 'NOT_INITIALIZED',
  playbackUsage: 'USAGE_VOICE_COMMUNICATION',
  playbackContentType: 'CONTENT_TYPE_SPEECH',
  playback: {
    state: 'STOPPED',
    responseId: null,
    writtenPlaybackFrames: 0,
    presentedPlaybackFrames: 0,
    referenceBufferedFrames: 0,
    referenceReady: false,
    timestampConfidence: 'NONE',
    estimatedDelayMs: null,
    echoSimilarity: null,
    echoCoherence: null,
    farEndRms: null,
    micRms: null,
    nearEndFarEndEnergyRatio: null,
    lastAssessmentTimestampNs: '0',
  },
  detector: {
    state: 'IDLE',
    lastEvent: 'NONE',
    lastReason: 'NONE',
    candidateCount: 0,
    rejectedEchoCount: 0,
    confirmedCount: 0,
    degradedCount: 0,
    lastResponseId: null,
    lastSourceFrameSequenceStart: 0,
    lastSourceFrameSequenceEnd: 0,
    lastInferenceIndex: 0,
    lastLocalStopLatencyMs: null,
    lastLocalStopResponseId: null,
  },
  softwareAec: {
    requestedMode: 'PLATFORM',
    state: 'DISABLED',
    implementation: 'PLATFORM_AEC',
    sampleRateHz: 16_000,
    frameDurationMs: 10,
    frameSizeSamples: 160,
    renderToCaptureDelayMs: 80,
    aecRequested: true,
    noiseSuppressionRequested: false,
    platformAecDisabled: false,
    platformNoiseSuppressionDisabled: false,
    referenceReadyFrames: 0,
    referenceMissingFrames: 0,
    captureFrames: 0,
    renderFrames: 0,
    processedFrames: 0,
    bypassedFrames: 0,
    droppedFrames: 0,
    processingErrorCount: 0,
    lastReferenceConfidence: 'NONE',
    lastFarEndRms: 0,
    lastInputRms: 0,
    lastOutputRms: 0,
    lastError: null,
  },
  route: {
    activeLeaseCount: 0,
    captureLeaseCount: 0,
    playbackLeaseCount: 0,
    requestedMode: 3,
    actualMode: 0,
    priorMode: null,
    modeAcquired: false,
    modeRestored: true,
    requestedCommunicationDevice: 'AUTO',
    actualCommunicationDevice: 'NONE',
    inputDeviceType: 'UNKNOWN',
    outputDeviceType: 'UNKNOWN',
    communicationDeviceSelected: false,
    playbackRoute: 'UNKNOWN',
    audioFocusRequested: false,
    audioFocusGranted: false,
    audioFocusState: 'NONE',
    audioFocusRestored: true,
    restorationCount: 0,
    api31CommunicationDeviceSupported: false,
    lastError: null,
  },
};

const INITIAL_AUDIO_PROCESSING_STATUS: AudioProcessingStatus = {
  audioSessionId: -1,
  aec: {
    supported: false,
    available: false,
    requested: true,
    created: false,
    enabled: false,
    platformEnabledBeforeAttach: false,
    effectiveness: 'NOT_ATTACHED',
    lastError: null,
  },
  noiseSuppression: {
    supported: false,
    available: false,
    requested: true,
    created: false,
    enabled: false,
    platformEnabledBeforeAttach: false,
    effectiveness: 'NOT_ATTACHED',
    lastError: null,
  },
  manufacturer: 'UNKNOWN',
  model: 'UNKNOWN',
  androidSdk: 0,
  aecSelection: 'PLATFORM',
  aecHealth: 'NOT_ATTACHED',
  noiseSuppressionSelection: 'PLATFORM',
  noiseSuppressionHealth: 'NOT_ATTACHED',
  softwareAec: {
    requestedMode: 'PLATFORM',
    state: 'DISABLED',
    implementation: 'PLATFORM_AEC',
    sampleRateHz: 16_000,
    frameDurationMs: 10,
    frameSizeSamples: 160,
    renderToCaptureDelayMs: 80,
    aecRequested: true,
    noiseSuppressionRequested: false,
    platformAecDisabled: false,
    platformNoiseSuppressionDisabled: false,
    referenceReadyFrames: 0,
    referenceMissingFrames: 0,
    captureFrames: 0,
    renderFrames: 0,
    processedFrames: 0,
    bypassedFrames: 0,
    droppedFrames: 0,
    processingErrorCount: 0,
    lastReferenceConfidence: 'NONE',
    lastFarEndRms: 0,
    lastInputRms: 0,
    lastOutputRms: 0,
    lastError: null,
  },
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
    speechStartCount: 0,
    speechStopCount: 0,
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
    thresholdAnalysis: [0.1, 0.2, 0.3, 0.35, 0.4, 0.45, 0.5].map(threshold => ({
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
    })),
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

const INITIAL_MANUAL_WAKE_WORD_TRIAL_STATUS: ManualWakeWordTrialStatus = {
  active: false,
  trialId: null,
  microphoneSessionId: -1,
  startTimestampMs: 0,
  stopTimestampMs: 0,
  wakeDetectionCount: 0,
  inferenceWindowCount: 0,
  aboveThresholdWindowCount: 0,
  maximumScore: null,
  maximumScoreTimestampMs: 0,
  lastDetectionTimestampMs: 0,
  lastDetectionIntervalMs: null,
  currentWakeState: 'IDLE',
  cooldownActive: false,
  cooldownRemainingMs: 0,
  cooldownDurationMs: 2000,
  queueDepthFrames: 0,
  queueHighWaterMarkFrames: 0,
  queueDrops: 0,
  runtimeErrors: 0,
  workerGeneration: 0,
  aecEnabled: false,
  noiseSuppressionEnabled: false,
  pcmOverflowCount: 0,
  wakeWorkerDropCount: 0,
  audioRecordErrorCount: 0,
  audioRecordReadErrorCount: 0,
  pcmPipelineErrorCount: 0,
  wakeRuntimeErrorCount: 0,
  sileroRuntimeErrorCount: 0,
  energyVadState: 'SILENCE',
  sileroVadState: 'SILENCE',
  energyVadSpeechStartCount: 0,
  energyVadSpeechStopCount: 0,
  sileroVadSpeechStartCount: 0,
  sileroVadSpeechStopCount: 0,
  detections: [],
  thresholdCrossings: [],
  history: [],
};

const INITIAL_VOICE_GATEWAY_STATUS: VoiceGatewayStatus = {
  state: 'DISCONNECTED',
  connected: false,
  sessionStarted: false,
  turnActive: false,
  sessionId: null,
  turnId: null,
  responseId: null,
  framesQueued: 0,
  queueHighWaterMark: 0,
  droppedFrames: 0,
  invalidFrames: 0,
  framesSent: 0,
  bytesSent: 0,
  websocketErrorCount: 0,
  lastServerEvent: null,
  lastServerEventTimestampMs: 0,
  lastError: null,
};

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  return String(error);
}

export function DiagnosticScreen() {
  const styles = useDiagnosticStyles();
  const { colors } = useAppTheme();
  const [status, setStatus] = useState(INITIAL_STATUS);
  const [audioProcessing, setAudioProcessing] = useState(
    INITIAL_AUDIO_PROCESSING_STATUS,
  );
  const [audioPipeline, setAudioPipeline] = useState(
    INITIAL_AUDIO_PIPELINE_STATUS,
  );
  const [wakeWord, setWakeWord] = useState(INITIAL_WAKE_WORD_STATUS);
  const [manualTrial, setManualTrial] = useState(
    INITIAL_MANUAL_WAKE_WORD_TRIAL_STATUS,
  );
  const [wakeCapture, setWakeCapture] = useState(INITIAL_WAKE_CAPTURE_STATUS);
  const [voiceGateway, setVoiceGateway] = useState(
    INITIAL_VOICE_GATEWAY_STATUS,
  );
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
  const [lastSileroActivityEvent, setLastSileroActivityEvent] =
    useState<SileroVadEvent | null>(null);
  const [lastSileroErrorEvent, setLastSileroErrorEvent] =
    useState<SileroVadErrorEvent | null>(null);
  const [lastBargeInEvent, setLastBargeInEvent] =
    useState<BargeInSemanticEvent | null>(null);
  const [lastWakeDetection, setLastWakeDetection] =
    useState<WakeWordDetectionEvent | null>(null);
  const wakeEventKeys = useRef(new Set<string>());
  const [bridgeWakeEventCount, setBridgeWakeEventCount] = useState(0);
  const [bridgeDuplicateEventCount, setBridgeDuplicateEventCount] = useState(0);
  const [lastWakeEngineEvent, setLastWakeEngineEvent] = useState('NONE');
  const [wakeCalibrationConditionIndex, setWakeCalibrationConditionIndex] =
    useState(0);
  const wakeCalibrationCondition =
    WAKE_CALIBRATION_CONDITIONS[wakeCalibrationConditionIndex];

  const [activeTurnNumber, setActiveTurnNumber] = useState<number>(1);
  const [turnStepState, setTurnStepState] = useState<
    'IDLE' | 'STARTING' | 'RECORDING' | 'COMMITTING'
  >('IDLE');
  const [lastTurnInfo, setLastTurnInfo] = useState<string | null>(null);
  const [pcmCaptureConsent, setPcmCaptureConsent] = useState(false);

  const refreshDiagnostics = useCallback(async () => {
    try {
      const [
        microphoneStatus,
        audioProcessingStatus,
        audioPipelineStatus,
        wakeWordStatus,
        manualWakeWordTrialStatus,
        wakeCaptureStatus,
        voiceGatewayStatus,
      ] = await Promise.all([
        getMicrophoneStatus(),
        getAudioProcessingStatus(),
        getAudioPipelineStatus(),
        getWakeWordStatus(),
        getManualWakeWordTrialStatus(),
        getWakeWordDiagnosticPcmCaptureStatus(),
        getVoiceGatewayStatus(),
      ]);
      setStatus(microphoneStatus);
      setAudioProcessing(audioProcessingStatus);
      setAudioPipeline(audioPipelineStatus);
      setWakeWord(wakeWordStatus);
      setManualTrial(manualWakeWordTrialStatus);
      setWakeCapture(wakeCaptureStatus);
      setVoiceGateway(voiceGatewayStatus);
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
      DeviceEventEmitter.addListener('AUDIO_ENGINE_ERROR', refreshDiagnostics),
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
        'SILERO_VAD_SPEECH_ACTIVITY',
        (event: SileroVadEvent) => {
          setLastSileroActivityEvent(event);
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
      (() => {
        const unsubscribe = subscribeVoiceBargeInEvent(event => {
          setLastBargeInEvent(event);
          refreshDiagnostics();
        });
        return { remove: unsubscribe };
      })(),
      DeviceEventEmitter.addListener(
        'WAKE_WORD_DETECTED',
        (event: WakeWordDetectionEvent) => {
          const eventKey = `${event.microphoneSessionId}:${event.detectionSequenceNumber}:${event.inferenceIndex}`;
          setBridgeWakeEventCount(count => count + 1);
          if (wakeEventKeys.current.has(eventKey)) {
            setBridgeDuplicateEventCount(count => count + 1);
          } else {
            wakeEventKeys.current.add(eventKey);
          }
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
      DeviceEventEmitter.addListener(
        'VOICE_GATEWAY_STATUS',
        (event: VoiceGatewayStatus) => setVoiceGateway(event),
      ),
      DeviceEventEmitter.addListener('VOICE_GATEWAY_EVENT', refreshDiagnostics),
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
    setLastBargeInEvent(null);
    setLastSileroErrorEvent(null);
    setLastWakeDetection(null);
    setLastWakeEngineEvent('NONE');
    wakeEventKeys.current.clear();
    setBridgeWakeEventCount(0);
    setBridgeDuplicateEventCount(0);

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

  const ensureDeviceAuthenticated = async () => {
    try {
      const email = 'rmx5070-primary@voiceai.local';
      const password = 'rmx5070-permanent-auth-password-123';
      const deviceId = 'rmx5070-physical-primary';

      // Register device user (ignore if already registered)
      try {
        await fetch(`${publicApiConfig.httpBaseUrl}/auth/register`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password }),
        });
      } catch {
        // user already exists
      }

      // Login to retrieve valid tokens
      const loginRes = await fetch(
        `${publicApiConfig.httpBaseUrl}/auth/login`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            email,
            password,
            device_identifier: deviceId,
            platform: 'android',
          }),
        },
      );

      if (loginRes.ok) {
        const data = await loginRes.json();
        if (data.access_token && data.refresh_token) {
          await storeAuthTokens(data.access_token, data.refresh_token);
        }
      }
    } catch (e) {
      console.warn('Auto-auth error:', e);
    }
  };

  const handleConnectVoiceGateway = async () => {
    setBusy(true);
    setUiError(null);
    try {
      await ensureDeviceAuthenticated();
      setVoiceGateway(await connectVoiceGateway(DEFAULT_VOICE_GATEWAY_URL));
    } catch {
      try {
        await ensureDeviceAuthenticated();
        setVoiceGateway(await connectVoiceGateway(DEFAULT_VOICE_GATEWAY_URL));
      } catch (retryError) {
        setUiError(errorMessage(retryError));
        await refreshDiagnostics();
      }
    } finally {
      setBusy(false);
    }
  };

  const handleDisconnectVoiceGateway = async () => {
    setBusy(true);
    setUiError(null);
    try {
      setVoiceGateway(await disconnectVoiceGateway());
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleStartVoiceSession = async () => {
    setBusy(true);
    setUiError(null);
    try {
      if (!status.isRecording) {
        try {
          await requestMicrophonePermission();
          setStatus(await startMicrophone());
        } catch {}
      }
      setVoiceGateway(await startVoiceSession());
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleStartVoiceTurn = async () => {
    setBusy(true);
    setUiError(null);
    try {
      if (!status.isRecording) {
        try {
          await requestMicrophonePermission();
          setStatus(await startMicrophone());
        } catch {}
      }
      setVoiceGateway(await startVoiceTurn());
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleCommitVoiceAudio = async () => {
    setBusy(true);
    setUiError(null);
    try {
      setVoiceGateway(await commitVoiceAudio(0));
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleCancelVoiceResponse = async () => {
    setBusy(true);
    setUiError(null);
    try {
      setVoiceGateway(await cancelVoiceResponse('physical_validation_cancel'));
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleOneTouchStartTurn = async () => {
    setBusy(true);
    setUiError(null);
    setTurnStepState('STARTING');
    setLastTurnInfo('Connecting and preparing audio...');
    try {
      if (!status.isRecording) {
        await requestMicrophonePermission();
        const mic = await startMicrophone();
        setStatus(mic);
      }

      let gw = await getVoiceGatewayStatus();
      if (!gw.connected) {
        gw = await connectVoiceGateway(DEFAULT_VOICE_GATEWAY_URL);
        setVoiceGateway(gw);
        for (let i = 0; i < 30; i++) {
          await new Promise<void>(resolve => {
            setTimeout(resolve, 100);
          });
          gw = await getVoiceGatewayStatus();
          if (gw.connected) break;
        }
      }

      if (!gw.sessionStarted) {
        gw = await startVoiceSession();
        setVoiceGateway(gw);
        for (let i = 0; i < 30; i++) {
          await new Promise<void>(resolve => {
            setTimeout(resolve, 100);
          });
          gw = await getVoiceGatewayStatus();
          if (gw.sessionStarted) break;
        }
      }

      gw = await startVoiceTurn();
      setVoiceGateway(gw);
      setTurnStepState('RECORDING');
      setLastTurnInfo(
        `Turn ${activeTurnNumber} is active! Speak your sentence now.`,
      );
    } catch (error) {
      setUiError(errorMessage(error));
      setTurnStepState('IDLE');
    } finally {
      setBusy(false);
    }
  };

  const handleOneTouchFinishTurn = async () => {
    setBusy(true);
    setTurnStepState('COMMITTING');
    setLastTurnInfo(`Committing audio for Turn ${activeTurnNumber}...`);
    try {
      const gw = await commitVoiceAudio(0);
      setVoiceGateway(gw);
      setLastTurnInfo(`Turn ${activeTurnNumber} submitted! Transcribing...`);
      setActiveTurnNumber(prev => Math.min(10, prev + 1));
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setTurnStepState('IDLE');
      setBusy(false);
    }
  };

  const handleEndVoiceSession = async () => {
    setBusy(true);
    setUiError(null);
    try {
      setVoiceGateway(await endVoiceSession('diagnostic_complete'));
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleWakeCalibrationMode = async () => {
    setBusy(true);
    setUiError(null);
    try {
      setWakeWord(
        await setWakeWordCalibrationMode(!wakeWord.acousticDiagnostics.enabled),
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

  const handleStartDiagnosticCapture = async (expectedPositive: boolean) => {
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
          pcmCaptureConsent,
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

  const handleExportDiagnosticEvidence = async () => {
    setBusy(true);
    setUiError(null);
    try {
      const result = await exportDiagnosticEvidence();
      setLastTurnInfo(
        result.copied
          ? `Metadata evidence copied (${result.byteLength} bytes), session ${result.diagnosticSessionId}.`
          : 'Metadata evidence was assembled but could not be copied.',
      );
    } catch (error) {
      setUiError(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleCaptureSource = async (source: AudioCaptureSource) => {
    setBusy(true);
    setUiError(null);
    try {
      setStatus(await setAudioCaptureSource(source));
      await refreshDiagnostics();
    } catch (error) {
      setUiError(errorMessage(error));
      await refreshDiagnostics();
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsCanvas testID="developer-diagnostics-screen">
      <SettingsHero
        title="Developer diagnostics"
        subtitle="Inspect audio engine, VAD, and latency tracing."
      />
      <DiagnosticSections>
        <DiagnosticSectionTitle>LIVE ACOUSTIC SESSION</DiagnosticSectionTitle>
        <StatusRow
          label="Diagnostic session"
          value={status.diagnosticSessionId ?? 'NONE'}
        />
        <StatusRow
          label="Capture / route"
          value={`${status.requestedCaptureSource} → ${status.actualCaptureSource} · mode ${status.route.actualMode} · ${status.route.inputDeviceType} → ${status.route.outputDeviceType}`}
        />
        <StatusRow
          label="AEC / NS selection"
          value={`${audioProcessing.aecSelection} (${audioProcessing.aecHealth}) / ${audioProcessing.noiseSuppressionSelection} (${audioProcessing.noiseSuppressionHealth})`}
        />
        <StatusRow
          label="Playback frames written / presented"
          value={`${Math.floor(
            status.playback.writtenPlaybackFrames,
          )} / ${Math.floor(status.playback.presentedPlaybackFrames)}`}
        />
        <StatusRow
          label="Reference readiness / timestamp"
          value={`${yesNo(status.playback.referenceReady)} / ${
            status.playback.timestampConfidence
          }`}
        />
        <StatusRow
          label="Echo delay / similarity / coherence"
          value={`${formatMilliseconds(
            status.playback.estimatedDelayMs,
          )} / ${formatConfidence(
            status.playback.echoSimilarity,
          )} / ${formatConfidence(status.playback.echoCoherence)}`}
        />
        <StatusRow
          label="Near/far energy ratio"
          value={formatConfidence(status.playback.nearEndFarEndEnergyRatio)}
        />
        <StatusRow
          label="Detector"
          value={`${status.detector.state} · ${status.detector.lastEvent} · ${status.detector.lastReason}`}
        />
        <StatusRow
          label="Candidate / rejected / confirmed / degraded"
          value={`${status.detector.candidateCount} / ${status.detector.rejectedEchoCount} / ${status.detector.confirmedCount} / ${status.detector.degradedCount}`}
        />
        <StatusRow
          label="Last local-stop latency"
          value={formatMilliseconds(status.detector.lastLocalStopLatencyMs)}
        />
        <View style={styles.controls}>
          <DiagnosticButton
            title="Copy Metadata-Only Evidence JSON"
            onPress={handleExportDiagnosticEvidence}
            disabled={busy}
          />
        </View>

        <View style={styles.controls}>
          <DiagnosticButton
            title="Refresh Audio Diagnostics"
            onPress={handleRefresh}
            disabled={busy}
          />
          <DiagnosticButton
            title="Start Microphone"
            onPress={handleStart}
            disabled={busy || status.isRecording}
          />
          <DiagnosticButton
            title="Stop Microphone"
            onPress={handleStop}
            disabled={busy || !status.isRecording}
          />
          <DiagnosticButton
            title="Use VOICE_COMMUNICATION Capture"
            onPress={() => handleCaptureSource('VOICE_COMMUNICATION')}
            disabled={busy || status.isRecording}
          />
          <DiagnosticButton
            title="Use MIC Capture (A/B Diagnostic)"
            onPress={() => handleCaptureSource('MIC')}
            disabled={busy || status.isRecording}
          />
        </View>

        <DiagnosticSectionTitle>VOICE GATEWAY</DiagnosticSectionTitle>
        <Text style={styles.subtitle}>
          Explicit controls only. The gateway reads the access token from
          Android secure storage; PCM and credentials never cross this UI.
        </Text>
        <View style={styles.controls}>
          <DiagnosticButton
            title="Connect Voice Gateway"
            onPress={handleConnectVoiceGateway}
            disabled={busy || voiceGateway.connected}
          />
          <DiagnosticButton
            title="Start Voice Session"
            onPress={handleStartVoiceSession}
            disabled={
              busy || !voiceGateway.connected || voiceGateway.sessionStarted
            }
          />
          <DiagnosticButton
            title="Start Voice Turn"
            onPress={handleStartVoiceTurn}
            disabled={
              busy || !voiceGateway.sessionStarted || voiceGateway.turnActive
            }
          />
          <DiagnosticButton
            title="Commit Voice Audio"
            onPress={handleCommitVoiceAudio}
            disabled={busy || !voiceGateway.turnActive}
          />
          <DiagnosticButton
            title="Cancel Voice Response"
            onPress={handleCancelVoiceResponse}
            disabled={busy || !voiceGateway.turnActive}
          />
          <DiagnosticButton
            title="End Voice Session"
            onPress={handleEndVoiceSession}
            disabled={busy || !voiceGateway.sessionStarted}
          />
          <DiagnosticButton
            title="Disconnect Voice Gateway"
            onPress={handleDisconnectVoiceGateway}
            disabled={busy || !voiceGateway.connected}
          />
        </View>
        <StatusRow label="Gateway state" value={voiceGateway.state} />
        <StatusRow label="Gateway URL" value={DEFAULT_VOICE_GATEWAY_URL} />
        <StatusRow
          label="Session ID"
          value={voiceGateway.sessionId ?? 'NONE'}
        />
        <StatusRow label="Turn ID" value={voiceGateway.turnId ?? 'NONE'} />
        <StatusRow
          label="Queue / high-water / drops"
          value={`${voiceGateway.framesQueued} / ${voiceGateway.queueHighWaterMark} / ${voiceGateway.droppedFrames}`}
        />
        <StatusRow
          label="Frames / bytes sent"
          value={`${Math.floor(voiceGateway.framesSent)} / ${Math.floor(
            voiceGateway.bytesSent,
          )}`}
        />
        <StatusRow
          label="Last server event"
          value={voiceGateway.lastServerEvent ?? 'NONE'}
        />
        {voiceGateway.lastError ? (
          <Text style={styles.errorText}>
            Gateway error: {voiceGateway.lastError}
          </Text>
        ) : null}

        <DiagnosticSectionTitle>MANUAL WAKE-WORD TRIAL</DiagnosticSectionTitle>
        <Text style={styles.subtitle}>
          One trial equals one manually controlled microphone session. Start and
          stop capture only with the buttons above; wake events never restart
          AudioRecord.
        </Text>
        <StatusRow
          label="Microphone"
          value={status.isRecording ? 'RUNNING' : 'STOPPED'}
        />
        <StatusRow
          label="Wake engine"
          value={wakeWord.running ? 'RUNNING' : 'STOPPED'}
        />
        <StatusRow
          label="Silero VAD"
          value={audioPipeline.sileroVad.running ? 'RUNNING' : 'STOPPED'}
        />
        <StatusRow label="AEC" value={yesNo(manualTrial.aecEnabled)} />
        <StatusRow
          label="NS"
          value={yesNo(manualTrial.noiseSuppressionEnabled)}
        />
        <StatusRow label="Trial ID" value={manualTrial.trialId ?? 'NONE'} />
        <StatusRow
          label="Microphone session ID"
          value={String(manualTrial.microphoneSessionId)}
        />
        <StatusRow
          label="Trial start / stop"
          value={`${formatTimestamp(
            manualTrial.startTimestampMs,
          )} / ${formatTimestamp(manualTrial.stopTimestampMs)}`}
        />
        <StatusRow
          label="Wake detections"
          value={`${Math.floor(manualTrial.wakeDetectionCount)} (native)`}
        />
        <StatusRow
          label="Bridge events / duplicate deliveries"
          value={`${bridgeWakeEventCount} / ${bridgeDuplicateEventCount}`}
        />
        <StatusRow
          label="Classifier windows"
          value={String(Math.floor(manualTrial.inferenceWindowCount))}
        />
        <StatusRow
          label="Windows score >= 0.50"
          value={String(Math.floor(manualTrial.aboveThresholdWindowCount))}
        />
        <StatusRow
          label="Maximum score / timestamp"
          value={`${formatConfidence(
            manualTrial.maximumScore,
          )} / ${formatTimestamp(manualTrial.maximumScoreTimestampMs)}`}
        />
        <StatusRow
          label="Last detection / interval"
          value={`${formatTimestamp(
            manualTrial.lastDetectionTimestampMs,
          )} / ${formatMilliseconds(manualTrial.lastDetectionIntervalMs)}`}
        />
        <StatusRow
          label="Wake state"
          value={`${manualTrial.currentWakeState} / cooldown=${
            manualTrial.cooldownActive
              ? `${Math.floor(manualTrial.cooldownRemainingMs)} ms remaining`
              : 'OFF'
          }`}
        />
        <StatusRow
          label="Cooldown duration"
          value={`${Math.floor(manualTrial.cooldownDurationMs)} ms`}
        />
        <StatusRow
          label="Queue depth / high-water"
          value={`${manualTrial.queueDepthFrames} / ${manualTrial.queueHighWaterMarkFrames} frames`}
        />
        <StatusRow
          label="Queue drops"
          value={String(Math.floor(manualTrial.wakeWorkerDropCount))}
        />
        <StatusRow
          label="PCM overflows"
          value={String(Math.floor(manualTrial.pcmOverflowCount))}
        />
        <StatusRow
          label="AudioRecord errors / read errors"
          value={`${manualTrial.audioRecordErrorCount} / ${Math.floor(
            manualTrial.audioRecordReadErrorCount,
          )}`}
        />
        <StatusRow
          label="ONNX runtime errors wake / Silero"
          value={`${Math.floor(
            manualTrial.wakeRuntimeErrorCount,
          )} / ${Math.floor(manualTrial.sileroRuntimeErrorCount)}`}
        />
        <StatusRow
          label="VAD state / speech start-stop"
          value={`${manualTrial.energyVadState} / ${Math.floor(
            manualTrial.energyVadSpeechStartCount,
          )}-${Math.floor(manualTrial.energyVadSpeechStopCount)}`}
        />
        <StatusRow
          label="Silero state / speech start-stop"
          value={`${manualTrial.sileroVadState} / ${Math.floor(
            manualTrial.sileroVadSpeechStartCount,
          )}-${Math.floor(manualTrial.sileroVadSpeechStopCount)}`}
        />

        <Text style={styles.subsectionTitle}>NATIVE DETECTION METADATA</Text>
        {manualTrial.detections.slice(-10).map(detection => (
          <StatusRow
            key={`${manualTrial.trialId}-detection-${detection.detectionSequenceNumber}`}
            label={`Detection #${Math.floor(
              detection.detectionSequenceNumber,
            )} / window #${Math.floor(detection.inferenceWindowSequence)}`}
            value={`score=${detection.classifierScore.toFixed(4)}, ${
              detection.wakeStateBefore
            }->${detection.wakeStateAfter}, cooldown=${Math.floor(
              detection.cooldownRemainingMs,
            )} ms, Δ=${formatMilliseconds(
              detection.millisecondsSincePreviousDetection,
            )}`}
          />
        ))}
        <Text style={styles.subsectionTitle}>
          THRESHOLD-CROSSING WINDOWS (LAST 10)
        </Text>
        {manualTrial.thresholdCrossings.slice(-10).map(crossing => (
          <StatusRow
            key={`${manualTrial.trialId}-crossing-${crossing.inferenceWindowSequence}`}
            label={`Window #${Math.floor(crossing.inferenceWindowSequence)} / ${
              crossing.wakeStateBefore
            }->${crossing.wakeStateAfter}`}
            value={`score=${crossing.score.toFixed(4)}, event=${yesNo(
              crossing.generatedWakeEvent,
            )}, cooldown-suppressed=${yesNo(crossing.suppressedByCooldown)}`}
          />
        ))}
        <Text style={styles.subsectionTitle}>COMPLETED MANUAL TRIALS</Text>
        {manualTrial.history.slice(-10).map(trial => (
          <StatusRow
            key={`history-${trial.trialId}`}
            label={trial.trialId ?? 'UNKNOWN'}
            value={`detections=${Math.floor(
              trial.wakeDetectionCount,
            )}, windows>=0.50=${Math.floor(
              trial.aboveThresholdWindowCount,
            )}, max=${formatConfidence(trial.maximumScore)}`}
          />
        ))}

        <DiagnosticSectionTitle>CALIBRATION CONTROLS</DiagnosticSectionTitle>
        <View style={styles.controls}>
          <DiagnosticButton
            title={
              wakeWord.acousticDiagnostics.enabled
                ? 'Disable Calibration Mode'
                : 'Enable Calibration Mode'
            }
            onPress={handleWakeCalibrationMode}
            disabled={busy}
          />
          <DiagnosticButton
            title={`Condition: ${wakeCalibrationCondition}`}
            onPress={handleNextWakeCalibrationCondition}
            disabled={
              busy || wakeWord.acousticDiagnostics.activeTrialLabel !== null
            }
          />
          <DiagnosticButton
            title="Reset Wake Statistics"
            onPress={handleResetWakeDiagnostics}
            disabled={busy}
          />
          <DiagnosticButton
            title="Effects: AEC + NS"
            onPress={() => handleAudioCalibrationMode('AEC_NS')}
            disabled={busy || status.isRecording}
          />
          <DiagnosticButton
            title="Effects: AEC only"
            onPress={() => handleAudioCalibrationMode('AEC_ONLY')}
            disabled={busy || status.isRecording}
          />
          <DiagnosticButton
            title="Effects: NS only"
            onPress={() => handleAudioCalibrationMode('NS_ONLY')}
            disabled={busy || status.isRecording}
          />
          <DiagnosticButton
            title="Effects: disabled"
            onPress={() => handleAudioCalibrationMode('DISABLED')}
            disabled={busy || status.isRecording}
          />
        </View>

        <DiagnosticSectionTitle>
          TEMPORARY PCM REPLAY DIAGNOSTICS
        </DiagnosticSectionTitle>
        <Text style={styles.subtitle}>
          Optional recorded-fixture generation only. Captures are app-private,
          capped at {MAX_DIAGNOSTIC_PCM_DURATION_MS / 1000}s, never cross React
          Native, and must be deleted after validation.
        </Text>
        <View style={styles.consentRow}>
          <Switch
            value={pcmCaptureConsent}
            onValueChange={setPcmCaptureConsent}
            disabled={busy}
          />
          <Text style={styles.consentText}>
            I explicitly consent to temporary diagnostic PCM capture.
          </Text>
        </View>
        <View style={styles.controls}>
          <DiagnosticButton
            title="Capture Positive PCM"
            onPress={() => handleStartDiagnosticCapture(true)}
            disabled={
              busy ||
              !status.isRecording ||
              wakeCapture.active ||
              !pcmCaptureConsent
            }
          />
          <DiagnosticButton
            title="Capture Negative PCM"
            onPress={() => handleStartDiagnosticCapture(false)}
            disabled={
              busy ||
              !status.isRecording ||
              wakeCapture.active ||
              !pcmCaptureConsent
            }
          />
          <DiagnosticButton
            title="Stop Diagnostic Capture"
            onPress={handleStopDiagnosticCapture}
            disabled={busy || !wakeCapture.active}
          />
          <DiagnosticButton
            title="Replay Captures Twice"
            onPress={handleReplayDiagnosticCaptures}
            disabled={
              busy ||
              status.isRecording ||
              wakeCapture.completedCaptureCount === 0
            }
          />
          <DiagnosticButton
            title="Delete Diagnostic PCM/Traces"
            onPress={handleDeleteDiagnosticData}
            disabled={busy || status.isRecording || wakeCapture.active}
          />
        </View>
        <StatusRow
          label="Capture active / label"
          value={`${yesNo(wakeCapture.active)} / ${
            wakeCapture.label ?? 'NONE'
          }`}
        />
        <StatusRow
          label="Capture windows"
          value={`${wakeCapture.inferenceWindowsWritten}/${wakeCapture.targetInferenceWindows}`}
        />
        <StatusRow
          label="Capture queue / drops"
          value={`${wakeCapture.queueDepthWindows}/${
            wakeCapture.queueCapacityWindows
          } / ${Math.floor(wakeCapture.droppedWindows)}`}
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

        <DiagnosticSectionTitle>DEVICE</DiagnosticSectionTitle>
        <StatusRow label="Manufacturer" value={audioProcessing.manufacturer} />
        <StatusRow label="Model" value={audioProcessing.model} />
        <StatusRow
          label="Android SDK"
          value={String(audioProcessing.androidSdk)}
        />

        <DiagnosticSectionTitle>AUDIO</DiagnosticSectionTitle>
        <StatusRow label="Sample rate" value={`${status.sampleRateHz} Hz`} />
        <StatusRow label="Channels" value="Mono (1)" />
        <StatusRow label="PCM format" value="PCM16 LE signed" />
        <StatusRow
          label="Audio session ID"
          value={String(audioProcessing.audioSessionId)}
        />

        <DiagnosticSectionTitle>AEC</DiagnosticSectionTitle>
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
        <StatusRow label="Created" value={yesNo(audioProcessing.aec.created)} />
        <StatusRow label="Enabled" value={yesNo(audioProcessing.aec.enabled)} />
        <StatusRow
          label="Effectiveness state"
          value={audioProcessing.aec.effectiveness}
        />

        <DiagnosticSectionTitle>SOFTWARE AEC3 FALLBACK</DiagnosticSectionTitle>
        <StatusRow
          label="Requested mode"
          value={audioProcessing.softwareAec.requestedMode}
        />
        <StatusRow
          label="State / implementation"
          value={`${audioProcessing.softwareAec.state} / ${audioProcessing.softwareAec.implementation}`}
        />
        <StatusRow
          label="Render-to-capture delay"
          value={`${audioProcessing.softwareAec.renderToCaptureDelayMs} ms`}
        />
        <StatusRow
          label="Platform AEC disabled"
          value={yesNo(audioProcessing.softwareAec.platformAecDisabled)}
        />
        <StatusRow
          label="Reference ready / missing"
          value={`${Math.floor(
            audioProcessing.softwareAec.referenceReadyFrames,
          )} / ${Math.floor(
            audioProcessing.softwareAec.referenceMissingFrames,
          )}`}
        />
        <StatusRow
          label="Processed / bypassed"
          value={`${Math.floor(
            audioProcessing.softwareAec.processedFrames,
          )} / ${Math.floor(audioProcessing.softwareAec.bypassedFrames)}`}
        />
        <StatusRow
          label="Last RMS input / output"
          value={`${audioProcessing.softwareAec.lastInputRms.toFixed(
            1,
          )} / ${audioProcessing.softwareAec.lastOutputRms.toFixed(1)}`}
        />
        {audioProcessing.softwareAec.lastError ? (
          <Text style={styles.errorText}>
            Software AEC error: {audioProcessing.softwareAec.lastError}
          </Text>
        ) : null}

        <DiagnosticSectionTitle>NOISE SUPPRESSION</DiagnosticSectionTitle>
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
        <StatusRow
          label="Effectiveness state"
          value={audioProcessing.noiseSuppression.effectiveness}
        />

        <DiagnosticSectionTitle>MICROPHONE CAPTURE</DiagnosticSectionTitle>
        <StatusRow
          label="Microphone permission"
          value={status.permissionStatus}
        />
        <StatusRow label="Audio engine status" value={status.state} />
        <StatusRow
          label="Requested capture source"
          value={status.requestedCaptureSource}
        />
        <StatusRow
          label="Actual capture source"
          value={status.actualCaptureSource}
        />
        <StatusRow label="Playback usage" value={status.playbackUsage} />
        <StatusRow
          label="Playback content type"
          value={status.playbackContentType}
        />
        <StatusRow
          label="Android mode"
          value={`${status.route.actualMode} (requested ${status.route.requestedMode})`}
        />
        <StatusRow
          label="Route leases"
          value={`total=${status.route.activeLeaseCount}, capture=${status.route.captureLeaseCount}, playback=${status.route.playbackLeaseCount}`}
        />
        <StatusRow
          label="Communication device"
          value={`${status.route.actualCommunicationDevice} (requested ${status.route.requestedCommunicationDevice})`}
        />
        <StatusRow label="Playback route" value={status.route.playbackRoute} />
        <StatusRow
          label="Audio focus"
          value={`${status.route.audioFocusState}, granted=${yesNo(
            status.route.audioFocusGranted,
          )}`}
        />
        <StatusRow
          label="Restoration"
          value={`${yesNo(status.route.modeRestored)} / ${yesNo(
            status.route.audioFocusRestored,
          )}, count=${status.route.restorationCount}`}
        />
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

        <DiagnosticSectionTitle>NATIVE PCM PIPELINE</DiagnosticSectionTitle>
        <StatusRow label="Pipeline state" value={audioPipeline.state} />
        <StatusRow label="PCM format" value={audioPipeline.pcmFormat} />
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
          value={String(Math.floor(audioPipeline.framesWrittenToRingBuffer))}
        />
        <StatusRow
          label="Frames consumed"
          value={String(Math.floor(audioPipeline.framesConsumedFromRingBuffer))}
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

        <DiagnosticSectionTitle>NATIVE VAD</DiagnosticSectionTitle>
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
          value={`${Math.floor(audioPipeline.vad.currentSpeechDurationMs)} ms`}
        />
        <StatusRow
          label="Current silence duration"
          value={`${Math.floor(audioPipeline.vad.currentSilenceDurationMs)} ms`}
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

        <DiagnosticSectionTitle>SILERO VAD</DiagnosticSectionTitle>
        <StatusRow
          label="Model"
          value={audioPipeline.sileroVad.modelPresent ? 'PRESENT' : 'MISSING'}
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
          value={formatProbability(audioPipeline.sileroVad.currentProbability)}
        />
        <StatusRow
          label="Threshold"
          value={audioPipeline.sileroVad.speechProbabilityThreshold.toFixed(2)}
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
          value={String(Math.floor(audioPipeline.sileroVad.droppedFrames))}
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
        <StatusRow
          label="Last speech-activity event"
          value={formatSileroEvent(lastSileroActivityEvent)}
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

        <DiagnosticSectionTitle>PLAYBACK-AWARE BARGE-IN</DiagnosticSectionTitle>
        <StatusRow
          label="Last semantic decision"
          value={formatBargeInEvent(lastBargeInEvent)}
        />
        <StatusRow
          label="Local stop acknowledgement"
          value={formatBargeInStopAcknowledgement(lastBargeInEvent)}
        />

        <DiagnosticSectionTitle>OPENWAKEWORD</DiagnosticSectionTitle>
        <StatusRow label="Enabled" value={yesNo(wakeWord.enabled)} />
        <StatusRow label="Engine available" value={yesNo(wakeWord.available)} />
        <StatusRow label="Model present" value={yesNo(wakeWord.modelPresent)} />
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
            wakeWord.modelHashVerified ? 'SHA-256 VERIFIED' : 'NOT VERIFIED'
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
          value={wakeWord.tensorContractVerified ? 'VERIFIED' : 'NOT VERIFIED'}
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

        <DiagnosticSectionTitle>
          WAKE ACOUSTIC CALIBRATION
        </DiagnosticSectionTitle>
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
          value={`${wakeWord.acousticDiagnostics.lastPcmRms.toFixed(2)} / ${
            wakeWord.acousticDiagnostics.lastPcmPeak
          }`}
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
              item => `${item.threshold.toFixed(2)}=${Math.floor(item.count)}`,
            )
            .join(' / ')}
        />
        <StatusRow
          label="Last inference metadata"
          value={`#${Math.floor(
            wakeWord.acousticDiagnostics.lastInferenceIndex,
          )}, score=${formatConfidence(
            wakeWord.acousticDiagnostics.lastClassifierScore,
          )}, queue=${
            wakeWord.acousticDiagnostics.lastQueueDepthFrames
          }, ${wakeWord.acousticDiagnostics.lastInferenceLatencyMs.toFixed(
            3,
          )} ms`}
        />
        <StatusRow
          label="Active calibration trial"
          value={
            wakeWord.acousticDiagnostics.activeTrialLabel
              ? `${wakeWord.acousticDiagnostics.activeTrialLabel} / ${wakeWord.acousticDiagnostics.activeTrialCondition} / #${wakeWord.acousticDiagnostics.activeTrialAttemptNumber}`
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
              value={`max=${formatConfidence(trial.maximumScore)}, peak=${
                trial.peakPcmAmplitude
              }/${trial.peakPcmDbFs.toFixed(1)} dBFS, ${
                trial.audioProcessingMode
              }, detected=${
                trial.detectionCount > 0 ? 'YES' : 'NO'
              }, latency=${formatMilliseconds(trial.firstDetectionLatencyMs)}`}
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
      </DiagnosticSections>
      <View
        style={[
          styles.card,
          {
            backgroundColor: colors.surface,
            borderColor: colors.borderSubtle,
            borderWidth: 1,
            padding: 16,
            marginBottom: 16,
          },
        ]}
      >
        <Text
          style={[
            styles.title,
            { color: colors.primary, textAlign: 'center', fontSize: 20 },
          ]}
        >
          🎙️ Manual speech test
        </Text>
        <Text
          style={[
            styles.subtitle,
            { color: colors.textMuted, textAlign: 'center', marginBottom: 12 },
          ]}
        >
          Speech validation — Turn {activeTurnNumber} of 10
        </Text>

        {turnStepState === 'RECORDING' ? (
          <TouchableOpacity
            style={{
              backgroundColor: '#dc2626',
              paddingVertical: 20,
              borderRadius: 12,
              alignItems: 'center',
              justifyContent: 'center',
              shadowColor: '#ef4444',
              shadowOpacity: 0.5,
              shadowRadius: 10,
              elevation: 6,
            }}
            onPress={handleOneTouchFinishTurn}
            disabled={busy}
          >
            <Text
              style={{
                color: '#ffffff',
                fontWeight: 'bold',
                fontSize: 18,
                textAlign: 'center',
              }}
            >
              ⏹️ FINISH & COMMIT TURN {activeTurnNumber}
            </Text>
            <Text style={{ color: '#fecaca', fontSize: 13, marginTop: 4 }}>
              (Tap as soon as you finish speaking)
            </Text>
          </TouchableOpacity>
        ) : (
          <TouchableOpacity
            style={{
              backgroundColor: '#059669',
              paddingVertical: 20,
              borderRadius: 12,
              alignItems: 'center',
              justifyContent: 'center',
              shadowColor: '#10b981',
              shadowOpacity: 0.5,
              shadowRadius: 10,
              elevation: 6,
            }}
            onPress={handleOneTouchStartTurn}
            disabled={busy}
          >
            <Text
              style={{
                color: '#ffffff',
                fontWeight: 'bold',
                fontSize: 18,
                textAlign: 'center',
              }}
            >
              {turnStepState === 'STARTING'
                ? '⏳ PREPARING AUDIO...'
                : `🎙️ SPEAK TURN ${activeTurnNumber}`}
            </Text>
            <Text style={{ color: '#a7f3d0', fontSize: 13, marginTop: 4 }}>
              (Tap and speak your sentence)
            </Text>
          </TouchableOpacity>
        )}

        {lastTurnInfo ? (
          <Text
            style={{
              color: colors.warning,
              textAlign: 'center',
              marginTop: 10,
              fontWeight: '600',
              fontSize: 14,
            }}
          >
            {lastTurnInfo}
          </Text>
        ) : null}
      </View>
    </SettingsCanvas>
  );
}

function DiagnosticSectionTitle({ children }: { children: React.ReactNode }) {
  return <Text>{children}</Text>;
}

function DiagnosticSections({ children }: { children: React.ReactNode }) {
  const sections: { title?: string; children: React.ReactNode[] }[] = [];
  let section: (typeof sections)[number] = { children: [] };
  React.Children.toArray(children).forEach(child => {
    if (
      React.isValidElement<{ children: React.ReactNode }>(child) &&
      child.type === DiagnosticSectionTitle
    ) {
      if (section.children.length) sections.push(section);
      section = {
        title: React.Children.toArray(child.props.children).join(''),
        children: [],
      };
    } else {
      section.children.push(child);
    }
  });
  if (section.children.length) sections.push(section);
  return (
    <>
      {sections.map((item, index) => (
        <SettingsSection key={index} title={item.title}>
          {item.children}
        </SettingsSection>
      ))}
    </>
  );
}

function DiagnosticButton({
  title,
  onPress,
  disabled,
  color,
  ...rest
}: ButtonProps) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      {...rest}
      accessibilityLabel={rest.accessibilityLabel ?? title}
      accessibilityRole="button"
      accessibilityState={{ disabled: Boolean(disabled) }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        diagnosticButtonStyles.button,
        {
          backgroundColor: color ?? colors.primary,
          opacity: disabled ? 0.45 : pressed ? 0.75 : 1,
        },
      ]}
    >
      <Text style={diagnosticButtonStyles.label}>{title}</Text>
    </Pressable>
  );
}

const diagnosticButtonStyles = StyleSheet.create({
  button: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 44,
    padding: 12,
    borderRadius: 16,
    experimental_backgroundImage:
      'linear-gradient(100deg, #7B61FF 0%, #D653EC 45%, #FF80AA 75%, #FFB276 100%)',
  },
  label: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
    textAlign: 'center',
  },
});

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

  const reference = event.playbackReferenceAvailable ? 'ready' : 'not-ready';
  const delay =
    event.estimatedDelayMs == null ? 'N/A' : `${event.estimatedDelayMs} ms`;
  const sourceFrames =
    event.sourceFrameSequenceStart == null ||
    event.sourceFrameSequenceEnd == null
      ? 'N/A'
      : `${event.sourceFrameSequenceStart}-${event.sourceFrameSequenceEnd}`;
  return (
    `${event.event} (${event.probability.toFixed(4)}) ` +
    `state=${event.playbackState ?? 'N/A'} ref=${reference} ` +
    `delay=${delay} confidence=${event.timestampConfidence ?? 'N/A'} ` +
    `frames=${sourceFrames} discontinuous=${event.discontinuous ? 'yes' : 'no'}`
  );
}

function formatBargeInEvent(event: BargeInSemanticEvent | null): string {
  if (!event) {
    return 'NONE';
  }
  const response = event.responseId ?? 'NONE';
  return (
    `${event.event} state=${event.state} reason=${event.reason} ` +
    `response=${response} playback=${event.playbackState} ` +
    `frames=${event.sourceFrameSequenceStart}-${event.sourceFrameSequenceEnd} ` +
    `inference=${event.inferenceIndex} ` +
    `capture=${event.captureStartNs}-${event.captureEndNs} ` +
    `delay=${formatMilliseconds(event.estimatedDelayMs ?? null)}`
  );
}

function formatBargeInStopAcknowledgement(
  event: BargeInSemanticEvent | null,
): string {
  if (!event) {
    return 'NONE';
  }
  return (
    `requested=${yesNo(event.localStopRequested === true)} ` +
    `completed=${yesNo(event.localStopCompleted === true)} ` +
    `stopped=${yesNo(event.audioTrackStopped === true)} ` +
    `flushed=${yesNo(event.audioTrackFlushed === true)} ` +
    `released=${yesNo(event.audioTrackReleased === true)} ` +
    `pending=${yesNo(event.localStopReleasePending === true)} ` +
    `latency=${formatMilliseconds(event.localStopLatencyMs ?? null)} ` +
    `reason=${event.stopReason ?? 'N/A'} ` +
    `detectNs=${event.monotonicNs} stopNs=${
      event.stopRequestedMonotonicNs ?? 'N/A'
    }`
  );
}

function StatusRow({ label, value }: { label: string; value: string }) {
  const styles = useDiagnosticStyles();
  const longValue = value.length > 48;
  return (
    <View style={[styles.statusRow, longValue ? styles.longStatusRow : null]}>
      <Text
        style={[styles.statusLabel, longValue ? styles.longStatusLabel : null]}
      >
        {label}
      </Text>
      <Text
        style={[styles.statusValue, longValue ? styles.longStatusValue : null]}
      >
        {value}
      </Text>
    </View>
  );
}

const PermissionsAndroidResult = {
  GRANTED: 'granted',
} as const;

function useDiagnosticStyles() {
  const { colors } = useAppTheme();
  return useMemo(
    () =>
      StyleSheet.create({
        safeArea: {
          flex: 1,
          backgroundColor: colors.background,
        },
        content: {
          flexGrow: 1,
          justifyContent: 'center',
          padding: 16,
        },
        card: {
          borderRadius: 22,
          backgroundColor: colors.surface,
          padding: 16,
        },
        title: {
          color: colors.text,
          fontSize: 28,
          fontWeight: '700',
        },
        subtitle: {
          color: colors.textMuted,
          fontSize: 13,
          lineHeight: 20,
          marginTop: 8,
          marginBottom: 12,
          paddingHorizontal: 16,
        },
        controls: {
          gap: 10,
          padding: 16,
        },
        consentRow: {
          alignItems: 'center',
          flexDirection: 'row',
          gap: 10,
          marginBottom: 12,
        },
        consentText: {
          color: colors.textMuted,
          flex: 1,
          fontSize: 13,
        },
        sectionTitle: {
          color: colors.text,
          fontSize: 18,
          fontWeight: '700',
          marginTop: 16,
          marginBottom: 10,
        },
        subsectionTitle: {
          color: colors.textMuted,
          fontSize: 13,
          paddingHorizontal: 16,
          fontWeight: '700',
          marginTop: 14,
          marginBottom: 6,
        },
        statusRow: {
          flexDirection: 'row',
          justifyContent: 'space-between',
          gap: 12,
          paddingVertical: 8,
          paddingHorizontal: 16,
          borderBottomWidth: StyleSheet.hairlineWidth,
          borderBottomColor: colors.borderSubtle,
        },
        statusLabel: {
          color: colors.textMuted,
          flex: 1,
          fontSize: 13,
          lineHeight: 20,
        },
        statusValue: {
          color: colors.text,
          flex: 1,
          fontSize: 13,
          lineHeight: 20,
          fontWeight: '700',
          textAlign: 'right',
        },
        longStatusRow: { flexDirection: 'column', gap: 4 },
        longStatusLabel: { flex: 0 },
        longStatusValue: { flex: 0, textAlign: 'left', fontWeight: '500' },
        errorText: {
          color: colors.error,
          fontSize: 14,
          marginTop: 12,
        },
      }),
    [colors],
  );
}
