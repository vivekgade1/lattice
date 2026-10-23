// The extraction: the order of the passes, the per-route assembly, the login route and the document's own keys.
// A pass added by another node (IExtractPass in its own file) runs last.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed partial class Ex
{
    static readonly HashSet<string> VerifyNames = new(StringComparer.Ordinal) { "Verify", "VerifyHashedPassword", "VerifyPassword", "CheckPassword", "CheckPasswordAsync", "CheckPasswordSignInAsync", "PasswordSignInAsync", "ValidatePassword", "FixedTimeEquals" };

    public Doc Run()
    {
        Load();
        D.Dotnet.Sdk = SdkVersion;
        D.Dotnet.TargetFramework = P.Tfm;
        D.Dotnet.SdkProject = P.Sdk;
        D.Dotnet.Nullable = P.Nullable.ToLowerInvariant() == "enable" ? "enable" : P.Nullable;
        D.Module = P.RootNamespace;
        IndexSymbols();
        Guard("ResolveProviders", ResolveProviders);
        Guard("ResolveJsonNaming", ResolveJsonNaming);
        Guard("ReadAuthConfig", ReadAuthConfig);
        Guard("BuildTables", BuildTables);
        Guard("ReadValidators", ReadValidators);
        Guard("BuildExceptionMap", BuildExceptionMap);
        Guard("CollectCaller", CollectCaller);
        Guard("ReadQueryFilters", ReadQueryFilters);
        Guard("DiscoverRoutes", DiscoverRoutes);
        Guard("ReportConventional", ReportConventional);
        Guard("CollectMiddleware", CollectMiddleware);
        Guard("CollectFilters", CollectFilters);
        foreach (var rec in recOrder) Guard(rec.Id, () => AssembleRoute(rec), rec);
        Guard("ScanStateOutsideRoutes", ScanStateOutsideRoutes);
        Guard("NoteFilterResolutions", NoteFilterResolutions);
        Guard("FinishState", FinishState);
        Guard("BuildModels", BuildModels);
        Guard("ReportBindStatus", ReportBindStatus);
        Guard("BuildErrorKeys", BuildErrorKeys);
        Guard("BuildAuthObject", BuildAuthObject);
        Guard("FinishDocument", FinishDocument);
        foreach (var pass in typeof(Ex).Assembly.GetTypes().Where(t => typeof(IExtractPass).IsAssignableFrom(t) && t is { IsClass: true, IsAbstract: false }).Select(t => (IExtractPass)Activator.CreateInstance(t)!).OrderBy(p => p.Order).ThenBy(p => p.GetType().FullName, StringComparer.Ordinal))
            pass.Run(this, D);
        return D;
    }

    /// <summary>A pass that throws is an `unread` entry (and an incomplete document), never a lost extraction.</summary>
    void Guard(string what, Action pass, RouteRec? rec = null)
    {
        try { pass(); }
        catch (Exception e) when (e is not OutOfMemoryException)
        {
            var reason = "extractor error " + e.GetType().Name + " at " + (e.StackTrace ?? "").Split('\n').FirstOrDefault()?.Trim().Split(" in ")[0];
            var u = new Unread { File = rec?.R.File ?? P.File, Line = rec?.R.Line ?? 1, What = "pass " + what, Reason = reason, Category = "internal", Route = rec?.Id };
            UnreadAll.Add(u);
            if (rec != null) { rec.R.Unread.Add(u); if (!D.Routes.Contains(rec.R)) D.Routes.Add(rec.R); }
        }
    }

    void AssembleRoute(RouteRec rec)
    {
        var r = rec.R;
        r.PathTemplate = Normalize(r.Path);
        if (rec.Framework)
        {
            foreach (var p in r.Params.Where(p => p.Source == "body")) { p.Name = Wire(p.Name, "minimal"); p.Wire = p.Name; }
            rec.Reach = new Reach();
            D.Routes.Add(r);
            return;
        }
        EvalAuth(rec);
        if (rec.Body == null)
        {
            AddUnread(r.Unread, rec.At, r.Handler?.Func ?? rec.Id, "handler body is not available", "route", rec.Id);
            rec.Reach = new Reach();
            rec.Reach.Visits.Add(new Visit { Body = rec.At, In = r.Handler?.Func ?? "" });
        }
        else rec.Reach = ComputeReach(rec.Body, rec.HandlerSym, r.Handler!.Func, rec.Id);
        var reach = rec.Reach;
        BuildParams(rec);
        var success = new HashSet<int>();
        var raises = new List<XRaise>();
        ScanRaises(reach, rec.Id, raises, success, r.Unread);
        r.Raises.InsertRange(0, raises);
        HandAuthenticated(rec);
        ApplyMiddleware(rec);
        ScanOwners(rec);
        ScanState(rec);
        r.Unread.AddRange(reach.Unread.Where(u => !r.Unread.Any(x => x.File == u.File && x.Line == u.Line && x.What == u.What)));
        r.Calls = reach.Calls.ToList();
        r.Tx = reach.Visits.Any(v => v.Body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>().Any(i => InvokedName(i.Expression) is "BeginTransaction" or "BeginTransactionAsync" or "CreateExecutionStrategy") || v.Body.DescendantNodesAndSelf().OfType<ObjectCreationExpressionSyntax>().Any(o => o.Type.ToString() == "TransactionScope"));
        r.StatusCodes = success.OrderBy(x => x).ToList();
        r.StatusCode = r.StatusCodes.Count == 1 ? r.StatusCodes[0] : null;
        r.Tables = r.Tables.Distinct(StringComparer.Ordinal).OrderBy(x => x, StringComparer.Ordinal).ToList();
        r.BodyModels = r.BodyModels.Distinct(StringComparer.Ordinal).ToList();
        r.Raises = r.Raises.GroupBy(x => $"{x.File}|{x.Line}|{x.Status}|{x.Code}|{x.Bind}").Select(g => g.OrderBy(x => x.Kind == "middleware" ? 1 : 0).First()).OrderBy(x => x.File, StringComparer.Ordinal).ThenBy(x => x.Line).ThenBy(x => x.Status).ToList();
        r.ErrorWriter = r.Raises.Any(x => x.Kind == "exception") ? D.ErrorWriter?.Func : null;
        r.Unread = r.Unread.OrderBy(u => u.File, StringComparer.Ordinal).ThenBy(u => u.Line).ThenBy(u => u.What, StringComparer.Ordinal).ToList();
        D.Routes.Add(r);
        foreach (var dc in reach.DiCalls) (dc.ResolvedTo != null ? D.Dotnet.Di.ResolvedCalls : D.Dotnet.Di.UnreadCalls).Add(dc);
    }

    // ---- the auth object and the login route --------------------------------------------------------------------------------

    void BuildAuthObject()
    {
        var a = new XAuth { Scheme = scheme, CallerKeys = ClaimReads.ToList() };
        var amw = Mws.FirstOrDefault(m => m.X.Authenticating);
        a.Middleware = amw?.X.Func;
        DetectLogin(a);
        var users = a.UsersTable != null ? Entities.Keys.FirstOrDefault(k => k.Name == a.UsersTable) : Entities.Keys.FirstOrDefault(k => amw?.X.UsersTable == k.Name) ?? Entities.Values.FirstOrDefault(e => e.Props.Any(p => System.Text.RegularExpressions.Regex.IsMatch(p.P.Name, "password|hash|pwd", System.Text.RegularExpressions.RegexOptions.IgnoreCase)))?.T;
        if (users != null) { a.UsersTable = users.Name; D.Dotnet.Auth.UsersTableSql = TableNameOf(Entities[users]); }
        if (a.Scheme == null && Mws.Count == 0 && a.LoginPath == null && users == null) a.Notes.Add("no authentication evidence");
        if (a.LoginPath == null && identityPrefix != null)
        {
            a.LoginPath = identityPrefix + "/login"; a.UsernameField = Wire("Email", "minimal"); a.PasswordField = Wire("Password", "minimal"); a.TokenField = Wire("AccessToken", "minimal");
            a.UsersTable = identityUser; a.PasswordColumn = "PasswordHash"; D.Dotnet.Auth.UsersTableSql = "AspNetUsers"; users = null;
        }
        D.Auth = a;
        D.Dotnet.Auth.AdminRole = adminRole;
        D.Dotnet.Auth.CallerKeys = CallerKeys.OrderBy(k => k, StringComparer.Ordinal).ToList();
        D.Dotnet.Auth.CallerFuncs = CallerFuncs.Select(Qual).OrderBy(x => x, StringComparer.Ordinal).ToList();
        D.Dotnet.Auth.FallbackPolicy = fallbackMeta?.Where;
        D.Dotnet.Auth.Pipeline = pipeline.Distinct().ToList();
        D.Dotnet.Auth.RoleColumn = RoleColumnOf(users);
    }

    /// <summary>The users entity's enum property with a member named like the admin role: (column, members, storage).</summary>
    XRoleColumn? RoleColumnOf(INamedTypeSymbol? users)
    {
        if (users == null || adminRole == null || !Entities.TryGetValue(users, out var ei)) return null;
        foreach (var pi in ei.Props)
        {
            var st = Strip(pi.P.Type);
            if (st.TypeKind != TypeKind.Enum) continue;
            var members = st.GetMembers().OfType<IFieldSymbol>().Where(f => f.HasConstantValue).ToList();
            if (!members.Any(f => string.Equals(f.Name, adminRole, StringComparison.OrdinalIgnoreCase))) continue;
            return new XRoleColumn
            {
                Column = pi.Col.Name,
                Members = members.Select(f => new XRoleMember { Name = f.Name, Value = Convert.ToInt64(f.ConstantValue) }).ToList(),
                Storage = pi.CustomConv ? null : pi.Conv ?? "int",
            };
        }
        return null;
    }

    static readonly System.Text.RegularExpressions.Regex PasswordLike = new("password|hash|pwd", System.Text.RegularExpressions.RegexOptions.IgnoreCase | System.Text.RegularExpressions.RegexOptions.CultureInvariant);

    void DetectLogin(XAuth a)
    {
        foreach (var rec in recOrder.Where(r => r.R.Method == "POST" && r.R.Auth == "Public" && r.Reach != null))
        {
            foreach (var v in rec.Reach!.Visits)
            {
                foreach (var bin in v.Body.DescendantNodesAndSelf().OfType<BinaryExpressionSyntax>().Where(b => b.IsKind(SyntaxKind.EqualsExpression) || b.IsKind(SyntaxKind.NotEqualsExpression)))
                {
                    var cr = ColumnRef(bin.Left, null, null) ?? ColumnRef(bin.Right, null, null);
                    if (cr is not { Nav: null } || !PasswordLike.IsMatch(cr.Value.Prop)) continue;
                    var other = ColumnRef(bin.Left, null, null) != null ? bin.Right : bin.Left;
                    a.UsersTable = cr.Value.Entity.Name; a.PasswordColumn = PropOf(Entities[cr.Value.Entity], cr.Value.Prop)!.Col.Name; a.LoginPath = rec.R.PathTemplate;
                    a.PasswordField = FieldOf(other, v, rec.R.Style, 0); a.UsernameField = UsernameFieldOf(rec, cr.Value.Entity, v);
                    a.LoginEncoding = rec.R.Params.Any(p => p.Source == "form") ? "form" : "json"; a.TokenField = TokenFieldOf(rec);
                    return;
                }
                foreach (var inv in v.Body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>().Where(i => VerifyNames.Contains(InvokedName(i.Expression) ?? "") || IsProjectCompare(i)))
                {
                    var args = inv.ArgumentList.Arguments.Select(x => x.Expression).Concat(inv.Expression is MemberAccessExpressionSyntax ma ? new[] { ma.Expression } : []).ToList();
                    foreach (var arg in args)
                        if (ColumnRef(arg, null, null) is { Nav: null } cr && System.Text.RegularExpressions.Regex.IsMatch(cr.Prop, "password|hash|pwd", System.Text.RegularExpressions.RegexOptions.IgnoreCase))
                        {
                            a.UsersTable = cr.Entity.Name;
                            a.PasswordColumn = PropOf(Entities[cr.Entity], cr.Prop)!.Col.Name;
                            a.LoginPath = rec.R.PathTemplate;
                            a.PasswordField = args.Where(x => x != arg).Select(x => FieldOf(x, v, rec.R.Style, 0)).FirstOrDefault(x => x != null);
                            a.UsernameField = UsernameFieldOf(rec, cr.Entity, v);
                            a.LoginEncoding = rec.R.Params.Any(p => p.Source == "form") ? "form" : "json";
                            a.TokenField = TokenFieldOf(rec);
                            return;
                        }
                }
            }
        }
    }

    /// <summary>A call of a project method with two or more arguments, one of them the stored password column: a project-written comparison (<c>SamePassword(user.Password, typed)</c>) is the verify call.</summary>
    bool IsProjectCompare(InvocationExpressionSyntax i) => i.ArgumentList.Arguments.Count >= 2 && Resolve(i).Project
        && i.ArgumentList.Arguments.Any(a => ColumnRef(a.Expression, null, null) is { Nav: null } cr && PasswordLike.IsMatch(cr.Prop));

    string? UsernameFieldOf(RouteRec rec, INamedTypeSymbol users, Visit at)
    {
        foreach (var v in rec.Reach!.Visits)
            foreach (var inv in v.Body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>().Where(i => PredicateOps.Contains(InvokedName(i.Expression) ?? "")))
            {
                if (inv.Expression is not MemberAccessExpressionSyntax ma || ChainOf(ma.Expression) is not { } ch || !SymbolEqualityComparer.Default.Equals(ch.T, users)) continue;
                var lam = inv.ArgumentList.Arguments.Select(x => x.Expression).OfType<LambdaExpressionSyntax>().FirstOrDefault();
                var pn = lam is SimpleLambdaExpressionSyntax sl ? sl.Parameter.Identifier.Text : (lam as ParenthesizedLambdaExpressionSyntax)?.ParameterList.Parameters.FirstOrDefault()?.Identifier.Text;
                var body = lam is SimpleLambdaExpressionSyntax s2 ? s2.Body : (lam as ParenthesizedLambdaExpressionSyntax)?.Body;
                if (pn == null || body is not ExpressionSyntax be) continue;
                foreach (var term in Conjuncts(be))
                    if (Unwrap(term) is BinaryExpressionSyntax b && b.IsKind(SyntaxKind.EqualsExpression))
                    {
                        var cr = ColumnRef(b.Left, pn, users); var other = b.Right;
                        if (cr == null) { cr = ColumnRef(b.Right, pn, users); other = b.Left; }
                        if (cr is { Nav: null } && FieldOf(other, v, rec.R.Style, 0) is { } f) return f;
                    }
            }
        return null;
    }

    string? TokenFieldOf(RouteRec rec)
    {
        var style = rec.R.Style;
        foreach (var v in rec.Reach!.Visits.Take(1))
            foreach (var inv in v.Body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>())
            {
                if (!FrameworkResultCall(inv, Resolve(inv))) continue;
                foreach (var arg in inv.ArgumentList.Arguments.Select(x => x.Expression))
                {
                    var inner = Unwrap(arg is AwaitExpressionSyntax aw ? aw.Expression : arg);
                    if (inner is AnonymousObjectCreationExpressionSyntax ao)
                    {
                        var m = ao.Initializers.Select(i => i.NameEquals?.Name.Identifier.Text ?? (i.Expression as MemberAccessExpressionSyntax)?.Name.Identifier.Text).FirstOrDefault(n => n != null && n.Contains("token", StringComparison.OrdinalIgnoreCase) && !n.Contains("refresh", StringComparison.OrdinalIgnoreCase));
                        if (m != null) return Js.Apply(namingByStyle.GetValueOrDefault(style, "CamelCase"), m);
                    }
                    var ty = Sm(arg).GetTypeInfo(arg is AwaitExpressionSyntax aw2 ? aw2.Expression : arg).Type;
                    if (ty is INamedTypeSymbol { IsGenericType: true, Name: "Task" or "ValueTask" } tt) ty = tt.TypeArguments[0];
                    if (ty is INamedTypeSymbol nt && IsProject(nt))
                    {
                        var cand = ModelProps(nt).Where(p => p.Name.Contains("token", StringComparison.OrdinalIgnoreCase) && !p.Name.Contains("refresh", StringComparison.OrdinalIgnoreCase)).OrderBy(p => p.Name.StartsWith("Access", StringComparison.Ordinal) ? 0 : 1).FirstOrDefault();
                        if (cand != null) return Wire(cand.Name, style, cand);
                    }
                }
            }
        return null;
    }

    /// <summary>The request field a value was read from: a body DTO property, or a constant key passed to a key accessor.</summary>
    string? FieldOf(ExpressionSyntax e, Visit v, string style, int depth)
    {
        if (depth > 6) return null;
        e = Unwrap(e);
        switch (e)
        {
            case AwaitExpressionSyntax aw: return FieldOf(aw.Expression, v, style, depth + 1);
            case BinaryExpressionSyntax { RawKind: (int)SyntaxKind.CoalesceExpression } b: return FieldOf(b.Left, v, style, depth + 1);
            case InvocationExpressionSyntax inv:
                {
                    var c = Resolve(inv);
                    if (c.Name is "Trim" or "ToLower" or "ToLowerInvariant" or "ToUpper" or "ToUpperInvariant" or "ToString" or "Normalize" && inv.Expression is MemberAccessExpressionSyntax rm) return FieldOf(rm.Expression, v, style, depth + 1);
                    if (c.Project && c.M != null && inv.ArgumentList.Arguments.Count > 0 && ConstOf(inv.ArgumentList.Arguments[0].Expression, v) is string key && IsKeyAccessor(c.M, 0)) return key;
                    return null;
                }
            case IdentifierNameSyntax id:
                return Sm(id).GetSymbolInfo(id).Symbol is ILocalSymbol ls && ls.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax() is VariableDeclaratorSyntax { Initializer.Value: { } init } ? FieldOf(init, v, style, depth + 1) : null;
            case MemberAccessExpressionSyntax ma when Sm(ma).GetSymbolInfo(ma.Name).Symbol is IPropertySymbol ps && IsProject(ps) && Sm(ma).GetSymbolInfo(ma.Expression).Symbol is IParameterSymbol or ILocalSymbol:
                return Wire(ps.Name, style, ps);
        }
        return null;
    }

    bool IsKeyAccessor(IMethodSymbol m, int depth)
    {
        if (depth > 2 || m.Parameters.Length == 0 || m.Parameters[0].Type.SpecialType != SpecialType.System_String || m.ContainingType == null || Entities.ContainsKey(m.ContainingType)) return false;
        var body = BodyOf(DeclOf(m));
        if (body == null) return false;
        var key = m.Parameters[0];
        foreach (var inv in body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>())
        {
            var n = InvokedName(inv.Expression);
            if (n is "TryGetPropertyValue" or "TryGetValue" or "GetProperty" or "TryGetProperty" or "ContainsKey" or "GetValueOrDefault" && inv.ArgumentList.Arguments.Any(a => a.Expression is IdentifierNameSyntax i && i.Identifier.Text == key.Name)) return true;
            var c = Resolve(inv);
            if (c.Project && c.M != null && inv.ArgumentList.Arguments.Any(a => a.Expression is IdentifierNameSyntax i && i.Identifier.Text == key.Name) && IsKeyAccessor(c.M, depth + 1)) return true;
        }
        return body.DescendantNodesAndSelf().OfType<ElementAccessExpressionSyntax>().Any(ea => ea.ArgumentList.Arguments.Any(a => a.Expression is IdentifierNameSyntax i && i.Identifier.Text == key.Name));
    }

    // ---- the document ------------------------------------------------------------------------------------------------------------

    void FinishDocument()
    {
        D.Main = Trees.Where(t => t.GetRoot().ChildNodes().OfType<GlobalStatementSyntax>().Any()).Select(t => t.FilePath).Distinct().OrderBy(x => x, StringComparer.Ordinal).ToList();
        if (D.Main.Count == 0) D.Main = MethodsByName.GetValueOrDefault("Main")?.Where(m => m.IsStatic && IsProject(m)).Select(m => FileOf(DeclOf(m)!)).Distinct().OrderBy(x => x, StringComparer.Ordinal).ToList() ?? new();
        if (Workflow) { D.ParamUses ??= new(); D.Creates ??= new(); D.ParamLiterals ??= new(); D.WorkflowUnread ??= new(); D.WorkflowNotes ??= new(); }
        foreach (var kv in Entities.Values) kv.Tab.Source = "efcore";
        // the caller's own account row is not ownership
        var usersEnt = D.Auth?.UsersTable;
        foreach (var rt in D.Routes) rt.OwnerUses.RemoveAll(o => o.Model == usersEnt && Entities.Keys.FirstOrDefault(k => k.Name == o.Model) is { } ue && PropOf(Entities[ue], Entities[ue].Props.FirstOrDefault(p => p.Col.Name == o.Column)?.P.Name ?? "")?.Col.PrimaryKey == true);
        D.Tables = D.Tables.Distinct().ToList();
        D.Models = D.Models.OrderBy(m => m.Name, StringComparer.Ordinal).ToList();
        D.StatusWrites = D.StatusWrites.OrderBy(w => w.File, StringComparer.Ordinal).ThenBy(w => w.Line).ThenBy(w => w.Value, StringComparer.Ordinal).ThenBy(w => w.Root, StringComparer.Ordinal).ToList();
        D.Unread = UnreadAll.GroupBy(u => $"{u.File}|{u.Line}|{u.What}|{u.Reason}|{u.Route}").Select(g => g.First()).OrderBy(u => u.File, StringComparer.Ordinal).ThenBy(u => u.Line).ThenBy(u => u.What, StringComparer.Ordinal).ThenBy(u => u.Route, StringComparer.Ordinal).ToList();
        D.Excluded = D.Excluded.OrderBy(e => e.File, StringComparer.Ordinal).ThenBy(e => e.Line).ThenBy(e => e.What, StringComparer.Ordinal).ToList();
        D.Notes = D.Notes.Distinct().OrderBy(n => n, StringComparer.Ordinal).ToList();
        var di = D.Dotnet.Di;
        di.Registrations = Regs.Where(r => !r.Keyed).Select(r => new DiReg { Interface = r.IfaceText, Implementation = r.ImplText, Lifetime = r.Lifetime, File = r.File, Line = r.Line }).OrderBy(r => r.File, StringComparer.Ordinal).ThenBy(r => r.Line).ToList();
        foreach (var g in di.Registrations.GroupBy(r => r.Interface)) (g.Count() == 1 ? di.Single : di.Multiple).Add(g.Key);
        di.ResolvedCalls = di.ResolvedCalls.GroupBy(c => $"{c.Route}|{c.Call}|{c.File}|{c.Line}").Select(g => g.First()).OrderBy(c => c.Route, StringComparer.Ordinal).ThenBy(c => c.File, StringComparer.Ordinal).ThenBy(c => c.Line).ToList();
        di.UnreadCalls = di.UnreadCalls.GroupBy(c => $"{c.Route}|{c.Call}|{c.File}|{c.Line}").Select(g => g.First()).OrderBy(c => c.Route, StringComparer.Ordinal).ThenBy(c => c.File, StringComparer.Ordinal).ThenBy(c => c.Line).ToList();
        D.Dotnet.QueryFilters = D.Dotnet.QueryFilters.Select(q => { q.IgnoredBy.Sort(StringComparer.Ordinal); q.AppliesToRoutes.Sort(StringComparer.Ordinal); return q; }).ToList();
        D.Dotnet.Validation = new ValidationInfo
        {
            AnnotationsEnforcedOn = (Recs.Where(r => r.Controller != null && ChainAttrs(r.Controller, "ApiController").Any() && !suppressModelState).Select(r => r.Controller!.Name).Distinct().OrderBy(x => x, StringComparer.Ordinal).Select(x => "controllers with [ApiController]: " + x)).ToList(),
            MinimalApiValidation = minimalValidation,
            Fluentvalidation = validators.Keys.Select(k => QualType(k)).OrderBy(x => x, StringComparer.Ordinal).ToList(),
        };
        DeriveProjectionPrivacy();
        if (!Policy) { foreach (var t in D.Tables) foreach (var c in t.Columns) c.Json = null; }
        D.Dotnet.JsonNamingByStyle = new SortedDictionary<string, string>(D.Dotnet.JsonNamingByStyle, StringComparer.Ordinal);
    }
}
