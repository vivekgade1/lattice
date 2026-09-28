package rt

import (
	"fmt"
	"strings"
	"time"
	"unicode/utf8"
)

// Date is a Lattice Date: Python's date.toordinal() (0001-01-01 is 1). Only years
// 1..9999 exist, as in Python.
type Date int64

// DateTime is a Lattice DateTime: a UTC instant, microseconds since 1970-01-01T00:00Z.
// Only years 1..9999 exist, as in Python.
type DateTime int64

const (
	minYear  = 1
	maxYear  = 9999
	epochOrd = 719163 // date(1970, 1, 1).toordinal()
	usPerDay = 86_400_000_000
)

var daysBeforeMonth = [13]int{0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334}

func isLeap(y int) bool { return y%4 == 0 && (y%100 != 0 || y%400 == 0) }

func daysInMonth(y, m int) int {
	if m == 2 && isLeap(y) {
		return 29
	}
	return [13]int{0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}[m]
}

func daysBeforeYear(y int) int64 {
	n := int64(y - 1)
	return n*365 + n/4 - n/100 + n/400
}

// ymdToOrd is Python's _ymd2ord (the date must be valid).
func ymdToOrd(y, m, d int) int64 {
	db := daysBeforeMonth[m]
	if m > 2 && isLeap(y) {
		db++
	}
	return daysBeforeYear(y) + int64(db) + int64(d)
}

// ordToYMD is Python's _ord2ymd.
func ordToYMD(n int64) (int, int, int) {
	n--
	n400, n0 := n/146097, n%146097
	year := int(n400)*400 + 1
	n100, n1 := n0/36524, n0%36524
	n4, n2 := n1/1461, n1%1461
	n1y, n3 := n2/365, n2%365
	year += int(n100)*100 + int(n4)*4 + int(n1y)
	if n1y == 4 || n100 == 4 {
		return year - 1, 12, 31
	}
	leap := n1y == 3 && (n4 != 24 || n100 == 3)
	month := int((n3 + 50) >> 5)
	preceding := daysBeforeMonth[month]
	if month > 2 && leap {
		preceding++
	}
	if int64(preceding) > n3 {
		month--
		preceding = daysBeforeMonth[month]
		if month > 2 && leap {
			preceding++
		}
	}
	return year, month, int(n3-int64(preceding)) + 1
}

func validDate(y, m, d int) bool {
	return y >= minYear && y <= maxYear && m >= 1 && m <= 12 && d >= 1 && d <= daysInMonth(y, m)
}

// MakeDate is date(y, m, d); ok is false where Python raises ValueError.
func MakeDate(y, m, d int) (Date, bool) {
	if !validDate(y, m, d) {
		return 0, false
	}
	return Date(ymdToOrd(y, m, d)), true
}

// YMD is the date's year, month and day.
func (d Date) YMD() (int, int, int) { return ordToYMD(int64(d)) }

// ISO is date.isoformat().
func (d Date) ISO() string {
	y, m, dd := d.YMD()
	return fmt.Sprintf("%04d-%02d-%02d", y, m, dd)
}

var (
	minDateTime = DateTime((ymdToOrd(minYear, 1, 1) - epochOrd) * usPerDay)
	maxDateTime = DateTime((ymdToOrd(maxYear, 12, 31)-epochOrd+1)*usPerDay - 1)
)

// MakeDateTime is the UTC instant of a valid civil date-time.
func MakeDateTime(y, mo, d, h, mi, s, us int) DateTime {
	days := ymdToOrd(y, mo, d) - epochOrd
	return DateTime(days*usPerDay + int64(h)*3_600_000_000 + int64(mi)*60_000_000 +
		int64(s)*1_000_000 + int64(us))
}

func floorDivMod(a, b int64) (int64, int64) {
	q, r := a/b, a%b
	if r < 0 {
		q, r = q-1, r+b
	}
	return q, r
}

// Parts is the instant's UTC year, month, day, hour, minute, second and microsecond.
func (t DateTime) Parts() (y, mo, d, h, mi, s, us int) {
	days, rem := floorDivMod(int64(t), usPerDay)
	y, mo, d = ordToYMD(days + epochOrd)
	us = int(rem % 1_000_000)
	secs := rem / 1_000_000
	return y, mo, d, int(secs / 3600), int(secs / 60 % 60), int(secs % 60), us
}

// DateTimeOf is the instant of a Go time (truncated to microseconds).
func DateTimeOf(t time.Time) DateTime {
	return DateTime(t.Unix()*1_000_000 + int64(t.Nanosecond()/1000))
}

// Now is the current instant (fn.now()).
func Now() DateTime { return DateTimeOf(time.Now()) }

// Text is the prelude's datetime_text: fixed-width UTC ISO-8601 with microseconds and
// a "Z" (the "iso_z" storage and wire spelling).
func (t DateTime) Text() string {
	y, mo, d, h, mi, s, us := t.Parts()
	return fmt.Sprintf("%04d-%02d-%02dT%02d:%02d:%02d.%06dZ", y, mo, d, h, mi, s, us)
}

// isoNaive is the UTC value's isoformat() without a zone (microseconds only when not 0).
func (t DateTime) isoNaive(sep string, alwaysMicros bool) string {
	y, mo, d, h, mi, s, us := t.Parts()
	out := fmt.Sprintf("%04d-%02d-%02d%s%02d:%02d:%02d", y, mo, d, sep, h, mi, s)
	if us != 0 || alwaysMicros {
		out += fmt.Sprintf(".%06d", us)
	}
	return out
}

// ---- datetime.fromisoformat / date.fromisoformat (CPython's C implementation) --------

func isDigit(c byte) bool { return c >= '0' && c <= '9' }

// at is s[i], or 0 past the end (the C code reads a NUL-terminated buffer).
func at(s string, i int) byte {
	if i >= 0 && i < len(s) {
		return s[i]
	}
	return 0
}

// digits parses exactly n ASCII digits at s[i:]; ok false otherwise.
func digits(s string, i, n int) (int, int, bool) {
	v := 0
	for k := 0; k < n; k++ {
		c := at(s, i+k)
		if !isDigit(c) {
			return 0, i, false
		}
		v = v*10 + int(c-'0')
	}
	return v, i + n, true
}

func isoWeek1Monday(year int) int64 {
	first := ymdToOrd(year, 1, 1)
	wd := (first + 6) % 7
	monday := first - wd
	if wd > 3 {
		monday += 7
	}
	return monday
}

func isoToYMD(year, week, day int) (int, int, int, bool) {
	if year < minYear || year > maxYear {
		return 0, 0, 0, false
	}
	if week <= 0 || week >= 53 {
		ok := false
		if week == 53 {
			first := ymdToOrd(year, 1, 1) % 7
			if first == 4 || (first == 3 && isLeap(year)) {
				ok = true
			}
		}
		if !ok {
			return 0, 0, 0, false
		}
	}
	if day <= 0 || day >= 8 {
		return 0, 0, 0, false
	}
	y, m, d := ordToYMD(isoWeek1Monday(year) + int64((week-1)*7+day-1))
	return y, m, d, true
}

// parseISODate is CPython's parse_isoformat_date over s[:n].
func parseISODate(s string, n int) (int, int, int, bool) {
	year, p, ok := digits(s, 0, 4)
	if !ok {
		return 0, 0, 0, false
	}
	sep := at(s, p) == '-'
	if sep {
		p++
	}
	if at(s, p) == 'W' {
		p++
		week, q, ok := digits(s, p, 2)
		if !ok {
			return 0, 0, 0, false
		}
		p = q
		day := 1
		if p < n {
			if sep {
				if at(s, p) != '-' {
					return 0, 0, 0, false
				}
				p++
			}
			if day, _, ok = digits(s, p, 1); !ok {
				return 0, 0, 0, false
			}
		}
		return isoToYMD(year, week, day)
	}
	month, p, ok := digits(s, p, 2)
	if !ok {
		return 0, 0, 0, false
	}
	if sep {
		if at(s, p) != '-' {
			return 0, 0, 0, false
		}
		p++
	}
	day, _, ok := digits(s, p, 2)
	if !ok {
		return 0, 0, 0, false
	}
	return year, month, day, true
}

// ParseISODate is date.fromisoformat(s); ok false where Python raises ValueError.
func ParseISODate(s string) (Date, bool) {
	if n := len(s); n != 7 && n != 8 && n != 10 {
		return 0, false
	}
	y, m, d, ok := parseISODate(s, len(s))
	if !ok {
		return 0, false
	}
	return MakeDate(y, m, d)
}

func isoSeparator(s string) int {
	n := len(s)
	if n == 7 {
		return 7
	}
	if at(s, 4) == '-' {
		if at(s, 5) == 'W' {
			if n < 8 {
				return -1
			}
			if n > 8 && at(s, 8) == '-' {
				if n == 9 {
					return -1
				}
				if n > 10 && isDigit(at(s, 10)) {
					return 8
				}
				return 10
			}
			return 8
		}
		return 10
	}
	if at(s, 4) == 'W' {
		idx := 7
		for ; idx < n; idx++ {
			if !isDigit(s[idx]) {
				break
			}
		}
		if idx < 9 {
			return idx
		}
		if idx%2 == 0 {
			return 7
		}
		return 8
	}
	return 8
}

// parseHMSF is CPython's parse_hh_mm_ss_ff over s[:end]: 0 at the end of the string,
// 1 when something follows, <0 on error.
func parseHMSF(s string, end int) (h, m, sec, us, rv int) {
	vals := [3]int{}
	p := 0
	hasSep := true
	i := 0
	for ; i < 3; i++ {
		v, q, ok := digits(s, p, 2)
		if !ok {
			return 0, 0, 0, 0, -3
		}
		vals[i] = v
		p = q
		c := at(s, p)
		p++
		if i == 0 {
			hasSep = c == ':'
		}
		if p >= end {
			r := 0
			if c != 0 {
				r = 1
			}
			return vals[0], vals[1], vals[2], 0, r
		} else if hasSep && c == ':' {
			if i == 2 {
				return 0, 0, 0, 0, -4
			}
			continue
		} else if c == '.' || c == ',' {
			if i < 2 {
				return 0, 0, 0, 0, -3
			}
			break
		} else if !hasSep {
			p--
		} else {
			return 0, 0, 0, 0, -4
		}
	}
	remains := end - p
	if remains <= 0 {
		return 0, 0, 0, 0, -3
	}
	toParse := min(remains, 6)
	frac, q, ok := digits(s, p, toParse)
	if !ok {
		return 0, 0, 0, 0, -3
	}
	for k := toParse; k < 6; k++ {
		frac *= 10
	}
	p = q
	for isDigit(at(s, p)) {
		p++
	}
	r := 0
	if at(s, p) != 0 {
		r = 1
	}
	return vals[0], vals[1], vals[2], frac, r
}

// parseISOTime is CPython's parse_isoformat_time: (h, m, s, us, tz offset in
// microseconds, has tz, ok).
func parseISOTime(s string) (h, m, sec, us int, off int64, aware, ok bool) {
	tz := strings.IndexAny(s, "Z+-")
	if tz < 0 {
		tz = len(s)
	}
	h, m, sec, us, rv := parseHMSF(s, tz)
	if rv < 0 {
		return
	}
	if tz >= len(s) {
		if rv == 1 {
			return
		}
		return h, m, sec, us, 0, false, true
	}
	if s[tz] == 'Z' {
		if tz+1 != len(s) {
			return
		}
		return h, m, sec, us, 0, true, true
	}
	sign := int64(1)
	if s[tz] == '-' {
		sign = -1
	}
	rest := s[tz+1:]
	th, tm, ts, tus, trv := parseHMSF(rest, len(rest))
	if th > 23 || tm > 59 || ts > 59 {
		return
	}
	if trv != 0 {
		return
	}
	off = sign * ((int64(th)*3600+int64(tm)*60+int64(ts))*1_000_000 + int64(tus))
	return h, m, sec, us, off, true, true
}

// ParseDateTime is the prelude's parse_datetime: an ISO-8601 date-time (a naive one
// is UTC) as a UTC instant; ok false where Python answers None.
func ParseDateTime(s string) (DateTime, bool) {
	if utf8.RuneCountInString(s) < 7 {
		return 0, false
	}
	sepAt := isoSeparator(s)
	if sepAt < 0 {
		return 0, false
	}
	y, mo, d, ok := parseISODate(s, sepAt)
	if !ok {
		return 0, false
	}
	h, mi, sec, us := 0, 0, 0, 0
	var off int64
	if len(s) > sepAt {
		_, size := utf8.DecodeRuneInString(s[sepAt:])
		if s[sepAt] >= 0x80 && size == 1 {
			return 0, false
		}
		h, mi, sec, us, off, _, ok = parseISOTime(s[sepAt+size:])
		if !ok {
			return 0, false
		}
	}
	if h == 24 && mo >= 1 && mo <= 12 {
		dim := daysInMonth(y, mo)
		if d <= dim {
			if mi != 0 || sec != 0 || us != 0 {
				return 0, false
			}
			h = 0
			d++
			if d > dim {
				d, mo = 1, mo+1
				if mo > 12 {
					mo, y = 1, y+1
				}
			}
		}
	}
	if !validDate(y, mo, d) || h > 23 || mi > 59 || sec > 59 {
		return 0, false
	}
	t := MakeDateTime(y, mo, d, h, mi, sec, us) - DateTime(off)
	if t < minDateTime || t > maxDateTime { // astimezone(UTC) overflows
		return 0, false
	}
	return t, true
}

// ---- UUIDs ------------------------------------------------------------------------------

// UUID is a Lattice Uuid parameter value: canonical lowercase hyphenated text.
type UUID string

func isHex(c byte) bool { return isDigit(c) || (c|0x20 >= 'a' && c|0x20 <= 'f') }

// UUIDText is the prelude's uuid_text: the canonical text of a UUID written hyphenated
// or as 32 hex digits (any case); ok false otherwise.
func UUIDText(s string) (string, bool) {
	var hex []byte
	switch len(s) {
	case 32:
		hex = []byte(s)
	case 36:
		for i := 0; i < 36; i++ {
			if i == 8 || i == 13 || i == 18 || i == 23 {
				if s[i] != '-' {
					return "", false
				}
				continue
			}
			hex = append(hex, s[i])
		}
	default:
		return "", false
	}
	for _, c := range hex {
		if !isHex(c) {
			return "", false
		}
	}
	h := strings.ToLower(string(hex))
	return h[:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:], true
}
