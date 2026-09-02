using System.Globalization;
using System.Speech.Recognition;

namespace VoiceAssistance.WindowsStt.Recognition;

public sealed class RecognizerManager
{
    private readonly IReadOnlyList<RecognizerInfo> recognizers;

    private RecognizerManager(IReadOnlyList<RecognizerInfo> recognizers)
    {
        this.recognizers = recognizers;
    }

    public IReadOnlyList<RecognizerInfo> Recognizers => recognizers;

    public IReadOnlyList<string> Languages => recognizers
        .Select(item => item.Culture.Name)
        .Distinct(StringComparer.OrdinalIgnoreCase)
        .OrderBy(item => item, StringComparer.OrdinalIgnoreCase)
        .ToArray();

    public static RecognizerManager Discover()
    {
        var installed = SpeechRecognitionEngine.InstalledRecognizers();
        return new RecognizerManager(installed.ToArray());
    }

    public static bool LanguageMatches(CultureInfo culture, string requestedLanguage)
    {
        var requested = requestedLanguage.Trim().Replace('_', '-');
        if (requested.Length == 0)
        {
            return false;
        }

        if (requested.Length == 2)
        {
            return culture.TwoLetterISOLanguageName.Equals(
                requested,
                StringComparison.OrdinalIgnoreCase);
        }

        return culture.Name.Equals(requested, StringComparison.OrdinalIgnoreCase)
            || culture.TwoLetterISOLanguageName.Equals(
                requested.Split('-', 2)[0],
                StringComparison.OrdinalIgnoreCase);
    }

    public RecognizerInfo Select(string requestedLanguage)
    {
        var exact = recognizers.FirstOrDefault(item =>
            item.Culture.Name.Equals(
                requestedLanguage.Replace('_', '-'),
                StringComparison.OrdinalIgnoreCase));
        if (exact is not null)
        {
            return exact;
        }

        return recognizers.FirstOrDefault(item =>
                   LanguageMatches(item.Culture, requestedLanguage))
               ?? throw new WorkerCommandException(
                   "unsupported_language",
                   $"No installed Windows recognizer matches '{requestedLanguage}'.");
    }

    public string? PreferredEnglishLanguage(string preferred)
    {
        var english = Languages.Where(item =>
                item.Split('-', 2)[0].Equals("en", StringComparison.OrdinalIgnoreCase))
            .ToArray();
        return english.FirstOrDefault(item =>
                   item.Equals(preferred, StringComparison.OrdinalIgnoreCase))
               ?? english.FirstOrDefault();
    }
}

public sealed class WorkerCommandException : Exception
{
    public WorkerCommandException(string code, string message)
        : base(message)
    {
        Code = code;
    }

    public string Code { get; }
}
