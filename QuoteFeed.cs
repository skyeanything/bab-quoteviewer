using System.Diagnostics;
using System.Globalization;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;

namespace Mt5Bridge.QuoteViewer;

internal sealed record QuoteRow(string Symbol, string? Bid, string? Ask, string? Last,
    string Readiness, DateTimeOffset ReceivedAt, DateTimeOffset ObservedAt, long MaximumAge, long? SourceAge,
    long Updates, int Direction, string Raw, long Version, long Connection);

internal sealed record FeedSnapshot(string SourceId, string Group, string Remote, string State,
    string? Error, long Connections, long Messages, long PriceChanges, int ChangedSymbols,
    int Symbols, int WithData, int Ready, long Version, DateTimeOffset? LastReceivedAt,
    DateTimeOffset? TokenExpiresAt, long Renewals, bool Renewing, QuoteRow[] Rows);

internal sealed class QuoteFeed : BackgroundService
{
    private readonly object _gate = new();
    private readonly Dictionary<string, QuoteRow> _rows = new(StringComparer.Ordinal);
    private readonly HashSet<string> _changedSymbols = new(StringComparer.Ordinal);
    private readonly string _group = Required("QUOTE_VIEWER_GROUP");
    private readonly string _source = Required("QUOTE_VIEWER_SOURCE");
    private readonly string _remote = Required("QUOTE_VIEWER_REMOTE");
    private string _state = "connecting";
    private string? _error;
    private long _version;
    private long _connections;
    private long _messages;
    private long _renewals;
    private bool _renewing;
    private long _priceChanges;
    private DateTimeOffset? _lastReceived;
    private DateTimeOffset? _expires;

    internal FeedSnapshot Snapshot(long after)
    {
        lock (_gate)
        {
            var now = DateTimeOffset.UtcNow;
            return new(_source, _group, _remote, _state, _error, _connections, _messages,
                _priceChanges, _changedSymbols.Count, _rows.Count,
                _rows.Values.Count(row => row.Bid is not null),
                _state == "connected" ? _rows.Values.Count(row => row.Readiness == "ready" && row.Connection == _connections
                    && Math.Max(0, (now - row.ObservedAt).TotalMilliseconds) + (row.SourceAge ?? 0) < row.MaximumAge) : 0,
                _version, _lastReceived, _expires, _renewals, _renewing,
                _rows.Values.Where(row => row.Version > after).ToArray());
        }
    }

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                SetState("authorizing");
                var (token, expires) = await IssueTokenAsync(stoppingToken);
                lock (_gate) { _expires = expires; }
                using var socket = new ClientWebSocket();
                socket.Options.SetRequestHeader("Authorization", "Bearer " + token);
                var address = Required("QUOTE_VIEWER_UPSTREAM") + "/v1/sources/" + Uri.EscapeDataString(_source)
                    + "/quotes/stream?group=" + Uri.EscapeDataString(_group);
                SetState("connecting");
                using (var connect = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken))
                {
                    connect.CancelAfter(TimeSpan.FromSeconds(15));
                    await socket.ConnectAsync(new Uri(address), connect.Token);
                    await socket.SendAsync(Encoding.UTF8.GetBytes("{\"all\":true}"), WebSocketMessageType.Text, true, connect.Token);
                }
                lock (_gate)
                {
                    _connections++;
                    _state = "connected";
                    _error = null;
                    _version++;
                }
                await RunSessionAsync(socket, expires, stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception exception) when (exception is WebSocketException or IOException or JsonException
                or OperationCanceledException or InvalidOperationException or TimeoutException)
            {
                Console.Error.WriteLine("Quote connection ended: " + exception.Message);
                SetState("disconnected", exception.Message);
                try { await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken); }
                catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            }
        }
        SetState("stopped");
    }

    private async Task RunSessionAsync(ClientWebSocket socket, DateTimeOffset expires, CancellationToken stoppingToken)
    {
        using var session = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
        session.CancelAfter(Remaining(expires));
        TaskCompletionSource<JsonElement>? pending = null;
        var receive = ReceiveAsync();
        var renew = RenewAsync();
        try
        {
            await await Task.WhenAny(receive, renew);
        }
        finally
        {
            await session.CancelAsync();
            try { await Task.WhenAll(receive, renew); }
            catch (Exception exception) when (exception is OperationCanceledException or WebSocketException
                or IOException or JsonException or InvalidOperationException or TimeoutException)
            {
                // 主任务负责报告错误；关闭并等待另一任务，避免遗留接收或续期操作。
            }
            lock (_gate) { _renewing = false; }
        }

        async Task ReceiveAsync()
        {
            var buffer = new byte[65536];
            while (!session.IsCancellationRequested)
            {
                using var message = new MemoryStream();
                WebSocketReceiveResult result;
                do
                {
                    result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), session.Token);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        throw new IOException($"服务器关闭连接：{(int?)result.CloseStatus} {result.CloseStatusDescription}");
                    }
                    message.Write(buffer, 0, result.Count);
                }
                while (!result.EndOfMessage);
                var raw = Encoding.UTF8.GetString(message.GetBuffer(), 0, checked((int)message.Length));
                using var document = JsonDocument.Parse(raw);
                var value = document.RootElement;
                if (value.GetProperty("type").GetString() == "authorization")
                {
                    Volatile.Read(ref pending)?.TrySetResult(value.Clone());
                }
                else
                {
                    Observe(raw);
                }
            }
        }

        async Task RenewAsync()
        {
            while (!session.IsCancellationRequested)
            {
                var lifetime = Remaining(expires);
                var lead = TimeSpan.FromSeconds(Math.Min(30, lifetime.TotalSeconds / 3));
                await Task.Delay(lifetime - lead, session.Token);
                var accepted = false;
                while (!accepted && !session.IsCancellationRequested)
                {
                    lock (_gate) { _renewing = true; _version++; }
                    try
                    {
                        var (token, _) = await IssueTokenAsync(session.Token);
                        var response = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
                        Volatile.Write(ref pending, response);
                        var request = JsonSerializer.SerializeToUtf8Bytes(new { type = "authorization", token });
                        await socket.SendAsync(request, WebSocketMessageType.Text, true, session.Token);
                        var ack = await response.Task.WaitAsync(TimeSpan.FromSeconds(10), session.Token);
                        if (ack.GetProperty("status").GetString() == "accepted")
                        {
                            expires = ack.GetProperty("expiresAt").GetDateTimeOffset();
                            session.CancelAfter(Remaining(expires));
                            lock (_gate)
                            {
                                _expires = expires;
                                _renewals++;
                                _renewing = false;
                                _error = null;
                                _version++;
                            }
                            accepted = true;
                        }
                        else
                        {
                            lock (_gate) { _error = "授权续期被拒绝：" + ack.GetProperty("code").GetString(); _version++; }
                        }
                    }
                    catch (IOException)
                    {
                        lock (_gate) { _error = "暂时无法获取新授权，将在当前授权到期前重试。"; _version++; }
                    }
                    finally { Volatile.Write(ref pending, null); }
                    if (!accepted) { await Task.Delay(TimeSpan.FromSeconds(5), session.Token); }
                }
            }
        }
    }

    private static TimeSpan Remaining(DateTimeOffset expires)
    {
        var remaining = expires - DateTimeOffset.UtcNow;
        return remaining > TimeSpan.Zero ? remaining : TimeSpan.Zero;
    }

    private void Observe(string raw)
    {
        using var document = JsonDocument.Parse(raw);
        var value = document.RootElement;
        if (value.GetProperty("sourceId").GetString() != _source || value.GetProperty("contextGroup").GetString() != _group)
        {
            throw new InvalidOperationException("收到的报价不属于当前来源或账户组。");
        }
        var symbol = value.GetProperty("symbol").GetString()!;
        var data = value.GetProperty("data");
        string? Price(string key) => data.ValueKind == JsonValueKind.Object ? data.GetProperty(key).GetString() : null;
        var bid = Price("bid"); var ask = Price("ask"); var last = Price("last");
        lock (_gate)
        {
            _rows.TryGetValue(symbol, out var previous);
            var direction = 0;
            if (bid is not null && previous?.Bid is not null)
            {
                if (bid != previous.Bid || ask != previous.Ask || last != previous.Last)
                {
                    _priceChanges++;
                    _changedSymbols.Add(symbol);
                }
                if (decimal.TryParse(bid, NumberStyles.Float, CultureInfo.InvariantCulture, out var current)
                    && decimal.TryParse(previous.Bid, NumberStyles.Float, CultureInfo.InvariantCulture, out var old))
                {
                    direction = current.CompareTo(old);
                }
            }
            _lastReceived = DateTimeOffset.UtcNow;
            _messages++;
            var age = value.GetProperty("sourceAgeMilliseconds");
            _rows[symbol] = new(symbol, bid, ask, last, value.GetProperty("readiness").GetString()!,
                _lastReceived.Value, value.GetProperty("observedAt").GetDateTimeOffset(),
                value.GetProperty("maximumDataAgeMilliseconds").GetInt64(),
                age.ValueKind == JsonValueKind.Number ? age.GetInt64() : null,
                (previous?.Updates ?? 0) + 1, direction, raw, ++_version, _connections);
        }
    }

    private static async Task<(string Token, DateTimeOffset Expires)> IssueTokenAsync(CancellationToken cancellationToken)
    {
        var command = JsonSerializer.Deserialize<string[]>(Required("QUOTE_VIEWER_TOKEN_COMMAND"))!;
        var start = new ProcessStartInfo(command[0]) { RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false };
        foreach (var argument in command.Skip(1)) { start.ArgumentList.Add(argument); }
        using var process = Process.Start(start) ?? throw new IOException("无法启动授权进程。");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        var output = process.StandardOutput.ReadToEndAsync(timeout.Token);
        var error = process.StandardError.ReadToEndAsync(timeout.Token);
        try { await process.WaitForExitAsync(timeout.Token); }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new IOException("获取授权超时。");
        }
        finally { if (!process.HasExited) { process.Kill(entireProcessTree: true); } }
        var text = await output;
        _ = await error;
        if (process.ExitCode != 0) { throw new IOException("获取授权失败，请检查令牌提供程序。"); }
        using var result = JsonDocument.Parse(text);
        return (result.RootElement.GetProperty("token").GetString()!,
            DateTimeOffset.FromUnixTimeSeconds(result.RootElement.GetProperty("expiresAt").GetInt64()));
    }

    private void SetState(string state, string? error = null)
    {
        lock (_gate) { _state = state; _error = error; _version++; }
    }

    private static string Required(string name) => Environment.GetEnvironmentVariable(name)
        ?? throw new InvalidOperationException("缺少环境变量：" + name + "。请按 README 配置后启动。");
}
