package svc

import (
	"context"

	notesv1 "example.com/notesapp/gen/notes/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"gorm.io/gorm"
)

// Accounts serves notes.v1.Accounts.
type Accounts struct {
	notesv1.UnimplementedAccountsServer
	db  *gorm.DB
	bug string
}

// NewAccounts builds the service over db; bug names a deliberate fault (tests only).
func NewAccounts(db *gorm.DB, bug string) *Accounts { return &Accounts{db: db, bug: bug} }

func (s *Accounts) Register(ctx context.Context, req *notesv1.RegisterRequest) (*notesv1.RegisterResponse, error) {
	if len(req.Name) < 3 || len(req.Name) > 32 {
		return nil, status.Error(codes.InvalidArgument, "name must be 3 to 32 characters")
	}
	if len(req.Password) < 6 {
		return nil, status.Error(codes.InvalidArgument, "password is too short")
	}
	var taken int64
	if err := s.db.Model(&User{}).Where("name = ?", req.Name).Count(&taken).Error; err != nil {
		return nil, internalErr(err)
	}
	if taken > 0 {
		if s.bug == "dup_code" {
			return nil, status.Errorf(codes.InvalidArgument, "name %q is taken", req.Name)
		}
		return nil, status.Errorf(codes.AlreadyExists, "name %q is taken", req.Name)
	}
	u := User{Name: req.Name, Password: req.Password, Token: "tok-" + req.Name}
	if err := s.db.Create(&u).Error; err != nil {
		return nil, internalErr(err)
	}
	return &notesv1.RegisterResponse{Id: int64(u.ID)}, nil
}

func (s *Accounts) Login(ctx context.Context, req *notesv1.LoginRequest) (*notesv1.LoginResponse, error) {
	if req.Name == "" {
		return nil, status.Error(codes.InvalidArgument, "name is required")
	}
	var u User
	if err := s.db.Where("name = ? AND password = ?", req.Name, req.Password).First(&u).Error; err != nil {
		return nil, status.Error(codes.Unauthenticated, "bad credentials")
	}
	return &notesv1.LoginResponse{Token: u.Token}, nil
}

func (s *Accounts) Me(ctx context.Context, req *notesv1.MeRequest) (*notesv1.MeResponse, error) {
	u, err := currentUser(ctx)
	if err != nil {
		return nil, err
	}
	return &notesv1.MeResponse{Id: int64(u.ID), Name: u.Name}, nil
}
