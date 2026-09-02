using System.Speech.AudioFormat;

namespace VoiceAssistance.WindowsStt.Protocol;

public static class AudioFormatContract
{
    public const int SampleRateHz = 16_000;
    public const short Channels = 1;
    public const short BitsPerSample = 16;

    public static bool IsValidPcm16Mono16Khz(byte[] audio)
    {
        return audio.Length > 0 && audio.Length % 2 == 0;
    }

    public static SpeechAudioFormatInfo CreateSpeechFormat()
    {
        return new SpeechAudioFormatInfo(
            SampleRateHz,
            AudioBitsPerSample.Sixteen,
            AudioChannel.Mono);
    }
}
