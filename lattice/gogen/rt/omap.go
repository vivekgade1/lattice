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

// Dict is a Python dict: insertion-ordered; setting an existing key keeps its place,
// deleting and re-inserting moves it to the end. Entries live in a slice (deleted ones
// as tombstones, compacted once they are half of it) indexed by a map.
type Dict[K comparable, V any] struct {
	entries []entry[K, V]
	index   map[K]int
	dead    int
}

// NewDict is {}.
func NewDict[K comparable, V any]() *Dict[K, V] {
	return &Dict[K, V]{index: map[K]int{}}
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

// Get is d[k]: KeyError (str(e) == repr(k)) when k is missing.
func (d *Dict[K, V]) Get(k K) V {
	i, ok := d.index[k]
	if !ok {
		Raise("KeyError", Repr(k))
	}
	return d.entries[i].val
}

// Set is d[k] = v.
func (d *Dict[K, V]) Set(k K, v V) {
	if i, ok := d.index[k]; ok {
		d.entries[i].val = v
		return
	}
	d.index[k] = len(d.entries)
	d.entries = append(d.entries, entry[K, V]{key: k, val: v, live: true})
}

// Delete removes k, reporting whether it was present (del d[k] raises KeyError on
// false; that is the caller's choice of message).
func (d *Dict[K, V]) Delete(k K) bool {
	i, ok := d.index[k]
	if !ok {
		return false
	}
	delete(d.index, k)
	var zero entry[K, V]
	d.entries[i] = zero
	d.dead++
	if d.dead > 8 && d.dead*2 > len(d.entries) {
		d.compact()
	}
	return true
}

func (d *Dict[K, V]) compact() {
	live := make([]entry[K, V], 0, len(d.index))
	for _, e := range d.entries {
		if e.live {
			d.index[e.key] = len(live)
			live = append(live, e)
		}
	}
	d.entries, d.dead = live, 0
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

// Copy is d.copy() (a shallow copy).
func (d *Dict[K, V]) Copy() *Dict[K, V] {
	c := &Dict[K, V]{entries: make([]entry[K, V], 0, len(d.index)), index: make(map[K]int, len(d.index))}
	for _, e := range d.entries {
		if e.live {
			c.index[e.key] = len(c.entries)
			c.entries = append(c.entries, e)
		}
	}
	return c
}

// Clear is d.clear().
func (d *Dict[K, V]) Clear() {
	d.entries, d.index, d.dead = nil, map[K]int{}, 0
}
