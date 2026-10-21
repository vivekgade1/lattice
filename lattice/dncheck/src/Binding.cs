// Model binding and bounds (N5): where each handler parameter comes from, which bounds exist, and -- the point of the
// section -- whether anything ENFORCES them. An annotation nobody enforces gives no bound; it is listed
// "declared, not enforced". Also the effective JSON naming policy (wire names).
using System.Text.RegularExpressions;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed class Bound
{
    public string Kind = "";   // required | ge | le | min_length | max_length | choices
    public long? Num;
    public List<string>? Choices;
    public string File = "";
    public int Line;
    public string What = "";
    public string Source = ""; // annotation | fluent | implicit | json-required
}

public sealed partial class Ex
{
    readonly Dictionary<string, string> namingByStyle = new(StringComparer.Ordinal);
    readonly Dictionary<INamedTypeSymbol, List<(string Prop, List<Bound> Bounds)>> validators = new(SymbolEqualityComparer.Default);
    readonly Dictionary<INamedTypeSymbol, XModel> modelsByType = new(SymbolEqualityComparer.Default);
    readonly Dictionary<INamedTypeSymbol, List<(bool Same, Dictionary<string, XParam> Fields)>> modelUse = new(SymbolEqualityComparer.Default);
    bool customNaming;

    // ---- JSON naming -------------------------------------------------------------------------------------------------------

    void ResolveJsonNaming()
    {
        var found = new List<(string Policy, HashSet<string> Styles)>();
        foreach (var asg in Trees.SelectMany(t => t.GetRoot().DescendantNodes().OfType<AssignmentExpressionSyntax>()))
        {
            var left = asg.Left is MemberAccessExpressionSyntax lm ? lm.Name.Identifier.Text : (asg.Left as IdentifierNameSyntax)?.Identifier.Text;
            if (left == "PropertyNameCaseInsensitive" && Sm(asg).GetConstantValue(asg.Right) is { Value: false }) D.Dotnet.JsonInputCaseInsensitive = false;
            if (left != "PropertyNamingPolicy") continue;
            string policy = asg.Right is LiteralExpressionSyntax l && l.IsKind(SyntaxKind.NullLiteralExpression) ? "null"
                : asg.Right is MemberAccessExpressionSyntax rm && rm.Expression.ToString().EndsWith("JsonNamingPolicy") ? rm.Name.Identifier.Text : "custom";
            if (policy == "custom") { customNaming = true; AddUnread(null, asg, "PropertyNamingPolicy = " + Js.Text(asg.Right), "a custom JsonNamingPolicy: wire names are not read", "json"); }
            found.Add((policy, StylesOf(asg, 0)));
        }
        string For(string style)
        {
            var hit = found.Where(f => f.Styles.Contains(style) || f.Styles.Count == 0 || f.Styles.Contains("any")).Select(f => f.Policy).Distinct().ToList();
            return hit.Count == 0 ? "CamelCase" : hit[0];
        }
        namingByStyle["minimal"] = For("minimal");
        namingByStyle["controller"] = For("controller");
        D.Dotnet.JsonNamingByStyle["controller"] = namingByStyle["controller"];
        D.Dotnet.JsonNamingByStyle["minimal"] = namingByStyle["minimal"];
        D.Dotnet.JsonNaming = found.Any(f => f.Styles.Contains("minimal") || f.Styles.Count == 0) ? namingByStyle["minimal"] : found.Count > 0 ? namingByStyle["controller"] : "CamelCase";
        if (customNaming) D.Dotnet.JsonNaming = "custom";
    }

    HashSet<string> StylesOf(SyntaxNode n, int depth)
    {
        var res = new HashSet<string>();
        if (depth > 3) return res;
        foreach (var a in n.Ancestors())
        {
            if (a is InvocationExpressionSyntax inv && inv.ArgumentList.Arguments.Any(x => x.Expression.Span.Contains(n.Span)))
            {
                var nm = InvokedName(inv.Expression);
                if (nm == "ConfigureHttpJsonOptions") res.Add("minimal");
                else if (nm == "AddJsonOptions") res.Add("controller");
            }
            if (a is MethodDeclarationSyntax or LocalFunctionStatementSyntax)
            {
                var sym = Sm(a).GetDeclaredSymbol(a) as IMethodSymbol;
                if (sym != null)
                    foreach (var call in AllInvocations().Where(i => InvokedName(i.Expression) == sym.Name))
                    {
                        var c = Resolve(call);
                        if (c.M != null && SymbolEqualityComparer.Default.Equals(c.M, sym.OriginalDefinition)) res.UnionWith(StylesOf(call, depth + 1));
                    }
                break;
            }
        }
        return res;
    }

    public string Wire(string prop, string style, ISymbol? sym = null)
    {
        if (sym != null && Attr(sym, "JsonPropertyName") is { } jp && ArgValue(jp, 0) is string n) return n;
        return Js.Apply(namingByStyle.GetValueOrDefault(style, "CamelCase"), prop);
    }

    // ---- validators (FluentValidation) --------------------------------------------------------------------------------------------

    void ReadValidators()
    {
        foreach (var t in ProjTypes.Where(t => t.TypeKind == TypeKind.Class))
        {
            if (DeclOf(t) is not ClassDeclarationSyntax cd || cd.BaseList?.Types.Select(b => b.Type).OfType<GenericNameSyntax>().FirstOrDefault(g => g.Identifier.Text == "AbstractValidator") is not { } g) continue;
            if (Sm(cd).GetTypeInfo(g.TypeArgumentList.Arguments[0]).Type is not INamedTypeSymbol model) continue;
            var rules = new List<(string, List<Bound>)>();
            foreach (var top in cd.DescendantNodes().OfType<InvocationExpressionSyntax>())
            {
                if (top.Parent is MemberAccessExpressionSyntax) continue;
                var segs = ChainSegments(top, out var root);
                if (root is not InvocationExpressionSyntax rf || InvokedName(rf.Expression) != "RuleFor") continue;
                var props = LambdaProps(rf.ArgumentList.Arguments.FirstOrDefault()?.Expression);
                if (props.Count == 0) continue;
                var bl = new List<Bound>();
                foreach (var s in segs)
                {
                    var nm = InvokedName(s.Expression);
                    var a = s.ArgumentList.Arguments.Select(x => Sm(s).GetConstantValue(x.Expression).Value).ToList();
                    long? N(int i) => i < a.Count && a[i] != null ? Convert.ToInt64(a[i], System.Globalization.CultureInfo.InvariantCulture) : null;
                    void Put(string k, long? v) { if (v != null) bl.Add(new Bound { Kind = k, Num = v, File = FileOf(s), Line = LineOf(s), What = "FluentValidation " + nm + " on " + model.Name + "." + props[0], Source = "fluent" }); }
                    switch (nm)
                    {
                        case "NotEmpty": case "NotNull": bl.Add(new Bound { Kind = "required", File = FileOf(s), Line = LineOf(s), What = "FluentValidation " + nm, Source = "fluent" }); break;
                        case "Length": Put("min_length", N(0)); Put("max_length", N(1)); break;
                        case "MaximumLength": Put("max_length", N(0)); break;
                        case "MinimumLength": Put("min_length", N(0)); break;
                        case "InclusiveBetween": Put("ge", N(0)); Put("le", N(1)); break;
                        case "ExclusiveBetween": Put("ge", N(0) + 1); Put("le", N(1) - 1); break;
                        case "GreaterThanOrEqualTo": Put("ge", N(0)); break;
                        case "GreaterThan": Put("ge", N(0) + 1); break;
                        case "LessThanOrEqualTo": Put("le", N(0)); break;
                        case "LessThan": Put("le", N(0) - 1); break;
                        case "Must":
                        case "Matches":
                        case "When":
                        case "Unless":
                        case "MustAsync":
                        case "Custom":
                            AddUnread(null, s, $"FluentValidation {nm} on {model.Name}.{props[0]}", "rule is not a constant bound", "bind"); break;
                    }
                }
                rules.Add((props[0], bl));
            }
            validators[model] = rules;
        }
    }

    // ---- annotations ---------------------------------------------------------------------------------------------------------------

    List<Bound> Declared(IPropertySymbol p, string typeName)
    {
        var res = new List<Bound>();
        foreach (var a in AttrsOf(p))
        {
            var n = AttrName(a);
            var what = $"[{Js.Text(a)}] on {typeName}.{p.Name}";
            Bound B(string k, long? num, List<string>? ch = null) => new() { Kind = k, Num = num, Choices = ch, File = FileOf(a), Line = LineOf(a), What = what, Source = "annotation" };
            long? Num(object? o) => o == null ? null : o is double d ? (d == Math.Floor(d) ? (long)d : null) : Convert.ToInt64(o, System.Globalization.CultureInfo.InvariantCulture);
            switch (n)
            {
                case "Required": res.Add(B("required", null)); break;
                case "StringLength":
                    if (Num(ArgValue(a, 0)) is { } mx) res.Add(B("max_length", mx));
                    if (Num(ArgValue(a, 0, "MinimumLength")) is { } mn) res.Add(B("min_length", mn));
                    break;
                case "MaxLength": if (Num(ArgValue(a, 0)) is { } ml) res.Add(B("max_length", ml)); break;
                case "MinLength": if (Num(ArgValue(a, 0)) is { } nl) res.Add(B("min_length", nl)); break;
                case "Length": if (Num(ArgValue(a, 0)) is { } l0) res.Add(B("min_length", l0)); if (Num(ArgValue(a, 1)) is { } l1) res.Add(B("max_length", l1)); break;
                case "Range":
                    if (Num(ArgValue(a, 0)) is { } lo && Num(ArgValue(a, 1)) is { } hi) { res.Add(B("ge", lo)); res.Add(B("le", hi)); }
                    else AddUnread(null, a, what, "non-integer or non-constant range", "bind");
                    break;
                case "AllowedValues": res.Add(B("choices", null, a.ArgumentList!.Arguments.Select(x => Js.Inv(Sm(a).GetConstantValue(x.Expression).Value)).ToList())); break;
            }
        }
        return res;
    }

    // ---- parameters ------------------------------------------------------------------------------------------------------------------

    static readonly HashSet<string> SpecialTypes = new(StringComparer.Ordinal) { "HttpContext", "HttpRequest", "HttpResponse", "CancellationToken", "ClaimsPrincipal", "Stream", "PipeReader", "IFormFile", "IFormFileCollection", "IFormCollection", "ConnectionInfo", "WebSocket", "IServiceProvider" };

    bool IsSimple(ITypeSymbol t)
    {
        t = Strip(t);
        if (t is IArrayTypeSymbol at) return IsSimple(at.ElementType);
        if (t is INamedTypeSymbol { IsGenericType: true } g && g.Name is "List" or "IEnumerable" or "IList" or "ICollection" or "HashSet" && g.TypeArguments.Length == 1) return IsSimple(g.TypeArguments[0]);
        return IsScalar(t) || t.Name is "StringValues" or "Version";
    }

    bool IsServiceType(ITypeSymbol t)
    {
        if (t is IErrorTypeSymbol) return true;
        if (t is INamedTypeSymbol nt && (IsDbContextType(nt) || Regs.Any(r => (r.Iface != null && SymbolEqualityComparer.Default.Equals(r.Iface, nt)) || (r.Impl != null && SymbolEqualityComparer.Default.Equals(r.Impl, nt))))) return true;
        return t.TypeKind == TypeKind.Interface && !IsSimple(t) || t.Name is "ILogger" or "IConfiguration" or "IOptions" or "IHttpContextAccessor" or "IWebHostEnvironment" or "IHostEnvironment";
    }

    static readonly Regex TplParam = new(@"\{(?<star>\*{0,2})(?<name>[A-Za-z_]\w*)(?<cons>(?::[^{}=?]+(?:\([^)]*\))?)*)(?<def>=[^}?]*)?(?<opt>\?)?\}", RegexOptions.CultureInvariant);

    public static string Normalize(string tpl) => TplParam.Replace(tpl, m => "{" + m.Groups["name"].Value + "}");

    static (string? Type, string? Parse, long? Ge, long? Le, int? Min, int? Max, bool Unread) Constraint(string cons)
    {
        string? type = null, parse = null; long? ge = null, le = null; int? mn = null, mx = null; bool unread = false;
        foreach (var raw in cons.Split(':', StringSplitOptions.RemoveEmptyEntries))
        {
            var m = Regex.Match(raw, @"^(?<n>\w+)(\((?<a>[^)]*)\))?$");
            var nm = m.Groups["n"].Value.ToLowerInvariant(); var arg = m.Groups["a"].Value;
            switch (nm)
            {
                case "int" or "long": type = "Int"; parse = "Int"; break;
                case "guid": type = "Uuid"; break;
                case "bool": type = "Bool"; break;
                case "datetime": type = "DateTime"; break;
                case "decimal" or "double" or "float": type = "Float"; break;
                case "alpha": type ??= "Str"; break;
                case "min" when long.TryParse(arg, out var a): ge = a; break;
                case "max" when long.TryParse(arg, out var b): le = b; break;
                case "range": var rp = arg.Split(','); if (rp.Length == 2 && long.TryParse(rp[0], out var lo) && long.TryParse(rp[1], out var hi)) { ge = lo; le = hi; } break;
                case "length": var lp = arg.Split(','); if (lp.Length == 1 && int.TryParse(lp[0], out var ln)) { mn = ln; mx = ln; } else if (lp.Length == 2 && int.TryParse(lp[0], out var l1) && int.TryParse(lp[1], out var l2)) { mn = l1; mx = l2; } break;
                case "minlength" when int.TryParse(arg, out var m1): mn = m1; break;
                case "maxlength" when int.TryParse(arg, out var m2): mx = m2; break;
                case "regex": unread = true; break;
            }
        }
        return (type, parse, ge, le, mn, mx, unread);
    }

    void BuildParams(RouteRec rec)
    {
        var r = rec.R;
        var style = r.Style;
        var tplParams = TplParam.Matches(r.Template).Cast<Match>().ToList();
        var seenPath = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        bool apiController = rec.Controller != null && ChainAttrs(rec.Controller, "ApiController").Any();
        // route values first, in template order
        foreach (var m in tplParams)
        {
            var name = m.Groups["name"].Value;
            var cons = Constraint(m.Groups["cons"].Value);
            var hp = rec.Params.FirstOrDefault(p => string.Equals(p.Name, name, StringComparison.OrdinalIgnoreCase) || AttrsOf(p).Any(a => AttrName(a) == "FromRoute" && ArgValue(a, 0, "Name") as string == name));
            var x = new XParam
            {
                Name = name,
                Wire = name,
                Source = "path",
                BindSource = "route",
                Required = m.Groups["opt"].Success ? false : true,
                Annotation = hp != null ? ParamTypeText(hp) : "string",
                File = hp != null ? FileOf(DeclOf(hp) ?? rec.At) : r.File,
                Line = hp != null ? LineOf(DeclOf(hp) ?? rec.At) : r.Line,
                Ge = cons.Ge,
                Le = cons.Le,
                MinLength = cons.Min,
                MaxLength = cons.Max,
            };
            x.Type = cons.Type ?? (hp != null ? LatticeType(hp.Type) : "Str") ?? "Str";
            if (hp != null && Strip(hp.Type).TypeKind == TypeKind.Enum) x.Choices = Strip(hp.Type).GetMembers().OfType<IFieldSymbol>().Where(f => f.HasConstantValue).Select(f => f.Name).ToList();
            if (m.Groups["def"].Success) x.Default = m.Groups["def"].Value.TrimStart('=');
            if (cons.Parse != null) { x.Parse = cons.Parse; x.ParseAt = $"{r.File}:{r.Line}"; }
            if (cons.Unread) AddUnread(r.Unread, rec.At, "{" + name + m.Groups["cons"].Value + "}", "regex route constraint is not read", "bind", rec.Id);
            if (m.Groups["star"].Length > 0) AddUnread(r.Unread, rec.At, "{" + m.Groups["star"].Value + name + "}", "catch-all parameters are not read", "bind", rec.Id);
            seenPath.Add(name);
            r.Params.Add(x);
        }
        foreach (var p in rec.Params)
        {
            var attrs = AttrsOf(p).ToList();
            var routeName = attrs.Where(a => AttrName(a) == "FromRoute").Select(a => ArgValue(a, 0, "Name") as string).FirstOrDefault() ?? p.Name;
            if (seenPath.Contains(routeName) || seenPath.Contains(p.Name)) continue;
            string? src = null; string? attrName = null;
            foreach (var a in attrs)
            {
                switch (AttrName(a))
                {
                    case "FromBody": src = "body"; break;
                    case "FromQuery": src = "query"; attrName = ArgValue(a, 0, "Name") as string; break;
                    case "FromRoute": src = "path"; attrName = ArgValue(a, 0, "Name") as string; break;
                    case "FromHeader": src = "header"; attrName = ArgValue(a, 0, "Name") as string; break;
                    case "FromForm": src = "form"; attrName = ArgValue(a, 0, "Name") as string; break;
                    case "FromServices": case "FromKeyedServices": src = "services"; break;
                    case "AsParameters": src = "asparameters"; break;
                }
            }
            var t = p.Type;
            if (src == null)
            {
                if (SpecialTypes.Contains(Strip(t).Name)) src = "special";
                else if (IsServiceType(t)) src = "services";
                else if (IsSimple(t)) src = "query";
                else src = "body";
            }
            if (src is "services" or "special") continue;
            if (src == "asparameters" && t is INamedTypeSymbol at)
            {
                foreach (var prop in at.GetMembers().OfType<IPropertySymbol>().Where(q => !q.IsStatic && q.DeclaredAccessibility == Accessibility.Public))
                    r.Params.Add(SimpleParam(rec, prop.Name, prop.Type, "query", "query", DeclOf(prop) ?? rec.At, prop.NullableAnnotation == NullableAnnotation.Annotated, null));
                continue;
            }
            if (src == "body" && !IsSimple(t))
            {
                if (rec.Controller != null && !apiController && !attrs.Any(a => AttrName(a) == "FromBody")) { AddUnread(r.Unread, DeclOf(p) ?? rec.At, $"{Js.Text((DeclOf(p) as ParameterSyntax)?.Type ?? (SyntaxNode)rec.At)} {p.Name}", "complex type binds from form/query in a controller without [ApiController]", "bind", rec.Id); continue; }
                if (Strip(t) is INamedTypeSymbol bt && IsProject(bt)) { BodyFields(rec, bt, p, style, apiController); continue; }
                AddUnread(r.Unread, DeclOf(p) ?? rec.At, $"body parameter {p.Name} : {t.ToDisplayString()}", "body type is not a project type", "bind", rec.Id);
                continue;
            }
            bool optional = p.NullableAnnotation == NullableAnnotation.Annotated || p.HasExplicitDefaultValue || t is INamedTypeSymbol { OriginalDefinition.SpecialType: SpecialType.System_Nullable_T };
            var nm = attrName ?? (src == "header" ? p.Name : p.Name);
            r.Params.Add(SimpleParam(rec, nm, t, src, src == "path" ? "route" : src, DeclOf(p) ?? rec.At, optional, p));
        }
        AccessorParams(rec);
        GuardBounds(rec);
    }

    static IEnumerable<ExpressionSyntax> Disjuncts(ExpressionSyntax e)
    {
        e = Unwrap(e);
        if (e is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.LogicalOrExpression } b) { foreach (var x in Disjuncts(b.Left)) yield return x; foreach (var x in Disjuncts(b.Right)) yield return x; }
        else yield return e;
    }

    /// <summary>Explicit comparison guards in the handler that refuse with one constant 4xx (the Go Body-guard rule): `if (x.P &lt; 1 || x.P &gt; 5) return BadRequest()`.</summary>
    void GuardBounds(RouteRec rec)
    {
        var r = rec.R;
        var body = r.Params.Where(p => p.Source == "body").ToList();
        if (body.Count == 0 || rec.Reach!.Visits.Count == 0) return;
        var v = rec.Reach.Visits[0];
        XParam? Subject(ExpressionSyntax e, out bool length)
        {
            e = Unwrap(e); length = false;
            if (e is MemberAccessExpressionSyntax { Name.Identifier.Text: "Length", Expression: var inner }) { length = true; e = Unwrap(inner); }
            if (e is MemberAccessExpressionSyntax ma && Sm(ma).GetSymbolInfo(ma.Name).Symbol is IPropertySymbol ps && IsProject(ps)) { var w = Wire(ps.Name, r.Style, ps); return body.FirstOrDefault(p => p.Wire == w); }
            return null;
        }
        foreach (var ifs in v.Body.DescendantNodes().OfType<IfStatementSyntax>())
        {
            if (ifs.Else != null || !Refuses(ifs.Statement) || RefusalStatus(ifs.Statement, v) is not { } st || st < 400 || st >= 500) continue;
            foreach (var term in Disjuncts(ifs.Condition))
            {
                if (term is BinaryExpressionSyntax cmp && (cmp.IsKind(SyntaxKind.LessThanExpression) || cmp.IsKind(SyntaxKind.LessThanOrEqualExpression) || cmp.IsKind(SyntaxKind.GreaterThanExpression) || cmp.IsKind(SyntaxKind.GreaterThanOrEqualExpression)))
                {
                    var kind = cmp.Kind();
                    var (subj, cexpr) = (cmp.Left, cmp.Right);
                    if (Sm(cmp).GetConstantValue(cmp.Left).HasValue) { (subj, cexpr) = (cmp.Right, cmp.Left); kind = kind switch { SyntaxKind.LessThanExpression => SyntaxKind.GreaterThanExpression, SyntaxKind.LessThanOrEqualExpression => SyntaxKind.GreaterThanOrEqualExpression, SyntaxKind.GreaterThanExpression => SyntaxKind.LessThanExpression, _ => SyntaxKind.LessThanOrEqualExpression }; }
                    if (Subject(subj, out var length) is not { } f || ConstOf(cexpr, v) is not { } cv || cv is string or bool) continue;
                    long c = Convert.ToInt64(cv, System.Globalization.CultureInfo.InvariantCulture);
                    switch (kind)
                    {
                        case SyntaxKind.LessThanExpression: if (length) f.MinLength = (int)c; else f.Ge = c; break;
                        case SyntaxKind.LessThanOrEqualExpression: if (length) f.MinLength = (int)c + 1; else f.Ge = c + 1; break;
                        case SyntaxKind.GreaterThanExpression: if (length) f.MaxLength = (int)c; else f.Le = c; break;
                        default: if (length) f.MaxLength = (int)c - 1; else f.Le = c - 1; break;
                    }
                    f.EnforcedBy ??= "handler-guard";
                }
                else if (term is BinaryExpressionSyntax eq && eq.IsKind(SyntaxKind.EqualsExpression) && Unwrap(eq.Right) is LiteralExpressionSyntax { RawKind: (int)SyntaxKind.NullLiteralExpression } && Subject(eq.Left, out _) is { } rf) { rf.Required = true; rf.EnforcedBy ??= "handler-guard"; }
                else if (term is IsPatternExpressionSyntax { Pattern: ConstantPatternSyntax { Expression: LiteralExpressionSyntax { RawKind: (int)SyntaxKind.NullLiteralExpression } } } ip && Subject(ip.Expression, out _) is { } pf) { pf.Required = true; pf.EnforcedBy ??= "handler-guard"; }
                else if (term is InvocationExpressionSyntax { ArgumentList.Arguments.Count: 1 } ni && InvokedName(ni.Expression) is "IsNullOrEmpty" or "IsNullOrWhiteSpace" && Subject(ni.ArgumentList.Arguments[0].Expression, out _) is { } nf) { nf.Required = true; nf.EnforcedBy ??= "handler-guard"; }
            }
        }
    }

    /// <summary>Fields read through a request-body wrapper: `body.OptionalString("email")` on a project type built from the request body.</summary>
    void AccessorParams(RouteRec rec)
    {
        var r = rec.R;
        if (r.Params.Any(p => p.Source == "body")) return;
        var seen = new Dictionary<string, XParam>(StringComparer.Ordinal);
        foreach (var v in rec.Reach!.Visits)
            foreach (var inv in v.Body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>())
            {
                if (inv.ArgumentList.Arguments.Count == 0 || inv.Expression is not MemberAccessExpressionSyntax) continue;
                var c = Resolve(inv);
                if (!c.Project || c.M == null || c.M.IsStatic || ConstOf(inv.ArgumentList.Arguments[0].Expression, v) is not string key || !IsKeyAccessor(c.M, 0) || !IsBodyWrapper(c.M.ContainingType)) continue;
                bool required = AccessorRequired(inv, c.M, v);
                if (seen.TryGetValue(key, out var prior)) { if (required) prior.Required = true; continue; }
                var suffix = Regex.Replace(c.M.Name, "^(Optional|Required|Try|Get|Read)", "");
                var np = new XParam { Name = key, Wire = key, Source = "body", BindSource = "body", Annotation = c.M.ReturnType.ToDisplayString(), Type = suffix switch { "String" => "Str", "Int" or "Int32" or "Long" => "Int", "Bool" or "Boolean" => "Bool", "Uuid" or "Guid" => "Uuid", "Date" => "Date", "DateTime" => "DateTime", "Double" or "Decimal" or "Number" => "Float", _ => null }, Required = required, File = FileOf(inv), Line = LineOf(inv), EnforcedBy = null };
                seen[key] = np;
                r.Params.Add(np);
            }
    }

    bool IsBodyWrapper(INamedTypeSymbol t) => MethodsByName.Values.SelectMany(x => x).Any(m => m.IsStatic && IsProject(m) && (m.ReturnType is INamedTypeSymbol rt && (SymbolEqualityComparer.Default.Equals(rt, t) || rt.IsGenericType && rt.TypeArguments.Length == 1 && SymbolEqualityComparer.Default.Equals(rt.TypeArguments[0], t))) && m.Parameters.Any(p => p.Type.Name == "HttpRequest"));

    string ParamTypeText(IParameterSymbol p) => (DeclOf(p) as ParameterSyntax)?.Type is { } ts ? Js.Text(ts) : p.Type.ToDisplayString();

    XParam SimpleParam(RouteRec rec, string name, ITypeSymbol t, string src, string bindSrc, SyntaxNode decl, bool optional, IParameterSymbol? p)
    {
        var x = new XParam { Name = name, Wire = name, Source = src, BindSource = bindSrc, Required = !optional, Annotation = decl is ParameterSyntax ps && ps.Type != null ? Js.Text(ps.Type) : t.ToDisplayString(), Type = LatticeType(t), File = FileOf(decl), Line = LineOf(decl) };
        if (p != null && p.HasExplicitDefaultValue && p.ExplicitDefaultValue != null) x.Default = Js.Inv(p.ExplicitDefaultValue);
        if (Strip(t).TypeKind == TypeKind.Enum) x.Choices = Strip(t).GetMembers().OfType<IFieldSymbol>().Where(f => f.HasConstantValue).Select(f => f.Name).ToList();
        return x;
    }

    // ---- body models ------------------------------------------------------------------------------------------------------------------

    IEnumerable<IPropertySymbol> ModelProps(INamedTypeSymbol t)
    {
        var chain = new List<INamedTypeSymbol>();
        for (var b = t; b != null && IsProject(b); b = b.BaseType) chain.Add(b);
        chain.Reverse();
        foreach (var c in chain)
            foreach (var p in c.GetMembers().OfType<IPropertySymbol>().Where(p => !p.IsStatic && !p.IsIndexer && p.DeclaredAccessibility == Accessibility.Public && p.GetMethod != null && (p.SetMethod != null || DeclOf(p) is ParameterSyntax)).OrderBy(p => p.Locations.First().SourceSpan.Start))
                yield return p;
    }

    string? Enforcement(RouteRec rec, bool apiController, INamedTypeSymbol model, out bool fluent)
    {
        fluent = false;
        var reach = rec.Reach!;
        var text = string.Concat(reach.Visits.Select(v => v.Body.ToString()));
        string? by = null;
        if (rec.Controller != null) { if (apiController && !suppressModelState) by = "apicontroller"; }
        else if (minimalValidation) by = "addvalidation";
        if (by == null && (text.Contains("ModelState.IsValid") || text.Contains("TryValidateModel"))) by = "modelstate";
        if (by == null && text.Contains("TryValidateObject")) by = "validator";
        if (validators.ContainsKey(model))
        {
            bool called = reach.Visits.SelectMany(v => v.Body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>()).Any(i =>
                InvokedName(i.Expression) is "Validate" or "ValidateAsync" or "ValidateAndThrow" or "ValidateAndThrowAsync" && i.ArgumentList.Arguments.Any(a => Sm(i).GetTypeInfo(a.Expression).Type is { } at && SymbolEqualityComparer.Default.Equals(Strip(at), model)));
            if (called || (fluentAuto && rec.Controller != null)) fluent = true;
        }
        return by;
    }

    void BodyFields(RouteRec rec, INamedTypeSymbol model, IParameterSymbol param, string style, bool apiController)
    {
        var r = rec.R;
        var by = Enforcement(rec, apiController, model, out var fluent);
        bool implicitReq = rec.Controller != null && apiController && P.Nullable.ToLowerInvariant() == "enable" && !suppressImplicit;
        r.BodyModels.Add(QualType(model));
        var rules = fluent ? validators[model] : null;
        var fields = new Dictionary<string, XParam>(StringComparer.Ordinal);
        foreach (var p in ModelProps(model))
        {
            if (Attr(p, "JsonIgnore") is { } ji && !(ji.ArgumentList?.Arguments.Any(a => a.NameEquals?.Name.Identifier.Text == "Condition") ?? false)) continue;
            var decl = DeclOf(p) ?? rec.At;
            var wire = Wire(p.Name, style, p);
            var x = new XParam
            {
                Name = wire,
                Wire = wire,
                Source = "body",
                BindSource = "body",
                Annotation = decl is PropertyDeclarationSyntax pd ? Js.Text(pd.Type) : decl is ParameterSyntax ps && ps.Type != null ? Js.Text(ps.Type) : p.Type.ToDisplayString(),
                Type = LatticeType(p.Type),
                File = FileOf(decl),
                Line = LineOf(decl),
                EnforcedBy = null,
            };
            if (Strip(p.Type).TypeKind == TypeKind.Enum) x.Choices = Strip(p.Type).GetMembers().OfType<IFieldSymbol>().Where(f => f.HasConstantValue).Select(f => f.Name).ToList();
            var declared = Declared(p, model.Name);
            void Apply(Bound b, string src)
            {
                switch (b.Kind)
                {
                    case "required": x.Required = true; break;
                    case "ge": x.Ge = b.Num; break;
                    case "le": x.Le = b.Num; break;
                    case "min_length": x.MinLength = (int?)b.Num; break;
                    case "max_length": x.MaxLength = (int?)b.Num; break;
                    case "choices": x.Choices = b.Choices ?? new(); break;
                }
                x.EnforcedBy ??= src;
            }
            foreach (var b in declared)
            {
                if (by != null) Apply(b, by);
                else AddUnread(r.Unread, FindAttr(b), b.What, "declared, not enforced", "bind", rec.Id, b.Line);
            }
            if (p.IsRequired) { x.Required = true; x.EnforcedBy ??= "json-required"; }
            if (implicitReq && !p.Type.IsValueType && p.NullableAnnotation == NullableAnnotation.NotAnnotated && !x.Required && !p.IsRequired) { x.Required = true; x.EnforcedBy = "apicontroller-implicit-required"; }
            if (rules != null)
                foreach (var (prop, bl) in rules.Where(q => q.Prop == p.Name))
                    foreach (var b in bl) Apply(b, "fluentvalidation");
            fields[p.Name] = x;
            r.Params.Add(Clone(x));
        }
        if (!modelUse.TryGetValue(model, out var uses)) modelUse[model] = uses = new();
        uses.Add((true, fields));
        if (!modelsByType.ContainsKey(model))
        {
            var d = DeclOf(model)!;
            modelsByType[model] = new XModel { Name = QualType(model), Package = model.ContainingNamespace.IsGlobalNamespace ? "" : model.ContainingNamespace.ToDisplayString(), File = FileOf(d), Line = LineOf(((BaseTypeDeclarationSyntax)d).Identifier), Kind = "request" };
        }
        if (by != null || fluent) rec.BindEnforced = true;
        if (Policy && by is "apicontroller" or "addvalidation" or "modelstate") r.Raises.Add(new XRaise { Status = D.Dotnet.BindStatus ?? 400, Line = r.Line, File = r.File, Kind = "bind", In = r.Handler?.Func ?? "", Bind = true });
    }

    SyntaxNode FindAttr(Bound b) => Trees.First(t => t.FilePath == b.File).GetRoot().DescendantNodes().OfType<AttributeSyntax>().First(a => LineOf(a) == b.Line);

    static XParam Clone(XParam x) => new()
    {
        Name = x.Name,
        Annotation = x.Annotation,
        Type = x.Type,
        Source = x.Source,
        Required = x.Required,
        Default = x.Default,
        Ge = x.Ge,
        Le = x.Le,
        MinLength = x.MinLength,
        MaxLength = x.MaxLength,
        Choices = x.Choices?.ToList() ?? new(),
        File = x.File,
        Line = x.Line,
        Parse = x.Parse,
        ParseAt = x.ParseAt,
        Wire = x.Wire,
        BindSource = x.BindSource,
        EnforcedBy = x.EnforcedBy,
    };

    void BuildModels()
    {
        foreach (var kv in modelsByType.OrderBy(k => k.Value.Name, StringComparer.Ordinal))
        {
            var m = kv.Value;
            var uses = modelUse[kv.Key];
            foreach (var f in uses[0].Fields.Values)
            {
                var common = Clone(f);
                foreach (var u in uses.Skip(1))
                    if (u.Fields.TryGetValue(f.Wire!, out var o)) { if (o.Ge != common.Ge) common.Ge = null; if (o.Le != common.Le) common.Le = null; if (o.MinLength != common.MinLength) common.MinLength = null; if (o.MaxLength != common.MaxLength) common.MaxLength = null; if (o.Required != common.Required) common.Required = false; }
                m.Fields.Add(common);
            }
            D.Models.Add(m);
        }
    }

    void ReportBindStatus()
    {
        if (namingByStyle.GetValueOrDefault("minimal") != namingByStyle.GetValueOrDefault("controller") && Recs.Any(r => r.Controller != null) && Recs.Any(r => r.Controller == null && !r.Framework))
            D.Notes.Add($"JSON naming differs by style: minimal {namingByStyle["minimal"]}, controllers {namingByStyle["controller"]}");
        int? status = null;
        bool any = Recs.Any(r => r.Controller != null && ChainAttrs(r.Controller, "ApiController").Any()) && !suppressModelState || minimalValidation;
        foreach (var asg in Trees.SelectMany(t => t.GetRoot().DescendantNodes().OfType<AssignmentExpressionSyntax>()))
            if (asg.Left is MemberAccessExpressionSyntax { Name.Identifier.Text: "InvalidModelStateResponseFactory" })
            {
                var st = StatusIn(asg.Right);
                if (st != null) status = st; else AddUnread(null, asg, "InvalidModelStateResponseFactory", "custom factory without a constant status", "bind");
            }
        D.Dotnet.BindStatus = any ? status ?? 400 : status;
    }
}
