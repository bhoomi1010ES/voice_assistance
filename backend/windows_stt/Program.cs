using System.Text.Json;
using VoiceAssistance.WindowsStt.Protocol;
using VoiceAssistance.WindowsStt.Recognition;

namespace VoiceAssistance.WindowsStt;

internal static class Program
{
    public static int Main()
    {
        return new Worker().Run();
    }
}

internal sealed class Worker
{
    private readonly object outputLock = new();
    private readonly Dictionary<TurnKey, TurnRecognizer> turns = new();
    private readonly JsonSerializerOptions jsonOptions = new(JsonSerializerDefaults.Web);
    private RecognizerManager? recognizers;
    private bool shuttingDown;

    public int Run()
    {
        try
        {
            recognizers = RecognizerManager.Discover();
            var preferredEnglish = recognizers.PreferredEnglishLanguage("en-US");
            Write(new
            {
                type = "READY",
                engine = "windows",
                runtime = "System.Speech.Recognition",
                available = recognizers.Recognizers.Count > 0,
                language = preferredEnglish,
                recognizer_name = preferredEnglish is null
                    ? null
                    : recognizers.Select(preferredEnglish).Name,
                languages = recognizers.Languages,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            });
        }
        catch (Exception error)
        {
            Write(new
            {
                type = "ERROR",
                code = "recognizer_unavailable",
                message = SafeError(error),
            });
            return 2;
        }

        string? line;
        while (!shuttingDown && (line = Console.ReadLine()) is not null)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }
            HandleLine(line);
        }
        ShutdownTurns();
        return 0;
    }

    private void HandleLine(string line)
    {
        JsonDocument? document = null;
        RequestContext? context = null;
        try
        {
            document = JsonDocument.Parse(line);
            var root = document.RootElement;
            var type = RequiredString(root, "type");
            context = TryReadContext(root);
            switch (type)
            {
                case "START_TURN":
                    HandleStart(root);
                    break;
                case "AUDIO":
                    HandleAudio(root);
                    break;
                case "COMMIT":
                    HandleCommit(root);
                    break;
                case "CANCEL":
                    HandleCancel(root);
                    break;
                case "SHUTDOWN":
                    HandleShutdown();
                    break;
                default:
                    WriteError(null, null, null, null, null, "unknown_command", $"Unknown command '{type}'.");
                    break;
            }
        }
        catch (WorkerCommandException error)
        {
            WriteError(
                context?.SessionId,
                context?.TurnId,
                context?.ResponseId,
                context?.Generation,
                null,
                error.Code,
                error.Message);
        }
        catch (JsonException error)
        {
            WriteError(
                context?.SessionId,
                context?.TurnId,
                context?.ResponseId,
                context?.Generation,
                null,
                "malformed_message",
                SafeError(error));
        }
        catch (Exception error)
        {
            WriteError(
                context?.SessionId,
                context?.TurnId,
                context?.ResponseId,
                context?.Generation,
                null,
                "worker_error",
                SafeError(error));
        }
        finally
        {
            document?.Dispose();
        }
    }

    private void HandleStart(JsonElement root)
    {
        var context = ReadContext(root);
        var requestedLanguage = OptionalString(root, "language") ?? "en-US";
        var key = new TurnKey(context.SessionId, context.TurnId);
        if (turns.ContainsKey(key))
        {
            throw new WorkerCommandException("duplicate_turn", "The turn is already active.");
        }
        var info = recognizers!.Select(requestedLanguage);
        var turn = new TurnRecognizer(
            context.SessionId,
            context.TurnId,
            context.ResponseId,
            context.Generation,
            info,
            hypothesis =>
            EmitPartial(context, hypothesis));
        turns.Add(key, turn);
        try
        {
            turn.Start();
            Write(new
            {
                type = "TURN_READY",
                session_id = context.SessionId,
                turn_id = context.TurnId,
                response_id = context.ResponseId,
                generation = context.Generation,
                language = info.Culture.Name,
                recognizer_name = info.Name,
            });
        }
        catch
        {
            turns.Remove(key);
            turn.Dispose();
            throw;
        }
    }

    private void HandleAudio(JsonElement root)
    {
        var context = ReadContext(root);
        var turn = GetTurn(context);
        var encoded = RequiredString(root, "audio_base64");
        byte[] audio;
        try
        {
            audio = Convert.FromBase64String(encoded);
        }
        catch (FormatException error)
        {
            throw new WorkerCommandException("malformed_audio", error.Message);
        }
        turn.PushAudio(audio);
    }

    private void HandleCommit(JsonElement root)
    {
        var context = ReadContext(root);
        var key = new TurnKey(context.SessionId, context.TurnId);
        if (!turns.TryGetValue(key, out var turn) || turn.IsCancelled)
        {
            throw new WorkerCommandException("unknown_turn", "The turn is not active.");
        }
        if (turn.IsCommitted)
        {
            throw new WorkerCommandException("duplicate_commit", "The turn was already committed.");
        }
        if (turn.ResponseId != context.ResponseId || context.Generation <= turn.Generation)
        {
            throw new WorkerCommandException(
                "correlation_mismatch",
                "The response ID or commit generation does not match the turn.");
        }
        turn.AdvanceGeneration(context.Generation);
        if (!turn.TryCommit())
        {
            throw new WorkerCommandException("duplicate_commit", "The turn was already committed or cancelled.");
        }
        _ = CompleteTurnAsync(context, key, turn);
    }

    private void HandleCancel(JsonElement root)
    {
        var context = ReadContext(root);
        var key = new TurnKey(context.SessionId, context.TurnId);
        if (!turns.TryGetValue(key, out var turn))
        {
            throw new WorkerCommandException("unknown_turn", "The turn is not active.");
        }
        if (turn.ResponseId != context.ResponseId || context.Generation <= turn.Generation)
        {
            throw new WorkerCommandException(
                "correlation_mismatch",
                "The response ID or cancellation generation does not match the turn.");
        }
        if (turn.TryCancel())
        {
            Write(new
            {
                type = "CANCELLED",
                session_id = context.SessionId,
                turn_id = context.TurnId,
                response_id = context.ResponseId,
                generation = context.Generation,
            });
            turns.Remove(key);
            turn.Dispose();
        }
    }

    private void HandleShutdown()
    {
        shuttingDown = true;
        ShutdownTurns();
        Write(new { type = "SHUTDOWN_ACK" });
    }

    private async Task CompleteTurnAsync(RequestContext context, TurnKey key, TurnRecognizer turn)
    {
        try
        {
            var result = await turn.FinishAsync().ConfigureAwait(false);
            if (turn.IsCancelled)
            {
                return;
            }
            Write(new
            {
                type = "FINAL",
                session_id = context.SessionId,
                turn_id = context.TurnId,
                response_id = context.ResponseId,
                generation = context.Generation,
                text = result.Text,
                language = turn.CultureName,
                confidence = result.Confidence,
                audio_duration_ms = result.AudioDurationMs,
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            });
        }
        catch (OperationCanceledException)
        {
            // CANCELLED was already emitted by HandleCancel.
        }
        catch (Exception error)
        {
            WriteError(
                context.SessionId,
                context.TurnId,
                context.ResponseId,
                context.Generation,
                turn.CultureName,
                "recognizer_error",
                SafeError(error));
        }
        finally
        {
            if (turns.Remove(key, out var removed))
            {
                removed.Dispose();
            }
        }
    }

    private void EmitPartial(RequestContext context, RecognitionHypothesis hypothesis)
    {
        if (!turns.TryGetValue(new TurnKey(context.SessionId, context.TurnId), out var turn)
            || turn.IsCancelled
            || turn.IsCommitted)
        {
            return;
        }
        Write(new
        {
            type = "PARTIAL",
            session_id = context.SessionId,
            turn_id = context.TurnId,
            response_id = context.ResponseId,
            generation = context.Generation,
            text = hypothesis.Text,
            language = turn.CultureName,
            confidence = hypothesis.Confidence,
            audio_duration_ms = hypothesis.AudioDurationMs,
            timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
        });
    }

    private TurnRecognizer GetTurn(RequestContext context)
    {
        if (!turns.TryGetValue(new TurnKey(context.SessionId, context.TurnId), out var turn))
        {
            throw new WorkerCommandException("unknown_turn", "The turn is not active.");
        }
        if (turn.IsCancelled)
        {
            throw new WorkerCommandException("stale_generation", "The turn correlation is no longer active.");
        }
        if (turn.IsCommitted)
        {
            throw new WorkerCommandException("audio_after_commit", "Audio arrived after commit.");
        }
        if (
            turn.ResponseId != context.ResponseId
            || turn.Generation != context.Generation
            || context.Generation < 0)
        {
            throw new WorkerCommandException("stale_generation", "The turn correlation is no longer active.");
        }
        return turn;
    }

    private static RequestContext ReadContext(JsonElement root)
    {
        return new RequestContext(
            RequiredGuid(root, "session_id"),
            RequiredGuid(root, "turn_id"),
            RequiredGuid(root, "response_id"),
            RequiredInt(root, "generation"));
    }

    private static RequestContext? TryReadContext(JsonElement root)
    {
        try
        {
            return ReadContext(root);
        }
        catch (WorkerCommandException)
        {
            return null;
        }
    }

    private void ShutdownTurns()
    {
        foreach (var turn in turns.Values.ToArray())
        {
            turn.TryCancel();
            turn.Dispose();
        }
        turns.Clear();
    }

    private void WriteError(
        Guid? sessionId,
        Guid? turnId,
        Guid? responseId,
        int? generation,
        string? language,
        string code,
        string message)
    {
        Write(new
        {
            type = "ERROR",
            session_id = sessionId,
            turn_id = turnId,
            response_id = responseId,
            generation,
            language,
            code,
            message,
        });
    }

    private void Write(object message)
    {
        var json = JsonSerializer.Serialize(message, jsonOptions);
        lock (outputLock)
        {
            Console.Out.WriteLine(json);
            Console.Out.Flush();
        }
    }

    private static string RequiredString(JsonElement root, string name)
    {
        if (!root.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.String)
        {
            throw new WorkerCommandException("malformed_message", $"Missing string field '{name}'.");
        }
        return value.GetString()!;
    }

    private static string? OptionalString(JsonElement root, string name)
    {
        return root.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;
    }

    private static Guid RequiredGuid(JsonElement root, string name)
    {
        var value = RequiredString(root, name);
        return Guid.TryParse(value, out var parsed)
            ? parsed
            : throw new WorkerCommandException("malformed_message", $"Field '{name}' is not a UUID.");
    }

    private static int RequiredInt(JsonElement root, string name)
    {
        if (!root.TryGetProperty(name, out var value) || !value.TryGetInt32(out var parsed))
        {
            throw new WorkerCommandException("malformed_message", $"Missing integer field '{name}'.");
        }
        return parsed;
    }

    private static string SafeError(Exception error)
    {
        return error.Message.Length > 256 ? error.Message[..256] : error.Message;
    }

    private sealed record RequestContext(Guid SessionId, Guid TurnId, Guid ResponseId, int Generation);
    private readonly record struct TurnKey(Guid SessionId, Guid TurnId);
}
