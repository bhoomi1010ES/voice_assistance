using System.Globalization;
using VoiceAssistance.WindowsStt.Protocol;
using VoiceAssistance.WindowsStt.Recognition;
using Xunit;

namespace VoiceAssistance.WindowsStt.Tests;

public sealed class WorkerContractTests
{
    [Fact]
    public void AudioContractAcceptsEvenSizedPcm16Chunk()
    {
        Assert.True(AudioFormatContract.IsValidPcm16Mono16Khz(new byte[640]));
        Assert.False(AudioFormatContract.IsValidPcm16Mono16Khz(Array.Empty<byte>()));
        Assert.False(AudioFormatContract.IsValidPcm16Mono16Khz(new byte[3]));
    }

    [Theory]
    [InlineData("en-US", "en-US", true)]
    [InlineData("en-US", "en", true)]
    [InlineData("en-GB", "en-US", true)]
    [InlineData("fr-FR", "en", false)]
    public void LanguageMatcherRequiresRequestedCultureOrLanguage(
        string installed,
        string requested,
        bool expected)
    {
        Assert.Equal(expected, RecognizerManager.LanguageMatches(
            CultureInfo.GetCultureInfo(installed),
            requested));
    }
}
