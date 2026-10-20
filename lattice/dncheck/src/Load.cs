// Loading: the target project's *.cs files (never restored, built or written), the implicit global usings its csproj
// implies, and the installed reference packs as the only metadata references. Nothing absolute reaches the output:
// every tree is named by its path relative to the root, with '/' separators.
using System.Text.RegularExpressions;
using System.Xml.Linq;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed class UsingItem
{
    public string Name = "";
    public bool Static;
    public string? Alias;
}

public sealed class ProjectInfo
{
    public string Dir = "", File = "", Sdk = "", Tfm = "net10.0", Nullable = "", RootNamespace = "", AssemblyName = "";
    public bool ImplicitUsings;
    public List<UsingItem> Usings = new();
    public List<string> Packages = new();
    public List<string> ProjectRefs = new();
    public List<string> CompileRemove = new();
    public List<string> CompileInclude = new();
    public List<string> Defines = new();
    public bool DefaultCompile = true;
    public bool IsWeb => Sdk.EndsWith(".Web", StringComparison.Ordinal) || Packages.Any(p => p.StartsWith("Microsoft.AspNetCore.", StringComparison.Ordinal)) || HasFrameworkRef;
    public bool HasFrameworkRef;
}

public sealed class DnCheckException(string message) : Exception(message);

public sealed partial class Ex
{
    public readonly string Root;
    public readonly string SdkVersion;
    /// <summary>The import root a ProjectReference must stay inside (default: the project directory).</summary>
    public string Workspace;
    public bool Policy, Workflow;
    public ProjectInfo P = new();
    public List<ProjectInfo> Projects = new();
    public CSharpCompilation Comp = null!;
    public readonly List<SyntaxTree> Trees = new();
    public readonly List<SyntaxTree> MigrationTrees = new();
    readonly Dictionary<SyntaxTree, SemanticModel> models = new();
    public readonly Doc D = new();
    public readonly List<Unread> UnreadAll = new();
    readonly HashSet<string> unreadSeen = new(StringComparer.Ordinal);

    public Ex(string root, string sdk, string? workspace = null) { Root = root; SdkVersion = sdk; Workspace = workspace ?? root; }

    public SemanticModel Sm(SyntaxNode n)
    {
        var t = n.SyntaxTree;
        if (!models.TryGetValue(t, out var m)) models[t] = m = Comp.GetSemanticModel(t);
        return m;
    }

    public static string Rel(string root, string abs) => Path.GetRelativePath(root, abs).Replace('\\', '/');

    static readonly string[] BaseUsings = ["System", "System.Collections.Generic", "System.IO", "System.Linq", "System.Net.Http", "System.Threading", "System.Threading.Tasks"];
    static readonly string[] WebUsings = ["System.Net.Http.Json", "Microsoft.AspNetCore.Builder", "Microsoft.AspNetCore.Hosting", "Microsoft.AspNetCore.Http", "Microsoft.AspNetCore.Routing", "Microsoft.Extensions.Configuration", "Microsoft.Extensions.DependencyInjection", "Microsoft.Extensions.Hosting", "Microsoft.Extensions.Logging"];
    static readonly string[] WorkerUsings = ["Microsoft.Extensions.Configuration", "Microsoft.Extensions.DependencyInjection", "Microsoft.Extensions.Hosting", "Microsoft.Extensions.Logging"];

    static string? Prop(XElement proj, string name) => proj.Elements("PropertyGroup").Elements(name).Select(e => e.Value.Trim()).LastOrDefault(v => v.Length > 0);

    ProjectInfo ReadProject(string csproj)
    {
        var doc = XDocument.Load(csproj);
        var proj = doc.Root ?? throw new DnCheckException("empty project file");
        var p = new ProjectInfo { Dir = Path.GetDirectoryName(csproj)!, File = Path.GetFileName(csproj) };
        p.Sdk = (string?)proj.Attribute("Sdk") ?? "Microsoft.NET.Sdk";
        p.Tfm = Prop(proj, "TargetFramework") ?? Prop(proj, "TargetFrameworks")?.Split(';')[0] ?? "net10.0";
        p.Nullable = Prop(proj, "Nullable") ?? "";
        p.ImplicitUsings = (Prop(proj, "ImplicitUsings") ?? "").ToLowerInvariant() is "enable" or "true";
        p.RootNamespace = Prop(proj, "RootNamespace") ?? Path.GetFileNameWithoutExtension(csproj);
        p.AssemblyName = Prop(proj, "AssemblyName") ?? Path.GetFileNameWithoutExtension(csproj);
        p.DefaultCompile = (Prop(proj, "EnableDefaultCompileItems") ?? "true").ToLowerInvariant() != "false";
        foreach (var d in (Prop(proj, "DefineConstants") ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries)) p.Defines.Add(d.Trim());
        foreach (var e in proj.Descendants())
        {
            switch (e.Name.LocalName)
            {
                case "PackageReference": p.Packages.Add((string?)e.Attribute("Include") ?? ""); break;
                case "FrameworkReference": if ((string?)e.Attribute("Include") == "Microsoft.AspNetCore.App") p.HasFrameworkRef = true; break;
                case "ProjectReference": p.ProjectRefs.Add(((string?)e.Attribute("Include") ?? "").Replace('\\', '/')); break;
                case "Compile":
                    if ((string?)e.Attribute("Remove") is { } rm) p.CompileRemove.AddRange(rm.Split(';').Select(x => x.Replace('\\', '/')));
                    if ((string?)e.Attribute("Include") is { } inc) p.CompileInclude.AddRange(inc.Split(';').Select(x => x.Replace('\\', '/')));
                    break;
                case "Using":
                    if ((string?)e.Attribute("Remove") is { } ur) p.Usings.RemoveAll(u => u.Name == ur);
                    else if ((string?)e.Attribute("Include") is { } ui) p.Usings.Add(new UsingItem { Name = ui, Static = ((string?)e.Attribute("Static") ?? "").ToLowerInvariant() == "true", Alias = (string?)e.Attribute("Alias") });
                    break;
            }
        }
        return p;
    }

    static Regex Glob(string pattern)
    {
        var sb = new System.Text.StringBuilder("^");
        for (int i = 0; i < pattern.Length; i++)
        {
            char c = pattern[i];
            if (c == '*' && i + 1 < pattern.Length && pattern[i + 1] == '*')
            {
                i++;
                if (i + 1 < pattern.Length && pattern[i + 1] == '/') { i++; sb.Append("(?:.*/)?"); } else sb.Append(".*");
            }
            else if (c == '*') sb.Append("[^/]*");
            else if (c == '?') sb.Append("[^/]");
            else sb.Append(Regex.Escape(c.ToString()));
        }
        return new Regex(sb.Append('$').ToString(), RegexOptions.CultureInvariant);
    }

    static bool Under(string rel, string dir) => rel.StartsWith(dir + "/", StringComparison.Ordinal);

    IEnumerable<string> SourceFiles(ProjectInfo p)
    {
        var nested = Directory.EnumerateFiles(p.Dir, "*.csproj", SearchOption.AllDirectories).Where(f => !Path.GetFileName(f).StartsWith('.'))
            .Select(f => Rel(p.Dir, Path.GetDirectoryName(f)!)).Where(d => d != ".").ToList();
        var removes = p.CompileRemove.Select(Glob).ToList();
        var includes = p.CompileInclude.Select(Glob).ToList();
        var files = new List<string>();
        foreach (var f in Directory.EnumerateFiles(p.Dir, "*.cs", SearchOption.AllDirectories))
        {
            var rel = Rel(p.Dir, f);
            if (Path.GetFileName(f).StartsWith('.')) continue; // AppleDouble (._x.cs) and other hidden files
            if (rel.StartsWith("bin/", StringComparison.Ordinal) || rel.StartsWith("obj/", StringComparison.Ordinal)) continue;
            if (rel.Split('/').Any(s => s is ".git" or "node_modules")) continue;
            bool listed = includes.Any(g => g.IsMatch(rel));
            if (!listed && (!p.DefaultCompile || nested.Any(d => Under(rel, d)))) continue;
            if (removes.Any(g => g.IsMatch(rel))) continue;
            files.Add(f);
        }
        return files;
    }

    static string PackVersionDir(string pack)
    {
        var root = Environment.GetEnvironmentVariable("DOTNET_ROOT");
        if (string.IsNullOrEmpty(root) || !Directory.Exists(Path.Combine(root, "packs")))
        {
            var rt = Path.GetDirectoryName(typeof(object).Assembly.Location)!; // <root>/shared/Microsoft.NETCore.App/<ver>
            root = Path.GetFullPath(Path.Combine(rt, "..", "..", ".."));
        }
        var dir = Path.Combine(root, "packs", pack);
        if (!Directory.Exists(dir)) throw new DnCheckException($"reference pack {pack} is not installed under {root}/packs");
        var best = Directory.GetDirectories(dir).Where(d => Version.TryParse(Path.GetFileName(d).Split('-')[0], out _))
            .OrderBy(d => Version.Parse(Path.GetFileName(d).Split('-')[0])).ThenBy(d => d, StringComparer.Ordinal).LastOrDefault()
            ?? throw new DnCheckException($"no version of {pack} is installed");
        var refDir = Directory.GetDirectories(Path.Combine(best, "ref")).OrderBy(d => d, StringComparer.Ordinal).Last();
        return refDir;
    }

    public void Load()
    {
        var csprojs = Directory.GetFiles(Root, "*.csproj").Where(f => !Path.GetFileName(f).StartsWith('.')).OrderBy(f => f, StringComparer.Ordinal).ToList();
        if (csprojs.Count != 1) throw new DnCheckException(csprojs.Count == 0 ? "no *.csproj directly in the root" : "more than one *.csproj in the root: " + string.Join(", ", csprojs.Select(Path.GetFileName)));
        P = ReadProject(csprojs[0]);
        Projects.Add(P);
        var seen = new HashSet<string>(StringComparer.Ordinal) { Path.GetFullPath(csprojs[0]) };
        for (int i = 0; i < Projects.Count; i++)
        {
            foreach (var r in Projects[i].ProjectRefs.OrderBy(x => x, StringComparer.Ordinal))
            {
                var abs = Path.GetFullPath(Path.Combine(Projects[i].Dir, r));
                if (!seen.Add(abs)) continue;
                var rel = Rel(Root, abs);
                if (Rel(Workspace, abs).StartsWith("../", StringComparison.Ordinal) || !File.Exists(abs))
                {
                    UnreadAll.Add(new Unread { File = Rel(Root, Path.Combine(Projects[i].Dir, Projects[i].File)), Line = 1, What = "ProjectReference " + Path.GetFileName(abs), Reason = "referenced project outside the import root", Category = "project" });
                    continue;
                }
                Projects.Add(ReadProject(abs));
                D.Dotnet.ProjectReferences.Add(rel);
            }
        }
        var opts = new CSharpParseOptions(LanguageVersion.Latest, preprocessorSymbols: new[] { "TRACE", "NET", "NETCOREAPP", "NET10_0", "NET10_0_OR_GREATER", "NET9_0_OR_GREATER", "NET8_0_OR_GREATER", "NET7_0_OR_GREATER", "NET6_0_OR_GREATER", "NET5_0_OR_GREATER", "NETCOREAPP3_1_OR_GREATER" }.Concat(P.Defines));
        var usings = new List<string>();
        foreach (var p in Projects)
        {
            if (p.ImplicitUsings) { usings.AddRange(BaseUsings); if (p.Sdk.EndsWith(".Web", StringComparison.Ordinal)) usings.AddRange(WebUsings); else if (p.Sdk.EndsWith(".Worker", StringComparison.Ordinal)) usings.AddRange(WorkerUsings); }
            foreach (var u in p.Usings) usings.Add((u.Static ? "static " : "") + (u.Alias != null ? u.Alias + " = " : "") + u.Name);
        }
        usings = usings.Distinct(StringComparer.Ordinal).ToList();
        D.Dotnet.GlobalUsings = usings;
        var all = new List<SyntaxTree> { CSharpSyntaxTree.ParseText(string.Concat(usings.Select(u => $"global using {(u.Contains(" = ") || u.StartsWith("static ") ? u : "global::" + u)};\n")), opts, "__global_usings.g.cs") };
        var files = Projects.SelectMany(SourceFiles).Select(f => (Abs: f, Rel: Rel(Root, f))).DistinctBy(x => x.Rel).OrderBy(x => x.Rel, StringComparer.Ordinal).ToList();
        foreach (var (abs, rel) in files)
        {
            var tree = CSharpSyntaxTree.ParseText(File.ReadAllText(abs), opts, rel);
            if (IsMigration(tree, rel)) MigrationTrees.Add(tree); else { Trees.Add(tree); all.Add(tree); }
        }
        var refs = new List<MetadataReference>();
        foreach (var pack in new[] { "Microsoft.NETCore.App.Ref", "Microsoft.AspNetCore.App.Ref" })
            foreach (var dll in Directory.EnumerateFiles(PackVersionDir(pack), "*.dll").OrderBy(f => f, StringComparer.Ordinal)) refs.Add(MetadataReference.CreateFromFile(dll));
        bool top = Trees.Any(t => t.GetRoot().ChildNodes().OfType<GlobalStatementSyntax>().Any());
        Comp = CSharpCompilation.Create(P.AssemblyName, all, refs, new CSharpCompilationOptions(top ? OutputKind.ConsoleApplication : OutputKind.DynamicallyLinkedLibrary,
            nullableContextOptions: P.Nullable.ToLowerInvariant() == "enable" ? NullableContextOptions.Enable : NullableContextOptions.Disable));
        foreach (var t in Trees)
            foreach (var d in t.GetDiagnostics().Where(d => d.Severity == DiagnosticSeverity.Error))
                UnreadAll.Add(new Unread { File = t.FilePath, Line = d.Location.GetLineSpan().StartLinePosition.Line + 1, What = "file", Reason = "syntax error " + d.Id, Category = "file" });
    }

    /// <summary>EF migration files (excluded from the compilation; the model snapshot is read syntactically, Ef.cs).</summary>
    static bool IsMigration(SyntaxTree t, string rel)
    {
        if (!rel.Split('/').Contains("Migrations")) return false;
        return t.GetRoot().DescendantNodes().OfType<ClassDeclarationSyntax>().Any(c => c.BaseList?.Types.Any(b => b.Type.ToString() is "Migration" or "ModelSnapshot" or "Microsoft.EntityFrameworkCore.Migrations.Migration") == true);
    }

    // ---- positions and unread entries -------------------------------------------------------------------------------

    public static int LineOf(SyntaxNode n) => n.SyntaxTree.GetLineSpan(n.Span).StartLinePosition.Line + 1;
    public static int LineOf(SyntaxToken t) => t.SyntaxTree!.GetLineSpan(t.Span).StartLinePosition.Line + 1;
    public static string FileOf(SyntaxNode n) => n.SyntaxTree.FilePath;

    public Unread MakeUnread(SyntaxNode at, string what, string reason, string category, string? route = null, int? line = null) =>
        new() { File = FileOf(at), Line = line ?? LineOf(at), What = what, Reason = reason, Category = category, Route = string.IsNullOrEmpty(route) ? null : route };

    /// <summary>Adds an unread entry once per (file, line, what, route); also to `into` (a route's list).</summary>
    public Unread AddUnread(List<Unread>? into, SyntaxNode at, string what, string reason, string category, string? route = null, int? line = null)
    {
        var u = MakeUnread(at, what, reason, category, route, line);
        if (into != null && !into.Any(x => x.File == u.File && x.Line == u.Line && x.What == u.What && x.Reason == u.Reason)) into.Add(u);
        if (unreadSeen.Add($"{u.File}|{u.Line}|{u.What}|{u.Reason}|{route}")) UnreadAll.Add(u);
        return u;
    }
}
