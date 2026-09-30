package rt

import (
	"slices"
	"sort"
)

// List is a Python list. It is always handled by pointer: assignment shares it, as in
// Python; a slice copies. The methods returning struct{} are Python's None methods (a
// value, so a call is an expression).
type List[T any] struct{ Items []T }

// NewList is [xs...] (a fresh list; xs is copied).
func NewList[T any](xs ...T) *List[T] {
	return &List[T]{Items: append(make([]T, 0, len(xs)), xs...)}
}

// Len is len(l).
func (l *List[T]) Len() int64 { return int64(len(l.Items)) }

func (l *List[T]) index(i int64, msg string) int64 {
	n := int64(len(l.Items))
	if i < 0 {
		i += n
	}
	if i < 0 || i >= n {
		Raise("IndexError", msg)
	}
	return i
}

// Get is l[i].
func (l *List[T]) Get(i int64) T { return l.Items[l.index(i, "list index out of range")] }

// Set is l[i] = v.
func (l *List[T]) Set(i int64, v T) { l.Items[l.index(i, "list assignment index out of range")] = v }

// Slice is l[lo:hi:step]: a new list.
func (l *List[T]) Slice(lo, hi, step *int64) *List[T] {
	return &List[T]{Items: sliceOf(l.Items, lo, hi, step)}
}

// Append is l.append(v).
func (l *List[T]) Append(v T) struct{} {
	l.Items = append(l.Items, v)
	return struct{}{}
}

// Extend is l.extend(xs) for a list, tuple or set xs: its elements as they are now
// (CPython copies a list first, so l.extend(l) doubles l).
func (l *List[T]) Extend(xs []T) struct{} {
	l.Items = append(l.Items, slices.Clone(xs)...)
	return struct{}{}
}

// ExtendCur is l.extend(it) for any other iterable: each element appended as it is
// produced (an iterator over l itself sees what was appended).
func (l *List[T]) ExtendCur(c Cursor[T]) struct{} {
	for {
		v, ok := c()
		if !ok {
			return struct{}{}
		}
		l.Items = append(l.Items, v)
	}
}

// Insert is l.insert(i, v): i is clamped to the list, as in Python.
func (l *List[T]) Insert(i int64, v T) struct{} {
	n := int64(len(l.Items))
	if i < 0 {
		i = max(i+n, 0)
	}
	i = min(i, n)
	l.Items = slices.Insert(l.Items, int(i), v)
	return struct{}{}
}

// Pop is l.pop() (i nil) or l.pop(i).
func (l *List[T]) Pop(i *int64) T {
	if len(l.Items) == 0 {
		Raise("IndexError", "pop from empty list")
	}
	k := int64(len(l.Items)) - 1
	if i != nil {
		k = l.index(*i, "pop index out of range")
	}
	v := l.Items[k]
	var zero T
	copy(l.Items[k:], l.Items[k+1:])
	l.Items[len(l.Items)-1] = zero
	l.Items = l.Items[:len(l.Items)-1]
	return v
}

// Remove is l.remove(v): the first element equal to v.
func (l *List[T]) Remove(v T, eq func(a, b T) bool) struct{} {
	for k := 0; k < len(l.Items); k++ {
		if eq(l.Items[k], v) {
			l.Items = slices.Delete(l.Items, k, k+1)
			return struct{}{}
		}
	}
	Raise("ValueError", "list.remove(x): x not in list")
	return struct{}{}
}

// RemoveBy is l.remove(v) with a probe of another type (an Int in a list of Floats).
func (l *List[T]) RemoveBy(match func(T) bool) struct{} {
	for k := 0; k < len(l.Items); k++ {
		if match(l.Items[k]) {
			l.Items = slices.Delete(l.Items, k, k+1)
			return struct{}{}
		}
	}
	Raise("ValueError", "list.remove(x): x not in list")
	return struct{}{}
}

// IndexBy is l.index(v): the first position matching.
func (l *List[T]) IndexBy(match func(T) bool) int64 {
	for k := 0; k < len(l.Items); k++ {
		if match(l.Items[k]) {
			return int64(k)
		}
	}
	Raise("ValueError", "list.index(x): x not in list")
	return 0
}

// CountBy is l.count(v).
func (l *List[T]) CountBy(match func(T) bool) int64 {
	var n int64
	for k := 0; k < len(l.Items); k++ {
		if match(l.Items[k]) {
			n++
		}
	}
	return n
}

// Clear is l.clear().
func (l *List[T]) Clear() struct{} {
	clear(l.Items)
	l.Items = l.Items[:0]
	return struct{}{}
}

// Reverse is l.reverse().
func (l *List[T]) Reverse() struct{} {
	slices.Reverse(l.Items)
	return struct{}{}
}

// Copy is l.copy().
func (l *List[T]) Copy() *List[T] { return &List[T]{Items: slices.Clone(l.Items)} }

// SortStable is l.sort() with a key order: stable, as Python's sort is.
func (l *List[T]) SortStable(less func(a, b T) bool) {
	sort.SliceStable(l.Items, func(i, j int) bool { return less(l.Items[i], l.Items[j]) })
}

// Sort is l.sort(reverse=r).
func (l *List[T]) Sort(less func(a, b T) bool, reverse bool) struct{} {
	sortStable(l.Items, less, reverse) // comparing T2 values runs no code: nothing is added
	return struct{}{}
}

// SortKey is l.sort(key=k, reverse=r): every key is computed first, in order, while
// the list is empty (CPython empties it before calling the key).
func SortKey[T, K any](l *List[T], key func(T) K, less func(a, b K) bool, reverse bool) struct{} {
	items := l.Items
	l.Items = nil
	type kv struct {
		k K
		v T
	}
	pairs := make([]kv, len(items))
	func() {
		defer func() {
			if r := recover(); r != nil {
				l.Items = items
				panic(r)
			}
		}()
		for i, v := range items {
			pairs[i] = kv{key(v), v}
		}
	}()
	sortStable(pairs, func(a, b kv) bool { return less(a.k, b.k) }, reverse)
	for i := range pairs {
		items[i] = pairs[i].v
	}
	modified := len(l.Items) != 0
	l.Items = items
	if modified {
		Raise("ValueError", "list modified during sort")
	}
	return struct{}{}
}
