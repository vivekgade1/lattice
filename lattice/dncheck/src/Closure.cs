// The shared reading machinery: the project's symbol index, call resolution (Roslyn's binding, then N-bind: a failed
// overload resolution with exactly one project candidate is bound to it, then N-bind2: a lexical match by (declaring
// type if known, name, arity), several candidates unread), DI resolution (N10), the request reach (depth 3) with the
// caller-derived arguments each helper receives, and the entity types of LINQ chains.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed class Call
{
    public IMethodSymbol? M;
    public bool Project, Reduced, Opaque;
    public List<string>? Ambiguous;
    public string Name = "";
}

public sealed class Arg
{
    public bool Caller, HasConst;
    public string Text = "";
    public object? Const;
}

public sealed class DiHop
{
    public string Call = "", ResolvedTo = "";
}

public sealed class Visit
{
    public SyntaxNode Body = null!;
    public IMethodSymbol? Method;
    public int Depth;
    public Visit? Parent;
    public Dictionary<ISymbol, Arg> Binds = new(SymbolEqualityComparer.Default);
    public string In = "";
    public DiHop? Di;
    public SyntaxNode? CallSite; // the invocation in Parent.Body that created this visit
    public HashSet<SyntaxNode>? Skip;
    public string BindKey => string.Join(",", Binds.OrderBy(b => b.Key.Name, StringComparer.Ordinal).Select(b => b.Key.Name + (b.Value.Caller ? "!" : "")));
}

public sealed class Reach
{
    public List<Visit> Visits = new();
    public SortedSet<string> Calls = new(StringComparer.Ordinal);
    public bool Incomplete;
    public List<Unread> Unread = new();
    public List<DiCall> DiCalls = new();
}

public sealed class Reg
{
    public string IfaceText = "", ImplText = "", Lifetime = "", File = "";
    public INamedTypeSymbol? Iface, Impl;
    public int Line;
    public bool Factory, Keyed, Open;
}

public sealed partial class Ex
{
    public const int HelperDepth = 3;
    static readonly SymbolDisplayFormat Fq = new(typeQualificationStyle: SymbolDisplayTypeQualificationStyle.NameAndContainingTypesAndNamespaces, genericsOptions: SymbolDisplayGenericsOptions.None);

    public readonly List<INamedTypeSymbol> ProjTypes = new();
    public readonly Dictionary<string, List<IMethodSymbol>> MethodsByName = new(StringComparer.Ordinal);
    public readonly Dictionary<IPropertySymbol, INamedTypeSymbol> DbSets = new(SymbolEqualityComparer.Default);
    public readonly List<INamedTypeSymbol> DbContextTypes = new();
    public readonly List<Reg> Regs = new();
    public bool AssemblyScanning;
    public readonly HashSet<string> CallerKeys = new(StringComparer.Ordinal);
    public readonly HashSet<ISymbol> CallerFuncs = new(SymbolEqualityComparer.Default);
    readonly Dictionary<SyntaxNode, Call> callCache = new();

    public static string QualType(ITypeSymbol t) => t.ToDisplayString(Fq);
    public static string Qual(ISymbol s) => s.ContainingType != null ? QualType(s.ContainingType) + "." + (s is IMethodSymbol { MethodKind: MethodKind.Constructor } ? s.ContainingType.Name : s.Name) : s.Name;
    public bool IsProject(ISymbol? s) => s != null && s is not IErrorTypeSymbol && s.OriginalDefinition.Locations.Any(l => l.IsInSource && Trees.Contains(l.SourceTree!));
    public static bool IsErr(ITypeSymbol? t) => t == null || t is IErrorTypeSymbol;
    public static SyntaxNode? DeclOf(ISymbol s) => s.OriginalDefinition.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax();

    public static SyntaxNode? BodyOf(SyntaxNode? d) => d switch
    {
        MethodDeclarationSyntax m => (SyntaxNode?)m.Body ?? m.ExpressionBody?.Expression,
        LocalFunctionStatementSyntax l => (SyntaxNode?)l.Body ?? l.ExpressionBody?.Expression,
        ConstructorDeclarationSyntax c => (SyntaxNode?)c.Body ?? c.ExpressionBody?.Expression,
        AccessorDeclarationSyntax a => (SyntaxNode?)a.Body ?? a.ExpressionBody?.Expression,
        PropertyDeclarationSyntax p => p.ExpressionBody != null ? p.ExpressionBody.Expression : GetterBody(p),
        SimpleLambdaExpressionSyntax s => s.Body,
        ParenthesizedLambdaExpressionSyntax s => s.Body,
        AnonymousMethodExpressionSyntax s => s.Body,
        _ => null,
    };

    static SyntaxNode? GetterBody(PropertyDeclarationSyntax p)
    {
        var g = p.AccessorList?.Accessors.FirstOrDefault(a => a.IsKind(SyntaxKind.GetAccessorDeclaration));
        return g == null ? null : (SyntaxNode?)g.Body ?? g.ExpressionBody?.Expression;
    }

    // ---- the index ----------------------------------------------------------------------------------------------------

    public bool IsDbContextType(INamedTypeSymbol? t)
    {
        for (var b = t; b != null; b = b.BaseType)
            if (b is IErrorTypeSymbol e ? e.Name is "DbContext" or "IdentityDbContext" : b.Name == "DbContext" && !IsProject(b)) return true;
        return false;
    }

    void IndexSymbols()
    {
        foreach (var tree in Trees)
        {
            var sm = Comp.GetSemanticModel(tree);
            foreach (var n in tree.GetRoot().DescendantNodes())
            {
                switch (n)
                {
                    case BaseTypeDeclarationSyntax td when sm.GetDeclaredSymbol(td) is INamedTypeSymbol t:
                        ProjTypes.Add(t);
                        if (td is TypeDeclarationSyntax && IsDbContextType(t)) DbContextTypes.Add(t);
                        break;
                    case MethodDeclarationSyntax md when sm.GetDeclaredSymbol(md) is IMethodSymbol m:
                        (MethodsByName.TryGetValue(m.Name, out var l) ? l : MethodsByName[m.Name] = new()).Add(m);
                        break;
                    case LocalFunctionStatementSyntax lf when sm.GetDeclaredSymbol(lf) is IMethodSymbol lm:
                        (MethodsByName.TryGetValue(lm.Name, out var l2) ? l2 : MethodsByName[lm.Name] = new()).Add(lm);
                        break;
                }
            }
        }
        foreach (var c in DbContextTypes)
            foreach (var p in c.GetMembers().OfType<IPropertySymbol>())
                if (DeclOf(p) is PropertyDeclarationSyntax { Type: GenericNameSyntax { Identifier.Text: "DbSet", TypeArgumentList.Arguments.Count: 1 } g } pd
                    && Sm(pd).GetTypeInfo(g.TypeArgumentList.Arguments[0]).Type is INamedTypeSymbol e && IsProject(e))
                    DbSets[p] = e;
        CollectDi();
    }

    // ---- calls --------------------------------------------------------------------------------------------------------

    public static string? InvokedName(ExpressionSyntax e) => e switch
    {
        IdentifierNameSyntax i => i.Identifier.Text,
        GenericNameSyntax g => g.Identifier.Text,
        MemberAccessExpressionSyntax m => InvokedName(m.Name),
        MemberBindingExpressionSyntax b => InvokedName(b.Name),
        _ => null,
    };

    static bool ArityOk(IMethodSymbol m, int argc)
    {
        var ps = m.Parameters;
        int req = ps.Count(p => !p.IsOptional && !p.IsParams);
        return argc >= req && (argc <= ps.Length || (ps.Length > 0 && ps[^1].IsParams));
    }

    IEnumerable<ITypeSymbol> TypeChain(ITypeSymbol t)
    {
        for (var b = t; b != null; b = b.BaseType) yield return b;
        foreach (var i in t.AllInterfaces) yield return i;
    }

    public Call Resolve(InvocationExpressionSyntax inv)
    {
        if (callCache.TryGetValue(inv, out var cached)) return cached;
        var c = new Call { Name = InvokedName(inv.Expression) ?? "" };
        callCache[inv] = c;
        var sm = Sm(inv);
        var info = sm.GetSymbolInfo(inv);
        if (info.Symbol is IMethodSymbol ms && ms.ContainingType is not IErrorTypeSymbol)
        {
            c.Reduced = ms.ReducedFrom != null;
            c.M = (ms.ReducedFrom ?? ms).OriginalDefinition;
            c.Project = IsProject(c.M);
            return c;
        }
        var cands = info.CandidateSymbols.OfType<IMethodSymbol>().Where(m => m.ContainingType is not IErrorTypeSymbol && IsProject(m)).Select(m => (m.ReducedFrom ?? m).OriginalDefinition).Distinct(SymbolEqualityComparer.Default).Cast<IMethodSymbol>().ToList();
        if (cands.Count == 0) cands = Lexical(inv, sm, c);
        if (cands.Count == 1) { c.M = cands[0]; c.Project = true; c.Reduced = cands[0].IsExtensionMethod && inv.Expression is MemberAccessExpressionSyntax ma0 && !(sm.GetSymbolInfo(ma0.Expression).Symbol is INamedTypeSymbol); }
        else if (cands.Count > 1) { c.Ambiguous = cands.Select(Qual).Distinct(StringComparer.Ordinal).OrderBy(x => x, StringComparer.Ordinal).ToList(); c.Project = true; }
        else c.Opaque = true;
        return c;
    }

    List<IMethodSymbol> Lexical(InvocationExpressionSyntax inv, SemanticModel sm, Call c)
    {
        var res = new List<IMethodSymbol>();
        int argc = inv.ArgumentList.Arguments.Count;
        if (inv.Expression is MemberAccessExpressionSyntax ma)
        {
            ITypeSymbol? rt;
            var rs = sm.GetSymbolInfo(ma.Expression).Symbol;
            bool isStatic = rs is INamedTypeSymbol;
            rt = isStatic ? (ITypeSymbol)rs! : sm.GetTypeInfo(ma.Expression).Type;
            if (IsErr(rt)) return res;
            foreach (var t in TypeChain(rt!))
                foreach (var m in t.GetMembers(c.Name).OfType<IMethodSymbol>())
                    if (IsProject(m) && ArityOk(m, argc)) res.Add(m.OriginalDefinition);
            if (!isStatic && MethodsByName.TryGetValue(c.Name, out var exts))
                foreach (var m in exts)
                    if (m.IsExtensionMethod && ArityOk(m, argc + 1) && m.Parameters[0].Type is { } p0 && (p0 is ITypeParameterSymbol || Comp.ClassifyConversion(rt!, p0).IsImplicit)) res.Add(m);
        }
        else
        {
            foreach (var m in sm.LookupSymbols(inv.SpanStart, null, c.Name).OfType<IMethodSymbol>())
                if (IsProject(m) && ArityOk(m, argc)) res.Add(m.OriginalDefinition);
        }
        return res.Distinct(SymbolEqualityComparer.Default).Cast<IMethodSymbol>().ToList();
    }

    public Call ResolveNew(ExpressionSyntax creation)
    {
        var sm = Sm(creation);
        var c = new Call { Name = "new" };
        var info = sm.GetSymbolInfo(creation);
        if (info.Symbol is IMethodSymbol ms && IsProject(ms)) { c.M = ms.OriginalDefinition; c.Project = true; return c; }
        var t = sm.GetTypeInfo(creation).Type as INamedTypeSymbol;
        if (t == null || !IsProject(t)) { c.Opaque = true; return c; }
        int argc = creation switch { ObjectCreationExpressionSyntax o => o.ArgumentList?.Arguments.Count ?? 0, ImplicitObjectCreationExpressionSyntax i => i.ArgumentList.Arguments.Count, _ => 0 };
        var cands = t.Constructors.Where(k => !k.IsStatic && ArityOk(k, argc)).ToList();
        if (cands.Count == 1) { c.M = cands[0]; c.Project = true; }
        else if (cands.Count > 1) { c.Project = true; c.Ambiguous = cands.Select(k => Qual(k) + "(" + string.Join(",", k.Parameters.Select(p => p.Type.Name)) + ")").ToList(); }
        else c.Opaque = true;
        return c;
    }

    // ---- DI (N10) -----------------------------------------------------------------------------------------------------

    static readonly HashSet<string> AddNames = new(StringComparer.Ordinal) { "AddScoped", "AddTransient", "AddSingleton", "TryAddScoped", "TryAddTransient", "TryAddSingleton", "AddKeyedScoped", "AddKeyedTransient", "AddKeyedSingleton" };

    void CollectDi()
    {
        foreach (var tree in Trees)
            foreach (var inv in tree.GetRoot().DescendantNodes().OfType<InvocationExpressionSyntax>())
            {
                var name = InvokedName(inv.Expression);
                if (name == null) continue;
                if (name == "Scan" && Sm(inv).GetSymbolInfo(inv).Symbol is not { } && Js.Text(inv).Contains("FromAssembl")) AssemblyScanning = true;
                if (!AddNames.Contains(name) || inv.Expression is not MemberAccessExpressionSyntax) continue;
                var sm = Sm(inv);
                var r = new Reg { Lifetime = name.Replace("TryAdd", "").Replace("AddKeyed", "").Replace("Add", "").ToLowerInvariant(), File = FileOf(inv), Line = LineOf(((MemberAccessExpressionSyntax)inv.Expression).Name.Identifier), Keyed = name.Contains("Keyed") };
                ITypeSymbol? Sym(TypeSyntax ts) => sm.GetTypeInfo(ts).Type;
                var args = inv.ArgumentList.Arguments;
                if (((MemberAccessExpressionSyntax)inv.Expression).Name is GenericNameSyntax g)
                {
                    var ta = g.TypeArgumentList.Arguments;
                    r.IfaceText = ta[0].ToString();
                    r.Iface = Sym(ta[0]) as INamedTypeSymbol;
                    if (ta.Count >= 2) { r.ImplText = ta[1].ToString(); r.Impl = Sym(ta[1]) as INamedTypeSymbol; }
                    else if (args.Count > 0 && args[^1].Expression is LambdaExpressionSyntax lam)
                    {
                        r.Factory = true;
                        var body = lam is SimpleLambdaExpressionSyntax sl ? sl.Body : ((ParenthesizedLambdaExpressionSyntax)lam).Body;
                        if (body is InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax { Name: GenericNameSyntax { Identifier.Text: "GetRequiredService" or "GetService", TypeArgumentList.Arguments.Count: 1 } gn } })
                        { r.ImplText = gn.TypeArgumentList.Arguments[0].ToString(); r.Impl = Sym(gn.TypeArgumentList.Arguments[0]) as INamedTypeSymbol; r.Factory = false; }
                        else if (body is ObjectCreationExpressionSyntax oc && oc.ArgumentList?.Arguments.Count == 0) { r.ImplText = oc.Type.ToString(); r.Impl = Sym(oc.Type) as INamedTypeSymbol; r.Factory = false; }
                    }
                    else { r.ImplText = r.IfaceText; r.Impl = r.Iface; }
                }
                else
                {
                    var tys = args.Select(a => a.Expression).OfType<TypeOfExpressionSyntax>().ToList();
                    if (tys.Count == 0) continue;
                    r.IfaceText = tys[0].Type.ToString();
                    r.ImplText = (tys.Count > 1 ? tys[1] : tys[0]).Type.ToString();
                    r.Iface = Sym(tys[0].Type) as INamedTypeSymbol;
                    r.Impl = Sym((tys.Count > 1 ? tys[1] : tys[0]).Type) as INamedTypeSymbol;
                    r.Open = tys.Any(t => t.Type is GenericNameSyntax { IsUnboundGenericName: true });
                }
                Regs.Add(r);
            }
    }

    static string Short(IMethodSymbol m) => m.ContainingType.Name + "." + m.Name;

    /// <summary>The implementation a call on an interface (or abstract) member reaches; null with a reason when none can be named.</summary>
    public IMethodSymbol? DiTarget(IMethodSymbol m, out string? reason, out List<string> impls)
    {
        reason = null; impls = new();
        var t = m.ContainingType;
        if (t.TypeKind != TypeKind.Interface && !(t.IsAbstract && m.IsAbstract)) return m;
        var regs = Regs.Where(r => !r.Keyed && r.Iface != null && SymbolEqualityComparer.Default.Equals(r.Iface, t)).ToList();
        impls = regs.Select(r => r.ImplText).Distinct(StringComparer.Ordinal).OrderBy(x => x, StringComparer.Ordinal).ToList();
        if (AssemblyScanning) { reason = "assembly scanning registers implementations (not read)"; return null; }
        if (regs.Count != 1 || regs[0].Impl == null || regs[0].Open) { reason = regs.Count == 0 ? "no registration of " + t.Name : $"{regs.Count} registrations ({string.Join(", ", impls)}): never the first match"; return null; }
        var impl = regs[0].Impl!;
        if (t.TypeKind == TypeKind.Interface)
        {
            var found = impl.FindImplementationForInterfaceMember(m) as IMethodSymbol;
            if (found == null) reason = "the implementation of " + Short(m) + " is not in the project";
            return found;
        }
        for (var b = impl; b != null; b = b.BaseType)
            foreach (var cand in b.GetMembers(m.Name).OfType<IMethodSymbol>())
                for (var o = cand; o != null; o = o.OverriddenMethod)
                    if (SymbolEqualityComparer.Default.Equals(o.OriginalDefinition, m)) return cand;
        reason = "no override of " + Short(m);
        return null;
    }

    // ---- the request reach ----------------------------------------------------------------------------------------------

    public IEnumerable<(IParameterSymbol P, ExpressionSyntax E)> MapArgs(InvocationExpressionSyntax inv, Call c)
    {
        var m = c.M!;
        var ps = m.Parameters;
        int shift = 0;
        if (c.Reduced && inv.Expression is MemberAccessExpressionSyntax ma && ps.Length > 0) { yield return (ps[0], ma.Expression); shift = 1; }
        int i = 0;
        foreach (var a in inv.ArgumentList.Arguments)
        {
            IParameterSymbol? p = a.NameColon != null ? ps.FirstOrDefault(x => x.Name == a.NameColon.Name.Identifier.Text) : (i + shift < ps.Length ? ps[i + shift] : ps.Length > 0 && ps[^1].IsParams ? ps[^1] : null);
            i++;
            if (p != null) yield return (p, a.Expression);
        }
    }

    public Reach ComputeReach(SyntaxNode body, IMethodSymbol? sym, string inName, string route, IEnumerable<IParameterSymbol>? handlerParams = null)
    {
        var r = new Reach();
        var root = new Visit { Body = body, Method = sym, In = inName };
        Walk(r, root, route, new HashSet<string>(StringComparer.Ordinal) { inName });
        return r;
    }

    static readonly HashSet<string> EfNames = new(StringComparer.Ordinal) { "Where", "Select", "SelectMany", "OrderBy", "OrderByDescending", "ThenBy", "ThenByDescending", "First", "FirstOrDefault", "FirstAsync", "FirstOrDefaultAsync", "Single", "SingleOrDefault", "SingleAsync", "SingleOrDefaultAsync", "Last", "LastOrDefault", "LastAsync", "LastOrDefaultAsync", "Find", "FindAsync", "Any", "AnyAsync", "All", "AllAsync", "Count", "CountAsync", "LongCount", "LongCountAsync", "Sum", "SumAsync", "Min", "MinAsync", "Max", "MaxAsync", "Average", "AverageAsync", "ToList", "ToListAsync", "ToArray", "ToArrayAsync", "ToDictionary", "ToDictionaryAsync", "Skip", "Take", "Include", "ThenInclude", "AsNoTracking", "AsNoTrackingWithIdentityResolution", "AsTracking", "AsQueryable", "AsEnumerable", "AsAsyncEnumerable", "Add", "AddAsync", "AddRange", "AddRangeAsync", "Remove", "RemoveRange", "Update", "UpdateRange", "Attach", "Entry", "SaveChanges", "SaveChangesAsync", "ExecuteUpdate", "ExecuteUpdateAsync", "ExecuteDelete", "ExecuteDeleteAsync", "SetProperty", "IgnoreQueryFilters", "Set", "BeginTransaction", "BeginTransactionAsync", "CommitAsync", "Commit", "RollbackAsync", "Rollback", "DisposeAsync", "Dispose", "Contains", "GroupBy", "Join", "Distinct", "Reverse", "TagWith", "AsSplitQuery", "ExecuteSqlRaw", "ExecuteSqlRawAsync", "FromSqlRaw", "FromSqlInterpolated", "Load", "LoadAsync", "CreateExecutionStrategy", "Execute", "ExecuteAsync", "EnsureCreated", "EnsureCreatedAsync", "MigrateAsync", "Migrate", "ChangeTracker", "Clear" };

    void Walk(Reach r, Visit v, string route, HashSet<string> stack)
    {
        r.Visits.Add(v);
        foreach (var node in v.Body.DescendantNodesAndSelf())
        {
            if (node is not InvocationExpressionSyntax inv) continue;
            var c = Resolve(inv);
            if (c.Ambiguous != null)
            {
                r.Incomplete = true;
                AddUnread(r.Unread, inv, c.Name, "ambiguous call: " + string.Join(", ", c.Ambiguous), "call", route);
                continue;
            }
            if (!c.Project || c.M == null) { if (c.M == null && !EfNames.Contains(c.Name) && ReceivesStateful(inv)) r.Incomplete = true; continue; }
            var target = DiTarget(c.M, out var why, out var impls);
            DiHop? hop = null;
            if (target == null || (!SymbolEqualityComparer.Default.Equals(target, c.M) && !SymbolEqualityComparer.Default.Equals(target.ContainingType, c.M.ContainingType)))
            {
                var callName = Short(c.M);
                if (target == null)
                {
                    r.Incomplete = true;
                    var reason = why + (ReceivesStateful(inv) ? "; the route's reach is incomplete (an opaque call receives an entity)" : "");
                    AddUnread(r.Unread, inv, callName, reason!, "di", route, LineOf(((inv.Expression as MemberAccessExpressionSyntax)?.Name ?? (SyntaxNode)inv).GetFirstToken()));
                    r.DiCalls.Add(new DiCall { Route = route, Call = callName, Reason = reason, File = FileOf(inv), Line = LineOf(inv) });
                    continue;
                }
                hop = new DiHop { Call = callName, ResolvedTo = Qual(target) };
                r.DiCalls.Add(new DiCall { Route = route, Call = callName, ResolvedTo = hop.ResolvedTo, File = FileOf(inv), Line = LineOf(inv) });
            }
            var body = BodyOf(DeclOf(target));
            if (body == null || (body.SyntaxTree == v.Body.SyntaxTree && v.Body.Span.Contains(body.Span))) continue; // abstract, or a local function already part of this body
            var q = Qual(target);
            r.Calls.Add(q);
            if (v.Depth >= HelperDepth) { r.Incomplete = true; continue; }
            var child = new Visit { Body = body, Method = target, Depth = v.Depth + 1, Parent = v, In = q, Di = hop ?? v.Di, CallSite = inv };
            foreach (var (p, e) in MapArgs(inv, c))
            {
                var tp = target.Parameters.FirstOrDefault(x => x.Name == p.Name && (x.Ordinal == p.Ordinal || SymbolEqualityComparer.Default.Equals(x.Type, p.Type))) ?? (p.Ordinal < target.Parameters.Length ? target.Parameters[p.Ordinal] : null);
                if (tp != null) { var cv = ConstOf(e, v); child.Binds[tp] = new Arg { Caller = IsCaller(e, v), Text = Js.Text(e), Const = cv, HasConst = cv != null }; }
            }
            var key = q + "|" + child.BindKey;
            if (stack.Contains(key) || r.Visits.Any(x => x.In == q && x.BindKey == child.BindKey && x.Depth <= child.Depth)) continue;
            stack.Add(key);
            Walk(r, child, route, stack);
            stack.Remove(key);
        }
    }

    bool ReceivesStateful(InvocationExpressionSyntax inv)
    {
        var sm = Sm(inv);
        foreach (var e in inv.ArgumentList.Arguments.Select(a => a.Expression).Concat(inv.Expression is MemberAccessExpressionSyntax ma ? new[] { ma.Expression } : []))
        {
            var t = sm.GetTypeInfo(e).Type as INamedTypeSymbol;
            if (t != null && (IsDbContextType(t) || Entities.ContainsKey(t) || t.Name == "HttpContext")) return true;
            if (EntityOfLocal(e) != null) return true;
        }
        return false;
    }

    // ---- who is the caller (N4, the Go callerKeys/callerFuncs) ----------------------------------------------------------

    static bool IsPrincipal(ITypeSymbol? t) => t is { Name: "ClaimsPrincipal" or "ClaimsIdentity" };
    static readonly HashSet<string> ClaimReaders = new(StringComparer.Ordinal) { "FindFirst", "FindFirstValue", "FindAll", "FindFirstOrDefault" };
    static readonly HashSet<string> PassThrough = new(StringComparer.Ordinal) { "Parse", "TryParse", "ToString", "ToInt32", "ToInt64", "ToUInt64", "ToUInt32", "ToInt16", "Trim", "ToLowerInvariant", "ToUpperInvariant", "GetValueOrDefault", "Value" };

    public bool IsCaller(ExpressionSyntax? e, Visit? v, int depth = 0)
    {
        if (e == null || depth > 6) return false;
        var sm = Sm(e);
        switch (e)
        {
            case ParenthesizedExpressionSyntax p: return IsCaller(p.Expression, v, depth + 1);
            case PostfixUnaryExpressionSyntax pu: return IsCaller(pu.Operand, v, depth + 1);
            case CastExpressionSyntax c: return IsCaller(c.Expression, v, depth + 1);
            case AwaitExpressionSyntax a: return IsCaller(a.Expression, v, depth + 1);
            case BinaryExpressionSyntax { RawKind: (int)SyntaxKind.CoalesceExpression or (int)SyntaxKind.AsExpression } b: return IsCaller(b.Left, v, depth + 1);
            case ConditionalAccessExpressionSyntax ca: return IsCaller(ca.Expression, v, depth + 1) || ReadsUser(ca);
            case ElementAccessExpressionSyntax ea:
                return ea.Expression is { } ee && sm.GetSymbolInfo(ee).Symbol is IPropertySymbol { Name: "Items" } && ea.ArgumentList.Arguments.Count == 1 && sm.GetConstantValue(ea.ArgumentList.Arguments[0].Expression) is { HasValue: true, Value: string key } && CallerKeys.Contains(key);
            case IdentifierNameSyntax id:
                {
                    var s = sm.GetSymbolInfo(id).Symbol;
                    if (s is IParameterSymbol ps)
                    {
                        for (var cur = v; cur != null; cur = null) if (cur.Binds.TryGetValue(ps, out var a)) return a.Caller;
                        return IsPrincipal(ps.Type) && (v == null || v.Depth == 0) && ps.ContainingSymbol is IMethodSymbol;
                    }
                    if (s is ILocalSymbol ls && ls.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax() is VariableDeclaratorSyntax { Initializer.Value: { } init }) return IsCaller(init, v, depth + 1);
                    if (s is IPropertySymbol prop) return PropertyIsCaller(prop, depth);
                    return false;
                }
            case MemberAccessExpressionSyntax ma:
                {
                    var s = sm.GetSymbolInfo(ma.Name).Symbol;
                    if (s is IPropertySymbol { Name: "User" } up && (up.ContainingType.Name is "ControllerBase" or "HttpContext" or "ControllerContext" or "PageModel")) return true;
                    if (s is IPropertySymbol { Name: "User" } && IsPrincipal(sm.GetTypeInfo(ma).Type)) return true;
                    if (s is IPropertySymbol prop && IsProject(prop) && PropertyIsCaller(prop, depth, ma.Expression)) return true;
                    return IsCaller(ma.Expression, v, depth + 1);
                }
            case InvocationExpressionSyntax inv:
                {
                    var c = Resolve(inv);
                    if (ClaimReaders.Contains(c.Name) && inv.Expression is MemberAccessExpressionSyntax cm && (IsCaller(cm.Expression, v, depth + 1) || ReadsUser(cm.Expression))) return true;
                    if (PassThrough.Contains(c.Name) || (c.M != null && !c.Project && c.M.ContainingType.Name is "Convert"))
                        return (inv.Expression is MemberAccessExpressionSyntax pm && IsCaller(pm.Expression, v, depth + 1)) || inv.ArgumentList.Arguments.Any(a => IsCaller(a.Expression, v, depth + 1));
                    if (!c.Project || c.M == null) return false;
                    var target = DiTarget(c.M, out _, out _);
                    if (target == null) return false;
                    if (CallerFuncs.Contains(target)) return true;
                    var body = BodyOf(DeclOf(target));
                    if (body == null) return false;
                    var nv = new Visit { Body = body, Method = target, Depth = (v?.Depth ?? 0) + 1 };
                    foreach (var (p, ex) in MapArgs(inv, c))
                    {
                        var tp = target.Parameters.FirstOrDefault(x => x.Name == p.Name) ?? p;
                        nv.Binds[tp] = new Arg { Caller = IsCaller(ex, v, depth + 1) };
                    }
                    return Returns(body).Any(rx => IsCaller(rx, nv, depth + 1));
                }
        }
        return false;
    }

    bool PropertyIsCaller(IPropertySymbol prop, int depth, ExpressionSyntax? recv = null)
    {
        if (!IsProject(prop) && prop.ContainingType.Name != "User") return false;
        IPropertySymbol target = prop;
        if (prop.ContainingType.TypeKind == TypeKind.Interface)
        {
            var regs = Regs.Where(r => !r.Keyed && r.Iface != null && SymbolEqualityComparer.Default.Equals(r.Iface, prop.ContainingType)).ToList();
            if (regs.Count != 1 || regs[0].Impl == null || regs[0].Impl!.FindImplementationForInterfaceMember(prop) is not IPropertySymbol ip) return false;
            target = ip;
        }
        var body = BodyOf(DeclOf(target));
        if (body == null) return false;
        return Returns(body).Any(x => IsCaller(x, new Visit { Body = body, Depth = 1 }, depth + 1));
    }

    static bool ReadsUser(SyntaxNode n) => n.DescendantNodesAndSelf().Any(x => x is MemberAccessExpressionSyntax { Name.Identifier.Text: "User" } or MemberBindingExpressionSyntax { Name.Identifier.Text: "User" });

    /// <summary>The expressions a body returns (an expression body is its own result).</summary>
    public static IEnumerable<ExpressionSyntax> Returns(SyntaxNode body)
    {
        if (body is ExpressionSyntax ex) { yield return ex; yield break; }
        foreach (var r in body.DescendantNodes(n => n is not LambdaExpressionSyntax and not LocalFunctionStatementSyntax).OfType<ReturnStatementSyntax>())
            if (r.Expression != null) yield return r.Expression;
    }

    // ---- LINQ chains: the entity a query yields ------------------------------------------------------------------------

    static readonly HashSet<string> KeepsElem = new(StringComparer.Ordinal) { "Where", "OrderBy", "OrderByDescending", "ThenBy", "ThenByDescending", "Skip", "Take", "Include", "AsNoTracking", "AsNoTrackingWithIdentityResolution", "AsTracking", "AsQueryable", "IgnoreQueryFilters", "Distinct", "Reverse", "TagWith", "AsSplitQuery", "AsEnumerable", "SkipWhile", "TakeWhile" };
    static readonly HashSet<string> OneElem = new(StringComparer.Ordinal) { "First", "FirstOrDefault", "FirstAsync", "FirstOrDefaultAsync", "Single", "SingleOrDefault", "SingleAsync", "SingleOrDefaultAsync", "Last", "LastOrDefault", "LastAsync", "LastOrDefaultAsync", "Find", "FindAsync", "ElementAt", "ElementAtOrDefault" };
    static readonly HashSet<string> ManyElem = new(StringComparer.Ordinal) { "ToList", "ToListAsync", "ToArray", "ToArrayAsync", "AsAsyncEnumerable" };

    public sealed class Chain
    {
        public INamedTypeSymbol? T;
        public bool One, Many, IgnoreFilters;
        public string Source = "";
    }

    /// <summary>The entity type a query expression yields and how it was shaped, or null when it is not a query on a DbSet.</summary>
    public Chain? ChainOf(ExpressionSyntax? e)
    {
        switch (e)
        {
            case null: return null;
            case ParenthesizedExpressionSyntax p: return ChainOf(p.Expression);
            case AwaitExpressionSyntax a: return ChainOf(a.Expression);
            case PostfixUnaryExpressionSyntax pu: return ChainOf(pu.Operand);
            case InvocationExpressionSyntax inv when inv.Expression is MemberAccessExpressionSyntax ma:
                {
                    var name = InvokedName(inv.Expression) ?? "";
                    if (name == "Set" && ma.Name is GenericNameSyntax { TypeArgumentList.Arguments.Count: 1 } g && Sm(inv).GetTypeInfo(g.TypeArgumentList.Arguments[0]).Type is INamedTypeSymbol st && Entities.ContainsKey(st))
                        return new Chain { T = st, Source = Js.Text(inv) };
                    var inner = ChainOf(ma.Expression);
                    if (inner == null || inner.T == null) return null;
                    if (name == "IgnoreQueryFilters") inner.IgnoreFilters = true;
                    if (KeepsElem.Contains(name)) return inner;
                    if (OneElem.Contains(name)) return new Chain { T = inner.T, One = true, IgnoreFilters = inner.IgnoreFilters, Source = inner.Source };
                    if (ManyElem.Contains(name)) return new Chain { T = inner.T, Many = true, IgnoreFilters = inner.IgnoreFilters, Source = inner.Source };
                    return new Chain { T = null, IgnoreFilters = inner.IgnoreFilters, Source = inner.Source };
                }
            case MemberAccessExpressionSyntax m:
                if (Sm(m).GetSymbolInfo(m.Name).Symbol is IPropertySymbol ps && DbSets.TryGetValue((IPropertySymbol)ps.OriginalDefinition, out var t)) return new Chain { T = t, Source = Js.Text(m) };
                return null;
            case QueryExpressionSyntax q:
                {
                    var inner = ChainOf(q.FromClause.Expression);
                    if (inner?.T == null) return null;
                    bool identity = q.Body.SelectOrGroup is SelectClauseSyntax { Expression: IdentifierNameSyntax sid } && sid.Identifier.Text == q.FromClause.Identifier.Text;
                    return new Chain { T = identity ? inner.T : null, IgnoreFilters = inner.IgnoreFilters, Source = inner.Source };
                }
            case IdentifierNameSyntax id:
                if (Sm(id).GetSymbolInfo(id).Symbol is IPropertySymbol ip && DbSets.TryGetValue((IPropertySymbol)ip.OriginalDefinition, out var t2)) return new Chain { T = t2, Source = Js.Text(id) };
                if (Sm(id).GetSymbolInfo(id).Symbol is ILocalSymbol ls && chainDepth < 4 && ls.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax() is VariableDeclaratorSyntax { Initializer.Value: { } init })
                {
                    chainDepth++;
                    try { return ChainOf(init); } finally { chainDepth--; }
                }
                return null;
        }
        return null;
    }

    int chainDepth;

    /// <summary>The entity a local/expression holds as one row: a variable assigned from a single-row query or `new T`.</summary>
    public INamedTypeSymbol? EntityOfLocal(ExpressionSyntax? e, int depth = 0)
    {
        if (e == null || depth > 4) return null;
        switch (e)
        {
            case ParenthesizedExpressionSyntax p: return EntityOfLocal(p.Expression, depth + 1);
            case PostfixUnaryExpressionSyntax pu: return EntityOfLocal(pu.Operand, depth + 1);
            case AwaitExpressionSyntax a: return EntityOfLocal(a.Expression, depth + 1);
            case ObjectCreationExpressionSyntax or ImplicitObjectCreationExpressionSyntax:
                return Sm(e).GetTypeInfo(e).Type is INamedTypeSymbol nt && Entities.ContainsKey(nt) ? nt : null;
            case IdentifierNameSyntax id:
                {
                    var s = Sm(id).GetSymbolInfo(id).Symbol;
                    if (s is ILocalSymbol ls)
                    {
                        if (ls.Type is INamedTypeSymbol lt && Entities.ContainsKey((INamedTypeSymbol)lt.OriginalDefinition)) return lt;
                        var d = ls.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax();
                        if (d is VariableDeclaratorSyntax { Initializer.Value: { } init }) return ChainOf(init) is { One: true, T: { } t } ? t : EntityOfLocal(init, depth + 1);
                        if (d is SingleVariableDesignationSyntax || d is ForEachStatementSyntax) return ForEachEntity(d);
                    }
                    if (s is IParameterSymbol pp && pp.Type is INamedTypeSymbol pt && Entities.ContainsKey(pt)) return pt;
                    return null;
                }
        }
        return null;
    }

    INamedTypeSymbol? ForEachEntity(SyntaxNode d)
    {
        var fe = d as ForEachStatementSyntax ?? d.Parent?.Parent as ForEachStatementSyntax;
        return fe != null && ChainOf(fe.Expression) is { Many: true, T: { } t } ? t : fe != null && ChainOf(fe.Expression) is { T: { } t2 } ? t2 : null;
    }
}
