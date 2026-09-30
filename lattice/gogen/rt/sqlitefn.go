package rt

import (
	"crypto/rand"
	"database/sql/driver"
	"errors"
	"fmt"
	"math/big"
	"sync"

	"modernc.org/sqlite"
)

// errUserFunction is what SQLite reports when a function the prelude registers raises
// (Python's sqlite3 answers every exception so).
var errUserFunction = errors.New("user-defined function raised exception")

type sqlFn = func(ctx *sqlite.FunctionContext, args []driver.Value) (driver.Value, error)

// SQLFunctions are the functions the compiled SQL calls (SqliteDialect.register_functions
// and the prelude's connect()), by name: lattice_lower/upper/strip/mod, lattice_to_uuid,
// lattice_to_datetime, lattice_datetime_add, lattice_datetime_diff and lattice_uuid4.
// They read the process-wide Storage.
var SQLFunctions = []struct {
	Name          string
	NArgs         int32
	Deterministic bool
	Fn            sqlFn
}{
	{"lattice_lower", 1, true, func(_ *sqlite.FunctionContext, a []driver.Value) (driver.Value, error) {
		if s, ok := a[0].(string); ok {
			return Lower(s), nil
		}
		return a[0], nil
	}},
	{"lattice_upper", 1, true, func(_ *sqlite.FunctionContext, a []driver.Value) (driver.Value, error) {
		if s, ok := a[0].(string); ok {
			return Upper(s), nil
		}
		return a[0], nil
	}},
	{"lattice_strip", 1, true, func(_ *sqlite.FunctionContext, a []driver.Value) (driver.Value, error) {
		if s, ok := a[0].(string); ok {
			return Strip(s, nil), nil
		}
		return a[0], nil
	}},
	{"lattice_mod", 2, true, func(_ *sqlite.FunctionContext, a []driver.Value) (driver.Value, error) {
		return sqlMod(a[0], a[1])
	}},
	{"lattice_to_uuid", 1, true, func(_ *sqlite.FunctionContext, a []driver.Value) (driver.Value, error) {
		if s, ok := a[0].(string); ok {
			if u, ok := UUIDText(s); ok {
				return StoredUUID(u), nil
			}
		}
		return nil, nil
	}},
	{"lattice_to_datetime", 2, true, func(_ *sqlite.FunctionContext, a []driver.Value) (driver.Value, error) {
		s, ok1 := a[0].(string)
		f, ok2 := a[1].(string)
		if !ok1 || !ok2 {
			return nil, nil
		}
		t, ok := Strptime(s, f)
		if !ok {
			return nil, nil
		}
		return StoredDateTime(t), nil
	}},
	{"lattice_datetime_add", 5, true, func(_ *sqlite.FunctionContext, a []driver.Value) (driver.Value, error) {
		return datetimeAdd(a)
	}},
	{"lattice_datetime_diff", 3, true, func(_ *sqlite.FunctionContext, a []driver.Value) (driver.Value, error) {
		return datetimeDiff(a[0], a[1], a[2]), nil
	}},
	{"lattice_uuid4", 0, false, func(_ *sqlite.FunctionContext, _ []driver.Value) (driver.Value, error) {
		return StoredUUID(NewUUID4()), nil
	}},
}

var registerOnce sync.Once

// RegisterFunctions installs SQLFunctions in the driver (once per process): every
// connection opened afterwards has them.
func RegisterFunctions() {
	registerOnce.Do(func() {
		for _, f := range SQLFunctions {
			fn := f.Fn
			impl := &sqlite.FunctionImpl{NArgs: f.NArgs, Deterministic: f.Deterministic,
				Scalar: func(ctx *sqlite.FunctionContext, args []driver.Value) (v driver.Value, err error) {
					defer func() {
						if r := recover(); r != nil {
							v, err = nil, errUserFunction
						}
					}()
					return fn(ctx, args)
				}}
			sqlite.MustRegisterFunction(f.Name, impl)
		}
	})
}

// NewUUID4 is str(uuid.uuid4()).
func NewUUID4() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// sqlMod is the prelude's _mod: null for a null operand or a zero divisor, else
// Python's %.
func sqlMod(a, b driver.Value) (driver.Value, error) {
	if a == nil || b == nil {
		return nil, nil
	}
	switch y := b.(type) {
	case int64:
		if y == 0 {
			return nil, nil
		}
	case float64:
		if y == 0 {
			return nil, nil
		}
	}
	switch x := a.(type) {
	case int64:
		switch y := b.(type) {
		case int64:
			if y == -1 {
				return int64(0), nil
			}
			return Mod(x, y), nil
		case float64:
			return ModF(float64(x), y), nil
		}
	case float64:
		switch y := b.(type) {
		case int64:
			return ModF(x, float64(y)), nil
		case float64:
			return ModF(x, y), nil
		}
	}
	return nil, errUserFunction // Python's % of a str / bytes raises (or formats)
}

func intArg(v driver.Value) (*big.Int, bool) {
	n, ok := v.(int64)
	if !ok {
		return nil, false
	}
	return big.NewInt(n), true
}

// datetimeAdd is the prelude's _datetime_add: null for a null/unparsable value or a
// non-integer amount, or when the result (or the timedelta) is out of range.
func datetimeAdd(a []driver.Value) (driver.Value, error) {
	s, ok := a[0].(string)
	if !ok {
		return nil, nil
	}
	t, ok := ParseDateTime(s)
	if !ok {
		return nil, nil
	}
	total := new(big.Int)
	for i, unit := range []int64{86400, 3600, 60, 1} {
		n, ok := intArg(a[i+1])
		if !ok {
			return nil, nil
		}
		total.Add(total, n.Mul(n, big.NewInt(unit)))
	}
	days := new(big.Int).Div(total, big.NewInt(86400)) // Euclidean = floor for a positive divisor
	if days.CmpAbs(big.NewInt(999_999_999)) > 0 {
		return nil, nil
	}
	us := new(big.Int).Mul(total, big.NewInt(1_000_000))
	us.Add(us, big.NewInt(int64(t)))
	if us.Cmp(big.NewInt(int64(minDateTime))) < 0 || us.Cmp(big.NewInt(int64(maxDateTime))) > 0 {
		return nil, nil
	}
	return StoredDateTime(DateTime(us.Int64())), nil
}

var datetimeUnitsUS = map[string]int64{
	"days":    86_400_000_000,
	"hours":   3_600_000_000,
	"minutes": 60_000_000,
	"seconds": 1_000_000,
}

// datetimeDiff is the prelude's _datetime_diff: end minus start in whole units,
// truncated toward zero.
func datetimeDiff(end, start, unit driver.Value) driver.Value {
	es, ok1 := end.(string)
	ss, ok2 := start.(string)
	u, ok3 := unit.(string)
	if !ok1 || !ok2 || !ok3 {
		return nil
	}
	a, okA := ParseDateTime(es)
	b, okB := ParseDateTime(ss)
	size, okU := datetimeUnitsUS[u]
	if !okA || !okB || !okU {
		return nil
	}
	micros := int64(a) - int64(b)
	if micros >= 0 {
		return micros / size
	}
	return -(-micros / size)
}
