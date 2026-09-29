package rt

import (
	"math"
	"math/big"
	"strconv"
	"strings"
)

// Date, DateTime and Duration as T2 procedures compute them (plan 5a Task 10): Python
// 3.14's datetime.date, an aware UTC datetime.datetime (lattice.t2.support.t2_decode)
// and datetime.timedelta -- the same values, normalisation, text and errors
// (Modules/_datetimemodule.c). Date and DateTime are in dates.go.

// Duration is a timedelta, normalised as Python's: -999999999 <= D <= 999999999 days,
// 0 <= S < 86400 seconds, 0 <= Us < 1000000 microseconds.
type Duration struct{ D, S, Us int64 }

const (
	maxDeltaDays = 999_999_999
	usPerSecond  = 1_000_000
	maxOrdinal   = 3_652_059 // date(9999, 12, 31).toordinal()
)

var (
	bigUsPerDay    = big.NewInt(usPerDay)
	bigUsPerSecond = big.NewInt(usPerSecond)
)

// Micros is the duration in microseconds (delta_to_microseconds).
func (d Duration) Micros() *big.Int {
	m := new(big.Int).Mul(big.NewInt(d.D), big.NewInt(86400))
	m.Add(m, big.NewInt(d.S))
	m.Mul(m, bigUsPerSecond)
	return m.Add(m, big.NewInt(d.Us))
}

// cInt is PyLong_AsInt: an OverflowError past a C int.
func cInt(v *big.Int) int64 {
	if !v.IsInt64() || v.Int64() > math.MaxInt32 || v.Int64() < math.MinInt32 {
		Raise("OverflowError", "Python int too large to convert to C int")
	}
	return v.Int64()
}

// DurationOfMicros is microseconds_to_delta_ex: OverflowError past the day range.
func DurationOfMicros(us *big.Int) Duration {
	s, u := new(big.Int).DivMod(us, bigUsPerSecond, new(big.Int))
	days, secs := new(big.Int).DivMod(s, big.NewInt(86400), new(big.Int))
	d := cInt(days)
	if d < -maxDeltaDays || d > maxDeltaDays {
		Raise("OverflowError", "days="+strconv.FormatInt(d, 10)+"; must have magnitude <= 999999999")
	}
	return Duration{d, secs.Int64(), u.Int64()}
}

// ---- construction: timedelta(...) and the Duration helpers ------------------------------

// DurationPart is one argument of timedelta(): an Int or a Float (IsInt) of a unit.
type DurationPart struct {
	F     float64
	I     int64
	IsInt bool
}

// DI is an Int argument, DF a Float one.
func DI(n int64) DurationPart   { return DurationPart{I: n, IsInt: true} }
func DF(f float64) DurationPart { return DurationPart{F: f} }

// The units of timedelta's arguments, in microseconds.
const (
	UnitSeconds = 1_000_000
	UnitMinutes = 60 * UnitSeconds
	UnitHours   = 60 * UnitMinutes
	UnitDays    = 24 * UnitHours
	UnitWeeks   = 7 * UnitDays
)

// Timedelta is timedelta(seconds=, minutes=, hours=, days=, weeks=) (delta_new): the
// parts accumulated in that order (nil: absent). An Int adds exactly; a Float adds its
// integral part exactly and leaves the rest of its fraction to a float sum, rounded
// half-even to a microsecond at the end.
func Timedelta(seconds, minutes, hours, days, weeks *DurationPart) Duration {
	units := []int64{UnitSeconds, UnitMinutes, UnitHours, UnitDays, UnitWeeks}
	parts := []*DurationPart{seconds, minutes, hours, days, weeks}
	x := new(big.Int)
	leftover := 0.0
	for i, p := range parts {
		if p == nil {
			continue
		}
		factor := big.NewInt(units[i])
		if p.IsInt {
			x.Add(x, new(big.Int).Mul(big.NewInt(p.I), factor))
			continue
		}
		intpart, frac := math.Modf(p.F)
		x.Add(x, new(big.Int).Mul(bigOfFloat(intpart), factor))
		if frac == 0 {
			continue
		}
		dnum := float64(float64(units[i]) * frac)
		ip, fp := math.Modf(dnum)
		x.Add(x, bigOfFloat(ip))
		leftover = float64(leftover + fp)
	}
	if leftover != 0 {
		whole := cRound(leftover)
		if math.Abs(float64(whole-leftover)) == 0.5 {
			odd := 0.0
			if x.Bit(0) == 1 {
				odd = 1
			}
			whole = float64(2.0*cRound(float64(float64(leftover+odd)*0.5))) - odd
		}
		x.Add(x, big.NewInt(int64(whole)))
	}
	return DurationOfMicros(x)
}

// cRound is C's round(): halves away from zero.
func cRound(x float64) float64 { return math.Round(x) }

// bigOfFloat is PyLong_FromDouble of an integral float (ValueError for nan,
// OverflowError for an infinity).
func bigOfFloat(f float64) *big.Int {
	switch {
	case math.IsNaN(f):
		Raise("ValueError", "cannot convert float NaN to integer")
	case math.IsInf(f, 0):
		Raise("OverflowError", "cannot convert float infinity to integer")
	}
	v, _ := new(big.Float).SetFloat64(f).Int(nil)
	return v
}

// ---- arithmetic -----------------------------------------------------------------------

// Add is a + b.
func (d Duration) Add(o Duration) Duration {
	return DurationOfMicros(new(big.Int).Add(d.Micros(), o.Micros()))
}

// Sub is a - b.
func (d Duration) Sub(o Duration) Duration {
	return DurationOfMicros(new(big.Int).Sub(d.Micros(), o.Micros()))
}

// Neg is -d.
func (d Duration) Neg() Duration { return DurationOfMicros(new(big.Int).Neg(d.Micros())) }

// MulInt is d * n.
func (d Duration) MulInt(n int64) Duration {
	return DurationOfMicros(new(big.Int).Mul(d.Micros(), big.NewInt(n)))
}

// ratio is x.as_integer_ratio().
func ratio(x float64) (*big.Int, *big.Int) {
	switch {
	case math.IsNaN(x):
		Raise("ValueError", "cannot convert NaN to integer ratio")
	case math.IsInf(x, 0):
		Raise("OverflowError", "cannot convert Infinity to integer ratio")
	}
	r := new(big.Rat)
	r.SetFloat64(x)
	return new(big.Int).Set(r.Num()), new(big.Int).Set(r.Denom())
}

// divNear is round(m / n) with halves to even (_PyLong_DivmodNear).
func divNear(m, n *big.Int) *big.Int {
	if n.Sign() == 0 {
		Raise("ZeroDivisionError", "division by zero")
	}
	q, r := bigDivMod(m, n)
	// compare 2r with n (r has n's sign)
	twice := new(big.Int).Lsh(r, 1)
	c := twice.CmpAbs(n)
	if c > 0 || (c == 0 && q.Bit(0) == 1) {
		q.Add(q, big.NewInt(1))
	}
	return q
}

// floorDivMod is Python's divmod for ints.
func bigDivMod(m, n *big.Int) (*big.Int, *big.Int) {
	if n.Sign() == 0 {
		Raise("ZeroDivisionError", "division by zero")
	}
	q, r := new(big.Int).QuoRem(m, n, new(big.Int))
	if r.Sign() != 0 && (r.Sign() < 0) != (n.Sign() < 0) {
		q.Sub(q, big.NewInt(1))
		r.Add(r, n)
	}
	return q, r
}

// MulFloat is d * x.
func (d Duration) MulFloat(x float64) Duration {
	num, den := ratio(x)
	return DurationOfMicros(divNear(new(big.Int).Mul(d.Micros(), num), den))
}

// DivInt is d / n.
func (d Duration) DivInt(n int64) Duration {
	return DurationOfMicros(divNear(d.Micros(), big.NewInt(n)))
}

// DivFloat is d / x.
func (d Duration) DivFloat(x float64) Duration {
	num, den := ratio(x)
	return DurationOfMicros(divNear(new(big.Int).Mul(d.Micros(), den), num))
}

// Div is d / o: a Float, correctly rounded.
func (d Duration) Div(o Duration) float64 {
	b := o.Micros()
	if b.Sign() == 0 {
		Raise("ZeroDivisionError", "division by zero")
	}
	f, _ := new(big.Rat).SetFrac(d.Micros(), b).Float64()
	return f
}

// FloorDiv is d // o: an Int (R623 outside the range).
func (d Duration) FloorDiv(o Duration) int64 {
	q, _ := bigDivMod(d.Micros(), o.Micros())
	if !q.IsInt64() {
		failRange(q)
	}
	return q.Int64()
}

// FloorDivInt is d // n.
func (d Duration) FloorDivInt(n int64) Duration {
	q, _ := bigDivMod(d.Micros(), big.NewInt(n))
	return DurationOfMicros(q)
}

// Mod is d % o.
func (d Duration) Mod(o Duration) Duration {
	_, r := bigDivMod(d.Micros(), o.Micros())
	return DurationOfMicros(r)
}

// Cmp is -1, 0 or 1 as d is shorter than, equal to or longer than o.
func (d Duration) Cmp(o Duration) int {
	switch {
	case d.D != o.D:
		return cmp3(d.D, o.D)
	case d.S != o.S:
		return cmp3(d.S, o.S)
	}
	return cmp3(d.Us, o.Us)
}

func cmp3(a, b int64) int {
	switch {
	case a < b:
		return -1
	case a > b:
		return 1
	}
	return 0
}

// Truth is bool(d).
func (d Duration) Truth() bool { return d != Duration{} }

// TotalSeconds is d.total_seconds().
func (d Duration) TotalSeconds() float64 {
	f, _ := new(big.Rat).SetFrac(d.Micros(), bigUsPerSecond).Float64()
	return f
}

// Days is d.days; Seconds d.seconds.
func (d Duration) Days() int64    { return d.D }
func (d Duration) Seconds() int64 { return d.S }

// Str is str(d): "[-]D day[s], H:MM:SS[.ffffff]".
func (d Duration) Str() string {
	h, m, s := d.S/3600, d.S/60%60, d.S%60
	var b strings.Builder
	if d.D != 0 {
		b.WriteString(strconv.FormatInt(d.D, 10) + " day")
		if d.D != 1 && d.D != -1 {
			b.WriteString("s")
		}
		b.WriteString(", ")
	}
	b.WriteString(strconv.FormatInt(h, 10) + ":" + pad2(m) + ":" + pad2(s))
	if d.Us != 0 {
		b.WriteString("." + padN(d.Us, 6))
	}
	return b.String()
}

// PyRepr is repr(d).
func (d Duration) PyRepr() string {
	var parts []string
	if d.D != 0 {
		parts = append(parts, "days="+strconv.FormatInt(d.D, 10))
	}
	if d.S != 0 {
		parts = append(parts, "seconds="+strconv.FormatInt(d.S, 10))
	}
	if d.Us != 0 {
		parts = append(parts, "microseconds="+strconv.FormatInt(d.Us, 10))
	}
	if len(parts) == 0 {
		parts = []string{"0"}
	}
	return "datetime.timedelta(" + strings.Join(parts, ", ") + ")"
}

func pad2(n int64) string { return padN(n, 2) }

func padN(n int64, w int) string {
	s := strconv.FormatInt(n, 10)
	for len(s) < w {
		s = "0" + s
	}
	return s
}

// ---- Date ------------------------------------------------------------------------------

// NewDate is date(y, m, d): Python's errors (a C int first, then the ranges).
func NewDate(y, m, d int64) Date {
	for _, v := range [3]int64{y, m, d} {
		if v > math.MaxInt32 {
			Raise("OverflowError", "signed integer is greater than maximum")
		}
		if v < math.MinInt32 {
			Raise("OverflowError", "signed integer is less than minimum")
		}
	}
	if y < minYear || y > maxYear {
		Raise("ValueError", "year must be in 1..9999, not "+strconv.FormatInt(y, 10))
	}
	if m < 1 || m > 12 {
		Raise("ValueError", "month must be in 1..12, not "+strconv.FormatInt(m, 10))
	}
	if dim := int64(daysInMonth(int(y), int(m))); d < 1 || d > dim {
		Raise("ValueError", "day "+strconv.FormatInt(d, 10)+" must be in range 1.."+
			strconv.FormatInt(dim, 10)+" for month "+strconv.FormatInt(m, 10)+" in year "+
			strconv.FormatInt(y, 10))
	}
	return Date(ymdToOrd(int(y), int(m), int(d)))
}

func dateOfOrd(n int64) Date {
	if n < 1 || n > maxOrdinal {
		Raise("OverflowError", "date value out of range")
	}
	return Date(n)
}

// AddDur is d + dur (only its days count).
func (d Date) AddDur(dur Duration) Date { return dateOfOrd(int64(d) + dur.D) }

// SubDur is d - dur.
func (d Date) SubDur(dur Duration) Date { return dateOfOrd(int64(d) - dur.D) }

// Sub is d - o.
func (d Date) Sub(o Date) Duration { return Duration{D: int64(d) - int64(o)} }

// Weekday is d.weekday() (Monday 0).
func (d Date) Weekday() int64 { return (int64(d) + 6) % 7 }

// IsoWeekday is d.isoweekday() (Monday 1).
func (d Date) IsoWeekday() int64 { return d.Weekday() + 1 }

// Year, Month and Day are d's attributes.
func (d Date) Year() int64 {
	y, _, _ := d.YMD()
	return int64(y)
}
func (d Date) Month() int64 {
	_, m, _ := d.YMD()
	return int64(m)
}
func (d Date) Day() int64 {
	_, _, dd := d.YMD()
	return int64(dd)
}

// PyRepr is repr(d).
func (d Date) PyRepr() string {
	y, m, dd := d.YMD()
	return "datetime.date(" + strconv.Itoa(y) + ", " + strconv.Itoa(m) + ", " + strconv.Itoa(dd) + ")"
}

// Strftime is d.strftime(format) (the directives p8_target lets through).
func (d Date) Strftime(format string) string {
	y, m, dd := d.YMD()
	return strftime(format, y, m, dd, 0, 0, 0, 0, d)
}

// ---- DateTime (an aware UTC datetime) -----------------------------------------------

func dateTimeOf(us *big.Int) DateTime {
	if !us.IsInt64() || DateTime(us.Int64()) < minDateTime || DateTime(us.Int64()) > maxDateTime {
		Raise("OverflowError", "date value out of range")
	}
	return DateTime(us.Int64())
}

// AddDur is t + dur.
func (t DateTime) AddDur(dur Duration) DateTime {
	return dateTimeOf(new(big.Int).Add(big.NewInt(int64(t)), dur.Micros()))
}

// SubDur is t - dur.
func (t DateTime) SubDur(dur Duration) DateTime {
	return dateTimeOf(new(big.Int).Sub(big.NewInt(int64(t)), dur.Micros()))
}

// Sub is t - o.
func (t DateTime) Sub(o DateTime) Duration {
	return DurationOfMicros(new(big.Int).Sub(big.NewInt(int64(t)), big.NewInt(int64(o))))
}

// Date is t.date().
func (t DateTime) Date() Date {
	days, _ := floorDivMod64(int64(t), usPerDay)
	return Date(days + epochOrd)
}

func floorDivMod64(a, b int64) (int64, int64) { return floorDivMod(a, b) }

// Weekday is t.weekday().
func (t DateTime) Weekday() int64 { return t.Date().Weekday() }

// Timestamp is t.timestamp(): (t - epoch).total_seconds().
func (t DateTime) Timestamp() float64 { return TrueDivInt(int64(t), usPerSecond) }

// Isoformat is t.isoformat(): microseconds only when not 0, and the UTC offset.
func (t DateTime) Isoformat() string { return t.isoNaive("T", false) + "+00:00" }

// Str is str(t).
func (t DateTime) Str() string { return t.isoNaive(" ", false) + "+00:00" }

// PyRepr is repr(t).
func (t DateTime) PyRepr() string {
	y, mo, d, h, mi, s, us := t.Parts()
	parts := []int{y, mo, d, h, mi}
	if us != 0 {
		parts = append(parts, s, us)
	} else if s != 0 {
		parts = append(parts, s)
	}
	text := make([]string, len(parts))
	for i, p := range parts {
		text[i] = strconv.Itoa(p)
	}
	return "datetime.datetime(" + strings.Join(text, ", ") + ", tzinfo=datetime.timezone.utc)"
}

// The attributes of a DateTime.
func (t DateTime) Year() int64 {
	y, _, _, _, _, _, _ := t.Parts()
	return int64(y)
}
func (t DateTime) Month() int64 {
	_, m, _, _, _, _, _ := t.Parts()
	return int64(m)
}
func (t DateTime) Day() int64 {
	_, _, d, _, _, _, _ := t.Parts()
	return int64(d)
}
func (t DateTime) Hour() int64 {
	_, _, _, h, _, _, _ := t.Parts()
	return int64(h)
}
func (t DateTime) Minute() int64 {
	_, _, _, _, m, _, _ := t.Parts()
	return int64(m)
}
func (t DateTime) Second() int64 {
	_, _, _, _, _, s, _ := t.Parts()
	return int64(s)
}
func (t DateTime) Microsecond() int64 {
	_, _, _, _, _, _, us := t.Parts()
	return int64(us)
}

// Strftime is t.strftime(format).
func (t DateTime) Strftime(format string) string {
	y, mo, d, h, mi, s, us := t.Parts()
	return strftime(format, y, mo, d, h, mi, s, us, t.Date())
}

// FmtDate is format(d, spec) (a spec is a strftime format), with its size check.
func FmtDate(f *Fuel, d Date, spec string) string {
	SpecGrow(f, spec)
	if spec == "" {
		return d.ISO()
	}
	return d.Strftime(spec)
}

// FmtDateTime is format(t, spec).
func FmtDateTime(f *Fuel, t DateTime, spec string) string {
	SpecGrow(f, spec)
	if spec == "" {
		return t.Str()
	}
	return t.Strftime(spec)
}

// ---- strftime ----------------------------------------------------------------------------

var (
	dayAbbr    = [7]string{"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
	dayName    = [7]string{"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
	monthAbbr  = [13]string{"", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"}
	monthNames = [13]string{"", "January", "February", "March", "April", "May", "June", "July",
		"August", "September", "October", "November", "December"}
)

// StrftimeDirectives are the directives Go implements (C locale); a format with any
// other is refused for Go (L892, lattice.check.p8_target).
const StrftimeDirectives = "YmdHMSfyaAbBjp%"

func strftime(format string, y, mo, d, h, mi, s, us int, date Date) string {
	var b strings.Builder
	rs := []rune(format)
	for i := 0; i < len(rs); i++ {
		if rs[i] != '%' || i+1 == len(rs) {
			b.WriteRune(rs[i])
			continue
		}
		i++
		switch rs[i] {
		case 'Y':
			b.WriteString(padN(int64(y), 4))
		case 'y':
			b.WriteString(pad2(int64(y % 100)))
		case 'm':
			b.WriteString(pad2(int64(mo)))
		case 'd':
			b.WriteString(pad2(int64(d)))
		case 'H':
			b.WriteString(pad2(int64(h)))
		case 'M':
			b.WriteString(pad2(int64(mi)))
		case 'S':
			b.WriteString(pad2(int64(s)))
		case 'f':
			b.WriteString(padN(int64(us), 6))
		case 'a':
			b.WriteString(dayAbbr[date.Weekday()])
		case 'A':
			b.WriteString(dayName[date.Weekday()])
		case 'b':
			b.WriteString(monthAbbr[mo])
		case 'B':
			b.WriteString(monthNames[mo])
		case 'j':
			b.WriteString(padN(int64(date)-ymdToOrd(y, 1, 1)+1, 3))
		case 'p':
			if h < 12 {
				b.WriteString("AM")
			} else {
				b.WriteString("PM")
			}
		case '%':
			b.WriteByte('%')
		default:
			panic("rt.strftime: %" + string(rs[i]) + " is not available on Go (p8_target refuses it)")
		}
	}
	return b.String()
}
