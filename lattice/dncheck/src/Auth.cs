// Auth evidence (N4): per route Public/Member/Admin from attributes, endpoint and group metadata, policies and the
// fallback policy; the project's middleware and authentication handlers with the refusals they answer; who the
// caller is (claims read, HttpContext.Items keys set by the authenticating component); the login route.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed class MwInfo
{
    public XMw X = new();
    public bool NonPublicOnly, RolesOnly;
    public SyntaxNode At = null!;
    public string Via = "";
    public List<Reach> Reaches = new();
    public INamedTypeSymbol? Type;
    public bool IsFilter;
    public HashSet<RouteRec> Scope = new();
    public Dictionary<XRaise, string> RaiseRole = new(ReferenceEqualityComparer.Instance);
}

public sealed partial class Ex
{
    readonly Dictionary<string, Meta> policies = new(StringComparer.Ordinal);
    Meta? fallbackMeta;
    bool controllerGlobalAuth, minimalValidation, suppressImplicit, suppressModelState, fluentAuto;
    string? scheme;
    string? adminRole;
    public readonly SortedSet<string> ClaimReads = new(StringComparer.Ordinal);
    public readonly List<MwInfo> Mws = new();
    readonly List<string> pipeline = new();

    static bool IsAdminName(string r) => r.Contains("admin", StringComparison.OrdinalIgnoreCase);
    static bool IsAuthHandler(INamedTypeSymbol t) { for (var b = t.BaseType; b != null; b = b.BaseType) if (b.Name == "AuthenticationHandler") return true; return false; }

    // ---- configuration -------------------------------------------------------------------------------------------------

    void ReadAuthConfig()
    {
        foreach (var inv in AllInvocations())
        {
            var name = InvokedName(inv.Expression);
            var args = inv.ArgumentList.Arguments;
            switch (name)
            {
                case "AddPolicy" when args.Count >= 2 && Sm(inv).GetConstantValue(args[0].Expression) is { HasValue: true, Value: string pn }:
                    {
                        var m = new Meta { Kind = "authorize", Where = Js.Text(inv), File = FileOf(inv), Line = LineOf(inv) };
                        ReadPolicyBuilder(args[1].Expression, m);
                        policies[pn] = m;
                        break;
                    }
                case "SetFallbackPolicy" or "SetDefaultPolicy" when name == "SetFallbackPolicy" && args.Count >= 1:
                    fallbackMeta = new Meta { Kind = "authorize", Where = Js.Text(inv), File = FileOf(inv), Line = LineOf(inv) };
                    ReadPolicyBuilder(args[0].Expression, fallbackMeta);
                    break;
                case "AddValidation": minimalValidation = true; break;
                case "AddFluentValidationAutoValidation": fluentAuto = true; break;
                case "AddAuthentication" when scheme == null && args.Count >= 1:
                    scheme = SchemeName(args[0].Expression, inv);
                    break;
                case "AddJwtBearer": scheme ??= args.Count > 0 && Sm(inv).GetConstantValue(args[0].Expression) is { HasValue: true, Value: string js } ? js : "Bearer"; break;
                case "AddCookie": scheme ??= "Cookies"; break;
                case "AddScheme" when args.Count > 0 && Sm(inv).GetConstantValue(args[0].Expression) is { HasValue: true, Value: string sn }: scheme ??= sn; break;
                case "Add" when inv.Expression is MemberAccessExpressionSyntax { Expression: MemberAccessExpressionSyntax { Name.Identifier.Text: "Filters" } }:
                    if (Js.Text(inv).Contains("AuthorizeFilter")) { controllerGlobalAuth = true; break; }
                    var ft = inv.Expression is MemberAccessExpressionSyntax { Name: GenericNameSyntax fg } ? Sm(inv).GetTypeInfo(fg.TypeArgumentList.Arguments[0]).Type as INamedTypeSymbol
                        : args.Count == 1 && args[0].Expression is TypeOfExpressionSyntax fto ? Sm(inv).GetTypeInfo(fto.Type).Type as INamedTypeSymbol
                        : args.Count == 1 && args[0].Expression is ObjectCreationExpressionSyntax foc ? Sm(inv).GetTypeInfo(foc).Type as INamedTypeSymbol : null;
                    if (ft != null && IsProject(ft)) mvcFilters.Add((ft, inv, "AddControllers(o => o.Filters.Add(" + ft.Name + "))"));
                    break;
                case "Add" when Js.Text(inv).Contains("RouteTokenTransformerConvention"): tokenTransformer = true; break;
            }
        }
        foreach (var asg in Trees.SelectMany(t => t.GetRoot().DescendantNodes().OfType<AssignmentExpressionSyntax>()))
        {
            var ln = asg.Left is MemberAccessExpressionSyntax lm ? lm.Name.Identifier.Text : (asg.Left as IdentifierNameSyntax)?.Identifier.Text;
            if (ln == "FallbackPolicy") { fallbackMeta = new Meta { Kind = "authorize", Where = Js.Text(asg), File = FileOf(asg), Line = LineOf(asg) }; ReadPolicyBuilder(asg.Right, fallbackMeta); }
            else if (ln == "SuppressImplicitRequiredAttributeForNonNullableReferenceTypes" && Sm(asg).GetConstantValue(asg.Right) is { Value: true }) suppressImplicit = true;
            else if (ln == "SuppressModelStateInvalidFilter" && Sm(asg).GetConstantValue(asg.Right) is { Value: true }) suppressModelState = true;
        }
        if (Trees.Any(t => t.GetText().ToString().Contains("IOutboundParameterTransformer"))) tokenTransformer = true;
        var roles = policies.Values.SelectMany(p => p.Roles).Concat(fallbackMeta?.Roles ?? new()).Concat(Trees.SelectMany(t => t.GetRoot().DescendantNodes().OfType<InvocationExpressionSyntax>().Where(i => InvokedName(i.Expression) == "RequireRole").SelectMany(i => i.ArgumentList.Arguments.Select(a => Sm(i).GetConstantValue(a.Expression).Value as string).Where(x => x != null).Cast<string>())));
        adminRole = roles.FirstOrDefault(IsAdminName);
    }

    string? SchemeName(ExpressionSyntax e, SyntaxNode at)
    {
        if (Sm(at).GetConstantValue(e) is { HasValue: true, Value: string s }) return s;
        var t = Js.Text(e);
        return t.EndsWith("JwtBearerDefaults.AuthenticationScheme") ? "Bearer" : t.EndsWith("CookieAuthenticationDefaults.AuthenticationScheme") ? "Cookies" : null;
    }

    // ---- who is the caller --------------------------------------------------------------------------------------------------

    IEnumerable<(INamedTypeSymbol T, MethodDeclarationSyntax M)> AuthComponentMethods()
    {
        foreach (var t in ProjTypes.Where(t => t.TypeKind == TypeKind.Class))
        {
            bool auth = IsAuthHandler(t), mw = t.GetMembers().OfType<IMethodSymbol>().Any(m => m.Name is "InvokeAsync" or "Invoke") && t.Constructors.Any(c => c.Parameters.Any(p => p.Type.Name == "RequestDelegate"));
            if (!auth && !mw) continue;
            foreach (var m in t.GetMembers().OfType<IMethodSymbol>().Where(m => m.MethodKind == MethodKind.Ordinary)) if (DeclOf(m) is MethodDeclarationSyntax md) yield return (t, md);
        }
    }

    void CollectCaller()
    {
        foreach (var (t, md) in AuthComponentMethods())
        {
            var body = BodyOf(md);
            if (body == null) continue;
            var r = ComputeReach(body, Sm(md).GetDeclaredSymbol(md), Qual(Sm(md).GetDeclaredSymbol(md)!), "");
            foreach (var v in r.Visits)
                foreach (var n in v.Body.DescendantNodesAndSelf())
                {
                    if (n is AssignmentExpressionSyntax { Left: ElementAccessExpressionSyntax ea } asg0 && Sm(ea).GetSymbolInfo(ea.Expression).Symbol is IPropertySymbol { Name: "Items" } && ea.ArgumentList.Arguments.Count == 1 && Sm(ea).GetConstantValue(ea.ArgumentList.Arguments[0].Expression) is { Value: string k } && Sm(asg0).GetTypeInfo(asg0.Right).Type is { } vt && !IsErr(vt) && IsProject(vt)) CallerKeys.Add(k);
                    if (n is ObjectCreationExpressionSyntax { Type: var ct } oc && ct.ToString().EndsWith("Claim") && oc.ArgumentList?.Arguments.Count > 0 && Sm(oc).GetConstantValue(oc.ArgumentList.Arguments[0].Expression) is { Value: string ck }) createdClaims.Add(ck);
                }
        }
        foreach (var tree in Trees)
            foreach (var n in tree.GetRoot().DescendantNodes())
            {
                if (n is InvocationExpressionSyntax inv && ClaimReaders.Contains(InvokedName(inv.Expression) ?? "") && inv.ArgumentList.Arguments.Count > 0 && Sm(inv).GetConstantValue(inv.ArgumentList.Arguments[0].Expression) is { Value: string claim })
                {
                    ClaimReads.Add(claim);
                    MarkCallerFunc(inv);
                }
                else if (n is ElementAccessExpressionSyntax ea && Sm(ea).GetSymbolInfo(ea.Expression).Symbol is IPropertySymbol { Name: "Items" } && ea.ArgumentList.Arguments.Count == 1 && Sm(ea).GetConstantValue(ea.ArgumentList.Arguments[0].Expression) is { Value: string k } && CallerKeys.Contains(k) && !(ea.Parent is AssignmentExpressionSyntax asg && asg.Left == ea))
                    MarkCallerFunc(ea);
            }
    }

    readonly HashSet<string> createdClaims = new(StringComparer.Ordinal);

    void MarkCallerFunc(SyntaxNode n)
    {
        var sm = Sm(n);
        for (var p = n.Parent; p != null; p = p.Parent)
        {
            ISymbol? s = p switch
            {
                MethodDeclarationSyntax md => sm.GetDeclaredSymbol(md),
                PropertyDeclarationSyntax pd => sm.GetDeclaredSymbol(pd),
                LocalFunctionStatementSyntax lf => sm.GetDeclaredSymbol(lf),
                AccessorDeclarationSyntax => null,
                _ => null,
            };
            if (s != null) { CallerFuncs.Add(s.OriginalDefinition); return; }
        }
    }

    // ---- per route ------------------------------------------------------------------------------------------------------

    Meta? AuthAttr(AttributeSyntax a)
    {
        var n = AttrName(a);
        if (n == "AllowAnonymous") return new Meta { Kind = "anonymous", Where = "[AllowAnonymous]", File = FileOf(a), Line = LineOf(a) };
        if (n != "Authorize") return null;
        var m = new Meta { Kind = "authorize", Where = "[" + Js.Text(a) + "]", File = FileOf(a), Line = LineOf(a) };
        foreach (var arg in a.ArgumentList?.Arguments ?? default)
        {
            var nm = arg.NameEquals?.Name.Identifier.Text;
            var val = Sm(a).GetConstantValue(arg.Expression);
            if (nm == "Roles") { if (val.Value is string rs) m.Roles.AddRange(rs.Split(',', StringSplitOptions.TrimEntries)); else m.Custom = "roles " + Js.Text(arg.Expression); }
            else if (nm == "Policy" || nm == null) { if (val.Value is string ps) m.Policies.Add(ps); else m.Custom = "policy " + Js.Text(arg.Expression); }
        }
        return m;
    }

    public List<Meta> AllMeta(RouteRec rec)
    {
        var all = new List<Meta>(rec.Meta);
        foreach (var g in rec.Group!.Chain()) all.AddRange(g.Meta);
        if (rec.Controller != null)
        {
            for (var b = rec.Controller; b != null && IsProject(b); b = b.BaseType) all.AddRange(AttrsOf(b).Select(AuthAttr).OfType<Meta>());
            all.AddRange(AttrsOf(rec.Action!).Select(AuthAttr).OfType<Meta>());
            if (controllerGlobalAuth) all.Add(new Meta { Kind = "authorize", Where = "AddControllers(o => o.Filters.Add(AuthorizeFilter))" });
        }
        else if (rec.Handler is ParenthesizedLambdaExpressionSyntax pl) all.AddRange(pl.AttributeLists.SelectMany(l => l.Attributes).Select(AuthAttr).OfType<Meta>());
        else if (rec.HandlerSym != null) all.AddRange(AttrsOf(rec.HandlerSym).Select(AuthAttr).OfType<Meta>());
        return all;
    }

    void EvalAuth(RouteRec rec)
    {
        var r = rec.R;
        var all = AllMeta(rec);
        var anon = all.FirstOrDefault(m => m.Kind == "anonymous");
        var auths = all.Where(m => m.Kind == "authorize").ToList();
        if (anon != null)
        {
            r.Auth = "Public";
            rec.ExplicitAnon = true;
            r.AuthReason = anon.Where + " wins over " + (auths.Count > 0 ? string.Join(", ", auths.Select(a => a.Where)) : "any authorization");
            r.Member = false;
            return;
        }
        if (auths.Count == 0 && fallbackMeta != null) auths.Add(fallbackMeta);
        if (auths.Count == 0) { r.Auth = "Public"; r.AuthReason = "no authorization evidence"; r.Member = false; return; }
        var roles = new List<string>();
        string? custom = null;
        foreach (var a in auths)
        {
            roles.AddRange(a.Roles);
            custom ??= a.Custom;
            foreach (var p in a.Policies)
            {
                if (policies.TryGetValue(p, out var pm)) { roles.AddRange(pm.Roles); custom ??= pm.Custom; }
                else custom ??= $"policy \"{p}\" is not registered in the project";
            }
        }
        r.Member = true;
        var admin = roles.FirstOrDefault(IsAdminName);
        if (admin != null) { r.Auth = "Admin"; r.AuthReason = $"{auths[0].Where}: role {admin} required"; }
        else
        {
            r.Auth = "Member";
            r.AuthReason = auths[0].Where + (roles.Count > 0 ? $" (roles {string.Join(", ", roles)}: not an admin role)" : "");
        }
        if (custom != null)
        {
            r.AuthReason += "; " + custom;
            AddUnread(r.Unread, rec.At, "authorization requirement", custom + ": not guessed as Admin", "auth", rec.Id);
        }
        rec.RoleGated = roles.Count > 0 || auths.Any(a => a.Policies.Count > 0);
    }

    // ---- middleware and authentication handlers ------------------------------------------------------------------------------------

    void CollectMiddleware()
    {
        int posRouting = -1, posAuthz = -1;
        foreach (var tree in Trees)
            foreach (var inv in tree.GetRoot().DescendantNodes().OfType<InvocationExpressionSyntax>())
            {
                var name = InvokedName(inv.Expression);
                if (name == null || inv.Expression is not MemberAccessExpressionSyntax ma) continue;
                if (name is "UseAuthentication" or "UseAuthorization" or "UseRouting" or "UseExceptionHandler" or "UseStatusCodePages" or "UseHttpsRedirection" or "UseCors" or "UseStaticFiles" or "UseHsts" or "UseDeveloperExceptionPage" or "UseCookiePolicy" or "UseResponseCompression" or "UseRateLimiter" or "UseForwardedHeaders")
                {
                    if (IsEndpointBuilder(Sm(inv).GetTypeInfo(ma.Expression).Type)) { pipeline.Add(name); if (name == "UseRouting") posRouting = pipeline.Count; if (name == "UseAuthorization") posAuthz = pipeline.Count; }
                    continue;
                }
                if (name == "UseMiddleware")
                {
                    INamedTypeSymbol? t = ma.Name is GenericNameSyntax g ? Sm(inv).GetTypeInfo(g.TypeArgumentList.Arguments[0]).Type as INamedTypeSymbol : inv.ArgumentList.Arguments.Select(a => a.Expression).OfType<TypeOfExpressionSyntax>().Select(x => Sm(inv).GetTypeInfo(x.Type).Type as INamedTypeSymbol).FirstOrDefault();
                    if (t != null && IsProject(t)) AddMw(t, inv, "app.UseMiddleware<" + t.Name + ">()");
                    else if (t != null) pipeline.Add("UseMiddleware<" + t.Name + ">");
                }
                else if (name == "Use" && inv.ArgumentList.Arguments.LastOrDefault()?.Expression is LambdaExpressionSyntax lam && IsEndpointBuilder(Sm(inv).GetTypeInfo(ma.Expression).Type))
                    AddMw(null, inv, "app.Use(lambda)", lam);
            }
        if (posRouting > 0 && posAuthz > 0 && posAuthz < posRouting) D.Notes.Add("UseAuthorization is called before UseRouting: authorization metadata is not seen by the middleware");
        foreach (var t in ProjTypes.Where(t => t.TypeKind == TypeKind.Class && IsAuthHandler(t)))
            if (!Mws.Any(m => SymbolEqualityComparer.Default.Equals(m.Type, t))) AddMw(t, DeclOf(t)!, "AddScheme<" + t.Name + ">", null, true, "auth_handler");
    }

    MwInfo AddMw(INamedTypeSymbol? t, SyntaxNode at, string via, LambdaExpressionSyntax? lam = null, bool handler = false, string? kind = null)
    {
        var mw = new MwInfo { At = at, Via = via, Type = t, IsFilter = kind != null && kind != "auth_handler" };
        var x = mw.X;
        var roots = new List<(SyntaxNode Body, IMethodSymbol? Sym, string In, string Role)>();
        if (t != null)
        {
            x.Func = QualType(t); x.File = FileOf(DeclOf(t)!); x.Line = LineOf(((BaseTypeDeclarationSyntax)DeclOf(t)!).Identifier);
            foreach (var m in t.GetMembers().OfType<IMethodSymbol>().Where(m => m.MethodKind == MethodKind.Ordinary && DeclOf(m) is MethodDeclarationSyntax))
                if (m.Name is "InvokeAsync" or "Invoke" or "HandleAuthenticateAsync" or "HandleChallengeAsync" or "HandleForbiddenAsync" or "OnActionExecuting" or "OnActionExecutionAsync" or "OnActionExecuted" or "OnAuthorization" or "OnAuthorizationAsync" or "OnResourceExecuting" or "OnResourceExecutionAsync" or "OnResultExecuting" or "OnResultExecutionAsync" or "OnException" && BodyOf(DeclOf(m)) is { } b) roots.Add((b, m, Qual(m), m.Name));
            x.Kind = kind ?? (handler ? "auth_handler" : "middleware");
        }
        else
        {
            var enc = Sm(lam!).GetEnclosingSymbol(lam!.SpanStart)?.ContainingType;
            x.Func = $"{(enc != null ? QualType(enc) : "Program")}.<lambda@{FileOf(lam)}:{LineOf(lam)}>"; x.File = FileOf(lam); x.Line = LineOf(lam);
            roots.Add((BodyOf(lam)!, null, x.Func, "Invoke"));
        }
        foreach (var (body, sym, inName, role) in roots)
        {
            var r = ComputeReach(body, sym, inName, "");
            mw.Reaches.Add(r);
            var raises = new List<XRaise>();
            ScanRaises(r, "", raises, null, x.Unread, "middleware");
            if (role == "HandleChallengeAsync") foreach (var rr in raises) rr.Kind = "auth";
            if (role == "HandleForbiddenAsync") mw.RolesOnly = true;
            foreach (var rr in raises) { rr.Kind = "middleware"; x.Raises.Add(rr); mw.RaiseRole[rr] = role; }
            foreach (var v in r.Visits)
            {
                var text = v.Body.ToString();
                if (text.Contains("IAuthorizeData") || text.Contains("AllowAnonymous")) mw.NonPublicOnly = true;
                if (text.Contains("Authorization") || text.Contains("IsAuthenticated")) x.Authenticating |= x.Raises.Any(q => q.Status is 401 or 403) || handler;
                if (text.Contains("Bearer")) x.Bearer = true;
                foreach (var n in v.Body.DescendantNodesAndSelf())
                    if (n is MemberAccessExpressionSyntax { Name: var nm } && Sm(n).GetSymbolInfo(nm).Symbol is IPropertySymbol { Name: "Items" }) { }
                foreach (var d in v.Body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>())
                    if (ChainOf(d) is { T: { } ct }) x.UsersTable ??= UsersCandidate(ct);
            }
        }
        if (handler) x.Authenticating = true;
        x.RecordsCaller = CallerKeys.OrderBy(k => k, StringComparer.Ordinal).Concat(createdClaims.OrderBy(k => k, StringComparer.Ordinal)).Distinct().ToList();
        x.MentionsAdmin = adminRole != null && roots.Any(r => r.Body.ToString().Contains(adminRole));
        x.Raises = x.Raises.OrderBy(q => q.File, StringComparer.Ordinal).ThenBy(q => q.Line).ToList();
        Mws.Add(mw);
        D.Middleware.Add(x);
        return mw;
    }

    string? UsersCandidate(INamedTypeSymbol t) => t.Name.Contains("User", StringComparison.OrdinalIgnoreCase) || t.Name.Contains("Account", StringComparison.OrdinalIgnoreCase) || t.Name.Contains("Member", StringComparison.OrdinalIgnoreCase) ? t.Name : null;

    /// <summary>A route with no authorization metadata whose own code reads the Authorization header and refuses with 401/403 authenticates by hand: Member (extra evidence beyond N4's metadata).</summary>
    void HandAuthenticated(RouteRec rec)
    {
        var r = rec.R;
        if (r.Auth != "Public" || rec.ExplicitAnon || rec.Reach == null) return;
        var reads = rec.Reach.Visits.SelectMany(v => v.Body.DescendantNodesAndSelf()).Any(n =>
            n is MemberAccessExpressionSyntax { Name.Identifier.Text: "Authorization" } || n is ElementAccessExpressionSyntax ea && ea.ArgumentList.Arguments.Count == 1 && Sm(ea).GetConstantValue(ea.ArgumentList.Arguments[0].Expression) is { Value: string hs } && hs.Equals("Authorization", StringComparison.OrdinalIgnoreCase));
        if (!reads || !r.Raises.Any(x => x.Kind != "middleware" && x.Status is 401 or 403)) return;
        r.Auth = "Member"; r.Member = true; r.AuthReason = "the handler reads the Authorization header and refuses with 401 (no authorization metadata)";
    }

    /// <summary>Endpoint filters (named on the endpoint or a group) and MVC filters (global or attribute), each scoped to the routes it covers.</summary>
    void CollectFilters()
    {
        var byKey = new Dictionary<string, MwInfo>(StringComparer.Ordinal);
        MwInfo? Get(string key, INamedTypeSymbol? t, SyntaxNode at, string via, LambdaExpressionSyntax? lam, string kind)
        {
            if (byKey.TryGetValue(key, out var have)) return have;
            if (t == null && lam == null) return null;
            return byKey[key] = AddMw(t, at, via, lam, false, kind);
        }
        foreach (var rec in recOrder.Where(r => !r.Framework))
        {
            foreach (var m in AllMeta(rec).Where(m => m.Kind == "filter" && m.Node is InvocationExpressionSyntax))
            {
                var inv = (InvocationExpressionSyntax)m.Node!;
                INamedTypeSymbol? t = inv.Expression is MemberAccessExpressionSyntax { Name: GenericNameSyntax g } ? Sm(inv).GetTypeInfo(g.TypeArgumentList.Arguments[0]).Type as INamedTypeSymbol : null;
                var lam = inv.ArgumentList.Arguments.Select(x => x.Expression).OfType<LambdaExpressionSyntax>().FirstOrDefault();
                if (t != null && !IsProject(t)) t = null;
                var mw = Get($"{FileOf(inv)}:{inv.SpanStart}", t, inv, Js.Text(inv), t == null ? lam : null, "endpoint_filter");
                if (mw != null) mw.Scope.Add(rec);
                else AddUnread(rec.R.Unread, inv, Js.Text(inv), "endpoint filter is not a project class or a lambda", "auth", rec.Id);
            }
            if (rec.Controller == null) continue;
            foreach (var (t, at, via) in mvcFilters.Concat(AttributeFilters(rec)))
                Get($"mvc:{QualType(t)}", t, at, via, null, "mvc_filter")?.Scope.Add(rec);
        }
    }

    readonly List<(INamedTypeSymbol T, SyntaxNode At, string Via)> mvcFilters = new();
    static readonly HashSet<string> FilterInterfaces = new(StringComparer.Ordinal) { "IActionFilter", "IAsyncActionFilter", "IAuthorizationFilter", "IAsyncAuthorizationFilter", "IResourceFilter", "IAsyncResourceFilter", "IResultFilter", "IAsyncResultFilter", "IExceptionFilter", "IAsyncExceptionFilter" };

    IEnumerable<(INamedTypeSymbol, SyntaxNode, string)> AttributeFilters(RouteRec rec)
    {
        var attrs = new List<AttributeSyntax>();
        for (var b = rec.Controller; b != null && IsProject(b); b = b.BaseType) attrs.AddRange(AttrsOf(b));
        attrs.AddRange(AttrsOf(rec.Action!));
        foreach (var a in attrs)
        {
            INamedTypeSymbol? t = null;
            var n = AttrName(a);
            if (n is "ServiceFilter" or "TypeFilter" && a.ArgumentList?.Arguments.FirstOrDefault()?.Expression is TypeOfExpressionSyntax toe) t = Sm(a).GetTypeInfo(toe.Type).Type as INamedTypeSymbol;
            else if (Sm(a).GetSymbolInfo(a).Symbol?.ContainingType is { } at && IsProject(at) && (at.AllInterfaces.Any(i => FilterInterfaces.Contains(i.Name)) || at.BaseType?.Name == "ActionFilterAttribute")) t = at;
            if (t != null && IsProject(t)) yield return (t, a, "[" + Js.Text(a) + "]");
        }
    }

    /// <summary>The middleware that run before a route's handler, with the refusals each answers there.</summary>
    void ApplyMiddleware(RouteRec rec)
    {
        foreach (var mw in Mws)
        {
            if (mw.X.Kind == "auth_handler" && rec.R.Auth == "Public") continue;
            if (mw.IsFilter && !mw.Scope.Contains(rec)) continue;
            rec.R.Middleware.Add(new XMwRef { Func = mw.X.Func, File = mw.X.File, Line = mw.X.Line, Wrappers = new(), Via = mw.Via, Resolved = true });
            foreach (var q in mw.X.Raises)
            {
                var role = mw.RaiseRole.TryGetValue(q, out var rl) ? rl : "";
                if (mw.NonPublicOnly && rec.R.Auth == "Public") continue;
                if (role == "HandleForbiddenAsync" && !rec.RoleGated) continue;
                if (role == "HandleChallengeAsync" && rec.R.Auth == "Public") continue;
                rec.R.Raises.Add(new XRaise { Status = q.Status, Code = q.Code, Detail = q.Detail, Line = q.Line, File = q.File, Kind = "middleware", In = q.In });
            }
        }
    }
}
