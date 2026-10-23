// DN801, what an earlier statement of the same request has already settled (find-then-act), and what a query handed on is.
//   * a row handed to a DbSet mutator (`db.Notes.Remove(note)`) is the row some other access loaded: that access is judged on its own;
//   * a query handed to a project method (`Shape(db.Notes.Where(..))`) is that query unless the callee compares the owner itself;
//   * a query that addresses a row by its key (`x.Id == noteId`, `Find(noteId)`) is covered when an EARLIER statement of the same method,
//     in a block the query is in, loaded that entity scoped to the caller with the miss refused (`?? throw`, `Single*`/`First*`, or
//     `if (row == null) throw|return`) from the same key (an argument of the loading call or of its predicate), or created the row with its
//     owner set -- and so is a helper's key when the caller passed such a key in. The key must be the same value: a check of one id does
//     not cover a write to another.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed partial class Ex
{
    static readonly HashSet<string> RowMutators = new(StringComparer.Ordinal) { "Remove", "RemoveRange", "Update", "UpdateRange", "Attach", "AttachRange" };
    static readonly HashSet<string> SingleRow = new(StringComparer.Ordinal) { "Single", "SingleAsync", "First", "FirstAsync" };

    static ExpressionSyntax Bare(ExpressionSyntax e)
    {
        while (true)
        {
            e = Unwrap(e);
            if (e is AwaitExpressionSyntax a) e = a.Expression; else return e;
        }
    }

    static IEnumerable<ExpressionSyntax> Returned(SyntaxNode body)
    {
        if (body is ExpressionSyntax ex) { yield return ex; yield break; }
        foreach (var r in body.DescendantNodes(n => n is not LambdaExpressionSyntax and not LocalFunctionStatementSyntax).OfType<ReturnStatementSyntax>())
            if (r.Expression != null) yield return r.Expression;
    }

    bool OwnerScopedRow(ExpressionSyntax e, EntityInfo ent, string prop) =>
        ChainOf(e) is { T: { } t } && SymbolEqualityComparer.Default.Equals(t, ent.T) && MentionsIn(e, ent.T, prop);

    /// <summary>Whether `e` loads one row of the entity scoped to the caller and a miss refuses; a project helper counts when every value it returns does.</summary>
    bool RefusingOwnerLoad(ExpressionSyntax e, EntityInfo ent, string prop, int depth)
    {
        e = Bare(e);
        if (e is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.CoalesceExpression } c && Unwrap(c.Right) is ThrowExpressionSyntax)
            return OwnerScopedRow(Bare(c.Left), ent, prop);
        if (e is not InvocationExpressionSyntax inv) return false;
        if (SingleRow.Contains(InvokedName(inv.Expression) ?? "") && OwnerScopedRow(inv, ent, prop)) return true;
        if (depth <= 0) return false;
        var call = Resolve(inv);
        if (!call.Project || call.M == null || call.Ambiguous != null || BodyOf(DeclOf(call.M)) is not { } body) return false;
        var rets = Returned(body).ToList();
        return rets.Count > 0 && rets.All(r => RefusingOwnerLoad(r, ent, prop, depth - 1));
    }

    static bool NullTest(ExpressionSyntax cond, ISymbol local, Func<ExpressionSyntax, ISymbol?> sym)
    {
        cond = Unwrap(cond);
        return cond switch
        {
            BinaryExpressionSyntax { RawKind: (int)SyntaxKind.EqualsExpression } b =>
                (Unwrap(b.Right).IsKind(SyntaxKind.NullLiteralExpression) && SymbolEqualityComparer.Default.Equals(sym(Unwrap(b.Left)), local))
                || (Unwrap(b.Left).IsKind(SyntaxKind.NullLiteralExpression) && SymbolEqualityComparer.Default.Equals(sym(Unwrap(b.Right)), local)),
            IsPatternExpressionSyntax { Pattern: ConstantPatternSyntax cp } ip => Unwrap(cp.Expression).IsKind(SyntaxKind.NullLiteralExpression) && SymbolEqualityComparer.Default.Equals(sym(Unwrap(ip.Expression)), local),
            BinaryExpressionSyntax { RawKind: (int)SyntaxKind.LogicalOrExpression } o => NullTest(o.Left, local, sym) || NullTest(o.Right, local, sym),
            _ => false,
        };
    }

    /// <summary>The values an earlier statement of `v`'s body settled as checked for the caller: (symbol, null) the value itself, (symbol, "") its primary key, (symbol, name) one member.</summary>
    List<(ISymbol Sym, string? Member)> OwnerKeys(Visit v, EntityInfo ent, string prop, SyntaxNode at)
    {
        var keys = new List<(ISymbol, string?)>();
        ISymbol? SymOf(ExpressionSyntax x) => x is IdentifierNameSyntax i ? Sm(i).GetSymbolInfo(i).Symbol : null;
        void FromLoad(ExpressionSyntax value, ISymbol? local)
        {
            foreach (var id in value.DescendantNodesAndSelf().OfType<IdentifierNameSyntax>())
            {
                var s = Sm(id).GetSymbolInfo(id).Symbol;
                if (s is ILocalSymbol or IParameterSymbol { ContainingSymbol: IMethodSymbol { MethodKind: not MethodKind.AnonymousFunction } }) keys.Add((s, null));
            }
            if (local != null) keys.Add((local, ""));
        }
        foreach (var st in v.Body.DescendantNodesAndSelf(n => n is not LambdaExpressionSyntax and not LocalFunctionStatementSyntax).OfType<StatementSyntax>())
        {
            if (st.Parent is not BlockSyntax blk || !blk.Span.Contains(at.Span) || st.Span.End > at.SpanStart) continue;
            var items = new List<(ExpressionSyntax Value, ISymbol? Local)>();
            switch (st)
            {
                case LocalDeclarationStatementSyntax ld:
                    foreach (var d in ld.Declaration.Variables)
                        if (d.Initializer != null) items.Add((d.Initializer.Value, Sm(d).GetDeclaredSymbol(d)));
                    break;
                case ExpressionStatementSyntax { Expression: AssignmentExpressionSyntax { RawKind: (int)SyntaxKind.SimpleAssignmentExpression, Left: IdentifierNameSyntax l } a }:
                    items.Add((a.Right, Sm(l).GetSymbolInfo(l).Symbol));
                    break;
                case ExpressionStatementSyntax es:
                    items.Add((es.Expression, null));
                    break;
            }
            foreach (var (value, local) in items)
            {
                if (RefusingOwnerLoad(value, ent, prop, 2)) { FromLoad(value, local); continue; }
                var bare = Bare(value);
                if (local != null && OwnerScopedRow(bare, ent, prop)
                    && blk.Statements.OfType<IfStatementSyntax>().Any(s => s.SpanStart >= st.Span.End && s.Span.End <= at.SpanStart && NullTest(s.Condition, local, SymOf)
                        && s.Statement.DescendantNodesAndSelf().Any(n => n is ThrowStatementSyntax or ReturnStatementSyntax or ThrowExpressionSyntax)))
                { FromLoad(value, local); continue; }
                if (local != null && bare is ObjectCreationExpressionSyntax or ImplicitObjectCreationExpressionSyntax
                    && Sm(bare).GetTypeInfo(bare).Type is INamedTypeSymbol ct && SymbolEqualityComparer.Default.Equals(ct, ent.T)
                    && (bare as ObjectCreationExpressionSyntax)?.Initializer is { } ini && MentionsIn(ini, ent.T, prop)) keys.Add((local, ""));
            }
        }
        return keys;
    }

    /// <summary>Whether the key expression stands for a row checked for the caller before `at` in `v`, or (a helper's parameter) in the caller that passed it.</summary>
    bool KeyCovered(Visit v, EntityInfo ent, string prop, ExpressionSyntax key, SyntaxNode at, int depth)
    {
        key = Bare(key);
        ISymbol? sym; string? member = null;
        if (key is IdentifierNameSyntax id) sym = Sm(id).GetSymbolInfo(id).Symbol;
        else if (key is MemberAccessExpressionSyntax { Expression: var inner } ma && Unwrap(inner) is IdentifierNameSyntax rid) { sym = Sm(rid).GetSymbolInfo(rid).Symbol; member = ma.Name.Identifier.Text; }
        else return false;
        if (sym == null) return false;
        var pk = ent.Props.Where(p => p.Key).Select(p => p.P.Name).ToHashSet(StringComparer.Ordinal);
        foreach (var (s, m) in OwnerKeys(v, ent, prop, at))
            if (SymbolEqualityComparer.Default.Equals(s, sym) && (m == member || (m == "" && member != null && pk.Contains(member)))) return true;
        if (sym is IParameterSymbol p && member == null && depth > 0 && v.Parent != null && v.CallSite is InvocationExpressionSyntax cs && v.Method != null
            && SymbolEqualityComparer.Default.Equals(p.ContainingSymbol.OriginalDefinition, v.Method.OriginalDefinition))
        {
            var args = cs.ArgumentList.Arguments;
            var arg = args.FirstOrDefault(a => a.NameColon?.Name.Identifier.Text == p.Name) ?? (p.Ordinal < args.Count && !Resolve(cs).Reduced ? args[p.Ordinal] : null);
            if (arg != null) return KeyCovered(v.Parent, ent, prop, arg.Expression, cs, depth - 1);
        }
        return false;
    }

    /// <summary>The values a query compares the entity's primary key with (`x.Id == K` joined by &&; `Find(K)`).</summary>
    IEnumerable<ExpressionSyntax> PkComparands(SyntaxNode scope, EntityInfo ent)
    {
        foreach (var inv in scope.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>())
        {
            if (inv.Expression is not MemberAccessExpressionSyntax ma || ChainOf(ma.Expression) is not { T: { } t } || !SymbolEqualityComparer.Default.Equals(t, ent.T)) continue;
            var name = InvokedName(inv.Expression) ?? "";
            if (name is "Find" or "FindAsync" && inv.ArgumentList.Arguments.Count >= 1) { yield return inv.ArgumentList.Arguments[0].Expression; continue; }
            if (!PredicateOps.Contains(name)) continue;
            foreach (var lam in inv.ArgumentList.Arguments.Select(a => a.Expression).OfType<LambdaExpressionSyntax>())
            {
                var pname = lam is SimpleLambdaExpressionSyntax sl ? sl.Parameter.Identifier.Text : (lam as ParenthesizedLambdaExpressionSyntax)?.ParameterList.Parameters.FirstOrDefault()?.Identifier.Text;
                if (pname == null || (lam is SimpleLambdaExpressionSyntax s2 ? s2.Body : ((ParenthesizedLambdaExpressionSyntax)lam).Body) is not ExpressionSyntax body) continue;
                foreach (var cj in Conjuncts(body))
                    if (cj is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.EqualsExpression } b)
                        foreach (var (a, o) in new[] { (b.Left, b.Right), (b.Right, b.Left) })
                            if (ColumnRef(a, pname, ent.T) is { Nav: null } cr && ent.Props.Any(p => p.Key && p.P.Name == cr.Prop)) yield return o;
            }
        }
    }

    /// <summary>`db.Notes.Remove(note)` and the like: the row the call is handed was loaded by every value its local is given (the loads are judged on their own); a body-bound or constructed entity is not.</summary>
    bool RowHandedToMutator(SyntaxNode top, EntityInfo ent, Visit v)
    {
        if (top is not InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax ma } inv || !RowMutators.Contains(ma.Name.Identifier.Text)) return false;
        if (inv.ArgumentList.Arguments.Count != 1 || Bare(inv.ArgumentList.Arguments[0].Expression) is not IdentifierNameSyntax id || Sm(id).GetSymbolInfo(id).Symbol is not ILocalSymbol local) return false;
        var values = new List<ExpressionSyntax>();
        foreach (var n in v.Body.DescendantNodesAndSelf(n => n is not LambdaExpressionSyntax and not LocalFunctionStatementSyntax))
        {
            if (n is VariableDeclaratorSyntax { Initializer: { } ini } d && SymbolEqualityComparer.Default.Equals(Sm(d).GetDeclaredSymbol(d), local)) values.Add(ini.Value);
            else if (n is AssignmentExpressionSyntax { RawKind: (int)SyntaxKind.SimpleAssignmentExpression, Left: IdentifierNameSyntax l } a && SymbolEqualityComparer.Default.Equals(Sm(l).GetSymbolInfo(l).Symbol, local)) values.Add(a.Right);
        }
        return values.Count > 0 && values.All(x => LoadsRows(x, ent, 3));
    }

    bool LoadsRows(ExpressionSyntax e, EntityInfo ent, int depth)
    {
        e = Bare(e);
        if (e is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.CoalesceExpression } c) return LoadsRows(c.Left, ent, depth);
        if (e.DescendantNodesAndSelf().Any(n => n is MemberAccessExpressionSyntax or IdentifierNameSyntax && ChainRootEntity(n) is { } t && SymbolEqualityComparer.Default.Equals(t, ent.T))) return true;
        if (depth <= 0 || e is not InvocationExpressionSyntax inv) return false;
        var call = Resolve(inv);
        if (!call.Project || call.M == null || call.Ambiguous != null || BodyOf(DeclOf(call.M)) is not { } body) return false;
        var rets = Returned(body).ToList();
        return rets.Count > 0 && rets.All(r => LoadsRows(r, ent, depth - 1));
    }

    /// <summary>A query that is an argument of a call: judged when the callee is a project method that compares no owner itself (it only shapes the query), not judged when the callee cannot be read.</summary>
    bool HandedOnScoped(InvocationExpressionSyntax i, INamedTypeSymbol owner, string prop)
    {
        var c = Resolve(i);
        if (FrameworkResultCall(i, c)) return false;
        if (!c.Project || c.M == null || c.Ambiguous != null || BodyOf(DeclOf(c.M)) is not { } body) return true;
        return MentionsIn(body, owner, prop);
    }

    /// <summary>The owner column set from a value the request carries (`UserId = request.UserId ?? caller`, `row.OwnerId = body.OwnerId`): the row can be handed to anyone. A refusing `if` on the value before the write bounds it (as for DN803); not judged on an admin or open route.</summary>
    void OwnerFromRequest(RouteRec rec, CheckPolicy pol, HashSet<string> reported, List<CFinding> found)
    {
        foreach (var v in rec.Reach!.Visits)
            foreach (var asg in v.Body.DescendantNodesAndSelf().OfType<AssignmentExpressionSyntax>().Where(a => a.IsKind(SyntaxKind.SimpleAssignmentExpression)))
            {
                INamedTypeSymbol? et = null; string? pn = null;
                if (ColumnRef(asg.Left, null, null) is { Nav: null } cr) (et, pn) = (cr.Entity, cr.Prop);
                else if (asg.Left is IdentifierNameSyntax lid && asg.Parent is InitializerExpressionSyntax { Parent: BaseObjectCreationExpressionSyntax oc } && Sm(oc).GetTypeInfo(oc).Type is INamedTypeSymbol ct) (et, pn) = (ct, lid.Identifier.Text);
                if (et == null || pn == null || !Entities.TryGetValue(et, out var ei)) continue;
                var schema = SchemaOf(pol, ei);
                if (schema == pol.UsersTable || !pol.Owners.TryGetValue(schema, out var col) || PropFor(ei, col)?.P.Name != pn || reported.Contains(schema)) continue;
                if (RequestValue(rec, asg.Right, out var subject) is not { } p || GuardedBefore(rec, v, asg, p)) continue;
                reported.Add(schema);
                found.Add(Mk("DN801", asg, $"DN801|{rec.Id}|{schema}",
                    $"{rec.Id} sets {schema}.{col} (`{pn}`) from the request ({subject}) at line {LineOf(asg)} (`{Js.Text(asg)}`): the caller can give a row to another user",
                    $"set `{pn}` from the caller, never from the request; or refuse a value that is not the caller's before the write"));
            }
    }
}
