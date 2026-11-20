using System.Net;
using System.Net.WebSockets;
using System.Reflection;
using System.Text;
using System.Text.Json;
using Mt5Bridge.QuoteViewer;

if (args.Contains("token", StringComparer.Ordinal))
{
    var expiresAt = DateTimeOffset.UtcNow.AddSeconds(9).ToUnixTimeSeconds();
    Console.WriteLine(JsonSerializer.Serialize(new { token = expiresAt.ToString(System.Globalization.CultureInfo.InvariantCulture), expiresAt }));
    return;
}

var builder = WebApplication.CreateBuilder(args);
builder.Logging.ClearProviders();
builder.WebHost.ConfigureKestrel(options => options.Listen(IPAddress.Loopback, 0));
await using var server = builder.Build();
server.UseWebSockets();
var reject = false;
var hold = false;
WebSocket? active = null;
var serverConnections = 0;
server.Map("/v1/sources/example-source/quotes/stream", async context =>
{
    using var socket = await context.WebSockets.AcceptWebSocketAsync();
    active = socket;
    Interlocked.Increment(ref serverConnections);
    using var stopped = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted);
    using var sends = new SemaphoreSlim(1);
    try
    {
        var subscription = await Receive(socket, stopped.Token);
        Check(subscription.GetProperty("all").GetBoolean(), "all-group subscription");
        var expiry = DateTimeOffset.FromUnixTimeSeconds(long.Parse(context.Request.Headers.Authorization.ToString()[7..], System.Globalization.CultureInfo.InvariantCulture));
        var quotes = SendQuotes();
        try
        {
            while (!stopped.IsCancellationRequested)
            {
                var request = await Receive(socket, stopped.Token);
                Check(request.GetProperty("type").GetString() == "authorization", "renewal type");
                if (!reject) { expiry = DateTimeOffset.FromUnixTimeSeconds(long.Parse(request.GetProperty("token").GetString()!, System.Globalization.CultureInfo.InvariantCulture)); }
                await Send(new { schemaVersion = "1", type = "authorization", status = reject ? "rejected" : "accepted", code = reject ? "grant_invalid" : null, expiresAt = expiry });
            }
        }
        finally
        {
            await stopped.CancelAsync();
            try { await quotes; } catch (Exception e) when (e is OperationCanceledException or WebSocketException) { }
        }

        async Task SendQuotes()
        {
            while (!stopped.IsCancellationRequested)
            {
                if (!hold)
                {
                    await Send(new { type = "quote", sourceId = "example-source", contextGroup = "demo\\group", symbol = "BTCUSD", readiness = "ready", observedAt = DateTimeOffset.UtcNow, maximumDataAgeMilliseconds = 2000, sourceAgeMilliseconds = 0, data = new { bid = "100.01", ask = "100.02", last = "100.01" } });
                }
                await Task.Delay(100, stopped.Token);
            }
        }
        async Task Send(object message)
        {
            await sends.WaitAsync(stopped.Token);
            try { await socket.SendAsync(JsonSerializer.SerializeToUtf8Bytes(message), WebSocketMessageType.Text, true, stopped.Token); }
            finally { sends.Release(); }
        }
    }
    catch (Exception e) when (e is OperationCanceledException or WebSocketException or IOException) { }
});
await server.StartAsync();
Environment.SetEnvironmentVariable("QUOTE_VIEWER_UPSTREAM", server.Urls.Single().Replace("http:", "ws:", StringComparison.Ordinal));
Environment.SetEnvironmentVariable("QUOTE_VIEWER_SOURCE", "example-source");
Environment.SetEnvironmentVariable("QUOTE_VIEWER_GROUP", "demo\\group");
Environment.SetEnvironmentVariable("QUOTE_VIEWER_REMOTE", "local protocol fixture");
Environment.SetEnvironmentVariable("QUOTE_VIEWER_TOKEN_COMMAND", JsonSerializer.Serialize(new[] { "dotnet", Assembly.GetExecutingAssembly().Location, "token" }));

using var feed = new QuoteFeed();
await feed.StartAsync(CancellationToken.None);
try
{
    await Until(() => feed.Snapshot(-1).Ready == 1);
    var initial = feed.Snapshot(-1);
    await Until(() => feed.Snapshot(-1).Renewals >= 1);
    var firstRenewalExpiry = feed.Snapshot(-1).TokenExpiresAt;
    await Until(() => DateTimeOffset.UtcNow > firstRenewalExpiry && feed.Snapshot(-1).Renewals >= 2, () =>
    {
        var state = feed.Snapshot(-1);
        Check(state.Connections == 1 && state.Symbols == 1 && state.Ready == 1, "same connection and quote cache across expiry");
    });
    var renewed = feed.Snapshot(-1);
    Check(serverConnections == 1 && renewed.Messages > initial.Messages && renewed.TokenExpiresAt > firstRenewalExpiry, "two accepted renewals beyond both old expiry times");
    Console.WriteLine("PASS renewal: same WebSocket, continuous quotes and cache across two expiry boundaries");

    reject = true;
    var beforeRejection = feed.Snapshot(-1);
    await Until(() => feed.Snapshot(-1).Error?.Contains("grant_invalid", StringComparison.Ordinal) == true);
    var rejected = feed.Snapshot(-1);
    Check(rejected.TokenExpiresAt == beforeRejection.TokenExpiresAt && rejected.Renewals == beforeRejection.Renewals && rejected.Ready == 1, "rejection preserves existing authorization and quote delivery");
    Console.WriteLine("PASS rejection: no expiry extension, no false success, quotes still received");

    reject = false;
    hold = true;
    active!.Abort();
    await Until(() => feed.Snapshot(-1).State == "disconnected");
    Check(feed.Snapshot(-1) is { Symbols: 1, Ready: 0 }, "retain stale quote on disconnect");
    await Until(() => feed.Snapshot(-1).Connections == 2);
    Check(feed.Snapshot(-1) is { Symbols: 1, Ready: 0 }, "old quote stays stale after reconnect until fresh data");
    hold = false;
    await Until(() => feed.Snapshot(-1).Ready == 1);
    Console.WriteLine("PASS reconnect: last quote retained, stale until new connection receives data");
}
finally
{
    await feed.StopAsync(CancellationToken.None);
    await server.StopAsync();
}

static async Task<JsonElement> Receive(WebSocket socket, CancellationToken cancellationToken)
{
    using var data = new MemoryStream();
    var buffer = new byte[4096];
    WebSocketReceiveResult part;
    do
    {
        part = await socket.ReceiveAsync(buffer, cancellationToken);
        if (part.MessageType == WebSocketMessageType.Close) { throw new IOException("closed"); }
        data.Write(buffer, 0, part.Count);
    } while (!part.EndOfMessage);
    using var json = JsonDocument.Parse(data.ToArray());
    return json.RootElement.Clone();
}
static async Task Until(Func<bool> condition, Action? invariant = null)
{
    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(35));
    while (!condition())
    {
        invariant?.Invoke();
        await Task.Delay(50, timeout.Token);
    }
}
static void Check(bool condition, string message)
{
    if (!condition) { throw new InvalidOperationException(message); }
}
