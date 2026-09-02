using System.Collections.Concurrent;
using System.Speech.Recognition;
using System.Text;
using VoiceAssistance.WindowsStt.Protocol;

namespace VoiceAssistance.WindowsStt.Recognition;

public sealed record RecognitionHypothesis(string Text, double Confidence, int AudioDurationMs);

public sealed record RecognitionFinal(
    string Text,
    double? Confidence,
    int AudioDurationMs);

public sealed class TurnRecognizer : IDisposable
{
    private readonly SpeechRecognitionEngine recognizer;
    private readonly BoundedPcmStream audioStream;
    private readonly Action<RecognitionHypothesis> hypothesisCallback;
    private readonly object stateLock = new();
    private readonly StringBuilder recognizedText = new();
    private readonly TaskCompletionSource<RecognitionFinal> completion =
        new(TaskCreationOptions.RunContinuationsAsynchronously);
    private bool committed;
    private bool cancelled;
    private bool disposed;
    private bool recognitionStarted;
    private int audioBytes;
    private double? confidence;

    public TurnRecognizer(
        Guid sessionId,
        Guid turnId,
        Guid responseId,
        int generation,
        RecognizerInfo recognizerInfo,
        Action<RecognitionHypothesis> hypothesisCallback)
    {
        SessionId = sessionId;
        TurnId = turnId;
        ResponseId = responseId;
        Generation = generation;
        CultureName = recognizerInfo.Culture.Name;
        RecognizerName = recognizerInfo.Name;
        this.hypothesisCallback = hypothesisCallback;
        audioStream = new BoundedPcmStream();
        recognizer = new SpeechRecognitionEngine(recognizerInfo);
        recognizer.SpeechHypothesized += OnSpeechHypothesized;
        recognizer.SpeechRecognized += OnSpeechRecognized;
        recognizer.RecognizeCompleted += OnRecognizeCompleted;
        recognizer.LoadGrammar(new DictationGrammar());
        recognizer.InitialSilenceTimeout = TimeSpan.FromSeconds(5);
        recognizer.BabbleTimeout = TimeSpan.FromSeconds(5);
        recognizer.EndSilenceTimeout = TimeSpan.FromMilliseconds(700);
        recognizer.EndSilenceTimeoutAmbiguous = TimeSpan.FromMilliseconds(1200);
    }

    public string CultureName { get; }

    public string RecognizerName { get; }

    public Guid SessionId { get; }

    public Guid TurnId { get; }

    public Guid ResponseId { get; }

    public int Generation { get; private set; }

    public void AdvanceGeneration(int generation)
    {
        lock (stateLock)
        {
            ThrowIfDisposed();
            if (generation <= Generation)
            {
                throw new WorkerCommandException(
                    "stale_generation",
                    "The commit generation must advance the active turn.");
            }
            Generation = generation;
        }
    }

    public bool IsCommitted
    {
        get { lock (stateLock) return committed; }
    }

    public bool IsCancelled
    {
        get { lock (stateLock) return cancelled; }
    }

    public void Start()
    {
        lock (stateLock)
        {
            ThrowIfDisposed();
            if (cancelled)
            {
                throw new WorkerCommandException("cancelled", "Turn was cancelled before start.");
            }
        }
    }

    public void PushAudio(byte[] audio)
    {
        if (!AudioFormatContract.IsValidPcm16Mono16Khz(audio))
        {
            throw new WorkerCommandException(
                "invalid_format",
                "Audio must be non-empty, mono, signed PCM16 little-endian at 16 kHz.");
        }

        lock (stateLock)
        {
            ThrowIfDisposed();
            if (cancelled)
            {
                throw new WorkerCommandException("cancelled", "Turn was cancelled.");
            }
            if (committed)
            {
                throw new WorkerCommandException("audio_after_commit", "Audio arrived after commit.");
            }
            audioBytes = checked(audioBytes + audio.Length);
        }

        audioStream.Push(audio);
        StartRecognitionIfNeeded();
    }

    public bool TryCommit()
    {
        var startRecognition = false;
        lock (stateLock)
        {
            ThrowIfDisposed();
            if (cancelled || committed)
            {
                return false;
            }
            committed = true;
            startRecognition = !recognitionStarted;
        }
        if (startRecognition)
        {
            StartRecognitionIfNeeded();
        }
        audioStream.Complete();
        return true;
    }

    public Task<RecognitionFinal> FinishAsync()
    {
        return completion.Task;
    }

    public bool TryCancel()
    {
        lock (stateLock)
        {
            if (disposed || cancelled)
            {
                return false;
            }
            cancelled = true;
            completion.TrySetCanceled();
        }
        audioStream.Cancel();
        try
        {
            recognizer.RecognizeAsyncCancel();
        }
        catch (InvalidOperationException)
        {
            // Recognition may already have completed; cancellation still
            // invalidates all future callbacks through the state guard.
        }
        return true;
    }

    public void Dispose()
    {
        lock (stateLock)
        {
            if (disposed)
            {
                return;
            }
            disposed = true;
            cancelled = true;
        }
        audioStream.Cancel();
        recognizer.SpeechHypothesized -= OnSpeechHypothesized;
        recognizer.SpeechRecognized -= OnSpeechRecognized;
        recognizer.RecognizeCompleted -= OnRecognizeCompleted;
        recognizer.Dispose();
        audioStream.Dispose();
    }

    private void StartRecognitionIfNeeded()
    {
        lock (stateLock)
        {
            ThrowIfDisposed();
            if (cancelled || recognitionStarted)
            {
                return;
            }
            recognitionStarted = true;
        }
        try
        {
            recognizer.SetInputToAudioStream(audioStream, AudioFormatContract.CreateSpeechFormat());
            recognizer.RecognizeAsync(RecognizeMode.Multiple);
        }
        catch
        {
            lock (stateLock)
            {
                recognitionStarted = false;
            }
            throw;
        }
    }

    private void OnSpeechHypothesized(object? sender, SpeechHypothesizedEventArgs args)
    {
        if (!CanEmitCallbacks(allowCommitted: false))
        {
            return;
        }
        var text = args.Result?.Text?.Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        hypothesisCallback(new RecognitionHypothesis(text, args.Result.Confidence, AudioDurationMs()));
    }

    private void OnSpeechRecognized(object? sender, SpeechRecognizedEventArgs args)
    {
        if (!CanEmitCallbacks(allowCommitted: true))
        {
            return;
        }
        var text = args.Result?.Text?.Trim();
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        lock (stateLock)
        {
            if (recognizedText.Length > 0)
            {
                recognizedText.Append(' ');
            }
            recognizedText.Append(text);
            confidence = args.Result.Confidence;
        }
    }

    private void OnRecognizeCompleted(object? sender, RecognizeCompletedEventArgs args)
    {
        lock (stateLock)
        {
            if (cancelled || disposed)
            {
                completion.TrySetCanceled();
                return;
            }
            if (args.Error is not null)
            {
                completion.TrySetException(args.Error);
                return;
            }
            if (args.Cancelled)
            {
                completion.TrySetCanceled();
                return;
            }
            var text = recognizedText.ToString().Trim();
            if (string.IsNullOrWhiteSpace(text) && args.Result is not null)
            {
                text = args.Result.Text?.Trim() ?? string.Empty;
                confidence = args.Result.Confidence;
            }
            completion.TrySetResult(new RecognitionFinal(text, confidence, AudioDurationMs()));
        }
    }

    private bool CanEmitCallbacks(bool allowCommitted)
    {
        lock (stateLock)
        {
            return !cancelled && !disposed && (allowCommitted || !committed);
        }
    }

    private int AudioDurationMs()
    {
        lock (stateLock)
        {
            return (int)Math.Round(audioBytes / 2.0 / AudioFormatContract.SampleRateHz * 1000.0);
        }
    }

    private void ThrowIfDisposed()
    {
        if (disposed)
        {
            throw new ObjectDisposedException(nameof(TurnRecognizer));
        }
    }
}

internal sealed class BoundedPcmStream : Stream
{
    // Android sends 20 ms / 640-byte PCM chunks. Keep a finite startup
    // cushion for SpeechRecognitionEngine without buffering an entire turn.
    private const int Capacity = 256;
    private readonly BlockingCollection<byte[]> chunks = new(
        new ConcurrentQueue<byte[]>(),
        Capacity);
    private readonly CancellationTokenSource cancellation = new();
    private byte[]? current;
    private int currentOffset;
    private long bytesAccepted;
    private long bytesRead;

    public override bool CanRead => true;
    // SpeechRecognitionEngine probes Position/Seek when it attaches an
    // audio stream, even though recognition itself remains forward-only.
    public override bool CanSeek => true;
    public override bool CanWrite => false;
    // The producer stream has no fixed file length. A finite value here would
    // make SpeechRecognitionEngine stop at the first currently queued chunk.
    public override long Length => long.MaxValue;
    public override long Position
    {
        get => Interlocked.Read(ref bytesRead);
        set
        {
            if (value != 0 || Interlocked.Read(ref bytesRead) != 0)
            {
                throw new NotSupportedException("The recognition stream is forward-only.");
            }
        }
    }

    public void Push(byte[] audio)
    {
        try
        {
            if (!chunks.TryAdd(audio, 1000, cancellation.Token))
            {
                throw new WorkerCommandException("audio_backpressure", "Recognizer audio queue is full.");
            }
            Interlocked.Add(ref bytesAccepted, audio.Length);
        }
        catch (InvalidOperationException)
        {
            throw new WorkerCommandException("audio_after_commit", "Audio stream is closed.");
        }
        catch (OperationCanceledException)
        {
            throw new WorkerCommandException("cancelled", "Audio stream was cancelled.");
        }
    }

    public void Complete()
    {
        if (!chunks.IsAddingCompleted)
        {
            chunks.CompleteAdding();
        }
    }

    public void Cancel()
    {
        cancellation.Cancel();
        Complete();
    }

    public override int Read(byte[] buffer, int offset, int count)
    {
        ArgumentNullException.ThrowIfNull(buffer);
        if (offset < 0 || count < 0 || offset + count > buffer.Length)
        {
            throw new ArgumentOutOfRangeException();
        }
        if (count == 0)
        {
            return 0;
        }

        var copied = 0;
        while (copied < count)
        {
            while (current is null || currentOffset >= current.Length)
            {
                try
                {
                    current = chunks.Take(cancellation.Token);
                    currentOffset = 0;
                }
                catch (InvalidOperationException)
                {
                    Interlocked.Add(ref bytesRead, copied);
                    return copied;
                }
                catch (OperationCanceledException)
                {
                    Interlocked.Add(ref bytesRead, copied);
                    return copied;
                }
            }

            var available = Math.Min(count - copied, current.Length - currentOffset);
            Buffer.BlockCopy(current, currentOffset, buffer, offset + copied, available);
            currentOffset += available;
            copied += available;
        }
        Interlocked.Add(ref bytesRead, copied);
        return copied;
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            Cancel();
            cancellation.Dispose();
            chunks.Dispose();
        }
        base.Dispose(disposing);
    }

    public override void Flush() { }
    public override long Seek(long offset, SeekOrigin origin)
    {
        if (offset == 0 && origin is SeekOrigin.Begin or SeekOrigin.Current)
        {
            return Position;
        }
        throw new NotSupportedException("The recognition stream is forward-only.");
    }
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
}
