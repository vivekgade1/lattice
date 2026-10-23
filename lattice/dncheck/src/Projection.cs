// Policy mode: columns an entity never shows. An app that answers with hand-written response records (not the entity, so no
// [JsonIgnore]) hides a column by never reading it into a response. A column is derived private (`columns[].json == "-"`) when
// its entity is not itself a response type and no read of the column reaches a response: a creation of a response type (a type
// named by a handler's return type, or by the argument of Ok/Created/Json/StatusCode/Results.*, and the types it holds), an
// anonymous object, or the arguments of such a call. An entity handed whole to something that is not read (a non-project call,
// a field, a collection) is opaque: nothing is derived for it. A project method receiving the entity is read too (depth 2).
// Absence of a read is evidence only where every read can be seen (H-PA1: over-deriving names ordinary columns and yields false DN802
// findings): a projection over an `IQueryable<T>` parameter resolves its lambda to T, and a member read off a `Select` parameter the
// compilation cannot type counts as a read of every entity column of that name.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed partial class Ex
{
    static readonly HashSet<string> ResponseCalls = new(StringComparer.Ordinal) { "Ok", "Created", "CreatedAtAction", "CreatedAtRoute", "Accepted", "AcceptedAtAction", "Json", "StatusCode", "Content" };

    EntityInfo? EntityOfType(ITypeSymbol? t)
    {
        t = t == null ? null : Strip(t);
        if (t is IArrayTypeSymbol at) t = at.ElementType;
        else if (t is INamedTypeSymbol { TypeArguments.Length: 1 } g && !Entities.ContainsKey(g)) t = g.TypeArguments[0];
        return t is INamedTypeSymbol nt && Entities.TryGetValue(nt, out var ei) ? ei : null;
    }

    /// <summary>The entity an expression holds: its type when the compilation binds it (EF Core is not referenced, so a LINQ
    /// result is often unbound), else the entity of the query it came from (a local, a foreach variable).</summary>
    EntityInfo? EntityOfExpr(ExpressionSyntax e)
    {
        if (EntityOfType(Sm(e).GetTypeInfo(e).Type) is { } ei) return ei;
        if ((EntityOfLocal(e) ?? ChainOf(e)?.T) is { } t && Entities.TryGetValue(t, out var el)) return el;
        if (e is IdentifierNameSyntax id && Sm(id).GetSymbolInfo(id).Symbol is ILocalSymbol ls
            && ls.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax() is { Parent: ForEachStatementSyntax fe } && ChainOf(fe.Expression)?.T is { } ft && Entities.TryGetValue(ft, out var ef)) return ef;
        if (e is IdentifierNameSyntax lp && Sm(lp).GetSymbolInfo(lp).Symbol is IParameterSymbol ps
            && ps.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax() is { } pd
            && pd.AncestorsAndSelf().OfType<LambdaExpressionSyntax>().FirstOrDefault() is { Parent: ArgumentSyntax { Parent: ArgumentListSyntax { Parent: InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax lm } } } }
            && QuerySourceEntity(lm.Expression) is { } el2) return el2;
        return null;
    }

    /// <summary>A projection lambda's parameter whose type the compilation does not bind (`.Select(r => new Body(r.Id))` over a query EF Core
    /// is needed to type): what it reads is a column of the rows it projects, whichever entity that is.</summary>
    bool IsUnboundProjectionParameter(ExpressionSyntax e) =>
        e is IdentifierNameSyntax id && Sm(id).GetSymbolInfo(id).Symbol is IParameterSymbol { Type.TypeKind: TypeKind.Error } ps
        && ps.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax().Parent is var lp && (lp is ParameterListSyntax ? lp.Parent : lp) is LambdaExpressionSyntax { Parent: ArgumentSyntax { Parent: ArgumentListSyntax { Parent: InvocationExpressionSyntax inv } } }
        && InvokedName(inv.Expression) is "Select" or "SelectMany";

    /// <summary>The entity a query yields: the chain of a DbSet, else (a helper taking `IQueryable&lt;T&gt;` / `IEnumerable&lt;T&gt;`, `rows.AsNoTracking()`)
    /// the entity of the typed source under the element-keeping calls.</summary>
    EntityInfo? QuerySourceEntity(ExpressionSyntax e)
    {
        if (ChainOf(e)?.T is { } t && Entities.TryGetValue(t, out var ei)) return ei;
        while (true)
        {
            if (e is ParenthesizedExpressionSyntax p) e = p.Expression;
            else if (e is AwaitExpressionSyntax a) e = a.Expression;
            else if (e is InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax ma } inv && KeepsElem.Contains(InvokedName(inv.Expression) ?? "")) e = ma.Expression;
            else break;
        }
        return e is IdentifierNameSyntax or MemberAccessExpressionSyntax ? EntityOfType(Sm(e).GetTypeInfo(e).Type) : null;
    }

    void DeriveProjectionPrivacy()
    {
        if (!Policy || Entities.Count == 0) return;
        var resp = new HashSet<INamedTypeSymbol>(SymbolEqualityComparer.Default);
        var respDefs = new HashSet<INamedTypeSymbol>(SymbolEqualityComparer.Default);
        void AddType(ITypeSymbol? t, int depth)
        {
            if (t == null || depth > 5) return;
            t = Strip(t);
            if (t is IArrayTypeSymbol at) { AddType(at.ElementType, depth + 1); return; }
            if (t is not INamedTypeSymbol nt) return;
            foreach (var a in nt.TypeArguments) AddType(a, depth + 1);
            if (!IsProject(nt) || !resp.Add(nt)) return;
            respDefs.Add(nt.OriginalDefinition);
            foreach (var m in nt.GetMembers().OfType<IPropertySymbol>()) AddType(m.Type, depth + 1);
            foreach (var c in nt.Constructors) foreach (var p in c.Parameters) AddType(p.Type, depth + 1);
        }
        foreach (var rec in Recs) AddType(rec.HandlerSym?.ReturnType, 0);
        foreach (var inv in AllInvocations().Where(i => ResponseCalls.Contains(InvokedName(i.Expression) ?? "")))
            foreach (var a in inv.ArgumentList.Arguments) { AddType(Sm(inv).GetTypeInfo(a.Expression).Type, 0); if (EntityOfExpr(a.Expression) is { } ae) AddType(ae.T, 0); }

        var owner = new HashSet<ISymbol>(SymbolEqualityComparer.Default);
        foreach (var e in Entities.Values) foreach (var pi in e.Props) owner.Add(pi.P.OriginalDefinition);
        var read = new HashSet<ISymbol>(SymbolEqualityComparer.Default);
        var unresolved = new HashSet<string>(StringComparer.Ordinal); // member reads whose receiver is not known: evidence of a read, not of hiding
        var opaque = new HashSet<EntityInfo>();
        var scanned = new HashSet<SyntaxNode>();
        void Scan(SyntaxNode n, int depth)
        {
            if (!scanned.Add(n)) return;
            foreach (var x in n.DescendantNodesAndSelf())
            {
                var sm = Sm(x);
                if (x is MemberAccessExpressionSyntax ma && sm.GetSymbolInfo(ma.Name).Symbol is { } s && owner.Contains(s.OriginalDefinition)) read.Add(s.OriginalDefinition);
                else if (x is MemberAccessExpressionSyntax um && sm.GetSymbolInfo(um.Name).Symbol == null && EntityOfExpr(um.Expression) is { } ue && ue.Props.FirstOrDefault(p => p.P.Name == um.Name.Identifier.Text) is { } up) read.Add(up.P.OriginalDefinition);
                else if (x is MemberAccessExpressionSyntax nm && sm.GetSymbolInfo(nm.Name).Symbol == null && IsUnboundProjectionParameter(nm.Expression) && EntityOfExpr(nm.Expression) == null) unresolved.Add(nm.Name.Identifier.Text);
                else if (x is MemberBindingExpressionSyntax mb && sm.GetSymbolInfo(mb.Name).Symbol is { } bs && owner.Contains(bs.OriginalDefinition)) read.Add(bs.OriginalDefinition);
                else if (x is IdentifierNameSyntax id && sm.GetSymbolInfo(id).Symbol is ILocalSymbol or IParameterSymbol && EntityOfExpr(id) is { } ei)
                {
                    var p = id.Parent;
                    if ((p is MemberAccessExpressionSyntax m && m.Expression == id) || (p is ConditionalAccessExpressionSyntax ca && ca.Expression == id)) continue;
                    if (p is ArgumentSyntax { Parent: ArgumentListSyntax { Parent: InvocationExpressionSyntax call } } && Resolve(call) is { Project: true, M: { } pm } && depth < 2 && BodyOf(DeclOf(pm)) is { } pb) { Scan(pb, depth + 1); continue; }
                    opaque.Add(ei);
                }
            }
        }
        foreach (var tree in Trees)
            foreach (var n in tree.GetRoot().DescendantNodes())
                if (n is AnonymousObjectCreationExpressionSyntax
                    || (n is InvocationExpressionSyntax inv && ResponseCalls.Contains(InvokedName(inv.Expression) ?? ""))
                    || (n is BaseObjectCreationExpressionSyntax oc && Sm(oc).GetTypeInfo(oc).Type is INamedTypeSymbol ct && respDefs.Contains(ct.OriginalDefinition) && !Entities.ContainsKey(ct)))
                    Scan(n, 0);

        foreach (var e in Entities.Values)
        {
            if (e.Skip || resp.Contains(e.T) || opaque.Contains(e)) continue;
            foreach (var pi in e.Props)
                if (!read.Contains(pi.P.OriginalDefinition) && !unresolved.Contains(pi.P.Name)) pi.Col.Json = "-";
        }
    }
}
