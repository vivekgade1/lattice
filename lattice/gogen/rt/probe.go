package rt

// A probe holding an Int where a container holds a Float (at any depth: a tuple's
// elements, an optional value) is looked up exactly, as == compares (R-widen,
// docs/lattice-language-rules.md): `2**53 + 1 in [2.0**53]` and
// `(2**53 + 1, 0) in {(2.0**53, 0.0)}` are False. The generated code converts the probe
// exactly (kk, exact: the key equal to it, when one can be) and, where a lookup stores
// the key, widened (as a store does).

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

// DictGetX is d[probe] for a probe converted exactly to kk (exact false: no key can
// equal it) and widened: a KeyError shows the probe; a Counter reads 0; a defaultdict
// stores a missing key widened (t2_fget).
func DictGetX[K comparable, V any](d *Dict[K, V], kk K, exact bool, widened K, probe any) V {
	if d.kind == defaultDict && !(exact && d.Has(kk)) {
		return d.GetP(widened, probe)
	}
	if !exact {
		return d.missing(widened, probe)
	}
	return d.GetP(kk, probe)
}

// DictKeyX is the key `d[probe] op= v` reads and writes (t2_fkey): the exact key when d
// holds it; else the widened key where the read stores or counts it (a defaultdict, a
// Counter); ok false for a plain dict, whose read then raises KeyError (DictReadX).
func DictKeyX[K comparable, V any](d *Dict[K, V], kk K, exact bool, widened K) (K, bool) {
	if exact && d.Has(kk) {
		return kk, true
	}
	return widened, d.kind != plainDict
}

// DictReadX is d[k] for a key from DictKeyX.
func DictReadX[K comparable, V any](d *Dict[K, V], k K, ok bool, probe any) V {
	if !ok {
		Raise("KeyError", Repr(probe))
	}
	return d.GetP(k, probe)
}

// DictGetOptX is d.get(probe).
func DictGetOptX[K comparable, V any](d *Dict[K, V], kk K, exact bool) Opt[V] {
	if exact {
		return d.GetOpt(kk)
	}
	return None[V]()
}

// DictGetOrX is d.get(probe, dflt).
func DictGetOrX[K comparable, V any](d *Dict[K, V], kk K, exact bool, dflt V) V {
	if exact {
		return d.GetOr(kk, dflt)
	}
	return dflt
}

// DictPopX is d.pop(probe).
func DictPopX[K comparable, V any](d *Dict[K, V], kk K, exact bool, probe any) V {
	if !exact {
		Raise("KeyError", Repr(probe))
	}
	return d.Pop(kk, probe)
}

// DictPopOrX is d.pop(probe, dflt).
func DictPopOrX[K comparable, V any](d *Dict[K, V], kk K, exact bool, dflt V) V {
	if exact {
		return d.PopOr(kk, dflt)
	}
	return dflt
}

// SetDiscardX is s.discard(probe).
func SetDiscardX[T comparable](s *Set[T], kk T, exact bool) struct{} {
	if exact {
		s.Discard(kk)
	}
	return struct{}{}
}

// SetRemoveX is s.remove(probe).
func SetRemoveX[T comparable](s *Set[T], kk T, exact bool, probe any) struct{} {
	if !exact {
		Raise("KeyError", Repr(probe))
	}
	return s.RemoveP(kk, probe)
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
