package rt

import (
	"math"
	"math/big"
)

// The `math` functions of T2 procedures (catalog T2_STDLIB["math"]), as CPython 3.14's
// mathmodule.c computes them: its special cases and error messages, and results
// correctly rounded (exp, log and log10 in big.Float, as C's libm rounds them; Go's
// math.Exp and math.Log may be one ulp off).

// MathFloor is math.floor(x) of a Float: an Int (R623 outside the range; ValueError for
// nan, OverflowError for an infinity, as int()).
func MathFloor(x float64) int64 { return floatToInt(math.Floor(x)) }

// MathCeil is math.ceil(x) of a Float.
func MathCeil(x float64) int64 { return floatToInt(math.Ceil(x)) }

// MathTrunc is math.trunc(x) of a Float.
func MathTrunc(x float64) int64 { return floatToInt(math.Trunc(x)) }

// math1 is CPython's math_1 after func(x) gave r: a nan from a non-nan is a domain
// error (errMsg with x's repr, or "math domain error"); an infinity from a finite x is
// OverflowError when the function can overflow, else a domain error.
func math1(x, r float64, canOverflow bool, errMsg string) float64 {
	if math.IsNaN(r) && !math.IsNaN(x) {
		mathDomain(x, errMsg)
	}
	if math.IsInf(r, 0) && !math.IsInf(x, 0) && !math.IsNaN(x) {
		if canOverflow {
			Raise("OverflowError", "math range error")
		}
		mathDomain(x, errMsg)
	}
	return r
}

func mathDomain(x float64, errMsg string) {
	if errMsg == "" {
		Raise("ValueError", "math domain error")
	}
	Raise("ValueError", errMsg+ReprFloat(x))
}

// MathSqrt is math.sqrt(x).
func MathSqrt(x float64) float64 {
	return math1(x, math.Sqrt(x), false, "expected a nonnegative input, got ")
}

// MathExp is math.exp(x).
func MathExp(x float64) float64 { return math1(x, expCR(x), true, "") }

// MathFabs is math.fabs(x).
func MathFabs(x float64) float64 { return math.Abs(x) }

// mLog is CPython's m_log (and m_log10 with base10): its special values, then the
// correctly rounded logarithm.
func mLog(x float64, base10 bool) float64 {
	switch {
	case math.IsNaN(x):
		return x
	case math.IsInf(x, 1):
		return x
	case x == 0:
		return math.Inf(-1)
	case x < 0:
		return math.NaN()
	}
	return logCR(x, base10)
}

const logMsg = "expected a positive input, got "

// MathLog is math.log(x) of a Float (loghelper: math_1 over m_log).
func MathLog(x float64) float64 { return math1(x, mLog(x, false), false, logMsg) }

// MathLog10 is math.log10(x) of a Float.
func MathLog10(x float64) float64 { return math1(x, mLog(x, true), false, logMsg) }

// MathLogI is math.log(n) of an Int (loghelper's int path: its own message, then the
// logarithm of float(n)).
func MathLogI(n int64) float64 {
	if n <= 0 {
		Raise("ValueError", "expected a positive input")
	}
	return mLog(float64(n), false)
}

// MathLog10I is math.log10(n) of an Int.
func MathLog10I(n int64) float64 {
	if n <= 0 {
		Raise("ValueError", "expected a positive input")
	}
	return mLog(float64(n), true)
}

// MathLogBase is math.log(x, base) once both logarithms are taken: num / den.
func MathLogBase(num, den float64) float64 { return DivF(num, den) }

// MathPow is math.pow(x, y) (math_pow_impl).
func MathPow(x, y float64) float64 {
	if math.IsInf(x, 0) || math.IsInf(y, 0) || math.IsNaN(x) || math.IsNaN(y) {
		switch {
		case math.IsNaN(x):
			if y == 0 {
				return 1
			}
			return x
		case math.IsNaN(y):
			if x == 1 {
				return 1
			}
			return y
		case math.IsInf(x, 0):
			oddY := !math.IsInf(y, 0) && math.Mod(math.Abs(y), 2) == 1
			switch {
			case y > 0:
				if oddY {
					return x
				}
				return math.Abs(x)
			case y == 0:
				return 1
			}
			if oddY {
				return math.Copysign(0, x)
			}
			return 0
		}
		ax := math.Abs(x)
		switch {
		case ax == 1:
			return 1
		case y > 0 && ax > 1:
			return y
		case y < 0 && ax < 1:
			return -y
		}
		return 0
	}
	r := cPow(x, y)
	switch {
	case math.IsNaN(r):
		Raise("ValueError", "math domain error")
	case math.IsInf(r, 0):
		if x == 0 {
			Raise("ValueError", "math domain error")
		}
		Raise("OverflowError", "math range error")
	}
	return r
}

func oddInteger(y float64) bool { return math.Mod(math.Abs(y), 2) == 1 }

// cPow is C's pow(x, y) for finite x and y (C99 Annex F), correctly rounded.
func cPow(a, b float64) float64 {
	if b == 0 || a == 1 {
		return 1
	}
	if a == 0 {
		if b < 0 {
			if oddInteger(b) {
				return math.Copysign(math.Inf(1), a)
			}
			return math.Inf(1)
		}
		if oddInteger(b) {
			return a
		}
		return 0
	}
	if a < 0 && b != math.Floor(b) {
		return math.NaN()
	}
	neg := a < 0 && oddInteger(b)
	aa := math.Abs(a)
	var r float64
	switch {
	case aa == 1:
		r = 1
	case b == 1:
		r = aa
	case b == 2:
		r = float64(aa * aa)
	case b == -1:
		r = 1 / aa
	case b == 0.5:
		r = math.Sqrt(aa)
	default:
		r = powCorrect(aa, b)
	}
	if neg {
		return -r
	}
	return r
}

// MathIsClose is math.isclose(a, b) (rel_tol=1e-09, abs_tol=0.0).
func MathIsClose(a, b float64) bool {
	const relTol = 1e-09
	if a == b {
		return true
	}
	if math.IsInf(a, 0) || math.IsInf(b, 0) {
		return false
	}
	diff := math.Abs(float64(b - a))
	return diff <= math.Abs(float64(relTol*b)) || diff <= math.Abs(float64(relTol*a)) || diff <= 0
}

// MathGcd is math.gcd(a, b): never negative (R623 for 2**63).
func MathGcd(a, b int64) int64 {
	x, y := uabs(a), uabs(b)
	for y != 0 {
		x, y = y, x%y
	}
	if x > math.MaxInt64 {
		failRange(new(big.Int).SetUint64(x))
	}
	return int64(x)
}

// ---- correctly rounded exp and log -----------------------------------------------------

// expCR is exp(x) correctly rounded (inf past the float range, 0 below it).
func expCR(x float64) float64 {
	switch {
	case math.IsNaN(x):
		return x
	case math.IsInf(x, 1):
		return x
	case math.IsInf(x, -1):
		return 0
	case x == 0:
		return 1
	case x > 710:
		return math.Inf(1)
	case x < -746:
		return 0
	}
	r, _ := bigExp(new(big.Float).SetPrec(powPrec).SetFloat64(x)).Float64()
	return r
}

var bigLn10 = bigLn(new(big.Float).SetPrec(powPrec).SetInt64(10))

// logCR is ln x (or log10 x) for a finite x > 0, correctly rounded; exact at 1 (and at
// the powers of ten for log10).
func logCR(x float64, base10 bool) float64 {
	if x == 1 {
		return 0
	}
	if base10 {
		if k, ok := exactPow10(x); ok {
			return float64(k)
		}
	}
	y := bigLn(new(big.Float).SetPrec(powPrec).SetFloat64(x))
	if base10 {
		y.Quo(y, bigLn10)
	}
	r, _ := y.Float64()
	return r
}

// exactPow10 is k when x is exactly 10**k (k >= 0; 10**k < 2**64 is exact in binary
// only up to 10**22).
func exactPow10(x float64) (int, bool) {
	p := 1.0
	for k := 0; k <= 22; k++ {
		if x == p {
			return k, true
		}
		p *= 10
	}
	return 0, false
}
