using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

if (args.Length != 1) { throw new ArgumentException("Pass the path to the private issuer configuration."); }
var configPath = Path.GetFullPath(args[0]);
var config = JsonSerializer.Deserialize<IssuerConfig>(File.ReadAllText(configPath), JsonSerializerOptions.Web)
    ?? throw new InvalidOperationException("Missing issuer configuration.");
if (config.LifetimeSeconds is < 1 or > 300) { throw new ArgumentOutOfRangeException(nameof(config.LifetimeSeconds)); }
var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
var expiresAt = now + config.LifetimeSeconds;
static string Encode(byte[] bytes) => Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
var header = Encode(JsonSerializer.SerializeToUtf8Bytes(new { alg = "RS256", typ = "JWT", kid = config.KeyId }));
var payload = Encode(JsonSerializer.SerializeToUtf8Bytes(new
{
    iss = config.Issuer, aud = config.Audience, sub = config.Subject,
    jti = Guid.NewGuid().ToString("N"), iat = now, nbf = now, exp = expiresAt,
    mt5bridge_grant = new
    {
        version = "1",
        referenceReads = new[] { new { sourceId = config.SourceId, group = config.Group, symbol = (string?)null } }
    }
}));
using var key = RSA.Create();
var keyPath = Path.GetFullPath(config.PrivateKeyFile, Path.GetDirectoryName(configPath)!);
key.ImportFromPem(File.ReadAllText(keyPath));
if (key.KeySize < 2048) { throw new InvalidOperationException("Use an RSA key of at least 2048 bits."); }
var content = header + "." + payload;
var signature = Encode(key.SignData(Encoding.ASCII.GetBytes(content), HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1));
Console.WriteLine(JsonSerializer.Serialize(new { token = content + "." + signature, expiresAt }));

internal sealed record IssuerConfig(string Issuer, string Audience, string KeyId, string Subject,
    string SourceId, string Group, string PrivateKeyFile, int LifetimeSeconds = 120);
