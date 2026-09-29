package rt

import (
	"math"
	"math/big"
	"math/bits"
	"strconv"
	"strings"
	"unicode/utf8"
)

// The core operations compiled T2 procedures call (lattice/gogen/t2.py): the charge of
// a call's step, Int and Float arithmetic with Python's results and errors, and the
// size checks of growing operators (T2Fuel.add/mul/pow/lshift/aug).

// Step charges one step (T2Fuel.call) and returns nothing: generated code writes
// `rt.After(fuel.Step(), f(args))`, so the step is charged before the arguments are
// evaluated and f is called (Go evaluates call arguments left to right).
func (f *Fuel) Step() struct{} {
	f.charge(1)
	return struct{}{}
}

// After returns v (see Step).
func After[T any](_ struct{}, v T) T { return v }

// P is a pointer to v (an optional slice bound).
func P(v int64) *int64 { return &v }

// ---- Int ----------------------------------------------------------------------------

// SumInt is sum(xs, start) of Ints: Python adds exactly (its ints are unbounded), so
// only the final result is range-checked (R623).
func SumInt(c Cursor[int64], start int64) int64 {
	acc := start
	var b *big.Int
	for {
		x, ok := c()
		if !ok {
			break
		}
		if b == nil {
			s := acc + x
			if (acc >= 0) == (x >= 0) && (s >= 0) != (acc >= 0) {
				b = new(big.Int).Add(bigOf(acc), bigOf(x))
				continue
			}
			acc = s
			continue
		}
		b.Add(b, bigOf(x))
	}
	if b == nil {
		return acc
	}
	if !b.IsInt64() {
		failRange(b)
	}
	return b.Int64()
}

// IntOfFloat is int(x) for a float: truncation toward zero; ValueError for nan,
// OverflowError for an infinity, R623 outside the 64-bit range.
func IntOfFloat(x float64) int64 { return floatToInt(math.Trunc(x)) }

// TrueDivInt is a / b for two Ints: the correctly rounded quotient, as Python's int
// true division (exact for operands beyond 2**53, where float64(a) would round first).
func TrueDivInt(a, b int64) float64 {
	if b == 0 {
		zeroDivision()
	}
	const exact = 1 << 53
	if a >= -exact && a <= exact && b >= -exact && b <= exact {
		return float64(a) / float64(b)
	}
	q, _ := new(big.Rat).SetFrac(bigOf(a), bigOf(b)).Float64()
	if q == 0 && (a < 0) != (b < 0) {
		return math.Copysign(0, -1)
	}
	return q
}

// ---- Float --------------------------------------------------------------------------

// DivF is a / b for floats: ZeroDivisionError for a zero divisor, as Python.
func DivF(a, b float64) float64 {
	if b == 0 {
		zeroDivision()
	}
	return a / b
}

// PowF is a ** b for floats (CPython's float_pow). Python returns a complex number for
// a negative base raised to a non-integer power; a Float has no such value, so that is
// refused with a ValueError here (see task-8-report.md).
func PowF(a, b float64) float64 {
	if b == 0 {
		return 1
	}
	if math.IsNaN(a) {
		return a
	}
	if math.IsNaN(b) {
		if a == 1 {
			return 1
		}
		return b
	}
	if math.IsInf(b, 0) {
		aa := math.Abs(a)
		switch {
		case aa == 1:
			return 1
		case (b > 0) == (aa > 1):
			return math.Inf(1)
		}
		return 0
	}
	if math.IsInf(a, 0) {
		oddInt := math.Mod(math.Abs(b), 2) == 1
		if b > 0 {
			if oddInt {
				return a
			}
			return math.Abs(a)
		}
		if oddInt {
			return math.Copysign(0, a)
		}
		return 0
	}
	if a == 0 {
		if b < 0 {
			Raise("ZeroDivisionError", "zero to a negative power")
		}
		if math.Mod(math.Abs(b), 2) == 1 {
			return a
		}
		return 0
	}
	if a < 0 && b != math.Floor(b) {
		// R-pow: no Float value (Python's ** gives a complex); math.pow's error
		Raise("ValueError", "math domain error")
	}
	if a == 1 || (a == -1 && b == math.Floor(b)) {
		if a == -1 && math.Mod(math.Abs(b), 2) == 1 {
			return -1
		}
		return 1
	}
	neg := a < 0 && math.Mod(math.Abs(b), 2) == 1
	var r float64
	switch b {
	case 1:
		r = math.Abs(a)
	case 2:
		r = float64(a * a)
	case -1:
		r = 1 / math.Abs(a)
	case 0.5:
		r = math.Sqrt(a)
	default:
		r = powCorrect(math.Abs(a), b)
	}
	if math.IsInf(r, 0) {
		Raise("OverflowError", "(34, 'Result too large')")
	}
	if neg {
		return -r
	}
	return r
}

// powPrec is the working precision of powCorrect: far more than float64's 53 bits, so
// the final rounding is correct except for values within 2**-190 of a halfway point
// (C's pow, which CPython calls, is correctly rounded in the same practical sense;
// Go's math.Pow is not).
const powPrec = 256

// powCorrect is a ** b for a finite a > 0 and a finite b, correctly rounded:
// exp(b * ln a) computed in big.Float.
func powCorrect(a, b float64) float64 {
	x := new(big.Float).SetPrec(powPrec).SetFloat64(a)
	y := bigLn(x)
	y.Mul(y, new(big.Float).SetPrec(powPrec).SetFloat64(b))
	// far outside the float range: +inf (overflow) or 0 (underflow)
	if y.Cmp(big.NewFloat(750)) > 0 {
		return math.Inf(1)
	}
	if y.Cmp(big.NewFloat(-760)) < 0 {
		return 0
	}
	r, _ := bigExp(y).Float64()
	return r
}

var bigLn2 = func() *big.Float {
	// ln 2 = 2 atanh(1/3)
	third := new(big.Float).SetPrec(powPrec+64).Quo(big.NewFloat(1), big.NewFloat(3))
	return bigAtanh2(third)
}()

// bigAtanh2 is 2 atanh(z) = 2 (z + z**3/3 + z**5/5 + ...) for a small |z|.
func bigAtanh2(z *big.Float) *big.Float {
	prec := z.Prec()
	sum := new(big.Float).SetPrec(prec).Set(z)
	z2 := new(big.Float).SetPrec(prec).Mul(z, z)
	term := new(big.Float).SetPrec(prec).Set(z)
	eps := new(big.Float).SetPrec(prec).SetMantExp(big.NewFloat(1), -int(prec)-8)
	for k := int64(3); ; k += 2 {
		term.Mul(term, z2)
		t := new(big.Float).SetPrec(prec).Quo(term, new(big.Float).SetInt64(k))
		if t.Sign() == 0 || new(big.Float).Abs(t).Cmp(eps) < 0 {
			break
		}
		sum.Add(sum, t)
	}
	return sum.Mul(sum, big.NewFloat(2))
}

// bigLn is ln x for x > 0: x = m * 2**e with m in [0.5, 1), ln x = e ln 2 + ln m.
func bigLn(x *big.Float) *big.Float {
	prec := x.Prec()
	m := new(big.Float).SetPrec(prec)
	e := x.MantExp(m)
	// ln m = 2 atanh((m - 1) / (m + 1)), |z| <= 1/3
	num := new(big.Float).SetPrec(prec).Sub(m, big.NewFloat(1))
	den := new(big.Float).SetPrec(prec).Add(m, big.NewFloat(1))
	r := bigAtanh2(num.Quo(num, den))
	return r.Add(r, new(big.Float).SetPrec(prec).Mul(bigLn2, new(big.Float).SetInt64(int64(e))))
}

// bigExp is exp(y): y = k ln 2 + r with |r| <= ln 2 / 2, exp(r) by its Taylor series on
// r / 2**16, squared back 16 times.
func bigExp(y *big.Float) *big.Float {
	prec := y.Prec() + 32
	kf := new(big.Float).SetPrec(prec).Quo(y, bigLn2)
	k, _ := kf.Int64()
	if kf.Sign() < 0 {
		k--
	}
	r := new(big.Float).SetPrec(prec).Sub(y, new(big.Float).SetPrec(prec).Mul(bigLn2, new(big.Float).SetInt64(k)))
	const halvings = 16
	r.SetMantExp(r, -halvings)
	sum := new(big.Float).SetPrec(prec).SetInt64(1)
	term := new(big.Float).SetPrec(prec).SetInt64(1)
	eps := new(big.Float).SetPrec(prec).SetMantExp(big.NewFloat(1), -int(prec)-8)
	for n := int64(1); ; n++ {
		term.Mul(term, r)
		term.Quo(term, new(big.Float).SetInt64(n))
		if term.Sign() == 0 || new(big.Float).Abs(term).Cmp(eps) < 0 {
			break
		}
		sum.Add(sum, term)
	}
	for range halvings {
		sum.Mul(sum, sum)
	}
	return sum.SetMantExp(sum, int(k))
}

// Unordered is CmpIF's answer when the float is nan.
const Unordered = 2

// CmpIF compares an Int with a Float exactly (Python does not round the int): -1, 0 or
// 1, or Unordered when b is nan.
func CmpIF(a int64, b float64) int {
	switch {
	case math.IsNaN(b):
		return Unordered
	case math.IsInf(b, 1):
		return -1
	case math.IsInf(b, -1):
		return 1
	}
	fa := float64(a)
	if fa < b {
		return -1
	}
	if fa > b {
		return 1
	}
	// float64(a) == b: b is an integer in about the Int range; compare exactly.
	if b >= 0x1p63 {
		return -1
	}
	if b < -0x1p63 {
		return 1
	}
	bi := int64(b)
	switch {
	case a < bi:
		return -1
	case a > bi:
		return 1
	}
	return 0
}

// isPySpace is the whitespace float() strips (str.isspace).
func isFloatSpace(r rune) bool { return pyIsSpace(r) }

// ParseFloat is Python's float(s) for a str: surrounding whitespace, an optional sign,
// inf/infinity/nan in any case, or a decimal literal with single underscores between
// digits (any Unicode decimal digit). Invalid text is ValueError.
func ParseFloat(s string) float64 {
	invalid := func() {
		Raise("ValueError", "could not convert string to float: "+ReprStr(s))
	}
	t := strings.TrimFunc(s, isFloatSpace)
	var b strings.Builder
	for _, r := range t {
		if r >= utf8.RuneSelf {
			if d := decimalValue(r); d >= 0 {
				b.WriteByte(byte('0' + d))
				continue
			}
			if pyIsSpace(r) {
				b.WriteByte(' ')
				continue
			}
			invalid()
		}
		b.WriteRune(r)
	}
	t = b.String()
	body := t
	if body != "" && (body[0] == '+' || body[0] == '-') {
		body = body[1:]
	}
	switch strings.ToLower(body) {
	case "inf", "infinity":
		if t[0] == '-' {
			return math.Inf(-1)
		}
		return math.Inf(1)
	case "nan":
		if t[0] == '-' {
			return math.Copysign(math.NaN(), -1)
		}
		return math.NaN()
	}
	// digits [. digits] [e [sign] digits], underscores only between two digits
	i, n := 0, len(body)
	clean := make([]byte, 0, n)
	digitsRun := func() int {
		start := i
		for i < n {
			c := body[i]
			if c >= '0' && c <= '9' {
				clean = append(clean, c)
				i++
				continue
			}
			if c == '_' && i > start && i+1 < n && body[i+1] >= '0' && body[i+1] <= '9' &&
				body[i-1] >= '0' && body[i-1] <= '9' {
				i++
				continue
			}
			break
		}
		return i - start
	}
	intDigits := digitsRun()
	fracDigits := 0
	if i < n && body[i] == '.' {
		clean = append(clean, '.')
		i++
		fracDigits = digitsRun()
	}
	if intDigits == 0 && fracDigits == 0 {
		invalid()
	}
	if i < n && (body[i] == 'e' || body[i] == 'E') {
		clean = append(clean, 'e')
		i++
		if i < n && (body[i] == '+' || body[i] == '-') {
			clean = append(clean, body[i])
			i++
		}
		if digitsRun() == 0 {
			invalid()
		}
	}
	if i != n {
		invalid()
	}
	f, err := strconv.ParseFloat(string(clean), 64)
	if err != nil && !strings.Contains(err.Error(), "range") {
		invalid()
	}
	if t[0] == '-' {
		return -f
	}
	return f
}

// RoundF is round(x, n) for a float (CPython's double_round): the value rounded to n
// decimal places, halves to even on the exact decimal value.
func RoundF(x float64, n int64) float64 {
	if math.IsNaN(x) || math.IsInf(x, 0) || x == 0 {
		return x
	}
	// NDIGITS_MAX / NDIGITS_MIN of floatobject.c
	if n > 323 {
		return x
	}
	if n < -308 {
		return math.Copysign(0, x)
	}
	var text string
	if n >= 0 {
		text = strconv.FormatFloat(x, 'f', int(n), 64) // correctly rounded, half to even
	} else {
		// round to a multiple of 10**-n, halves to even on the exact binary value (as
		// _Py_dg_dtoa mode 3 does), then back to the nearest float
		unit := new(big.Int).Exp(big.NewInt(10), big.NewInt(-n), nil)
		r := new(big.Rat).SetFloat64(math.Abs(x))
		r.Quo(r, new(big.Rat).SetInt(unit))
		q, rem := new(big.Int).QuoRem(r.Num(), r.Denom(), new(big.Int))
		twice := new(big.Int).Lsh(rem, 1)
		if c := twice.Cmp(r.Denom()); c > 0 || (c == 0 && q.Bit(0) == 1) {
			q.Add(q, big.NewInt(1))
		}
		text = q.Mul(q, unit).String()
		if x < 0 {
			text = "-" + text
		}
	}
	r, err := strconv.ParseFloat(text, 64)
	if err != nil {
		Raise("OverflowError", "rounded value too large to represent")
	}
	if r == 0 {
		return math.Copysign(0, x)
	}
	return r
}

// SumFloat is sum(xs, start) of Floats (CPython 3.14's builtin_sum): an int start
// (startIsInt) stays an int until the first element, which is added to it plainly;
// from then on (or from a float start) the elements are added with Neumaier's
// compensated summation. isInt reports that the result is the int start itself (no
// elements), which Python keeps as an int.
func SumFloat(c Cursor[float64], start float64, startIsInt bool) (result float64, isInt bool) {
	f := start
	if startIsInt {
		x, ok := c()
		if !ok {
			return start, true
		}
		f = start + x
	}
	comp := 0.0
	for {
		x, ok := c()
		if !ok {
			break
		}
		t := f + x
		if math.Abs(f) >= math.Abs(x) {
			comp += (f - t) + x
		} else {
			comp += (x - t) + f
		}
		f = t
	}
	if comp != 0 && !math.IsInf(comp, 0) && !math.IsNaN(comp) {
		f += comp
	}
	return f, false
}

// ---- sizes (T2Fuel.add / mul / pow / lshift / aug) -------------------------------------

// growExact is T2Fuel.grow for an exact size that may not fit an int64.
func growExact(f *Fuel, size *big.Int) {
	if !size.IsInt64() || size.Int64() > f.MaxSize {
		Fail("R622", "a procedure would build a value of "+size.String()+
			" characters or elements; the largest allowed is "+strconv.FormatInt(f.MaxSize, 10)+
			" (ApiProfile.max_value_size)")
	}
	f.Grow(size.Int64())
}

// SizeAdd is the size check of a concatenation of lengths na and nb.
func SizeAdd(f *Fuel, na, nb int64) { f.Grow(na + nb) }

// SizeRepeat is the size check of a sequence of length n repeated k times.
func SizeRepeat(f *Fuel, n, k int64) {
	k = max(k, 0)
	hi, lo := bits.Mul64(uint64(n), uint64(k))
	if hi != 0 || lo > math.MaxInt64 {
		growExact(f, new(big.Int).Mul(bigOf(n), bigOf(k)))
		return
	}
	f.Grow(int64(lo))
}

// BitsMul is the size check of an Int product (bits(a.bit_length() + b.bit_length())).
func BitsMul(f *Fuel, a, b int64) { f.Bits(bitLen(a) + bitLen(b)) }

// BitsPow is the size check of an Int power: bits(bit_length(|a|) * b) when b > 1 and
// |a| > 1.
func BitsPow(f *Fuel, a, b int64) {
	if b <= 1 || a == 0 || a == 1 || a == -1 {
		return
	}
	hi, lo := bits.Mul64(uint64(bitLen(a)), uint64(b))
	if hi != 0 || lo > math.MaxInt64 {
		n := new(big.Int).Mul(bigOf(bitLen(a)), bigOf(b))
		Fail("R622", "a procedure would build an Int of about "+n.String()+
			" bits; the largest allowed has "+strconv.FormatInt(T2MaxIntBits, 10))
	}
	f.Bits(int64(lo))
}

// BitsShl is the size check of a << b: bits(a.bit_length() + b) when a != 0 and b > 0.
func BitsShl(f *Fuel, a, b int64) {
	if a == 0 || b <= 0 {
		return
	}
	n := bitLen(a) + b
	if n < 0 { // overflowed int64
		v := new(big.Int).Add(bigOf(bitLen(a)), bigOf(b))
		Fail("R622", "a procedure would build an Int of about "+v.String()+
			" bits; the largest allowed has "+strconv.FormatInt(T2MaxIntBits, 10))
	}
	f.Bits(n)
}

// GrowAddStr is `a + b` for strs.
func GrowAddStr(f *Fuel, a, b string) string {
	SizeAdd(f, Len(a), Len(b))
	return a + b
}

// GrowMulStr is `s * n`.
func GrowMulStr(f *Fuel, s string, n int64) string {
	SizeRepeat(f, Len(s), n)
	return RepeatStr(s, n)
}

// RepeatStr is `s * n` without a size check (a module constant, evaluated uncharged).
func RepeatStr(s string, n int64) string {
	if n <= 0 || s == "" {
		return ""
	}
	return strings.Repeat(s, int(n))
}

// SumFloatV is the value of SumFloat.
func SumFloatV(c Cursor[float64], start float64, startIsInt bool) float64 {
	r, _ := SumFloat(c, start, startIsInt)
	return r
}

// FloatAsInt is f as an Int when f is integral and in the Int range (the only Floats
// equal to an Int): an exact `f in ints`.
func FloatAsInt(f float64) (int64, bool) {
	if f != math.Trunc(f) || f < -9223372036854775808.0 || f >= 9223372036854775808.0 {
		return 0, false
	}
	return int64(f), true
}

// CmpIFOp is `a op b` for an Int a and a Float b (op: == < <= > >=), exactly.
func CmpIFOp(a int64, b float64, op string) bool { return cmpHolds(CmpIF(a, b), op) }

// CmpFIOp is `a op b` for a Float a and an Int b, exactly.
func CmpFIOp(a float64, b int64, op string) bool {
	c := CmpIF(b, a)
	if c != Unordered {
		c = -c
	}
	return cmpHolds(c, op)
}

func cmpHolds(c int, op string) bool {
	if c == Unordered {
		return false
	}
	switch op {
	case "==":
		return c == 0
	case "<":
		return c < 0
	case "<=":
		return c <= 0
	case ">":
		return c > 0
	case ">=":
		return c >= 0
	}
	panic("rt: comparison " + op)
}

// GrowAddList is `a + b` for lists: a new list.
func GrowAddList[T any](f *Fuel, a, b *List[T]) *List[T] {
	SizeAdd(f, a.Len(), b.Len())
	out := make([]T, 0, len(a.Items)+len(b.Items))
	return &List[T]{Items: append(append(out, a.Items...), b.Items...)}
}

// GrowMulList is `xs * n`: a new list.
func GrowMulList[T any](f *Fuel, l *List[T], n int64) *List[T] {
	SizeRepeat(f, l.Len(), n)
	return &List[T]{Items: repeat(l.Items, n)}
}

func repeat[T any](xs []T, n int64) []T {
	if n <= 0 || len(xs) == 0 {
		return []T{}
	}
	out := make([]T, 0, len(xs)*int(n))
	for range n {
		out = append(out, xs...)
	}
	return out
}

// IMulList is `xs *= n` (in place; the size is checked by the caller).
func IMulList[T any](l *List[T], n int64) { l.Items = repeat(l.Items, n) }

// GrowMulInt is `a * b` for Ints.
func GrowMulInt(f *Fuel, a, b int64) int64 {
	BitsMul(f, a, b)
	return Mul(a, b)
}

// GrowPowInt is `a ** b` for Ints (b >= 0: the checker types other powers Float).
func GrowPowInt(f *Fuel, a, b int64) int64 {
	BitsPow(f, a, b)
	return Pow(a, b)
}

// GrowShlInt is `a << b`.
func GrowShlInt(f *Fuel, a, b int64) int64 {
	BitsShl(f, a, b)
	return Shl(a, b)
}

// PowIntF is `a ** b` for Ints typed Float (the checker types `Int ** n` as Int only
// for a literal n >= 0): a float power. T2 IR lowering widens both operands of such a
// power (R-widen), so the emitter calls PowF for it; this stays as the same meaning.
func PowIntF(a, b int64) float64 {
	if a == 0 && b < 0 {
		Raise("ZeroDivisionError", "zero to a negative power")
	}
	return PowF(float64(a), float64(b))
}

// ---- values ----------------------------------------------------------------------------

// StrInt is str(i).
func StrInt(i int64) string { return strconv.FormatInt(i, 10) }

// ReprOpt is repr / str of an optional value: "None", or the value's own text.
func ReprOpt[T any](o Opt[T], text func(T) string) string {
	if !o.Ok {
		return "None"
	}
	return text(o.V)
}

// PyRepr makes an Opt usable where Repr is (a KeyError's message).
func (o Opt[T]) PyRepr() string {
	if !o.Ok {
		return "None"
	}
	return Repr(any(o.V))
}

// Unpack is `a, b, ... = xs` for a list: exactly n elements, else ValueError.
func Unpack[T any](xs []T, n int) []T {
	if len(xs) > n {
		Raise("ValueError", "too many values to unpack (expected "+strconv.Itoa(n)+", got "+
			strconv.Itoa(len(xs))+")")
	}
	return UnpackIter(xs, n)
}

// UnpackIter is `a, b, ... = it` for a str or an iterator (whose length Python does
// not report when there are too many).
func UnpackIter[T any](xs []T, n int) []T {
	if len(xs) < n {
		Raise("ValueError", "not enough values to unpack (expected "+strconv.Itoa(n)+", got "+
			strconv.Itoa(len(xs))+")")
	}
	if len(xs) > n {
		Raise("ValueError", "too many values to unpack (expected "+strconv.Itoa(n)+")")
	}
	return xs
}

// MapCursor is c with f applied to each element (an Int iterable summed as Floats).
func MapCursor[A, B any](c Cursor[A], f func(A) B) Cursor[B] {
	return func() (B, bool) {
		v, ok := c()
		if !ok {
			var z B
			return z, false
		}
		return f(v), true
	}
}

// ReprError is repr(e) of a caught exception: KeyError('k'), ValueError('text').
func ReprError(e *Error) string {
	if e.Kind == "KeyError" { // its message is already repr(key)
		return e.Kind + "(" + e.Msg + ")"
	}
	return e.Kind + "(" + ReprStr(e.Msg) + ")"
}

// FmtObj is `f"{x:spec}"` for a value whose type has only object.__format__ (a record,
// a UUID, an exception): the spec's size check, then TypeError.
func FmtObj(f *Fuel, spec, tname string) string {
	SpecGrow(f, spec)
	Raise("TypeError", "unsupported format string passed to "+tname+".__format__")
	return ""
}

// EscapeNonASCII is ascii() of a value from its repr: every non-ASCII character
// escaped.
func EscapeNonASCII(repr string) string {
	if isASCII(repr) {
		return repr
	}
	var b strings.Builder
	for _, c := range repr {
		switch {
		case c < utf8.RuneSelf:
			b.WriteRune(c)
		case c <= 0xff:
			b.WriteString(`\x` + hexPad(c, 2))
		case c <= 0xffff:
			b.WriteString(`\u` + hexPad(c, 4))
		default:
			b.WriteString(`\U` + hexPad(c, 8))
		}
	}
	return b.String()
}

// Runes is list(s): the code points of s, each a str.
func Runes(s string) []string {
	out := make([]string, 0, len(s))
	for _, r := range s {
		out = append(out, string(r))
	}
	return out
}

// IndexSlice is xs[i] for a homogeneous tuple (a negative i counts from the end).
func IndexSlice[T any](xs []T, i int64, msg string) T {
	n := int64(len(xs))
	if i < 0 {
		i += n
	}
	if i < 0 || i >= n {
		Raise("IndexError", msg)
	}
	return xs[i]
}

// ListEq is a == b for lists, with the elements' equality.
func ListEq[T any](a, b *List[T], eq func(x, y T) bool) bool {
	if len(a.Items) != len(b.Items) {
		return false
	}
	for i := range a.Items {
		if !eq(a.Items[i], b.Items[i]) {
			return false
		}
	}
	return true
}

// DictEq is a == b for dicts (order does not matter), with the values' equality.
func DictEq[K comparable, V any](a, b *Dict[K, V], eq func(x, y V) bool) bool {
	if a.Len() != b.Len() {
		return false
	}
	for _, e := range a.entries {
		if !e.live {
			continue
		}
		v, ok := b.Lookup(e.key)
		if !ok || !eq(e.val, v) {
			return false
		}
	}
	return true
}

// OptEq is a == b for optional values.
func OptEq[T any](a, b Opt[T], eq func(x, y T) bool) bool {
	if a.Ok != b.Ok {
		return false
	}
	return !a.Ok || eq(a.V, b.V)
}

// Eq is a == b for comparable scalars.
func Eq[T comparable](a, b T) bool { return a == b }

// ListContains is x in l.
func ListContains[T any](l *List[T], x T, eq func(a, b T) bool) bool {
	for i := 0; i < len(l.Items); i++ {
		if eq(l.Items[i], x) {
			return true
		}
	}
	return false
}

// SliceContains is x in a tuple's elements.
func SliceContains[T any](xs []T, x T, eq func(a, b T) bool) bool {
	for _, y := range xs {
		if eq(y, x) {
			return true
		}
	}
	return false
}

// ContainsStr is sub in s.
func ContainsStr(s, sub string) bool { return strings.Contains(s, sub) }

// DictUnion is a | b: a new dict.
func DictUnion[K comparable, V any](a, b *Dict[K, V]) *Dict[K, V] {
	out := a.Copy()
	DictUpdate(out, b)
	return out
}

// DictUpdate is a |= b (and `{**b}` entries): b's items set into a, in b's order.
func DictUpdate[K comparable, V any](a, b *Dict[K, V]) {
	for _, p := range b.Items() {
		a.Set(p.Key, p.Value)
	}
}

// B2I is int(b): Python orders False < True.
func B2I(b bool) int {
	if b {
		return 1
	}
	return 0
}

// AndB is a & b for Bools (both operands evaluated).
func AndB(a, b bool) bool { return a && b }

// OrB is a | b for Bools.
func OrB(a, b bool) bool { return a || b }

// XorB is a ^ b for Bools.
func XorB(a, b bool) bool { return a != b }

// ISetOp is an in-place set operator: s |= o, s &= o, s -= o, s ^= o (the left
// operand's order first, as T2Set).
func ISetOp[T comparable](s, o *Set[T], op byte) {
	switch op {
	case '|':
		s.Update(o.Items()...)
	case '&':
		for _, x := range s.Items() {
			if !o.Has(x) {
				s.Discard(x)
			}
		}
	case '-':
		for _, x := range o.Items() {
			s.Discard(x)
		}
	case '^':
		var fresh []T
		for _, x := range o.Items() {
			if s.Has(x) {
				s.Discard(x)
			} else {
				fresh = append(fresh, x)
			}
		}
		s.Update(fresh...)
	}
}
