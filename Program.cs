using System.Text.Json;
using Mt5Bridge.QuoteViewer;

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls("http://localhost:" + (Environment.GetEnvironmentVariable("QUOTE_VIEWER_PORT") ?? "8877"));
builder.Logging.SetMinimumLevel(LogLevel.Warning);
builder.Services.AddSingleton<QuoteFeed>();
builder.Services.AddHostedService(services => services.GetRequiredService<QuoteFeed>());
var app = builder.Build();
app.UseDefaultFiles();
app.UseStaticFiles();
app.MapGet("/api/state", (QuoteFeed feed) => feed.Snapshot(-1));
app.MapGet("/events", async (HttpContext context, QuoteFeed feed) =>
{
    context.Response.ContentType = "text/event-stream";
    context.Response.Headers.CacheControl = "no-store";
    var version = -1L;
    using var timer = new PeriodicTimer(TimeSpan.FromMilliseconds(250));
    try
    {
        do
        {
            var snapshot = feed.Snapshot(version);
            version = snapshot.Version;
            var json = JsonSerializer.Serialize(snapshot, JsonSerializerOptions.Web);
            await context.Response.WriteAsync("data: " + json + "\n\n", context.RequestAborted);
            await context.Response.Body.FlushAsync(context.RequestAborted);
        }
        while (await timer.WaitForNextTickAsync(context.RequestAborted));
    }
    catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested)
    {
        // 浏览器关闭后结束本地显示流，不影响上游订阅。
    }
});
await app.RunAsync();
