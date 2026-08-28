import {
  NativeModules,
  PermissionsAndroid,
  Platform,
} from 'react-native';

export type VoiceDiagnostics = {
  nativeVoiceEngine: string;
  audioCapture: string;
  wakeWord: string;
  vad: string;
};

export type MicrophoneStatus = {
  permissionGranted: boolean;
  permissionStatus: string;
  state: string;
  isRecording: boolean;
  audioRecordInitialized: boolean;
  sampleRateHz: number;
  channelCount: number;
  encoding: string;
  bufferSizeBytes: number;
  minBufferSizeBytes: number;
  audioSessionId: number;
  pcmFramesCaptured: number;
  captureDurationMs: number;
  microphoneErrorCount: number;
  lastError: string | null;
};

export type AudioEffectStatus = {
  supported: boolean;
  available: boolean;
  requested: boolean;
  created: boolean;
  enabled: boolean;
  lastError: string | null;
};

export type AudioProcessingStatus = {
  audioSessionId: number;
  aec: AudioEffectStatus;
  noiseSuppression: AudioEffectStatus;
  manufacturer: string;
  model: string;
  androidSdk: number;
};

export type VadStatus = {
  enabled: boolean;
  sessionActive: boolean;
  state: 'SILENCE' | 'SPEECH';
  thresholdDbFs: number;
  lastEnergyDbFs: number;
  lastFrameClassification: 'NON_SPEECH' | 'SPEECH';
  frameDurationMs: number;
  frameSizeSamples: number;
  minimumSpeechDurationMs: number;
  minimumSilenceDurationMs: number;
  configuredSpeechStartConfirmationFrames: number;
  configuredSpeechEndConfirmationFrames: number;
  effectiveSpeechStartConfirmationFrames: number;
  effectiveSpeechEndConfirmationFrames: number;
  consecutiveSpeechFrames: number;
  consecutiveSilenceFrames: number;
  vadFramesProcessed: number;
  speechFrames: number;
  nonSpeechFrames: number;
  speechSegments: number;
  speechStartCount: number;
  speechStopCount: number;
  currentSpeechDurationMs: number;
  currentSilenceDurationMs: number;
  lastSpeechStartedFrameIndex: number;
  lastSpeechStoppedFrameIndex: number;
  vadErrorCount: number;
};

/** A low-frequency native state transition; it never contains PCM samples. */
export type VadEvent = {
  event: 'VAD_SPEECH_STARTED' | 'VAD_SPEECH_STOPPED';
  timestampMs: number;
  frameIndex: number;
  energyDbFs: number;
  speechDurationMs: number;
  speechSegmentCount: number;
  reason: string;
};

/** Silero metadata only. PCM input and recurrent tensors remain native. */
export type SileroVadStatus = {
  enabled: boolean;
  available: boolean;
  modelPresent: boolean;
  modelLoaded: boolean;
  modelName: string;
  modelVersion: string;
  modelGitTag: string;
  modelGitCommit: string;
  modelAssetPath: string;
  modelFormat: string;
  modelSizeBytes: number;
  modelSha256: string | null;
  modelSha256Verified: boolean;
  modelOnnxOpset: number;
  modelError: string | null;
  runtimeName: string;
  runtimeVersion: string;
  runtimeAvailable: boolean;
  runtimeInitialized: boolean;
  inferenceAvailable: boolean;
  sessionActive: boolean;
  running: boolean;
  workerThreadAlive: boolean;
  lifecycleState: string;
  state:
    | 'SILENCE'
    | 'SPEECH_START_PENDING'
    | 'SPEECH'
    | 'SPEECH_STOP_PENDING';
  speechProbabilityThreshold: number;
  speechStartConfirmationMs: number;
  speechStartConfirmationChunks: number;
  speechStopHangoverMs: number;
  speechStopConfirmationChunks: number;
  inputFrameDurationMs: number;
  inputFrameSizeSamples: number;
  inferenceChunkDurationMs: number;
  inferenceChunkSamples: number;
  modelContextSamples: number;
  queueDepthFrames: number;
  queueCapacityFrames: number;
  queueHighWaterMarkFrames: number;
  framesOffered: number;
  framesConsumed: number;
  droppedFrames: number;
  malformedFrames: number;
  inferenceCount: number;
  successfulInferenceCount: number;
  failedInferenceCount: number;
  averageInferenceDurationMs: number;
  maximumInferenceDurationMs: number;
  lastInferenceTimestampMs: number;
  currentProbability: number | null;
  speechStartCount: number;
  speechStopCount: number;
  resetCount: number;
  errorCount: number;
  lastErrorCode: string | null;
  lastErrorMessage: string | null;
};

export type SileroVadEvent = {
  event: 'SILERO_VAD_SPEECH_STARTED' | 'SILERO_VAD_SPEECH_STOPPED';
  timestampMs: number;
  probability: number;
  inferenceIndex: number;
  speechDurationMs: number;
  reason: string;
};

export type SileroVadErrorEvent = SileroVadStatus & {
  event: 'SILERO_VAD_ERROR';
  timestampMs: number;
};

export type WakeWordThresholdCount = {
  threshold: number;
  count: number;
};

export type WakeWordTrialThresholdResult = {
  threshold: number;
  detectionCount: number;
  duplicateSuppressionCount: number;
  firstDetectionLatencyMs: number | null;
};

export type WakeWordThresholdAnalysis = {
  threshold: number;
  positiveTrials: number;
  negativeTrials: number;
  trueAccepts: number;
  falseRejects: number;
  falseAccepts: number;
  trueNegatives: number;
  duplicateDetections: number;
  trueAcceptRate: number;
  falseRejectRate: number;
  falseAcceptRate: number;
  duplicateRate: number;
  medianDetectionLatencyMs: number | null;
  maximumDetectionLatencyMs: number | null;
};

export type WakeWordCalibrationTrial = {
  label: string;
  condition: string;
  attemptNumber: number;
  expectedPositive: boolean;
  audioProcessingMode: AudioProcessingCalibrationMode;
  aecEnabled: boolean;
  noiseSuppressionEnabled: boolean;
  startedAtTimestampMs: number;
  completedAtTimestampMs: number;
  firstInferenceIndex: number;
  lastInferenceIndex: number;
  inferenceWindowCount: number;
  minimumScore: number | null;
  maximumScore: number | null;
  averageScore: number;
  peakPcmAmplitude: number;
  peakPcmRms: number;
  peakPcmDbFs: number;
  maximumQueueDepthFrames: number;
  averageInferenceLatencyMs: number;
  maximumInferenceLatencyMs: number;
  detectionCount: number;
  duplicateDetectionCount: number;
  firstDetectionTimestampMs: number | null;
  firstDetectionLatencyMs: number | null;
  thresholdResults: WakeWordTrialThresholdResult[];
};

export type WakeWordAcousticDiagnostics = {
  available: boolean;
  enabled: boolean;
  pcmByteOrder: string;
  pcmScaling: string;
  byteSwapApplied: boolean;
  normalizationApplied: boolean;
  inferenceWindowCount: number;
  scoreMinimum: number | null;
  scoreMaximum: number | null;
  scoreAverage: number;
  scoreP50: number | null;
  scoreP90: number | null;
  scoreP95: number | null;
  scoreP99: number | null;
  thresholdCounts: WakeWordThresholdCount[];
  lastInferenceTimestampMs: number;
  lastInferenceIndex: number;
  lastClassifierScore: number | null;
  peakClassifierScore: number | null;
  lastPcmMinimum: number | null;
  lastPcmMaximum: number | null;
  lastPcmPeak: number;
  lastPcmRms: number;
  lastPcmDbFs: number;
  maximumObservedPcmRms: number;
  maximumObservedPcmDbFs: number;
  clippedSampleCount: number;
  lastQueueDepthFrames: number;
  lastInferenceLatencyMs: number;
  lastAecEnabled: boolean;
  lastNoiseSuppressionEnabled: boolean;
  activeTrialLabel: string | null;
  activeTrialCondition: string | null;
  activeTrialAttemptNumber: number | null;
  activeTrialExpectedPositive: boolean | null;
  completedPositiveTrials: number;
  completedNegativeTrials: number;
  positiveScoreMedian: number | null;
  positiveScoreMaximum: number | null;
  negativeScoreMedian: number | null;
  negativeScoreMaximum: number | null;
  medianDetectionLatencyMs: number | null;
  maximumDetectionLatencyMs: number | null;
  thresholdAnalysis: WakeWordThresholdAnalysis[];
  calibrationTrials: WakeWordCalibrationTrial[];
};

/** Temporary app-private PCM file metadata; no sample data crosses this API. */
export type WakeWordDiagnosticCaptureRecord = {
  captureId: string;
  label: string;
  fileName: string;
  startedAtTimestampMs: number;
  completedAtTimestampMs: number;
  durationMs: number;
  inferenceWindowsWritten: number;
  samplesWritten: number;
  bytesWritten: number;
  droppedWindows: number;
  sha256: string | null;
  valid: boolean;
  error: string | null;
};

export type WakeWordDiagnosticCaptureStatus = {
  diagnosticOnly: true;
  active: boolean;
  captureId: string | null;
  label: string | null;
  targetDurationMs: number;
  targetInferenceWindows: number;
  inferenceWindowsAccepted: number;
  inferenceWindowsWritten: number;
  queueDepthWindows: number;
  queueCapacityWindows: number;
  queueHighWaterMarkWindows: number;
  droppedWindows: number;
  completedCaptureCount: number;
  lastError: string | null;
  records: WakeWordDiagnosticCaptureRecord[];
};

export type WakeWordReplayResult = {
  captureId: string;
  pcmFileName: string;
  pcmSha256: string;
  repetition: number;
  traceFileName: string;
  inferenceCount: number;
  maximumEffectiveScore: number;
  maximumRawScore: number;
  runtimeErrorCount: number;
  elapsedMs: number;
};

export type WakeWordReplayBatchResult = {
  diagnosticOnly: true;
  runtimeName: string;
  runtimeVersion: string;
  repetitionCount: number;
  captureCount: number;
  replayCount: number;
  results: WakeWordReplayResult[];
};

/** openWakeWord metadata only; no field contains PCM or model tensors. */
export type WakeWordStatus = {
  enabled: boolean;
  available: boolean;
  modelPresent: boolean;
  modelName: string;
  modelVersion: string;
  modelReleaseTag: string;
  modelGitCommit: string;
  modelLicense: string;
  modelFormat: string;
  modelAssetDirectory: string;
  missingModelAssets: string;
  modelHashVerified: boolean;
  classifierSha256: string | null;
  runtimeName: string;
  runtimeVersion: string;
  runtimeAvailable: boolean;
  runtimeInitialized: boolean;
  tensorContractVerified: boolean;
  sessionActive: boolean;
  running: boolean;
  workerThreadAlive: boolean;
  state:
    | 'IDLE'
    | 'LISTENING'
    | 'WAKE_DETECTED'
    | 'COOLDOWN'
    | 'STOPPED'
    | 'ERROR';
  detectionThreshold: number;
  cooldownMs: number;
  cooldownRemainingMs: number;
  inputFrameDurationMs: number;
  inputFrameSizeSamples: number;
  inferenceWindowDurationMs: number;
  inferenceWindowSamples: number;
  queuedFrames: number;
  queueCapacityFrames: number;
  queueHighWaterMarkFrames: number;
  framesOffered: number;
  framesConsumed: number;
  inferenceCount: number;
  averageInferenceLatencyMs: number;
  maximumInferenceLatencyMs: number;
  detectionCount: number;
  duplicateSuppressionCount: number;
  droppedFrameCount: number;
  malformedFrameCount: number;
  runtimeErrorCount: number;
  lastDetectionTimestampMs: number;
  lastConfidence: number | null;
  pcmContextSamples: number;
  melHistoryFrames: number;
  melBins: number;
  embeddingHistoryFrames: number;
  embeddingFeatureSize: number;
  classifierOutputSemantics: string;
  acousticDiagnostics: WakeWordAcousticDiagnostics;
  lastErrorCode: string | null;
  lastErrorMessage: string | null;
};

export type WakeWordDetectionEvent = {
  event: 'WAKE_WORD_DETECTED';
  timestampMs: number;
  modelName: string;
  confidence: number;
  detectionCount: number;
  detectionSequenceNumber: number;
  inferenceIndex: number;
  inferenceTimestampMs: number;
  wakeStateBefore: string;
  wakeStateAfter: string;
  cooldownRemainingMs: number;
  millisecondsSincePreviousDetection: number | null;
  microphoneSessionId: number;
  workerGeneration: number;
  framesConsumed: number;
  queueDepthFrames: number;
  droppedFrameCount: number;
};

export type WakeWordManualDetection = {
  detectionSequenceNumber: number;
  classifierScore: number;
  inferenceWindowSequence: number;
  inferenceTimestampMs: number;
  wakeStateBefore: string;
  wakeStateAfter: string;
  cooldownRemainingMs: number;
  millisecondsSincePreviousDetection: number | null;
  workerGeneration: number;
};

export type WakeWordThresholdCrossing = {
  inferenceWindowSequence: number;
  inferenceTimestampMs: number;
  score: number;
  wakeStateBefore: string;
  wakeStateAfter: string;
  cooldownRemainingMs: number;
  generatedWakeEvent: boolean;
  suppressedByCooldown: boolean;
};

export type ManualWakeWordTrialStatus = {
  active: boolean;
  trialId: string | null;
  microphoneSessionId: number;
  startTimestampMs: number;
  stopTimestampMs: number;
  wakeDetectionCount: number;
  inferenceWindowCount: number;
  aboveThresholdWindowCount: number;
  maximumScore: number | null;
  maximumScoreTimestampMs: number;
  lastDetectionTimestampMs: number;
  lastDetectionIntervalMs: number | null;
  currentWakeState: string;
  cooldownActive: boolean;
  cooldownRemainingMs: number;
  cooldownDurationMs: number;
  queueDepthFrames: number;
  queueHighWaterMarkFrames: number;
  queueDrops: number;
  runtimeErrors: number;
  workerGeneration: number;
  aecEnabled: boolean;
  noiseSuppressionEnabled: boolean;
  pcmOverflowCount: number;
  wakeWorkerDropCount: number;
  audioRecordErrorCount: number;
  audioRecordReadErrorCount: number;
  pcmPipelineErrorCount: number;
  wakeRuntimeErrorCount: number;
  sileroRuntimeErrorCount: number;
  energyVadState: string;
  sileroVadState: string;
  energyVadSpeechStartCount: number;
  energyVadSpeechStopCount: number;
  sileroVadSpeechStartCount: number;
  sileroVadSpeechStopCount: number;
  detections: WakeWordManualDetection[];
  thresholdCrossings: WakeWordThresholdCrossing[];
  history: ManualWakeWordTrialSummary[];
};

export type ManualWakeWordTrialSummary = {
  trialId: string | null;
  microphoneSessionId: number;
  startTimestampMs: number;
  stopTimestampMs: number;
  wakeDetectionCount: number;
  inferenceWindowCount: number;
  aboveThresholdWindowCount: number;
  maximumScore: number | null;
  maximumScoreTimestampMs: number;
  lastDetectionTimestampMs: number;
  lastDetectionIntervalMs: number | null;
  currentWakeState: string;
  cooldownActive: boolean;
  cooldownRemainingMs: number;
  cooldownDurationMs: number;
  queueDepthFrames: number;
  queueHighWaterMarkFrames: number;
  queueDrops: number;
  runtimeErrors: number;
  workerGeneration: number;
  detections: WakeWordManualDetection[];
  thresholdCrossings: WakeWordThresholdCrossing[];
};

export type WakeWordStatusEvent = WakeWordStatus & {
  event:
    | 'WAKE_ENGINE_STARTED'
    | 'WAKE_ENGINE_STOPPED'
    | 'WAKE_ENGINE_ERROR';
};

/** Metadata and counters only. No field contains PCM sample data. */
export type AudioPipelineStatus = {
  recording: boolean;
  state: string;
  captureStarted: boolean;
  captureStopped: boolean;
  sampleRateHz: number;
  channelCount: number;
  pcmFormat: string;
  frameDurationMs: number;
  frameSizeSamples: number;
  frameSizeBytes: number;
  bufferedFrames: number;
  bufferedBytes: number;
  bufferCapacityFrames: number;
  bufferCapacityBytes: number;
  maxBufferedDurationMs: number;
  maxObservedBufferedFrames: number;
  totalPcmFramesCaptured: number;
  totalPcmBytesProcessed: number;
  framesWrittenToRingBuffer: number;
  framesConsumedFromRingBuffer: number;
  totalFramesProcessed: number;
  overflowCount: number;
  invalidReadCount: number;
  readErrorCount: number;
  pipelineErrorCount: number;
  partialFrameSamples: number;
  vad: VadStatus;
  sileroVad: SileroVadStatus;
};

/** Native WebSocket transport metadata only; PCM and tokens are excluded. */
export type VoiceGatewayStatus = {
  state: string;
  connected: boolean;
  sessionStarted: boolean;
  turnActive: boolean;
  sessionId: string | null;
  turnId: string | null;
  responseId: string | null;
  framesQueued: number;
  queueHighWaterMark: number;
  droppedFrames: number;
  invalidFrames: number;
  framesSent: number;
  bytesSent: number;
  websocketErrorCount: number;
  lastServerEvent: string | null;
  lastServerEventTimestampMs: number;
  lastError: string | null;
};

export type VoiceGatewayEvent = {
  event: string;
  sessionId: string | null;
  turnId: string | null;
  responseId: string | null;
};

type NativeVoiceModule = {
  getDiagnostics: () => Promise<VoiceDiagnostics>;
  startMicrophone: () => Promise<MicrophoneStatus>;
  stopMicrophone: () => Promise<MicrophoneStatus>;
  getMicrophoneStatus: () => Promise<MicrophoneStatus>;
  getAudioProcessingStatus: () => Promise<AudioProcessingStatus>;
  getAudioPipelineStatus: () => Promise<AudioPipelineStatus>;
  getWakeWordStatus: () => Promise<WakeWordStatus>;
  getManualWakeWordTrialStatus: () => Promise<ManualWakeWordTrialStatus>;
  setWakeWordCalibrationMode: (enabled: boolean) => Promise<WakeWordStatus>;
  beginWakeWordCalibrationTrial: (
    expectedPositive: boolean,
    condition: string,
  ) => Promise<WakeWordStatus>;
  resetWakeWordAcousticDiagnostics: () => Promise<WakeWordStatus>;
  startWakeWordDiagnosticPcmCapture: (
    label: string,
    durationMs: number,
  ) => Promise<WakeWordDiagnosticCaptureStatus>;
  stopWakeWordDiagnosticPcmCapture: () => Promise<WakeWordDiagnosticCaptureStatus>;
  getWakeWordDiagnosticPcmCaptureStatus: () => Promise<WakeWordDiagnosticCaptureStatus>;
  replayWakeWordDiagnosticPcm: (
    repetitions: number,
  ) => Promise<WakeWordReplayBatchResult>;
  deleteWakeWordDiagnosticData: () => Promise<{
    diagnosticOnly: true;
    deletedFileCount: number;
  }>;
  setAudioProcessingCalibrationMode: (
    mode: AudioProcessingCalibrationMode,
  ) => Promise<AudioProcessingStatus>;
  storeAuthTokens: (accessToken: string, refreshToken: string) => Promise<boolean>;
  clearAuthTokens: () => Promise<boolean>;
  connectVoiceGateway: (url: string) => Promise<VoiceGatewayStatus>;
  disconnectVoiceGateway: () => Promise<VoiceGatewayStatus>;
  startVoiceSession: (resumeSessionId?: string | null) => Promise<VoiceGatewayStatus>;
  startVoiceTurn: (clientTurnId?: string | null) => Promise<VoiceGatewayStatus>;
  commitVoiceAudio: (durationMs: number) => Promise<VoiceGatewayStatus>;
  cancelVoiceResponse: (reason?: string | null) => Promise<VoiceGatewayStatus>;
  endVoiceSession: (reason?: string | null) => Promise<VoiceGatewayStatus>;
  getVoiceGatewayStatus: () => Promise<VoiceGatewayStatus>;
};

export type AudioProcessingCalibrationMode =
  | 'AEC_NS'
  | 'AEC_ONLY'
  | 'NS_ONLY'
  | 'DISABLED';

const nativeVoiceModule = NativeModules.VoiceModule as
  | NativeVoiceModule
  | undefined;

function requireNativeVoiceModule(): NativeVoiceModule {
  if (Platform.OS !== 'android' || !nativeVoiceModule) {
    throw new Error('VoiceModule is only available on the Android build.');
  }

  return nativeVoiceModule;
}

/** Reads the original Phase 0.1 diagnostic state from the Android bridge. */
export async function getVoiceDiagnostics(): Promise<VoiceDiagnostics> {
  return requireNativeVoiceModule().getDiagnostics();
}

/** Checks the native AudioRecord/permission state without requesting access. */
export async function getMicrophoneStatus(): Promise<MicrophoneStatus> {
  return requireNativeVoiceModule().getMicrophoneStatus();
}

/** Reads device and session-bound Android AEC/NS diagnostics. */
export async function getAudioProcessingStatus(): Promise<AudioProcessingStatus> {
  return requireNativeVoiceModule().getAudioProcessingStatus();
}

/** Reads bounded native PCM pipeline metadata; raw samples never cross this API. */
export async function getAudioPipelineStatus(): Promise<AudioPipelineStatus> {
  return requireNativeVoiceModule().getAudioPipelineStatus();
}

/** Reads native openWakeWord worker/model/runtime metadata only. */
export async function getWakeWordStatus(): Promise<WakeWordStatus> {
  return requireNativeVoiceModule().getWakeWordStatus();
}

/** Reads bounded metadata for the manually controlled AudioRecord session. */
export async function getManualWakeWordTrialStatus(): Promise<ManualWakeWordTrialStatus> {
  return requireNativeVoiceModule().getManualWakeWordTrialStatus();
}

/** Marks one three-second metadata-only acoustic calibration trial. */
export async function beginWakeWordCalibrationTrial(
  expectedPositive: boolean,
  condition: string,
): Promise<WakeWordStatus> {
  return requireNativeVoiceModule().beginWakeWordCalibrationTrial(
    expectedPositive,
    condition,
  );
}

/** Explicitly enables/disables metadata-only per-inference calibration work. */
export async function setWakeWordCalibrationMode(
  enabled: boolean,
): Promise<WakeWordStatus> {
  return requireNativeVoiceModule().setWakeWordCalibrationMode(enabled);
}

export async function resetWakeWordAcousticDiagnostics(): Promise<WakeWordStatus> {
  return requireNativeVoiceModule().resetWakeWordAcousticDiagnostics();
}

/** Starts one finite app-private capture of exact 1,280-sample wake inputs. */
export async function startWakeWordDiagnosticPcmCapture(
  label: string,
  durationMs = 5120,
): Promise<WakeWordDiagnosticCaptureStatus> {
  return requireNativeVoiceModule().startWakeWordDiagnosticPcmCapture(
    label,
    durationMs,
  );
}

export async function stopWakeWordDiagnosticPcmCapture(): Promise<WakeWordDiagnosticCaptureStatus> {
  return requireNativeVoiceModule().stopWakeWordDiagnosticPcmCapture();
}

export async function getWakeWordDiagnosticPcmCaptureStatus(): Promise<WakeWordDiagnosticCaptureStatus> {
  return requireNativeVoiceModule().getWakeWordDiagnosticPcmCaptureStatus();
}

/** Replays app-private PCM natively; only score/file metadata is returned. */
export async function replayWakeWordDiagnosticPcm(
  repetitions = 2,
): Promise<WakeWordReplayBatchResult> {
  return requireNativeVoiceModule().replayWakeWordDiagnosticPcm(repetitions);
}

export async function deleteWakeWordDiagnosticData(): Promise<number> {
  const result = await requireNativeVoiceModule().deleteWakeWordDiagnosticData();
  return result.deletedFileCount;
}

/** Applies a reversible AEC/NS comparison mode for the next capture session. */
export async function setAudioProcessingCalibrationMode(
  mode: AudioProcessingCalibrationMode,
): Promise<AudioProcessingStatus> {
  return requireNativeVoiceModule().setAudioProcessingCalibrationMode(mode);
}

/** Starts AudioRecord after the UI has granted RECORD_AUDIO permission. */
export async function startMicrophone(): Promise<MicrophoneStatus> {
  return requireNativeVoiceModule().startMicrophone();
}

export async function stopMicrophone(): Promise<MicrophoneStatus> {
  return requireNativeVoiceModule().stopMicrophone();
}

/** Stores credentials through Android Keystore-backed native storage. */
export async function storeAuthTokens(
  accessToken: string,
  refreshToken: string,
): Promise<boolean> {
  return requireNativeVoiceModule().storeAuthTokens(accessToken, refreshToken);
}

export async function clearAuthTokens(): Promise<boolean> {
  return requireNativeVoiceModule().clearAuthTokens();
}

export async function connectVoiceGateway(url: string): Promise<VoiceGatewayStatus> {
  return requireNativeVoiceModule().connectVoiceGateway(url);
}

export async function disconnectVoiceGateway(): Promise<VoiceGatewayStatus> {
  return requireNativeVoiceModule().disconnectVoiceGateway();
}

export async function startVoiceSession(
  resumeSessionId?: string | null,
): Promise<VoiceGatewayStatus> {
  return requireNativeVoiceModule().startVoiceSession(resumeSessionId);
}

export async function startVoiceTurn(
  clientTurnId?: string | null,
): Promise<VoiceGatewayStatus> {
  return requireNativeVoiceModule().startVoiceTurn(clientTurnId);
}

export async function commitVoiceAudio(durationMs: number): Promise<VoiceGatewayStatus> {
  return requireNativeVoiceModule().commitVoiceAudio(durationMs);
}

export async function cancelVoiceResponse(
  reason?: string | null,
): Promise<VoiceGatewayStatus> {
  return requireNativeVoiceModule().cancelVoiceResponse(reason);
}

export async function endVoiceSession(
  reason?: string | null,
): Promise<VoiceGatewayStatus> {
  return requireNativeVoiceModule().endVoiceSession(reason);
}

export async function getVoiceGatewayStatus(): Promise<VoiceGatewayStatus> {
  return requireNativeVoiceModule().getVoiceGatewayStatus();
}

/**
 * Permission requests are initiated only by a foreground UI action. The
 * React Native PermissionsAndroid API routes the request through the active
 * Android activity rather than the native capture worker.
 */
export async function requestMicrophonePermission(): Promise<string> {
  if (Platform.OS !== 'android') {
    return 'denied';
  }

  return PermissionsAndroid.request(
    PermissionsAndroid.PERMISSIONS.RECORD_AUDIO,
    {
      title: 'Microphone permission',
      message: 'Voice AI needs microphone access for native audio capture.',
      buttonPositive: 'Allow',
      buttonNegative: 'Deny',
    },
  );
}
