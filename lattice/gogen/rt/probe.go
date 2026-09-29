package rt

// An Int looked up among Floats (R-widen, docs/lattice-language-rules.md): the lookup is
// exact, as == is -- `2**53 + 1 in [2.0**53]` is False -- and never widens the Int.

// IntAsFloat is n as a Float when that is exact (the only Float equal to n).
func IntAsFloat(n int64) (float64, bool) {
	f := float64(n)
	if f >= 0x1p63 || CmpIF(n, f) != 0 {
		return 0, false
	}
	return f, true
}

// EqIF is n == f exactly.
func EqIF(n int64, f float64) bool { return CmpIF(n, f) == 0 }

// DictGetIF is d[n] for Float keys: an exact lookup (a KeyError shows the Int); a
// defaultdict stores a missing key widened, as a store does (t2_fget).
func DictGetIF[V any](d *Dict[float64, V], n int64) V {
	f, exact := IntAsFloat(n)
	if d.kind == defaultDict && !(exact && d.Has(f)) {
		return d.GetP(float64(n), n)
	}
	if !exact {
		return d.missing(float64(n), n)
	}
	return d.GetP(f, n)
}

// DictHasIF is n in d for Float keys.
func DictHasIF[V any](d *Dict[float64, V], n int64) bool {
	f, ok := IntAsFloat(n)
	return ok && d.Has(f)
}

// DictGetOptIF is d.get(n) for Float keys.
func DictGetOptIF[V any](d *Dict[float64, V], n int64) Opt[V] {
	if f, ok := IntAsFloat(n); ok {
		return d.GetOpt(f)
	}
	return None[V]()
}

// DictGetOrIF is d.get(n, dflt) for Float keys.
func DictGetOrIF[V any](d *Dict[float64, V], n int64, dflt V) V {
	if f, ok := IntAsFloat(n); ok {
		return d.GetOr(f, dflt)
	}
	return dflt
}

// DictPopIF is d.pop(n) for Float keys.
func DictPopIF[V any](d *Dict[float64, V], n int64) V {
	f, ok := IntAsFloat(n)
	if !ok {
		Raise("KeyError", StrInt(n))
	}
	return d.Pop(f, n)
}

// DictPopOrIF is d.pop(n, dflt) for Float keys.
func DictPopOrIF[V any](d *Dict[float64, V], n int64, dflt V) V {
	if f, ok := IntAsFloat(n); ok {
		return d.PopOr(f, dflt)
	}
	return dflt
}

// SetHasIF is n in s for a set of Floats.
func SetHasIF(s *Set[float64], n int64) bool {
	f, ok := IntAsFloat(n)
	return ok && s.Has(f)
}

// SetDiscardIF is s.discard(n) for a set of Floats.
func SetDiscardIF(s *Set[float64], n int64) struct{} {
	if f, ok := IntAsFloat(n); ok {
		s.Discard(f)
	}
	return struct{}{}
}

// SetRemoveIF is s.remove(n) for a set of Floats.
func SetRemoveIF(s *Set[float64], n int64) struct{} {
	f, ok := IntAsFloat(n)
	if !ok {
		Raise("KeyError", StrInt(n))
	}
	return s.RemoveP(f, n)
}

// IterContainsBy is x in it where match compares x with each element.
func IterContainsBy[T any](it *Iter[T], match func(T) bool) bool {
	c := it.Cursor()
	for {
		y, ok := c()
		if !ok {
			return false
		}
		if match(y) {
			return true
		}
	}
}
