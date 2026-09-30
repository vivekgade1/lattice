package rt

import (
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"
)

// Python's format() of the scalar values a T2 procedure formats (f-string replacement
// fields with a spec), str() and ascii(): int.__format__, bool.__format__,
// str.__format__, None's object.__format__. Float formatting is FormatFloat.

// FormatInt is format(i, spec) for an int.
func FormatInt(i int64, spec string) string {
	if spec == "" {
		return strconv.FormatInt(i, 10)
	}
	return formatIntAs(i, spec, "int")
}

// FormatBool is format(b, spec): str(b) for an empty spec, else the int 0 or 1
// formatted (bool is an int subclass).
func FormatBool(b bool, spec string) string {
	if spec == "" {
		return StrBool(b)
	}
	var i int64
	if b {
		i = 1
	}
	return formatIntAs(i, spec, "bool")
}

// StrBool is str(b).
func StrBool(b bool) string {
	if b {
		return "True"
	}
	return "False"
}

// FormatNone is format(None, spec): "None", or TypeError for a non-empty spec.
func FormatNone(spec string) string {
	if spec != "" {
		Raise("TypeError", "unsupported format string passed to NoneType.__format__")
	}
	return "None"
}

// formatIntAs is CPython's _PyLong_FormatAdvancedWriter for an int of Python type tname.
func formatIntAs(i int64, spec, tname string) string {
	fs := parseFormatSpecFor(spec, tname, 'd', '>')
	switch fs.typ {
	case 'b', 'c', 'd', 'o', 'x', 'X', 'n':
	case 'e', 'E', 'f', 'F', 'g', 'G', '%':
		return FormatFloat(float64(i), spec) // PyNumber_Float, then the float formatter
	default:
		unknownCode(fs.typ, tname)
	}
	if fs.precision != -1 {
		Raise("ValueError", "Precision not allowed in integer format specifier")
	}
	if fs.noNeg0 {
		Raise("ValueError", "Negative zero coercion (z) not allowed in integer format specifier")
	}
	sign, prefix, digits, remainder := "", "", "", ""
	if fs.typ == 'c' {
		if fs.sign != 0 {
			Raise("ValueError", "Sign not allowed with integer format specifier 'c'")
		}
		if fs.alt {
			Raise("ValueError", "Alternate form (#) not allowed with integer format specifier 'c'")
		}
		if i < 0 || i > 0x10ffff {
			Raise("OverflowError", "%c arg not in range(0x110000)")
		}
		remainder = string(rune(i))
	} else {
		base := 10
		switch fs.typ {
		case 'b':
			base, prefix = 2, "0b"
		case 'o':
			base, prefix = 8, "0o"
		case 'x':
			base, prefix = 16, "0x"
		case 'X':
			base, prefix = 16, "0X"
		}
		if !fs.alt {
			prefix = ""
		}
		digits = strconv.FormatUint(uabs(i), base)
		if fs.typ == 'X' {
			digits = strings.ToUpper(digits)
		}
		if i < 0 {
			sign = "-"
		}
	}
	switch fs.sign {
	case '+':
		if sign == "" {
			sign = "+"
		}
	case ' ':
		if sign == "" {
			sign = " "
		}
	}
	sep, size := "", 3
	if fs.typ != 'n' && fs.group != 0 {
		sep = string(rune(fs.group))
		if fs.group4 {
			size = 4
		}
	}
	nonDigit := len(sign) + len(prefix) + utf8.RuneCountInString(remainder)
	minWidth := 0
	if fs.fill == '0' && fs.align == '=' {
		minWidth = fs.width - nonDigit
	}
	grouped := ""
	if digits != "" {
		grouped = groupDigitsN(digits, minWidth, sep, size)
	}
	lpad, spad, rpad := padding(fs, fs.width-(nonDigit+len(grouped)))
	fill := string(fs.fill)
	return strings.Repeat(fill, lpad) + sign + prefix + strings.Repeat(fill, spad) + grouped +
		remainder + strings.Repeat(fill, rpad)
}

// padding splits n > 0 padding characters by the spec's alignment (calc_number_widths).
func padding(fs formatSpec, n int) (lpad, spad, rpad int) {
	if n <= 0 {
		return 0, 0, 0
	}
	switch fs.align {
	case '<':
		return 0, 0, n
	case '^':
		return n / 2, 0, n - n/2
	case '=':
		return 0, n, 0
	}
	return n, 0, 0
}

func unknownCode(typ byte, tname string) {
	code := string(rune(typ))
	if typ <= 32 || typ >= 128 {
		code = "\\x" + strconv.FormatInt(int64(typ), 16)
	}
	Raise("ValueError", "Unknown format code '"+code+"' for object of type '"+tname+"'")
}

// FormatStr is format(s, spec) for a str (CPython's format_string_internal).
func FormatStr(s, spec string) string {
	if spec == "" {
		return s
	}
	fs := parseFormatSpecFor(spec, "str", 's', '<')
	if fs.typ != 's' {
		unknownCode(fs.typ, "str")
	}
	switch fs.sign {
	case 0:
	case ' ':
		Raise("ValueError", "Space not allowed in string format specifier")
	default:
		Raise("ValueError", "Sign not allowed in string format specifier")
	}
	if fs.noNeg0 {
		Raise("ValueError", "Negative zero coercion (z) not allowed in string format specifier")
	}
	if fs.alt {
		Raise("ValueError", "Alternate form (#) not allowed in string format specifier")
	}
	if fs.align == '=' {
		Raise("ValueError", "'=' alignment not allowed in string format specifier")
	}
	n := utf8.RuneCountInString(s)
	if fs.precision >= 0 && n > fs.precision {
		s = string([]rune(s)[:fs.precision])
		n = fs.precision
	}
	total := max(fs.width, n)
	lpad := 0
	switch fs.align {
	case '>':
		lpad = total - n
	case '^':
		lpad = (total - n) / 2
	}
	fill := string(fs.fill)
	return strings.Repeat(fill, lpad) + s + strings.Repeat(fill, total-n-lpad)
}

// AsciiStr is ascii(s): repr(s) with every non-ASCII character escaped.
func AsciiStr(s string) string { return EscapeNonASCII(ReprStr(s)) }

func hexPad(c rune, width int) string {
	h := strconv.FormatInt(int64(c), 16)
	return strings.Repeat("0", width-len(h)) + h
}

// SpecGrow is the size check of a replacement field with a format spec
// (T2Fuel.fmt): the sum of the spec's decimal numbers bounds what formatting adds, and
// is charged and capped (R622) before the value is formatted.
func SpecGrow(f *Fuel, spec string) {
	total := new(big.Int)
	run := make([]byte, 0, 8)
	flush := func() {
		if len(run) == 0 {
			return
		}
		if len(run) > IntMaxStrDigits { // int() of the digit run
			Raise("ValueError", "Exceeds the limit (4300 digits) for integer string conversion: value has "+
				strconv.Itoa(len(run))+" digits; use sys.set_int_max_str_digits() to increase the limit")
		}
		v, _ := new(big.Int).SetString(string(run), 10)
		total.Add(total, v)
		run = run[:0]
	}
	for _, r := range spec { // re.findall(r"\d+", spec): Unicode decimal digits
		if d := decimalValue(r); d >= 0 {
			run = append(run, byte('0'+d))
			continue
		}
		flush()
	}
	flush()
	if !total.IsInt64() || total.Int64() > f.MaxSize {
		Fail("R622", "a procedure would build a value of "+total.String()+
			" characters or elements; the largest allowed is "+strconv.FormatInt(f.MaxSize, 10)+
			" (ApiProfile.max_value_size)")
	}
	f.Grow(total.Int64())
}

// FmtInt is `f"{i:spec}"` (spec not empty): the spec's size check, then format.
func FmtInt(f *Fuel, i int64, spec string) string {
	SpecGrow(f, spec)
	return FormatInt(i, spec)
}

// FmtFloat is `f"{x:spec}"`.
func FmtFloat(f *Fuel, x float64, spec string) string {
	SpecGrow(f, spec)
	return FormatFloat(x, spec)
}

// FmtBool is `f"{b:spec}"`.
func FmtBool(f *Fuel, b bool, spec string) string {
	SpecGrow(f, spec)
	return FormatBool(b, spec)
}

// FmtStr is `f"{s:spec}"` (also a converted value: `f"{x!r:spec}"`).
func FmtStr(f *Fuel, s, spec string) string {
	SpecGrow(f, spec)
	return FormatStr(s, spec)
}

// FmtNone is `f"{None:spec}"`.
func FmtNone(f *Fuel, spec string) string {
	SpecGrow(f, spec)
	return FormatNone(spec)
}
