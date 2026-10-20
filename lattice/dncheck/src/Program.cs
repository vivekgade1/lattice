// dncheck: the Roslyn helper. `dncheck extract` reads {"root", "sdk"?, "workflow"?, "policy"?} on stdin and answers
// lattice.dnextract/1 on stdout; any other subcommand is looked up among the ICommand classes other nodes add.
// Exit codes: 0 ok, 2 bad input, 3 failure (stderr says why). The target is only read; the output carries no
// absolute path, time or host.
using System.Globalization;
using System.Text;
using System.Text.Json;
using DnCheck;

CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
CultureInfo.DefaultThreadCurrentUICulture = CultureInfo.InvariantCulture;
CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;
CultureInfo.CurrentUICulture = CultureInfo.InvariantCulture;

var sub = args.Length > 0 ? args[0] : "check";
var raw = await Console.In.ReadToEndAsync();
JsonElement input;
try { input = JsonDocument.Parse(raw).RootElement; }
catch (JsonException) { Console.Error.WriteLine("dncheck: the input must be JSON {\"root\": <directory>}"); return 2; }
var root = input.TryGetProperty("root", out var r) && r.ValueKind == JsonValueKind.String ? r.GetString() : null;
if (string.IsNullOrEmpty(root) || !Directory.Exists(root)) { Console.Error.WriteLine("dncheck: the root is not a directory: " + root); return 2; }

try
{
    var cmd = typeof(ICommand).Assembly.GetTypes().Where(t => typeof(ICommand).IsAssignableFrom(t) && t is { IsClass: true, IsAbstract: false }).Select(t => (ICommand)Activator.CreateInstance(t)!).FirstOrDefault(c => c.Name == sub);
    string output;
    if (cmd != null) output = cmd.Run(Path.GetFullPath(root), input);
    else if (sub == "extract")
    {
        string sdk = input.TryGetProperty("sdk", out var s) && s.ValueKind == JsonValueKind.String ? s.GetString()! : DefaultSdk();
        bool Flag(string k) => input.TryGetProperty(k, out var f) && f.ValueKind == JsonValueKind.True;
        var ws = input.TryGetProperty("workspace", out var w) && w.ValueKind == JsonValueKind.String && Directory.Exists(w.GetString()) ? Path.GetFullPath(w.GetString()!) : null;
        var ex = new Ex(Path.GetFullPath(root), sdk, ws) { Workflow = Flag("workflow"), Policy = Flag("policy") };
        output = Js.Serialize(ex.Run());
    }
    else { Console.Error.WriteLine("dncheck: unknown subcommand " + sub); return 2; }
    using var stdout = Console.OpenStandardOutput();
    var bytes = new UTF8Encoding(false).GetBytes(output + "\n");
    stdout.Write(bytes, 0, bytes.Length);
    return 0;
}
catch (DnCheckException e) { Console.Error.WriteLine("dncheck: " + e.Message); return 3; }
catch (Exception e) { Console.Error.WriteLine("dncheck: internal error: " + e); return 3; }

static string DefaultSdk()
{
    var rt = Path.GetDirectoryName(typeof(object).Assembly.Location)!;
    var sdks = Path.GetFullPath(Path.Combine(rt, "..", "..", "..", "sdk"));
    return Directory.Exists(sdks) ? Directory.GetDirectories(sdks).Select(Path.GetFileName).Where(n => Version.TryParse(n!.Split('-')[0], out _)).OrderBy(n => Version.Parse(n!.Split('-')[0])).LastOrDefault() ?? "" : "";
}

namespace DnCheck
{
    /// <summary>A subcommand added by another node (the layer A check is the default one).</summary>
    public interface ICommand
    {
        string Name { get; }
        string Run(string root, JsonElement input);
    }
}
