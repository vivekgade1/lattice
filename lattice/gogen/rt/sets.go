package rt

// Set is a Lattice v1.1 set (lattice.t2.support.T2Set): iteration order is insertion
// order. Results of the set algebra list the left operand's elements first, in its
// order, then (union, symmetric difference) the right operand's new ones in its order.
type Set[T comparable] struct{ d *Dict[T, struct{}] }

// NewSet is set(xs) (duplicates keep their first position).
func NewSet[T comparable](xs ...T) *Set[T] {
	s := &Set[T]{d: NewDict[T, struct{}]()}
	s.Update(xs...)
	return s
}

// Len is len(s).
func (s *Set[T]) Len() int64 { return s.d.Len() }

// Has is x in s.
func (s *Set[T]) Has(x T) bool { return s.d.Has(x) }

// Items is list(s).
func (s *Set[T]) Items() []T { return s.d.Keys() }

// Add is s.add(x) (an element already present keeps its position).
func (s *Set[T]) Add(x T) { s.d.Set(x, struct{}{}) }

// Update is s.update(xs).
func (s *Set[T]) Update(xs ...T) {
	for _, x := range xs {
		s.d.Set(x, struct{}{})
	}
}

// Discard is s.discard(x).
func (s *Set[T]) Discard(x T) { s.d.Delete(x) }

// Remove is s.remove(x): KeyError (str(e) == repr(x)) when x is missing.
func (s *Set[T]) Remove(x T) {
	if !s.d.Delete(x) {
		Raise("KeyError", Repr(x))
	}
}

// Clear is s.clear().
func (s *Set[T]) Clear() { s.d.Clear() }

// Copy is s.copy().
func (s *Set[T]) Copy() *Set[T] { return &Set[T]{d: s.d.Copy()} }

// Union is s | o.
func (s *Set[T]) Union(o *Set[T]) *Set[T] {
	r := s.Copy()
	r.Update(o.Items()...)
	return r
}

// Intersection is s & o.
func (s *Set[T]) Intersection(o *Set[T]) *Set[T] {
	r := NewSet[T]()
	for _, x := range s.Items() {
		if o.Has(x) {
			r.Add(x)
		}
	}
	return r
}

// Difference is s - o.
func (s *Set[T]) Difference(o *Set[T]) *Set[T] {
	r := NewSet[T]()
	for _, x := range s.Items() {
		if !o.Has(x) {
			r.Add(x)
		}
	}
	return r
}

// SymmetricDifference is s ^ o.
func (s *Set[T]) SymmetricDifference(o *Set[T]) *Set[T] {
	r := s.Difference(o)
	for _, x := range o.Items() {
		if !s.Has(x) {
			r.Add(x)
		}
	}
	return r
}

// IsSubset is s <= o.
func (s *Set[T]) IsSubset(o *Set[T]) bool {
	if s.Len() > o.Len() {
		return false
	}
	for _, x := range s.Items() {
		if !o.Has(x) {
			return false
		}
	}
	return true
}

// IsSuperset is s >= o.
func (s *Set[T]) IsSuperset(o *Set[T]) bool { return o.IsSubset(s) }

// Equal is s == o (order does not matter).
func (s *Set[T]) Equal(o *Set[T]) bool { return s.Len() == o.Len() && s.IsSubset(o) }
