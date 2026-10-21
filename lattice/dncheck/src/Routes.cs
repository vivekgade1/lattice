// Routes (N3): controllers (attribute routing) and minimal APIs (MapGet/... on the app, on RouteGroupBuilder groups
// followed through locals, return values and project extension methods, one call site at a time).
// The registration code is interpreted from the startup entry: a group is an object whose prefix and metadata
// are known when its endpoints are mapped.
using System.Text.RegularExpressions;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed class Meta
{
    public string Kind = ""; // authorize | anonymous | filter
    public List<string> Roles = new();
    public List<string> Policies = new();
    public string? Custom;
    public string Where = "";
    public int Line;
    public string File = "";
    public SyntaxNode? Node;
}

public sealed class Group
{
    public Group? Parent;
    public string Prefix = "";
    public bool Ok = true;
    public List<Meta> Meta = new();
    public string Desc = "";
    public IEnumerable<Group> Chain() { for (var g = this; g != null; g = g.Parent) yield return g; }
}

public sealed class RouteRec
{
    public XRoute R = new();
    public Group? Group;
    public List<Meta> Meta = new();
    public INamedTypeSymbol? Controller;
    public IMethodSymbol? Action;
    public SyntaxNode? Handler, Body;
    public IMethodSymbol? HandlerSym;
    public List<IParameterSymbol> Params = new();
    public SyntaxNode At = null!;
    public Reach? Reach;
    public string Id => R.Method + " " + R.Path;
    public string Key => R.Method + " " + Ex.Normalize(R.Path);
    public string Verb = "";
    public bool RoleGated, BindEnforced, Framework, ExplicitAnon;
}

sealed class CtrlVal { public List<RouteRec> Recs = new(); }
sealed class EpVal { public List<RouteRec> Recs = new(); }

public sealed partial class Ex
{
    public readonly List<RouteRec> Recs = new();
    readonly Group rootGroup = new() { Desc = "app" };
    readonly HashSet<SyntaxNode> visitedMaps = new();
    readonly List<string> conds = new();
    int loopDepth, walkDepth;
    readonly HashSet<ISymbol> walking = new(SymbolEqualityComparer.Default);
    bool controllersMapped, tokenTransformer, usePathBaseUnread;
    string? pathBase;
    SyntaxNode? controllersAt;
    readonly List<Excluded> grpcOrOther = new();

    static readonly Dictionary<string, string> Verbs = new(StringComparer.Ordinal) { ["MapGet"] = "GET", ["MapPost"] = "POST", ["MapPut"] = "PUT", ["MapDelete"] = "DELETE", ["MapPatch"] = "PATCH", ["MapHead"] = "HEAD", ["MapOptions"] = "OPTIONS" };
    static readonly HashSet<string> ConventionNames = new(StringComparer.Ordinal) { "RequireAuthorization", "AllowAnonymous", "AddEndpointFilter", "AddEndpointFilterFactory", "WithName", "WithTags", "WithDisplayName", "WithSummary", "WithDescription", "WithMetadata", "WithOpenApi", "WithGroupName", "Produces", "ProducesProblem", "ProducesValidationProblem", "Accepts", "RequireCors", "RequireRateLimiting", "DisableAntiforgery", "RequireHost", "ExcludeFromDescription", "DisableRateLimiting", "CacheOutput", "WithRequestTimeout", "WithOrder", "ShortCircuit", "AddOpenApiOperationTransformer", "WithFormOptions", "WithStaticAssets" };
    static readonly Dictionary<string, (string Kind, string Reason)> FrameworkMaps = new(StringComparer.Ordinal)
    {
        ["MapHealthChecks"] = ("framework", "framework endpoint"),
        ["MapOpenApi"] = ("framework", "framework endpoint"),
        ["MapStaticAssets"] = ("framework", "framework endpoint"),
        ["MapFallback"] = ("framework", "framework endpoint"),
        ["MapFallbackToFile"] = ("framework", "framework endpoint"),
        ["MapFallbackToPage"] = ("framework", "framework endpoint"),
        ["MapFallbackToController"] = ("framework", "framework endpoint"),
        ["MapHub"] = ("framework", "framework endpoint (SignalR hub)"),
        ["MapRazorPages"] = ("framework", "framework endpoint (Razor Pages)"),
        ["MapBlazorHub"] = ("framework", "framework endpoint (Blazor)"),
        ["MapRazorComponents"] = ("framework", "framework endpoint (Blazor)"),
        ["MapGrpcService"] = ("grpc", "gRPC for .NET is out of scope in Spike J round 1"),
        ["MapGrpcReflectionService"] = ("grpc", "gRPC for .NET is out of scope in Spike J round 1"),
        ["MapIdentityApi"] = ("framework", "ASP.NET Identity endpoints are not read in round 1"),
    };

    bool IsEndpointBuilder(ITypeSymbol? t) => t != null && (t.Name is "WebApplication" or "IEndpointRouteBuilder" or "RouteGroupBuilder" or "IApplicationBuilder" || t.AllInterfaces.Any(i => i.Name == "IEndpointRouteBuilder"));
    static bool IsGroupType(ITypeSymbol? t) => t is { Name: "RouteGroupBuilder" };

    string? Condition => conds.Count == 0 ? null : string.Join(" && ", conds);

    // ---- entry points ------------------------------------------------------------------------------------------------

    void DiscoverRoutes()
    {
        var globals = Trees.SelectMany(t => t.GetRoot().ChildNodes().OfType<GlobalStatementSyntax>()).Select(g => g.Statement).ToList();
        if (globals.Count > 0) WalkStatements(globals, new Dictionary<ISymbol, object?>(SymbolEqualityComparer.Default), new Ret());
        else
        {
            var main = MethodsByName.GetValueOrDefault("Main")?.FirstOrDefault(m => m.IsStatic && IsProject(m));
            if (main != null && BodyOf(DeclOf(main)) is { } mb) WalkBody(mb, new Dictionary<ISymbol, object?>(SymbolEqualityComparer.Default), new Ret());
        }
        if (!controllersMapped)
            foreach (var t in ProjTypes.Where(IsController).OrderBy(t => QualType(t), StringComparer.Ordinal))
            {
                int n0 = Recs.Count, o0 = recOrder.Count;
                ControllerRoutes(t, rootGroup, null, null);
                foreach (var rec in Recs.Skip(n0).ToList())
                    D.Excluded.Add(new Excluded { Kind = "controllers", What = $"{t.Name}.{rec.Action!.Name}", Method = rec.R.Method, Path = rec.R.Path, File = rec.R.File, Line = rec.R.Line, Reason = "controllers not mapped (no MapControllers() in the startup code)" });
                Recs.RemoveRange(n0, Recs.Count - n0);
                recOrder.RemoveRange(o0, recOrder.Count - o0);
            }
        // anything mapped that the walk never reached is unread, never dropped
        foreach (var tree in Trees)
            foreach (var inv in tree.GetRoot().DescendantNodes().OfType<InvocationExpressionSyntax>())
            {
                var n = InvokedName(inv.Expression);
                if (n == null || visitedMaps.Contains(inv) || !(Verbs.ContainsKey(n) || n is "MapMethods" or "MapControllers")) continue;
                if (inv.Expression is MemberAccessExpressionSyntax ma && IsEndpointBuilder(Sm(inv).GetTypeInfo(ma.Expression).Type))
                    AddUnread(null, inv, n, "registration not reached from the startup code", "route");
            }
    }

    sealed class Ret { public object? Value; public bool Done; }

    void WalkBody(SyntaxNode body, Dictionary<ISymbol, object?> env, Ret ret)
    {
        if (body is BlockSyntax b) WalkStatements(b.Statements, env, ret);
        else if (body is ExpressionSyntax e) ret.Value = Eval(e, env, ret);
    }

    void WalkStatements(IEnumerable<StatementSyntax> stmts, Dictionary<ISymbol, object?> env, Ret ret, bool top = true)
    {
        foreach (var st in stmts)
        {
            if (ret.Done) return;
            switch (st)
            {
                case LocalDeclarationStatementSyntax ld:
                    foreach (var v in ld.Declaration.Variables)
                        if (v.Initializer != null)
                        {
                            var val = Eval(v.Initializer.Value, env, ret);
                            if (Sm(v).GetDeclaredSymbol(v) is { } ls) env[ls] = val;
                        }
                    break;
                case ExpressionStatementSyntax es: Eval(es.Expression, env, ret); break;
                case ReturnStatementSyntax rs: if (rs.Expression != null && ret.Value == null) ret.Value = Eval(rs.Expression, env, ret); if (top) ret.Done = true; break;
                case BlockSyntax bl: WalkStatements(bl.Statements, env, ret, top); break;
                case IfStatementSyntax ifs:
                    {
                        var c = Js.Text(ifs.Condition);
                        conds.Add(c); WalkStatements([ifs.Statement], env, ret, false); conds.RemoveAt(conds.Count - 1);
                        if (ifs.Else != null) { conds.Add("!(" + c + ")"); WalkStatements([ifs.Else.Statement], env, ret, false); conds.RemoveAt(conds.Count - 1); }
                        break;
                    }
                case ForEachStatementSyntax or ForStatementSyntax or WhileStatementSyntax or DoStatementSyntax:
                    loopDepth++;
                    WalkStatements(st.ChildNodes().OfType<StatementSyntax>(), env, ret, false);
                    loopDepth--;
                    break;
                case UsingStatementSyntax us: WalkStatements([us.Statement], env, ret, false); break;
                case LockStatementSyntax ls2: WalkStatements([ls2.Statement], env, ret, false); break;
                case TryStatementSyntax ts: WalkStatements(ts.Block.Statements, env, ret, false); break;
                case SwitchStatementSyntax sw: foreach (var sec in sw.Sections) WalkStatements(sec.Statements, env, ret, false); break;
                case LocalFunctionStatementSyntax: break; // walked when called
            }
        }
    }

    // ---- expressions ---------------------------------------------------------------------------------------------------

    object? Eval(ExpressionSyntax e, Dictionary<ISymbol, object?> env, Ret ret)
    {
        switch (e)
        {
            case ParenthesizedExpressionSyntax p: return Eval(p.Expression, env, ret);
            case AwaitExpressionSyntax a: return Eval(a.Expression, env, ret);
            case PostfixUnaryExpressionSyntax pu: return Eval(pu.Operand, env, ret);
            case CastExpressionSyntax c: return Eval(c.Expression, env, ret);
            case AssignmentExpressionSyntax asg:
                {
                    var v = Eval(asg.Right, env, ret);
                    if (Sm(asg).GetSymbolInfo(asg.Left).Symbol is { } s) env[s] = v;
                    return v;
                }
            case IdentifierNameSyntax id:
                {
                    var s = Sm(id).GetSymbolInfo(id).Symbol;
                    if (s != null && env.TryGetValue(s, out var v)) return v;
                    var t = Sm(id).GetTypeInfo(id).Type;
                    return IsEndpointBuilder(t) && !IsGroupType(t) && t!.Name != "IApplicationBuilder" ? rootGroup : null;
                }
            case MemberAccessExpressionSyntax ma:
                {
                    var t = Sm(ma).GetTypeInfo(ma).Type;
                    return IsEndpointBuilder(t) && !IsGroupType(t) && t!.Name != "IApplicationBuilder" && !(Sm(ma).GetSymbolInfo(ma.Name).Symbol is IMethodSymbol) ? rootGroup : null;
                }
            case InvocationExpressionSyntax inv: return EvalCall(inv, env, ret);
        }
        return null;
    }

    Group? AsGroup(object? v, ExpressionSyntax? recv)
    {
        if (v is Group g) return g;
        if (recv != null && IsEndpointBuilder(Sm(recv).GetTypeInfo(recv).Type) && !IsGroupType(Sm(recv).GetTypeInfo(recv).Type)) return rootGroup;
        return null;
    }

    object? EvalCall(InvocationExpressionSyntax inv, Dictionary<ISymbol, object?> env, Ret ret)
    {
        var name = InvokedName(inv.Expression) ?? "";
        var ma = inv.Expression as MemberAccessExpressionSyntax;
        object? recv = ma != null ? Eval(ma.Expression, env, ret) : null;
        var recvGroup = AsGroup(recv, ma?.Expression);
        var args = inv.ArgumentList.Arguments;
        if (Verbs.TryGetValue(name, out var verb) && recvGroup != null) return new EpVal { Recs = MapEndpoint(inv, recvGroup, new[] { verb }, args.Count > 0 ? args[0].Expression : null, args.Count > 1 ? args[^1].Expression : null) };
        if (name == "MapMethods" && recvGroup != null && args.Count >= 3)
        {
            var verbs = new List<string>();
            foreach (var el in args[1].Expression.DescendantNodesAndSelf().OfType<LiteralExpressionSyntax>().Where(l => l.IsKind(SyntaxKind.StringLiteralExpression))) verbs.Add(el.Token.ValueText.ToUpperInvariant());
            return new EpVal { Recs = MapEndpoint(inv, recvGroup, verbs.Count > 0 ? verbs : new[] { "ANY" }, args[0].Expression, args[^1].Expression) };
        }
        if (name == "Map" && recvGroup != null && args.Count >= 2) return new EpVal { Recs = MapEndpoint(inv, recvGroup, new[] { "ANY" }, args[0].Expression, args[^1].Expression) };
        if (name == "MapGroup" && recvGroup != null)
        {
            visitedMaps.Add(inv);
            var g = new Group { Parent = recvGroup, Desc = args.Count > 0 ? Js.Text(args[0].Expression) : "" };
            if (args.Count > 0 && Sm(inv).GetConstantValue(args[0].Expression) is { HasValue: true, Value: string pfx }) g.Prefix = pfx;
            else { g.Ok = false; AddUnread(null, inv, "MapGroup with a non-constant prefix (" + g.Desc + ")", "prefix is not a compile-time constant", "route"); }
            return g;
        }
        if (name is "MapControllers" or "MapDefaultControllerRoute" && recvGroup != null) { visitedMaps.Add(inv); return MapControllers(inv, recvGroup); }
        if (name == "MapControllerRoute" && recvGroup != null)
        {
            visitedMaps.Add(inv);
            conventionalAt.Add(inv);
            return new CtrlVal();
        }
        if (name == "MapIdentityApi" && recvGroup != null) { visitedMaps.Add(inv); return new EpVal { Recs = MapIdentity(inv, recvGroup) }; }
        if (FrameworkMaps.TryGetValue(name, out var fm) && (recvGroup != null || IsEndpointBuilder(ma != null ? Sm(inv).GetTypeInfo(ma.Expression).Type : null)))
        {
            visitedMaps.Add(inv);
            var path = args.Count > 0 && Sm(inv).GetConstantValue(args[0].Expression) is { HasValue: true, Value: string ps } ? JoinPath(recvGroup, ps) : null;
            var what = inv.Expression is MemberAccessExpressionSyntax { Name: GenericNameSyntax gn } ? name + "<" + gn.TypeArgumentList.Arguments + ">" : name;
            D.Excluded.Add(new Excluded { Kind = fm.Kind, What = what, Path = path, File = FileOf(inv), Line = LineOf(inv.Expression is MemberAccessExpressionSyntax mm ? mm.Name.Identifier : inv.GetFirstToken()), Reason = fm.Reason });
            return new EpVal();
        }
        if (name == "UseEndpoints")
            foreach (var a in args)
                if (a.Expression is LambdaExpressionSyntax lam)
                {
                    var env2 = new Dictionary<ISymbol, object?>(env, SymbolEqualityComparer.Default);
                    var ps = lam is ParenthesizedLambdaExpressionSyntax pl ? pl.ParameterList.Parameters.FirstOrDefault() : (lam as SimpleLambdaExpressionSyntax)?.Parameter;
                    if (ps != null && Sm(lam).GetDeclaredSymbol(ps) is { } psym) env2[psym] = rootGroup;
                    WalkBody(lam is SimpleLambdaExpressionSyntax sl ? sl.Body : ((ParenthesizedLambdaExpressionSyntax)lam).Body, env2, new Ret());
                }
        if (name == "UseStartup" && inv.Expression is MemberAccessExpressionSyntax { Name: GenericNameSyntax sg } && Sm(inv).GetTypeInfo(sg.TypeArgumentList.Arguments[0]).Type is INamedTypeSymbol st)
            foreach (var cfg in st.GetMembers("Configure").OfType<IMethodSymbol>())
                if (BodyOf(DeclOf(cfg)) is { } cb)
                {
                    var env2 = new Dictionary<ISymbol, object?>(SymbolEqualityComparer.Default);
                    foreach (var p in cfg.Parameters) if (IsEndpointBuilder(p.Type)) env2[p] = rootGroup;
                    WalkBody(cb, env2, new Ret());
                }
        if (name == "UsePathBase" && args.Count == 1)
        {
            if (Sm(inv).GetConstantValue(args[0].Expression) is { HasValue: true, Value: string pb }) { pathBase = pb.TrimEnd('/'); D.Notes.Add($"{FileOf(inv)}:{LineOf(inv)} UsePathBase(\"{pb}\") is prepended to every route"); }
            else usePathBaseUnread = true;
            return recv;
        }
        if (ConventionNames.Contains(name) && recv != null) { ApplyConvention(recv, name, inv); return recv; }
        // a project method that is handed a group (or an endpoint builder): follow it with the bound values
        var call = Resolve(inv);
        if (call.Project && call.M != null && call.Ambiguous == null)
        {
            var bound = MapArgs(inv, call).Select(x => (x.P, V: Eval(x.E, env, ret), x.E)).ToList();
            if (bound.Any(b => b.V is Group || IsEndpointBuilder(b.P.Type)) || recv is Group)
            {
                var decl = DeclOf(call.M);
                var body = BodyOf(decl);
                if (body == null) return null;
                if (walkDepth > 12 || !walking.Add(call.M)) { AddUnread(null, inv, name, "recursive registration method", "route"); return null; }
                walkDepth++;
                var env2 = new Dictionary<ISymbol, object?>(SymbolEqualityComparer.Default);
                foreach (var b in bound) env2[b.P] = b.V ?? (IsEndpointBuilder(b.P.Type) && !IsGroupType(b.P.Type) ? rootGroup : null);
                var r2 = new Ret();
                var saveLoop = loopDepth;
                WalkBody(body, env2, r2);
                loopDepth = saveLoop;
                walkDepth--; walking.Remove(call.M);
                return r2.Value;
            }
        }
        else if (call.Ambiguous != null && ma != null && recv is Group) AddUnread(null, inv, name, "ambiguous registration method: " + string.Join(", ", call.Ambiguous), "route");
        return recv is Group or EpVal or CtrlVal && ConventionNames.Contains(name) ? recv : null;
    }

    readonly List<InvocationExpressionSyntax> conventionalAt = new();
    string? identityPrefix, identityUser;

    static readonly (string Verb, string Path, bool Member, string[] Body, string[] Query)[] IdentityTable =
    [
        ("POST", "/register", false, ["Email", "Password"], []),
        ("POST", "/login", false, ["Email", "Password", "TwoFactorCode", "TwoFactorRecoveryCode"], ["useCookies", "useSessionCookies"]),
        ("POST", "/refresh", false, ["RefreshToken"], []),
        ("GET", "/confirmEmail", false, [], ["userId", "code", "changedEmail"]),
        ("POST", "/resendConfirmationEmail", false, ["Email"], []),
        ("POST", "/forgotPassword", false, ["Email"], []),
        ("POST", "/resetPassword", false, ["Email", "ResetCode", "NewPassword"], []),
        ("POST", "/manage/2fa", true, ["Enable", "TwoFactorCode", "ResetSharedKey", "ResetRecoveryCodes", "ForgetMachine"], []),
        ("GET", "/manage/info", true, [], []),
        ("POST", "/manage/info", true, ["NewEmail", "NewPassword", "OldPassword"], []),
    ];

    /// <summary>MapIdentityApi&lt;TUser&gt;() under a constant prefix: the endpoints of its .NET 10 table (N4, Q13); a customised Identity is not read.</summary>
    List<RouteRec> MapIdentity(InvocationExpressionSyntax inv, Group g)
    {
        var recs = new List<RouteRec>();
        var user = inv.Expression is MemberAccessExpressionSyntax { Name: GenericNameSyntax gn } ? Js.Text(gn.TypeArgumentList.Arguments[0]) : "IdentityUser";
        if (g.Chain().Any(x => !x.Ok) || loopDepth > 0) { AddUnread(null, inv, "MapIdentityApi<" + user + ">", "prefix is not a compile-time constant", "route"); return recs; }
        identityPrefix = JoinPath(g, "").TrimEnd('/'); identityUser = user;
        D.Notes.Add($"{FileOf(inv)}:{LineOf(inv)} MapIdentityApi<{user}>: endpoints read from the fixed .NET 10 table; the Identity tables (AspNetUsers, AspNetRoles, AspNetUserRoles, AspNetUserClaims, AspNetRoleClaims, AspNetUserLogins, AspNetUserTokens) are excluded");
        D.Excluded.Add(new Excluded { Kind = "framework", What = "ASP.NET Identity tables", File = FileOf(inv), Line = LineOf(inv), Reason = "Identity's own tables are framework tables" });
        var tok = ((MemberAccessExpressionSyntax)inv.Expression).Name.Identifier;
        foreach (var (verb, path, member, body, query) in IdentityTable)
        {
            var rec = new RouteRec { Group = g, At = inv, Verb = verb, Framework = true };
            var r = rec.R;
            r.Method = verb; r.Style = "minimal"; r.File = FileOf(inv); r.Line = LineOf(tok); r.Condition = Condition;
            r.Path = r.Template = JoinPath(g, path);
            r.RouterChain = g.Chain().Reverse().Where(x => x.Parent != null).Select(x => x.Prefix).ToList();
            r.Handler = new XHandler { Func = "Microsoft.AspNetCore.Routing.IdentityApiEndpointRouteBuilderExtensions.MapIdentityApi", Module = "Microsoft.AspNetCore.Routing", File = r.File, Line = r.Line };
            r.Auth = member ? "Member" : "Public"; r.Member = member; r.AuthReason = member ? "MapIdentityApi: the /manage group requires authorization" : "MapIdentityApi: public account endpoint";
            r.StatusCodes = new() { 200 }; r.StatusCode = 200;
            foreach (var b in body) r.Params.Add(new XParam { Name = b, Annotation = "string", Type = b is "Enable" or "ResetSharedKey" or "ResetRecoveryCodes" or "ForgetMachine" ? "Bool" : "Str", Source = "body", BindSource = "body", Required = b is "Email" or "Password" or "RefreshToken" or "ResetCode" || (b == "NewPassword" && path == "/resetPassword"), File = r.File, Line = r.Line });
            foreach (var q in query) r.Params.Add(new XParam { Name = q, Annotation = "string", Type = q.StartsWith("use") ? "Bool" : "Str", Source = "query", BindSource = "query", File = r.File, Line = r.Line });
            recs.Add(rec); Recs.Add(rec); recOrder.Add(rec);
        }
        return recs;
    }

    // ---- metadata ----------------------------------------------------------------------------------------------------------

    void ApplyConvention(object recv, string name, InvocationExpressionSyntax inv)
    {
        Meta? m = null;
        var args = inv.ArgumentList.Arguments;
        var sm = Sm(inv);
        if (name == "AllowAnonymous") m = new Meta { Kind = "anonymous", Where = "." + name + "()" };
        else if (name == "RequireAuthorization")
        {
            m = new Meta { Kind = "authorize", Where = "." + name + "(" + string.Join(", ", args.Select(a => Js.Text(a.Expression))) + ")" };
            foreach (var a in args)
            {
                if (sm.GetConstantValue(a.Expression) is { HasValue: true, Value: string pol }) m.Policies.Add(pol);
                else if (a.Expression is LambdaExpressionSyntax lam) ReadPolicyBuilder(lam is SimpleLambdaExpressionSyntax sl ? sl.Body : ((ParenthesizedLambdaExpressionSyntax)lam).Body, m);
                else m.Custom = "authorization policy argument " + Js.Text(a.Expression);
            }
        }
        else if (name is "AddEndpointFilter" or "AddEndpointFilterFactory") m = new Meta { Kind = "filter", Where = Js.Text(inv), Node = inv };
        if (m == null) return;
        m.File = FileOf(inv); m.Line = LineOf(((MemberAccessExpressionSyntax)inv.Expression).Name.Identifier);
        switch (recv)
        {
            case Group g: g.Meta.Add(m); break;
            case EpVal ep: foreach (var r in ep.Recs) r.Meta.Add(m); break;
            case CtrlVal cv: foreach (var r in cv.Recs) r.Meta.Add(m); controllerMeta.Add(m); break;
        }
    }

    readonly List<Meta> controllerMeta = new();

    void ReadPolicyBuilder(SyntaxNode body, Meta m)
    {
        foreach (var c in body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>())
        {
            var n = InvokedName(c.Expression);
            if (n == "RequireRole") { foreach (var a in c.ArgumentList.Arguments) if (Sm(c).GetConstantValue(a.Expression) is { HasValue: true, Value: string r }) m.Roles.AddRange(r.Split(',', StringSplitOptions.TrimEntries)); else m.Custom = "role from " + Js.Text(a.Expression); }
            else if (n is "RequireAuthenticatedUser") { }
            else if (n is "RequireClaim" or "RequireUserName") m.Custom ??= Js.Text(c);
            else if (n is "AddRequirements" or "RequireAssertion" or "AddAuthenticationSchemes") m.Custom ??= Js.Text(c);
        }
    }

    // ---- minimal APIs ----------------------------------------------------------------------------------------------------------

    string JoinPath(Group? g, string leaf)
    {
        var parts = new List<string>();
        if (pathBase != null) parts.Add(pathBase);
        if (g != null) foreach (var x in g.Chain().Reverse()) parts.Add(x.Prefix);
        parts.Add(leaf);
        return NormPath(string.Join("/", parts));
    }

    static string NormPath(string p)
    {
        p = Regex.Replace(p, "/{2,}", "/");
        if (!p.StartsWith('/')) p = "/" + p;
        return p.Length > 1 ? p.TrimEnd('/') : p;
    }

    List<RouteRec> MapEndpoint(InvocationExpressionSyntax inv, Group g, IEnumerable<string> verbs, ExpressionSyntax? pattern, ExpressionSyntax? handler)
    {
        visitedMaps.Add(inv);
        var recs = new List<RouteRec>();
        var nameTok = inv.Expression is MemberAccessExpressionSyntax mm ? mm.Name.Identifier : inv.GetFirstToken();
        var name = InvokedName(inv.Expression)!;
        if (loopDepth > 0) { AddUnread(null, inv, name + " inside a loop", "registration inside a loop", "route"); return recs; }
        string? leaf = pattern != null && Sm(inv).GetConstantValue(pattern) is { HasValue: true, Value: string s } ? s : null;
        if (leaf == null) { AddUnread(null, inv, $"{name} with a non-constant template ({(pattern != null ? Js.Text(pattern) : "none")})", "template is not a compile-time constant", "route"); return recs; }
        if (g.Chain().Any(x => !x.Ok)) { AddUnread(null, inv, name + " under a group with a non-constant prefix", "group prefix is not a compile-time constant", "route"); return recs; }
        if (usePathBaseUnread) { AddUnread(null, inv, name, "UsePathBase is not a compile-time constant", "route"); return recs; }
        if (Regex.IsMatch(leaf, @"\{\*\*?\w+")) AddUnread(null, inv, name + " " + leaf, "catch-all templates are not read", "route");
        var full = JoinPath(g, leaf);
        foreach (var verb in verbs)
        {
            var rec = new RouteRec { Group = g, At = inv, Verb = verb };
            rec.R.Method = verb == "ANY" ? "ANY" : verb;
            rec.R.Style = "minimal";
            rec.R.File = FileOf(inv); rec.R.Line = LineOf(nameTok);
            rec.R.Condition = Condition;
            rec.R.Path = rec.R.Template = full;
            rec.R.RouterChain = g.Chain().Reverse().Where(x => x.Parent != null).Select(x => x.Prefix).ToList();
            AttachHandler(rec, handler);
            if (verb == "ANY") AddUnread(rec.R.Unread, inv, name + " " + leaf, "Map without an HTTP verb accepts every method", "route");
            recs.Add(rec); Recs.Add(rec);
            recOrder.Add(rec);
        }
        return recs;
    }

    readonly List<RouteRec> recOrder = new();

    void AttachHandler(RouteRec rec, ExpressionSyntax? handler)
    {
        if (handler == null) return;
        while (handler is ParenthesizedExpressionSyntax pe) handler = pe.Expression;
        var sm = Sm(handler);
        switch (handler)
        {
            case LambdaExpressionSyntax or AnonymousMethodExpressionSyntax:
                {
                    var sym = sm.GetSymbolInfo(handler).Symbol as IMethodSymbol;
                    rec.Handler = handler;
                    rec.Body = BodyOf(handler);
                    rec.HandlerSym = sym;
                    rec.Params = sym?.Parameters.ToList() ?? new();
                    var encl = sm.GetEnclosingSymbol(handler.SpanStart);
                    var type = encl?.ContainingType ?? (encl as INamedTypeSymbol);
                    var tname = type != null ? QualType(type) : "Program";
                    rec.R.Handler = new XHandler { Func = $"{tname}.<lambda@{FileOf(handler)}:{LineOf(handler)}>", Module = type?.ContainingNamespace?.IsGlobalNamespace == false ? type.ContainingNamespace.ToDisplayString() : "", File = FileOf(handler), Line = LineOf(handler) };
                    break;
                }
            case IdentifierNameSyntax or MemberAccessExpressionSyntax:
                {
                    var info = sm.GetSymbolInfo(handler);
                    var m = (info.Symbol ?? info.CandidateSymbols.FirstOrDefault()) as IMethodSymbol;
                    if (m != null && IsProject(m) && DeclOf(m) is { } d)
                    {
                        rec.Handler = d; rec.Body = BodyOf(d); rec.HandlerSym = m; rec.Params = m.Parameters.ToList();
                        rec.R.Handler = new XHandler { Func = Qual(m), Module = m.ContainingNamespace.IsGlobalNamespace ? "" : m.ContainingNamespace.ToDisplayString(), File = FileOf(d), Line = d is MethodDeclarationSyntax md ? LineOf(md.Identifier) : LineOf(d) };
                        break;
                    }
                    AddUnread(rec.R.Unread, handler, Js.Text(handler), "handler is not a project method", "route", null);
                    break;
                }
            default: AddUnread(rec.R.Unread, handler, Js.Text(handler), "handler expression is not a lambda or a method group", "route"); break;
        }
    }

    // ---- controllers --------------------------------------------------------------------------------------------------------

    CtrlVal MapControllers(InvocationExpressionSyntax inv, Group g)
    {
        var cv = new CtrlVal();
        if (controllersMapped) return cv;
        controllersMapped = true; controllersAt = inv;
        var cond = Condition;
        var types = ProjTypes.Where(IsController).OrderBy(t => QualType(t), StringComparer.Ordinal).ToList();
        foreach (var t in types)
        {
            var recs = ControllerRoutes(t, g, inv, cond);
            cv.Recs.AddRange(recs);
        }
        return cv;
    }

    bool IsController(INamedTypeSymbol t)
    {
        if (t.TypeKind != TypeKind.Class || t.IsAbstract || t.DeclaredAccessibility != Accessibility.Public || t.IsGenericType) return false;
        if (AttrsOf(t).Any(a => AttrName(a) == "NonController")) return false;
        for (var b = t; b != null; b = b.BaseType)
        {
            if (b.Name is "ControllerBase" or "Controller" && !IsProject(b)) return true;
            if (AttrsOf(b).Any(a => AttrName(a) is "ApiController" or "Controller")) return true;
        }
        return false;
    }

    static readonly Dictionary<string, string> VerbAttrs = new(StringComparer.Ordinal) { ["HttpGet"] = "GET", ["HttpPost"] = "POST", ["HttpPut"] = "PUT", ["HttpDelete"] = "DELETE", ["HttpPatch"] = "PATCH", ["HttpHead"] = "HEAD", ["HttpOptions"] = "OPTIONS" };

    IEnumerable<AttributeSyntax> ChainAttrs(INamedTypeSymbol t, string name)
    {
        for (var b = t; b != null; b = b.BaseType)
        {
            if (!IsProject(b)) yield break;
            foreach (var a in AttrsOf(b).Where(a => AttrName(a) == name)) yield return a;
        }
    }

    List<RouteRec> ControllerRoutes(INamedTypeSymbol t, Group g, SyntaxNode? mapAt, string? cond)
    {
        var res = new List<RouteRec>();
        // [Route] on the nearest class in the chain that has any
        List<AttributeSyntax> classRoutes = new();
        for (var b = t; b != null && IsProject(b); b = b.BaseType)
        {
            classRoutes = AttrsOf(b).Where(a => AttrName(a) == "Route").ToList();
            if (classRoutes.Count > 0) break;
        }
        var area = ChainAttrs(t, "Area").Select(a => ArgValue(a, 0) as string).FirstOrDefault();
        var ctrlName = t.Name.EndsWith("Controller", StringComparison.Ordinal) ? t.Name[..^10] : t.Name;
        var methods = new List<IMethodSymbol>();
        for (var b = t; b != null && IsProject(b); b = b.BaseType)
            methods.AddRange(b.GetMembers().OfType<IMethodSymbol>().Where(m => m.MethodKind == MethodKind.Ordinary && !m.IsStatic && m.DeclaredAccessibility == Accessibility.Public && !m.IsOverride && !m.IsAbstract && m.ContainingType.TypeKind == TypeKind.Class && !AttrsOf(m).Any(a => AttrName(a) == "NonAction")));
        methods = methods.OrderBy(m => m.Locations.First().SourceTree!.FilePath, StringComparer.Ordinal).ThenBy(m => m.Locations.First().SourceSpan.Start).ToList();
        bool anyAttrRoute = classRoutes.Count > 0 || methods.Any(m => AttrsOf(m).Any(a => AttrName(a) is "Route" or "AcceptVerbs" || VerbAttrs.ContainsKey(AttrName(a))));
        if (!anyAttrRoute) return res; // conventional routing only: listed once at MapControllerRoute
        foreach (var m in methods)
        {
            var attrs = AttrsOf(m).ToList();
            var verbAttrs = attrs.Where(a => VerbAttrs.ContainsKey(AttrName(a))).ToList();
            var routeAttrs = attrs.Where(a => AttrName(a) == "Route").ToList();
            var acceptVerbs = attrs.FirstOrDefault(a => AttrName(a) == "AcceptVerbs");
            var templs = new List<(string Verb, string? Template, AttributeSyntax At)>();
            foreach (var va in verbAttrs) templs.Add((VerbAttrs[AttrName(va)], ArgValue(va, 0) as string, va));
            if (acceptVerbs != null) foreach (var v in acceptVerbs.ArgumentList!.Arguments.Where(x => x.NameEquals == null).Select(x => Sm(acceptVerbs).GetConstantValue(x.Expression).Value as string).Where(x => x != null)) templs.Add((v!.ToUpperInvariant(), routeAttrs.Count > 0 ? ArgValue(routeAttrs[0], 0) as string : null, acceptVerbs));
            if (templs.Count == 0)
            {
                if (routeAttrs.Count > 0 || classRoutes.Count > 0) AddUnread(null, DeclOf(m)!, t.Name + "." + m.Name, "action without an HTTP verb attribute accepts every method", "route");
                continue;
            }
            // an action that renders a view is not an API route
            if (BodyOf(DeclOf(m)) is { } vb && vb.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>().Any(i => InvokedName(i.Expression) is "View" or "PartialView" && Resolve(i).M is { } vm && !IsProject(vm)))
            {
                D.Excluded.Add(new Excluded { Kind = "views", What = t.Name + "." + m.Name, File = FileOf(DeclOf(m)!), Line = LineOf(((MethodDeclarationSyntax)DeclOf(m)!).Identifier), Reason = "action returns a view" });
                continue;
            }
            foreach (var (verb, templ, at) in templs)
            {
                var prefixes = classRoutes.Count > 0 ? classRoutes.Select(a => ArgValue(a, 0) as string ?? "").ToList() : new List<string> { "" };
                foreach (var cr in prefixes)
                {
                    string full;
                    if (templ != null && (templ.StartsWith("/") || templ.StartsWith("~/"))) full = templ.TrimStart('~');
                    else full = (cr == "" ? "" : "/" + cr) + (string.IsNullOrEmpty(templ) ? "" : "/" + templ);
                    full = full.Replace("[controller]", ctrlName).Replace("[action]", m.Name).Replace("[area]", area ?? "");
                    var raw = JoinPath(g, full);
                    var rec = new RouteRec { Group = g, At = at, Verb = verb, Controller = t, Action = m, HandlerSym = m };
                    rec.R.Method = verb; rec.R.Style = "controller";
                    rec.R.File = FileOf(at); rec.R.Line = LineOf(at);
                    rec.R.Condition = cond;
                    rec.R.Path = rec.R.Template = raw;
                    rec.R.RouterChain = new() { t.Name };
                    var decl = DeclOf(m)!;
                    rec.Handler = decl; rec.Body = BodyOf(decl); rec.Params = m.Parameters.ToList();
                    rec.R.Handler = new XHandler { Func = Qual(m), Module = t.ContainingNamespace.IsGlobalNamespace ? "" : t.ContainingNamespace.ToDisplayString(), File = FileOf(decl), Line = LineOf(((MethodDeclarationSyntax)decl).Identifier) };
                    if (tokenTransformer && (full.Contains("[") || raw.Contains(ctrlName))) { AddUnread(rec.R.Unread, at, raw, "route token transformer: the transformed spelling is not guessed", "route"); continue; }
                    if (Regex.IsMatch(raw, @"\{\*\*?\w+")) AddUnread(rec.R.Unread, at, raw, "catch-all templates are not read", "route");
                    res.Add(rec); Recs.Add(rec); recOrder.Add(rec);
                }
            }
        }
        return res;
    }

    void ReportConventional()
    {
        foreach (var inv in conventionalAt)
        {
            var legacy = ProjTypes.Where(IsController).Where(t => !t.GetMembers().OfType<IMethodSymbol>().Any(m => AttrsOf(m).Any(a => AttrName(a) is "Route" or "AcceptVerbs" || VerbAttrs.ContainsKey(AttrName(a)))) && ChainAttrs(t, "Route").Count() == 0).Select(t => t.Name).OrderBy(x => x, StringComparer.Ordinal).ToList();
            AddUnread(null, inv, "conventional route (MapControllerRoute)" + (legacy.Count > 0 ? " for the attribute-less " + string.Join(", ", legacy) : ""), "conventional routing is not read", "route");
        }
    }
}
