// EF Core tables (N6): entity types reached from DbContext subclasses, their names and column facts from the model
// snapshot when one exists (EF's own record of the final model) and otherwise from attributes, the fluent
// configuration (OnModelCreating and IEntityTypeConfiguration<T> classes) and EF's conventions. Owned types,
// inheritance, keyless entities, views and the like are listed `unread`, never guessed.
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace DnCheck;

public sealed class PropInfo
{
    public IPropertySymbol P = null!;
    public XColumn Col = new();
    public string? ColName;
    public int? Max;
    public bool? Required;
    public string? Default;
    public bool DefaultSql, Key, Ignored, Hidden;
    public string? Conv;
    public bool CustomConv;
    public SyntaxNode? Decl;
}

public sealed class FkInfo
{
    public string Prop = "", Nav = "";
    public INamedTypeSymbol? Principal;
    public string? PrincipalKey;
}

public sealed class EntityInfo
{
    public INamedTypeSymbol T = null!;
    public XTable Tab = new();
    public List<PropInfo> Props = new();
    public Dictionary<string, (INamedTypeSymbol Target, bool Many)> Navs = new(StringComparer.Ordinal);
    public HashSet<string> Ignored = new(StringComparer.Ordinal);
    public List<string> Keys = new();
    public string? TableName, DbSetName, Schema;
    public List<(List<string> Props, bool Unique)> Indexes = new();
    public List<FkInfo> Fks = new();
    public bool Skip;
    public Dictionary<string, string> SnapCols = new(StringComparer.Ordinal);
}

public sealed partial class Ex
{
    public readonly Dictionary<INamedTypeSymbol, EntityInfo> Entities = new(SymbolEqualityComparer.Default);
    readonly List<INamedTypeSymbol> entityOrder = new();
    readonly HashSet<INamedTypeSymbol> ownedTypes = new(SymbolEqualityComparer.Default);
    readonly HashSet<string> configApplied = new(StringComparer.Ordinal);
    bool applyAll, snakeNames, otherNaming;

    // ---- attributes (by syntax: EF's own attributes are not referenced, so they stay unbound) -----------------------------

    public static IEnumerable<AttributeSyntax> AttrsOf(ISymbol s)
    {
        var res = new List<AttributeSyntax>();
        foreach (var d in s.DeclaringSyntaxReferences.Select(r => r.GetSyntax()).Distinct())
            switch (d)
            {
                case MemberDeclarationSyntax m: res.AddRange(m.AttributeLists.SelectMany(l => l.Attributes)); break;
                case ParameterSyntax p: res.AddRange(p.AttributeLists.SelectMany(l => l.Attributes)); break;
            }
        return res;
    }

    public static string AttrName(AttributeSyntax a)
    {
        var n = a.Name is QualifiedNameSyntax q ? q.Right.ToString() : a.Name.ToString();
        return n.EndsWith("Attribute", StringComparison.Ordinal) ? n[..^9] : n;
    }

    public AttributeSyntax? Attr(ISymbol s, string name) => AttrsOf(s).FirstOrDefault(a => AttrName(a) == name);

    public object? ArgValue(AttributeSyntax a, int pos, string? named = null)
    {
        var args = a.ArgumentList?.Arguments;
        if (args == null) return null;
        AttributeArgumentSyntax? arg = named != null ? args.Value.FirstOrDefault(x => x.NameEquals?.Name.Identifier.Text == named) : args.Value.Where(x => x.NameEquals == null).Skip(pos).FirstOrDefault();
        if (arg == null) return null;
        var cv = Sm(a).GetConstantValue(arg.Expression);
        return cv.HasValue ? cv.Value : arg.Expression is MemberAccessExpressionSyntax m ? m.Name.Identifier.Text : null;
    }

    // ---- types ---------------------------------------------------------------------------------------------------------

    static ITypeSymbol Strip(ITypeSymbol t) => t is INamedTypeSymbol { OriginalDefinition.SpecialType: SpecialType.System_Nullable_T } n ? n.TypeArguments[0] : t;

    public static string? LatticeType(ITypeSymbol t, string? conv = null)
    {
        t = Strip(t);
        if (t.TypeKind == TypeKind.Enum) return conv == "string" ? "Str" : "Int";
        switch (t.SpecialType)
        {
            case SpecialType.System_String: case SpecialType.System_Char: return "Str";
            case SpecialType.System_Boolean: return "Bool";
            case SpecialType.System_Single: case SpecialType.System_Double: case SpecialType.System_Decimal: return "Float";
            case SpecialType.System_DateTime: return "DateTime";
            case SpecialType.System_Byte: case SpecialType.System_SByte: case SpecialType.System_Int16: case SpecialType.System_UInt16: case SpecialType.System_Int32: case SpecialType.System_UInt32: case SpecialType.System_Int64: case SpecialType.System_UInt64: return "Int";
        }
        return t.Name switch { "Guid" => "Uuid", "DateTimeOffset" => "DateTime", "DateOnly" => "Date", _ => null };
    }

    bool IsScalar(ITypeSymbol t)
    {
        t = Strip(t);
        if (t.TypeKind == TypeKind.Enum || t.SpecialType != SpecialType.None) return true;
        if (t is IArrayTypeSymbol { ElementType.SpecialType: SpecialType.System_Byte }) return true;
        return t.Name is "Guid" or "DateTimeOffset" or "DateOnly" or "TimeOnly" or "TimeSpan" or "Uri" or "DateTime" or "CultureInfo" or "IPAddress" or "BigInteger";
    }

    (INamedTypeSymbol Target, bool Many)? NavType(ITypeSymbol t)
    {
        t = Strip(t);
        if (t is INamedTypeSymbol { IsGenericType: true, TypeArguments.Length: 1 } g && g.Name is "List" or "ICollection" or "IList" or "IEnumerable" or "HashSet" or "ISet" or "IReadOnlyCollection" or "IReadOnlyList" or "Collection")
            return g.TypeArguments[0] is INamedTypeSymbol e && IsProject(e) && e.TypeKind is TypeKind.Class or TypeKind.Struct ? (e, true) : null;
        return t is INamedTypeSymbol n && IsProject(n) && n.TypeKind == TypeKind.Class ? (n, false) : null;
    }

    // ---- discovery -------------------------------------------------------------------------------------------------------

    void AddEntity(INamedTypeSymbol t)
    {
        if (!IsProject(t) || t.TypeKind != TypeKind.Class || t.IsAbstract && false) return;
        if (Entities.ContainsKey(t)) return;
        Entities[t] = new EntityInfo { T = t };
        entityOrder.Add(t);
    }

    IEnumerable<InvocationExpressionSyntax> AllInvocations() => Trees.SelectMany(t => t.GetRoot().DescendantNodes().OfType<InvocationExpressionSyntax>());

    INamedTypeSymbol? GenericArg(InvocationExpressionSyntax inv, int i = 0) =>
        inv.Expression is MemberAccessExpressionSyntax { Name: GenericNameSyntax g } && g.TypeArgumentList.Arguments.Count > i ? Sm(inv).GetTypeInfo(g.TypeArgumentList.Arguments[i]).Type as INamedTypeSymbol : null;

    static string? GenericText(InvocationExpressionSyntax inv, int i = 0) =>
        inv.Expression is MemberAccessExpressionSyntax { Name: GenericNameSyntax g } && g.TypeArgumentList.Arguments.Count > i ? g.TypeArgumentList.Arguments[i].ToString() : null;

    void DiscoverEntities()
    {
        foreach (var kv in DbSets.OrderBy(k => Qual(k.Key.ContainingType), StringComparer.Ordinal).ThenBy(k => LineOf(DeclOf(k.Key)!)))
        {
            AddEntity(kv.Value);
            if (Entities.TryGetValue(kv.Value, out var ei) && ei.DbSetName == null) ei.DbSetName = kv.Key.Name;
        }
        foreach (var inv in AllInvocations())
        {
            var name = InvokedName(inv.Expression);
            if (name == "Entity" && GenericArg(inv) is { } et) AddEntity(et);
            else if (name == "ApplyConfigurationsFromAssembly") applyAll = true;
            else if (name == "ApplyConfiguration" && inv.ArgumentList.Arguments.Count == 1 && Sm(inv).GetTypeInfo(inv.ArgumentList.Arguments[0].Expression).Type is { } ct) configApplied.Add(QualType(ct));
            else if (name == "UseSnakeCaseNamingConvention") snakeNames = true;
            else if (name is "UseCamelCaseNamingConvention" or "UseLowerCaseNamingConvention" or "UseUpperCaseNamingConvention" or "UseUpperSnakeCaseNamingConvention") otherNaming = true;
        }
        foreach (var cfg in ConfigClasses()) if (cfg.Entity != null && (applyAll || configApplied.Contains(QualType(cfg.Type)))) AddEntity(cfg.Entity);
        // navigations from other entities
        for (int i = 0; i < entityOrder.Count; i++)
            foreach (var p in entityOrder[i].GetMembers().OfType<IPropertySymbol>())
                if (!p.IsStatic && NavType(p.Type) is { } nt && !ownedTypes.Contains(nt.Target) && Attr(nt.Target, "Owned") == null && Attr(p, "NotMapped") == null && !IsDbContextType(nt.Target)) AddEntity(nt.Target);
    }

    IEnumerable<(INamedTypeSymbol Type, INamedTypeSymbol? Entity, MethodDeclarationSyntax? Configure)> ConfigClasses()
    {
        foreach (var t in ProjTypes)
        {
            if (t.TypeKind != TypeKind.Class) continue;
            foreach (var decl in t.DeclaringSyntaxReferences.Select(r => r.GetSyntax()).OfType<ClassDeclarationSyntax>())
            {
                var bt = decl.BaseList?.Types.Select(x => x.Type).OfType<GenericNameSyntax>().FirstOrDefault(g => g.Identifier.Text == "IEntityTypeConfiguration");
                if (bt == null) continue;
                var ent = Sm(decl).GetTypeInfo(bt.TypeArgumentList.Arguments[0]).Type as INamedTypeSymbol;
                yield return (t, ent, decl.Members.OfType<MethodDeclarationSyntax>().FirstOrDefault(m => m.Identifier.Text == "Configure"));
            }
        }
    }

    // ---- the fluent configuration ---------------------------------------------------------------------------------------

    static List<InvocationExpressionSyntax> ChainSegments(InvocationExpressionSyntax top, out ExpressionSyntax root)
    {
        var segs = new List<InvocationExpressionSyntax>();
        ExpressionSyntax cur = top;
        while (cur is InvocationExpressionSyntax inv && inv.Expression is MemberAccessExpressionSyntax ma)
        {
            segs.Add(inv);
            cur = ma.Expression;
        }
        if (cur is ParenthesizedExpressionSyntax p) cur = p.Expression;
        root = cur;
        segs.Reverse();
        return segs;
    }

    List<string> LambdaProps(ExpressionSyntax? e)
    {
        var res = new List<string>();
        if (e is LambdaExpressionSyntax l)
        {
            var body = l is SimpleLambdaExpressionSyntax s ? s.Body : ((ParenthesizedLambdaExpressionSyntax)l).Body;
            switch (body)
            {
                case MemberAccessExpressionSyntax m: res.Add(m.Name.Identifier.Text); break;
                case AnonymousObjectCreationExpressionSyntax ao:
                    foreach (var i in ao.Initializers) res.Add(i.Expression is MemberAccessExpressionSyntax im ? im.Name.Identifier.Text : i.Expression.ToString());
                    break;
                case PostfixUnaryExpressionSyntax { Operand: MemberAccessExpressionSyntax pm }: res.Add(pm.Name.Identifier.Text); break;
            }
        }
        else if (e is LiteralExpressionSyntax lit && lit.IsKind(SyntaxKind.StringLiteralExpression)) res.Add(lit.Token.ValueText);
        return res;
    }

    INamedTypeSymbol? EntityOfBuilder(ExpressionSyntax root)
    {
        switch (root)
        {
            case InvocationExpressionSyntax inv when InvokedName(inv.Expression) == "Entity": return GenericArg(inv);
            case IdentifierNameSyntax id:
                var s = Sm(id).GetSymbolInfo(id).Symbol;
                var d = s?.DeclaringSyntaxReferences.FirstOrDefault()?.GetSyntax();
                if (d is ParameterSyntax { Type: GenericNameSyntax { Identifier.Text: "EntityTypeBuilder" } g }) return Sm(d).GetTypeInfo(g.TypeArgumentList.Arguments[0]).Type as INamedTypeSymbol;
                if (d is ParameterSyntax { Parent: ParameterListSyntax { Parent: ParenthesizedLambdaExpressionSyntax pl } } && pl.Parent is ArgumentSyntax { Parent: ArgumentListSyntax { Parent: InvocationExpressionSyntax li } } && InvokedName(li.Expression) == "Entity") return GenericArg(li);
                if (d is ParameterSyntax { Parent: SimpleLambdaExpressionSyntax } || d is ParameterSyntax { Parent.Parent: LambdaExpressionSyntax }) { var lam = d.Parent is SimpleLambdaExpressionSyntax ? d.Parent : d.Parent!.Parent; if (lam?.Parent is ArgumentSyntax { Parent: ArgumentListSyntax { Parent: InvocationExpressionSyntax li2 } } && InvokedName(li2.Expression) == "Entity") return GenericArg(li2); }
                if (d is SimpleLambdaExpressionSyntax sl && sl.Parent is ArgumentSyntax { Parent: ArgumentListSyntax { Parent: InvocationExpressionSyntax li3 } } && InvokedName(li3.Expression) == "Entity") return GenericArg(li3);
                if (d is VariableDeclaratorSyntax { Initializer.Value: { } iv }) return iv is InvocationExpressionSyntax ii ? (ChainSegments(ii, out var r2).Count > 0 ? EntityOfBuilder(r2) ?? (InvokedName(ii.Expression) == "Entity" ? GenericArg(ii) : null) : null) : null;
                return null;
        }
        return null;
    }

    void ReadFluent()
    {
        var seen = new HashSet<SyntaxNode>();
        foreach (var tree in Trees)
        {
            foreach (var top in tree.GetRoot().DescendantNodes().OfType<InvocationExpressionSyntax>())
            {
                // only maximal chains
                if (top.Parent is MemberAccessExpressionSyntax) continue;
                var segs = ChainSegments(top, out var root);
                if (segs.Count == 0) continue;
                var ent = EntityOfBuilder(root is InvocationExpressionSyntax ri && InvokedName(ri.Expression) == "Entity" ? ri : root);
                if (ent == null && segs.Count > 0 && InvokedName(segs[0].Expression) == "Entity") { ent = GenericArg(segs[0]); segs.RemoveAt(0); }
                if (ent == null || !Entities.TryGetValue(ent, out var ei)) continue;
                if (segs.Count > 0 && InvokedName(segs[0].Expression) == "Entity") segs.RemoveAt(0);
                ApplyChain(ei, segs, top);
            }
        }
    }

    PropInfo? PropOf(EntityInfo ei, string name) => ei.Props.FirstOrDefault(p => p.P.Name == name);

    void ApplyChain(EntityInfo ei, List<InvocationExpressionSyntax> segs, SyntaxNode where)
    {
        PropInfo? prop = null;
        List<string>? idx = null; bool inIdx = false;
        INamedTypeSymbol? relTarget = null; bool relMany = false, relHasOne = false;
        string relNav = "";
        foreach (var seg in segs)
        {
            var name = InvokedName(seg.Expression) ?? "";
            var args = seg.ArgumentList.Arguments;
            ExpressionSyntax? a0 = args.Count > 0 ? args[0].Expression : null;
            object? Const(int i) => i < args.Count && Sm(seg).GetConstantValue(args[i].Expression) is { HasValue: true } cv ? cv.Value : null;
            switch (name)
            {
                case "Property": prop = LambdaProps(a0).Select(n => PropOf(ei, n)).FirstOrDefault(); inIdx = false; break;
                case "HasKey": ei.Keys = LambdaProps(a0); break;
                case "ToTable":
                    if (Const(0) is string tn) { ei.TableName = tn; ei.Schema = Const(1) as string; ei.Tab.NameSource = "ToTable(\"" + tn + "\")"; }
                    else if (a0 != null && Const(0) == null) AddUnread(null, seg, "ToTable", "table name is not a compile-time constant", "table");
                    break;
                case "ToView": AddUnread(null, seg, "ToView on " + ei.T.Name, "views are not read", "table"); ei.Skip = true; break;
                case "HasNoKey": AddUnread(null, seg, "keyless entity " + ei.T.Name, "keyless entities are not read", "table"); ei.Skip = true; break;
                case "HasIndex": idx = LambdaProps(a0); if (a0 is not LambdaExpressionSyntax) idx = args.Select(x => Sm(seg).GetConstantValue(x.Expression).Value as string).Where(x => x != null).Cast<string>().ToList(); ei.Indexes.Add((idx, false)); inIdx = true; prop = null; break;
                case "IsUnique": if (inIdx && ei.Indexes.Count > 0) { var last = ei.Indexes[^1]; ei.Indexes[^1] = (last.Props, Const(0) as bool? ?? true); } break;
                case "HasOne": case "HasMany": relHasOne = name == "HasOne"; relNav = LambdaProps(a0).FirstOrDefault() ?? ""; relTarget = relNav != "" && ei.Navs.TryGetValue(relNav, out var nv) ? nv.Target : GenericArg(seg); relMany = name == "HasMany"; inIdx = false; prop = null; break;
                case "WithOne":
                case "WithMany":
                    if (relTarget != null && !relHasOne) { var navBack = LambdaProps(a0).FirstOrDefault() ?? ""; pendingRel = (relTarget, ei.T, navBack, relNav); }
                    else if (relTarget != null) pendingRel = (ei.T, relTarget, relNav, LambdaProps(a0).FirstOrDefault() ?? "");
                    break;
                case "HasForeignKey":
                    {
                        var fk = LambdaProps(a0);
                        if (pendingRel is { } pr && Entities.TryGetValue(pr.Dep, out var dep))
                            foreach (var f in fk) dep.Fks.Add(new FkInfo { Prop = f, Nav = pr.DepNav, Principal = pr.Prin });
                        pendingRel = null;
                        break;
                    }
                case "IsRequired": if (prop != null && !inIdx) prop.Required = Const(0) as bool? ?? true; break;
                case "HasMaxLength": if (prop != null && Const(0) is int ml) prop.Max = ml; break;
                case "HasColumnName": if (prop != null && Const(0) is string cn) prop.ColName = cn; break;
                case "HasDefaultValue": if (prop != null) { prop.Default = DefaultText(args.Count > 0 ? args[0].Expression : null); } break;
                case "HasDefaultValueSql": if (prop != null) { prop.DefaultSql = true; AddUnread(null, seg, ei.T.Name + "." + prop.P.Name + " default", "default is SQL (HasDefaultValueSql), not a constant", "table"); } break;
                case "HasConversion":
                    if (prop != null)
                    {
                        var g = GenericText(seg);
                        if (g is "string" or "int" or "long" or "short" or "byte" && a0 == null) prop.Conv = g == "string" ? "string" : "int";
                        else { prop.CustomConv = true; AddUnread(null, seg, "value converter on " + ei.T.Name + "." + prop.P.Name, "custom value converter is not read", "table"); }
                    }
                    break;
                case "HasQueryFilter": if (a0 != null) QueryFilterExprs.Add((ei, a0, seg)); break;
                case "Ignore": foreach (var n in LambdaProps(a0)) ei.Ignored.Add(n); break;
                case "OwnsOne":
                case "OwnsMany":
                    {
                        var nav = LambdaProps(a0).FirstOrDefault() ?? "";
                        var ot = ei.Navs.TryGetValue(nav, out var ov) ? ov.Target : ei.T.GetMembers(nav).OfType<IPropertySymbol>().Select(p => NavType(p.Type)?.Target).FirstOrDefault();
                        if (ot != null) { ownedTypes.Add(ot); Entities.Remove(ot); entityOrder.Remove(ot); }
                        ei.Ignored.Add(nav);
                        AddUnread(null, seg, $"owned type {ot?.Name ?? nav} ({name})", "owned types are not read", "table");
                        break;
                    }
                case "ComplexProperty": ei.Ignored.Add(LambdaProps(a0).FirstOrDefault() ?? ""); AddUnread(null, seg, "complex type on " + ei.T.Name, "complex types are not read", "table"); break;
                case "ToJson": AddUnread(null, seg, "JSON column on " + ei.T.Name, "JSON columns are not read", "table"); break;
                case "HasDiscriminator": inheritanceAt.TryAdd(ei.T, seg); ei.Skip = true; break;
                case "HasValue": if (GenericArg(seg) is { } dt) { AddEntity(dt); Entities[dt].Skip = true; } break;
                case "HasBaseType": inheritanceAt.TryAdd(ei.T, seg); ei.Skip = true; break;
                case "HasCheckConstraint": if (Const(1) is string ck) ei.Tab.Checks.Add(ck); break;
                case "HasData": D.Notes.Add($"{FileOf(seg)}:{LineOf(seg)} HasData seeding of {ei.T.Name} is not read"); break;
                case "IsTemporal": AddUnread(null, seg, "temporal table " + ei.T.Name, "temporal tables are not read", "table"); break;
            }
        }
    }

    (INamedTypeSymbol Prin, INamedTypeSymbol Dep, string DepNav, string PrinNav)? pendingRel;
    readonly Dictionary<INamedTypeSymbol, SyntaxNode> inheritanceAt = new(SymbolEqualityComparer.Default);
    public readonly List<(EntityInfo Ent, ExpressionSyntax Lambda, SyntaxNode At)> QueryFilterExprs = new();

    string? DefaultText(ExpressionSyntax? e)
    {
        if (e == null) return null;
        if (e is MemberAccessExpressionSyntax or IdentifierNameSyntax && Sm(e).GetSymbolInfo(e).Symbol is IFieldSymbol { ContainingType.TypeKind: TypeKind.Enum } ef) return ef.Name;
        var cv = Sm(e).GetConstantValue(e);
        if (cv.HasValue) return cv.Value switch { bool b => b ? "true" : "false", null => null, var v => Js.Inv(v) };
        return e is MemberAccessExpressionSyntax m ? m.Name.Identifier.Text : null;
    }

    // ---- building the tables --------------------------------------------------------------------------------------------

    public void BuildTables()
    {
        DiscoverEntities();
        // inheritance / table reachability: hierarchies are unread
        foreach (var t in entityOrder.ToList())
        {
            var b = t.BaseType;
            if (b != null && Entities.ContainsKey(b)) { Entities[t].Skip = true; Entities[b].Skip = true; }
        }
        foreach (var t in entityOrder.ToList())
        {
            var ei = Entities[t];
            ei.Tab.Struct = t.Name;
            ei.Tab.Package = t.ContainingNamespace.ToDisplayString();
            var td = DeclOf(t)!;
            ei.Tab.File = FileOf(td);
            ei.Tab.Line = LineOf(td is BaseTypeDeclarationSyntax bd ? bd.Identifier.GetLocation().SourceTree!.GetRoot().FindToken(bd.Identifier.SpanStart).Parent! : td);
            ei.Tab.Source = "efcore";
            CollectProps(ei);
        }
        ReadFluent();
        foreach (var ei in Entities.Values) FinishEntity(ei);
        foreach (var ei in Entities.Values) FindForeignKeys(ei);
        ApplySnapshot();
        foreach (var t in entityOrder.ToList())
        {
            var ei = Entities[t];
            if (ei.Skip) continue;
            Finalize(ei);
        }
        ReportInheritance();
        foreach (var t in ownedTypes) Entities.Remove(t);
        foreach (var t in entityOrder)
            if (Entities.TryGetValue(t, out var ei) && !ei.Skip) D.Tables.Add(ei.Tab);
    }

    void CollectProps(EntityInfo ei)
    {
        var chain = new List<INamedTypeSymbol>();
        for (var b = ei.T; b != null && b.SpecialType != SpecialType.System_Object; b = b.BaseType) { if (!SymbolEqualityComparer.Default.Equals(b, ei.T) && Entities.ContainsKey(b)) break; if (IsProject(b)) chain.Add(b); }
        chain.Reverse();
        foreach (var ct in chain)
            foreach (var p in ct.GetMembers().OfType<IPropertySymbol>().Where(p => !p.IsStatic && !p.IsIndexer && p.DeclaredAccessibility == Accessibility.Public).OrderBy(p => p.Locations.First().SourceSpan.Start))
            {
                if (p.GetMethod == null || p.SetMethod == null && !(DeclOf(p) is PropertyDeclarationSyntax { ExpressionBody: null, AccessorList: { } al } && al.Accessors.Any(a => a.IsKind(SyntaxKind.InitAccessorDeclaration)))) continue;
                if (Attr(p, "NotMapped") != null) continue;
                if (NavType(p.Type) is { } nt && !IsScalar(p.Type)) { ei.Navs[p.Name] = nt; continue; }
                if (!IsScalar(p.Type)) { AddUnread(null, DeclOf(p)!, ei.T.Name + "." + p.Name + " : " + p.Type.ToDisplayString(), "a property of a non-scalar type that is not an entity is not read (owned, complex, JSON or converted)", "table"); ei.Ignored.Add(p.Name); continue; }
                ei.Props.Add(new PropInfo { P = p, Decl = DeclOf(p) });
            }
    }

    void FinishEntity(EntityInfo ei)
    {
        // attribute facts
        if (Attr(ei.T, "Table") is { } ta && ArgValue(ta, 0) is string tn) { ei.TableName ??= tn; ei.Tab.NameSource ??= "[Table(\"" + tn + "\")]"; }
        if (Attr(ei.T, "Keyless") != null) { ei.Skip = true; AddUnread(null, DeclOf(ei.T)!, "keyless entity " + ei.T.Name, "keyless entities are not read", "table"); }
        foreach (var ia in AttrsOf(ei.T).Where(a => AttrName(a) == "Index"))
        {
            var cols = ia.ArgumentList!.Arguments.Where(x => x.NameEquals == null).Select(x => Sm(ia).GetConstantValue(x.Expression).Value as string).Where(x => x != null).Cast<string>().ToList();
            ei.Indexes.Add((cols, ArgValue(ia, 0, "IsUnique") as bool? ?? false));
        }
        foreach (var pi in ei.Props)
        {
            var p = pi.P;
            if (Attr(p, "Key") != null) pi.Key = true;
            if (Attr(p, "Required") != null) pi.Required ??= true;
            if (Attr(p, "Column") is { } ca && ArgValue(ca, 0) is string cn) pi.ColName ??= cn;
            if ((Attr(p, "MaxLength") ?? Attr(p, "StringLength")) is { } ma && ArgValue(ma, 0) is int ml) pi.Max ??= ml;
            if (p.IsRequired) pi.Required ??= true;
        }
        if (ei.Keys.Count > 0) foreach (var k in ei.Keys) if (PropOf(ei, k) is { } kp) kp.Key = true;
        if (!ei.Props.Any(p => p.Key))
        {
            var key = ei.Props.FirstOrDefault(p => p.P.Name.Equals("Id", StringComparison.OrdinalIgnoreCase)) ?? ei.Props.FirstOrDefault(p => p.P.Name.Equals(ei.T.Name + "Id", StringComparison.OrdinalIgnoreCase));
            if (key != null) key.Key = true;
        }
    }

    void FindForeignKeys(EntityInfo ei)
    {
        // foreign keys by convention and attribute
        foreach (var kv in ei.Navs)
        {
            if (kv.Value.Many || !Entities.TryGetValue(kv.Value.Target, out var pe)) continue;
            if (ei.Fks.Any(f => f.Nav == kv.Key)) continue;
            var fkAttr = Attr(ei.T.GetMembers(kv.Key).First(), "ForeignKey");
            var names = new List<string>();
            if (fkAttr != null && ArgValue(fkAttr, 0) is string fn) names.Add(fn);
            else
            {
                var pk = pe.Props.FirstOrDefault(p => p.Key)?.P.Name ?? "Id";
                names.Add(kv.Key + pk); names.Add(kv.Value.Target.Name + pk); names.Add(kv.Key + "Id"); names.Add(kv.Value.Target.Name + "Id"); if (pk != "Id") names.Add(pk);
            }
            var hit = names.Select(n => PropOf(ei, n)).FirstOrDefault(x => x != null && !x.Key);
            if (hit != null) ei.Fks.Add(new FkInfo { Prop = hit.P.Name, Nav = kv.Key, Principal = kv.Value.Target });
        }
        foreach (var pi in ei.Props)
            if (Attr(pi.P, "ForeignKey") is { } fa && ArgValue(fa, 0) is string nav && ei.Navs.TryGetValue(nav, out var nt) && !ei.Fks.Any(f => f.Prop == pi.P.Name)) ei.Fks.Add(new FkInfo { Prop = pi.P.Name, Nav = nav, Principal = nt.Target });
    }

    void ReportInheritance()
    {
        foreach (var root in entityOrder.Where(t => Entities.TryGetValue(t, out var e) && e.Skip && (t.BaseType == null || !Entities.ContainsKey(t.BaseType))))
        {
            var members = entityOrder.Where(t => { for (var b = t; b != null; b = b.BaseType) if (SymbolEqualityComparer.Default.Equals(b, root)) return true; return false; }).ToList();
            if (members.Count < 2) continue;
            var at = inheritanceAt.TryGetValue(root, out var s) ? s : DeclOf(root)!;
            bool disc = inheritanceAt.ContainsKey(root);
            AddUnread(null, at, $"TPH inheritance {string.Join(" / ", members.Select(m => m.Name))}" + (disc ? " (HasDiscriminator)" : ""), "inheritance mapping is not read", "table");
        }
    }

    string SqlName(string n) => snakeNames ? Js.Apply("SnakeCaseLower", n) : n;

    void Finalize(EntityInfo ei)
    {
        var tab = ei.Tab;
        var jn = D.Dotnet.JsonNaming;
        if (ei.TableName != null) { tab.Table = ei.TableName; }
        else if (otherNaming) { tab.Table = ei.DbSetName ?? ei.T.Name; tab.NameUnread = true; tab.NameSource = "naming plugin (not read)"; }
        else if (ei.DbSetName != null) { tab.Table = SqlName(ei.DbSetName); tab.NameSource = snakeNames ? "convention:snake_case" : $"DbSet<{ei.T.Name}> {ei.DbSetName}"; }
        else { tab.Table = SqlName(ei.T.Name); tab.NameSource = snakeNames ? "convention:snake_case" : "class name"; }
        var pk = ei.Props.Where(p => p.Key).ToList();
        foreach (var pi in ei.Props)
        {
            var p = pi.P;
            var c = pi.Col;
            c.Field = p.Name;
            c.Name = pi.ColName ?? (otherNaming ? p.Name : SqlName(p.Name));
            c.GoType = TypeText(p, pi.Decl);
            var st = Strip(p.Type);
            c.Type = LatticeType(p.Type, pi.Conv);
            bool nullableRef = st.IsReferenceType && (p.NullableAnnotation == NullableAnnotation.Annotated || P.Nullable.ToLowerInvariant() != "enable" && !pk.Contains(pi));
            bool nullableVal = p.Type is INamedTypeSymbol { OriginalDefinition.SpecialType: SpecialType.System_Nullable_T };
            c.Nullable = pk.Contains(pi) || pi.Required == true ? false : pi.Required == false ? true : nullableVal || nullableRef;
            c.PrimaryKey = pk.Contains(pi);
            c.Default = pi.Default;
            c.Required = !c.Nullable && !c.PrimaryKey && c.Default == null;
            c.MaxLength = pi.Max;
            c.Line = LineOf(pi.Decl ?? DeclOf(p)!);
            if (st.TypeKind == TypeKind.Enum)
            {
                c.EnumStorage = pi.CustomConv ? null : pi.Conv ?? "int";
                c.Choices = st.GetMembers().OfType<IFieldSymbol>().Where(f => f.HasConstantValue).Select(f => f.Name).ToList();
            }
            var jig = Attr(p, "JsonIgnore");
            bool ignored = jig != null && !(jig.ArgumentList?.Arguments.Any(a => a.NameEquals?.Name.Identifier.Text == "Condition" && !a.Expression.ToString().EndsWith("Always")) ?? false);
            var jpn = Attr(p, "JsonPropertyName") is { } jp ? ArgValue(jp, 0) as string : null;
            c.Private = ignored;
            c.Wire = ignored ? null : jpn ?? Js.Apply(jn, p.Name);
            if (Policy) c.Json = ignored ? "-" : c.Wire;
        }
        foreach (var ix in ei.Indexes)
        {
            var cols = ix.Props.Select(n => PropOf(ei, n)).ToList();
            if (cols.Any(x => x == null)) continue;
            if (cols.Count == 1) { if (ix.Unique) cols[0]!.Col.Unique = true; else cols[0]!.Col.Index = true; }
            else if (ix.Unique) tab.UniqueTogether.Add(cols.Select(x => x!.Col.Name).ToList());
        }
        foreach (var fk in ei.Fks)
            if (PropOf(ei, fk.Prop) is { } fp && fk.Principal != null && Entities.TryGetValue(fk.Principal, out var pe))
            {
                var key = pe.Props.FirstOrDefault(p => p.Key);
                if (key != null) { fp.Col.ForeignKey = TableNameOf(pe) + "." + (key.ColName ?? SqlName(key.P.Name)); }
            }
        tab.Columns = ei.Props.Select(p => p.Col).ToList();
        D.Dotnet.DbContexts = DbContextTypes.Select(Qual2).Distinct().OrderBy(x => x, StringComparer.Ordinal).ToList();
    }

    static string Qual2(INamedTypeSymbol t) => QualType(t);

    string TableNameOf(EntityInfo pe) => pe.Tab.Table != "" ? pe.Tab.Table : pe.TableName ?? pe.DbSetName ?? pe.T.Name;

    static string TypeText(IPropertySymbol p, SyntaxNode? decl) => decl is PropertyDeclarationSyntax pd ? Js.Text(pd.Type) : p.Type.ToDisplayString();

    /// <summary>The owner path a navigation chain implies: (parent entity, foreign key column of the child).</summary>
    public (EntityInfo Parent, string FkCol)? OwnerVia(INamedTypeSymbol child, string nav)
    {
        if (!Entities.TryGetValue(child, out var ce) || !ce.Navs.TryGetValue(nav, out var nt) || !Entities.TryGetValue(nt.Target, out var pe)) return null;
        var fk = ce.Fks.FirstOrDefault(f => f.Nav == nav) ?? ce.Fks.FirstOrDefault(f => f.Principal != null && SymbolEqualityComparer.Default.Equals(f.Principal, nt.Target));
        return fk != null && PropOf(ce, fk.Prop) is { } fp ? (pe, fp.Col.Name) : null;
    }

    public XColumn? ColumnOf(INamedTypeSymbol t, string prop) => Entities.TryGetValue(t, out var e) ? PropOf(e, prop)?.Col : null;
}

public sealed partial class Ex
{
    static readonly (string Call, string Dialect, string Provider)[] ProviderCalls =
    [
        ("UseMySql", "mysql", "mysql"), ("UseMySQL", "mysql", "mysql"), ("UseSqlite", "sqlite", "sqlite"),
        ("UseNpgsql", "postgres", "postgres"), ("UseSqlServer", "sqlserver", "sqlserver"), ("UseInMemoryDatabase", "", "inmemory"),
    ];

    /// <summary>The provider calls of the project (N6): one provider names the dialect, several leave it empty.</summary>
    void ResolveProviders()
    {
        var found = new SortedSet<string>(StringComparer.Ordinal);
        foreach (var inv in AllInvocations())
            foreach (var (call, _, prov) in ProviderCalls)
                if (InvokedName(inv.Expression) == call) found.Add(prov);
        D.Dotnet.Providers = found.ToList();
        var dialects = found.Where(p => p != "inmemory").ToList();
        D.Dialect = dialects.Count == 1 ? dialects[0] : "";
        if (dialects.Count == 1 && dialects[0] == "sqlserver") D.Notes.Add("provider sqlserver: no Lattice runtime");
    }

    // ---- the model snapshot (syntactic: migration files are not part of the compilation) ----------------------------------

    static object? Lit(ExpressionSyntax? e) => e switch
    {
        LiteralExpressionSyntax l when l.IsKind(SyntaxKind.TrueLiteralExpression) => true,
        LiteralExpressionSyntax l when l.IsKind(SyntaxKind.FalseLiteralExpression) => false,
        LiteralExpressionSyntax l when l.IsKind(SyntaxKind.NullLiteralExpression) => null,
        LiteralExpressionSyntax l => l.Token.Value,
        PrefixUnaryExpressionSyntax { RawKind: (int)SyntaxKind.UnaryMinusExpression, Operand: LiteralExpressionSyntax l } => l.Token.Value is int i ? -i : null,
        CastExpressionSyntax c => Lit(c.Expression),
        MemberAccessExpressionSyntax m => m.Name.Identifier.Text,
        _ => null,
    };

    void ApplySnapshot()
    {
        var snaps = MigrationTrees.Where(t => t.FilePath.EndsWith("ModelSnapshot.cs", StringComparison.Ordinal)).OrderBy(t => t.FilePath, StringComparer.Ordinal).ToList();
        if (snaps.Count == 0) return;
        string marker = D.Dialect switch { "mysql" => "MySql", "sqlite" => "Sqlite", "postgres" => "Npgsql", "sqlserver" => "SqlServer", _ => "" };
        var snap = (marker != "" ? snaps.FirstOrDefault(t => t.GetText().ToString().Contains(marker)) : null) ?? snaps[0];
        D.Dotnet.Snapshot = snap.FilePath;
        if (snaps.Count > 1) D.Notes.Add($"{snaps.Count} model snapshots ({string.Join(", ", snaps.Select(s => s.FilePath))}): {snap.FilePath} is read");
        foreach (var inv in snap.GetRoot().DescendantNodes().OfType<InvocationExpressionSyntax>())
        {
            if (InvokedName(inv.Expression) != "Entity" || inv.ArgumentList.Arguments.Count < 2 || Lit(inv.ArgumentList.Arguments[0].Expression) is not string fqn) continue;
            var lam = inv.ArgumentList.Arguments[1].Expression as LambdaExpressionSyntax;
            var body = lam is SimpleLambdaExpressionSyntax sl ? sl.Body : (lam as ParenthesizedLambdaExpressionSyntax)?.Body;
            if (body is not BlockSyntax blk) continue;
            var sym = Comp.GetTypeByMetadataName(fqn);
            if (sym == null || !Entities.TryGetValue(sym, out var ei)) continue;
            var keys = new List<string>(); var snapIdx = new List<(List<string>, bool)>(); var fks = new List<FkInfo>();
            bool sawIdx = false, sawKey = false;
            foreach (var st in blk.Statements.OfType<ExpressionStatementSyntax>())
            {
                if (st.Expression is not InvocationExpressionSyntax top) continue;
                var segs = ChainSegments(top, out _);
                if (segs.Count == 0) continue;
                var first = segs[0];
                var fname = InvokedName(first.Expression);
                var args = first.ArgumentList.Arguments.Select(a => Lit(a.Expression)).ToList();
                if (fname == "Property" && args.Count > 0 && args[0] is string pn && PropOf(ei, pn) is { } pi)
                {
                    pi.ColName = pn; bool req = false;
                    foreach (var s in segs.Skip(1))
                    {
                        var a = s.ArgumentList.Arguments.Select(x => Lit(x.Expression)).ToList();
                        switch (InvokedName(s.Expression))
                        {
                            case "HasMaxLength": if (a.Count > 0 && a[0] is int ml) pi.Max = ml; break;
                            case "HasColumnName": if (a.Count > 0 && a[0] is string cn) pi.ColName = cn; break;
                            case "IsRequired": req = true; break;
                            case "HasDefaultValue": pi.Default = a.Count > 0 && a[0] != null ? (a[0] is bool b ? (b ? "true" : "false") : Js.Inv(a[0])) : null; break;
                            case "HasDefaultValueSql": pi.DefaultSql = true; break;
                        }
                    }
                    if (req) pi.Required = true; else if (first.Expression is MemberAccessExpressionSyntax { Name: GenericNameSyntax g } && g.TypeArgumentList.Arguments[0].ToString().EndsWith("?") || pi.P.Type.IsReferenceType) pi.Required ??= false;
                }
                else if (fname == "HasKey") { sawKey = true; keys.AddRange(args.OfType<string>()); }
                else if (fname == "HasIndex") { sawIdx = true; snapIdx.Add((args.OfType<string>().ToList(), segs.Skip(1).Any(s => InvokedName(s.Expression) == "IsUnique"))); }
                else if (fname == "ToTable" && args.Count > 0 && args[0] is string tn) { if (ei.TableName != null && ei.TableName != tn || ei.TableName == null && ei.DbSetName != null && SqlName(ei.DbSetName) != tn) D.Notes.Add($"table of {ei.T.Name}: the code says {ei.TableName ?? ei.DbSetName}, the snapshot {snap.FilePath} says {tn} (the snapshot wins)"); ei.TableName = tn; ei.Tab.NameSource = "snapshot"; ei.Tab.MigratedAt = new() { new Loc { File = snap.FilePath, Line = LineOf(first) } }; }
                else if (fname == "HasOne" && segs.Any(s => InvokedName(s.Expression) == "HasForeignKey"))
                {
                    var psym = args.Count > 0 && args[0] is string pf ? Comp.GetTypeByMetadataName(pf) : null;
                    var fkp = segs.First(s => InvokedName(s.Expression) == "HasForeignKey").ArgumentList.Arguments.Select(a => Lit(a.Expression)).OfType<string>().ToList();
                    var nav = args.Count > 1 && args[1] is string nn ? nn : "";
                    foreach (var f in fkp) fks.Add(new FkInfo { Prop = f, Nav = nav, Principal = psym });
                }
            }
            if (sawKey) { foreach (var p in ei.Props) p.Key = keys.Contains(p.P.Name); }
            if (sawIdx || ei.Indexes.Count > 0 && snapIdx.Count > 0) ei.Indexes = snapIdx;
            if (fks.Count > 0) { ei.Fks.RemoveAll(f => fks.Any(g => g.Prop == f.Prop)); ei.Fks.AddRange(fks); }
        }
    }
}
