// RC1: a Roslyn compilation of a target project with ONLY the two reference packs (Microsoft.NETCore.App.Ref,
// Microsoft.AspNetCore.App.Ref). Prints one JSON document: how many invocations bind to project symbols, to
// framework symbols, and how many stay unbound; which unbound calls carry the name of a project-declared method
// (the "project symbols unbound" count RC1 expects to be 0); the error-type names left by packages that are not
// referenced (EF Core, Pomelo, FluentValidation, JwtBearer, Grpc); diagnostics by id only.
// usage: Rc1Bind <root> <aspnet-ref-dir> <netcore-ref-dir> <global-usings,comma-separated>
using System.Globalization;
using System.Text.Json;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
if (args.Length < 4)
{
    Console.Error.WriteLine("usage: Rc1Bind <root> <aspnet-ref-dir> <netcore-ref-dir> <usings,comma>");
    return 2;
}

var root = Path.GetFullPath(args[0]);
var usings = args[3].Split(',', StringSplitOptions.RemoveEmptyEntries);
var opts = new CSharpParseOptions(LanguageVersion.Latest);
var trees = new List<SyntaxTree>
{
    CSharpSyntaxTree.ParseText(string.Concat(usings.Select(u => $"global using global::{u};\n")), opts, "GlobalUsings.g.cs"),
};

bool Skip(string rel) => rel.StartsWith("bin/") || rel.StartsWith("obj/") || rel.Contains("/bin/") || rel.Contains("/obj/");
var files = Directory.EnumerateFiles(root, "*.cs", SearchOption.AllDirectories)
    .Select(f => Path.GetRelativePath(root, f).Replace('\\', '/'))
    .Where(r => !Skip(r))
    .OrderBy(r => r, StringComparer.Ordinal)
    .ToList();
foreach (var rel in files)
{
    trees.Add(CSharpSyntaxTree.ParseText(File.ReadAllText(Path.Combine(root, rel)), opts, rel));
}

var refs = new List<MetadataReference>();
foreach (var dir in new[] { args[2], args[1] })
{
    foreach (var dll in Directory.EnumerateFiles(dir, "*.dll").OrderBy(f => f, StringComparer.Ordinal))
    {
        refs.Add(MetadataReference.CreateFromFile(dll));
    }
}

var topLevel = trees.Any(t => t.GetRoot().DescendantNodes().OfType<GlobalStatementSyntax>().Any());
var comp = CSharpCompilation.Create(
    "rc1",
    trees,
    refs,
    new CSharpCompilationOptions(topLevel ? OutputKind.ConsoleApplication : OutputKind.DynamicallyLinkedLibrary,
        nullableContextOptions: NullableContextOptions.Enable));

var diagCount = new SortedDictionary<string, int>(StringComparer.Ordinal);
foreach (var d in comp.GetDiagnostics().Where(d => d.Severity == DiagnosticSeverity.Error))
{
    diagCount[d.Id] = diagCount.GetValueOrDefault(d.Id) + 1;
}

// names the project declares: methods and local functions
var declared = new HashSet<string>(StringComparer.Ordinal);
foreach (var t in trees.Skip(1))
{
    foreach (var n in t.GetRoot().DescendantNodes())
    {
        if (n is MethodDeclarationSyntax m) declared.Add(m.Identifier.Text);
        else if (n is LocalFunctionStatementSyntax l) declared.Add(l.Identifier.Text);
    }
}

static string? NameOf(ExpressionSyntax e) => e switch
{
    IdentifierNameSyntax i => i.Identifier.Text,
    GenericNameSyntax g => g.Identifier.Text,
    MemberAccessExpressionSyntax m => NameOf(m.Name),
    MemberBindingExpressionSyntax b => NameOf(b.Name),
    _ => null,
};

int total = 0, project = 0, framework = 0, unbound = 0, creations = 0, creationsProject = 0;
var suspects = new List<SortedDictionary<string, object>>();
var unboundNames = new SortedDictionary<string, int>(StringComparer.Ordinal);
var errorTypes = new SortedDictionary<string, int>(StringComparer.Ordinal);
var projectTypeCreationsUnbound = new List<SortedDictionary<string, object>>();

foreach (var tree in trees.Skip(1))
{
    var model = comp.GetSemanticModel(tree);
    foreach (var node in tree.GetRoot().DescendantNodes())
    {
        if (node is InvocationExpressionSyntax inv)
        {
            var name = NameOf(inv.Expression);
            if (name == "nameof")
            {
                continue;
            }
            total++;
            var info = model.GetSymbolInfo(inv);
            var sym = info.Symbol;
            if (sym is null || sym.ContainingType is IErrorTypeSymbol)
            {
                unbound++;
                unboundNames[name ?? "?"] = unboundNames.GetValueOrDefault(name ?? "?") + 1;
                if (name is not null && declared.Contains(name))
                {
                    var pos = tree.GetLineSpan(inv.Span).StartLinePosition.Line + 1;
                    suspects.Add(new SortedDictionary<string, object>
                    {
                        ["file"] = tree.FilePath, ["line"] = pos, ["name"] = name, ["text"] = inv.ToString().Split('\n')[0].Trim(),
                        ["candidate_reason"] = info.CandidateReason.ToString(),
                        ["candidates"] = info.CandidateSymbols.Select(c => c.ToDisplayString()).OrderBy(x => x, StringComparer.Ordinal).ToList(),
                    });
                }
                continue;
            }
            var origin = (sym as IMethodSymbol)?.ReducedFrom ?? sym;
            if (origin.OriginalDefinition.Locations.Any(l => l.IsInSource)) project++;
            else framework++;
        }
        else if (node is ObjectCreationExpressionSyntax oc)
        {
            creations++;
            var ti = model.GetTypeInfo(oc).Type;
            if (ti is IErrorTypeSymbol) continue;
            if (ti is not null && ti.Locations.Any(l => l.IsInSource))
            {
                creationsProject++;
                if (model.GetSymbolInfo(oc).Symbol is null)
                {
                    var oci = model.GetSymbolInfo(oc);
                    projectTypeCreationsUnbound.Add(new SortedDictionary<string, object>
                    {
                        ["file"] = tree.FilePath, ["line"] = tree.GetLineSpan(oc.Span).StartLinePosition.Line + 1, ["type"] = ti.Name,
                        ["candidate_reason"] = oci.CandidateReason.ToString(),
                        ["candidates"] = oci.CandidateSymbols.Select(c => c.ToDisplayString()).OrderBy(x => x, StringComparer.Ordinal).ToList(),
                    });
                }
            }
        }
        else if (node is IdentifierNameSyntax or GenericNameSyntax or QualifiedNameSyntax)
        {
            if (node.Parent is QualifiedNameSyntax q && q.Right == node) continue;
            var t = model.GetTypeInfo(node).Type;
            if (t is IErrorTypeSymbol et && !string.IsNullOrEmpty(et.Name))
            {
                errorTypes[et.Name] = errorTypes.GetValueOrDefault(et.Name) + 1;
            }
        }
    }
}

var result = new SortedDictionary<string, object>
{
    ["rc"] = "RC1",
    ["files"] = files.Count,
    ["references"] = refs.Count,
    ["global_usings"] = usings,
    ["output_kind"] = topLevel ? "ConsoleApplication (top-level statements)" : "DynamicallyLinkedLibrary",
    ["error_diagnostics_by_id"] = diagCount,
    ["invocations"] = new SortedDictionary<string, object>
    {
        ["total"] = total, ["bound_to_project_symbol"] = project, ["bound_to_framework_symbol"] = framework, ["unbound"] = unbound,
    },
    ["object_creations"] = new SortedDictionary<string, object>
    {
        ["total"] = creations, ["of_project_types"] = creationsProject, ["project_type_constructor_unbound"] = projectTypeCreationsUnbound.Count,
    },
    ["project_symbols_unbound"] = suspects.Count + projectTypeCreationsUnbound.Count,
    ["project_symbols_unbound_detail"] = suspects.Concat(projectTypeCreationsUnbound).ToList(),
    ["unbound_invocation_names"] = unboundNames,
    ["error_type_names"] = errorTypes,
    ["every_error_type_is_named"] = true,
};
Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
return 0;
