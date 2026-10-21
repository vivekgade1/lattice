// The lattice.dnextract/1 document: every key of lattice.goextract/1 plus one `dotnet` key. Property names are
// snake_case on the wire (the serializer's policy); a null optional key is omitted where the Go type had `omitempty`.
using System.Text.Json.Serialization;

namespace DnCheck;

public sealed class Loc
{
    public string File { get; set; } = "";
    public int Line { get; set; }
}

public sealed class Unread
{
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string What { get; set; } = "";
    public string Reason { get; set; } = "";
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Category { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Route { get; set; }
}

public sealed class Excluded
{
    public string Kind { get; set; } = "";
    public string What { get; set; } = "";
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Method { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Path { get; set; }
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string Reason { get; set; } = "";
}

public sealed class XRaise
{
    public int? Status { get; set; }
    public string? Code { get; set; }
    public string? Detail { get; set; }
    public int Line { get; set; }
    public string File { get; set; } = "";
    public string Kind { get; set; } = "return";
    public string In { get; set; } = "";
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Sentinel { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public bool? Bind { get; set; }
}

public sealed class XGuard
{
    public string Kind { get; set; } = "";
    public string Source { get; set; } = "";
    public int? Status { get; set; }
    public string? Detail { get; set; }
    public int Line { get; set; }
    public string File { get; set; } = "";
}

public sealed class XParam
{
    public string Name { get; set; } = "";
    public string Annotation { get; set; } = "";
    public string? Type { get; set; }
    public string Source { get; set; } = "";
    public bool Required { get; set; }
    public string? Default { get; set; }
    public long? Ge { get; set; }
    public long? Le { get; set; }
    public int? MinLength { get; set; }
    public int? MaxLength { get; set; }
    public List<string> Choices { get; set; } = new();
    public string File { get; set; } = "";
    public int Line { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Parse { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? ParseAt { get; set; }
    public string? Wire { get; set; }
    public string? BindSource { get; set; }
    public string? EnforcedBy { get; set; }
}

public sealed class XOwner
{
    public string Table { get; set; } = "";
    public string Model { get; set; } = "";
    public string Column { get; set; } = "";
    public string How { get; set; } = "compare";
    public int Line { get; set; }
    public string File { get; set; } = "";
    public string? Value { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public OwnerVia? Via { get; set; }
}

public sealed class OwnerVia
{
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Table { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Parent { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Fk { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Call { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? ResolvedTo { get; set; }
}

public sealed class XHandler
{
    public string Func { get; set; } = "";
    public string Module { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public List<string> Wrappers { get; set; } = new();
}

public sealed class XMwRef
{
    public string Func { get; set; } = "";
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? External { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? File { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public int? Line { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public List<string>? Wrappers { get; set; }
    public string Via { get; set; } = "";
    public bool Resolved { get; set; }
}

public sealed class XRoute
{
    public string Method { get; set; } = "";
    public string Path { get; set; } = "";
    public string PathTemplate { get; set; } = "";
    public bool PathExact { get; set; } = true;
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string? Condition { get; set; }
    public List<string> RouterChain { get; set; } = new();
    public XHandler? Handler { get; set; }
    public List<XMwRef> Middleware { get; set; } = new();
    public string Auth { get; set; } = "Public";
    public string AuthReason { get; set; } = "";
    public bool Member { get; set; }
    public List<XGuard> Guards { get; set; } = new();
    public List<XParam> Params { get; set; } = new();
    public List<XRaise> Raises { get; set; } = new();
    public List<XOwner> OwnerUses { get; set; } = new();
    public List<string> Tables { get; set; } = new();
    public List<string> BodyModels { get; set; } = new();
    public bool Tx { get; set; }
    public List<string> Calls { get; set; } = new();
    public int? StatusCode { get; set; }
    public List<int> StatusCodes { get; set; } = new();
    public string? ErrorWriter { get; set; }
    public List<Unread> Unread { get; set; } = new();
    public string Style { get; set; } = "minimal";
    public string Template { get; set; } = "";
}

public sealed class XMw
{
    public string Func { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public List<string> Wrappers { get; set; } = new();
    public bool Authenticating { get; set; }
    public List<string> RecordsCaller { get; set; } = new();
    public bool MentionsAdmin { get; set; }
    public List<XRaise> Raises { get; set; } = new();
    public string? UsersTable { get; set; }
    public bool Bearer { get; set; }
    public List<Unread> Unread { get; set; } = new();
    public string Kind { get; set; } = "middleware";
}

public sealed class XCtor
{
    public string Func { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public int? Status { get; set; }
    public int? StatusParam { get; set; }
    public string? Code { get; set; }
    public int? CodeParam { get; set; }
}

public sealed class XSentinel
{
    public string Name { get; set; } = "";
    public string Package { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string? Message { get; set; }
    public int? Status { get; set; }
    public string? Code { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Writer { get; set; }
}

public sealed class XWriteMap
{
    public string Sentinel { get; set; } = "";
    public int Status { get; set; }
    public string? Code { get; set; }
    public int Line { get; set; }
}

public sealed class XWriter
{
    public string Func { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public List<string> Wrappers { get; set; } = new();
    public List<XWriteMap> Maps { get; set; } = new();
    public List<string> AsTypes { get; set; } = new();
    public XWriteMap? CatchAll { get; set; }
}

public sealed class XAuth
{
    public string? Middleware { get; set; }
    public string? Scheme { get; set; }
    public string? UsersTable { get; set; }
    public string? LoginPath { get; set; }
    public string? PasswordColumn { get; set; }
    public string? UsernameField { get; set; }
    public string? PasswordField { get; set; }
    public string LoginEncoding { get; set; } = "json";
    public string? TokenField { get; set; }
    public List<string> CallerKeys { get; set; } = new();
    public List<string> Notes { get; set; } = new();
}

public sealed class XColumn
{
    public string Name { get; set; } = "";
    public string Field { get; set; } = "";
    public string GoType { get; set; } = "";
    public string? Type { get; set; }
    public bool Nullable { get; set; }
    public bool Required { get; set; }
    public string? Default { get; set; }
    public bool PrimaryKey { get; set; }
    public bool Unique { get; set; }
    public bool Index { get; set; }
    public int? MaxLength { get; set; }
    public string? ForeignKey { get; set; }
    public int Line { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Json { get; set; }
    public string? Wire { get; set; }
    public bool Private { get; set; }
    public string? EnumStorage { get; set; }
    public List<string>? Choices { get; set; }
}

public sealed class XTable
{
    public string Struct { get; set; } = "";
    public string Table { get; set; } = "";
    public bool NameUnread { get; set; }
    public string Package { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public string Source { get; set; } = "";
    public List<Loc> MigratedAt { get; set; } = new();
    public List<XColumn> Columns { get; set; } = new();
    public List<List<string>> UniqueTogether { get; set; } = new();
    public List<string> Checks { get; set; } = new();
    public string? NameSource { get; set; }
}

public sealed class XModel
{
    public string Name { get; set; } = "";
    public string Package { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public List<XParam> Fields { get; set; } = new();
    public string Kind { get; set; } = "request";
}

public sealed class XStatusW
{
    public string Table { get; set; } = "";
    public string Model { get; set; } = "";
    public string Column { get; set; } = "";
    public string Value { get; set; } = "";
    public string Root { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
    public int ValueLine { get; set; }
    public string Guard { get; set; } = "";
    public string In { get; set; } = "";
    public string? Stored { get; set; }
    public string Kind { get; set; } = "assign";
    public string Route { get; set; } = "";
    public List<string>? From { get; set; }
    public GuardInfo? GuardInfo { get; set; }
}

public sealed class GuardInfo
{
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public int? StatusIfNot { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public string? Where { get; set; }
    public string File { get; set; } = "";
    public int Line { get; set; }
}

public sealed class DiReg
{
    public string Interface { get; set; } = "";
    public string Implementation { get; set; } = "";
    public string Lifetime { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
}

public sealed class DiCall
{
    public string Route { get; set; } = "";
    public string Call { get; set; } = "";
    public string? ResolvedTo { get; set; }
    public string? Reason { get; set; }
    public string File { get; set; } = "";
    public int Line { get; set; }
}

public sealed class DiInfo
{
    public List<DiReg> Registrations { get; set; } = new();
    public List<string> Single { get; set; } = new();
    public List<string> Multiple { get; set; } = new();
    public List<DiCall> ResolvedCalls { get; set; } = new();
    public List<DiCall> UnreadCalls { get; set; } = new();
}

public sealed class ExceptionMapEntry
{
    public string Type { get; set; } = "";
    public int Status { get; set; }
    public string Via { get; set; } = "";
    public string File { get; set; } = "";
    public int Line { get; set; }
}

public sealed class QueryFilter
{
    public string Table { get; set; } = "";
    public string Column { get; set; } = "";
    public string Expr { get; set; } = "";
    public bool Owner { get; set; }
    public string? ReadsCallerVia { get; set; }
    public string File { get; set; } = "";
    public int Line { get; set; }
    public List<string> IgnoredBy { get; set; } = new();
    public List<string> AppliesToRoutes { get; set; } = new();
}

public sealed class ValidationInfo
{
    public List<string> AnnotationsEnforcedOn { get; set; } = new();
    public bool MinimalApiValidation { get; set; }
    public List<string> Fluentvalidation { get; set; } = new();
}

public sealed class DnAuthInfo
{
    public string? AdminRole { get; set; }
    public List<string> CallerKeys { get; set; } = new();
    public List<string> CallerFuncs { get; set; } = new();
    public string? FallbackPolicy { get; set; }
    public string? UsersTableSql { get; set; }
    public List<string> Pipeline { get; set; } = new();
}

public sealed class DotnetInfo
{
    public string Sdk { get; set; } = "";
    public string TargetFramework { get; set; } = "";
    public string SdkProject { get; set; } = "";
    public string Nullable { get; set; } = "";
    public List<string> Providers { get; set; } = new();
    public string JsonNaming { get; set; } = "CamelCase";
    public SortedDictionary<string, string> JsonNamingByStyle { get; set; } = new(StringComparer.Ordinal);
    public bool JsonInputCaseInsensitive { get; set; } = true;
    public int? BindStatus { get; set; }
    public List<string> GlobalUsings { get; set; } = new();
    public DiInfo Di { get; set; } = new();
    public ValidationInfo Validation { get; set; } = new();
    public List<ExceptionMapEntry> ExceptionMap { get; set; } = new();
    public List<QueryFilter> QueryFilters { get; set; } = new();
    public string? Snapshot { get; set; }
    public DnAuthInfo Auth { get; set; } = new();
    public List<string> DbContexts { get; set; } = new();
    public List<string> ProjectReferences { get; set; } = new();
}

public sealed class Doc
{
    public string Schema { get; set; } = "lattice.dnextract/1";
    public string Module { get; set; } = "";
    public string Go { get; set; } = "";
    public string Dialect { get; set; } = "";
    public List<string> Main { get; set; } = new();
    public List<XTable> Tables { get; set; } = new();
    public List<XRoute> Routes { get; set; } = new();
    public List<XMw> Middleware { get; set; } = new();
    public List<XCtor> Constructors { get; set; } = new();
    public List<XSentinel> Sentinels { get; set; } = new();
    public XWriter? ErrorWriter { get; set; }
    public List<XWriter> ErrorWriters { get; set; } = new();
    public XAuth? Auth { get; set; }
    public List<XModel> Models { get; set; } = new();
    public List<XStatusW> StatusWrites { get; set; } = new();
    public List<Excluded> Excluded { get; set; } = new();
    public List<Unread> Unread { get; set; } = new();
    public List<string> Notes { get; set; } = new();
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public List<object>? ParamUses { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public List<object>? Creates { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public List<object>? ParamLiterals { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public List<object>? StatusFacts { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public List<object>? WorkflowUnread { get; set; }
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] public List<string>? WorkflowNotes { get; set; }
    public DotnetInfo Dotnet { get; set; } = new();
}

/// <summary>The extension point of B3/B4: a pass added in its own file runs after the base extraction (workflow keys, checks).</summary>
public interface IExtractPass
{
    int Order { get; }
    void Run(Ex ex, Doc doc);
}
