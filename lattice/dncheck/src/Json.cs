// Serialization (compact UTF-8, no escaping of '<' '>' '&', properties in declaration order), the System.Text.Json
// naming policies (ported, so wire names are computed exactly as the runtime computes them) and small text helpers.
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using Microsoft.CodeAnalysis;

namespace DnCheck;

public static class Js
{
    static readonly JsonSerializerOptions Opts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DictionaryKeyPolicy = null,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        WriteIndented = false,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.Never,
    };

    public static string Serialize(object o) => JsonSerializer.Serialize(o, o.GetType(), Opts);

    /// <summary>The wire name of a member under a System.Text.Json naming policy name; null for "as declared".</summary>
    public static string Apply(string? policy, string name) => policy switch
    {
        "CamelCase" => Camel(name),
        "SnakeCaseLower" => Separated(name, '_', false),
        "SnakeCaseUpper" => Separated(name, '_', true),
        "KebabCaseLower" => Separated(name, '-', false),
        "KebabCaseUpper" => Separated(name, '-', true),
        _ => name,
    };

    static string Camel(string s)
    {
        if (string.IsNullOrEmpty(s) || !char.IsUpper(s[0])) return s;
        var c = s.ToCharArray();
        for (int i = 0; i < c.Length; i++)
        {
            if (i == 1 && !char.IsUpper(c[i])) break;
            bool hasNext = i + 1 < c.Length;
            if (i > 0 && hasNext && !char.IsUpper(c[i + 1]))
            {
                if (c[i + 1] == ' ') c[i] = char.ToLowerInvariant(c[i]);
                break;
            }
            c[i] = char.ToLowerInvariant(c[i]);
        }
        return new string(c);
    }

    static string Separated(string s, char sep, bool upper)
    {
        var sb = new StringBuilder();
        int state = 0; // 0 none, 1 upper, 2 lower-or-digit, 3 separator
        for (int i = 0; i < s.Length; i++)
        {
            char ch = s[i];
            if (char.IsUpper(ch))
            {
                if (state == 2 || (state == 1 && i + 1 < s.Length && char.IsLower(s[i + 1]))) sb.Append(sep);
                sb.Append(upper ? ch : char.ToLowerInvariant(ch));
                state = 1;
            }
            else if (char.IsLower(ch) || char.IsDigit(ch))
            {
                sb.Append(upper ? char.ToUpperInvariant(ch) : ch);
                state = 2;
            }
            else if (ch == ' ' || ch == '_' || ch == '-')
            {
                if (state != 0) state = 3;
                sb.Append(ch == ' ' ? sep : ch);
            }
            else { sb.Append(ch); state = 0; }
        }
        return sb.ToString();
    }

    /// <summary>Source text on one line: runs of white space collapsed.</summary>
    public static string Text(SyntaxNode n)
    {
        var sb = new StringBuilder();
        bool sp = false;
        foreach (var ch in n.ToString())
        {
            if (char.IsWhiteSpace(ch)) { sp = sb.Length > 0; continue; }
            if (sp) sb.Append(' ');
            sp = false;
            sb.Append(ch);
        }
        return sb.ToString();
    }

    public static string Inv(object? o) => Convert.ToString(o, CultureInfo.InvariantCulture) ?? "";

    public static string Sha256(string s) => Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(s))).ToLowerInvariant();
}
