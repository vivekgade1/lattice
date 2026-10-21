// Layer A (N14): the `check` subcommand, the findings and the policy it reads, the helpers every rule shares, and DN800
// (the app's code does not parse: Roslyn syntax errors, ids only). DN804 (route order) is moot on endpoint routing and
// DN805 (error conventions) is gated for .NET: neither has code, `lattice/dncheck/policy.py` states why (DN_GATED).
// The rules read the same extraction `extract` makes (Ex.Run) and add nothing to it: B1's files are not edited.
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed class CFinding
{
    public string Code { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string Message { get; set; } = "";
    public string Hint { get; set; } = "";
    public string Key { get; set; } = "";
}

public sealed class CMachine
{
    public string Column = "";
    public HashSet<(string From, string To)> Edges = new();
    public HashSet<string> States = new(StringComparer.OrdinalIgnoreCase);
}

/// <summary>The guarantees the app is checked against (GoPolicy.to_json): names are the spec's (schema names, SQL column names).</summary>
public sealed class CheckPolicy
{
    public Dictionary<string, string> Tables = new(StringComparer.Ordinal);
    public string? UsersTable;
    public Dictionary<string, string> Owners = new(StringComparer.Ordinal);
    public Dictionary<string, (string Parent, string Fk)> OwnerVia = new(StringComparer.Ordinal);
    public Dictionary<string, List<string>> Private = new(StringComparer.Ordinal);
    public Dictionary<string, CMachine> Machines = new(StringComparer.Ordinal);
    public List<(string Method, string Path)> Admin = new(), Open = new();
    public string AdminRole = "Admin";
    public HashSet<string> Rules = new(StringComparer.Ordinal);

    public bool On(string code) => Rules.Count == 0 || Rules.Contains(code);

    static IEnumerable<JsonProperty> Props(JsonElement e, string k) => e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.Object ? v.EnumerateObject() : Enumerable.Empty<JsonProperty>();
    static IEnumerable<JsonElement> Items(JsonElement e) => e.ValueKind == JsonValueKind.Array ? e.EnumerateArray() : Enumerable.Empty<JsonElement>();
    static List<(string, string)> Pairs(JsonElement e, string k) => e.TryGetProperty(k, out var v) ? Items(v).Where(x => x.GetArrayLength() == 2).Select(x => (x[0].GetString()!, x[1].GetString()!)).ToList() : new();

    public static CheckPolicy From(JsonElement e)
    {
        var p = new CheckPolicy();
        if (e.ValueKind != JsonValueKind.Object) return p;
        foreach (var t in Props(e, "tables")) p.Tables[t.Name] = t.Value.TryGetProperty("sql", out var s) ? s.GetString() ?? t.Name : t.Name;
        p.UsersTable = e.TryGetProperty("users_table", out var u) && u.ValueKind == JsonValueKind.String && u.GetString() != "" ? u.GetString() : null;
        foreach (var o in Props(e, "owners")) p.Owners[o.Name] = o.Value.GetString()!;
        foreach (var o in Props(e, "owner_via")) p.OwnerVia[o.Name] = (Parent: o.Value[1].GetString()!, Fk: o.Value[0].GetString()!); // GoPolicy: [fk column, parent table]
        foreach (var o in Props(e, "private")) p.Private[o.Name] = Items(o.Value).Select(x => x.GetString()!).ToList();
        foreach (var m in Props(e, "machines"))
        {
            var cm = new CMachine { Column = m.Value.GetProperty("column").GetString()! };
            foreach (var edge in Items(m.Value.GetProperty("edges"))) { cm.Edges.Add((edge[0].GetString()!, edge[1].GetString()!)); cm.States.Add(edge[0].GetString()!); cm.States.Add(edge[1].GetString()!); }
            p.Machines[m.Name] = cm;
        }
        p.Admin = Pairs(e, "admin_routes"); p.Open = Pairs(e, "open_routes");
        if (e.TryGetProperty("admin_role", out var ar) && ar.ValueKind == JsonValueKind.String) p.AdminRole = ar.GetString()!;
        if (e.TryGetProperty("rules", out var rl)) foreach (var r in Items(rl)) p.Rules.Add(r.GetString()!);
        return p;
    }
}

/// <summary>`dncheck check`: {"root", "sdk"?, "policy": {...}} on stdin, {"findings": [...]} on stdout.</summary>
public sealed class CheckCommand : ICommand
{
    public string Name => "check";

    public string Run(string root, JsonElement input)
    {
        var pol = CheckPolicy.From(input.TryGetProperty("policy", out var p) ? p : default);
        var ex = new Ex(root, input.TryGetProperty("sdk", out var s) && s.ValueKind == JsonValueKind.String ? s.GetString()! : "");
        Exception? failed = null;
        try { ex.Run(); } catch (DnCheckException) { throw; } catch (Exception e) when (e is not OutOfMemoryException) { failed = e; }
        var found = ex.Parse(pol);
        if (found.Count == 0)
        {
            if (failed != null) throw new DnCheckException("the extraction failed: " + failed.Message);
            found = ex.RunChecks(pol);
        }
        return Js.Serialize(new { findings = found, stats = new { owned_routes = ex.StatOwned, judged_routes = ex.StatJudged, b1_complete_routes = ex.StatB1Complete } });
    }
}

public sealed partial class Ex
{
    /// <summary>Every rule over the extraction, sorted by file and line (DN800 alone when the code does not parse).</summary>
    public List<CFinding> RunChecks(CheckPolicy pol)
    {
        var found = new List<CFinding>();
        CheckOwnership(pol, found);
        CheckPrivate(pol, found);
        CheckBounds(pol, found);
        CheckDatetime(pol, found);
        CheckPartial(pol, found);
        CheckMachines(pol, found);
        return found.GroupBy(f => $"{f.Code}|{f.Key}|{f.File}|{f.Line}").Select(g => g.First())
            .OrderBy(f => f.File, StringComparer.Ordinal).ThenBy(f => f.Line).ThenBy(f => f.Code, StringComparer.Ordinal).ThenBy(f => f.Key, StringComparer.Ordinal).ToList();
    }

    /// <summary>DN800: one finding per compiled file with a syntax error, at its first error (the diagnostic ids only).</summary>
    public List<CFinding> Parse(CheckPolicy pol)
    {
        var found = new List<CFinding>();
        if (!pol.On("DN800")) return found;
        foreach (var t in Trees.Concat(MigrationTrees))
        {
            var errs = t.GetDiagnostics().Where(d => d.Severity == DiagnosticSeverity.Error).OrderBy(d => d.Location.SourceSpan.Start).ToList();
            if (errs.Count == 0) continue;
            found.Add(new CFinding
            {
                Code = "DN800", File = t.FilePath, Line = errs[0].Location.GetLineSpan().StartLinePosition.Line + 1, Key = "DN800|" + t.FilePath,
                Message = "the file does not parse: " + string.Join(", ", errs.Select(d => d.Id).Distinct().Take(5)),
                Hint = "fix the syntax error; the app cannot build",
            });
        }
        return found;
    }

    // ---- what the rules share --------------------------------------------------------------------------------------------

    static string Norm(string s) => s.Replace("_", "").ToLowerInvariant();
    static bool Same(string a, string b) => Norm(a) == Norm(b);

    /// <summary>The entity the policy's schema name stands for: by name, else by the SQL name the policy gives it.</summary>
    EntityInfo? EntityFor(CheckPolicy pol, string schema)
    {
        var byName = Entities.Values.FirstOrDefault(e => e.T.Name == schema);
        if (byName != null) return byName;
        return pol.Tables.TryGetValue(schema, out var sql) ? Entities.Values.FirstOrDefault(e => Same(TableNameOf(e), sql)) : null;
    }

    /// <summary>The policy's name for an entity (its schema name), else the entity's own.</summary>
    string SchemaOf(CheckPolicy pol, EntityInfo ei) =>
        pol.Tables.Keys.FirstOrDefault(k => k == ei.T.Name) ?? pol.Tables.Where(kv => Same(kv.Value, TableNameOf(ei))).Select(kv => kv.Key).FirstOrDefault() ?? ei.T.Name;

    PropInfo? PropFor(EntityInfo ei, string col) => ei.Props.FirstOrDefault(p => Same(p.Col.Name, col) || Same(p.P.Name, col));

    /// <summary>The routes the rules judge, in a stable order: read and with a reach.</summary>
    IEnumerable<RouteRec> Judged() => Recs.Where(r => !r.Framework && r.Reach != null).OrderBy(r => r.Id, StringComparer.Ordinal);

    bool AdminOnly(RouteRec r, CheckPolicy pol) => r.R.Auth == "Admin" || pol.Admin.Any(a => a.Method == r.R.Method && (a.Path == r.R.PathTemplate || a.Path == r.R.Path));

    bool OpenRoute(RouteRec r, CheckPolicy pol) => pol.Open.Any(a => a.Method == r.R.Method && (a.Path == r.R.PathTemplate || a.Path == r.R.Path));

    static CFinding Mk(string code, SyntaxNode at, string key, string message, string hint, int? line = null) =>
        new() { Code = code, File = FileOf(at), Line = line ?? LineOf(at), Key = key, Message = message, Hint = hint };

    static bool Derives(INamedTypeSymbol? t, ITypeSymbol? of)
    {
        for (var b = t; b != null; b = b.BaseType) if (SymbolEqualityComparer.Default.Equals(b.OriginalDefinition, of?.OriginalDefinition)) return true;
        return false;
    }

    /// <summary>The route whose reach contains the node (the first by id), or the label of code no route reaches.</summary>
    string RouteOf(SyntaxNode n) =>
        Judged().FirstOrDefault(r => r.Reach!.Visits.Any(v => v.Body.SyntaxTree == n.SyntaxTree && v.Body.Span.Contains(n.Span)))?.Id ?? "(not reached from a route)";
}
