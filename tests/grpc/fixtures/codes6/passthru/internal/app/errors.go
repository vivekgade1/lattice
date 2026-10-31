package app

import (
	"errors"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// AppError carries its own gRPC code.
type AppError struct {
	code codes.Code
	msg  string
}

func (e *AppError) Error() string { return e.msg }

// GRPCStatus lets grpc-go report the code without a mapping step.
func (e *AppError) GRPCStatus() *status.Status { return status.New(e.code, e.msg) }

func newErr(c codes.Code, msg string) *AppError { return &AppError{code: c, msg: msg} }

// Domain errors; only toStatus knows their codes.
var (
	ErrMissing = errors.New("missing")
	ErrBad     = errors.New("bad")
)

// keep passes application errors through and hides anything else.
func keep(err error) error {
	var ae *AppError
	if errors.As(err, &ae) {
		return err
	}
	return newErr(codes.Internal, "internal")
}

// conv turns any error into a status error.
func conv(err error) error {
	var ae *AppError
	if errors.As(err, &ae) {
		return ae.GRPCStatus().Err()
	}
	if _, ok := status.FromError(err); ok {
		return err
	}
	return status.Error(codes.Internal, "internal")
}

// toStatus is the one place a domain error becomes a status.
func toStatus(err error) error {
	switch {
	case err == nil:
		return nil
	case errors.Is(err, ErrMissing):
		return status.Error(codes.NotFound, err.Error())
	case errors.Is(err, ErrBad):
		return status.Error(codes.InvalidArgument, err.Error())
	default:
		return status.Error(codes.Internal, "internal")
	}
}

// echo returns its argument: no filter.
func echo(err error) error { return err }
