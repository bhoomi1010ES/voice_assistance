import {
  evaluatePlaybackBargeIn,
  PLAYBACK_START_GUARD_MS,
} from '../src/voice/playbackBargeInPolicy';

test('rejects the measured loudspeaker startup echo window', () => {
  const decision = evaluatePlaybackBargeIn({
    playbackActive: true,
    playbackStartedAtMs: 10_000,
    candidateAtMs: 10_000 + PLAYBACK_START_GUARD_MS - 1,
    probability: 0.99,
  });

  expect(decision).toMatchObject({
    accepted: false,
    reason: 'likely_playback_echo',
  });
});

test('accepts sustained near-end speech after the playback guard', () => {
  const decision = evaluatePlaybackBargeIn({
    playbackActive: true,
    playbackStartedAtMs: 10_000,
    candidateAtMs: 10_000 + PLAYBACK_START_GUARD_MS,
    probability: 0.9,
  });

  expect(decision).toMatchObject({
    accepted: true,
    reason: 'near_end_speech',
  });
});

test('rejects playback candidates below the high-confidence threshold', () => {
  const decision = evaluatePlaybackBargeIn({
    playbackActive: true,
    playbackStartedAtMs: 10_000,
    candidateAtMs: 10_000 + PLAYBACK_START_GUARD_MS,
    probability: 0.89,
  });

  expect(decision).toMatchObject({
    accepted: false,
    reason: 'likely_playback_echo',
  });
});

test('rejects playback-time candidates without a Silero probability', () => {
  const decision = evaluatePlaybackBargeIn({
    playbackActive: true,
    playbackStartedAtMs: 10_000,
    candidateAtMs: 10_000 + PLAYBACK_START_GUARD_MS,
  });

  expect(decision).toMatchObject({
    accepted: false,
    reason: 'likely_playback_echo',
  });
});

test('rejects a high-probability candidate that matches the playback reference', () => {
  const decision = evaluatePlaybackBargeIn({
    playbackActive: true,
    playbackStartedAtMs: 10_000,
    candidateAtMs: 12_000,
    probability: 0.99,
    playbackReferenceAvailable: true,
    playbackState: 'TTS_PLAYING',
    echoLikely: true,
    echoSimilarity: 0.94,
  });

  expect(decision).toMatchObject({
    accepted: false,
    reason: 'likely_playback_echo',
    echoSimilarity: 0.94,
  });
});

test('rejects playback candidates when native reference evidence is unavailable', () => {
  const decision = evaluatePlaybackBargeIn({
    playbackActive: true,
    playbackStartedAtMs: 10_000,
    candidateAtMs: 12_000,
    probability: 0.99,
    playbackReferenceAvailable: false,
    playbackState: 'TTS_PLAYING',
  });

  expect(decision).toMatchObject({
    accepted: false,
    reason: 'likely_playback_echo',
  });
});

test('keeps normal VAD unchanged when TTS is inactive', () => {
  expect(
    evaluatePlaybackBargeIn({
      playbackActive: false,
      playbackStartedAtMs: null,
      candidateAtMs: 20,
      probability: 0.5,
    }),
  ).toMatchObject({ accepted: true, reason: 'normal_vad' });
});
