package rt

import (
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"unicode"
)

// Strptime is datetime.strptime(s, format) in the C locale, as the prelude's
// lattice_to_datetime calls it: the instant (a naive result is UTC) and ok, false
// where Python raises ValueError. Supported directives: %Y %y %m %d %e %H %k %I %l %M
// %S %f %j %p %b %B %h %a %A %z %T %R %% (with Python's ignored flags and widths). Any
// other directive answers ok=false (Python accepts a few more: %U %W %w %u %V %G %Z %c
// %x %X %r and the %O forms).
func Strptime(s, format string) (DateTime, bool) {
	re, ok := strptimeRegex(format)
	if !ok {
		return 0, false
	}
	m := re.FindStringSubmatchIndex(s)
	if m == nil || m[1] != len(s) {
		return 0, false
	}
	group := map[string]string{}
	for i, name := range re.SubexpNames() {
		if name != "" && m[2*i] >= 0 {
			if _, seen := group[name]; !seen {
				group[name] = s[m[2*i]:m[2*i+1]]
			}
		}
	}
	year, haveYear := 0, false
	month, day := 1, 1
	hour, minute, second, fraction := 0, 0, 0, 0
	julian := -1
	var off int64
	aware := false
	for _, name := range re.SubexpNames() {
		v, found := group[name]
		if name == "" || !found {
			continue
		}
		switch name {
		case "y":
			year, haveYear = pyDigitsInt(v), true
			if year <= 68 {
				year += 2000
			} else {
				year += 1900
			}
		case "Y":
			year, haveYear = pyDigitsInt(v), true
		case "m":
			month = pyDigitsInt(v)
		case "B":
			month = indexOf(fullMonths, strings.ToLower(v)) + 1
		case "b":
			month = indexOf(abbrMonths, strings.ToLower(v)) + 1
		case "d":
			day = pyDigitsInt(strings.TrimLeft(v, " "))
		case "H":
			hour = pyDigitsInt(strings.TrimLeft(v, " "))
		case "I":
			hour = pyDigitsInt(strings.TrimLeft(v, " "))
			switch strings.ToLower(group["p"]) {
			case "", "am":
				if hour == 12 {
					hour = 0
				}
			case "pm":
				if hour != 12 {
					hour += 12
				}
			}
		case "M":
			minute = pyDigitsInt(v)
		case "S":
			second = pyDigitsInt(v)
		case "f":
			fraction, _ = strconv.Atoi(v + strings.Repeat("0", 6-len(v)))
		case "j":
			julian = pyDigitsInt(v)
		case "z":
			aware = true
			if v == "Z" {
				continue
			}
			z := v
			if z[3] == ':' {
				z = z[:3] + z[4:]
				if len(z) > 5 {
					if z[5] != ':' {
						return 0, false
					}
					z = z[:5] + z[6:]
				}
			}
			hours, minutes := pyDigitsInt(z[1:3]), pyDigitsInt(z[3:5])
			secs := 0
			if len(z) >= 7 {
				secs = pyDigitsInt(z[5:7])
			}
			frac := 0
			if len(z) > 8 {
				r := z[8:]
				frac = pyDigitsInt(r + strings.Repeat("0", 6-len([]rune(r))))
			}
			off = int64(hours*3600+minutes*60+secs)*1_000_000 + int64(frac)
			if z[0] == '-' {
				off = -off
			}
		}
	}
	leapFix := false
	if !haveYear {
		if month == 2 && day == 29 {
			year, leapFix = 1904, true
		} else {
			year = 1900
		}
	}
	if julian >= 0 {
		if !validDate(year, 1, 1) {
			return 0, false
		}
		y, mo, d := ordToYMD(int64(julian-1) + ymdToOrd(year, 1, 1))
		year, month, day = y, mo, d
	} else if !validDate(year, month, day) {
		return 0, false
	}
	if leapFix {
		year = 1900
	}
	if !validDate(year, month, day) || hour > 23 || minute > 59 || second > 59 {
		return 0, false
	}
	t := MakeDateTime(year, month, day, hour, minute, second, fraction)
	if aware {
		if off <= -usPerDay || off >= usPerDay {
			return 0, false
		}
		t -= DateTime(off)
		if t < minDateTime || t > maxDateTime {
			return 0, false
		}
	}
	return t, true
}

var (
	fullMonths = []string{"january", "february", "march", "april", "may", "june", "july",
		"august", "september", "october", "november", "december"}
	abbrMonths = []string{"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
		"oct", "nov", "dec"}
	fullDays = []string{"monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
		"sunday"}
	abbrDays = []string{"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
)

func indexOf(xs []string, s string) int {
	for i, x := range xs {
		if x == s {
			return i
		}
	}
	return -1
}

// pyDigitsInt is int() of a run of Unicode decimal digits (Python's \d matched them).
func pyDigitsInt(s string) int {
	n := 0
	for _, r := range s {
		n = n*10 + digitValue(r)
	}
	return n
}

// digitValue is the value of a Unicode decimal digit (category Nd): decimal digits
// come in runs of ten consecutive code points starting at zero.
func digitValue(r rune) int {
	if r >= '0' && r <= '9' {
		return int(r - '0')
	}
	start := r
	for unicode.IsDigit(start - 1) {
		start--
	}
	return int(r-start) % 10
}

const (
	pyDigit = `\p{Nd}`
	// Python's \s in a str pattern: str.isspace() characters.
	pySpace = `[\t\n\x0b\x0c\r\x1c-\x1f \x{85}\x{a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]`
)

func seqRE(name string, xs []string) string {
	sorted := append([]string(nil), xs...)
	sort.SliceStable(sorted, func(i, j int) bool { return len(sorted[i]) > len(sorted[j]) })
	return "(?P<" + name + ">" + strings.Join(sorted, "|") + ")"
}

var strptimeDirectives = map[byte]string{
	'd': `(?P<d>3[0-1]|[1-2]\d|0[1-9]|[1-9]| [1-9])`,
	'f': `(?P<f>[0-9]{1,6})`,
	'H': `(?P<H>2[0-3]|[0-1]\d|\d| \d)`,
	'I': `(?P<I>1[0-2]|0[1-9]|[1-9]| [1-9])`,
	'j': `(?P<j>36[0-6]|3[0-5]\d|[1-2]\d\d|0[1-9]\d|00[1-9]|[1-9]\d|0[1-9]|[1-9])`,
	'm': `(?P<m>1[0-2]|0[1-9]|[1-9])`,
	'M': `(?P<M>[0-5]\d|\d)`,
	'S': `(?P<S>6[0-1]|[0-5]\d|\d)`,
	'y': `(?P<y>\d\d)`,
	'Y': `(?P<Y>\d\d\d\d)`,
	'z': `(?P<z>[+-]\d\d:?[0-5]\d(:?[0-5]\d(\.\d{1,6})?)?|(?-i:Z))`,
	'A': seqRE("A", fullDays),
	'a': seqRE("a", abbrDays),
	'B': seqRE("B", fullMonths),
	'b': seqRE("b", abbrMonths),
	'p': seqRE("p", []string{"am", "pm"}),
	'%': `%`,
}

func init() {
	strptimeDirectives['e'] = strptimeDirectives['d']
	strptimeDirectives['k'] = strptimeDirectives['H']
	strptimeDirectives['l'] = strptimeDirectives['I']
	strptimeDirectives['h'] = strptimeDirectives['b']
	strptimeDirectives['P'] = strptimeDirectives['p']
}

var (
	strptimeCache sync.Map // format -> *regexp.Regexp (nil: unsupported)
	reEscapeChars = regexp.MustCompile(`([\\.^$*+?(){}\[\]|])`)
	reWhitespace  = regexp.MustCompile(pySpace + `+`)
	reDirective   = regexp.MustCompile(`%[-_0^#]*[0-9]*([OE]?\\?.?)`)
)

// strptimeRegex is TimeRE.compile(format) (IGNORECASE); ok false for a format Python
// rejects or this port does not support.
func strptimeRegex(format string) (*regexp.Regexp, bool) {
	if v, ok := strptimeCache.Load(format); ok {
		re, _ := v.(*regexp.Regexp)
		return re, re != nil
	}
	re := buildStrptime(format)
	strptimeCache.Store(format, re)
	return re, re != nil
}

func buildStrptime(format string) *regexp.Regexp {
	f := reEscapeChars.ReplaceAllString(format, `\$1`)
	f = reWhitespace.ReplaceAllString(f, `\s+`)
	f = strings.ReplaceAll(f, "'", "['ʼ]")
	bad := false
	seen := map[string]bool{}
	f = reDirective.ReplaceAllStringFunc(f, func(m string) string {
		sub := reDirective.FindStringSubmatch(m)[1]
		var pat string
		switch sub {
		case "T":
			pat = strptimeDirectives['H'] + ":" + strptimeDirectives['M'] + ":" + strptimeDirectives['S']
		case "R":
			pat = strptimeDirectives['H'] + ":" + strptimeDirectives['M']
		default:
			if len(sub) != 1 {
				bad = true
				return ""
			}
			p, ok := strptimeDirectives[sub[0]]
			if !ok {
				bad = true
				return ""
			}
			pat = p
		}
		// Python's re rejects a group name used twice ("redefinition of group name")
		for _, name := range regexp.MustCompile(`\?P<(\w+)>`).FindAllStringSubmatch(pat, -1) {
			if seen[name[1]] {
				bad = true
			}
			seen[name[1]] = true
		}
		return pat
	})
	if bad {
		return nil
	}
	f = strings.ReplaceAll(f, `\s+`, pySpace+`+`)
	f = strings.ReplaceAll(f, `\d`, pyDigit)
	re, err := regexp.Compile(`^(?i:` + f + `)`)
	if err != nil {
		return nil
	}
	return re
}
