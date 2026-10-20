// Required-ness of a request-body field read through an accessor (N5, pre-freeze fix). `body.OptionalString("name")` is
// optional by its name, but the handler may refuse a missing value anyway: `ValidName(body.OptionalString("name"))` where
// ValidName throws 422 on null, or `var t = body.OptionalString("t"); if (string.IsNullOrEmpty(t)) throw ...`, or
// `?? ""` followed by a length check. The field is then required. The test is a small evaluator over the project's own
// source: the missing value (null, or the constant after `??`) is pushed through the statements that follow and through
// the project methods they call; anything the evaluator cannot decide leaves the field optional. A field read only
// under a condition (`if (body.Has(k))`, a ternary, a loop, a lambda: a batch item's fields) is not required of the body.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed partial class Ex
{
    sealed class UnkEx : Exception { }
    sealed class RefusedEx : Exception { public int? Status; }
    static readonly UnkEx UnknownValue = new();

    sealed class ReqEnv
    {
        public Dictionary<SyntaxNode, object?> Nodes = new();
        public Dictionary<ISymbol, object?> Vars = new(SymbolEqualityComparer.Default);
        public int[] Steps = { 0 };
        public int Calls;
        public bool Returned;
        public object? Ret;
    }

    // ---- is the access made whenever the handler runs? -----------------------------------------------------------------

    bool UncondIn(SyntaxNode node, SyntaxNode body)
    {
        SyntaxNode child = node;
        foreach (var a in node.Ancestors())
        {
            if (a == body) return true;
            switch (a)
            {
                case IfStatementSyntax ifs when child == ifs.Statement || child == ifs.Else: return false;
                case ConditionalExpressionSyntax ce when child != ce.Condition: return false;
                case CommonForEachStatementSyntax fe when child == fe.Statement: return false;
                case ForStatementSyntax fs when child == fs.Statement: return false;
                case WhileStatementSyntax ws when child == ws.Statement: return false;
                case DoStatementSyntax ds when child == ds.Statement: return false;
                case SwitchSectionSyntax or SwitchExpressionArmSyntax or CatchClauseSyntax or FinallyClauseSyntax or LocalFunctionStatementSyntax or AnonymousFunctionExpressionSyntax: return false;
                case BinaryExpressionSyntax { RawKind: (int)SyntaxKind.LogicalAndExpression or (int)SyntaxKind.LogicalOrExpression or (int)SyntaxKind.CoalesceExpression } b when child == b.Right: return false;
                case ConditionalAccessExpressionSyntax ca when child == ca.WhenNotNull: return false;
            }
            child = a;
        }
        return true;
    }

    bool Unconditional(SyntaxNode node, Visit v)
    {
        var n = node;
        for (var cur = v; cur != null; cur = cur.Parent)
        {
            if (!UncondIn(n, cur.Body)) return false;
            if (cur.CallSite == null || cur.Parent == null) return true;
            n = cur.CallSite;
        }
        return true;
    }

    // ---- the evaluator ---------------------------------------------------------------------------------------------------

    static bool Truth(object? o) => o is bool b ? b : throw UnknownValue;
    static long? Num(object? o) => o switch { int i => i, long l => l, short s => s, byte b => b, uint u => u, char c => c, _ => null };
    static bool SameValue(object? a, object? b) => a == null || b == null ? a == null && b == null : Num(a) is { } x && Num(b) is { } y ? x == y : a.Equals(b);

    object? Ev(ExpressionSyntax e, ReqEnv env, int depth)
    {
        if (++env.Steps[0] > 3000 || depth > 30) throw UnknownValue;
        if (env.Nodes.TryGetValue(e, out var pre)) return pre;
        var cv = Sm(e).GetConstantValue(e);
        if (cv.HasValue) return cv.Value;
        switch (e)
        {
            case ParenthesizedExpressionSyntax p: return Ev(p.Expression, env, depth + 1);
            case PostfixUnaryExpressionSyntax { RawKind: (int)SyntaxKind.SuppressNullableWarningExpression } su: return Ev(su.Operand, env, depth + 1);
            case IdentifierNameSyntax id:
                return Sm(id).GetSymbolInfo(id).Symbol is { } s && env.Vars.TryGetValue(s, out var val) ? val : throw UnknownValue;
            case PrefixUnaryExpressionSyntax { RawKind: (int)SyntaxKind.LogicalNotExpression } not: return !Truth(Ev(not.Operand, env, depth + 1));
            case ThrowExpressionSyntax te: throw new RefusedEx { Status = ErrorOf(te.Expression, null)?.Status };
            case ConditionalExpressionSyntax ce: return Truth(Ev(ce.Condition, env, depth + 1)) ? Ev(ce.WhenTrue, env, depth + 1) : Ev(ce.WhenFalse, env, depth + 1);
            case IsPatternExpressionSyntax ip: return Pat(ip.Pattern, Ev(ip.Expression, env, depth + 1), env, depth + 1);
            case MemberAccessExpressionSyntax { Name.Identifier.Text: "Length" } ma: return Ev(ma.Expression, env, depth + 1) is string str ? str.Length : throw UnknownValue;
            case BinaryExpressionSyntax b: return Bin(b, env, depth + 1);
            case InvocationExpressionSyntax inv: return Invoke(inv, env, depth + 1);
        }
        throw UnknownValue;
    }

    object? Bin(BinaryExpressionSyntax b, ReqEnv env, int depth)
    {
        switch (b.Kind())
        {
            case SyntaxKind.LogicalOrExpression:
            case SyntaxKind.LogicalAndExpression:
                {
                    bool or = b.IsKind(SyntaxKind.LogicalOrExpression);
                    bool? l = null;
                    try { l = Truth(Ev(b.Left, env, depth)); } catch (UnkEx) { }
                    if (l == or) return or;
                    var r = Truth(Ev(b.Right, env, depth));
                    if (r == or) return or;
                    return l == null ? throw UnknownValue : !or;
                }
            case SyntaxKind.CoalesceExpression:
                return Ev(b.Left, env, depth) ?? Ev(b.Right, env, depth);
        }
        var x = Ev(b.Left, env, depth);
        var y = Ev(b.Right, env, depth);
        switch (b.Kind())
        {
            case SyntaxKind.EqualsExpression: return SameValue(x, y);
            case SyntaxKind.NotEqualsExpression: return !SameValue(x, y);
        }
        if (Num(x) is not { } n || Num(y) is not { } m) throw UnknownValue;
        return b.Kind() switch
        {
            SyntaxKind.LessThanExpression => n < m,
            SyntaxKind.LessThanOrEqualExpression => n <= m,
            SyntaxKind.GreaterThanExpression => n > m,
            SyntaxKind.GreaterThanOrEqualExpression => n >= m,
            _ => throw UnknownValue,
        };
    }

    bool Pat(PatternSyntax p, object? val, ReqEnv env, int depth)
    {
        switch (p)
        {
            case ParenthesizedPatternSyntax pp: return Pat(pp.Pattern, val, env, depth);
            case UnaryPatternSyntax { RawKind: (int)SyntaxKind.NotPattern } up: return !Pat(up.Pattern, val, env, depth);
            case BinaryPatternSyntax { RawKind: (int)SyntaxKind.OrPattern } bo:
                {
                    bool? l = null;
                    try { l = Pat(bo.Left, val, env, depth); } catch (UnkEx) { }
                    if (l == true) return true;
                    var r = Pat(bo.Right, val, env, depth);
                    return r || (l ?? throw UnknownValue);
                }
            case BinaryPatternSyntax { RawKind: (int)SyntaxKind.AndPattern } ba: return Pat(ba.Left, val, env, depth) && Pat(ba.Right, val, env, depth);
            case ConstantPatternSyntax cp:
                {
                    var c = Sm(cp).GetConstantValue(cp.Expression);
                    return c.HasValue ? SameValue(val, c.Value) : throw UnknownValue;
                }
            case RelationalPatternSyntax rp:
                {
                    var c = Sm(rp).GetConstantValue(rp.Expression);
                    if (!c.HasValue || Num(c.Value) is not { } m) throw UnknownValue;
                    if (val == null) return false;
                    if (Num(val) is not { } n) throw UnknownValue;
                    return rp.OperatorToken.Kind() switch
                    {
                        SyntaxKind.LessThanToken => n < m,
                        SyntaxKind.LessThanEqualsToken => n <= m,
                        SyntaxKind.GreaterThanToken => n > m,
                        SyntaxKind.GreaterThanEqualsToken => n >= m,
                        _ => throw UnknownValue,
                    };
                }
            case RecursivePatternSyntax { Type: null, PositionalPatternClause: null, PropertyPatternClause.Subpatterns.Count: 0 } rec:
                if (val == null) return false;
                if (rec.Designation is SingleVariableDesignationSyntax sv && Sm(sv).GetDeclaredSymbol(sv) is { } ds) env.Vars[ds] = val;
                return true;
        }
        throw UnknownValue;
    }

    object? Invoke(InvocationExpressionSyntax inv, ReqEnv env, int depth)
    {
        var c = Resolve(inv);
        if (c.Ambiguous != null) throw UnknownValue;
        if (c.Project && c.M != null) return CallProject(inv, c, env, depth);
        var args = inv.ArgumentList.Arguments;
        var recv = inv.Expression is MemberAccessExpressionSyntax ma ? ma.Expression : null;
        switch (c.Name)
        {
            case "IsNullOrEmpty" or "IsNullOrWhiteSpace" when args.Count == 1 && c.M?.ContainingType.SpecialType == SpecialType.System_String:
                {
                    var s = Ev(args[0].Expression, env, depth);
                    if (s != null && s is not string) throw UnknownValue;
                    return c.Name == "IsNullOrEmpty" ? string.IsNullOrEmpty((string?)s) : string.IsNullOrWhiteSpace((string?)s);
                }
            case "GetByteCount" when args.Count == 1:
                return Ev(args[0].Expression, env, depth) is string gb ? System.Text.Encoding.UTF8.GetByteCount(gb) : throw UnknownValue;
            case "Trim" or "TrimStart" or "TrimEnd" or "ToLowerInvariant" or "ToLower" or "ToUpperInvariant" or "ToUpper" when args.Count == 0 && recv != null:
                {
                    if (Ev(recv, env, depth) is not string rs) throw UnknownValue;
                    return c.Name switch { "Trim" => rs.Trim(), "TrimStart" => rs.TrimStart(), "TrimEnd" => rs.TrimEnd(), "ToLowerInvariant" or "ToLower" => rs.ToLowerInvariant(), _ => rs.ToUpperInvariant() };
                }
            case "Any" when args.Count == 1 && recv != null:
                return Ev(recv, env, depth) is "" ? false : throw UnknownValue;
        }
        throw UnknownValue;
    }

    object? CallProject(InvocationExpressionSyntax inv, Call c, ReqEnv env, int depth)
    {
        var m = c.M!;
        if (env.Calls >= 4 || BodyOf(DeclOf(m)) is not { } body) throw UnknownValue;
        var ne = new ReqEnv { Steps = env.Steps, Calls = env.Calls + 1 };
        foreach (var (p, e) in MapArgs(inv, c))
        {
            var tp = m.Parameters.FirstOrDefault(x => x.Name == p.Name) ?? p;
            try { ne.Vars[tp] = Ev(e, env, depth); } catch (UnkEx) { }
        }
        foreach (var p in m.Parameters) if (!ne.Vars.ContainsKey(p) && p.HasExplicitDefaultValue) ne.Vars[p] = p.ExplicitDefaultValue;
        if (body is not BlockSyntax blk) return Ev((ExpressionSyntax)body, ne, depth);
        Exec(blk, ne, depth);
        return ne.Returned ? ne.Ret : throw UnknownValue;
    }

    void Exec(StatementSyntax s, ReqEnv env, int depth)
    {
        if (env.Returned) return;
        if (++env.Steps[0] > 3000) throw UnknownValue;
        switch (s)
        {
            case BlockSyntax b: foreach (var x in b.Statements) { Exec(x, env, depth); if (env.Returned) return; } return;
            case LocalDeclarationStatementSyntax ld:
                foreach (var d in ld.Declaration.Variables)
                    if (d.Initializer != null && Sm(d).GetDeclaredSymbol(d) is { } sym)
                    {
                        try { env.Vars[sym] = Ev(d.Initializer.Value, env, depth); } catch (UnkEx) { env.Vars.Remove(sym); }
                    }
                return;
            case IfStatementSyntax ifs:
                if (Truth(Ev(ifs.Condition, env, depth))) Exec(ifs.Statement, env, depth);
                else if (ifs.Else != null) Exec(ifs.Else.Statement, env, depth);
                return;
            case ReturnStatementSyntax rs: env.Ret = rs.Expression == null ? null : Ev(rs.Expression, env, depth); env.Returned = true; return;
            case ThrowStatementSyntax th: throw new RefusedEx { Status = ErrorOf(th.Expression, null)?.Status };
            case ExpressionStatementSyntax es when es.Expression is not (AssignmentExpressionSyntax or PostfixUnaryExpressionSyntax or PrefixUnaryExpressionSyntax): return;
        }
        throw UnknownValue;
    }

    // ---- the verdict for one accessor ------------------------------------------------------------------------------------

    static bool Refusal4xx(RefusedEx r) => r.Status is >= 400 and < 500;

    /// <summary>The field is required when the handler refuses (4xx) with the accessor's value missing: null, or the default after `??`.</summary>
    bool NullRefuses(InvocationExpressionSyntax inv, Visit v)
    {
        var stmt = inv.Ancestors().OfType<StatementSyntax>().FirstOrDefault();
        if (stmt == null) return false;
        var env = new ReqEnv();
        env.Nodes[inv] = null;
        var tainted = new HashSet<ISymbol>(SymbolEqualityComparer.Default);
        bool Mentions(SyntaxNode n) => n.DescendantNodesAndSelf().OfType<IdentifierNameSyntax>().Any(i => Sm(i).GetSymbolInfo(i).Symbol is { } s && tainted.Contains(s));
        void Bind(VariableDeclaratorSyntax decl, ReqEnv e)
        {
            if (decl.Initializer == null || Sm(decl).GetDeclaredSymbol(decl) is not { } sym) return;
            tainted.Add(sym);
            try { env.Vars[sym] = Ev(decl.Initializer.Value, e, 0); } catch (UnkEx) { env.Vars.Remove(sym); }
        }
        try
        {
            switch (stmt)
            {
                case LocalDeclarationStatementSyntax ld:
                    foreach (var d in ld.Declaration.Variables) if (d.Initializer != null && d.Initializer.Value.Span.Contains(inv.Span)) Bind(d, env);
                    break;
                case ExpressionStatementSyntax es: Ev(es.Expression, env, 0); return false;
                case ReturnStatementSyntax { Expression: { } re }: Ev(re, env, 0); return false;
                case IfStatementSyntax ifs when ifs.Condition.Span.Contains(inv.Span):
                    return Truth(Ev(ifs.Condition, env, 0)) && Refuses(ifs.Statement) && RefusalStatus(ifs.Statement, v) is >= 400 and < 500;
                default: return false;
            }
        }
        catch (RefusedEx r) { return Refusal4xx(r); }
        catch (UnkEx) { return false; }
        if (stmt.Parent is not BlockSyntax block) return false;
        foreach (var next in block.Statements.SkipWhile(x => x != stmt).Skip(1))
        {
            if (tainted.Count == 0) return false;
            if (!Mentions(next)) continue;
            try
            {
                switch (next)
                {
                    case IfStatementSyntax ifs:
                        if (Truth(Ev(ifs.Condition, env, 0)) && Refuses(ifs.Statement) && RefusalStatus(ifs.Statement, v) is >= 400 and < 500) return true;
                        break;
                    case LocalDeclarationStatementSyntax ld:
                        foreach (var d in ld.Declaration.Variables) if (d.Initializer != null && Mentions(d.Initializer)) Bind(d, env);
                        break;
                    case ExpressionStatementSyntax es:
                        if (es.Expression is AssignmentExpressionSyntax { Left: IdentifierNameSyntax li } && Sm(li).GetSymbolInfo(li).Symbol is { } ls) { env.Vars.Remove(ls); tainted.Remove(ls); }
                        else Ev(es.Expression, env, 0);
                        break;
                }
            }
            catch (RefusedEx r) { if (Refusal4xx(r)) return true; }
            catch (UnkEx) { }
        }
        return false;
    }

    /// <summary>`if (!body.Has("k") || ...) throw 4xx` read at the top level of the handler: the key must be present.</summary>
    bool HasGuardRefuses(InvocationExpressionSyntax inv, Visit v)
    {
        SyntaxNode n = inv;
        while (n.Parent is ParenthesizedExpressionSyntax) n = n.Parent;
        if (n.Parent is not PrefixUnaryExpressionSyntax { RawKind: (int)SyntaxKind.LogicalNotExpression } not) return false;
        var ifs = not.Ancestors().OfType<IfStatementSyntax>().FirstOrDefault();
        return ifs != null && ifs.Else == null && Disjuncts(ifs.Condition).Contains(not) && Refuses(ifs.Statement) && RefusalStatus(ifs.Statement, v) is >= 400 and < 500;
    }

    bool AccessorRequired(InvocationExpressionSyntax inv, IMethodSymbol m, Visit v)
    {
        if (!Unconditional(inv, v)) return false;
        if (m.Name.StartsWith("Required", StringComparison.Ordinal) || inv.Parent is BinaryExpressionSyntax { RawKind: (int)SyntaxKind.CoalesceExpression, Right: ThrowExpressionSyntax }) return true;
        return m.Name == "Has" ? HasGuardRefuses(inv, v) : NullRefuses(inv, v);
    }
}
