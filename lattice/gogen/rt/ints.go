package rt

import (
	"math"
	"math/big"
	"math/bits"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// IntMaxStrDigits is CPython's default sys.get_int_max_str_digits(): int() of a longer
// digit string, and str() of an Int with more digits, raise ValueError.
const IntMaxStrDigits = 4300

// failRange raises R623 for the exact result v, with the message of
// lattice.t2.support.T2IntOverflow (f"integer {value} is outside the 64-bit range").
// Rendering a value of more than IntMaxStrDigits digits is itself a ValueError in
// Python, which then surfaces instead.
func failRange(v *big.Int) {
	s := v.String()
	if len(strings.TrimPrefix(s, "-")) > IntMaxStrDigits {
		Raise("ValueError", "Exceeds the limit (4300 digits) for integer string conversion; "+
			"use sys.set_int_max_str_digits() to increase the limit")
	}
	Fail("R623", "integer "+s+" is outside the 64-bit range")
}

func bigOf(a int64) *big.Int { return big.NewInt(a) }

// Add is a + b.
func Add(a, b int64) int64 {
	s := a + b
	if (a >= 0) == (b >= 0) && (s >= 0) != (a >= 0) {
		failRange(new(big.Int).Add(bigOf(a), bigOf(b)))
	}
	return s
}

// Sub is a - b.
func Sub(a, b int64) int64 {
	d := a - b
	if (a >= 0) != (b >= 0) && (d >= 0) != (a >= 0) {
		failRange(new(big.Int).Sub(bigOf(a), bigOf(b)))
	}
	return d
}

func uabs(a int64) uint64 {
	if a < 0 {
		return uint64(-(a + 1)) + 1 // exact for MinInt64
	}
	return uint64(a)
}

// Mul is a * b.
func Mul(a, b int64) int64 {
	hi, lo := bits.Mul64(uabs(a), uabs(b))
	neg := (a < 0) != (b < 0) && a != 0 && b != 0
	if hi != 0 || (!neg && lo > math.MaxInt64) || (neg && lo > 1<<63) {
		failRange(new(big.Int).Mul(bigOf(a), bigOf(b)))
	}
	if neg {
		return int64(-lo) // two's complement; lo == 1<<63 gives MinInt64
	}
	return int64(lo)
}

// Neg is -a.
func Neg(a int64) int64 {
	if a == math.MinInt64 {
		failRange(new(big.Int).Neg(bigOf(a)))
	}
	return -a
}

// Abs is abs(a).
func Abs(a int64) int64 {
	if a == math.MinInt64 {
		failRange(new(big.Int).Neg(bigOf(a)))
	}
	if a < 0 {
		return -a
	}
	return a
}

func zeroDivision() { Raise("ZeroDivisionError", "division by zero") }

// FloorDiv is a // b (rounds toward negative infinity).
func FloorDiv(a, b int64) int64 {
	if b == 0 {
		zeroDivision()
	}
	if a == math.MinInt64 && b == -1 {
		failRange(new(big.Int).Neg(bigOf(a)))
	}
	q := a / b
	if a%b != 0 && (a < 0) != (b < 0) {
		q--
	}
	return q
}

// Mod is a % b (the result has the sign of b).
func Mod(a, b int64) int64 {
	if b == 0 {
		zeroDivision()
	}
	if b == -1 {
		return 0
	}
	m := a % b
	if m != 0 && (m < 0) != (b < 0) {
		m += b
	}
	return m
}

// failBits is T2Fuel.bits' refusal: Python sizes an Int power / shift before computing
// it, so a result of more than T2MaxIntBits bits is R622 before it can be R623.
func failBits(n int64) {
	Fail("R622", "a procedure would build an Int of about "+strconv.FormatInt(n, 10)+
		" bits; the largest allowed has "+strconv.FormatInt(T2MaxIntBits, 10))
}

func bitLen(a int64) int64 { return int64(bits.Len64(uabs(a))) }

// Pow is a ** b for b >= 0. The T2 checker types `Int ** Int` as Int only when the
// exponent is a non-negative literal (any other exponent makes the result Float), so a
// negative b is a compiler bug, not a program error.
func Pow(a, b int64) int64 {
	if b < 0 {
		panic("rt.Pow: negative exponent (the checker types Int ** -n as Float)")
	}
	switch a {
	case 0:
		if b == 0 {
			return 1
		}
		return 0
	case 1:
		return 1
	case -1:
		if b%2 == 0 {
			return 1
		}
		return -1
	}
	// Square and multiply on the magnitude; any intermediate above 2**63 means the
	// result (|a| >= 2) overflows, except exactly -(2**63).
	base, e, r := uabs(a), b, uint64(1)
	overflow := false
	for e > 0 {
		if e&1 == 1 {
			hi, lo := bits.Mul64(r, base)
			if hi != 0 || lo > 1<<63 {
				overflow = true
				break
			}
			r = lo
		}
		e >>= 1
		if e > 0 {
			hi, lo := bits.Mul64(base, base)
			if hi != 0 || lo > 1<<63 {
				overflow = true
				break
			}
			base = lo
		}
	}
	neg := a < 0 && b%2 == 1
	if !overflow && (r <= math.MaxInt64 || (neg && r == 1<<63)) {
		if neg {
			return int64(-r)
		}
		return int64(r)
	}
	// T2Fuel.pow: bits(abs(a).bit_length() * b), refused past T2MaxIntBits.
	if hi, n := bits.Mul64(uint64(bitLen(a)), uint64(b)); hi != 0 || n > T2MaxIntBits {
		if hi != 0 || n > math.MaxInt64 {
			n = math.MaxInt64
		}
		failBits(int64(n))
	}
	failRange(new(big.Int).Exp(bigOf(a), bigOf(b), nil))
	return 0
}

// Shl is a << b.
func Shl(a, b int64) int64 {
	if b < 0 {
		Raise("ValueError", "negative shift count")
	}
	if a == 0 {
		return 0
	}
	if b < 64 {
		r := a << uint(b)
		if r>>uint(b) == a {
			return r
		}
	}
	// T2Fuel.lshift: bits(a.bit_length() + b).
	if n := bitLen(a) + b; n < 0 || n > T2MaxIntBits {
		if n < 0 {
			n = math.MaxInt64
		}
		failBits(n)
	}
	failRange(new(big.Int).Lsh(bigOf(a), uint(b)))
	return 0
}

// Shr is a >> b (floor division by 2**b).
func Shr(a, b int64) int64 {
	if b < 0 {
		Raise("ValueError", "negative shift count")
	}
	if b >= 64 {
		if a < 0 {
			return -1
		}
		return 0
	}
	return a >> uint(b)
}

// isIntSpace is the whitespace int() strips: ASCII C isspace, and every non-ASCII
// character Python's str.isspace accepts (CPython maps those to ' ' first).
func isIntSpace(r rune) bool {
	if r < utf8.RuneSelf {
		return r == ' ' || (r >= '\t' && r <= '\r')
	}
	return pyIsSpace(r)
}

// ParseInt is Python's int(s) for a str: surrounding whitespace, an optional sign,
// decimal digits (any Unicode decimal digit) with single underscores between them.
// Invalid text is ValueError; a value outside the 64-bit range is R623.
func ParseInt(s string) int64 {
	invalid := func() {
		r := []rune(ReprStr(s))
		if len(r) > 200 { // "%.200R"
			r = r[:200]
		}
		Raise("ValueError", "invalid literal for int() with base 10: "+string(r))
	}
	t := strings.TrimFunc(s, isIntSpace)
	neg := false
	if t != "" && (t[0] == '+' || t[0] == '-') {
		neg = t[0] == '-'
		t = t[1:]
	}
	if t == "" {
		invalid()
	}
	digits := make([]byte, 0, len(t))
	prevUnderscore := true // no leading underscore
	for _, r := range t {
		if r == '_' {
			if prevUnderscore {
				invalid()
			}
			prevUnderscore = true
			continue
		}
		d := decimalValue(r)
		if d < 0 {
			invalid()
		}
		digits = append(digits, byte('0'+d))
		prevUnderscore = false
	}
	if prevUnderscore {
		invalid()
	}
	if len(digits) > IntMaxStrDigits {
		Raise("ValueError", "Exceeds the limit (4300 digits) for integer string conversion: value has "+
			strconv.Itoa(len(digits))+" digits; use sys.set_int_max_str_digits() to increase the limit")
	}
	var u uint64
	over := false
	for _, d := range digits {
		hi, lo := bits.Mul64(u, 10)
		lo2, carry := bits.Add64(lo, uint64(d-'0'), 0)
		if hi != 0 || carry != 0 {
			over = true
			break
		}
		u = lo2
	}
	if !over && (u <= math.MaxInt64 || (neg && u == 1<<63)) {
		if neg {
			return int64(-u)
		}
		return int64(u)
	}
	v, _ := new(big.Int).SetString(string(digits), 10)
	if neg {
		v.Neg(v)
	}
	failRange(v)
	return 0
}

// decimalValue is the value of a Unicode decimal digit (general category Nd), else -1.
func decimalValue(r rune) int {
	if r >= '0' && r <= '9' {
		return int(r - '0')
	}
	if r < utf8.RuneSelf || !unicode.Is(unicode.Nd, r) {
		return -1
	}
	// Nd characters come in contiguous runs of ten, 0 through 9.
	for _, rg := range unicode.Nd.R16 {
		if r >= rune(rg.Lo) && r <= rune(rg.Hi) {
			return int(r-rune(rg.Lo)) % 10
		}
	}
	for _, rg := range unicode.Nd.R32 {
		if r >= rune(rg.Lo) && r <= rune(rg.Hi) {
			return int(r-rune(rg.Lo)) % 10
		}
	}
	return -1
}
