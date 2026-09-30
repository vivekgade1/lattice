package rt

// Opt is a Lattice `T | None`: V is meaningful only when Ok.
type Opt[T any] struct {
	V  T
	Ok bool
}

// Some is a present value.
func Some[T any](v T) Opt[T] { return Opt[T]{V: v, Ok: true} }

// None is the absent value.
func None[T any]() Opt[T] { return Opt[T]{} }
