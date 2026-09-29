package rt

import (
	"math/big"
	"sort"
)

// Iteration as compiled T2 procedures see it. A Cursor is a Python iterator: each call
// yields the next element (ok false once exhausted). Cursors over containers follow
// CPython's iterators: a list iterator is live (it sees elements appended while it
// runs) and stays exhausted once done; a dict (or set) iterator raises RuntimeError
// when the dict changes size while it runs.
type Cursor[T any] func() (T, bool)

// Iter is a Lattice Iter[T]: range(...) (re-iterable and sized: each iteration starts
// again) or a one-shot iterator (enumerate, zip, reversed, a generator expression).
type Iter[T any] struct {
	cur    Cursor[T]
	fresh  func() Cursor[T]
	size   int64
	sizeFn func() int64 // a dict view's live length
	has    func(T) bool // a keys view's lookup
	rng    *rangeT
}

type rangeT struct{ start, stop, step, n int64 }

// NewIter is a one-shot iterator.
func NewIter[T any](c Cursor[T]) *Iter[T] { return &Iter[T]{cur: c} }

// Cursor starts iterating it: a range starts again, a one-shot iterator continues.
func (it *Iter[T]) Cursor() Cursor[T] {
	if it.fresh != nil {
		return it.fresh()
	}
	return it.cur
}

// Sized is len(it) for a range; ok is false for a one-shot iterator.
func (it *Iter[T]) Sized() (int64, bool) {
	if it.sizeFn != nil {
		return it.sizeFn(), true
	}
	return it.size, it.fresh != nil
}

// Contains is x in it: arithmetic for a range, else the iterator is consumed up to the
// first equal element.
func (it *Iter[T]) Contains(x T, eq func(a, b T) bool) bool {
	if r := it.rng; r != nil {
		v := any(x).(int64)
		if r.step > 0 {
			if v < r.start || v >= r.stop {
				return false
			}
		} else if v > r.start || v <= r.stop {
			return false
		}
		d := new(big.Int).Sub(bigOf(v), bigOf(r.start))
		return d.Rem(d, bigOf(r.step)).Sign() == 0
	}
	if it.has != nil {
		return it.has(x)
	}
	c := it.Cursor()
	for {
		y, ok := c()
		if !ok {
			return false
		}
		if eq(y, x) {
			return true
		}
	}
}

// Range is range(start, stop, step).
func Range(start, stop, step int64) *Iter[int64] {
	if step == 0 {
		Raise("ValueError", "range() arg 3 must not be zero")
	}
	// the length, computed exactly (it may pass 2**63 only for a range whose len()
	// Python cannot take either)
	var n int64
	lo, hi, st := bigOf(start), bigOf(stop), bigOf(step)
	if step > 0 && start < stop {
		d := new(big.Int).Sub(hi, lo)
		d.Sub(d, big.NewInt(1)).Quo(d, st).Add(d, big.NewInt(1))
		n = clampInt64(d)
	} else if step < 0 && start > stop {
		d := new(big.Int).Sub(lo, hi)
		d.Sub(d, big.NewInt(1)).Quo(d, new(big.Int).Neg(st)).Add(d, big.NewInt(1))
		n = clampInt64(d)
	}
	r := &rangeT{start, stop, step, n}
	it := &Iter[int64]{size: n, rng: r}
	it.fresh = func() Cursor[int64] {
		var i int64
		return func() (int64, bool) {
			if i >= r.n {
				return 0, false
			}
			v := r.start + i*r.step // wraps only past the last element
			i++
			return v, true
		}
	}
	return it
}

func clampInt64(d *big.Int) int64 {
	if d.IsInt64() {
		return d.Int64()
	}
	return int64(^uint64(0) >> 1)
}

// IterSlice iterates a fixed slice (a tuple's elements, a sorted copy).
func IterSlice[T any](xs []T) Cursor[T] {
	i := 0
	return func() (T, bool) {
		if i >= len(xs) {
			var z T
			return z, false
		}
		v := xs[i]
		i++
		return v, true
	}
}

// IterList is iter(l): live by index, exhausted for good once past the end.
func IterList[T any](l *List[T]) Cursor[T] {
	i, done := 0, false
	return func() (T, bool) {
		if done || i >= len(l.Items) {
			done = true
			var z T
			return z, false
		}
		v := l.Items[i]
		i++
		return v, true
	}
}

// IterStr is iter(s): its code points, each a str.
func IterStr(s string) Cursor[string] { return IterSlice(Runes(s)) }

// IterDict is iter(d): its keys in insertion order. Like CPython's dict iterator it
// raises RuntimeError when d changes size between two steps, and when keys were
// replaced (as many removed as added) so that more keys would be produced than d had.
func IterDict[K comparable, V any](d *Dict[K, V]) Cursor[K] {
	used := len(d.index)
	remaining, pos, done := used, 0, false
	return func() (K, bool) {
		var z K
		if done {
			return z, false
		}
		if len(d.index) != used {
			Raise("RuntimeError", "dictionary changed size during iteration")
		}
		for pos < len(d.entries) && !d.entries[pos].live {
			pos++
		}
		if pos >= len(d.entries) {
			done = true
			return z, false
		}
		if remaining == 0 {
			done = true
			Raise("RuntimeError", "dictionary keys changed during iteration")
		}
		k := d.entries[pos].key
		pos++
		remaining--
		return k, true
	}
}

// IterSet is iter(s) (a T2Set iterates its dict: the same RuntimeError on a change of
// size).
func IterSet[T comparable](s *Set[T]) Cursor[T] { return IterDict(s.d) }

// Reversed is reversed(l): from the last element down, live like CPython's
// list_reverseiterator (an index past the current end stops it).
func Reversed[T any](l *List[T]) *Iter[T] {
	i := len(l.Items) - 1
	return NewIter(func() (T, bool) {
		if i >= 0 && i < len(l.Items) {
			v := l.Items[i]
			i--
			return v, true
		}
		i = -1
		var z T
		return z, false
	})
}

// Enumerate is enumerate(c, start); mk builds the (index, element) tuple.
func Enumerate[T, P any](c Cursor[T], start int64, mk func(int64, T) P) *Iter[P] {
	i := start
	return NewIter(func() (P, bool) {
		v, ok := c()
		if !ok {
			var z P
			return z, false
		}
		p := mk(i, v)
		i = Add(i, 1)
		return p, true
	})
}

// Zip2 is zip(a, b): the first iterator is advanced before the second is asked.
func Zip2[A, B, P any](a Cursor[A], b Cursor[B], mk func(A, B) P) *Iter[P] {
	return NewIter(func() (P, bool) {
		var z P
		x, ok := a()
		if !ok {
			return z, false
		}
		y, ok := b()
		if !ok {
			return z, false
		}
		return mk(x, y), true
	})
}

// Zip3 is zip(a, b, c).
func Zip3[A, B, C, P any](a Cursor[A], b Cursor[B], c Cursor[C], mk func(A, B, C) P) *Iter[P] {
	return NewIter(func() (P, bool) {
		var z P
		x, ok := a()
		if !ok {
			return z, false
		}
		y, ok := b()
		if !ok {
			return z, false
		}
		w, ok := c()
		if !ok {
			return z, false
		}
		return mk(x, y, w), true
	})
}

// Each charges one step per element as it is produced (T2Fuel.each).
func Each[T any](f *Fuel, c Cursor[T]) Cursor[T] {
	return func() (T, bool) {
		v, ok := c()
		if ok {
			f.charge(1)
		}
		return v, ok
	}
}

// BulkIter is T2Fuel.bulk for an Iter: a range is charged its length up front and
// passed unchanged; a one-shot iterator is charged per element as it is consumed.
func BulkIter[T any](f *Fuel, it *Iter[T]) *Iter[T] {
	if n, ok := it.Sized(); ok {
		f.charge(n)
		return it
	}
	return NewIter(Each(f, it.cur))
}

// Bulked charges n (a sized argument's length) and returns v unchanged.
func Bulked[T any](f *Fuel, n int64, v T) T {
	f.charge(n)
	return v
}

// ---- generator expressions ------------------------------------------------------------

// FlatMap is one `for` clause of a generator expression: for each element of c, the
// cursor f builds (the clause's filters and inner clauses), lazily.
func FlatMap[A, B any](c Cursor[A], f func(A) Cursor[B]) Cursor[B] {
	var inner Cursor[B]
	return func() (B, bool) {
		for {
			if inner != nil {
				if v, ok := inner(); ok {
					return v, true
				}
				inner = nil
			}
			a, ok := c()
			if !ok {
				var z B
				return z, false
			}
			inner = f(a)
		}
	}
}

// One is a cursor of the single element v() (computed when it is asked for).
func One[T any](v func() T) Cursor[T] {
	done := false
	return func() (T, bool) {
		if done {
			var z T
			return z, false
		}
		done = true
		return v(), true
	}
}

// Empty is a cursor of no elements.
func Empty[T any]() Cursor[T] {
	return func() (T, bool) {
		var z T
		return z, false
	}
}

// Gen is a generator expression's value: a one-shot iterator.
func Gen[T any](c Cursor[T]) *Iter[T] { return NewIter(c) }

// ---- consumers --------------------------------------------------------------------------

// Collect is list(c).
func Collect[T any](c Cursor[T]) *List[T] {
	out := []T{}
	for {
		v, ok := c()
		if !ok {
			return &List[T]{Items: out}
		}
		out = append(out, v)
	}
}

// CollectSet is set(c).
func CollectSet[T comparable](c Cursor[T]) *Set[T] {
	s := NewSet[T]()
	for {
		v, ok := c()
		if !ok {
			return s
		}
		s.Add(v)
	}
}

// CollectDict is dict(c) of (key, value) pairs.
func CollectDict[P any, K comparable, V any](c Cursor[P], kv func(P) (K, V)) *Dict[K, V] {
	d := NewDict[K, V]()
	for {
		p, ok := c()
		if !ok {
			return d
		}
		k, v := kv(p)
		d.Set(k, v)
	}
}

// Any is any(c).
func Any(c Cursor[bool]) bool {
	for {
		v, ok := c()
		if !ok {
			return false
		}
		if v {
			return true
		}
	}
}

// All is all(c).
func All(c Cursor[bool]) bool {
	for {
		v, ok := c()
		if !ok {
			return true
		}
		if !v {
			return false
		}
	}
}

// MinMax is min(c) / max(c) (CPython's min_max): the first element, replaced by each
// later one that better(later, best) holds for -- `<` for min, `>` for max. name is
// "min" or "max" (the ValueError of an empty iterable).
func MinMax[T any](c Cursor[T], better func(a, b T) bool, name string) T {
	best, ok := c()
	if !ok {
		Raise("ValueError", name+"() iterable argument is empty")
	}
	for {
		v, ok := c()
		if !ok {
			return best
		}
		if better(v, best) {
			best = v
		}
	}
}

// MinMaxKey is min(c, key=k) / max(c, key=k).
func MinMaxKey[T, K any](c Cursor[T], key func(T) K, better func(a, b K) bool, name string) T {
	best, ok := c()
	if !ok {
		Raise("ValueError", name+"() iterable argument is empty")
	}
	bestKey := key(best)
	for {
		v, ok := c()
		if !ok {
			return best
		}
		k := key(v)
		if better(k, bestKey) {
			best, bestKey = v, k
		}
	}
}

// Sorted is sorted(c, reverse=r): a stable sort by `<` (a reverse sort reverses, sorts
// stably and reverses back, as CPython's list.sort).
func Sorted[T any](c Cursor[T], less func(a, b T) bool, reverse bool) *List[T] {
	l := Collect(c)
	sortStable(l.Items, less, reverse)
	return l
}

func sortStable[T any](xs []T, less func(a, b T) bool, reverse bool) {
	if reverse {
		reverseSlice(xs)
	}
	sort.SliceStable(xs, func(i, j int) bool { return less(xs[i], xs[j]) })
	if reverse {
		reverseSlice(xs)
	}
}

func reverseSlice[T any](xs []T) {
	for i, j := 0, len(xs)-1; i < j; i, j = i+1, j-1 {
		xs[i], xs[j] = xs[j], xs[i]
	}
}

// SortedKey is sorted(c, key=k, reverse=r): every key is computed first, in order.
func SortedKey[T, K any](c Cursor[T], key func(T) K, less func(a, b K) bool, reverse bool) *List[T] {
	l := Collect(c)
	type kv struct {
		k K
		v T
	}
	pairs := make([]kv, len(l.Items))
	for i, v := range l.Items {
		pairs[i] = kv{key(v), v}
	}
	sortStable(pairs, func(a, b kv) bool { return less(a.k, b.k) }, reverse)
	for i := range pairs {
		l.Items[i] = pairs[i].v
	}
	return l
}
