package rt

import "strconv"

// The constants of lattice/t2/support.py.
const (
	// T2MaxDepth: a procedure call deeper than this is R620.
	T2MaxDepth = 200
	// T2DefaultMaxSize: the default largest value a procedure may build
	// (ApiProfile.max_value_size): a str's characters, a list's elements.
	T2DefaultMaxSize = 10_000_000
	// T2SizeUnit: a growing operation costs one unit of fuel per this many characters /
	// elements of its result.
	T2SizeUnit = 16
	// T2MaxIntBits: the largest Int a procedure may build, in bits (R622). With 64-bit
	// Ints it only bounds the sizing of `**` and `<<` before they are computed.
	T2MaxIntBits = 1 << 16
)

// Fuel is lattice.t2.support.T2Fuel: one request's budget of steps (Left), the current
// procedure call depth, and the largest value a procedure may build. The methods charge
// exactly what T2Fuel's do and fail with the same messages.
type Fuel struct {
	Budget, Left, Depth, MaxDepth int64
	MaxSize                       int64
}

// NewFuel is T2Fuel(budget) with the default depth and size limits.
func NewFuel(budget int64) *Fuel {
	return &Fuel{Budget: budget, Left: budget, MaxDepth: T2MaxDepth, MaxSize: T2DefaultMaxSize}
}

// spent is T2Fuel._spent: Left becomes -1 and R620 is raised.
func (f *Fuel) spent() {
	f.Left = -1
	Fail("R620", "procedure ran out of fuel: the request's budget of "+strconv.FormatInt(f.Budget, 10)+
		" steps is spent (every loop iteration, call and comprehension element costs one)")
}

func (f *Fuel) charge(n int64) {
	f.Left -= n
	if f.Left < 0 {
		f.spent()
	}
}

// Tick is one step: a loop iteration or a call (T2Fuel.tick / T2Fuel.call).
func (f *Fuel) Tick() { f.charge(1) }

// Enter is a procedure call: one step, one level deeper (T2Fuel.enter).
func (f *Fuel) Enter() {
	f.charge(1)
	f.Depth++
	if f.Depth > f.MaxDepth {
		Fail("R620", "procedure calls nest deeper than "+strconv.FormatInt(f.MaxDepth, 10)+
			" (recursion is bounded)")
	}
}

// Leave ends a procedure call (T2Fuel.leave).
func (f *Fuel) Leave() { f.Depth-- }

// Each charges one comprehension element (T2Fuel.each). Generated code calls it once
// per element, as the element is produced and before that element's body runs, so
// earlier elements' bodies (their effects and catchable errors) happen before a later
// element runs out of fuel. Never charge a comprehension's elements up front; Bulk is
// only for a sized argument a builtin consumes whole.
func (f *Fuel) Each() { f.charge(1) }

// Bulk charges a sized argument a builtin consumes whole: its length, up front
// (T2Fuel.bulk).
func (f *Fuel) Bulk(n int) { f.charge(int64(n)) }

// Grow is T2Fuel.grow: a value of size characters / elements is about to be built;
// refused past MaxSize (R622), else charged size // T2SizeUnit.
func (f *Fuel) Grow(size int64) {
	if size > f.MaxSize {
		Fail("R622", "a procedure would build a value of "+strconv.FormatInt(size, 10)+
			" characters or elements; the largest allowed is "+strconv.FormatInt(f.MaxSize, 10)+
			" (ApiProfile.max_value_size)")
	}
	f.charge(floorDivInt(size, T2SizeUnit))
}

// Bits is T2Fuel.bits: an Int of about n bits is about to be computed; refused past
// T2MaxIntBits (R622), else charged n // (8 * T2SizeUnit).
func (f *Fuel) Bits(n int64) {
	if n > T2MaxIntBits {
		failBits(n)
	}
	f.charge(floorDivInt(n, 8*T2SizeUnit))
}

// floorDivInt is Python's // for a positive divisor.
func floorDivInt(a, b int64) int64 {
	q := a / b
	if a%b != 0 && a < 0 {
		q--
	}
	return q
}
