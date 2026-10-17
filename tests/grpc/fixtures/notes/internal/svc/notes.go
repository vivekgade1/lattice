package svc

import (
	"context"
	"errors"

	notesv1 "example.com/notesapp/gen/notes/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"gorm.io/gorm"
)

const maxPublished = 3

// Notes serves notes.v1.Notes. ShareNote is left to the embedded Unimplemented stub.
type Notes struct {
	notesv1.UnimplementedNotesServer
	DB  *gorm.DB
	Bug string
}

// archiveRefusals picks the answer for a note that cannot be archived again.
var archiveRefusals = map[string]codes.Code{
	"archived": codes.FailedPrecondition,
}

func visibility(state string) notesv1.Visibility {
	if state == "published" {
		return notesv1.Visibility_SHARED
	}
	return notesv1.Visibility_PRIVATE
}

func toProto(n *Note) *notesv1.Note {
	return &notesv1.Note{
		Id: int64(n.ID), Title: n.Title, Body: n.Body, State: n.State,
		Priority: int32(n.Priority), ClientRev: n.ClientRev, Visibility: visibility(n.State),
	}
}

// owned loads the caller's note.
func (s *Notes) owned(ctx context.Context, id int64) (*Note, error) {
	u := caller(ctx)
	var n Note
	q := s.DB.Where("id = ? AND user_id = ?", id, u.ID)
	if s.Bug == "no_owner" {
		q = s.DB.Where("id = ?", id)
	}
	if err := q.First(&n).Error; err != nil {
		return nil, status.Error(codes.NotFound, "note not found")
	}
	return &n, nil
}

func (s *Notes) CreateNote(ctx context.Context, req *notesv1.CreateNoteRequest) (*notesv1.CreateNoteResponse, error) {
	u := caller(ctx)
	if req.Title == "" || len(req.Title) > 80 {
		return nil, status.Error(codes.InvalidArgument, "title must be 1 to 80 characters")
	}
	if (req.Priority < 0 || req.Priority > 5) && s.Bug != "no_priority_check" {
		return nil, status.Error(codes.InvalidArgument, "priority must be 0 to 5")
	}
	n := Note{UserID: u.ID, Title: req.Title, Body: req.Body, State: "draft", Priority: int(req.Priority), ClientRev: req.ClientRev}
	if err := s.DB.Create(&n).Error; err != nil {
		return nil, internalErr(err)
	}
	return &notesv1.CreateNoteResponse{Note: toProto(&n)}, nil
}

func (s *Notes) GetNote(ctx context.Context, req *notesv1.GetNoteRequest) (*notesv1.GetNoteResponse, error) {
	n, err := s.owned(ctx, req.Id)
	if err != nil {
		return nil, err
	}
	return &notesv1.GetNoteResponse{Note: toProto(n)}, nil
}

func (s *Notes) ListNotes(ctx context.Context, req *notesv1.ListNotesRequest) (*notesv1.ListNotesResponse, error) {
	u := caller(ctx)
	q := s.DB.Where("user_id = ?", u.ID)
	if s.Bug == "list_all" {
		q = s.DB.Where("1 = 1")
	}
	if req.State != "" {
		q = q.Where("state = ?", req.State)
	}
	var rows []Note
	if err := q.Order("id").Find(&rows).Error; err != nil {
		return nil, internalErr(err)
	}
	out := &notesv1.ListNotesResponse{}
	for i := range rows {
		out.Notes = append(out.Notes, toProto(&rows[i]))
	}
	return out, nil
}

func (s *Notes) UpdateNote(ctx context.Context, req *notesv1.UpdateNoteRequest) (*notesv1.UpdateNoteResponse, error) {
	n, err := s.owned(ctx, req.Id)
	if err != nil {
		return nil, err
	}
	if n.State == "archived" && s.Bug != "update_archived" {
		return nil, status.Error(codes.FailedPrecondition, "archived notes are read-only")
	}
	if req.Title == "" || len(req.Title) > 80 {
		return nil, status.Error(codes.InvalidArgument, "title must be 1 to 80 characters")
	}
	n.Title, n.Body, n.Priority, n.ClientRev = req.Title, req.Body, int(req.Priority), req.ClientRev
	if err := s.DB.Save(n).Error; err != nil {
		return nil, internalErr(err)
	}
	return &notesv1.UpdateNoteResponse{Note: toProto(n)}, nil
}

func (s *Notes) PublishNote(ctx context.Context, req *notesv1.PublishNoteRequest) (*notesv1.PublishNoteResponse, error) {
	u := caller(ctx)
	var n Note
	if err := s.DB.Where("id = ? AND user_id = ?", req.Id, u.ID).First(&n).Error; err != nil {
		return nil, status.Error(codes.NotFound, "note not found")
	}
	if n.State != "draft" && s.Bug != "publish_any_state" {
		return nil, status.Error(codes.FailedPrecondition, "only a draft can be published")
	}
	var live int64
	if err := s.DB.Model(&Note{}).Where("user_id = ? AND state = ?", u.ID, "published").Count(&live).Error; err != nil {
		return nil, internalErr(err)
	}
	if live >= maxPublished && s.Bug != "publish_limit_off" {
		return nil, status.Error(codes.ResourceExhausted, "at most 3 published notes")
	}
	if err := s.DB.Model(&n).Update("state", "published").Error; err != nil {
		return nil, internalErr(err)
	}
	return &notesv1.PublishNoteResponse{Note: toProto(&n)}, nil
}

func (s *Notes) ArchiveNote(ctx context.Context, req *notesv1.ArchiveNoteRequest) (*notesv1.ArchiveNoteResponse, error) {
	n, err := s.owned(ctx, req.Id)
	if err != nil {
		return nil, err
	}
	if n.State == "archived" {
		return nil, status.Error(archiveRefusals[n.State], "already archived")
	}
	if err := s.DB.Model(n).Update("state", "archived").Error; err != nil {
		return nil, internalErr(err)
	}
	return &notesv1.ArchiveNoteResponse{Note: toProto(n)}, nil
}

func (s *Notes) DeleteNote(ctx context.Context, req *notesv1.DeleteNoteRequest) (*notesv1.DeleteNoteResponse, error) {
	n, err := s.owned(ctx, req.Id)
	if err != nil {
		return nil, err
	}
	if n.State == "published" && s.Bug != "delete_published" {
		return nil, errors.New("published notes cannot be deleted")
	}
	if err := s.DB.Delete(n).Error; err != nil {
		return nil, internalErr(err)
	}
	return &notesv1.DeleteNoteResponse{}, nil
}

func (s *Notes) WatchNotes(req *notesv1.WatchNotesRequest, stream notesv1.Notes_WatchNotesServer) error {
	u := caller(stream.Context())
	var rows []Note
	if err := s.DB.Where("user_id = ?", u.ID).Order("id").Find(&rows).Error; err != nil {
		return internalErr(err)
	}
	for i := range rows {
		if err := stream.Send(toProto(&rows[i])); err != nil {
			return err
		}
	}
	return nil
}
