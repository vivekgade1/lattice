// DN803 bounds (PY803): a request value (N5: a body DTO field, a query/form parameter) written to a column or used as
// Take(n)/Skip(n) with no enforced bound. Enforced is B1's reading (`params[]`: an annotation under [ApiController], a
// FluentValidation validator the handler calls and refuses on, a handler guard): a string needs a maximum length, a number
// an upper bound; booleans, ids, dates and enumerations are bounded by their type. A refusing `if` on the value before the
// write bounds it. Only a value carried unchanged is followed (a member of the bound DTO, a handler parameter, a local built
// from one, through Trim/ToLower/Parse): a value passed through Math.Min, a helper or an expression is not judged.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed partial class Ex
{
    static readonly HashSet<string> PassCalls = new(StringComparer.Ordinal) { "Trim", "TrimStart", "TrimEnd", "ToLower", "ToLowerInvariant", "ToUpper", "ToUpperInvariant", "Normalize", "ToString", "Parse", "ToInt32", "ToInt64", "ToDecimal", "ToDouble" };

    /// <summary>The request value an expression carries unchanged, with the name a finding gives it.</summary>
    XParam? RequestValue(RouteRec rec, ExpressionSyntax e, out string subject, int depth = 0)
    {
        subject = "";
        if (depth > 4) return null;
        e = Unwrap(e);
        switch (e)
        {
            case AwaitExpressionSyntax aw: return RequestValue(rec, aw.Expression, out subject, depth + 1);
            case BinaryExpressionSyntax { RawKind: (int)SyntaxKind.CoalesceExpression } b: return RequestValue(rec, b.Left, out subject, depth + 1);
            case InvocationExpressionSyntax inv when PassCalls.Contains(InvokedName(inv.Expression) ?? ""):
                {
                    var inner = inv.Expression is MemberAccessExpressionSyntax rm && Sm(inv).GetSymbolInfo(rm.Expression).Symbol is not INamedTypeSymbol ? rm.Expression : inv.ArgumentList.Arguments.FirstOrDefault()?.Expression;
                    return inner == null ? null : RequestValue(rec, inner, out subject, depth + 1);
                }
            case MemberAccessExpressionSyntax ma when Sm(ma).GetSymbolInfo(ma.Name).Symbol is IPropertySymbol ps && IsProject(ps) && Unwrap(ma.Expression) is IdentifierNameSyntax rid
                    && Sm(rid).GetSymbolInfo(rid).Symbol is IParameterSymbol or ILocalSymbol && RequestTypes(rec).Any(t => Derives(t, ps.ContainingType)):
                {
                    var wire = Wire(ps.Name, rec.R.Style, ps);
                    subject = ps.ContainingType.Name + "." + ps.Name;
                    return rec.R.Params.FirstOrDefault(x => x.Source != "path" && x.Name == wire) ?? rec.R.Params.FirstOrDefault(x => x.Source != "path" && Same(x.Name, ps.Name));
                }
            case IdentifierNameSyntax id:
                {
                    var sym = Sm(id).GetSymbolInfo(id).Symbol;
                    if (sym is IParameterSymbol pp && rec.Params.Any(x => SymbolEqualityComparer.Default.Equals(x, pp)))
                    {
                        subject = pp.Name;
                        return rec.R.Params.FirstOrDefault(x => x.Source is "query" or "form" or "body" or "header" && (x.Name == pp.Name || Same(x.Name, pp.Name)));
                    }
                    if (sym is ILocalSymbol ls && ls.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax() is VariableDeclaratorSyntax { Initializer.Value: { } init }) return RequestValue(rec, init, out subject, depth + 1);
                    return null;
                }
        }
        return null;
    }

    /// <summary>The project types the request is bound to: the handler's DTO parameters.</summary>
    IEnumerable<INamedTypeSymbol> RequestTypes(RouteRec rec) =>
        rec.Params.Select(p => p.Type).OfType<INamedTypeSymbol>().Where(t => t.SpecialType == SpecialType.None && IsProject(t) && !Entities.ContainsKey(t));

    static bool Bounded(XParam p, bool limit) => p.Type switch
    {
        "Str" => p.EnforcedBy != null && (p.MaxLength != null || p.Choices.Count > 0),
        "Int" or "Float" => p.EnforcedBy != null && (p.Le != null || p.Choices.Count > 0),
        _ => true,
    };

    bool GuardedBefore(RouteRec rec, Visit v, SyntaxNode sink, XParam p) =>
        v.Body.DescendantNodes().OfType<IfStatementSyntax>().Any(i => i.Span.End <= sink.SpanStart && Refuses(i.Statement)
            && i.Condition.DescendantNodesAndSelf().OfType<ExpressionSyntax>().Any(x => RequestValue(rec, x, out _) == p));

    void CheckBounds(CheckPolicy pol, List<CFinding> found)
    {
        if (!pol.On("DN803")) return;
        foreach (var rec in Judged())
        {
            var reported = new HashSet<string>(StringComparer.Ordinal);
            foreach (var v in rec.Reach!.Visits)
                foreach (var node in v.Body.DescendantNodesAndSelf())
                {
                    ExpressionSyntax? value = null; string target = "", what = ""; bool limit = false;
                    switch (node)
                    {
                        case AssignmentExpressionSyntax asg when asg.IsKind(SyntaxKind.SimpleAssignmentExpression):
                            if (ColumnRef(asg.Left, null, null) is { Nav: null } cr) (value, target) = (asg.Right, cr.Entity.Name + "." + cr.Prop);
                            else if (asg.Left is IdentifierNameSyntax lid && asg.Parent is InitializerExpressionSyntax { Parent: BaseObjectCreationExpressionSyntax oc }
                                     && Sm(oc).GetTypeInfo(oc).Type is INamedTypeSymbol et && Entities.TryGetValue(et, out var ei) && PropOf(ei, lid.Identifier.Text) != null)
                                (value, target) = (asg.Right, et.Name + "." + lid.Identifier.Text);
                            what = "written to";
                            break;
                        case InvocationExpressionSyntax inv when InvokedName(inv.Expression) == "SetProperty" && inv.ArgumentList.Arguments.Count == 2:
                            {
                                var up = inv.Ancestors().OfType<InvocationExpressionSyntax>().FirstOrDefault(i => InvokedName(i.Expression) is "ExecuteUpdate" or "ExecuteUpdateAsync");
                                if (up?.Expression is MemberAccessExpressionSyntax uma && ChainOf(uma.Expression)?.T is { } ut && LambdaProps(inv.ArgumentList.Arguments[0].Expression).FirstOrDefault() is { } lp)
                                    (value, target, what) = (inv.ArgumentList.Arguments[1].Expression, ut.Name + "." + lp, "written to");
                                break;
                            }
                        case InvocationExpressionSyntax inv when InvokedName(inv.Expression) is "Take" or "Skip" && inv.ArgumentList.Arguments.Count == 1 && inv.Expression is MemberAccessExpressionSyntax:
                            (value, target, what, limit) = (inv.ArgumentList.Arguments[0].Expression, InvokedName(inv.Expression)!, "used as", true);
                            break;
                    }
                    if (value == null || RequestValue(rec, value, out var subject) is not { } p || Bounded(p, limit) || GuardedBefore(rec, v, node, p) || !reported.Add(subject)) continue;
                    found.Add(Mk("DN803", node, $"DN803|{rec.Id}|{subject}",
                        $"{rec.Id} takes {subject} from the request and it is {what} {target} at line {LineOf(node)} (`{Js.Text(node)}`) with no enforced bound",
                        limit ? $"bound {subject} before it is used: `Math.Clamp`, or refuse a value above the maximum"
                              : $"bound {subject} where the request is read: a length or range annotation under [ApiController], a validator the handler runs, or a refusing check before the write"));
                }
        }
    }
}
