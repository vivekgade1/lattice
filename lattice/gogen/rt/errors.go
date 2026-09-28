// Package rt is the runtime of Go programs compiled from Lattice: Python 3.14
// semantics for the values T2 procedures compute (checked 64-bit Int, Python float
// text, code-point strings, insertion-ordered dict and set), the fuel accounting of
// lattice/t2/support.py, and try/except of Lattice errors.
package rt

// Error is every error a compiled program raises. A Lattice runtime diagnostic
// (fuel R620, row bound R621, size R622, Int range R623) has a Code and no Kind; it is
// never caught by `except`. A Python exception a procedure may catch (KeyError,
// IndexError, ValueError, ZeroDivisionError) has a Kind and no Code. Msg is the text
// Python gives: the diagnostic's message, or str() of the exception.
type Error struct{ Code, Kind, Msg string }

func (e *Error) Error() string {
	if e.Code != "" {
		return e.Code + ": " + e.Msg
	}
	return e.Kind + ": " + e.Msg
}

// Raise raises the Python exception kind with str(exception) == msg.
func Raise(kind, msg string) { panic(&Error{Kind: kind, Msg: msg}) }

// Fail raises the Lattice runtime diagnostic code with its message.
func Fail(code, msg string) { panic(&Error{Code: code, Msg: msg}) }
