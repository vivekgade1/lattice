// DN807 partial writes (PY807; Lattice's one transaction per write): in a route's reach, a commit (`SaveChanges[Async]`
// as a statement of its own, or the `Commit` of a transaction) that a refusal can follow -- any 4xx answer or mapped throw
// on a path after it, in the same method, in a loop's next pass, or in the caller after a helper that returns nothing
// the caller could branch on -- or a second commit, outside a transaction: `BeginTransaction[Async]` before it in a scope
// that contains it (and no commit between), a `TransactionScope`. A save whose result is used (`var n = await
// SaveChangesAsync()`) is not judged for refusals (the branch may be the one that wrote nothing); 5xx are not refusals.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed partial class Ex
{
    sealed class CommitPt
    {
        public InvocationExpressionSyntax Inv = null!;
        public Visit V = null!;
        public int Ordinal;
        public bool Bare;
    }

    static bool IsExit(StatementSyntax s, out bool returns)
    {
        returns = false;
        switch (s)
        {
            case ReturnStatementSyntax: returns = true; return true;
            case ThrowStatementSyntax: return true;
            case BlockSyntax b: return b.Statements.Count > 0 && IsExit(b.Statements[^1], out returns);
            case IfStatementSyntax { Else: { } e } i:
                {
                    var both = IsExit(i.Statement, out var r1) && IsExit(e.Statement, out _);
                    returns = r1;
                    return both;
                }
        }
        return false;
    }

    SyntaxNode? CallSiteOf(Visit v)
    {
        if (v.Parent == null || v.Method == null) return null;
        return v.Parent.Body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>().FirstOrDefault(i =>
        {
            var c = Resolve(i);
            return c.Project && c.M != null && DiTarget(c.M, out _, out _) is { } t && SymbolEqualityComparer.Default.Equals(t.OriginalDefinition, v.Method.OriginalDefinition);
        });
    }

    /// <summary>A node that begins a transaction: BeginTransaction[Async], a TransactionScope, or a call of a project method that does (a unit-of-work `BeginAsync`).</summary>
    bool Begins(SyntaxNode n, int depth)
    {
        if (n is ObjectCreationExpressionSyntax o) return o.Type.ToString().EndsWith("TransactionScope", StringComparison.Ordinal);
        if (n is not InvocationExpressionSyntax i) return false;
        if (InvokedName(i.Expression) is "BeginTransaction" or "BeginTransactionAsync") return true;
        var c = Resolve(i);
        return depth < 3 && c.Project && c.M != null && DiTarget(c.M, out _, out _) is { } t && BodyOf(DeclOf(t)) is { } body && body.DescendantNodesAndSelf().Any(x => Begins(x, depth + 1));
    }

    /// <summary>Whether the node runs inside a transaction scope begun before it (here or in a caller before the call).</summary>
    bool InTransaction(Visit v, SyntaxNode at)
    {
        foreach (var b in v.Body.DescendantNodesAndSelf().Where(n => n.Span.End <= at.SpanStart && Begins(n, 0)))
        {
            var scope = b.Ancestors().FirstOrDefault(a => a is BlockSyntax or UsingStatementSyntax);
            if (scope == null || !scope.Span.Contains(at.Span)) continue;
            bool ended = scope.DescendantNodes().OfType<InvocationExpressionSyntax>().Any(i => InvokedName(i.Expression) is "Commit" or "CommitAsync" or "Complete" && i.SpanStart >= b.Span.End && i.Span.End <= at.SpanStart);
            if (!ended) return true;
        }
        return v.Parent != null && CallSiteOf(v) is { } cs && InTransaction(v.Parent, cs);
    }

    List<CommitPt> Commits(Visit v)
    {
        var res = new List<CommitPt>();
        int k = 0;
        foreach (var inv in v.Body.DescendantNodesAndSelf().OfType<InvocationExpressionSyntax>().OrderBy(i => i.SpanStart))
        {
            var name = InvokedName(inv.Expression);
            if (name is not ("SaveChanges" or "SaveChangesAsync" or "Commit" or "CommitAsync") || Resolve(inv).Project || inv.Expression is not MemberAccessExpressionSyntax ma) continue;
            var rt = Sm(inv).GetTypeInfo(ma.Expression).Type as INamedTypeSymbol;
            bool save = name.StartsWith("SaveChanges", StringComparison.Ordinal);
            if (save ? !IsDbContextType(rt) : rt == null || !rt.Name.Contains("Transaction", StringComparison.Ordinal)) continue;
            k++;
            if (InTransaction(v, inv) && save) continue;
            var stmt = inv.Ancestors().OfType<StatementSyntax>().FirstOrDefault();
            ExpressionSyntax? top = (stmt as ExpressionStatementSyntax)?.Expression;
            while (top is AwaitExpressionSyntax or ParenthesizedExpressionSyntax || top is InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax { Name.Identifier.Text: "ConfigureAwait" } })
                top = top is AwaitExpressionSyntax a ? a.Expression : top is ParenthesizedExpressionSyntax p ? p.Expression : ((MemberAccessExpressionSyntax)((InvocationExpressionSyntax)top).Expression).Expression;
            res.Add(new CommitPt { Inv = inv, V = v, Ordinal = k, Bare = top == inv });
        }
        return res;
    }

    /// <summary>The code that can run after the node (siblings after it, a loop's next pass, what follows the enclosing statements) and whether the method returns normally.</summary>
    (List<SyntaxNode> Nodes, bool Returns) After(SyntaxNode at)
    {
        var nodes = new List<SyntaxNode>();
        var cur = at.AncestorsAndSelf().OfType<StatementSyntax>().FirstOrDefault();
        if (cur == null) return (nodes, false);
        if (cur is ReturnStatementSyntax) return (nodes, true);
        while (cur != null)
        {
            IEnumerable<StatementSyntax> sibs = cur.Parent switch { BlockSyntax b => b.Statements, SwitchSectionSyntax s => s.Statements, _ => Enumerable.Empty<StatementSyntax>() };
            foreach (var sib in sibs.SkipWhile(x => x != cur).Skip(1))
            {
                nodes.Add(sib);
                if (IsExit(sib, out var rets)) return (nodes, rets);
            }
            var up = cur.Parent?.AncestorsAndSelf().OfType<StatementSyntax>().FirstOrDefault(x => x != cur);
            if (up == null || cur.Parent is LambdaExpressionSyntax || cur.Parent is MethodDeclarationSyntax || cur.Parent is LocalFunctionStatementSyntax) return (nodes, true);
            if (up is ForStatementSyntax or ForEachStatementSyntax or WhileStatementSyntax or DoStatementSyntax) nodes.Add(up);
            cur = up;
            if (cur is BlockSyntax blk && blk.Parent is LambdaExpressionSyntax or MethodDeclarationSyntax or LocalFunctionStatementSyntax or AccessorDeclarationSyntax) return (nodes, true);
        }
        return (nodes, true);
    }

    static bool Within(List<SyntaxNode> region, string file, int line) =>
        region.Any(n => n.SyntaxTree.FilePath == file && n.SyntaxTree.GetLineSpan(n.Span).StartLinePosition.Line + 1 <= line && line <= n.SyntaxTree.GetLineSpan(n.Span).EndLinePosition.Line + 1);

    void CheckPartial(CheckPolicy pol, List<CFinding> found)
    {
        if (!pol.On("DN807")) return;
        foreach (var rec in Judged())
        {
            var visits = rec.Reach!.Visits;
            var commits = visits.SelectMany(Commits).ToList();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var cp in commits)
            {
                if (!seen.Add(FileOf(cp.Inv) + ":" + LineOf(cp.Inv))) continue;
                var region = new List<SyntaxNode>();
                var (nodes, returns) = After(cp.Inv);
                region.AddRange(nodes);
                // the callers: a helper that returns nothing the caller could branch on leaves the caller's own later code to run after the write
                for (var w = cp.V; returns && w.Parent != null && w.Method is { } m && (m.ReturnsVoid || m.ReturnType.Name is "Task" or "ValueTask" && m.ReturnType is INamedTypeSymbol { IsGenericType: false });)
                {
                    if (CallSiteOf(w) is not { } cs) break;
                    (var more, returns) = After(cs);
                    region.AddRange(more);
                    w = w.Parent;
                }
                foreach (var w in visits)
                {
                    var root = w;
                    while (root.Parent != null && root.Parent != cp.V) root = root.Parent;
                    if (root.Parent == cp.V && CallSiteOf(root) is { } site && region.Any(n => n.SyntaxTree == site.SyntaxTree && n.Span.Contains(site.Span))) region.Add(w.Body);
                }
                string? after = null;
                var refusal = cp.Bare ? rec.R.Raises.FirstOrDefault(x => x.Status is >= 400 and < 500 && x.Kind != "middleware" && Within(region, x.File, x.Line)) : null;
                var other = commits.FirstOrDefault(c => c != cp && c.Inv.Span.Start != cp.Inv.Span.Start && region.Any(n => n.SyntaxTree == c.Inv.SyntaxTree && n.Span.Contains(c.Inv.Span)));
                if (refusal != null) after = $"can be refused ({refusal.Status}) at {refusal.File}:{refusal.Line}";
                else if (other != null) after = $"commits again at {FileOf(other.Inv)}:{LineOf(other.Inv)}";
                if (after == null) continue;
                var name = InvokedName(cp.Inv.Expression)!;
                found.Add(Mk("DN807", cp.Inv, $"DN807|{rec.Id}|{(cp.V.Depth == 0 ? "handler" : cp.V.In)}:{name}#{cp.Ordinal}",
                    $"{rec.Id}: `{Js.Text(cp.Inv)}` commits at line {LineOf(cp.Inv)} outside a transaction and the request {after}: the earlier write stays",
                    "run the writes in one transaction (`await using var tx = await db.Database.BeginTransactionAsync()` ... `await tx.CommitAsync()`), or make every check before the first `SaveChanges`"));
            }
        }
    }
}
