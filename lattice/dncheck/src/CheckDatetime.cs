// DN806 naive/aware datetimes (PY806; active for .NET, where a DateTime carries a Kind): a comparison, subtraction or
// Compare/Max/Min between a DateTime of known Local kind (DateTime.Now/Today, ToLocalTime(), SpecifyKind(.., Local)) and one
// of known Utc kind (DateTime.UtcNow, ToUniversalTime(), DateTimeOffset.UtcDateTime, SpecifyKind(.., Utc)); and a DateTime of
// known Local or Unspecified kind implicitly converted to DateTimeOffset and compared with an offset value. Unknown kinds
// stay silent: EF reads a DateTime column as Unspecified, so a column compared with UtcNow is not claimed; DateTimeOffset
// values compare as instants and are never findings. The whole project is read (a hosted service is as good as a route).
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed partial class Ex
{
    enum DK { Unknown, Local, Utc, Unspecified }

    static readonly HashSet<string> KindKeeping = new(StringComparer.Ordinal) { "AddDays", "AddHours", "AddMinutes", "AddSeconds", "AddMilliseconds", "AddTicks", "AddMonths", "AddYears", "Add" };

    bool IsType(ITypeSymbol? t, string name) => t is { Name: var n } && n == name && !IsProject(t) && t.ContainingNamespace?.ToDisplayString() == "System";

    /// <summary>The Kind a DateTime expression is known to have.</summary>
    DK KindOf(ExpressionSyntax e, int depth = 0)
    {
        if (depth > 3) return DK.Unknown;
        e = Unwrap(e);
        if (e is AwaitExpressionSyntax aw) return KindOf(aw.Expression, depth + 1);
        var sm = Sm(e);
        switch (e)
        {
            case MemberAccessExpressionSyntax ma when sm.GetSymbolInfo(ma.Name).Symbol is IPropertySymbol ps:
                if (IsType(ps.ContainingType, "DateTime")) return ps.Name switch { "Now" or "Today" => DK.Local, "UtcNow" => DK.Utc, "Date" => KindOf(ma.Expression, depth + 1), _ => DK.Unknown };
                if (IsType(ps.ContainingType, "DateTimeOffset")) return ps.Name switch { "UtcDateTime" => DK.Utc, "LocalDateTime" => DK.Local, _ => DK.Unknown };
                return DK.Unknown;
            case InvocationExpressionSyntax inv when InvokedName(inv.Expression) is { } name:
                {
                    if (!IsType(sm.GetTypeInfo(inv).Type, "DateTime")) return DK.Unknown;
                    if (name == "ToLocalTime") return DK.Local;
                    if (name == "ToUniversalTime") return DK.Utc;
                    if (name == "SpecifyKind" && inv.ArgumentList.Arguments.Count == 2) return KindName(inv.ArgumentList.Arguments[1].Expression);
                    if (KindKeeping.Contains(name) && inv.Expression is MemberAccessExpressionSyntax rm) return KindOf(rm.Expression, depth + 1);
                    return DK.Unknown;
                }
            case BaseObjectCreationExpressionSyntax oc when IsType(sm.GetTypeInfo(oc).Type, "DateTime"):
                {
                    var args = (oc as ObjectCreationExpressionSyntax)?.ArgumentList?.Arguments ?? (oc as ImplicitObjectCreationExpressionSyntax)?.ArgumentList.Arguments ?? default;
                    if (args.Count >= 3 && KindName(args[^1].Expression) is var k && k != DK.Unknown) return k;
                    return args.Count >= 3 && !args.Any(a => sm.GetTypeInfo(a.Expression).Type?.Name == "DateTimeKind") && !args.Any(a => sm.GetTypeInfo(a.Expression).Type?.Name == "Calendar") ? DK.Unspecified : DK.Unknown;
                }
            case IdentifierNameSyntax id when sm.GetSymbolInfo(id).Symbol is ILocalSymbol ls && ls.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax() is VariableDeclaratorSyntax { Initializer.Value: { } init } decl:
                {
                    var scope = decl.Ancestors().OfType<MemberDeclarationSyntax>().FirstOrDefault() as SyntaxNode ?? decl.Ancestors().Last();
                    bool reassigned = scope.DescendantNodes().OfType<AssignmentExpressionSyntax>().Any(a => a.Left is IdentifierNameSyntax l && l.Identifier.Text == id.Identifier.Text);
                    return reassigned ? DK.Unknown : KindOf(init, depth + 1);
                }
        }
        return DK.Unknown;
    }

    DK KindName(ExpressionSyntax e) => Unwrap(e) is MemberAccessExpressionSyntax { Name.Identifier.Text: var n } && n is "Utc" or "Local" or "Unspecified" ? Enum.Parse<DK>(n) : DK.Unknown;

    void CheckDatetime(CheckPolicy pol, List<CFinding> found)
    {
        if (!pol.On("DN806")) return;
        foreach (var tree in Trees)
            foreach (var node in tree.GetRoot().DescendantNodes())
            {
                ExpressionSyntax? l = null, r = null;
                if (node is BinaryExpressionSyntax b && (b.IsKind(SyntaxKind.SubtractExpression) || b.IsKind(SyntaxKind.LessThanExpression) || b.IsKind(SyntaxKind.GreaterThanExpression) || b.IsKind(SyntaxKind.LessThanOrEqualExpression)
                    || b.IsKind(SyntaxKind.GreaterThanOrEqualExpression) || b.IsKind(SyntaxKind.EqualsExpression) || b.IsKind(SyntaxKind.NotEqualsExpression))) (l, r) = (b.Left, b.Right);
                else if (node is InvocationExpressionSyntax inv && InvokedName(inv.Expression) is "Compare" or "CompareTo" or "Subtract" or "Max" or "Min" or "Equals")
                {
                    var ops = inv.ArgumentList.Arguments.Select(a => a.Expression).ToList();
                    if (inv.Expression is MemberAccessExpressionSyntax rm && Sm(inv).GetSymbolInfo(rm.Expression).Symbol is not INamedTypeSymbol) ops.Insert(0, rm.Expression);
                    if (ops.Count == 2) (l, r) = (ops[0], ops[1]);
                }
                if (l == null || r == null) continue;
                var sm = Sm(node);
                var (lt, rt) = (sm.GetTypeInfo(l).Type, sm.GetTypeInfo(r).Type);
                string? why = null;
                if (IsType(lt, "DateTime") && IsType(rt, "DateTime"))
                {
                    var (lk, rk) = (KindOf(l), KindOf(r));
                    if (lk != DK.Unknown && rk != DK.Unknown && lk != rk && (lk is DK.Local or DK.Utc) && (rk is DK.Local or DK.Utc)) why = $"a {lk} DateTime is compared with a {rk} one";
                }
                else if ((IsType(lt, "DateTimeOffset") && IsType(rt, "DateTime")) || (IsType(lt, "DateTime") && IsType(rt, "DateTimeOffset")))
                {
                    var plain = IsType(lt, "DateTime") ? l : r;
                    var k = KindOf(plain);
                    if (k is DK.Local or DK.Unspecified) why = $"a {k} DateTime is converted to a DateTimeOffset as local time and compared with an offset value";
                }
                if (why == null) continue;
                var text = Js.Text(node);
                found.Add(Mk("DN806", node, $"DN806|{RouteOf(node)}|{text}", $"{RouteOf(node)}: {why} at line {LineOf(node)} (`{text}`)",
                    "use UTC on both sides (`DateTime.UtcNow`, `ToUniversalTime()`), or DateTimeOffset, which compares instants"));
            }
    }
}
