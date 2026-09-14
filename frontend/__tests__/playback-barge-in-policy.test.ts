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
