package rt

import "sort"

// The dict, Counter and defaultdict methods of T2 procedures (plan 5a Task 10), with
// Python 3.14's results, errors and orders.

// GetOpt is d.get(k): None when k is missing (a Counter or defaultdict too).
func (d *Dict[K, V]) GetOpt(k K) Opt[V] {
	if v, ok := d.Lookup(k); ok {
		return Some(v)
	}
	return None[V]()
}

// GetOr is d.get(k, dflt).
func (d *Dict[K, V]) GetOr(k K, dflt V) V {
	if v, ok := d.Lookup(k); ok {
		return v
	}
	return dflt
}

// Pop is d.pop(k): KeyError (repr of the key as written, probe) when missing.
func (d *Dict[K, V]) Pop(k K, probe any) V {
	v, ok := d.Lookup(k)
	if !ok {
		Raise("KeyError", Repr(probe))
	}
	d.Delete(k)
	return v
}

// PopOr is d.pop(k, dflt).
func (d *Dict[K, V]) PopOr(k K, dflt V) V {
	v, ok := d.Lookup(k)
	if !ok {
		return dflt
	}
	d.Delete(k)
	return v
}

// SetDefault is d.setdefault(k, v).
func (d *Dict[K, V]) SetDefault(k K, v V) V {
	if old, ok := d.Lookup(k); ok {
		return old
	}
	d.Set(k, v)
	return v
}

// Update is d.update(o). A non-empty Counter adds o's counts to its own (T2Counter:
// an Int outside the 64-bit range is R623); anything else is dict.update.
func (d *Dict[K, V]) Update(o *Dict[K, V]) struct{} {
	if d.kind == counterDict && len(d.index) > 0 {
		for _, p := range o.Items() {
			cur, _ := d.Lookup(p.Key)
			sum := Add(any(p.Value).(int64), any(cur).(int64))
			d.Set(p.Key, any(sum).(V))
		}
		return struct{}{}
	}
	d.Merge(o)
	return struct{}{}
}

// CounterOf is collections.Counter(it): each element counted, in first-seen order.
func CounterOf[K comparable](c Cursor[K]) *Dict[K, int64] {
	d := NewCounter[K]()
	for {
		k, ok := c()
		if !ok {
			return d
		}
		n, _ := d.Lookup(k)
		d.Set(k, n+1)
	}
}

// CounterOfDict is collections.Counter(m) of a mapping: its counts (dict.update).
func CounterOfDict[K comparable](m *Dict[K, int64]) *Dict[K, int64] {
	d := NewCounter[K]()
	d.Merge(m)
	return d
}

// MostCommon is c.most_common(n) (n nil: all): by count, largest first; equal counts
// keep insertion order (T2Counter).
func MostCommon[K comparable, P any](c *Dict[K, int64], n *int64, mk func(K, int64) P) *List[P] {
	items := c.Items()
	sort.SliceStable(items, func(i, j int) bool { return items[i].Value > items[j].Value })
	if n != nil {
		items = items[:min(max(*n, 0), int64(len(items)))]
	}
	out := make([]P, len(items))
	for i, p := range items {
		out[i] = mk(p.Key, p.Value)
	}
	return &List[P]{Items: out}
}

// counts is a Counter's count of k (0 when missing).
func counts[K comparable, V any](d *Dict[K, V], k K) int64 {
	v, ok := d.Lookup(k)
	if !ok {
		return 0
	}
	return any(v).(int64)
}

// DictEq is a == b for dicts (order does not matter), with the values' equality. Two
// Counters compare as Counters: a missing key counts 0.
func DictEq[K comparable, V any](a, b *Dict[K, V], eq func(x, y V) bool) bool {
	if a.kind == counterDict && b.kind == counterDict {
		for _, d := range [2]*Dict[K, V]{a, b} {
			for _, k := range d.Keys() {
				if counts(a, k) != counts(b, k) {
					return false
				}
			}
		}
		return true
	}
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

// DictUnion is a | b: a new dict. Two Counters give Counter's union (each key's larger
// count, kept when positive); a defaultdict operand gives a defaultdict (the left one's
// factory when both are); otherwise a plain dict: a copy of a, updated with b.
func DictUnion[K comparable, V any](a, b *Dict[K, V]) *Dict[K, V] {
	switch {
	case a.kind == counterDict && b.kind == counterDict:
		out := a.emptyLike()
		for _, p := range a.Items() {
			n := max(any(p.Value).(int64), counts(b, p.Key))
			if n > 0 {
				out.Set(p.Key, any(n).(V))
			}
		}
		for _, p := range b.Items() {
			if !a.Has(p.Key) && any(p.Value).(int64) > 0 {
				out.Set(p.Key, p.Value)
			}
		}
		return out
	case a.kind == defaultDict || b.kind == defaultDict:
		like := a
		if a.kind != defaultDict {
			like = b
		}
		out := like.emptyLike()
		out.Merge(a)
		out.Merge(b)
		return out
	}
	out := a.plainCopy()
	out.Merge(b)
	return out
}

// DictIOr is a |= b: a Counter keeps each key's larger count, then drops the counts
// that are not positive (Counter.__ior__); any other dict is updated with b.
func DictIOr[K comparable, V any](a, b *Dict[K, V]) {
	if a.kind != counterDict {
		a.Merge(b)
		return
	}
	for _, p := range b.Items() {
		if any(p.Value).(int64) > counts(a, p.Key) {
			a.Set(p.Key, p.Value)
		}
	}
	for _, p := range a.Items() {
		if any(p.Value).(int64) <= 0 {
			a.Delete(p.Key)
		}
	}
}

// ---- views: d.keys(), d.values(), d.items() -------------------------------------------

// KeysView is d.keys(): a live view, sized (len(d) when consumed whole) and
// re-iterable; `in` looks the key up.
func KeysView[K comparable, V any](d *Dict[K, V]) *Iter[K] {
	return &Iter[K]{
		fresh:  func() Cursor[K] { return IterDict(d) },
		sizeFn: d.Len,
		has:    d.Has,
	}
}

// ValuesView is d.values().
func ValuesView[K comparable, V any](d *Dict[K, V]) *Iter[V] {
	return &Iter[V]{
		fresh:  func() Cursor[V] { return MapCursor(iterEntries(d), func(e *entry[K, V]) V { return e.val }) },
		sizeFn: d.Len,
	}
}

// ItemsView is d.items(); mk builds the (key, value) tuple.
func ItemsView[K comparable, V, P any](d *Dict[K, V], mk func(K, V) P) *Iter[P] {
	return &Iter[P]{
		fresh: func() Cursor[P] {
			return MapCursor(iterEntries(d), func(e *entry[K, V]) P { return mk(e.key, e.val) })
		},
		sizeFn: d.Len,
	}
}

// iterEntries is IterDict over the live entries (the same RuntimeErrors).
func iterEntries[K comparable, V any](d *Dict[K, V]) Cursor[*entry[K, V]] {
	keys := IterDict(d)
	return func() (*entry[K, V], bool) {
		k, ok := keys()
		if !ok {
			return nil, false
		}
		return &d.entries[d.index[k]], true
	}
}
