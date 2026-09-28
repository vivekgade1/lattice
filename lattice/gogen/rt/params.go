package rt

import (
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"sync"
)

// ---- parameter specs (the prelude's Param) ------------------------------------------------

type unsetT struct{}
type nullT struct{}

// Unset is the value of an optional parameter that was not sent (the prelude's UNSET):
// update() keeps the column it would write.
var Unset any = unsetT{}

// Null is ParamSpec.Default for a parameter whose default is None (`p: T | None = None`).
var Null any = nullT{}

// ParamSpec is one endpoint parameter: the prelude's Param, field for field.
// Scalar is Int | Float | Str | Bool | Date | DateTime | Uuid; Source is path | query |
// body. Default is nil for none (the prelude's MISSING), Unset for `= UNSET` (with
// Optional), Null for a None default, else the value (int64, float64, string, bool,
// Date, DateTime; a Uuid default as its canonical string or a UUID). Nullable (with
// Optional): an explicit JSON null is accepted and sets NULL. Pattern (nil: none) is a
// regular expression the whole Str value must match; Email: it is Email's pattern
// (only the message differs).
type ParamSpec struct {
	Name, Source, Scalar string
	Ge, Le               *float64
	MinLen, MaxLen       *int64
	OneOf                []string // nil: no OneOf
	Default              any
	Optional, Nullable   bool
	Pattern              *string
	Email                bool
}

// F, I and S are pointers to a constant, for ParamSpec literals.
func F(v float64) *float64 { return &v }
func I(v int64) *int64     { return &v }
func S(v string) *string   { return &v }

// ---- conversion and validation ---------------------------------------------------------

// EmailMaxLen is RFC 5321's cap on an Email value, whatever its max_len.
const EmailMaxLen = 254

var typeNames = map[string]string{
	"Int":      "an integer",
	"Float":    "a number",
	"Str":      "a string",
	"Bool":     "a boolean",
	"Date":     "an ISO date (YYYY-MM-DD)",
	"DateTime": "an ISO date-time",
	"Uuid":     "a UUID",
}

var (
	reInt   = regexp.MustCompile(`^[+-]?[0-9]+$`)
	reFloat = regexp.MustCompile(`^[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$`)
)

const outOfRange = "must be an integer between -9223372036854775808 and 9223372036854775807"

type paramError string

// convert is the prelude's _convert: the typed value of one parameter, or its message.
// text: the raw value comes from the path, the query string or a form (a string).
func convert(spec *ParamSpec, raw any, text bool) (any, paramError) {
	bad := paramError("must be " + typeNames[spec.Scalar])
	str, isStr := raw.(string)
	if js, ok := raw.(JSONStr); ok {
		if js.Bad {
			if spec.Scalar == "Str" {
				return nil, "must be valid UTF-8 text"
			}
			return nil, bad
		}
		str, isStr = js.S, true
	}
	switch spec.Scalar {
	case "Int":
		if text && isStr {
			t := Strip(str, nil)
			if !reInt.MatchString(t) {
				return nil, bad
			}
			if len(strings.TrimLeft(t, "+-")) > 4300 {
				return nil, outOfRange
			}
			n, err := strconv.ParseInt(strings.TrimPrefix(t, "+"), 10, 64)
			if err != nil {
				return nil, outOfRange
			}
			return n, ""
		}
		if text {
			return nil, bad
		}
		switch x := raw.(type) {
		case int64:
			return x, ""
		case *big.Int:
			return nil, outOfRange
		case float64:
			if math.IsNaN(x) || math.IsInf(x, 0) || x != math.Trunc(x) {
				return nil, bad
			}
			if x < -9223372036854775808.0 || x >= 9223372036854775808.0 {
				return nil, outOfRange
			}
			return int64(x), ""
		}
		return nil, bad
	case "Float":
		var v float64
		switch {
		case text && isStr:
			t := Strip(str, nil)
			if !reFloat.MatchString(t) {
				return nil, bad
			}
			v, _ = strconv.ParseFloat(t, 64)
		case !text:
			switch x := raw.(type) {
			case int64:
				v = float64(x)
			case *big.Int:
				v, _ = new(big.Float).SetInt(x).Float64()
			case float64:
				v = x
			default:
				return nil, bad
			}
		default:
			return nil, bad
		}
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return nil, bad
		}
		return v, ""
	case "Str":
		if isStr {
			return str, ""
		}
		return nil, bad
	case "Bool":
		if text && isStr {
			switch Lower(str) {
			case "true", "1", "yes", "on":
				return true, ""
			case "false", "0", "no", "off":
				return false, ""
			}
			return nil, bad
		}
		if b, ok := raw.(bool); ok && !text {
			return b, ""
		}
		return nil, bad
	case "DateTime":
		if isStr {
			if t, ok := ParseDateTime(str); ok {
				return t, ""
			}
		}
		return nil, bad
	case "Uuid":
		if isStr {
			if u, ok := UUIDText(str); ok {
				return UUID(u), ""
			}
		}
		return nil, bad
	}
	if isStr { // Date
		if d, ok := ParseISODate(str); ok {
			return d, ""
		}
	}
	return nil, bad
}

// num is the prelude's _num: a bound as Python prints it.
func num(v float64) string {
	if v == math.Trunc(v) && !math.IsInf(v, 0) {
		i, _ := new(big.Float).SetFloat64(v).Int(nil)
		return i.String()
	}
	return ReprFloat(v)
}

// cmpNum compares a parameter value (int64 or float64) with a bound exactly, as Python
// compares an int with a float.
func cmpNum(value any, bound float64) int {
	switch x := value.(type) {
	case int64:
		return new(big.Float).SetInt64(x).Cmp(new(big.Float).SetFloat64(bound))
	case float64:
		switch {
		case x < bound:
			return -1
		case x > bound:
			return 1
		}
	}
	return 0
}

var patternCache sync.Map // pattern -> *regexp.Regexp

// FullMatch is re.fullmatch(pattern, s) (the checker admits only the RE2 subset, L926).
func FullMatch(pattern, s string) bool {
	v, ok := patternCache.Load(pattern)
	if !ok {
		v = regexp.MustCompile(`\A(?:` + pattern + `)\z`)
		patternCache.Store(pattern, v)
	}
	return v.(*regexp.Regexp).MatchString(s)
}

// constrain is the prelude's _constrain.
func constrain(spec *ParamSpec, value any) paramError {
	if spec.OneOf != nil {
		s, _ := value.(string)
		found := false
		for _, o := range spec.OneOf {
			if o == s {
				found = true
			}
		}
		if !found {
			parts := make([]string, len(spec.OneOf))
			for i, o := range spec.OneOf {
				parts[i] = ReprStr(o)
			}
			return paramError("must be one of " + strings.Join(parts, ", "))
		}
	}
	switch value.(type) {
	case int64, float64:
		if spec.Ge != nil && cmpNum(value, *spec.Ge) < 0 {
			return paramError("must be >= " + num(*spec.Ge))
		}
		if spec.Le != nil && cmpNum(value, *spec.Le) > 0 {
			return paramError("must be <= " + num(*spec.Le))
		}
	}
	if s, ok := value.(string); ok {
		n := Len(s)
		if spec.MinLen != nil && n < *spec.MinLen {
			return paramError("must have at least " + strconv.FormatInt(*spec.MinLen, 10) + " characters")
		}
		if spec.MaxLen != nil && n > *spec.MaxLen {
			return paramError("must have at most " + strconv.FormatInt(*spec.MaxLen, 10) + " characters")
		}
		if spec.Email && n > EmailMaxLen {
			return paramError("must have at most " + strconv.Itoa(EmailMaxLen) + " characters")
		}
		if spec.Pattern != nil && !FullMatch(*spec.Pattern, s) {
			if spec.Email {
				return "must be an email address"
			}
			return paramError("must match the pattern " + ReprStr(*spec.Pattern))
		}
	}
	return ""
}

// defaultValue is the prelude's _default (a Uuid default as a UUID, like a sent one).
func defaultValue(spec *ParamSpec) any {
	switch d := spec.Default.(type) {
	case nullT:
		return nil
	case string:
		if spec.Scalar == "Uuid" {
			if u, ok := UUIDText(d); ok {
				return UUID(u)
			}
		}
	case int:
		return int64(d)
	}
	return spec.Default
}

// FormFields is the prelude's form_fields: a URL-encoded form body's fields (a
// repeated field keeps its last value); any other content type has no fields.
func FormFields(body []byte, contentType string) map[string]string {
	media, _, _ := strings.Cut(contentType, ";")
	if strings.ToLower(Strip(media, nil)) != "application/x-www-form-urlencoded" {
		return map[string]string{}
	}
	return ParseQS(string(body))
}

// ValidationDetail is one problem of a 422 answer: {"loc": [source, name], "msg": ...}.
func validationDetail(loc []string, msg string) Obj {
	l := make([]any, len(loc))
	for i, s := range loc {
		l[i] = s
	}
	return Obj{{"loc", l}, {"msg", msg}}
}

// ParseParams is the prelude's parse_params: every parameter converted and validated,
// in order; else the 422 validation error listing each problem in order. Values:
// Int int64, Float float64, Str string, Bool bool, Date Date, DateTime DateTime, Uuid
// UUID; nil for None; Unset for an optional parameter not sent.
func ParseParams(specs []ParamSpec, path, query map[string]string, body []byte, form bool,
	contentType string) (map[string]any, *ErrorResp) {
	var detail []any
	values := map[string]any{}
	data := map[string]any{}
	if form {
		for k, v := range FormFields(body, contentType) {
			data[k] = v
		}
	} else {
		hasBody := false
		for i := range specs {
			hasBody = hasBody || specs[i].Source == "body"
		}
		if hasBody {
			var parsed any = &JSONObj{Vals: map[string]any{}}
			if len(strings.Trim(string(body), " \t\n\r\x0b\x0c")) > 0 {
				v, err := LoadsJSON(body)
				if err != nil {
					v = nil
				}
				parsed = v
			}
			obj, ok := parsed.(*JSONObj)
			if !ok {
				detail = append(detail, validationDetail([]string{"body"}, "the body must be a JSON object"))
				return nil, NewErrorResp(422, "validation", detail)
			}
			data = obj.Vals
		}
	}
	for i := range specs {
		spec := &specs[i]
		var raw any
		present := false
		switch spec.Source {
		case "path":
			raw, present = lookupStr(path, spec.Name)
		case "query":
			raw, present = lookupStr(query, spec.Name)
		default:
			raw, present = data[spec.Name]
		}
		loc := []string{spec.Source, spec.Name}
		if spec.Optional && present && raw == nil {
			if spec.Nullable {
				values[spec.Name] = nil
			} else {
				detail = append(detail, validationDetail(loc, "must not be null"))
			}
			continue
		}
		if !present || raw == nil {
			if spec.Default != nil {
				values[spec.Name] = defaultValue(spec)
			} else {
				detail = append(detail, validationDetail(loc, "field required"))
			}
			continue
		}
		value, msg := convert(spec, raw, spec.Source != "body" || form)
		if msg == "" {
			msg = constrain(spec, value)
		}
		if msg != "" {
			detail = append(detail, validationDetail(loc, string(msg)))
			continue
		}
		values[spec.Name] = value
	}
	if len(detail) > 0 {
		return nil, NewErrorResp(422, "validation", detail)
	}
	return values, nil
}

func lookupStr(m map[string]string, k string) (any, bool) {
	v, ok := m[k]
	if !ok {
		return nil, false
	}
	return v, true
}
