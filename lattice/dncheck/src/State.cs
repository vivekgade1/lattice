// Status and state writes (N7): constants written to a column of a tracked entity (assignment, object initializer,
// ExecuteUpdate/SetProperty) with the guard that decides which states a write may come from.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed class XStatusFact
{
    public string Table { get; set; } = "";
    public string Model { get; set; } = "";
    public string Column { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string Kind { get; set; } = "assign";
    public List<string> From { get; set; } = new();
    public List<Loc> FromAt { get; set; } = new();
    public string In { get; set; } = "";
    public string Route { get; set; } = "";
    public string To { get; set; } = "";
}

public sealed partial class Ex
{
    readonly Dictionary<(INamedTypeSymbol, string), List<string>> writtenValues = new();
    readonly HashSet<(INamedTypeSymbol, string)> guardedCols = new();
    readonly List<XStatusFact> facts = new();
    readonly List<(INamedTypeSymbol T, string Prop, SyntaxNode At, string Expr)> nonConst = new();

    /// <summary>A written constant: its name (enum member) or text, and what the database stores.</summary>
    (string Value, string Stored)? StateConst(ExpressionSyntax e, Visit v, XColumn col)
    {
        e = Unwrap(e);
        var sm = Sm(e);
        var sym = e is MemberAccessExpressionSyntax or IdentifierNameSyntax ? sm.GetSymbolInfo(e).Symbol : null;
        if (sym is IFieldSymbol { ContainingType.TypeKind: TypeKind.Enum, HasConstantValue: true } ef)
            return (ef.Name, col.EnumStorage == "string" ? ef.Name : Js.Inv(ef.ConstantValue));
        var cv = ConstOf(e, v);
        if (cv is string s) return (s, s);
        if (cv is int or long or short or byte or bool) return (Js.Inv(cv is bool b ? (b ? "true" : "false") : cv), Js.Inv(cv is bool b2 ? (b2 ? "true" : "false") : cv));
        return null;
    }

    /// <summary>Every constant a value expression can be: a conditional or a switch expression of constants gives several.</summary>
    List<(string Value, string Stored)>? StateConsts(ExpressionSyntax e, Visit v, XColumn col)
    {
        e = Unwrap(e);
        if (e is ConditionalExpressionSyntax c) { var a = StateConsts(c.WhenTrue, v, col); var b = StateConsts(c.WhenFalse, v, col); return a == null || b == null ? null : a.Concat(b).Distinct().ToList(); }
        if (e is SwitchExpressionSyntax sw) { var all = new List<(string, string)>(); foreach (var arm in sw.Arms) { var x = StateConsts(arm.Expression, v, col); if (x == null) return null; all.AddRange(x); } return all.Distinct().ToList(); }
        return StateConst(e, v, col) is { } one ? new() { one } : null;
    }

    /// <summary>The states a write may come from, read from a dominating refusal (`if (x.Col != A) return 409`) or a Where on the column.</summary>
    (List<string>? From, GuardInfo? Info, string Text) GuardFor(SyntaxNode write, string local, string prop, Visit v, XColumn col, INamedTypeSymbol t)
    {
        foreach (var ifs in v.Body.DescendantNodes().OfType<IfStatementSyntax>().Where(i => i.Span.End <= write.SpanStart).OrderByDescending(i => i.SpanStart))
        {
            if (!Refuses(ifs.Statement) || ifs.Else != null) continue;
            var cond = Unwrap(ifs.Condition);
            var from = new List<string>(); var forbidden = new List<string>(); bool ok = true;
            foreach (var term in Conjuncts(cond))
            {
                if (Unwrap(term) is BinaryExpressionSyntax b && (b.IsKind(SyntaxKind.NotEqualsExpression) || b.IsKind(SyntaxKind.EqualsExpression)))
                {
                    var cr = ColumnRef(b.Left, null, null) ?? ColumnRef(b.Right, null, null);
                    var other = ColumnRef(b.Left, null, null) != null ? b.Right : b.Left;
                    if (cr is { Nav: null } && cr.Value.Prop == prop && Unwrap(other) is var oe && StateConst(oe, v, col) is { } sc)
                    { if (b.IsKind(SyntaxKind.NotEqualsExpression)) from.Add(sc.Value); else forbidden.Add(sc.Value); continue; }
                }
                ok = false;
            }
            if (!ok || (from.Count == 0 && forbidden.Count == 0)) continue;
            if (from.Count == 0 && col.Choices != null) from = col.Choices.Where(c => !forbidden.Contains(c)).ToList();
            else if (from.Count == 0) continue;
            guardedCols.Add((t, prop));
            return (from, new GuardInfo { StatusIfNot = RefusalStatus(ifs.Statement, v), File = FileOf(ifs), Line = LineOf(ifs) }, Js.Text(ifs.Condition));
        }
        return (null, null, "");
    }

    bool Refuses(StatementSyntax s) => s is ReturnStatementSyntax or ThrowStatementSyntax || s is BlockSyntax b && b.Statements.Count > 0 && Refuses(b.Statements[^1]);

    int? RefusalStatus(StatementSyntax s, Visit v)
    {
        foreach (var inv in s.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>())
        {
            var c = Resolve(inv);
            if (FactoryStatus.TryGetValue(c.Name, out var st) && !c.Project) return st;
            if (c.Name == "StatusCode" && inv.ArgumentList.Arguments.Count > 0 && ConstOf(inv.ArgumentList.Arguments[0].Expression, v) is int s2) return s2;
        }
        foreach (var th in s.DescendantNodesAndSelf().OfType<ThrowStatementSyntax>()) if (ErrorOf(th.Expression, v)?.Status is { } es) return es;
        return null;
    }

    void ScanState(RouteRec rec) => ScanStateIn(rec.Reach!, rec.Key, rec.Id);

    void ScanStateIn(Reach reach, string rootKey, string routeId)
    {
        var seen = new HashSet<string>(StringComparer.Ordinal);
        void Emit(Visit v, INamedTypeSymbol t, string prop, SyntaxNode at, ExpressionSyntax value, string kind, string local, List<string>? from, GuardInfo? gi, string gtext)
        {
            var ei = Entities[t]; var pi = PropOf(ei, prop)!; var col = pi.Col;
            if (col.EnumStorage == null && col.Type != "Str") return;
            var scs = StateConsts(value, v, col);
            if (scs == null) { nonConst.Add((t, prop, at, Js.Text(value))); return; }
            foreach (var sc in scs) EmitOne(sc);
            void EmitOne((string Value, string Stored) sc)
            {
                var w = new XStatusW { Table = TableNameOf(ei), Model = t.Name, Column = col.Name, Value = sc.Value, Root = rootKey, Route = routeId, File = FileOf(at), Line = LineOf(at), ValueLine = LineOf(value), Guard = gtext, In = v.In, Stored = sc.Stored, Kind = kind, From = from, GuardInfo = gi };
                if (!seen.Add($"{w.Model}|{w.Column}|{w.Value}|{w.File}|{w.Line}|{w.Kind}")) return;
                D.StatusWrites.Add(w);
                var vals = writtenValues.TryGetValue((t, prop), out var l) ? l : writtenValues[(t, prop)] = new();
                vals.Add(sc.Value);
                facts.Add(new XStatusFact { Table = w.Table, Model = t.Name, Column = col.Name, File = w.File, Line = w.Line, Kind = kind, From = from?.ToList() ?? new(), FromAt = gi != null ? new() { new Loc { File = gi.File, Line = gi.Line } } : new(), In = v.In, Route = routeId, To = sc.Value });
            }
        }
        foreach (var v in reach.Visits)
            foreach (var node in v.Body.DescendantNodesAndSelf(n => v.Skip == null || !v.Skip.Contains(n)))
            {
                switch (node)
                {
                    case AssignmentExpressionSyntax asg when asg.IsKind(SyntaxKind.SimpleAssignmentExpression) && ColumnRef(asg.Left, null, null) is { Nav: null } cr:
                        {
                            var (from, gi, gt) = GuardFor(asg, ((IdentifierNameSyntax)Unwrap(((MemberAccessExpressionSyntax)Unwrap(asg.Left)).Expression)).Identifier.Text, cr.Prop, v, PropOf(Entities[cr.Entity], cr.Prop)!.Col, cr.Entity);
                            Emit(v, cr.Entity, cr.Prop, asg, asg.Right, "assign", "", from, gi, gt);
                            break;
                        }
                    case BaseObjectCreationExpressionSyntax oc when oc.Initializer != null && Sm(oc).GetTypeInfo(oc).Type is INamedTypeSymbol et && Entities.ContainsKey(et):
                        foreach (var a in oc.Initializer.Expressions.OfType<AssignmentExpressionSyntax>())
                            if (a.Left is IdentifierNameSyntax idn && PropOf(Entities[et], idn.Identifier.Text) is { } pi && pi.Col.EnumStorage != null || a.Left is IdentifierNameSyntax idn2 && PropOf(Entities[et], idn2.Identifier.Text) is { } pj && Js.Inv(pj.Col.Type) == "Str")
                                Emit(v, et, ((IdentifierNameSyntax)a.Left).Identifier.Text, a, a.Right, "initial", "", null, null, "");
                        break;
                    case InvocationExpressionSyntax inv when InvokedName(inv.Expression) is "SetProperty" && inv.ArgumentList.Arguments.Count == 2 && inv.Expression is MemberAccessExpressionSyntax:
                        {
                            var lamProp = LambdaProps(inv.ArgumentList.Arguments[0].Expression).FirstOrDefault();
                            var up = inv.Ancestors().OfType<InvocationExpressionSyntax>().FirstOrDefault(i => InvokedName(i.Expression) is "ExecuteUpdate" or "ExecuteUpdateAsync");
                            if (lamProp == null || up?.Expression is not MemberAccessExpressionSyntax uma) break;
                            var ch = ChainOf(uma.Expression);
                            if (ch?.T == null || PropOf(Entities[ch.T], lamProp) is not { } pi2) break;
                            var (from, gi, gt) = WhereGuard(uma.Expression, lamProp, pi2.Col, ch.T, v);
                            Emit(v, ch.T, lamProp, inv, inv.ArgumentList.Arguments[1].Expression, "bulk", "", from, gi, gt);
                            break;
                        }
                }
            }
    }

    /// <summary>State writes in code no route reaches (a background worker, a hosted service): rooted at "(not reached from a route)".</summary>
    void ScanStateOutsideRoutes()
    {
        var reached = new HashSet<string>(Recs.SelectMany(r => r.Reach!.Visits.Select(v => v.In)), StringComparer.Ordinal);
        var handlers = Recs.Select(r => r.Handler).OfType<SyntaxNode>().ToHashSet();
        foreach (var m in MethodsByName.Values.SelectMany(x => x).Where(IsProject).OrderBy(m => Qual(m), StringComparer.Ordinal).ThenBy(m => m.Locations.First().SourceSpan.Start))
        {
            if (reached.Contains(Qual(m)) || BodyOf(DeclOf(m)) is not { } body) continue;
            var reach = new Reach();
            reach.Visits.Add(new Visit { Body = body, Method = m, In = Qual(m), Skip = handlers });
            ScanStateIn(reach, "(not reached from a route)", "(not reached from a route)");
        }
    }

    (List<string>? From, GuardInfo? Info, string Text) WhereGuard(ExpressionSyntax chain, string prop, XColumn col, INamedTypeSymbol t, Visit v)
    {
        for (ExpressionSyntax? c = chain; c is InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax ma } inv; c = ma.Expression)
            if (InvokedName(inv.Expression) == "Where" && inv.ArgumentList.Arguments.FirstOrDefault()?.Expression is LambdaExpressionSyntax lam)
            {
                var pname = lam is SimpleLambdaExpressionSyntax sl ? sl.Parameter.Identifier.Text : ((ParenthesizedLambdaExpressionSyntax)lam).ParameterList.Parameters.FirstOrDefault()?.Identifier.Text;
                var body = lam is SimpleLambdaExpressionSyntax s2 ? s2.Body : ((ParenthesizedLambdaExpressionSyntax)lam).Body;
                if (pname == null || body is not ExpressionSyntax be) continue;
                foreach (var term in Conjuncts(be))
                    if (Unwrap(term) is BinaryExpressionSyntax b && b.IsKind(SyntaxKind.EqualsExpression))
                    {
                        var cr = ColumnRef(b.Left, pname, t) ?? ColumnRef(b.Right, pname, t);
                        var other = ColumnRef(b.Left, pname, t) != null ? b.Right : b.Left;
                        if (cr is { Nav: null } && cr.Value.Prop == prop && StateConst(other, v, col) is { } sc)
                        { guardedCols.Add((t, prop)); return (new List<string> { sc.Value }, new GuardInfo { Where = Js.Text(term), File = FileOf(term), Line = LineOf(term) }, Js.Text(term)); }
                    }
            }
        return (null, null, "");
    }

    /// <summary>Enum columns are status-like (and get `choices`) only when written with two distinct constants or guarded (the K5 rule).</summary>
    void FinishState()
    {
        foreach (var (t, prop, at, expr) in nonConst.DistinctBy(n => (n.At.SyntaxTree.FilePath, n.At.SpanStart)))
            if (writtenValues.ContainsKey((t, prop))) AddUnread(null, at, $"{t.Name}.{prop} write", $"value of {t.Name}.{prop} is not a constant: `{expr}`", "state");
        foreach (var ei in Entities.Values)
            foreach (var pi in ei.Props.Where(p => p.Col.Choices != null))
            {
                var key = (ei.T, pi.P.Name);
                bool status = writtenValues.TryGetValue(key, out var vals) && vals.Distinct().Count() >= 2 || guardedCols.Contains(key);
                if (!status) pi.Col.Choices = null;
            }
        D.StatusFacts = facts.OrderBy(f => f.File, StringComparer.Ordinal).ThenBy(f => f.Line).ThenBy(f => f.Route, StringComparer.Ordinal).Cast<object>().ToList();
    }
}
