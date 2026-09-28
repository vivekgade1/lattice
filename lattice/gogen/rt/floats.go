package rt

import (
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"
)

// ReprFloat is Python's repr(f) (and str(f)): the shortest digits that round-trip,
// in exponent form iff the decimal exponent is < -4 or >= 16, "1.0" not "1".
func ReprFloat(f float64) string {
	return doubleToString(f, 'r', 0, false, true, false)
}

// pyDigits is CPython's _Py_dg_dtoa for a finite f >= 0: the significant digits (no
// trailing zeros) and the position of the decimal point. mode 0 is the shortest
// round-trip string; mode 2 at most n significant digits; mode 3 n digits after the
// point (an empty digit string when the value rounds to zero).
func pyDigits(f float64, mode, n int) (string, int) {
	if f == 0 {
		return "0", 1
	}
	switch mode {
	case 0, 2:
		prec := -1
		if mode == 2 {
			prec = n - 1
		}
		s := strconv.FormatFloat(f, 'e', prec, 64) // d.ddde±XX
		mant, exp, _ := strings.Cut(s, "e")
		e, _ := strconv.Atoi(exp)
		d := strings.TrimRight(strings.Replace(mant, ".", "", 1), "0")
		if d == "" {
			d = "0"
		}
		return d, e + 1
	default: // 3
		s := strconv.FormatFloat(f, 'f', n, 64)
		ip, fp, _ := strings.Cut(s, ".")
		all, decpt := ip+fp, len(ip)
		for len(all) > 0 && all[0] == '0' {
			all = all[1:]
			decpt--
		}
		all = strings.TrimRight(all, "0")
		if all == "" {
			return "", -n
		}
		return all, decpt
	}
}

// doubleToString is CPython's PyOS_double_to_string / format_float_short. code is one
// of e E f F g G r; precision is the Python precision (ignored for r).
func doubleToString(f float64, code byte, precision int, alt, addDot0, noNeg0 bool) string {
	upper := code == 'E' || code == 'F' || code == 'G'
	if upper {
		code += 'a' - 'A'
	}
	neg := math.Signbit(f)
	if math.IsNaN(f) {
		if upper {
			return "NAN"
		}
		return "nan"
	}
	if math.IsInf(f, 0) {
		s := "inf"
		if upper {
			s = "INF"
		}
		if neg {
			return "-" + s
		}
		return s
	}
	mode := 0
	switch code {
	case 'e':
		mode = 2
		precision++
	case 'f':
		mode = 3
	case 'g':
		mode = 2
		if precision == 0 {
			precision = 1
		}
	}
	digits, decpt := pyDigits(math.Abs(f), mode, precision)
	if noNeg0 && neg && (digits == "" || digits == "0") {
		neg = false
	}
	dlen := len(digits)
	vend := dlen
	useExp := false
	switch code {
	case 'e':
		useExp = true
		vend = precision
	case 'f':
		vend = decpt + precision
	case 'g':
		limit := precision
		if addDot0 {
			limit = precision - 1
		}
		if decpt <= -4 || decpt > limit {
			useExp = true
		}
		if alt {
			vend = precision
		}
	case 'r':
		if decpt <= -4 || decpt > 16 {
			useExp = true
		}
	}
	exp := 0
	if useExp {
		exp = decpt - 1
		decpt = 1
	}
	vstart := 0
	if decpt <= 0 {
		vstart = decpt - 1
	}
	if !useExp && addDot0 {
		vend = max(vend, decpt+1)
	} else {
		vend = max(vend, decpt)
	}
	var b strings.Builder
	if neg {
		b.WriteByte('-')
	}
	if decpt <= 0 {
		b.WriteString(strings.Repeat("0", decpt-vstart))
		b.WriteByte('.')
		b.WriteString(strings.Repeat("0", -decpt))
	} else {
		b.WriteString(strings.Repeat("0", -vstart))
	}
	if 0 < decpt && decpt <= dlen {
		b.WriteString(digits[:decpt])
		b.WriteByte('.')
		b.WriteString(digits[decpt:])
	} else {
		b.WriteString(digits)
	}
	if dlen < decpt {
		b.WriteString(strings.Repeat("0", decpt-dlen))
		b.WriteByte('.')
		b.WriteString(strings.Repeat("0", vend-decpt))
	} else {
		b.WriteString(strings.Repeat("0", vend-dlen))
	}
	s := b.String()
	if strings.HasSuffix(s, ".") && !alt {
		s = s[:len(s)-1]
	}
	if useExp {
		e := "e"
		if upper {
			e = "E"
		}
		sign := "+"
		if exp < 0 {
			sign = "-"
			exp = -exp
		}
		es := strconv.Itoa(exp)
		if len(es) < 2 {
			es = "0" + es
		}
		s += e + sign + es
	}
	return s
}

// formatSpec is a parsed Python format specification (PEP 3101 mini-language).
type formatSpec struct {
	fill                rune
	align, sign, typ    byte
	noNeg0, alt         bool
	width, precision    int // -1: not given
	group, fracGroup    byte
	fillGiven, hasAlign bool
}

func isAlign(r rune) bool { return r == '<' || r == '>' || r == '=' || r == '^' }

// parseFormatSpec is CPython's parse_internal_render_format_spec for a float.
func parseFormatSpec(spec string) formatSpec {
	f := formatSpec{fill: ' ', align: '>', width: -1, precision: -1}
	rs := []rune(spec)
	pos, end := 0, len(rs)
	if end-pos >= 2 && isAlign(rs[pos+1]) {
		f.fill, f.align, f.fillGiven, f.hasAlign = rs[pos], byte(rs[pos+1]), true, true
		pos += 2
	} else if end-pos >= 1 && isAlign(rs[pos]) {
		f.align, f.hasAlign = byte(rs[pos]), true
		pos++
	}
	if end-pos >= 1 && (rs[pos] == '+' || rs[pos] == '-' || rs[pos] == ' ') {
		f.sign = byte(rs[pos])
		pos++
	}
	if end-pos >= 1 && rs[pos] == 'z' {
		f.noNeg0 = true
		pos++
	}
	if end-pos >= 1 && rs[pos] == '#' {
		f.alt = true
		pos++
	}
	if !f.fillGiven && end-pos >= 1 && rs[pos] == '0' {
		f.fill = '0'
		if !f.hasAlign {
			f.align = '='
		}
		pos++
	}
	integer := func() (int, int) {
		n, consumed := 0, 0
		for pos < end && rs[pos] >= '0' && rs[pos] <= '9' {
			if n > (math.MaxInt32-9)/10 {
				Raise("ValueError", "Too many decimal digits in format string")
			}
			n = n*10 + int(rs[pos]-'0')
			pos++
			consumed++
		}
		return n, consumed
	}
	bothSeps := func() { Raise("ValueError", "Cannot specify both ',' and '_'.") }
	if w, c := integer(); c > 0 {
		f.width = w
	}
	if pos < end && rs[pos] == ',' {
		f.group = ','
		pos++
	}
	if pos < end && rs[pos] == '_' {
		if f.group != 0 {
			bothSeps()
		}
		f.group = '_'
		pos++
	}
	if pos < end && rs[pos] == ',' && f.group == '_' {
		bothSeps()
	}
	if pos < end && rs[pos] == '.' {
		pos++
		p, consumed := integer()
		f.precision = p
		if pos < end && rs[pos] == ',' {
			if consumed == 0 {
				f.precision = -1
			}
			f.fracGroup = ','
			pos++
			consumed++
		}
		if pos < end && rs[pos] == '_' {
			if f.fracGroup != 0 {
				bothSeps()
			}
			if consumed == 0 {
				f.precision = -1
			}
			f.fracGroup = '_'
			pos++
			consumed++
		}
		if pos < end && rs[pos] == ',' && f.fracGroup == '_' {
			bothSeps()
		}
		if consumed == 0 {
			Raise("ValueError", "Format specifier missing precision")
		}
	}
	if end-pos > 1 {
		Raise("ValueError", "Invalid format specifier '"+spec+"' for object of type 'float'")
	}
	if end-pos == 1 {
		if rs[pos] >= utf8.RuneSelf {
			// CPython stores the type in a char: a non-ASCII type cannot be a known code.
			Raise("ValueError", "Unknown format code '\\x"+strconv.FormatInt(int64(rs[pos]), 16)+
				"' for object of type 'float'")
		}
		f.typ = byte(rs[pos])
	}
	cannot := func(sep byte) {
		t := string(rune(f.typ))
		if f.typ <= 32 || f.typ >= 128 {
			t = "\\x" + strconv.FormatInt(int64(f.typ), 16)
		}
		Raise("ValueError", "Cannot specify '"+string(rune(sep))+"' with '"+t+"'.")
	}
	if f.group != 0 && !strings.ContainsRune("defgEG%F\x00", rune(f.typ)) {
		cannot(f.group)
	}
	if f.fracGroup != 0 && !strings.ContainsRune("efgEG%F\x00", rune(f.typ)) {
		cannot(f.fracGroup)
	}
	return f
}

// groupDigits is CPython's _PyUnicode_InsertThousandsGrouping for groups of three (sep
// != "") or no grouping (sep == ""): digits padded with zeros to at least minWidth
// characters, separators counted in the width.
func groupDigits(digits string, minWidth int, sep string) string {
	var chunks []string // right to left
	remaining, pos := len(digits), len(digits)
	useSep, broken := false, false
	take := func(l int) {
		nZeros := max(0, l-remaining)
		nChars := max(0, min(remaining, l))
		if useSep {
			chunks = append(chunks, sep)
		}
		chunks = append(chunks, strings.Repeat("0", nZeros)+digits[pos-nChars:pos])
		pos -= nChars
		remaining -= nChars
	}
	if sep != "" {
		for {
			l := min(3, max(remaining, minWidth, 1))
			take(l)
			useSep = true
			minWidth -= l
			if remaining <= 0 && minWidth <= 0 {
				broken = true
				break
			}
			minWidth -= len(sep)
		}
	}
	if !broken {
		take(max(remaining, minWidth, 1))
	}
	var b strings.Builder
	for i := len(chunks) - 1; i >= 0; i-- {
		b.WriteString(chunks[i])
	}
	return b.String()
}

// groupFrac groups the fractional digits in threes from the left (3.14's `.N,f`).
func groupFrac(frac, sep string) string {
	if sep == "" || len(frac) <= 3 {
		return frac
	}
	var b strings.Builder
	for i := 0; i < len(frac); i += 3 {
		if i > 0 {
			b.WriteString(sep)
		}
		b.WriteString(frac[i:min(i+3, len(frac))])
	}
	return b.String()
}

// FormatFloat is Python's format(f, spec) for a float: fill, align, sign, z, #, 0,
// width, grouping (integer and fractional), precision and the types e E f F g G n %
// (or none). An invalid spec raises ValueError with Python's message.
func FormatFloat(f float64, spec string) string {
	if spec == "" {
		return ReprFloat(f)
	}
	fs := parseFormatSpec(spec)
	typ := fs.typ
	switch typ {
	case 0, 'e', 'E', 'f', 'F', 'g', 'G', 'n', '%':
	default:
		code := string(rune(typ))
		if typ <= 32 || typ >= 128 {
			code = "\\x" + strconv.FormatInt(int64(typ), 16)
		}
		Raise("ValueError", "Unknown format code '"+code+"' for object of type 'float'")
	}
	precision, defaultPrecision := fs.precision, 6
	addDot0 := false
	if typ == 0 {
		addDot0 = true
		typ = 'r'
		defaultPrecision = 0
	}
	if typ == 'n' {
		typ = 'g'
	}
	pct := false
	if typ == '%' {
		typ = 'f'
		f *= 100
		pct = true
	}
	if precision < 0 {
		precision = defaultPrecision
	} else if typ == 'r' {
		typ = 'g'
	}
	num := doubleToString(f, typ, precision, fs.alt, addDot0, fs.noNeg0)
	if pct {
		num += "%"
	}
	if fs.sign != '+' && fs.sign != ' ' && fs.width == -1 && fs.typ != 'n' && fs.group == 0 && fs.fracGroup == 0 {
		return num
	}
	sign := ""
	if strings.HasPrefix(num, "-") {
		sign = "-"
		num = num[1:]
	}
	// parse_number: integer digits, an optional '.', fractional digits, the remainder.
	i := 0
	for i < len(num) && num[i] >= '0' && num[i] <= '9' {
		i++
	}
	intDigits := num[:i]
	decimal := ""
	if i < len(num) && num[i] == '.' {
		decimal = "."
		i++
	}
	j := i
	for j < len(num) && num[j] >= '0' && num[j] <= '9' {
		j++
	}
	frac, remainder := num[i:j], num[j:]
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
	sep, fracSep := "", ""
	if fs.group != 0 {
		sep = string(rune(fs.group))
	}
	if fs.fracGroup != 0 {
		fracSep = string(rune(fs.fracGroup))
	}
	fracText := groupFrac(frac, fracSep)
	nonDigit := len(sign) + len(decimal) + len(fracText) + len(remainder)
	minWidth := 0
	if fs.fill == '0' && fs.align == '=' {
		minWidth = fs.width - nonDigit
	}
	grouped := ""
	if intDigits != "" {
		grouped = groupDigits(intDigits, minWidth, sep)
	}
	var lpad, spad, rpad int
	if n := fs.width - (nonDigit + len(grouped)); n > 0 {
		switch fs.align {
		case '<':
			rpad = n
		case '^':
			lpad = n / 2
			rpad = n - lpad
		case '=':
			spad = n
		default:
			lpad = n
		}
	}
	fill := string(fs.fill)
	return strings.Repeat(fill, lpad) + sign + strings.Repeat(fill, spad) + grouped + decimal +
		fracText + remainder + strings.Repeat(fill, rpad)
}

// RoundHalfEven is Python's round(f) for a float (banker's rounding to an Int).
func RoundHalfEven(f float64) int64 {
	return floatToInt(math.RoundToEven(f))
}

// floatToInt is int(f) of an integral float: ValueError for nan, OverflowError for
// inf, R623 outside the 64-bit range.
func floatToInt(r float64) int64 {
	switch {
	case math.IsNaN(r):
		Raise("ValueError", "cannot convert float NaN to integer")
	case math.IsInf(r, 0):
		Raise("OverflowError", "cannot convert float infinity to integer")
	case r >= 0x1p63 || r < -0x1p63:
		v, _ := new(big.Float).SetFloat64(r).Int(nil)
		failRange(v)
	}
	return int64(r)
}

// divmodF is CPython's _float_div_mod.
func divmodF(vx, wx float64) (float64, float64) {
	if wx == 0 {
		zeroDivision()
	}
	mod := math.Mod(vx, wx)
	div := (vx - mod) / wx
	if mod != 0 {
		if (wx < 0) != (mod < 0) {
			mod += wx
			div -= 1.0
		}
	} else {
		mod = math.Copysign(0, wx)
	}
	var floordiv float64
	if div != 0 {
		floordiv = math.Floor(div)
		if div-floordiv > 0.5 {
			floordiv += 1.0
		}
	} else {
		floordiv = math.Copysign(0, vx/wx)
	}
	return floordiv, mod
}

// FloorDivF is a // b for floats.
func FloorDivF(a, b float64) float64 {
	d, _ := divmodF(a, b)
	return d
}

// ModF is a % b for floats (CPython's float_rem: the result has the sign of b).
func ModF(a, b float64) float64 {
	if b == 0 {
		zeroDivision()
	}
	mod := math.Mod(a, b)
	if mod != 0 {
		if (b < 0) != (mod < 0) {
			mod += b
		}
	} else {
		mod = math.Copysign(0, b)
	}
	return mod
}
