// DN801 ownership (PY801): a route (a complete reach, N10) that reads, updates or deletes rows of an owned table through a
// query with no owner evidence for THAT query, outside the admin and open routes. Per query, because a route that scopes
// one lookup to the caller (`EnsureNameFree(caller.Id, ..)`) does not scope the habit it then loads by id. The evidence for
// a query on an owned table is one of: the owner property compared, assigned or initialized anywhere in the query chain
// (a lambda predicate, query syntax); a global query filter in force (no IgnoreQueryFilters in the chain); a statement about
// the row the query yielded that compares or assigns its owner property (find-then-check: `row.OwnerId != caller`,
// `device.UserId = caller.Id`), or hands the row to a method that does; a private column of the table in the predicate (a
// lookup by a secret -- a refresh token's hash -- is addressed by the credential, not by id; on a route with no authenticated
// caller, so is a lookup by a unique non-key column). A query whose result leaves the
// method (returned, passed on) is not judged. A table owned through a parent needs that evidence on the parent anywhere in
// the route (GO801's rule). Creating a row alone (Add) is not a read.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed partial class Ex
{
    static readonly HashSet<string> CreateOnly = new(StringComparer.Ordinal) { "Add", "AddAsync", "AddRange", "AddRangeAsync" };

    /// <summary>Every access to a DbSet (`db.Notes`, `db.Set&lt;Note&gt;()`) in the reach, other than a bare create.</summary>
    List<(EntityInfo E, SyntaxNode At, Visit V)> Touches(Reach reach)
    {
        var res = new List<(EntityInfo, SyntaxNode, Visit)>();
        foreach (var v in reach.Visits)
            foreach (var node in v.Body.DescendantNodesAndSelf())
            {
                INamedTypeSymbol? t = null;
                if (node is MemberAccessExpressionSyntax or IdentifierNameSyntax) t = ChainRootEntity(node);
                else if (node is InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax { Name: GenericNameSyntax { Identifier.Text: "Set" } } } set && ChainOf(set) is { T: { } st }) t = st;
                if (t == null || !Entities.TryGetValue(t, out var ei)) continue;
                if (node.Parent is MemberAccessExpressionSyntax ma && ma.Expression == node && CreateOnly.Contains(ma.Name.Identifier.Text)) continue;
                res.Add((ei, node, v));
            }
        return res;
    }

    /// <summary>The entity type of each range variable in the tree: lambda parameters of calls on a DbSet chain, query-syntax `from`/`join` variables (EF is not referenced, so Roslyn leaves their types unbound).</summary>
    Dictionary<string, INamedTypeSymbol> RangeVars(SyntaxNode root)
    {
        var vars = new Dictionary<string, INamedTypeSymbol>(StringComparer.Ordinal);
        foreach (var n in root.DescendantNodesAndSelf())
        {
            if (n is InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax ma } inv && ChainOf(ma.Expression)?.T is { } t)
                foreach (var lam in inv.ArgumentList.Arguments.Select(a => a.Expression).OfType<LambdaExpressionSyntax>())
                {
                    var p = lam is SimpleLambdaExpressionSyntax sl ? sl.Parameter.Identifier.Text : (lam as ParenthesizedLambdaExpressionSyntax)?.ParameterList.Parameters.FirstOrDefault()?.Identifier.Text;
                    if (p != null) vars[p] = t;
                }
            else if (n is QueryExpressionSyntax q)
            {
                if (ChainOf(q.FromClause.Expression)?.T is { } ft) vars[q.FromClause.Identifier.Text] = ft;
                foreach (var j in q.Body.Clauses.OfType<JoinClauseSyntax>()) if (ChainOf(j.InExpression)?.T is { } jt) vars[j.Identifier.Text] = jt;
            }
        }
        return vars;
    }

    /// <summary>Whether the tree compares, assigns or initializes the entity's property (any value: GO801's `mentionsColumn`).</summary>
    bool MentionsIn(SyntaxNode root, INamedTypeSymbol owner, string prop)
    {
        Dictionary<string, INamedTypeSymbol>? vars = null;
        foreach (var n in root.DescendantNodesAndSelf().OfType<SimpleNameSyntax>())
        {
            if (n.Identifier.Text != prop) continue;
            bool hit = Sm(n).GetSymbolInfo(n).Symbol is IPropertySymbol ps && Derives(owner, ps.ContainingType);
            SyntaxNode top = n.Parent is MemberAccessExpressionSyntax ma && ma.Name == n ? ma : n;
            if (!hit && top is MemberAccessExpressionSyntax mx)
            {
                var r = ColumnRef(mx, null, null);
                if (r == null && Unwrap(mx.Expression) is IdentifierNameSyntax id && (vars ??= RangeVars(root)).TryGetValue(id.Identifier.Text, out var vt)) r = ColumnRef(mx, id.Identifier.Text, vt);
                hit = r is { Nav: null } rr && rr.Prop == prop && Derives(owner, rr.Entity);
            }
            if (!hit) continue;
            while (top.Parent is ParenthesizedExpressionSyntax or PostfixUnaryExpressionSyntax or CastExpressionSyntax) top = top.Parent;
            if (top.Parent is BinaryExpressionSyntax b && (b.IsKind(SyntaxKind.EqualsExpression) || b.IsKind(SyntaxKind.NotEqualsExpression))) return true;
            if (top.Parent is AssignmentExpressionSyntax a && a.Left == top) return true;
            if (top.Parent is IsPatternExpressionSyntax or SwitchExpressionArmSyntax) return true;
        }
        return false;
    }

    bool Mentions(Reach reach, INamedTypeSymbol owner, string prop) => reach.Visits.Any(v => MentionsIn(v.Body, owner, prop));

    /// <summary>The query a DbSet access starts: the whole call chain, through await/parentheses and `?? throw`.</summary>
    static SyntaxNode ChainTop(SyntaxNode access)
    {
        var top = access;
        while (true)
        {
            var p = top.Parent;
            if (p is MemberAccessExpressionSyntax ma && ma.Expression == top) { top = ma.Parent is InvocationExpressionSyntax inv && inv.Expression == ma ? inv : ma; if (top == ma) return top; }
            else if (p is ParenthesizedExpressionSyntax or AwaitExpressionSyntax or PostfixUnaryExpressionSyntax or CastExpressionSyntax) top = p;
            else if (p is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.CoalesceExpression } c && c.Left == top) top = p;
            else return top;
        }
    }

    ISymbol? LocalOf(SyntaxNode top) => top.Parent switch
    {
        EqualsValueClauseSyntax { Parent: VariableDeclaratorSyntax d } => Sm(d).GetDeclaredSymbol(d),
        AssignmentExpressionSyntax { Left: IdentifierNameSyntax l } a when a.Right == top => Sm(l).GetSymbolInfo(l).Symbol,
        ForEachStatementSyntax fe when fe.Expression == top => Sm(fe).GetDeclaredSymbol(fe),
        _ => null,
    };

    /// <summary>The statements of a body, each cut to what it evaluates itself (an `if` to its condition).</summary>
    static IEnumerable<SyntaxNode> Units(SyntaxNode body)
    {
        if (body is ExpressionSyntax) { yield return body; yield break; }
        foreach (var n in body.DescendantNodes())
            switch (n)
            {
                case IfStatementSyntax i: yield return i.Condition; break;
                case WhileStatementSyntax w: yield return w.Condition; break;
                case ForEachStatementSyntax f: yield return f.Expression; break;
                case SwitchStatementSyntax s: yield return s.Expression; break;
                case ExpressionStatementSyntax or LocalDeclarationStatementSyntax or ReturnStatementSyntax or ThrowStatementSyntax: yield return n; break;
            }
    }

    /// <summary>Whether this one query is scoped to the caller (or cannot be judged) -- see the file's comment.</summary>
    bool QueryScoped(RouteRec rec, CheckPolicy pol, EntityInfo ent, string schema, string prop, SyntaxNode at, Visit v)
    {
        var top = ChainTop(at);
        var scope = (SyntaxNode?)at.Ancestors().OfType<QueryExpressionSyntax>().FirstOrDefault() ?? top;
        if (MentionsIn(scope, ent.T, prop)) return true;
        if (!ChainIgnores(at) && rec.R.OwnerUses.Any(o => o.How == "filter" && o.Model == ent.T.Name)) return true;
        foreach (var col in pol.Private.GetValueOrDefault(schema) ?? new())
            if (PropFor(ent, col) is { } secret && MentionsIn(scope, ent.T, secret.P.Name)) return true;
        if (rec.R.Auth == "Public") // nobody to scope to: a lookup by a unique non-key column (a token's hash) is addressed by that credential
            foreach (var u in ent.Props.Where(x => x.Col.Unique && !x.Col.PrimaryKey && !x.Key))
                if (MentionsIn(scope, ent.T, u.P.Name)) return true;
        if (RowHandedToMutator(top, ent, v)) return true;
        if (PkComparands(scope, ent).Any(k => KeyCovered(v, ent, prop, k, at, 3))) return true;
        var local = LocalOf(top);
        if (local == null) // a value that leaves the method (returned from a helper) is not judged; one handed to a project method that compares no owner itself, or answered by the handler or by a result factory, is
            return top.Parent switch
            {
                ArgumentSyntax { Parent: ArgumentListSyntax { Parent: InvocationExpressionSyntax i } } => HandedOnScoped(i, ent.T, prop),
                ReturnStatementSyntax or ArrowExpressionClauseSyntax or LambdaExpressionSyntax => v.Depth > 0,
                AssignmentExpressionSyntax => true,
                _ => false,
            };
        foreach (var u in Units(v.Body))
        {
            if (!u.DescendantNodesAndSelf().OfType<IdentifierNameSyntax>().Any(i => SymbolEqualityComparer.Default.Equals(Sm(i).GetSymbolInfo(i).Symbol, local))) continue;
            if (MentionsIn(u, ent.T, prop)) return true;
            foreach (var inv in u.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>())
                if (inv.ArgumentList.Arguments.Any(a => a.Expression is IdentifierNameSyntax i && SymbolEqualityComparer.Default.Equals(Sm(i).GetSymbolInfo(i).Symbol, local))
                    && Resolve(inv) is { Project: true, M: { } m } && BodyOf(DeclOf(m)) is { } mb && MentionsIn(mb, ent.T, prop)) return true;
        }
        return false;
    }

    /// <summary>A table owned through a parent: the evidence on the parent, or a comparison through the navigation, anywhere in the route.</summary>
    bool ParentScoped(RouteRec rec, CheckPolicy pol, string schema, int depth)
    {
        var ent = EntityFor(pol, schema);
        if (depth < 0 || ent == null) return true; // a cycle of parents, or a table the code does not have: not judged
        if (pol.Owners.TryGetValue(schema, out var col)) return PropFor(ent, col) is not { } prop || Mentions(rec.Reach!, ent.T, prop.P.Name) || rec.R.OwnerUses.Any(o => o.Model == ent.T.Name && Same(o.Column, prop.Col.Name) && o.Via?.Parent == null);
        if (!pol.OwnerVia.TryGetValue(schema, out var via)) return true;
        var parent = EntityFor(pol, via.Parent);
        return (parent != null && rec.R.OwnerUses.Any(o => o.Model == ent.T.Name && o.Via?.Parent == parent.T.Name)) || ParentScoped(rec, pol, via.Parent, depth - 1);
    }

    /// <summary>Whether everything the route runs that could check ownership is read: B1's reach is incomplete for any call it cannot resolve (a clock with two registrations), but only a call that is handed the context, a row or the request can check ownership.</summary>
    public bool Readable(Reach reach)
    {
        if (!reach.Incomplete) return true;
        foreach (var v in reach.Visits)
            foreach (var inv in v.Body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>())
            {
                var c = Resolve(inv);
                if (c.Ambiguous != null || ((!c.Project || c.M == null) && c.M == null && !EfNames.Contains(c.Name))) { if (ReceivesStateful(inv)) return false; continue; }
                if (!c.Project || c.M == null) continue;
                var target = DiTarget(c.M, out _, out _);
                if (target == null) { if (ReceivesStateful(inv)) return false; continue; }
                if (v.Depth >= HelperDepth && BodyOf(DeclOf(target)) is { } b && !(b.SyntaxTree == v.Body.SyntaxTree && v.Body.Span.Contains(b.Span)) && ReceivesStateful(inv)) return false;
            }
        return true;
    }

    /// <summary>A-JUDGE: routes that touch an owned table, and how many of them were read completely (by Readable, and by B1's reach).</summary>
    public int StatOwned, StatJudged, StatB1Complete;

    void CheckOwnership(CheckPolicy pol, List<CFinding> found)
    {
        if (!pol.On("DN801") || (pol.Owners.Count == 0 && pol.OwnerVia.Count == 0)) return;
        foreach (var rec in Judged())
        {
            if (AdminOnly(rec, pol) || OpenRoute(rec, pol)) continue;
            if (Touches(rec.Reach!).Any(t => SchemaOf(pol, t.E) is var sc && sc != pol.UsersTable && (pol.Owners.ContainsKey(sc) || pol.OwnerVia.ContainsKey(sc))))
            { StatOwned++; if (Readable(rec.Reach!)) StatJudged++; if (!rec.Reach!.Incomplete) StatB1Complete++; }
            if (!Readable(rec.Reach!)) continue;
            var reported = new HashSet<string>(StringComparer.Ordinal);
            foreach (var (ei, at, v) in Touches(rec.Reach).OrderBy(t => FileOf(t.At), StringComparer.Ordinal).ThenBy(t => LineOf(t.At)))
            {
                var schema = SchemaOf(pol, ei);
                if (schema == pol.UsersTable || reported.Contains(schema)) continue;
                bool direct = pol.Owners.TryGetValue(schema, out var ownerCol);
                if (!direct && !pol.OwnerVia.ContainsKey(schema)) continue;
                var ownerProp = direct ? PropFor(ei, ownerCol!)?.P.Name : null;
                if (direct ? ownerProp == null || QueryScoped(rec, pol, ei, schema, ownerProp, at, v) : ParentScoped(rec, pol, schema, 4)) continue;
                reported.Add(schema);
                string owned = schema, via = "";
                for (int d = 0; d < 4 && !pol.Owners.ContainsKey(owned) && pol.OwnerVia.TryGetValue(owned, out var pv); d++) { via += $" through {owned}.{pv.Fk}"; owned = pv.Parent; }
                var col = pol.Owners.GetValueOrDefault(owned) ?? "owner";
                var prop = EntityFor(pol, owned) is { } oe ? PropFor(oe, col)?.P.Name ?? col : col;
                found.Add(Mk("DN801", at, $"DN801|{rec.Id}|{schema}",
                    $"{rec.Id} reads or writes {schema} rows at line {LineOf(at)} (`{Js.Text(at.Parent is MemberAccessExpressionSyntax ? at.Parent : at)}`), and nothing it runs for that query -- the chain, the row it yields, the methods it hands the row to -- filters, compares or sets {owned}.{col} (`{prop}`){via}: another user's rows reach the caller",
                    via == "" ? $"scope the query to the caller: `.Where(x => x.{prop} == <the caller's id>)`, or load the row and refuse when `row.{prop} != <the caller's id>`; on create, set `{prop}` from the caller"
                              : $"load the owning {owned} row and refuse unless its `{prop}` is the caller (`.Where(x => x.{prop} == <the caller's id>)`) before reaching {schema} rows"));
            }
            OwnerFromRequest(rec, pol, reported, found);
        }
    }
}
