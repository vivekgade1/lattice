// Ownership in LINQ (N8): where a request compares or assigns an entity's owner column with the caller -- lambda
// predicates joined by &&, query syntax, a row found then checked, object initializers, navigation chains (owner_via)
// and global query filters (applied to every query on the entity unless IgnoreQueryFilters() removes them).
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed partial class Ex
{
    static readonly HashSet<string> PredicateOps = new(StringComparer.Ordinal) { "Where", "First", "FirstOrDefault", "FirstAsync", "FirstOrDefaultAsync", "Single", "SingleOrDefault", "SingleAsync", "SingleOrDefaultAsync", "Any", "AnyAsync", "Count", "CountAsync", "LongCount", "LongCountAsync", "All", "AllAsync", "Last", "LastOrDefault", "LastAsync", "LastOrDefaultAsync" };

    static IEnumerable<ExpressionSyntax> Conjuncts(ExpressionSyntax e)
    {
        while (e is ParenthesizedExpressionSyntax p) e = p.Expression;
        if (e is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.LogicalAndExpression } b) { foreach (var x in Conjuncts(b.Left)) yield return x; foreach (var x in Conjuncts(b.Right)) yield return x; }
        else yield return e;
    }

    static ExpressionSyntax Unwrap(ExpressionSyntax e)
    {
        while (true)
        {
            if (e is ParenthesizedExpressionSyntax p) e = p.Expression;
            else if (e is PostfixUnaryExpressionSyntax { RawKind: (int)SyntaxKind.SuppressNullableWarningExpression } s) e = s.Operand;
            else if (e is CastExpressionSyntax c) e = c.Expression;
            else return e;
        }
    }

    /// <summary>`p.Col` or `p.Nav.Col` over the lambda parameter (or a typed local): (entity, property, navigation).</summary>
    (INamedTypeSymbol Entity, string Prop, string? Nav)? ColumnRef(ExpressionSyntax e, string? param, INamedTypeSymbol? paramType)
    {
        e = Unwrap(e);
        if (e is not MemberAccessExpressionSyntax ma) return null;
        var inner = Unwrap(ma.Expression);
        if (inner is IdentifierNameSyntax id)
        {
            INamedTypeSymbol? t = param != null && id.Identifier.Text == param ? paramType : param == null ? EntityOfLocal(id) : null;
            return t != null && Entities.TryGetValue(t, out var ei) && ei.Props.Any(p => p.P.Name == ma.Name.Identifier.Text) ? (t, ma.Name.Identifier.Text, null) : null;
        }
        if (inner is MemberAccessExpressionSyntax nav && Unwrap(nav.Expression) is IdentifierNameSyntax rid)
        {
            INamedTypeSymbol? t = param != null && rid.Identifier.Text == param ? paramType : param == null ? EntityOfLocal(rid) : null;
            if (t != null && Entities.TryGetValue(t, out var ei) && ei.Navs.TryGetValue(nav.Name.Identifier.Text, out var nt) && Entities.TryGetValue(nt.Target, out var pe) && pe.Props.Any(p => p.P.Name == ma.Name.Identifier.Text))
                return (t, ma.Name.Identifier.Text, nav.Name.Identifier.Text);
        }
        return null;
    }

    XOwner? MakeOwner(INamedTypeSymbol t, string prop, string? nav, string how, SyntaxNode at, ExpressionSyntax value, Visit v)
    {
        var ei = Entities[t];
        var o = new XOwner { Model = t.Name, Table = TableNameOf(ei), How = how, File = FileOf(at), Line = LineOf(at), Value = Js.Text(value) };
        if (nav == null)
        {
            var col = PropOf(ei, prop)?.Col;
            if (col == null) return null;
            o.Column = col.Name;
        }
        else
        {
            var via = OwnerVia(t, nav);
            if (via == null) return null;
            var pcol = PropOf(via.Value.Parent, prop)?.Col;
            if (pcol == null) return null;
            o.Column = pcol.Name;
            o.Via = new OwnerVia { Table = t.Name, Parent = via.Value.Parent.T.Name, Fk = via.Value.FkCol };
        }
        if (v.Di != null) { o.Via ??= new OwnerVia(); o.Via.Call = v.Di.Call; o.Via.ResolvedTo = v.Di.ResolvedTo; }
        return o;
    }

    void ScanOwners(RouteRec rec)
    {
        var r = rec.R;
        var found = new List<XOwner>();
        var queried = new Dictionary<INamedTypeSymbol, bool>(SymbolEqualityComparer.Default); // entity -> any query without IgnoreQueryFilters
        foreach (var v in rec.Reach!.Visits)
        {
            foreach (var node in v.Body.DescendantNodesAndSelf())
            {
                switch (node)
                {
                    case InvocationExpressionSyntax inv when inv.Expression is MemberAccessExpressionSyntax ma && PredicateOps.Contains(InvokedName(inv.Expression) ?? ""):
                        {
                            var lam = inv.ArgumentList.Arguments.Select(a => a.Expression).OfType<LambdaExpressionSyntax>().FirstOrDefault();
                            var ch = ChainOf(ma.Expression);
                            if (ch?.T != null && lam == null)
                            {
                                foreach (var arg in inv.ArgumentList.Arguments.Select(x => x.Expression))
                                    if (Sm(arg).GetTypeInfo(arg).Type is { } at && (at.Name is "Func" or "Expression" or "Predicate" || IsErr(at)) || Sm(arg).GetSymbolInfo(arg).Symbol is IMethodSymbol)
                                        AddUnread(rec.R.Unread, arg, $"{InvokedName(inv.Expression)}({Js.Text(arg)})", "predicate is not a lambda: its owner scope is not read", "owner", rec.Id);
                                break;
                            }
                            if (lam == null || ch?.T == null) break;
                            var pname = lam is SimpleLambdaExpressionSyntax sl ? sl.Parameter.Identifier.Text : (lam as ParenthesizedLambdaExpressionSyntax)?.ParameterList.Parameters.FirstOrDefault()?.Identifier.Text;
                            var body = lam is SimpleLambdaExpressionSyntax s2 ? s2.Body : ((ParenthesizedLambdaExpressionSyntax)lam).Body;
                            if (pname != null && body is ExpressionSyntax be) Terms(be, pname, ch.T, v, found, rec);
                            break;
                        }
                    case QueryExpressionSyntax q:
                        {
                            var vars = new List<(string Name, INamedTypeSymbol T)>();
                            if (ChainOf(q.FromClause.Expression) is { T: { } ft }) vars.Add((q.FromClause.Identifier.Text, ft));
                            foreach (var j in q.Body.Clauses.OfType<JoinClauseSyntax>())
                                if (ChainOf(j.InExpression) is { T: { } jt })
                                {
                                    vars.Add((j.Identifier.Text, jt));
                                    JoinVia(j, vars, v, q, found);
                                }
                            foreach (var w in q.Body.Clauses.OfType<WhereClauseSyntax>())
                                foreach (var (nm, t) in vars) Terms(w.Condition, nm, t, v, found, rec);
                            break;
                        }
                    case BinaryExpressionSyntax { RawKind: (int)SyntaxKind.EqualsExpression or (int)SyntaxKind.NotEqualsExpression } be when !InsideLambdaOfQuery(be):
                        {
                            var (l, rr) = (be.Left, be.Right);
                            var cr = ColumnRef(l, null, null); var other = rr;
                            if (cr == null) { cr = ColumnRef(rr, null, null); other = l; }
                            if (cr != null && IsCaller(other, v)) { var o = MakeOwner(cr.Value.Entity, cr.Value.Prop, cr.Value.Nav, "compare", be, other, v); if (o != null) found.Add(o); }
                            break;
                        }
                    case AssignmentExpressionSyntax asg when asg.IsKind(SyntaxKind.SimpleAssignmentExpression) && ColumnRef(asg.Left, null, null) is { Nav: null } cr2 && IsCaller(asg.Right, v):
                        {
                            var o = MakeOwner(cr2.Entity, cr2.Prop, null, "assign", asg, asg.Right, v);
                            if (o != null) found.Add(o);
                            break;
                        }
                    case ObjectCreationExpressionSyntax or ImplicitObjectCreationExpressionSyntax:
                        {
                            var init = (node as BaseObjectCreationExpressionSyntax)?.Initializer;
                            if (init == null || Sm(node).GetTypeInfo(node).Type is not INamedTypeSymbol et || !Entities.ContainsKey(et)) break;
                            foreach (var a in init.Expressions.OfType<AssignmentExpressionSyntax>())
                                if (a.Left is IdentifierNameSyntax idn && IsCaller(a.Right, v) && MakeOwner(et, idn.Identifier.Text, null, "assign", a, a.Right, v) is { } o) found.Add(o);
                            break;
                        }
                }
                // queries on a DbSet: which entities does the route read, and is the query filter in force
                if (node is MemberAccessExpressionSyntax or IdentifierNameSyntax && ChainRootEntity(node) is { } ent)
                {
                    var ignore = ChainIgnores(node);
                    queried[ent] = (queried.TryGetValue(ent, out var prev) && prev) || !ignore;
                }
            }
        }
        // global query filters
        foreach (var qf in D.Dotnet.QueryFilters.Where(q => q.Owner))
        {
            var ent = Entities.Keys.FirstOrDefault(k => k.Name == qf.Table);
            if (ent == null || !queried.TryGetValue(ent, out var inForce) || !inForce) continue;
            if (found.Any(o => o.Model == qf.Table && o.How is "compare" or "assign")) continue;
            var ei = Entities[ent];
            found.Add(new XOwner { Model = qf.Table, Table = TableNameOf(ei), Column = qf.Column, How = "filter", File = qf.File, Line = qf.Line, Value = qf.ReadsCallerVia != null ? qf.Expr.Split("==").Last().Trim() : qf.Expr });
            if (!qf.AppliesToRoutes.Contains(rec.Id)) qf.AppliesToRoutes.Add(rec.Id);
        }
        foreach (var kv in queried.Where(k => !k.Value))
            foreach (var qf in D.Dotnet.QueryFilters.Where(q => q.Table == kv.Key.Name))
                if (!qf.IgnoredBy.Contains(rec.Id)) qf.IgnoredBy.Add(rec.Id);
        foreach (var kv in queried) r.Tables.Add(TableNameOf(Entities[kv.Key]));
        r.OwnerUses = found.GroupBy(o => $"{o.Model}|{o.Column}|{o.How}|{o.File}|{o.Line}|{o.Via?.Call}").Select(g => g.First()).OrderBy(o => o.File, StringComparer.Ordinal).ThenBy(o => o.Line).ThenBy(o => o.How, StringComparer.Ordinal).ToList();
    }

    bool InsideLambdaOfQuery(SyntaxNode n) => n.Ancestors().OfType<LambdaExpressionSyntax>().Any(l => l.Parent is ArgumentSyntax { Parent: ArgumentListSyntax { Parent: InvocationExpressionSyntax i } } && (PredicateOps.Contains(InvokedName(i.Expression) ?? "") || InvokedName(i.Expression) == "HasQueryFilter")) || n.Ancestors().OfType<QueryExpressionSyntax>().Any();

    void Terms(ExpressionSyntax cond, string param, INamedTypeSymbol t, Visit v, List<XOwner> found, RouteRec rec)
    {
        foreach (var term in Conjuncts(cond))
        {
            var u = Unwrap(term);
            if (u is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.EqualsExpression } b)
            {
                var cr = ColumnRef(b.Left, param, t); var other = b.Right;
                if (cr == null) { cr = ColumnRef(b.Right, param, t); other = b.Left; }
                if (cr != null && IsCaller(other, v) && MakeOwner(cr.Value.Entity, cr.Value.Prop, cr.Value.Nav, "compare", b, other, v) is { } o) found.Add(o);
            }
            else if (u is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.LogicalOrExpression } or PrefixUnaryExpressionSyntax { RawKind: (int)SyntaxKind.LogicalNotExpression })
            {
                foreach (var inner in u.DescendantNodes().OfType<BinaryExpressionSyntax>().Where(x => x.IsKind(SyntaxKind.EqualsExpression) || x.IsKind(SyntaxKind.NotEqualsExpression)))
                {
                    var cr = ColumnRef(inner.Left, param, t) ?? ColumnRef(inner.Right, param, t);
                    var other = ColumnRef(inner.Left, param, t) != null ? inner.Right : inner.Left;
                    if (cr != null && IsCaller(other, v)) AddUnread(rec.R.Unread, inner, Js.Text(inner), "owner comparison under || or !: not read as a scope", "owner", rec.Id);
                }
            }
        }
    }

    /// <summary>`from c in C join p in P on c.Fk equals p.Id where p.Owner == caller`: the owner is reached through the parent (owner_via on C).</summary>
    void JoinVia(JoinClauseSyntax j, List<(string Name, INamedTypeSymbol T)> vars, Visit v, QueryExpressionSyntax q, List<XOwner> found)
    {
        var childRef = ColumnOnVar(j.LeftExpression, vars) ?? ColumnOnVar(j.RightExpression, vars);
        var parentRef = ColumnOnVar(j.RightExpression, vars) is { } pr && pr.Var == j.Identifier.Text ? pr : ColumnOnVar(j.LeftExpression, vars);
        if (childRef == null || parentRef == null || childRef.Value.Var == parentRef.Value.Var || parentRef.Value.Var != j.Identifier.Text) return;
        foreach (var w in q.Body.Clauses.OfType<WhereClauseSyntax>())
            foreach (var term in Conjuncts(w.Condition))
                if (Unwrap(term) is BinaryExpressionSyntax b && b.IsKind(SyntaxKind.EqualsExpression))
                {
                    var cr = ColumnRef(b.Left, parentRef.Value.Var, parentRef.Value.T); var other = b.Right;
                    if (cr == null) { cr = ColumnRef(b.Right, parentRef.Value.Var, parentRef.Value.T); other = b.Left; }
                    if (cr is not { Nav: null } || !IsCaller(other, v)) continue;
                    var pe = Entities[parentRef.Value.T]; var ce = Entities[childRef.Value.T];
                    if (PropOf(ce, childRef.Value.Prop)?.Col is not { } fk || PropOf(pe, cr.Value.Prop)?.Col is not { } pc) continue;
                    found.Add(new XOwner { Model = ce.T.Name, Table = TableNameOf(ce), Column = pc.Name, How = "compare", File = FileOf(b), Line = LineOf(b), Value = Js.Text(other), Via = new OwnerVia { Table = ce.T.Name, Parent = pe.T.Name, Fk = fk.Name } });
                }
    }

    (string Var, INamedTypeSymbol T, string Prop)? ColumnOnVar(ExpressionSyntax e, List<(string Name, INamedTypeSymbol T)> vars)
    {
        if (Unwrap(e) is MemberAccessExpressionSyntax { Expression: IdentifierNameSyntax id } ma)
            foreach (var (n, t) in vars) if (n == id.Identifier.Text && Entities.ContainsKey(t)) return (n, t, ma.Name.Identifier.Text);
        return null;
    }

    INamedTypeSymbol? ChainRootEntity(SyntaxNode node)
    {
        ISymbol? s = node switch { MemberAccessExpressionSyntax m => Sm(m).GetSymbolInfo(m.Name).Symbol, IdentifierNameSyntax i when !(i.Parent is MemberAccessExpressionSyntax pm && pm.Name == i) => Sm(i).GetSymbolInfo(i).Symbol, _ => null };
        if (s is IPropertySymbol ps && DbSets.TryGetValue((IPropertySymbol)ps.OriginalDefinition, out var t)) return t;
        return node is InvocationExpressionSyntax ? null : null;
    }

    bool ChainIgnores(SyntaxNode root)
    {
        SyntaxNode top = root;
        while (true)
        {
            var p = top.Parent;
            if (p is MemberAccessExpressionSyntax ma && ma.Expression == top) { top = ma; if (ma.Parent is InvocationExpressionSyntax inv && inv.Expression == ma) { if (ma.Name.Identifier.Text == "IgnoreQueryFilters") return true; top = inv; } }
            else if (p is AwaitExpressionSyntax or ParenthesizedExpressionSyntax) top = p;
            else return false;
        }
    }

    // ---- global query filters ---------------------------------------------------------------------------------------------------

    void ReadQueryFilters()
    {
        foreach (var (ei, lam, at) in QueryFilterExprs)
        {
            var body = lam is SimpleLambdaExpressionSyntax sl ? sl.Body : (lam as ParenthesizedLambdaExpressionSyntax)?.Body;
            var pname = lam is SimpleLambdaExpressionSyntax sl2 ? sl2.Parameter.Identifier.Text : (lam as ParenthesizedLambdaExpressionSyntax)?.ParameterList.Parameters.FirstOrDefault()?.Identifier.Text;
            if (body is not ExpressionSyntax be || pname == null) continue;
            var qf = new QueryFilter { Table = ei.T.Name, Expr = Js.Text(be), File = FileOf(at), Line = LineOf(at) };
            foreach (var term in Conjuncts(be))
                if (Unwrap(term) is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.EqualsExpression } b)
                {
                    var cr = ColumnRef(b.Left, pname, ei.T); var other = b.Right;
                    if (cr == null) { cr = ColumnRef(b.Right, pname, ei.T); other = b.Left; }
                    if (cr is { Nav: null } && IsCaller(other, null))
                    {
                        qf.Owner = true; qf.Column = PropOf(ei, cr.Value.Prop)!.Col.Name;
                        if (Unwrap(other) is MemberAccessExpressionSyntax om && Sm(om).GetSymbolInfo(om.Name).Symbol is IPropertySymbol cp)
                            qf.ReadsCallerVia = $"{cp.ContainingType.Name}.{cp.Name} -> {(cp.ContainingType.TypeKind == TypeKind.Interface ? ImplOfProp(cp) : Qual(cp))}" + (ClaimReads.Count > 0 ? $" (claim '{ClaimReads.First()}')" : "");
                    }
                }
            D.Dotnet.QueryFilters.Add(qf);
        }
    }

    /// <summary>The DI resolution inside a filter lambda (ICurrentUser.UserId -> its single implementation), listed once, on the first route the filter applies to.</summary>
    void NoteFilterResolutions()
    {
        foreach (var qf in D.Dotnet.QueryFilters.Where(q => q.ReadsCallerVia != null && q.AppliesToRoutes.Count > 0))
        {
            var parts = qf.ReadsCallerVia!.Split(" -> ");
            var first = qf.AppliesToRoutes.OrderBy(x => x, StringComparer.Ordinal).First();
            var target = parts[1].Split(" (")[0];
            if (parts[0].Contains('.') && target != parts[0]) D.Dotnet.Di.ResolvedCalls.Add(new DiCall { Route = first, Call = parts[0] + " (inside the HasQueryFilter lambda)", ResolvedTo = target, File = qf.File, Line = qf.Line });
        }
    }

    string ImplOfProp(IPropertySymbol p)
    {
        var regs = Regs.Where(r => r.Iface != null && SymbolEqualityComparer.Default.Equals(r.Iface, p.ContainingType)).ToList();
        return regs.Count == 1 && regs[0].Impl?.FindImplementationForInterfaceMember(p) is { } ip ? Qual(ip) : Qual(p);
    }
}
