package rt

// Handler is one `except K1, K2 as e:` clause.
type Handler struct {
	Kinds []string
	Fn    func(e *Error)
}

// Try is try/except: body runs; a Python exception (*Error with a Kind) raised in it
// runs the first handler naming its kind. Lattice runtime errors (*Error with a Code:
// fuel, size, Int range) are never caught, as a T2 procedure cannot name T2Error; any
// other panic propagates unchanged. An error raised by a handler propagates.
func Try(body func(), handlers ...Handler) {
	var caught *Error
	var h *Handler
	func() {
		defer func() {
			r := recover()
			if r == nil {
				return
			}
			e, ok := r.(*Error)
			if !ok || e.Code != "" {
				panic(r)
			}
			for i := range handlers {
				for _, k := range handlers[i].Kinds {
					if k == e.Kind {
						caught, h = e, &handlers[i]
						return
					}
				}
			}
			panic(r)
		}()
		body()
	}()
	if h != nil {
		h.Fn(caught) // outside the deferred call: a handler's own error is an ordinary panic
	}
}
