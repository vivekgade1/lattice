package rt

import (
	"strconv"
	"strings"
	"sync"
	"unicode"
	"unicode/utf8"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

// A Lattice Str is a sequence of code points; Go strings hold them as UTF-8, so every
// length and index here counts runes, never bytes.

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= utf8.RuneSelf {
			return false
		}
	}
	return true
}

// Len is len(s).
func Len(s string) int64 { return int64(utf8.RuneCountInString(s)) }

// IndexStr is s[i] (a negative i counts from the end).
func IndexStr(s string, i int64) string {
	if isASCII(s) {
		n := int64(len(s))
		if i < 0 {
			i += n
		}
		if i < 0 || i >= n {
			Raise("IndexError", "string index out of range")
		}
		return s[i : i+1]
	}
	rs := []rune(s)
	n := int64(len(rs))
	if i < 0 {
		i += n
	}
	if i < 0 || i >= n {
		Raise("IndexError", "string index out of range")
	}
	return string(rs[i])
}

// sliceIndices is CPython's PySlice_Unpack + PySlice_AdjustIndices for a sequence of
// length n: the first index, the step and the number of elements.
func sliceIndices(n int64, lo, hi, step *int64) (start, st, count int64) {
	st = 1
	if step != nil {
		st = *step
		if st == 0 {
			Raise("ValueError", "slice step cannot be zero")
		}
		if st < -maxSsize { // CPython clamps the step to -PY_SSIZE_T_MAX
			st = -maxSsize
		}
	}
	var stop int64
	if lo == nil {
		if st < 0 {
			start = maxSsize
		} else {
			start = 0
		}
	} else {
		start = *lo
	}
	if hi == nil {
		if st < 0 {
			stop = minSsize
		} else {
			stop = maxSsize
		}
	} else {
		stop = *hi
	}
	adjust := func(x int64) int64 {
		if x < 0 {
			x += n
			if x < 0 {
				if st < 0 {
					return -1
				}
				return 0
			}
		} else if x >= n {
			if st < 0 {
				return n - 1
			}
			return n
		}
		return x
	}
	start, stop = adjust(start), adjust(stop)
	if st < 0 {
		if stop < start {
			count = (start-stop-1)/(-st) + 1
		}
	} else if start < stop {
		count = (stop-start-1)/st + 1
	}
	return start, st, count
}

const (
	maxSsize = int64(^uint64(0) >> 1)
	minSsize = -maxSsize - 1
)

func sliceOf[T any](xs []T, lo, hi, step *int64) []T {
	start, st, count := sliceIndices(int64(len(xs)), lo, hi, step)
	out := make([]T, count)
	if st == 1 {
		copy(out, xs[start:start+count])
		return out
	}
	for i := range count {
		out[i] = xs[start+i*st]
	}
	return out
}

// SliceStr is s[lo:hi:step] (nil for an omitted bound).
func SliceStr(s string, lo, hi, step *int64) string {
	if isASCII(s) {
		return string(sliceOf([]byte(s), lo, hi, step))
	}
	return string(sliceOf([]rune(s), lo, hi, step))
}

// ---- case mapping ----------------------------------------------------------------------

// The per-character full mappings (SpecialCasing included) come from x/text/cases;
// the composition (final sigma, title's word rule) is CPython's.

var (
	caseMu    sync.Mutex
	lowerOne  = map[rune]string{}
	titleOne  = map[rune]string{}
	lowerCase = cases.Lower(language.Und)
	titleCase = cases.Title(language.Und, cases.NoLower)
)

func mapOne(cache map[rune]string, c cases.Caser, r rune) string {
	caseMu.Lock()
	defer caseMu.Unlock()
	if s, ok := cache[r]; ok {
		return s
	}
	s := c.String(string(r))
	cache[r] = s
	return s
}

// toLowerFull is _PyUnicode_ToLowerFull.
func toLowerFull(r rune) string {
	if r < utf8.RuneSelf {
		if 'A' <= r && r <= 'Z' {
			r += 'a' - 'A'
		}
		return string(r)
	}
	return mapOne(lowerOne, lowerCase, r)
}

// toTitleFull is _PyUnicode_ToTitleFull.
func toTitleFull(r rune) string {
	if r < utf8.RuneSelf {
		if 'a' <= r && r <= 'z' {
			r -= 'a' - 'A'
		}
		return string(r)
	}
	return mapOne(titleOne, titleCase, r)
}

// isCased is Unicode's Cased property (_PyUnicode_IsCased).
func isCased(r rune) bool {
	return unicode.IsUpper(r) || unicode.IsLower(r) || unicode.IsTitle(r) ||
		unicode.Is(unicode.Other_Lowercase, r) || unicode.Is(unicode.Other_Uppercase, r)
}

// wordBreakMid are the characters with Word_Break MidLetter, MidNumLet or Single_Quote,
// which Case_Ignorable includes besides categories Mn Me Cf Lm Sk.
var wordBreakMid = map[rune]bool{
	'\'': true, '.': true, ':': true, 0x00B7: true, 0x0387: true, 0x055F: true, 0x05F4: true,
	0x2018: true, 0x2019: true, 0x2024: true, 0x2027: true, 0xFE13: true, 0xFE52: true,
	0xFE55: true, 0xFF07: true, 0xFF0E: true, 0xFF1A: true,
}

// isCaseIgnorable is Unicode's Case_Ignorable property (_PyUnicode_IsCaseIgnorable).
func isCaseIgnorable(r rune) bool {
	return wordBreakMid[r] || unicode.In(r, unicode.Mn, unicode.Me, unicode.Cf, unicode.Lm, unicode.Sk)
}

// lowerAt is CPython's lower_ucs4: the lower mapping of rs[i], with capital sigma
// becoming final sigma after a cased letter and not before one (case-ignorable
// characters skipped both ways).
func lowerAt(rs []rune, i int) string {
	c := rs[i]
	if c != 0x3A3 {
		return toLowerFull(c)
	}
	j := i - 1
	for j >= 0 && isCaseIgnorable(rs[j]) {
		j--
	}
	final := j >= 0 && isCased(rs[j])
	if final && i+1 < len(rs) {
		j = i + 1
		for j < len(rs) && isCaseIgnorable(rs[j]) {
			j++
		}
		final = j == len(rs) || !isCased(rs[j])
	}
	if final {
		return "ς"
	}
	return "σ"
}

// Upper is s.upper().
func Upper(s string) string {
	if isASCII(s) {
		return strings.ToUpper(s)
	}
	return cases.Upper(language.Und).String(s)
}

// Lower is s.lower().
func Lower(s string) string {
	if isASCII(s) {
		return strings.ToLower(s)
	}
	rs := []rune(s)
	var b strings.Builder
	for i := range rs {
		b.WriteString(lowerAt(rs, i))
	}
	return b.String()
}

// Casefold is s.casefold().
func Casefold(s string) string {
	if isASCII(s) {
		return strings.ToLower(s)
	}
	// CaseFolding.txt folds Cherokee to its uppercase letters (U+13A0..U+13F5), which
	// Python follows; x/text folds them to the lowercase ones (U+AB70.., U+13F8..).
	return strings.Map(func(r rune) rune {
		switch {
		case r >= 0xAB70 && r <= 0xABBF:
			return r - 0xAB70 + 0x13A0
		case r >= 0x13F8 && r <= 0x13FD:
			return r - 8
		}
		return r
	}, cases.Fold().String(s))
}

// Title is s.title(): a cased character after an uncased one is title-cased, every
// other character lower-cased ("o'neil" -> "O'Neil").
func Title(s string) string {
	rs := []rune(s)
	var b strings.Builder
	prevCased := false
	for i, c := range rs {
		if prevCased {
			b.WriteString(lowerAt(rs, i))
		} else {
			b.WriteString(toTitleFull(c))
		}
		prevCased = isCased(c)
	}
	return b.String()
}

// Capitalize is s.capitalize(): the first character title-cased, the rest lower-cased.
func Capitalize(s string) string {
	rs := []rune(s)
	var b strings.Builder
	for i, c := range rs {
		if i == 0 {
			b.WriteString(toTitleFull(c))
		} else {
			b.WriteString(lowerAt(rs, i))
		}
	}
	return b.String()
}

// ---- character classes ------------------------------------------------------------------

// pyIsSpace is Py_UNICODE_ISSPACE: bidirectional class WS, B or S, or category Zs.
func pyIsSpace(r rune) bool {
	switch r {
	case '\t', '\n', '\v', '\f', '\r', 0x1C, 0x1D, 0x1E, 0x1F, ' ', 0x85, 0xA0, 0x1680,
		0x2028, 0x2029, 0x202F, 0x205F, 0x3000:
		return true
	}
	return r >= 0x2000 && r <= 0x200A
}

// digitNotDecimal are the characters with Numeric_Type=Digit (str.isdigit but not
// str.isdecimal): superscripts, circled digits and the like.
var digitNotDecimal = &unicode.RangeTable{
	R16: []unicode.Range16{
		{0x00B2, 0x00B3, 1}, {0x00B9, 0x00B9, 1}, {0x1369, 0x1371, 1}, {0x19DA, 0x19DA, 1},
		{0x2070, 0x2070, 1}, {0x2074, 0x2079, 1}, {0x2080, 0x2089, 1}, {0x2460, 0x2468, 1},
		{0x2474, 0x247C, 1}, {0x2488, 0x2490, 1}, {0x24EA, 0x24EA, 1}, {0x24F5, 0x24FD, 1},
		{0x24FF, 0x24FF, 1}, {0x2776, 0x277E, 1}, {0x2780, 0x2788, 1}, {0x278A, 0x2792, 1},
	},
	R32: []unicode.Range32{
		{0x10A40, 0x10A43, 1}, {0x10E60, 0x10E68, 1}, {0x11052, 0x1105A, 1}, {0x1F100, 0x1F10A, 1},
	},
}

func pyIsDigit(r rune) bool {
	return ('0' <= r && r <= '9') || (r >= utf8.RuneSelf && (unicode.Is(unicode.Nd, r) || unicode.Is(digitNotDecimal, r)))
}

func pyIsAlpha(r rune) bool { return unicode.IsLetter(r) }

// pyIsAlnum is isalpha or isnumeric (Numeric_Type Decimal, Digit or Numeric: the
// categories Nd Nl No, and ideographs, which are letters).
func pyIsAlnum(r rune) bool { return unicode.IsLetter(r) || unicode.IsNumber(r) }

func pyIsLower(r rune) bool { return unicode.IsLower(r) || unicode.Is(unicode.Other_Lowercase, r) }
func pyIsUpper(r rune) bool { return unicode.IsUpper(r) || unicode.Is(unicode.Other_Uppercase, r) }

func all(s string, f func(rune) bool) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !f(r) {
			return false
		}
	}
	return true
}

// IsSpace is s.isspace().
func IsSpace(s string) bool { return all(s, pyIsSpace) }

// IsDigit is s.isdigit().
func IsDigit(s string) bool { return all(s, pyIsDigit) }

// IsAlpha is s.isalpha().
func IsAlpha(s string) bool { return all(s, pyIsAlpha) }

// IsAlnum is s.isalnum().
func IsAlnum(s string) bool { return all(s, pyIsAlnum) }

// IsLower is s.islower(): some cased character, and none upper- or title-case.
func IsLower(s string) bool {
	cased := false
	for _, r := range s {
		if pyIsUpper(r) || unicode.IsTitle(r) {
			return false
		}
		if !cased && pyIsLower(r) {
			cased = true
		}
	}
	return cased
}

// IsUpper is s.isupper(): some cased character, and none lower- or title-case.
func IsUpper(s string) bool {
	cased := false
	for _, r := range s {
		if pyIsLower(r) || unicode.IsTitle(r) {
			return false
		}
		if !cased && pyIsUpper(r) {
			cased = true
		}
	}
	return cased
}

// ---- split and strip ----------------------------------------------------------------------

// Split is s.split(sep, maxsplit): sep nil splits on runs of whitespace; max < 0 means
// no limit.
func Split(s string, sep *string, max int64) []string {
	if sep == nil {
		return splitWhitespace(s, max)
	}
	if *sep == "" {
		Raise("ValueError", "empty separator")
	}
	n := -1
	if max >= 0 && max < int64(len(s))+1 {
		n = int(max) + 1
	}
	return strings.SplitN(s, *sep, n)
}

// splitWhitespace is CPython's split_whitespace.
func splitWhitespace(s string, max int64) []string {
	if max < 0 {
		max = maxSsize
	}
	out := []string{}
	i, n := 0, len(s)
	next := func(i int) (rune, int) { r, w := utf8.DecodeRuneInString(s[i:]); return r, w }
	for ; max > 0; max-- {
		for i < n {
			r, w := next(i)
			if !pyIsSpace(r) {
				break
			}
			i += w
		}
		if i == n {
			break
		}
		j := i
		for i < n {
			r, w := next(i)
			if pyIsSpace(r) {
				break
			}
			i += w
		}
		out = append(out, s[j:i])
	}
	if i < n {
		for i < n {
			r, w := next(i)
			if !pyIsSpace(r) {
				break
			}
			i += w
		}
		if i != n {
			out = append(out, s[i:])
		}
	}
	return out
}

// Strip is s.strip(chars): chars nil strips whitespace.
func Strip(s string, chars *string) string {
	if chars == nil {
		return strings.TrimFunc(s, pyIsSpace)
	}
	return strings.Trim(s, *chars)
}

// LStrip is s.lstrip(chars).
func LStrip(s string, chars *string) string {
	if chars == nil {
		return strings.TrimLeftFunc(s, pyIsSpace)
	}
	return strings.TrimLeft(s, *chars)
}

// RStrip is s.rstrip(chars).
func RStrip(s string, chars *string) string {
	if chars == nil {
		return strings.TrimRightFunc(s, pyIsSpace)
	}
	return strings.TrimRight(s, *chars)
}

// ---- repr -------------------------------------------------------------------------------

// ReprStr is Python's repr(s).
func ReprStr(s string) string {
	quote := byte('\'')
	if strings.IndexByte(s, '\'') >= 0 && strings.IndexByte(s, '"') < 0 {
		quote = '"'
	}
	var b strings.Builder
	b.WriteByte(quote)
	hex := func(prefix string, r rune, width int) {
		h := strconv.FormatInt(int64(r), 16)
		b.WriteString(prefix + strings.Repeat("0", width-len(h)) + h)
	}
	for _, r := range s {
		switch {
		case r == rune(quote) || r == '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '\t':
			b.WriteString(`\t`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r < ' ' || r == 0x7F:
			hex(`\x`, r, 2)
		case r < 0x7F:
			b.WriteRune(r)
		case unicode.IsPrint(r): // Python's isprintable: not C*, not Z* except ' '
			b.WriteRune(r)
		case r <= 0xFF:
			hex(`\x`, r, 2)
		case r <= 0xFFFF:
			hex(`\u`, r, 4)
		default:
			hex(`\U`, r, 8)
		}
	}
	b.WriteByte(quote)
	return b.String()
}

// Reprer is a value with its own Python repr (a generated record or row type).
type Reprer interface{ PyRepr() string }

// Repr is Python's repr(v) for the scalar values a compiled program holds.
func Repr(v any) string {
	switch x := v.(type) {
	case string:
		return ReprStr(x)
	case int64:
		return strconv.FormatInt(x, 10)
	case int:
		return strconv.Itoa(x)
	case float64:
		return ReprFloat(x)
	case bool:
		if x {
			return "True"
		}
		return "False"
	case nil:
		return "None"
	case Reprer:
		return x.PyRepr()
	}
	panic("rt.Repr: no Python repr for this Go value")
}
