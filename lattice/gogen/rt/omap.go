package rt

// Pair is one (key, value) item of a Dict.
type Pair[K comparable, V any] struct {
	Key   K
	Value V
}

type entry[K comparable, V any] struct {
	key  K
	val  V
	live bool
}

// The kinds of Dict: a dict, a collections.Counter (a missing key reads 0; update adds
// counts), or a collections.defaultdict (a missing key reads factory(), stored).
const (
	plainDict uint8 = iota
	counterDict
	defaultDict
)

// Dict is a Python dict: insertion-ordered; setting an existing key keeps its place,
// deleting and re-inserting moves it to the end.
//
// Its entries follow CPython's dict layout (Objects/dictobject.c), so that a dict (or a
// set) changed while it is iterated stops or raises exactly where CPython's iterator
// does: entries are appended; a deleted entry stays as a hole; the table has `usable`
// free entries, and inserting into a full table resizes it -- to the smallest power of
// two >= max(8, 3 * len) -- which compacts the holes away. A new or cleared dict has
// CPython's empty keys (size 1, nothing usable); copies, merges and presized literals
// take CPython's sizes (see Copy, Merge, NewDictSized).
type Dict[K comparable, V any] struct {
	entries []entry[K, V]
	index   map[K]int
	log2    uint8 // the table size is 1 << log2 (0: CPython's empty keys)
	usable  int
	kind    uint8
	factory func() V
	fname   string // the factory's name (defaultdict's repr)
}

const dictMinLog2 = 3 // PyDict_LOG_MINSIZE

func usableFraction(size int) int { return size << 1 / 3 }

// calcLog2 is calculate_log2_keysize: the smallest table (log2) of at least minsize.
func calcLog2(minsize int) uint8 {
	minsize = max(minsize, 1<<dictMinLog2)
	var n uint8
	for m := minsize - 1; m > 0; m >>= 1 {
		n++
	}
	return n
}

// estimateLog2 is estimate_log2_keysize: a table n entries fit in without resizing.
func estimateLog2(n int) uint8 { return calcLog2((n*3 + 1) / 2) }

// NewDict is {} (CPython's empty keys).
func NewDict[K comparable, V any]() *Dict[K, V] {
	return &Dict[K, V]{index: map[K]int{}}
}

// NewDictSized is a dict literal of n plain entries (`BUILD_MAP n`: dict_new_presized).
func NewDictSized[K comparable, V any](n int) *Dict[K, V] {
	d := NewDict[K, V]()
	if n <= usableFraction(1<<dictMinLog2) {
		return d
	}
	log2 := uint8(17)
	if n <= usableFraction(1<<17) {
		log2 = estimateLog2(n)
	}
	d.log2, d.usable = log2, usableFraction(1<<log2)
	return d
}

// NewCounter is collections.Counter() (T2Counter).
func NewCounter[K comparable]() *Dict[K, int64] {
	d := NewDict[K, int64]()
	d.kind = counterDict
	return d
}

// NewDefaultDict is collections.defaultdict(factory); name is the factory's name
// (list, set, int, float or str).
func NewDefaultDict[K comparable, V any](factory func() V, name string) *Dict[K, V] {
	d := NewDict[K, V]()
	d.kind, d.factory, d.fname = defaultDict, factory, name
	return d
}

// IsCounter reports whether d is a collections.Counter.
func (d *Dict[K, V]) IsCounter() bool { return d.kind == counterDict }

// resize is dictresize: a new table of 1 << log2 holding the live entries, compacted.
func (d *Dict[K, V]) resize(log2 uint8) {
	live := make([]entry[K, V], 0, len(d.index))
	for _, e := range d.entries {
		if e.live {
			d.index[e.key] = len(live)
			live = append(live, e)
		}
	}
	d.entries, d.log2 = live, log2
	d.usable = usableFraction(1<<log2) - len(live)
}

// Len is len(d).
func (d *Dict[K, V]) Len() int64 { return int64(len(d.index)) }

// Lookup is d.get(k) with a found flag.
func (d *Dict[K, V]) Lookup(k K) (V, bool) {
	if i, ok := d.index[k]; ok {
		return d.entries[i].val, true
	}
	var zero V
	return zero, false
}

// Has is k in d.
func (d *Dict[K, V]) Has(k K) bool {
	_, ok := d.index[k]
	return ok
}

// Get is d[k]: KeyError (str(e) == repr(k)) when k is missing -- a Counter reads 0, a
// defaultdict stores and returns factory().
func (d *Dict[K, V]) Get(k K) V { return d.GetP(k, k) }

// GetP is d[k] where the key was written as probe (a KeyError shows repr(probe)).
func (d *Dict[K, V]) GetP(k K, probe any) V {
	if i, ok := d.index[k]; ok {
		return d.entries[i].val
	}
	return d.missing(k, probe)
}

func (d *Dict[K, V]) missing(k K, probe any) V {
	switch d.kind {
	case counterDict:
		var zero V
		return zero
	case defaultDict:
		v := d.factory()
		d.Set(k, v)
		return v
	}
	Raise("KeyError", Repr(probe))
	panic("unreachable")
}

// Set is d[k] = v.
func (d *Dict[K, V]) Set(k K, v V) struct{} {
	if i, ok := d.index[k]; ok {
		d.entries[i].val = v
		return struct{}{}
	}
	if d.usable <= 0 {
		d.resize(calcLog2(len(d.index) * 3)) // insertion_resize: GROWTH_RATE = used * 3
	}
	d.index[k] = len(d.entries)
	d.entries = append(d.entries, entry[K, V]{key: k, val: v, live: true})
	d.usable--
	return struct{}{}
}

// Delete removes k, reporting whether it was present (its entry stays as a hole).
func (d *Dict[K, V]) Delete(k K) bool {
	i, ok := d.index[k]
	if !ok {
		return false
	}
	delete(d.index, k)
	var zero entry[K, V]
	d.entries[i] = zero
	return true
}

// Keys is list(d).
func (d *Dict[K, V]) Keys() []K {
	out := make([]K, 0, len(d.index))
	for _, e := range d.entries {
		if e.live {
			out = append(out, e.key)
		}
	}
	return out
}

// Values is list(d.values()).
func (d *Dict[K, V]) Values() []V {
	out := make([]V, 0, len(d.index))
	for _, e := range d.entries {
		if e.live {
			out = append(out, e.val)
		}
	}
	return out
}

// Items is list(d.items()).
func (d *Dict[K, V]) Items() []Pair[K, V] {
	out := make([]Pair[K, V], 0, len(d.index))
	for _, e := range d.entries {
		if e.live {
			out = append(out, Pair[K, V]{e.key, e.val})
		}
	}
	return out
}

// clone is clone_combined_dict_keys: the same table, holes included.
func (d *Dict[K, V]) clone() *Dict[K, V] {
	c := &Dict[K, V]{
		entries: append([]entry[K, V](nil), d.entries...),
		index:   make(map[K]int, len(d.index)),
		log2:    d.log2,
		usable:  d.usable,
	}
	for k, i := range d.index {
		c.index[k] = i
	}
	return c
}

// plainCopy is PyDict_Copy (copy_lock_held): a plain dict with d's items.
func (d *Dict[K, V]) plainCopy() *Dict[K, V] {
	if len(d.index) == 0 {
		return NewDict[K, V]()
	}
	if len(d.index) >= len(d.entries)*2/3 {
		return d.clone()
	}
	c := NewDict[K, V]()
	c.Merge(d)
	return c
}

// Copy is d.copy(): a dict of the same kind. dict.copy is PyDict_Copy; Counter.copy
// is Counter(d) and defaultdict.copy defaultdict(factory, d), both a merge into an
// empty dict.
func (d *Dict[K, V]) Copy() *Dict[K, V] {
	if d.kind == plainDict {
		return d.plainCopy()
	}
	c := d.emptyLike()
	c.Merge(d)
	return c
}

// emptyLike is an empty dict of d's kind (and factory).
func (d *Dict[K, V]) emptyLike() *Dict[K, V] {
	c := NewDict[K, V]()
	c.kind, c.factory, c.fname = d.kind, d.factory, d.fname
	return c
}

// Merge is dict_merge(d, o, override=1) (dict.update, `|=`, `{**o}`): one resize up
// front, or a clone of o's table when d is empty and o is compact.
func (d *Dict[K, V]) Merge(o *Dict[K, V]) {
	if o == d || len(o.index) == 0 {
		return
	}
	if len(d.index) == 0 && len(o.index) == len(o.entries) &&
		(o.log2 == dictMinLog2 || usableFraction((1<<o.log2)/2) < len(o.index)) {
		c := o.clone()
		d.entries, d.index, d.log2, d.usable = c.entries, c.index, c.log2, c.usable
		return
	}
	if usableFraction(1<<d.log2) < len(o.index) {
		d.resize(estimateLog2(len(d.index) + len(o.index)))
	}
	for _, e := range o.entries {
		if e.live {
			d.Set(e.key, e.val)
		}
	}
}

// fromKeysOf is dict.fromkeys(o) for an exact dict o (T2Set.copy): one resize to fit
// o, then its keys inserted.
func fromKeysOf[K comparable, V, W any](o *Dict[K, W]) *Dict[K, V] {
	d := NewDict[K, V]()
	d.resize(estimateLog2(len(o.index)))
	var zero V
	for _, e := range o.entries {
		if e.live {
			d.Set(e.key, zero)
		}
	}
	return d
}

// Clear is d.clear() (CPython's empty keys again).
func (d *Dict[K, V]) Clear() struct{} {
	d.entries, d.index, d.log2, d.usable = nil, map[K]int{}, 0, 0
	return struct{}{}
}
