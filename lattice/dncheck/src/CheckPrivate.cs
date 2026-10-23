// DN802 private columns (PY802): in a route that is not admin-only (or a helper it calls), a response (`Ok`, `Created*`,
// `Accepted*`, `Json`, `TypedResults.Ok`, a handler's `return value`) that serializes a private column: an entity (a
// collection, a DTO field, a navigation of one) whose private property carries no `[JsonIgnore]`, or an anonymous object or
// DTO built from `row.PrivateProperty`. `[JsonIgnore]` with a condition (WhenWritingNull/Default) is not claimed: the value
// may be absent, and a unprovable case stays silent. Cannot see: values copied through containers or helpers' arguments.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed partial class Ex
{
    static readonly HashSet<string> ValueResults = new(StringComparer.Ordinal) { "Ok", "Created", "CreatedAtAction", "CreatedAtRoute", "Accepted", "AcceptedAtAction", "AcceptedAtRoute", "Json", "StatusCode" };
    static readonly HashSet<string> ResultTypes = new(StringComparer.Ordinal) { "OkObjectResult", "CreatedResult", "CreatedAtActionResult", "CreatedAtRouteResult", "AcceptedResult", "ObjectResult", "JsonResult" };
    static readonly HashSet<string> IgnoreAttrs = new(StringComparer.Ordinal) { "JsonIgnore", "IgnoreDataMember", "NonSerialized" };

    /// <summary>Whether the serializer skips the property: an ignore attribute (with any condition: not claimed otherwise).</summary>
    bool Hidden(IPropertySymbol p) => AttrsOf(p).Any(a => IgnoreAttrs.Contains(AttrName(a))) || p.GetAttributes().Any(a => a.AttributeClass is { Name: "JsonConverterAttribute" });

    ITypeSymbol Element(ITypeSymbol t)
    {
        if (t is IArrayTypeSymbol arr) return arr.ElementType;
        if (t is INamedTypeSymbol { IsGenericType: true } g)
        {
            if (g.Name is "Task" or "ValueTask" or "ActionResult" or "Nullable" or "IEnumerable" or "List" or "IList" or "ICollection" or "IReadOnlyList" or "IReadOnlyCollection" or "IQueryable" or "HashSet" or "ISet" or "Collection" or "Ok") return Element(g.TypeArguments[0]);
            if (g.Name is "Dictionary" or "IDictionary" or "IReadOnlyDictionary" or "KeyValuePair" && g.TypeArguments.Length == 2) return Element(g.TypeArguments[1]);
        }
        return t;
    }

    /// <summary>The (entity, property) pairs of the policy's private columns a value of type `t` serializes.</summary>
    void Leaks(ITypeSymbol t, Dictionary<INamedTypeSymbol, HashSet<string>> priv, HashSet<ITypeSymbol> seen, List<(INamedTypeSymbol E, string P)> into, int depth = 0)
    {
        t = Element(t);
        if (depth > 3 || t is not INamedTypeSymbol nt || nt.SpecialType != SpecialType.None || nt.TypeKind == TypeKind.Enum || !seen.Add(nt)) return;
        if (!nt.IsAnonymousType && !IsProject(nt)) return;
        if (nt.GetAttributes().Any(a => a.AttributeClass is { Name: "JsonConverterAttribute" })) return;
        foreach (var p in nt.GetMembers().OfType<IPropertySymbol>().Where(p => p.DeclaredAccessibility == Accessibility.Public && !p.IsStatic && !p.IsIndexer))
        {
            if (!nt.IsAnonymousType && Hidden(p)) continue;
            if (priv.TryGetValue(nt, out var names) && names.Contains(p.Name)) into.Add((nt, p.Name));
            else Leaks(p.Type, priv, seen, into, depth + 1);
        }
    }

    /// <summary>The expressions a route answers with: the value of a result factory, and what the handler itself returns.</summary>
    IEnumerable<(ExpressionSyntax E, SyntaxNode At)> Responses(Reach reach)
    {
        foreach (var v in reach.Visits)
        {
            foreach (var n in v.Body.DescendantNodesAndSelf())
            {
                if (n is InvocationExpressionSyntax inv && ValueResults.Contains(InvokedName(inv.Expression) ?? "") && FrameworkResultCall(inv, Resolve(inv)) && inv.ArgumentList.Arguments.Count > (InvokedName(inv.Expression) == "StatusCode" ? 1 : 0))
                    yield return (InvokedName(inv.Expression) == "Json" ? inv.ArgumentList.Arguments[0].Expression : inv.ArgumentList.Arguments.Last().Expression, inv);
                else if (n is BaseObjectCreationExpressionSyntax oc && Sm(oc).GetTypeInfo(oc).Type is { } ot && ResultTypes.Contains(ot.Name) && oc.ArgumentList?.Arguments.Count > 0)
                    yield return (oc.ArgumentList.Arguments[0].Expression, oc);
            }
            if (v.Depth == 0)
                foreach (var re in Returns(v.Body))
                    if (Sm(re).GetTypeInfo(re).Type is { } rt && rt.Name is not ("IResult" or "IActionResult") && !rt.AllInterfaces.Any(i => i.Name is "IResult" or "IActionResult")) yield return (re, re);
        }
    }

    /// <summary>The expression and what its local variables were built from (a projection assigned first, then answered).</summary>
    IEnumerable<SyntaxNode> Sources(ExpressionSyntax e, int depth = 0)
    {
        yield return e;
        if (depth > 3) yield break;
        foreach (var id in e.DescendantNodesAndSelf().OfType<IdentifierNameSyntax>())
            if (Sm(id).GetSymbolInfo(id).Symbol is ILocalSymbol ls && ls.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax() is VariableDeclaratorSyntax { Initializer.Value: { } init })
                foreach (var s in Sources(init, depth + 1)) yield return s;
        // the answer is what a project method returns (`Ok(await service.Get(id))`): what that method returns is built from the row
        if (Bare(e) is InvocationExpressionSyntax call && Resolve(call) is { Project: true, M: { } m, Ambiguous: null } c && !FrameworkResultCall(call, c))
        {
            var target = DiTarget(m, out _, out _) ?? m;
            if (BodyOf(DeclOf(target)) is { } body)
                foreach (var r in Returned(body))
                    foreach (var s in Sources(r, depth + 1)) yield return s;
        }
    }

    /// <summary>Whether the member access is copied into what is answered (an initializer, a constructor argument, the answer itself), not just tested.</summary>
    static bool Copied(SyntaxNode ma)
    {
        SyntaxNode cur = ma;
        for (var p = cur.Parent; p != null; cur = p, p = p.Parent)
        {
            switch (p)
            {
                case ParenthesizedExpressionSyntax or PostfixUnaryExpressionSyntax or CastExpressionSyntax or ConditionalExpressionSyntax or AwaitExpressionSyntax: continue;
                case BinaryExpressionSyntax b when b.IsKind(SyntaxKind.CoalesceExpression): continue;
                case AnonymousObjectMemberDeclaratorSyntax: return true;
                case AssignmentExpressionSyntax a when a.Parent is InitializerExpressionSyntax && a.Right == cur: return true;
                case ArgumentSyntax { Parent: ArgumentListSyntax { Parent: BaseObjectCreationExpressionSyntax } }: return true;
                case ArgumentSyntax { Parent: ArgumentListSyntax { Parent: InvocationExpressionSyntax i } } when InvokedName(i.Expression) is "Ok" or "Json" or "Created": return true;
                default: return false;
            }
        }
        return true;
    }

    void CheckPrivate(CheckPolicy pol, List<CFinding> found)
    {
        if (!pol.On("DN802") || pol.Private.Count == 0) return;
        var priv = new Dictionary<INamedTypeSymbol, HashSet<string>>(SymbolEqualityComparer.Default);
        var label = new Dictionary<(INamedTypeSymbol, string), string>();
        foreach (var kv in pol.Private)
        {
            if (EntityFor(pol, kv.Key) is not { } ent) continue;
            foreach (var col in kv.Value)
                if (PropFor(ent, col) is { } pi) { (priv.TryGetValue(ent.T, out var s) ? s : priv[ent.T] = new()).Add(pi.P.Name); label[(ent.T, pi.P.Name)] = $"{kv.Key}.{col}"; }
        }
        foreach (var rec in Judged())
        {
            if (AdminOnly(rec, pol)) continue;
            var reported = new HashSet<string>(StringComparer.Ordinal);
            void Report(SyntaxNode at, INamedTypeSymbol e, string p, string how)
            {
                var subject = label[(e, p)];
                if (!reported.Add(subject)) return;
                found.Add(Mk("DN802", at, $"DN802|{rec.Id}|{subject}", $"{rec.Id} answers with {subject} ({how}) at line {LineOf(at)} (`{Js.Text(at)}`): a private column reaches the response",
                    $"leave {subject} out of the response: a DTO without it, or `[JsonIgnore]` on {e.Name}.{p}"));
            }
            foreach (var (e, at) in Responses(rec.Reach!))
            {
                var leaks = new List<(INamedTypeSymbol, string)>();
                if (Sm(e).GetTypeInfo(e).Type is { } ty) Leaks(ty, priv, new HashSet<ITypeSymbol>(SymbolEqualityComparer.Default), leaks);
                foreach (var (le, lp) in leaks) Report(at, le, lp, "the entity is serialized with the column");
                foreach (var src in Sources(e))
                    foreach (var ma in src.DescendantNodesAndSelf().OfType<MemberAccessExpressionSyntax>())
                    {
                        // the row's type: Roslyn's, else (EF is not referenced: a query's result is unbound) the entity the variable holds
                        var et = Sm(ma).GetTypeInfo(ma.Expression).Type as INamedTypeSymbol;
                        if (et == null || IsErr(et)) et = EntityOfLocal(ma.Expression);
                        if (et != null && priv.TryGetValue(et, out var names) && names.Contains(ma.Name.Identifier.Text) && Copied(ma))
                            Report(ma, et, ma.Name.Identifier.Text, "copied into the answer");
                    }
            }
        }
    }
}
