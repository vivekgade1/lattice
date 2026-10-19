package app

import (
	"fmt"
	"os"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// missing is an error type whose GRPCStatus is one literal code.
type missing struct{ what string }

func (m missing) Error() string { return "no " + m.what }

// GRPCStatus implements the interface grpc-go's status package looks for.
func (m missing) GRPCStatus() *status.Status { return status.New(codes.NotFound, m.Error()) }

func newMissing(what string) *missing { return &missing{what} }

// refusal carries its code in a field.
type refusal struct {
	code codes.Code
	msg  string
}

func (r *refusal) Error() string { return r.msg }

// GRPCStatus implements the interface grpc-go's status package looks for.
func (r *refusal) GRPCStatus() *status.Status { return status.New(r.code, r.msg) }

func refuse(c codes.Code, m string) *refusal { return &refusal{code: c, msg: m} }

func pickCode(in fmt.Stringer) codes.Code {
	if os.Getenv("STRICT") != "" {
		return codes.FailedPrecondition
	}
	return codes.Unknown
}

var errGone = &refusal{codes.Unavailable, "gone"}

func fail(c codes.Code, m string) error { return status.Error(c, m) }

func failf(c codes.Code, f string, a ...any) error { return status.Errorf(c, f, a...) }

func deny(c codes.Code) error { return fail(c, "denied") }

func wrapped(c codes.Code) error { return status.New(c, "w").Err() }
