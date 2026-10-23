// Raise sites (N9): the refusals a request can end in -- result factories (NotFound(), Results.Conflict(), StatusCode(n),
// Problem(statusCode:)), throws mapped to a constant status (IExceptionHandler switches, catch blocks, status-carrying
// exception types and the factories that build them), project error writers handed a status constant -- plus the
// success statuses of the handler. A thrown type with no constant mapping is unread, never guessed.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed class ErrInfo
{
    public INamedTypeSymbol? Type;
    public int? Status;
    public string? Code, Detail;
}

public sealed partial class Ex
{
    /// <summary>Exception type -> (status, how, where) as an IExceptionHandler, a catch block or UseExceptionHandler maps it.</summary>
    public readonly Dictionary<INamedTypeSymbol, (int Status, string Via, string File, int Line)> ExceptionMap = new(SymbolEqualityComparer.Default);
    readonly HashSet<INamedTypeSymbol> handledTypes = new(SymbolEqualityComparer.Default);
    /// <summary>Exception types a handler (IExceptionHandler, UseExceptionHandler lambda, middleware catch) answers with the status the exception itself carries (<c>api.Status</c>).</summary>
    readonly HashSet<INamedTypeSymbol> carriedTypes = new(SymbolEqualityComparer.Default);

    static readonly Dictionary<string, int> FactoryStatus = new(StringComparer.Ordinal)
    {
        ["NotFound"] = 404,
        ["BadRequest"] = 400,
        ["Conflict"] = 409,
        ["Unauthorized"] = 401,
        ["Forbid"] = 403,
        ["UnprocessableEntity"] = 422,
        ["ValidationProblem"] = 400,
        ["NotFoundResult"] = 404,
        ["NotFoundObjectResult"] = 404,
        ["BadRequestResult"] = 400,
        ["BadRequestObjectResult"] = 400,
        ["ConflictResult"] = 409,
        ["ConflictObjectResult"] = 409,
        ["UnauthorizedResult"] = 401,
        ["UnauthorizedObjectResult"] = 401,
        ["UnprocessableEntityResult"] = 422,
        ["UnprocessableEntityObjectResult"] = 422,
        ["ForbidResult"] = 403,
    };
    static readonly Dictionary<string, int> SuccessStatus = new(StringComparer.Ordinal)
    {
        ["Ok"] = 200,
        ["Json"] = 200,
        ["Text"] = 200,
        ["Content"] = 200,
        ["File"] = 200,
        ["Bytes"] = 200,
        ["Stream"] = 200,
        ["PhysicalFile"] = 200,
        ["VirtualFile"] = 200,
        ["NoContent"] = 204,
        ["Created"] = 201,
        ["CreatedAtAction"] = 201,
        ["CreatedAtRoute"] = 201,
        ["Accepted"] = 202,
        ["AcceptedAtAction"] = 202,
        ["AcceptedAtRoute"] = 202,
        ["Redirect"] = 302,
        ["LocalRedirect"] = 302,
        ["RedirectToAction"] = 302,
        ["RedirectToRoute"] = 302,
        ["RedirectPermanent"] = 301,
        ["OkObjectResult"] = 200,
        ["OkResult"] = 200,
        ["NoContentResult"] = 204,
        ["CreatedResult"] = 201,
        ["JsonResult"] = 200,
        ["ContentResult"] = 200,
    };
    static bool IsStatusParam(string n) => n.Equals("status", StringComparison.OrdinalIgnoreCase) || n.Equals("statusCode", StringComparison.OrdinalIgnoreCase) || n.Equals("httpStatus", StringComparison.OrdinalIgnoreCase) || n.Equals("httpStatusCode", StringComparison.OrdinalIgnoreCase);
    static bool IsCodeParam(string n) => n.Equals("code", StringComparison.OrdinalIgnoreCase) || n.Equals("errorCode", StringComparison.OrdinalIgnoreCase) || n.Equals("error", StringComparison.OrdinalIgnoreCase);
    static bool IsMessageParam(string n) => n.Equals("message", StringComparison.OrdinalIgnoreCase) || n.Equals("detail", StringComparison.OrdinalIgnoreCase) || n.Equals("title", StringComparison.OrdinalIgnoreCase);

    public object? ConstOf(ExpressionSyntax? e, Visit? v)
    {
        if (e == null) return null;
        var cv = Sm(e).GetConstantValue(e);
        if (cv.HasValue) return cv.Value;
        if (e is ParenthesizedExpressionSyntax p) return ConstOf(p.Expression, v);
        if (e is IdentifierNameSyntax id && Sm(id).GetSymbolInfo(id).Symbol is IParameterSymbol ps && v != null && v.Binds.TryGetValue(ps, out var a) && a.HasConst) return a.Const;
        return null;
    }

    // ---- exception mapping ---------------------------------------------------------------------------------------------------

    static bool HasStatusCtor(INamedTypeSymbol t) => t.Constructors.Any(c => c.Parameters.Any(p => IsStatusParam(p.Name) && p.Type.SpecialType == SpecialType.System_Int32));

    void BuildExceptionMap()
    {
        foreach (var t in ProjTypes.Where(t => t.TypeKind == TypeKind.Class))
        {
            bool isHandler = t.AllInterfaces.Any(i => i.Name == "IExceptionHandler");
            bool isMiddleware = t.GetMembers("InvokeAsync").Concat(t.GetMembers("Invoke")).Any();
            if (!isHandler && !isMiddleware) continue;
            foreach (var d in t.DeclaringSyntaxReferences.Select(r => r.GetSyntax()))
                ReadMappings(d, isHandler ? "IExceptionHandler " + t.Name : "middleware " + t.Name);
        }
        foreach (var inv in AllInvocations().Where(i => InvokedName(i.Expression) == "UseExceptionHandler"))
            foreach (var a in inv.ArgumentList.Arguments.Where(a => a.Expression is LambdaExpressionSyntax)) ReadMappings(a.Expression, "UseExceptionHandler");
        foreach (var inv in AllInvocations().Where(i => InvokedName(i.Expression) == "Use"))
            foreach (var a in inv.ArgumentList.Arguments.Where(a => a.Expression is LambdaExpressionSyntax && a.Expression.DescendantNodes().OfType<TryStatementSyntax>().Any())) DetectCarried(a.Expression);
    }

    void ReadMappings(SyntaxNode scope, string via)
    {
        // switch expression arms:  T => <constant status>
        foreach (var sw in scope.DescendantNodes().OfType<SwitchExpressionSyntax>())
            foreach (var arm in sw.Arms)
                if (arm.Pattern is DeclarationPatternSyntax or TypePatternSyntax or ConstantPatternSyntax or RecursivePatternSyntax)
                {
                    var tsyn = arm.Pattern is DeclarationPatternSyntax dp ? dp.Type : arm.Pattern is TypePatternSyntax tp ? tp.Type : arm.Pattern is ConstantPatternSyntax { Expression: var cx } ? cx as TypeSyntax : null;
                    if (tsyn == null || Sm(arm).GetTypeInfo(tsyn).Type is not INamedTypeSymbol et || !IsProject(et)) continue;
                    if (Sm(arm).GetConstantValue(arm.Expression) is { HasValue: true, Value: int st }) AddMap(et, st, via, arm);
                }
        // if (exception is T) { ... StatusCode = n }   and   catch (T) { ... StatusCode = n }
        foreach (var isx in scope.DescendantNodes().OfType<IsPatternExpressionSyntax>())
            if (isx.Pattern is DeclarationPatternSyntax or TypePatternSyntax && isx.Parent is IfStatementSyntax or BinaryExpressionSyntax or ConditionalExpressionSyntax)
            {
                var tsyn = isx.Pattern is DeclarationPatternSyntax dp ? dp.Type : ((TypePatternSyntax)isx.Pattern).Type;
                if (Sm(isx).GetTypeInfo(tsyn).Type is not INamedTypeSymbol et || !IsProject(et)) continue;
                var ifs = isx.AncestorsAndSelf().OfType<IfStatementSyntax>().FirstOrDefault();
                if (ifs != null && StatusIn(ifs.Statement) is { } st) AddMap(et, st, via, isx);
            }
        foreach (var cc in scope.DescendantNodes().OfType<CatchClauseSyntax>())
        {
            if (cc.Declaration == null || Sm(cc).GetTypeInfo(cc.Declaration.Type).Type is not INamedTypeSymbol et || !IsProject(et)) continue;
            handledTypes.Add(et);
            if (StatusIn(cc.Block) is { } st) AddMap(et, st, via, cc);
        }
        DetectCarried(scope);
    }

    static bool IsStatusMember(ISymbol? s) => s switch
    {
        IPropertySymbol p => IsStatusParam(p.Name) && IsStatusValueType(p.Type),
        IFieldSymbol f => IsStatusParam(f.Name) && IsStatusValueType(f.Type),
        _ => false,
    };

    static bool IsStatusValueType(ITypeSymbol t) => t.SpecialType == SpecialType.System_Int32 || t.TypeKind == TypeKind.Enum || t is INamedTypeSymbol { OriginalDefinition.SpecialType: SpecialType.System_Nullable_T } n && IsStatusValueType(n.TypeArguments[0]);

    /// <summary>Whether the scope puts a status on the response: <c>X.StatusCode = ..</c>, <c>Status = ..</c> in a ProblemDetails initializer, a <c>statusCode:</c> argument or a Problem/StatusCode result.</summary>
    static bool WritesStatus(SyntaxNode scope)
    {
        foreach (var asg in scope.DescendantNodes().OfType<AssignmentExpressionSyntax>())
            if ((asg.Left as MemberAccessExpressionSyntax)?.Name.Identifier.Text is { } mn && IsStatusParam(mn) || asg.Left is IdentifierNameSyntax idn && IsStatusParam(idn.Identifier.Text)) return true;
        foreach (var inv in scope.DescendantNodes().OfType<InvocationExpressionSyntax>())
        {
            if (inv.ArgumentList.Arguments.Any(a => a.NameColon != null && IsStatusParam(a.NameColon.Name.Identifier.Text))) return true;
            if (InvokedName(inv.Expression) is "Problem" or "StatusCode") return true;
        }
        return false;
    }

    /// <summary>A handler that reads the status member of the exception it caught (<c>error is ApiException api</c> ... <c>api.Status</c>): that type's constructor chain decides the status of a throw.</summary>
    void DetectCarried(SyntaxNode scope)
    {
        if (!WritesStatus(scope)) return;
        var vars = new List<(ILocalSymbol Local, INamedTypeSymbol Type)>();
        foreach (var dp in scope.DescendantNodes().OfType<DeclarationPatternSyntax>())
            if (dp.Designation is SingleVariableDesignationSyntax sv && Sm(dp).GetDeclaredSymbol(sv) is ILocalSymbol { Type: INamedTypeSymbol t } ls && IsExceptionType(t) && IsProject(t)) vars.Add((ls, t));
        foreach (var cc in scope.DescendantNodes().OfType<CatchClauseSyntax>())
            if (cc.Declaration is { Identifier.Text.Length: > 0 } d && Sm(cc).GetDeclaredSymbol(d) is ILocalSymbol { Type: INamedTypeSymbol t } ls && IsExceptionType(t) && IsProject(t)) vars.Add((ls, t));
        foreach (var (local, type) in vars)
            foreach (var ma in scope.DescendantNodes().OfType<MemberAccessExpressionSyntax>())
                if (ma.Expression is IdentifierNameSyntax id && id.Identifier.Text == local.Name && SymbolEqualityComparer.Default.Equals(Sm(id).GetSymbolInfo(id).Symbol, local) && IsStatusMember(Sm(ma).GetSymbolInfo(ma).Symbol))
                {
                    carriedTypes.Add(type);
                    handledTypes.Add(type);
                }
    }

    bool Carried(INamedTypeSymbol t) { for (var b = t; b != null; b = b.BaseType) if (carriedTypes.Contains(b)) return true; return false; }

    /// <summary>The constant status a constructor hands to the base constructor chain (<c>: base(404, ..)</c>, or the primary-constructor base call <c>class E(string d) : Api(404, "x", d)</c>); <paramref name="bound"/> holds the constants the caller passed for this constructor's parameters.</summary>
    int? ChainStatus(IMethodSymbol ctor, Dictionary<IParameterSymbol, object?> bound, int depth = 0)
    {
        if (depth > 4 || !IsProject(ctor)) return null;
        ArgumentListSyntax? args = null;
        SyntaxNode? callNode = null;
        switch (DeclOf(ctor))
        {
            case ConstructorDeclarationSyntax { Initializer: { } init } when init.ThisOrBaseKeyword.IsKind(SyntaxKind.BaseKeyword) || init.ThisOrBaseKeyword.IsKind(SyntaxKind.ThisKeyword): args = init.ArgumentList; callNode = init; break;
            case TypeDeclarationSyntax { BaseList: { } bl } when bl.Types.OfType<PrimaryConstructorBaseTypeSyntax>().FirstOrDefault() is { } pb: args = pb.ArgumentList; callNode = pb; break;
        }
        if (args == null || callNode == null || Sm(callNode).GetSymbolInfo(callNode).Symbol is not IMethodSymbol next) return null;
        var nb = new Dictionary<IParameterSymbol, object?>(SymbolEqualityComparer.Default);
        int i = 0;
        foreach (var a in args.Arguments)
        {
            var p = a.NameColon != null ? next.Parameters.FirstOrDefault(x => x.Name == a.NameColon.Name.Identifier.Text) : (i < next.Parameters.Length ? next.Parameters[i] : null);
            i++;
            if (p == null) continue;
            object? cv = null;
            var k = Sm(a.Expression).GetConstantValue(a.Expression);
            if (k.HasValue) cv = k.Value;
            else if (a.Expression is IdentifierNameSyntax idn && Sm(idn).GetSymbolInfo(idn).Symbol is IParameterSymbol ps && bound.TryGetValue(ps, out var b)) cv = b;
            if (cv == null) continue;
            nb[p.OriginalDefinition] = cv;
            if (IsStatusParam(p.Name) && IsStatusValueType(p.Type) && cv is int st) return st;
        }
        return ChainStatus(next.OriginalDefinition, nb, depth + 1);
    }

    int? StatusIn(SyntaxNode n)
    {
        foreach (var asg in n.DescendantNodes().OfType<AssignmentExpressionSyntax>())
            if (asg.Left is MemberAccessExpressionSyntax { Name.Identifier.Text: "StatusCode" } && Sm(asg).GetConstantValue(asg.Right) is { HasValue: true, Value: int s }) return s;
        foreach (var inv in n.DescendantNodes().OfType<InvocationExpressionSyntax>())
        {
            var c = Resolve(inv);
            if (c.M != null && FactoryStatus.TryGetValue(c.Name, out var fs) && !c.Project) return fs;
            if (c.M != null && c.Project) foreach (var (p, e) in MapArgs(inv, c)) if (IsStatusParam(p.Name) && Sm(e).GetConstantValue(e) is { HasValue: true, Value: int s2 }) return s2;
        }
        return null;
    }

    void AddMap(INamedTypeSymbol t, int status, string via, SyntaxNode at)
    {
        handledTypes.Add(t);
        if (!ExceptionMap.ContainsKey(t)) ExceptionMap[t] = (status, via, FileOf(at), LineOf(at));
    }

    bool Handled(INamedTypeSymbol t) { for (var b = t; b != null; b = b.BaseType) if (handledTypes.Contains(b) || ExceptionMap.ContainsKey(b)) return true; return false; }

    // ---- error values (throw sites, factories) -------------------------------------------------------------------------------

    public ErrInfo? ErrorOf(ExpressionSyntax? e, Visit? v, int depth = 0)
    {
        if (e == null || depth > 3) return null;
        switch (e)
        {
            case ParenthesizedExpressionSyntax p: return ErrorOf(p.Expression, v, depth);
            case ObjectCreationExpressionSyntax or ImplicitObjectCreationExpressionSyntax:
                {
                    var t = Sm(e).GetTypeInfo(e).Type as INamedTypeSymbol;
                    if (t == null) return null;
                    var info = new ErrInfo { Type = t };
                    var c = ResolveNew(e);
                    var args = e switch { ObjectCreationExpressionSyntax o => o.ArgumentList?.Arguments, ImplicitObjectCreationExpressionSyntax i => i.ArgumentList.Arguments, _ => null };
                    var passed = new Dictionary<IParameterSymbol, object?>(SymbolEqualityComparer.Default);
                    if (c.M != null && args != null)
                    {
                        int i = 0;
                        foreach (var a in args.Value)
                        {
                            var p = a.NameColon != null ? c.M.Parameters.FirstOrDefault(x => x.Name == a.NameColon.Name.Identifier.Text) : (i < c.M.Parameters.Length ? c.M.Parameters[i] : null);
                            i++;
                            if (p == null) continue;
                            var cv = ConstOf(a.Expression, v);
                            if (cv != null) passed[p.OriginalDefinition] = cv;
                            if (IsStatusParam(p.Name) && cv is int s) info.Status = s;
                            else if (IsCodeParam(p.Name) && cv is string cs) info.Code = cs;
                            else if (IsMessageParam(p.Name) && cv is string ms) info.Detail = ms;
                        }
                    }
                    if (info.Status == null && ExceptionMap.TryGetValue(t, out var m)) info.Status = m.Status;
                    else if (info.Status == null) for (var b = t.BaseType; b != null && IsProject(b); b = b.BaseType) if (ExceptionMap.TryGetValue(b, out var bm)) { info.Status = bm.Status; break; }
                    // the status the exception itself carries, when a handler answers with it
                    if (info.Status == null && c.M != null && Carried(t)) info.Status = ChainStatus(c.M, passed);
                    return info;
                }
            case InvocationExpressionSyntax inv:
                {
                    var c = Resolve(inv);
                    if (!c.Project || c.M == null) return null;
                    var body = BodyOf(DeclOf(c.M));
                    if (body == null) return null;
                    var nv = new Visit { Body = body, Method = c.M, Depth = (v?.Depth ?? 0) + 1 };
                    foreach (var (p, ex) in MapArgs(inv, c))
                    {
                        var tp = c.M.Parameters.FirstOrDefault(x => x.Name == p.Name) ?? p;
                        var cv = ConstOf(ex, v);
                        nv.Binds[tp] = new Arg { Text = Js.Text(ex), Const = cv, HasConst = cv != null };
                    }
                    foreach (var r in Returns(body)) { var ei = ErrorOf(r, nv, depth + 1); if (ei != null) return ei; }
                    return null;
                }
        }
        return null;
    }

    // ---- scanning a reach ------------------------------------------------------------------------------------------------------

    (string? Code, string? Detail) ArgsText(InvocationExpressionSyntax inv, Call c, Visit v)
    {
        string? code = null, detail = null;
        foreach (var a in inv.ArgumentList.Arguments)
        {
            var nm = a.NameColon?.Name.Identifier.Text;
            if (nm is "detail" or "title" or "message" && ConstOf(a.Expression, v) is string d) detail ??= d;
            else if (nm is "code" or "errorCode" && ConstOf(a.Expression, v) is string cc) code ??= cc;
            else if (a.Expression is AnonymousObjectCreationExpressionSyntax ao)
                foreach (var m in ao.Initializers)
                {
                    var mn = m.NameEquals?.Name.Identifier.Text ?? (m.Expression as MemberAccessExpressionSyntax)?.Name.Identifier.Text ?? "";
                    var val = ConstOf(m.Expression, v) as string;
                    if (val == null) continue;
                    if (mn.Equals("code", StringComparison.OrdinalIgnoreCase) || mn.Equals("error", StringComparison.OrdinalIgnoreCase) || mn.Equals("error_code", StringComparison.OrdinalIgnoreCase) || mn.Equals("errorCode", StringComparison.OrdinalIgnoreCase)) code ??= val;
                    else if (mn.Equals("message", StringComparison.OrdinalIgnoreCase) || mn.Equals("detail", StringComparison.OrdinalIgnoreCase) || mn.Equals("title", StringComparison.OrdinalIgnoreCase) || mn.Equals("description", StringComparison.OrdinalIgnoreCase)) detail ??= val;
                }
            else if (nm == null && ConstOf(a.Expression, v) is string plain && c.M != null && !c.Project && inv.ArgumentList.Arguments.Count == 1 && c.Name is "NotFound" or "BadRequest" or "Conflict" or "Unauthorized" or "UnprocessableEntity") detail ??= plain;
        }
        return (code, detail);
    }

    bool FrameworkResultCall(InvocationExpressionSyntax inv, Call c)
    {
        if (c.Project) return false;
        if (c.M != null) return c.M.ContainingType.Name is "ControllerBase" or "Controller" or "Results" or "TypedResults" or "ResultsExtensions" or "HttpResults";
        // unbound (an argument of an unknown type): by the receiver's spelling
        return inv.Expression is MemberAccessExpressionSyntax { Expression: IdentifierNameSyntax { Identifier.Text: "Results" or "TypedResults" } } || inv.Expression is IdentifierNameSyntax or GenericNameSyntax && InControllerClass(inv);
    }

    bool InControllerClass(SyntaxNode n)
    {
        var t = Sm(n).GetEnclosingSymbol(n.SpanStart)?.ContainingType;
        for (var b = t; b != null; b = b.BaseType) if (b.Name is "ControllerBase" or "Controller") return true;
        return false;
    }

    /// <summary>Refusals (status >= 400) and the success statuses found in a reach.</summary>
    public void ScanRaises(Reach r, string route, List<XRaise> raises, HashSet<int>? success, List<Unread> unread, string? kindOverride = null)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        void Add(Visit v, SyntaxNode at, int status, string? code, string? detail, string kind, string? sentinel = null, int? line = null)
        {
            if (status >= 500) return; // a server failure is not a refusal
            if (status < 400) { if (v.Depth == 0 && success != null) success.Add(status); return; }
            var x = new XRaise { Status = status, Code = code, Detail = detail, Line = line ?? LineOf(at), File = FileOf(at), Kind = kindOverride ?? kind, In = v.In, Sentinel = sentinel };
            if (seen.Add($"{x.File}|{x.Line}|{x.Status}|{x.Code}|{x.Kind}|{x.Sentinel}")) raises.Add(x);
        }
        foreach (var v in r.Visits)
        {
            foreach (var node in v.Body.DescendantNodesAndSelf())
            {
                switch (node)
                {
                    case InvocationExpressionSyntax inv:
                        {
                            var c = Resolve(inv);
                            var name = c.Name;
                            if (FrameworkResultCall(inv, c))
                            {
                                int? status = null;
                                if (c.M != null)
                                    foreach (var (p, e) in MapArgs(inv, c)) if (IsStatusParam(p.Name) && ConstOf(e, v) is int sp) status = sp;
                                if (status == null) foreach (var a in inv.ArgumentList.Arguments) if (a.NameColon?.Name.Identifier.Text is "statusCode" && ConstOf(a.Expression, v) is int ns) status = ns;
                                if (name is "StatusCode" or "Problem" or "ValidationProblem" or "Json" or "Text" or "Content" or "Bytes" or "Stream" or "File" && status == null)
                                {
                                    if (name == "ValidationProblem") status = 400;
                                    else if (name == "StatusCode" && inv.ArgumentList.Arguments.Count > 0 && ConstOf(inv.ArgumentList.Arguments[0].Expression, v) is int pos) status = pos;
                                    else if (name == "StatusCode") { unread.Add(MakeUnread(inv, name + "(" + Js.Text(inv.ArgumentList) + ")", "status is not a constant", "raise", route)); continue; }
                                    else if (name == "Problem") continue;
                                }
                                status ??= FactoryStatus.TryGetValue(name, out var fs) ? fs : SuccessStatus.TryGetValue(name, out var ss) ? ss : null;
                                if (status == null) break;
                                var (code, detail) = ArgsText(inv, c, v);
                                Add(v, inv, status.Value, code, detail, "return", null, LineOf(inv));
                                break;
                            }
                            if (c.Project && c.M != null)
                                foreach (var (p, e) in MapArgs(inv, c))
                                    if (IsStatusParam(p.Name) && p.Type.SpecialType == SpecialType.System_Int32 && ConstOf(e, v) is int st && st >= 400 && !ReturnsError(c.M))
                                    {
                                        string? code = null, detail = null;
                                        foreach (var (p2, e2) in MapArgs(inv, c)) { if (IsCodeParam(p2.Name) && ConstOf(e2, v) is string cs) code = cs; else if (IsMessageParam(p2.Name) && ConstOf(e2, v) is string ms) detail = ms; }
                                        Add(v, inv, st, code, detail, "return");
                                    }
                            break;
                        }
                    case ObjectCreationExpressionSyntax oc when oc.Type is { } ot && (FactoryStatus.ContainsKey(ot.ToString().Split('.').Last()) || SuccessStatus.ContainsKey(ot.ToString().Split('.').Last())):
                        {
                            var tn = ot.ToString().Split('.').Last();
                            if (Sm(oc).GetTypeInfo(oc).Type is INamedTypeSymbol tt && IsProject(tt)) break;
                            int status = FactoryStatus.TryGetValue(tn, out var f2) ? f2 : SuccessStatus[tn];
                            foreach (var a in oc.ArgumentList?.Arguments ?? default) if (ConstOf(a.Expression, v) is int ci && ci is >= 100 and < 600 && tn == "StatusCodeResult") status = ci;
                            Add(v, oc, status, null, null, "return");
                            break;
                        }
                    case AssignmentExpressionSyntax asg when asg.Left is MemberAccessExpressionSyntax { Name.Identifier.Text: "StatusCode" } && ConstOf(asg.Right, v) is int sc:
                        Add(v, asg, sc, null, null, "return"); break;
                    case ThrowStatementSyntax or ThrowExpressionSyntax:
                        {
                            var ex = node is ThrowStatementSyntax ts ? ts.Expression : ((ThrowExpressionSyntax)node).Expression;
                            if (ex == null) break;
                            var ei = ErrorOf(ex, v);
                            if (ei?.Status != null) { Add(v, node, ei.Status.Value, ei.Code, ei.Detail, "exception", ei.Type != null ? QualType(ei.Type) : null); break; }
                            var what = "throw " + (ex is ObjectCreationExpressionSyntax oce ? "new " + Js.Text(oce.Type) : Js.Text(ex));
                            unread.Add(MakeUnread(node, what, "thrown, no constant mapping", "raise", route));
                            AddUnread(null, node, what, "thrown, no constant mapping", "raise", route);
                            break;
                        }
                }
            }
        }
        // success statuses from implicit returns (a handler that returns a plain value answers 200)
        if (success != null && r.Visits.Count > 0)
        {
            var root = r.Visits[0];
            foreach (var re in Returns(root.Body)) if (ImplicitOk(re)) success.Add(200);
        }
    }

    bool ReturnsError(IMethodSymbol m) => m.ReturnType is INamedTypeSymbol rt && (rt.AllInterfaces.Any(i => i.Name == "IResult" || i.Name == "IActionResult") || rt.BaseType?.Name == "Exception" || IsExceptionType(rt));

    static bool IsExceptionType(ITypeSymbol t) { for (var b = t; b != null; b = b.BaseType) if (b.Name == "Exception") return true; return false; }

    bool ImplicitOk(ExpressionSyntax e) => e switch
    {
        ParenthesizedExpressionSyntax p => ImplicitOk(p.Expression),
        AwaitExpressionSyntax => false,
        InvocationExpressionSyntax inv => false,
        ConditionalExpressionSyntax c => ImplicitOk(c.WhenTrue) || ImplicitOk(c.WhenFalse),
        SwitchExpressionSyntax s => s.Arms.Any(a => ImplicitOk(a.Expression)),
        BinaryExpressionSyntax { RawKind: (int)SyntaxKind.CoalesceExpression } b => ImplicitOk(b.Right),
        LiteralExpressionSyntax or InterpolatedStringExpressionSyntax or AnonymousObjectCreationExpressionSyntax => true,
        ObjectCreationExpressionSyntax oc => !(oc.Type.ToString().EndsWith("Result") || oc.Type.ToString().EndsWith("ObjectResult")),
        _ => false,
    };

    // ---- constructors / sentinels / error writers (the goextract keys) --------------------------------------------------------------

    void BuildErrorKeys()
    {
        foreach (var m in MethodsByName.Values.SelectMany(x => x).Where(IsProject).OrderBy(m => Qual(m), StringComparer.Ordinal).ThenBy(m => m.Locations.First().SourceSpan.Start))
        {
            if (m.ReturnType is not INamedTypeSymbol rt || !IsExceptionType(rt) || !IsProject(rt)) continue;
            var body = BodyOf(DeclOf(m));
            if (body == null) continue;
            foreach (var re in Returns(body))
            {
                if (re is not (ObjectCreationExpressionSyntax or ImplicitObjectCreationExpressionSyntax)) continue;
                var c = ResolveNew(re);
                if (c.M == null) continue;
                var args = re is ObjectCreationExpressionSyntax o ? o.ArgumentList!.Arguments : ((ImplicitObjectCreationExpressionSyntax)re).ArgumentList.Arguments;
                var ctor = new XCtor { Func = Qual(m), File = FileOf(re), Line = LineOf(re) };
                int i = 0;
                foreach (var a in args)
                {
                    var p = i < c.M.Parameters.Length ? c.M.Parameters[i] : null; i++;
                    if (p == null) continue;
                    var cv = Sm(a).GetConstantValue(a.Expression);
                    if (IsStatusParam(p.Name)) { if (cv.HasValue && cv.Value is int s) ctor.Status = s; else if (a.Expression is IdentifierNameSyntax idn && m.Parameters.FirstOrDefault(x => x.Name == idn.Identifier.Text) is { } sp) ctor.StatusParam = sp.Ordinal; }
                    else if (IsCodeParam(p.Name)) { if (cv.HasValue && cv.Value is string cs) ctor.Code = cs; else if (a.Expression is IdentifierNameSyntax idc && m.Parameters.FirstOrDefault(x => x.Name == idc.Identifier.Text) is { } cp) ctor.CodeParam = cp.Ordinal; }
                }
                if (ctor.Status != null || ctor.StatusParam != null) D.Constructors.Add(ctor);
                break;
            }
        }
        foreach (var kv in ExceptionMap.OrderBy(k => k.Value.File, StringComparer.Ordinal).ThenBy(k => k.Value.Line))
        {
            var t = kv.Key;
            var d = DeclOf(t)!;
            D.Sentinels.Add(new XSentinel { Name = t.Name, Package = t.ContainingNamespace.IsGlobalNamespace ? "" : t.ContainingNamespace.ToDisplayString(), File = FileOf(d), Line = LineOf(d), Status = kv.Value.Status, Writer = kv.Value.Via });
            D.Dotnet.ExceptionMap.Add(new ExceptionMapEntry { Type = QualType(t), Status = kv.Value.Status, Via = kv.Value.Via, File = kv.Value.File, Line = kv.Value.Line });
        }
        foreach (var grp in ExceptionMap.GroupBy(k => k.Value.Via).OrderBy(g => g.Key, StringComparer.Ordinal))
        {
            var first = grp.First().Value;
            var w = new XWriter { Func = grp.Key, File = first.File, Line = first.Line };
            foreach (var kv in grp.OrderBy(k => QualType(k.Key), StringComparer.Ordinal)) w.Maps.Add(new XWriteMap { Sentinel = QualType(kv.Key), Status = kv.Value.Status, Line = kv.Value.Line });
            D.ErrorWriters.Add(w);
        }
        foreach (var t in handledTypes.Where(t => !ExceptionMap.ContainsKey(t) && HasStatusCtor(t)).OrderBy(t => QualType(t), StringComparer.Ordinal))
        {
            var d = DeclOf(t)!;
            D.Sentinels.Add(new XSentinel { Name = t.Name, Package = t.ContainingNamespace.IsGlobalNamespace ? "" : t.ContainingNamespace.ToDisplayString(), File = FileOf(d), Line = LineOf(d), Status = null, Writer = "status carried by the exception (constructor argument)" });
        }
        D.ErrorWriter = D.ErrorWriters.FirstOrDefault();
    }
}
