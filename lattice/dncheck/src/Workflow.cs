// The workflow pass (N16): the opt-in keys of `dncheck extract` with {"workflow": true} that the importer's workflow layer
// reads -- param_uses (a request value reaches a column), creates (a new entity is added, and where its key comes from),
// param_literals (the constants a request value is compared to), status_facts (create or update, and the states written
// from) and workflow_unread. The default part of the document is the default document, except for status writes that go
// through a tuple local (`outcome = (Deliveries.Sent, null); row.Delivery = outcome.delivery`), which this pass adds
// (and says so in workflow_notes). Request values are followed as origins ("path:id", "body:name", "query:limit") through
// locals, DTO members, record constructors and project calls (depth 4, DI resolved as everywhere); a call that cannot
// be followed with a request value in its arguments is an `unread` entry, never a guess.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed class WfUse
{
    public string Route { get; set; } = "";
    public string Param { get; set; } = "";
    public string Source { get; set; } = "";
    public string Table { get; set; } = "";
    public string Model { get; set; } = "";
    public string Column { get; set; } = "";
    public string How { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string In { get; set; } = "";
}

public sealed class WfCreate
{
    public string Route { get; set; } = "";
    public string Table { get; set; } = "";
    public string Model { get; set; } = "";
    public string Key { get; set; } = "";
    public string KeyFrom { get; set; } = "server";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string In { get; set; } = "";
}

public sealed class WfLiteral
{
    public string Route { get; set; } = "";
    public string Param { get; set; } = "";
    public string Source { get; set; } = "";
    public List<string> Values { get; set; } = new();
    public string File { get; set; } = "";
    public int Line { get; set; }
}

public sealed class WfFact
{
    public string Table { get; set; } = "";
    public string Model { get; set; } = "";
    public string Column { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string Kind { get; set; } = "update";
    public List<string> From { get; set; } = new();
    public List<Loc> FromAt { get; set; } = new();
    public string In { get; set; } = "";
}

/// <summary>Request-value origins by field path: "" is the value itself, ".F" its member F.</summary>
sealed class Taint : Dictionary<string, SortedSet<string>>
{
    public bool Add(string field, IEnumerable<string> os)
    {
        bool ch = false;
        foreach (var o in os) { if (!TryGetValue(field, out var s)) this[field] = s = new(StringComparer.Ordinal); ch |= s.Add(o); }
        return ch;
    }
    public bool Merge(Taint? t) { bool ch = false; if (t != null) foreach (var kv in t.ToList()) ch |= Add(kv.Key, kv.Value); return ch; }
    public SortedSet<string> All() { var r = new SortedSet<string>(StringComparer.Ordinal); foreach (var s in Values) r.UnionWith(s); return r; }
    public string Sig => string.Join(";", this.OrderBy(k => k.Key, StringComparer.Ordinal).Select(k => k.Key + "=" + string.Join(",", k.Value)));
    public Taint Copy() { var c = new Taint(); c.Merge(this); return c; }
    public static Taint? Of(IEnumerable<string> os) { var t = new Taint(); t.Add("", os); return t.Count == 0 ? null : t; }
    public static Taint? Join(params Taint?[] ts) { var t = new Taint(); foreach (var x in ts) t.Merge(x); return t.Count == 0 ? null : t; }
    public Taint? Sel(string f) { var r = new Taint(); if (TryGetValue("", out var a)) r.Add("", a); if (TryGetValue("." + f, out var b)) r.Add("", b); return r.Count == 0 ? null : r; }
}

sealed class WfFrame
{
    public Dictionary<ISymbol, Taint> Env = new(SymbolEqualityComparer.Default);
    public SyntaxNode Body = null!;
    public int Depth;
    public string In = "";
}

sealed class WfCtx
{
    public string Route = "";
    public Dictionary<string, Taint?> Memo = new(StringComparer.Ordinal);
    public HashSet<string> Busy = new(StringComparer.Ordinal), UseSeen = new(StringComparer.Ordinal), CreateSeen = new(StringComparer.Ordinal);
    public Dictionary<string, WfLiteral> Lits = new(StringComparer.Ordinal);
}

public sealed class WorkflowPass : IExtractPass
{
    public int Order => 100;
    public void Run(Ex ex, Doc doc) { if (ex.Workflow) ex.RunWorkflow(); }
}

public sealed partial class Ex
{
    const int WfDepth = 4;
    readonly List<WfUse> wfUses = new();
    readonly List<WfCreate> wfCreates = new();
    readonly List<WfLiteral> wfLits = new();
    readonly List<Unread> wfUnread = new();
    readonly List<string> wfNotes = new();
    readonly HashSet<string> wfUnreadSeen = new(StringComparer.Ordinal);
    WfCtx wc = new();

    public void RunWorkflow()
    {
        foreach (var rec in Recs.Where(r => !r.Framework && r.Body != null && r.Reach != null).OrderBy(r => r.Id, StringComparer.Ordinal))
        {
            wc = new WfCtx { Route = rec.R.Method + " " + rec.R.PathTemplate };
            try { WfRoot(rec); }
            catch (Exception e) when (e is not OutOfMemoryException) { WfNote(rec.At, "workflow", "internal error reading " + wc.Route + ": " + e.GetType().Name); }
            wfLits.AddRange(wc.Lits.Values);
        }
        try { WfStatusFacts(); }
        catch (Exception e) when (e is not OutOfMemoryException) { wfUnread.Add(new Unread { File = P.File, Line = 1, What = "workflow", Reason = "internal error reading status writes: " + e.GetType().Name }); }
        string L(int n) => n.ToString("D6");
        D.ParamUses = wfUses.OrderBy(a => $"{a.Route}|{a.Source}|{a.Param}|{a.Table}|{a.Column}|{a.How}|{a.File}|{L(a.Line)}", StringComparer.Ordinal).Cast<object>().ToList();
        D.Creates = wfCreates.OrderBy(a => $"{a.Route}|{a.Table}|{a.File}|{L(a.Line)}", StringComparer.Ordinal).Cast<object>().ToList();
        D.ParamLiterals = wfLits.OrderBy(a => $"{a.Route}|{a.Source}|{a.Param}", StringComparer.Ordinal).Cast<object>().ToList();
        D.WorkflowUnread = wfUnread.OrderBy(u => u.File, StringComparer.Ordinal).ThenBy(u => u.Line).ThenBy(u => u.What + u.Reason, StringComparer.Ordinal).Cast<object>().ToList();
        D.WorkflowNotes = wfNotes.Distinct().OrderBy(n => n, StringComparer.Ordinal).ToList();
    }

    void WfNote(SyntaxNode at, string what, string reason)
    {
        var u = MakeUnread(at, what, reason, "workflow");
        u.Category = null;
        if (wfUnreadSeen.Add($"{u.File}|{u.Line}|{what}|{reason}")) wfUnread.Add(u);
    }

    static bool SymEq(ISymbol? a, ISymbol? b) => a != null && b != null && SymbolEqualityComparer.Default.Equals(a, b);

    // ---- the route: its request values are the seeds -------------------------------------------------------------------

    void WfRoot(RouteRec rec)
    {
        var seeds = new Dictionary<ISymbol, Taint>(SymbolEqualityComparer.Default);
        foreach (var xp in rec.R.Params.Where(p => p.Source is "path" or "query"))
        {
            var hp = rec.Params.FirstOrDefault(p => string.Equals(p.Name, xp.Name, StringComparison.OrdinalIgnoreCase) || AttrsOf(p).Any(a => AttrName(a) == "FromRoute" && ArgValue(a, 0, "Name") as string == xp.Name));
            if (hp != null) seeds[hp] = Taint.Of(new[] { xp.Source + ":" + xp.Name })!;
        }
        foreach (var p in rec.Params)
            if (!seeds.ContainsKey(p) && Strip(p.Type) is INamedTypeSymbol bt && IsProject(bt) && !IsSimple(p.Type) && !IsServiceType(p.Type) && !SpecialTypes.Contains(bt.Name))
            {
                var t = new Taint();
                foreach (var mp in ModelProps(bt)) t.Add("." + mp.Name, new[] { "body:" + Wire(mp.Name, rec.R.Style, mp) });
                seeds[p] = t;
            }
        WfVisit(rec.Body!, seeds, 0, rec.R.Handler?.Func ?? rec.Id);
    }

    /// <summary>Reads one body with its parameters carrying the arguments' origins: its environment settles, then it is scanned; the origins of its results.</summary>
    Taint? WfVisit(SyntaxNode body, Dictionary<ISymbol, Taint> seeds, int depth, string inName)
    {
        var key = inName + "|" + string.Join("|", seeds.OrderBy(s => s.Key.Name, StringComparer.Ordinal).Select(s => s.Key.Name + "=" + s.Value.Sig));
        if (wc.Memo.TryGetValue(key, out var done)) return done;
        if (!wc.Busy.Add(key)) return null;
        try
        {
            var f = new WfFrame { Env = new Dictionary<ISymbol, Taint>(seeds, SymbolEqualityComparer.Default), Body = body, Depth = depth, In = inName };
            for (int round = 0; round < 4; round++) if (!WfFlow(f)) break;
            WfScan(f);
            Taint? res = null;
            foreach (var r in Returns(body)) res = Taint.Join(res, WfEv(r, f));
            return wc.Memo[key] = res;
        }
        finally { wc.Busy.Remove(key); }
    }

    // ---- the flow of origins through one body --------------------------------------------------------------------------

    bool WfPut(WfFrame f, ISymbol? s, Taint? t, string field = "")
    {
        if (s == null || t == null || t.Count == 0) return false;
        if (!f.Env.TryGetValue(s, out var cur)) f.Env[s] = cur = new Taint();
        return field == "" ? cur.Merge(t) : cur.Add(field, t.All());
    }

    bool WfFlow(WfFrame f)
    {
        bool ch = false;
        foreach (var n in f.Body.DescendantNodesAndSelf(x => x is not LambdaExpressionSyntax and not LocalFunctionStatementSyntax))
            switch (n)
            {
                case VariableDeclaratorSyntax { Initializer.Value: { } init } vd:
                    ch |= WfPut(f, Sm(vd).GetDeclaredSymbol(vd), WfEv(init, f)); break;
                case AssignmentExpressionSyntax asg:
                    {
                        var t = WfEv(asg.Right, f);
                        ExpressionSyntax l = Unwrap(asg.Left);
                        string field = "";
                        if (l is MemberAccessExpressionSyntax lm) { field = "." + lm.Name.Identifier.Text; l = Unwrap(lm.Expression); }
                        while (l is MemberAccessExpressionSyntax lm2) l = Unwrap(lm2.Expression);
                        if (l is ElementAccessExpressionSyntax le) l = Unwrap(le.Expression);
                        if (l is IdentifierNameSyntax id && Sm(id).GetSymbolInfo(id).Symbol is ILocalSymbol or IParameterSymbol) ch |= WfPut(f, Sm(id).GetSymbolInfo(id).Symbol, t, field);
                        break;
                    }
                case ForEachStatementSyntax fe:
                    { var t = WfEv(fe.Expression, f); if (t != null) ch |= WfPut(f, Sm(fe).GetDeclaredSymbol(fe), Taint.Of(t.All())); break; }
                case IsPatternExpressionSyntax ip:
                    { var t = WfEv(ip.Expression, f); if (t != null) foreach (var d in ip.Pattern.DescendantNodesAndSelf().OfType<SingleVariableDesignationSyntax>()) ch |= WfPut(f, Sm(d).GetDeclaredSymbol(d), t); break; }
                case InvocationExpressionSyntax or BaseObjectCreationExpressionSyntax: WfEv((ExpressionSyntax)n, f); break;
            }
        return ch;
    }

    Taint? WfEv(ExpressionSyntax? e, WfFrame f)
    {
        switch (e)
        {
            case null: return null;
            case ParenthesizedExpressionSyntax p: return WfEv(p.Expression, f);
            case CastExpressionSyntax c: return WfEv(c.Expression, f);
            case AwaitExpressionSyntax a: return WfEv(a.Expression, f);
            case PostfixUnaryExpressionSyntax pu: return WfEv(pu.Operand, f);
            case AssignmentExpressionSyntax asg: return WfEv(asg.Right, f);
            case IdentifierNameSyntax id: return Sm(id).GetSymbolInfo(id).Symbol is { } s && s is ILocalSymbol or IParameterSymbol && f.Env.TryGetValue(s, out var t) ? t.Copy() : null;
            case MemberAccessExpressionSyntax ma: return WfEv(ma.Expression, f)?.Sel(ma.Name.Identifier.Text);
            case ConditionalAccessExpressionSyntax ca:
                { var t0 = WfEv(ca.Expression, f); return t0 == null ? null : ca.WhenNotNull is MemberBindingExpressionSyntax mb ? t0.Sel(mb.Name.Identifier.Text) : Taint.Of(t0.All()); }
            case BinaryExpressionSyntax b when b.Kind() is SyntaxKind.CoalesceExpression or SyntaxKind.AddExpression or SyntaxKind.SubtractExpression or SyntaxKind.MultiplyExpression or SyntaxKind.DivideExpression or SyntaxKind.ModuloExpression:
                return Taint.Join(WfEv(b.Left, f), WfEv(b.Right, f));
            case BinaryExpressionSyntax { RawKind: (int)SyntaxKind.AsExpression } b2: return WfEv(b2.Left, f);
            case ConditionalExpressionSyntax ce: return Taint.Join(WfEv(ce.WhenTrue, f), WfEv(ce.WhenFalse, f));
            case SwitchExpressionSyntax sw: return Taint.Join(sw.Arms.Select(a => WfEv(a.Expression, f)).ToArray());
            case InterpolatedStringExpressionSyntax ist: return Taint.Of(ist.Contents.OfType<InterpolationSyntax>().SelectMany(i => WfEv(i.Expression, f)?.All() ?? new SortedSet<string>()));
            case ElementAccessExpressionSyntax ea: { var t1 = WfEv(ea.Expression, f); return t1 == null ? null : Taint.Of(t1.All()); }
            case InvocationExpressionSyntax inv: return WfCall(inv, f);
            case BaseObjectCreationExpressionSyntax oc: return WfNew(oc, f);
            case AnonymousObjectCreationExpressionSyntax ao:
                {
                    var t2 = new Taint();
                    foreach (var i in ao.Initializers) t2.Add("." + (i.NameEquals?.Name.Identifier.Text ?? (i.Expression as MemberAccessExpressionSyntax)?.Name.Identifier.Text ?? ""), WfEv(i.Expression, f)?.All() ?? new SortedSet<string>());
                    return t2.Count == 0 ? null : t2;
                }
        }
        return null;
    }

    Taint? WfNew(BaseObjectCreationExpressionSyntax oc, WfFrame f)
    {
        var t = new Taint();
        var c = ResolveNew(oc);
        var type = Sm(oc).GetTypeInfo(oc).Type as INamedTypeSymbol;
        var args = oc.ArgumentList?.Arguments ?? default;
        for (int i = 0; i < args.Count; i++)
        {
            var ev = WfEv(args[i].Expression, f)?.All();
            if (ev == null || ev.Count == 0) continue;
            var ps = c.M?.Parameters;
            var p = args[i].NameColon != null ? ps?.FirstOrDefault(x => x.Name == args[i].NameColon!.Name.Identifier.Text) : ps != null && i < ps.Value.Length ? ps.Value[i] : null;
            var prop = p == null ? null : type?.GetMembers().OfType<IPropertySymbol>().FirstOrDefault(q => string.Equals(q.Name, p.Name, StringComparison.OrdinalIgnoreCase));
            t.Add(prop != null ? "." + prop.Name : "", ev);
        }
        foreach (var x in oc.Initializer?.Expressions ?? default)
        {
            if (x is AssignmentExpressionSyntax { Left: IdentifierNameSyntax li } a) t.Add("." + li.Identifier.Text, WfEv(a.Right, f)?.All() ?? new SortedSet<string>());
            else t.Add("", WfEv(x, f)?.All() ?? new SortedSet<string>());
        }
        return t.Count == 0 ? null : t;
    }

    Taint? WfCall(InvocationExpressionSyntax inv, WfFrame f)
    {
        var c = Resolve(inv);
        var args = inv.ArgumentList.Arguments;
        // a key accessor of the request-body wrapper reads one body member
        if (c.Project && c.M != null && !c.M.IsStatic && args.Count > 0 && ConstOf(args[0].Expression, null) is string key && IsKeyAccessor(c.M, 0) && IsBodyWrapper(c.M.ContainingType))
            return Taint.Of(new[] { "body:" + key });
        var recv = (inv.Expression as MemberAccessExpressionSyntax)?.Expression;
        if (ChainOf(inv) != null) { foreach (var a in args) WfEv(a.Expression, f); return null; } // a query: rows are not request values
        if (c.Project && (c.M != null || c.Ambiguous != null))
        {
            var given = new List<(IParameterSymbol P, Taint? T)>();
            if (c.M != null) foreach (var (p, e) in MapArgs(inv, c)) given.Add((p, WfEv(e, f)));
            else foreach (var a in args) WfEv(a.Expression, f);
            bool tainted = given.Any(g => g.T != null);
            if (c.M == null) { if (tainted) WfNote(inv, c.Name, "ambiguous call: " + string.Join(", ", c.Ambiguous!)); return null; }
            var target = DiTarget(c.M, out var why, out _);
            if (target == null) { if (tainted) WfNote(inv, Short(c.M), why ?? "no implementation"); return null; }
            var body = BodyOf(DeclOf(target));
            if (body == null) return null;
            if (f.Depth >= WfDepth) { if (tainted) WfNote(inv, Short(c.M), "request values pass beyond the depth of the walk"); return null; }
            var seeds = new Dictionary<ISymbol, Taint>(SymbolEqualityComparer.Default);
            foreach (var (p, t) in given)
            {
                var tp = target.Parameters.FirstOrDefault(x => x.Name == p.Name && (x.Ordinal == p.Ordinal || SymbolEqualityComparer.Default.Equals(x.Type, p.Type))) ?? (p.Ordinal < target.Parameters.Length ? target.Parameters[p.Ordinal] : null);
                if (tp != null && t != null) seeds[tp] = t;
            }
            return WfVisit(body, seeds, f.Depth + 1, Qual(target));
        }
        // outside the project: the value comes from the arguments and a tainted receiver; out arguments receive it
        var all = new SortedSet<string>(StringComparer.Ordinal);
        foreach (var a in args) all.UnionWith(WfEv(a.Expression, f)?.All() ?? new SortedSet<string>());
        if (recv != null && Sm(recv).GetSymbolInfo(recv).Symbol is not INamedTypeSymbol) all.UnionWith(WfEv(recv, f)?.All() ?? new SortedSet<string>());
        var res = Taint.Of(all);
        if (res != null)
            foreach (var a in args.Where(a => a.RefKindKeyword.IsKind(SyntaxKind.OutKeyword)))
                WfPut(f, a.Expression is DeclarationExpressionSyntax { Designation: SingleVariableDesignationSyntax d } ? Sm(d).GetDeclaredSymbol(d) : Sm(a.Expression).GetSymbolInfo(a.Expression).Symbol, res);
        return res;
    }

    // ---- the scan: uses, creates, literals -----------------------------------------------------------------------------

    static IEnumerable<string> WfOrigins(Taint? t, params string[] kinds) => t == null ? [] : t.All().Where(o => kinds.Contains(o[..o.IndexOf(':')]));

    void WfUseOf(WfFrame f, string origin, INamedTypeSymbol t, string prop, string how, SyntaxNode at)
    {
        if (!Entities.TryGetValue(t, out var ei) || PropOf(ei, prop)?.Col is not { } col) return;
        var i = origin.IndexOf(':');
        var u = new WfUse { Route = wc.Route, Param = origin[(i + 1)..], Source = origin[..i], Table = TableNameOf(ei), Model = t.Name, Column = col.Name, How = how, File = FileOf(at), Line = LineOf(at), In = f.In };
        if (wc.UseSeen.Add($"{origin}|{u.Table}|{u.Column}|{how}|{u.File}|{u.Line}")) wfUses.Add(u);
    }

    void WfCond(ExpressionSyntax cond, string param, INamedTypeSymbol t, WfFrame f)
    {
        foreach (var term in Conjuncts(cond))
            if (Unwrap(term) is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.EqualsExpression } b)
            {
                var cr = ColumnRef(b.Left, param, t); var other = b.Right;
                if (cr == null) { cr = ColumnRef(b.Right, param, t); other = b.Left; }
                if (cr is { Nav: null } c) foreach (var o in WfOrigins(WfEv(other, f), "path", "body")) WfUseOf(f, o, c.Entity, c.Prop, "compare", b);
            }
    }

    void WfScan(WfFrame f)
    {
        foreach (var n in f.Body.DescendantNodesAndSelf())
            switch (n)
            {
                case InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax ma } inv: WfScanCall(inv, ma, f); break;
                case QueryExpressionSyntax q:
                    {
                        var vars = new List<(string Name, INamedTypeSymbol T)>();
                        if (ChainOf(q.FromClause.Expression) is { T: { } ft }) vars.Add((q.FromClause.Identifier.Text, ft));
                        foreach (var j in q.Body.Clauses.OfType<JoinClauseSyntax>()) if (ChainOf(j.InExpression) is { T: { } jt }) vars.Add((j.Identifier.Text, jt));
                        foreach (var w in q.Body.Clauses.OfType<WhereClauseSyntax>()) foreach (var (nm, t) in vars) WfCond(w.Condition, nm, t, f);
                        break;
                    }
                case AssignmentExpressionSyntax asg when asg.IsKind(SyntaxKind.SimpleAssignmentExpression) && ColumnRef(asg.Left, null, null) is { Nav: null } cr:
                    foreach (var o in WfOrigins(WfEv(asg.Right, f), "path", "body")) WfUseOf(f, o, cr.Entity, cr.Prop, "assign", asg);
                    break;
                case BaseObjectCreationExpressionSyntax { Initializer: { } init } oc when Sm(oc).GetTypeInfo(oc).Type is INamedTypeSymbol et && Entities.ContainsKey(et):
                    foreach (var a in init.Expressions.OfType<AssignmentExpressionSyntax>().Where(a => a.Left is IdentifierNameSyntax))
                        foreach (var o in WfOrigins(WfEv(a.Right, f), "path", "body")) WfUseOf(f, o, et, ((IdentifierNameSyntax)a.Left).Identifier.Text, "assign", a);
                    break;
                case BinaryExpressionSyntax b when b.IsKind(SyntaxKind.EqualsExpression) || b.IsKind(SyntaxKind.NotEqualsExpression):
                    foreach (var (x, y) in new[] { (b.Left, b.Right), (b.Right, b.Left) })
                        if (ConstOf(y, null) is string s) foreach (var o in WfOrigins(WfEv(x, f), "path", "body", "query")) WfLit(f, o, s, b);
                    break;
                case IsPatternExpressionSyntax ip: WfPatternLits(ip.Expression, new[] { ip.Pattern }, f); break;
                case SwitchExpressionSyntax sw: WfPatternLits(sw.GoverningExpression, sw.Arms.Select(a => a.Pattern), f); break;
                case SwitchStatementSyntax ss:
                    WfPatternLits(ss.Expression, ss.Sections.SelectMany(s => s.Labels).Select(l => l switch { CasePatternSwitchLabelSyntax cp => (SyntaxNode)cp.Pattern, CaseSwitchLabelSyntax cl => cl.Value, _ => l }), f);
                    break;
            }
    }

    void WfPatternLits(ExpressionSyntax subject, IEnumerable<SyntaxNode> pats, WfFrame f)
    {
        var os = WfOrigins(WfEv(subject, f), "path", "body", "query").ToList();
        if (os.Count == 0) return;
        foreach (var p in pats)
            foreach (var x in p is ExpressionSyntax ex ? new[] { ex } : p.DescendantNodesAndSelf().OfType<ConstantPatternSyntax>().Select(c => c.Expression))
                if (ConstOf(x, null) is string s) foreach (var o in os) WfLit(f, o, s, x);
    }

    void WfLit(WfFrame f, string origin, string value, SyntaxNode at)
    {
        if (value == "") return; // `x == ""` tests emptiness; it names no state a value can take
        var i = origin.IndexOf(':');
        var key = origin;
        if (!wc.Lits.TryGetValue(key, out var l)) wc.Lits[key] = l = new WfLiteral { Route = wc.Route, Param = origin[(i + 1)..], Source = origin[..i], File = FileOf(at), Line = LineOf(at) };
        if (!l.Values.Contains(value)) l.Values.Add(value);
    }

    void WfScanCall(InvocationExpressionSyntax inv, MemberAccessExpressionSyntax ma, WfFrame f)
    {
        var name = InvokedName(inv.Expression) ?? "";
        var args = inv.ArgumentList.Arguments;
        if (PredicateOps.Contains(name))
        {
            var lam = args.Select(a => a.Expression).OfType<LambdaExpressionSyntax>().FirstOrDefault();
            if (lam == null || ChainOf(ma.Expression)?.T is not { } t) return;
            var pname = lam is SimpleLambdaExpressionSyntax sl ? sl.Parameter.Identifier.Text : ((ParenthesizedLambdaExpressionSyntax)lam).ParameterList.Parameters.FirstOrDefault()?.Identifier.Text;
            var body = lam is SimpleLambdaExpressionSyntax s2 ? s2.Body : ((ParenthesizedLambdaExpressionSyntax)lam).Body;
            if (pname != null && body is ExpressionSyntax be) WfCond(be, pname, t, f);
        }
        else if (name is "Find" or "FindAsync" && args.Count > 0 && ChainOf(ma.Expression)?.T is { } ft && Entities.TryGetValue(ft, out var fe) && fe.Props.Where(p => p.Col.PrimaryKey).ToList() is [var pk])
            foreach (var o in WfOrigins(WfEv(args[0].Expression, f), "path", "body")) WfUseOf(f, o, ft, pk.P.Name, "compare", inv);
        else if (name == "SetProperty" && args.Count == 2 && LambdaProps(args[0].Expression).FirstOrDefault() is { } sp && inv.Ancestors().OfType<InvocationExpressionSyntax>().FirstOrDefault(i => InvokedName(i.Expression) is "ExecuteUpdate" or "ExecuteUpdateAsync")?.Expression is MemberAccessExpressionSyntax uma && ChainOf(uma.Expression)?.T is { } st)
            foreach (var o in WfOrigins(WfEv(args[1].Expression, f), "path", "body")) WfUseOf(f, o, st, sp, "assign", inv);
        else if (name is "Add" or "AddAsync" or "AddRange" or "AddRangeAsync" && args.Count > 0) WfAdd(inv, ma, f);
    }

    /// <summary>`db.T.Add(x)` / `db.Add(x)` of a new entity: its table and where its key comes from (a body member, else the server).</summary>
    void WfAdd(InvocationExpressionSyntax inv, MemberAccessExpressionSyntax ma, WfFrame f)
    {
        var arg = Unwrap(inv.ArgumentList.Arguments[0].Expression);
        var t = ChainOf(ma.Expression)?.T;
        if (t == null && Sm(ma.Expression).GetTypeInfo(ma.Expression).Type is INamedTypeSymbol rt && IsDbContextType(rt)) t = EntityOfLocal(arg) ?? Sm(arg).GetTypeInfo(arg).Type as INamedTypeSymbol;
        if (t == null || !Entities.TryGetValue(t, out var ei)) return;
        var pks = ei.Props.Where(p => p.Col.PrimaryKey).ToList();
        var keyProp = pks.Count == 1 ? pks[0].P.Name : null;
        var values = new List<ExpressionSyntax>();
        void FromCreation(BaseObjectCreationExpressionSyntax oc) => values.AddRange(oc.Initializer?.Expressions.OfType<AssignmentExpressionSyntax>().Where(a => a.Left is IdentifierNameSyntax li && li.Identifier.Text == keyProp).Select(a => a.Right) ?? []);
        if (arg is BaseObjectCreationExpressionSyntax oc0) FromCreation(oc0);
        else if (arg is IdentifierNameSyntax lid && Sm(lid).GetSymbolInfo(lid).Symbol is ILocalSymbol ls)
            foreach (var n in f.Body.DescendantNodes())
                switch (n)
                {
                    case VariableDeclaratorSyntax { Initializer.Value: BaseObjectCreationExpressionSyntax c1 } vd when SymEq(Sm(vd).GetDeclaredSymbol(vd), ls): FromCreation(c1); break;
                    case AssignmentExpressionSyntax { Left: IdentifierNameSyntax li, Right: BaseObjectCreationExpressionSyntax c2 } when SymEq(Sm(li).GetSymbolInfo(li).Symbol, ls): FromCreation(c2); break;
                    case AssignmentExpressionSyntax { Left: MemberAccessExpressionSyntax { Expression: IdentifierNameSyntax ri } lm } a3 when lm.Name.Identifier.Text == keyProp && SymEq(Sm(ri).GetSymbolInfo(ri).Symbol, ls): values.Add(a3.Right); break;
                }
        var bodies = values.SelectMany(v => WfOrigins(WfEv(v, f), "body")).Distinct().OrderBy(x => x, StringComparer.Ordinal).ToList();
        var c = new WfCreate { Route = wc.Route, Table = TableNameOf(ei), Model = t.Name, Key = keyProp == null ? "" : pks[0].Col.Name, KeyFrom = bodies.Count > 0 ? bodies[0][5..] : "server", File = FileOf(inv), Line = LineOf(inv), In = f.In };
        if (bodies.Count > 1) WfNote(inv, "create", $"the key of {c.Table} comes from {bodies.Count} body fields: {string.Join(", ", bodies)}");
        if (wc.CreateSeen.Add($"{c.Table}|{c.File}|{c.Line}")) wfCreates.Add(c);
    }

    // ---- status facts: create or update, and the states a write is made from ------------------------------------------

    static bool WfCreatedLocal(SyntaxNode asg, ILocalSymbol ls, Ex ex) =>
        asg.FirstAncestorOrSelf<BaseMethodDeclarationSyntax>() is { } m && m.DescendantNodes().Any(n =>
            n is VariableDeclaratorSyntax { Initializer.Value: BaseObjectCreationExpressionSyntax } vd && SymEq(ex.Sm(vd).GetDeclaredSymbol(vd), ls)
            || n is AssignmentExpressionSyntax { Left: IdentifierNameSyntax li, Right: BaseObjectCreationExpressionSyntax } && SymEq(ex.Sm(li).GetSymbolInfo(li).Symbol, ls)
            || n is InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax } ai && InvokedName(ai.Expression) is "Add" or "AddAsync" && ai.ArgumentList.Arguments.Count == 1 && ai.ArgumentList.Arguments[0].Expression is IdentifierNameSyntax ai0 && SymEq(ex.Sm(ai0).GetSymbolInfo(ai0).Symbol, ls));

    /// <summary>The states a status write is made from, read from the loop or the test around it: `foreach (var n in rows.Where(n => n.Col == A))`, `if (n.Col == A)`.</summary>
    List<(string Value, int Line, string File)> WfFromStates(AssignmentExpressionSyntax asg, INamedTypeSymbol t, string prop, XColumn col)
    {
        var res = new List<(string, int, string)>();
        var dummy = new Visit { Body = asg };
        void Add(ExpressionSyntax other, SyntaxNode at) { if (StateConst(other, dummy, col) is { } sc) res.Add((sc.Value, LineOf(at), FileOf(at))); }
        var recv = Unwrap(((MemberAccessExpressionSyntax)Unwrap(asg.Left)).Expression) as IdentifierNameSyntax;
        foreach (var anc in asg.Ancestors())
        {
            if (anc is ForEachStatementSyntax fe && recv != null && fe.Identifier.Text == recv.Identifier.Text)
                foreach (var w in fe.Expression.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>().Where(i => InvokedName(i.Expression) == "Where" && i.ArgumentList.Arguments.FirstOrDefault()?.Expression is LambdaExpressionSyntax))
                {
                    var lam = w.ArgumentList.Arguments[0].Expression;
                    var pn = lam is SimpleLambdaExpressionSyntax sl ? sl.Parameter.Identifier.Text : ((ParenthesizedLambdaExpressionSyntax)lam).ParameterList.Parameters.FirstOrDefault()?.Identifier.Text;
                    var body = lam is SimpleLambdaExpressionSyntax s2 ? s2.Body : ((ParenthesizedLambdaExpressionSyntax)lam).Body;
                    if (pn != null && body is ExpressionSyntax be)
                        foreach (var term in Conjuncts(be))
                            if (Unwrap(term) is BinaryExpressionSyntax b && b.IsKind(SyntaxKind.EqualsExpression))
                            {
                                var cr = ColumnRef(b.Left, pn, t) ?? ColumnRef(b.Right, pn, t);
                                if (cr is { Nav: null } c && c.Prop == prop) Add(ColumnRef(b.Left, pn, t) != null ? b.Right : b.Left, b);
                            }
                }
            if (anc is IfStatementSyntax ifs && ifs.Statement.Span.Contains(asg.Span))
                foreach (var term in Conjuncts(ifs.Condition))
                    if (Unwrap(term) is BinaryExpressionSyntax b && b.IsKind(SyntaxKind.EqualsExpression))
                    {
                        var cr = ColumnRef(b.Left, null, null) ?? ColumnRef(b.Right, null, null);
                        if (cr is { Nav: null } c && c.Prop == prop) Add(ColumnRef(b.Left, null, null) != null ? b.Right : b.Left, b);
                    }
        }
        return res;
    }

    /// <summary>The tuple literals a value can be (a conditional of tuples gives several); null when any alternative is not a literal.</summary>
    static List<TupleExpressionSyntax>? WfTuples(ExpressionSyntax e) => Unwrap(e) switch
    {
        TupleExpressionSyntax t => new() { t },
        ConditionalExpressionSyntax c when WfTuples(c.WhenTrue) is { } a && WfTuples(c.WhenFalse) is { } b => a.Concat(b).ToList(),
        _ => null,
    };

    /// <summary>`outcome = (Deliveries.Sent, null); row.Delivery = outcome.delivery`: the writes B1 listed as non-constant, resolved through the tuple's constants.</summary>
    void WfTupleWrites()
    {
        var reached = Recs.Where(r => r.Reach != null).SelectMany(r => r.Reach!.Visits.Select(v => (v.In, r.Key))).GroupBy(x => x.In).ToDictionary(g => g.Key, g => g.Select(x => x.Key).OrderBy(k => k, StringComparer.Ordinal).First(), StringComparer.Ordinal);
        foreach (var (t, prop, at, _) in nonConst.DistinctBy(n => (n.At.SyntaxTree.FilePath, n.At.SpanStart)).ToList())
        {
            if (at is not AssignmentExpressionSyntax asg || Unwrap(asg.Right) is not MemberAccessExpressionSyntax tm || Unwrap(tm.Expression) is not IdentifierNameSyntax rid || Sm(rid).GetSymbolInfo(rid).Symbol is not ILocalSymbol ls || ls.Type is not INamedTypeSymbol { IsTupleType: true } tt) continue;
            if (!Entities.TryGetValue(t, out var ei) || PropOf(ei, prop)?.Col is not { } col || (col.EnumStorage == null && col.Type != "Str") || asg.FirstAncestorOrSelf<BaseMethodDeclarationSyntax>() is not { } method) continue;
            var nm = tm.Name.Identifier.Text;
            var idx = tt.TupleElements.ToList().FindIndex(x => x.Name == nm);
            if (idx < 0 && nm.StartsWith("Item", StringComparison.Ordinal) && int.TryParse(nm[4..], out var k)) idx = k - 1;
            var dummy = new Visit { Body = asg };
            var consts = new List<(string Value, string Stored)>(); bool known = idx >= 0;
            foreach (var n in method.DescendantNodes())
            {
                ExpressionSyntax? rhs = n switch { AssignmentExpressionSyntax { Left: IdentifierNameSyntax li } a when SymEq(Sm(li).GetSymbolInfo(li).Symbol, ls) => a.Right, VariableDeclaratorSyntax vd when SymEq(Sm(vd).GetDeclaredSymbol(vd), ls) => vd.Initializer?.Value, _ => null };
                if (rhs == null) continue;
                var tups = WfTuples(rhs);
                if (tups == null) known = false;
                else foreach (var te in tups) { if (idx >= 0 && idx < te.Arguments.Count && StateConst(te.Arguments[idx].Expression, dummy, col) is { } sc) consts.Add(sc); else known = false; }
            }
            if (!known || consts.Count == 0) continue;
            var inName = Sm(asg).GetEnclosingSymbol(asg.SpanStart) is IMethodSymbol em ? Qual(em) : "";
            var from = WfFromStates(asg, t, prop, col);
            var root = reached.TryGetValue(inName, out var rk) ? rk : "(not reached from a route)";
            foreach (var sc in consts.Distinct())
            {
                var w = new XStatusW { Table = TableNameOf(ei), Model = t.Name, Column = col.Name, Value = sc.Value, Root = root, Route = root, File = FileOf(asg), Line = LineOf(asg), ValueLine = LineOf(asg), In = inName, Stored = sc.Stored, Kind = "assign", From = from.Select(x => x.Value).Distinct().ToList() };
                if (D.StatusWrites.Any(x => x.Model == w.Model && x.Column == w.Column && x.Value == w.Value && x.File == w.File && x.Line == w.Line)) continue;
                D.StatusWrites.Add(w);
                facts.Add(new XStatusFact { Table = w.Table, Model = w.Model, Column = w.Column, File = w.File, Line = w.Line, Kind = "assign", From = w.From, FromAt = from.Select(x => new Loc { File = x.File, Line = x.Line }).Distinct().ToList(), In = inName, Route = root, To = sc.Value });
            }
            wfNotes.Add($"{t.Name}.{prop}: writes through a tuple local resolved from its assigned constants ({FileOf(asg)}:{LineOf(asg)})");
        }
        D.StatusWrites = D.StatusWrites.OrderBy(w => w.File, StringComparer.Ordinal).ThenBy(w => w.Line).ThenBy(w => w.Value, StringComparer.Ordinal).ThenBy(w => w.Root, StringComparer.Ordinal).ToList();
    }

    void WfStatusFacts()
    {
        WfTupleWrites();
        var sites = D.StatusWrites.Select(w => (w.File, w.Line, w.Column)).ToHashSet();
        var nvalues = D.StatusWrites.GroupBy(w => w.Table + "." + w.Column).ToDictionary(g => g.Key, g => g.Select(w => w.Value).Distinct().Count());
        var outFacts = new List<WfFact>();
        foreach (var g in facts.Where(x => sites.Contains((x.File, x.Line, x.Column))).GroupBy(x => (x.File, x.Line, x.Column)))
        {
            var x0 = g.First();
            bool create = g.Any(x => x.Kind == "initial");
            if (!create && g.All(x => x.Kind == "assign") && Trees.FirstOrDefault(t => t.FilePath == x0.File)?.GetRoot().DescendantNodes().OfType<AssignmentExpressionSyntax>().FirstOrDefault(a => LineOf(a) == x0.Line && Unwrap(a.Left) is MemberAccessExpressionSyntax { Expression: IdentifierNameSyntax }) is { } asg
                && Unwrap(((MemberAccessExpressionSyntax)Unwrap(asg.Left)).Expression) is IdentifierNameSyntax ri && Sm(ri).GetSymbolInfo(ri).Symbol is ILocalSymbol rl) create = WfCreatedLocal(asg, rl, this);
            var f = new WfFact { Table = x0.Table, Model = x0.Model, Column = x0.Column, File = x0.File, Line = x0.Line, Kind = create ? "create" : "update", In = x0.In };
            if (!create)
            {
                f.From = g.SelectMany(x => x.From).Distinct().ToList();
                f.FromAt = g.SelectMany(x => x.FromAt).Distinct().ToList();
                if (f.From.Count == 0)
                {
                    f.From = new() { "*" };
                    if (nvalues[x0.Table + "." + x0.Column] >= 2) wfUnread.Add(new Unread { File = x0.File, Line = x0.Line, What = "status_write", Reason = $"the state {x0.Table}.{x0.Column} is written from is not read: an update with no enclosing test of the column and no query pinning it" });
                }
            }
            outFacts.Add(f);
        }
        D.StatusFacts = outFacts.OrderBy(a => a.File, StringComparer.Ordinal).ThenBy(a => a.Line).ThenBy(a => a.Column, StringComparer.Ordinal).Cast<object>().ToList();
    }
}
