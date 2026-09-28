package rt

import (
	"slices"
	"sort"
)

// List is a Python list. It is always handled by pointer: assignment shares it, as in
// Python; a slice copies.
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
func (l *List[T]) Append(v T) { l.Items = append(l.Items, v) }

// Extend is l.extend(xs).
func (l *List[T]) Extend(xs []T) { l.Items = append(l.Items, xs...) }

// Insert is l.insert(i, v): i is clamped to the list, as in Python.
func (l *List[T]) Insert(i int64, v T) {
	n := int64(len(l.Items))
	if i < 0 {
		i = max(i+n, 0)
	}
	i = min(i, n)
	l.Items = slices.Insert(l.Items, int(i), v)
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
func (l *List[T]) Remove(v T, eq func(a, b T) bool) {
	for k, x := range l.Items {
		if eq(x, v) {
			l.Items = slices.Delete(l.Items, k, k+1)
			return
		}
	}
	Raise("ValueError", "list.remove(x): x not in list")
}

// Clear is l.clear().
func (l *List[T]) Clear() {
	clear(l.Items)
	l.Items = l.Items[:0]
}

// Reverse is l.reverse().
func (l *List[T]) Reverse() { slices.Reverse(l.Items) }

// SortStable is l.sort() with a key order: stable, as Python's sort is.
func (l *List[T]) SortStable(less func(a, b T) bool) {
	sort.SliceStable(l.Items, func(i, j int) bool { return less(l.Items[i], l.Items[j]) })
}
