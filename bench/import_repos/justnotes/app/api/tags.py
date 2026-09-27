# ruff: noqa
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session
from pydantic import BaseModel

from ..database.connection import get_db_session
from ..database import TagCRUD, KeywordCRUD, NoteCRUD
from ..api.auth import get_current_user

router = APIRouter()


def get_tag_crud(session: Session = Depends(get_db_session)):
    """
    Dependency function to get TagCRUD instance with database session.

    Args:
        session: Database session from dependency injection

    Returns:
        TagCRUD: Instance with database operations for tags
    """
    return TagCRUD(session)


def get_keyword_crud(session: Session = Depends(get_db_session)):
    """
    Dependency function to get KeywordCRUD instance with database session.

    Args:
        session: Database session from dependency injection

    Returns:
        KeywordCRUD: Instance with database operations for keywords
    """
    return KeywordCRUD(session)


def get_note_crud(session: Session = Depends(get_db_session)):
    """
    Dependency function to get NoteCRUD instance with database session.

    Args:
        session: Database session from dependency injection

    Returns:
        NoteCRUD: Instance with database operations for notes
    """
    return NoteCRUD(session)


class TagCreate(BaseModel):
    """Request model for creating a tag."""

    name: str
    category: str | None = None


class KeywordCreate(BaseModel):
    """Request model for creating a keyword."""

    term: str
    is_main_topic: bool = False


@router.get("/tags", tags=["Tags"])
def get_user_tags(
    current_user=Depends(get_current_user), crud: TagCRUD = Depends(get_tag_crud)
):
    """
    Get all tags for the current user.

    Args:
        current_user: Authenticated user from JWT token
        crud: TagCRUD instance for database operations

    Returns:
        list[dict]: List of tags with their information
    """
    tags = crud.get_user_tags(current_user.id)
    return [
        {
            "id": tag.id,
            "name": tag.name,
            "category": tag.category,
            "createdAt": tag.createdAt,
        }
        for tag in tags
    ]


@router.post("/tags", tags=["Tags"], status_code=201)
def create_tag(
    tag_data: TagCreate,
    current_user=Depends(get_current_user),
    crud: TagCRUD = Depends(get_tag_crud),
):
    """
    Create a new tag.

    Args:
        tag_data: Tag data from request body
        current_user: Authenticated user from JWT token
        crud: TagCRUD instance for database operations

    Returns:
        dict: Created tag information
    """
    tag = crud.get_or_create_tag(tag_data.name, tag_data.category, current_user.id)
    return {
        "id": tag.id,
        "name": tag.name,
        "category": tag.category,
        "createdAt": tag.createdAt,
    }


@router.delete("/tags/{tag_id}", tags=["Tags"])
def delete_tag(
    tag_id: int,
    current_user=Depends(get_current_user),
    crud: TagCRUD = Depends(get_tag_crud),
):
    """
    Delete a tag.

    Args:
        tag_id: Tag ID from URL path
        current_user: Authenticated user from JWT token
        crud: TagCRUD instance for database operations

    Returns:
        dict: Success message

    Raises:
        HTTPException: 404 if tag not found or 403 if not owned by user
    """
    crud.delete_tag(tag_id, current_user.id)
    return {"message": "Tag deleted successfully"}


@router.post("/notes/{note_id}/tags/{tag_id}", tags=["Tags"])
def add_tag_to_note(
    note_id: int,
    tag_id: int,
    current_user=Depends(get_current_user),
    tag_crud: TagCRUD = Depends(get_tag_crud),
    note_crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Add a tag to a note.

    Args:
        note_id: Note ID from URL path
        tag_id: Tag ID from URL path
        current_user: Authenticated user from JWT token
        tag_crud: TagCRUD instance for database operations
        note_crud: NoteCRUD instance for database operations

    Returns:
        dict: Success message

    Raises:
        HTTPException: 404 if note or tag not found, 403 if not authorized
    """
    note, _ = note_crud.get_note_by_id(note_id)
    if note.owner_id != current_user.id:
        raise HTTPException(
            status_code=403, detail="Not authorized to modify this note"
        )

    tag_crud.add_tag_to_note(note_id, tag_id)
    return {"message": "Tag added to note successfully"}


@router.delete("/notes/{note_id}/tags/{tag_id}", tags=["Tags"])
def remove_tag_from_note(
    note_id: int,
    tag_id: int,
    current_user=Depends(get_current_user),
    tag_crud: TagCRUD = Depends(get_tag_crud),
    note_crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Remove a tag from a note.

    Args:
        note_id: Note ID from URL path
        tag_id: Tag ID from URL path
        current_user: Authenticated user from JWT token
        tag_crud: TagCRUD instance for database operations
        note_crud: NoteCRUD instance for database operations

    Returns:
        dict: Success message

    Raises:
        HTTPException: 404 if note not found, 403 if not authorized
    """
    note, _ = note_crud.get_note_by_id(note_id)
    if note.owner_id != current_user.id:
        raise HTTPException(
            status_code=403, detail="Not authorized to modify this note"
        )

    tag_crud.remove_tag_from_note(note_id, tag_id)
    return {"message": "Tag removed from note successfully"}


@router.get("/notes/{note_id}/tags", tags=["Tags"])
def get_note_tags(
    note_id: int,
    current_user=Depends(get_current_user),
    tag_crud: TagCRUD = Depends(get_tag_crud),
    note_crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Get all tags for a specific note.

    Args:
        note_id: Note ID from URL path
        current_user: Authenticated user from JWT token
        tag_crud: TagCRUD instance for database operations
        note_crud: NoteCRUD instance for database operations

    Returns:
        list[dict]: List of tags associated with the note

    Raises:
        HTTPException: 404 if note not found, 403 if not authorized
    """
    note, _ = note_crud.get_note_by_id(note_id)
    if note.owner_id != current_user.id and not note.isPublic:
        raise HTTPException(
            status_code=403, detail="Not authorized to access this note"
        )

    tags = tag_crud.get_note_tags(note_id)
    return [
        {
            "id": tag.id,
            "name": tag.name,
            "category": tag.category,
            "createdAt": tag.createdAt,
        }
        for tag in tags
    ]


@router.get("/keywords", tags=["Keywords"])
def get_user_keywords(
    current_user=Depends(get_current_user),
    crud: KeywordCRUD = Depends(get_keyword_crud),
):
    """
    Get all keywords for the current user.

    Args:
        current_user: Authenticated user from JWT token
        crud: KeywordCRUD instance for database operations

    Returns:
        list[dict]: List of keywords with their information
    """
    keywords = crud.get_user_keywords(current_user.id)
    return [
        {
            "id": kw.id,
            "term": kw.term,
            "is_main_topic": kw.is_main_topic,
            "createdAt": kw.createdAt,
        }
        for kw in keywords
    ]


@router.post("/keywords", tags=["Keywords"], status_code=201)
def create_keyword(
    keyword_data: KeywordCreate,
    current_user=Depends(get_current_user),
    crud: KeywordCRUD = Depends(get_keyword_crud),
):
    """
    Create a new keyword.

    Args:
        keyword_data: Keyword data from request body
        current_user: Authenticated user from JWT token
        crud: KeywordCRUD instance for database operations

    Returns:
        dict: Created keyword information
    """
    keyword = crud.get_or_create_keyword(
        keyword_data.term, keyword_data.is_main_topic, current_user.id
    )
    return {
        "id": keyword.id,
        "term": keyword.term,
        "is_main_topic": keyword.is_main_topic,
        "createdAt": keyword.createdAt,
    }


@router.delete("/keywords/{keyword_id}", tags=["Keywords"])
def delete_keyword(
    keyword_id: int,
    current_user=Depends(get_current_user),
    crud: KeywordCRUD = Depends(get_keyword_crud),
):
    """
    Delete a keyword.

    Args:
        keyword_id: Keyword ID from URL path
        current_user: Authenticated user from JWT token
        crud: KeywordCRUD instance for database operations

    Returns:
        dict: Success message

    Raises:
        HTTPException: 404 if keyword not found or 403 if not owned by user
    """
    crud.delete_keyword(keyword_id, current_user.id)
    return {"message": "Keyword deleted successfully"}


@router.post("/notes/{note_id}/keywords/{keyword_id}", tags=["Keywords"])
def add_keyword_to_note(
    note_id: int,
    keyword_id: int,
    current_user=Depends(get_current_user),
    keyword_crud: KeywordCRUD = Depends(get_keyword_crud),
    note_crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Add a keyword to a note.

    Args:
        note_id: Note ID from URL path
        keyword_id: Keyword ID from URL path
        current_user: Authenticated user from JWT token
        keyword_crud: KeywordCRUD instance for database operations
        note_crud: NoteCRUD instance for database operations

    Returns:
        dict: Success message

    Raises:
        HTTPException: 404 if note or keyword not found, 403 if not authorized
    """
    note, _ = note_crud.get_note_by_id(note_id)
    if note.owner_id != current_user.id:
        raise HTTPException(
            status_code=403, detail="Not authorized to modify this note"
        )

    keyword_crud.add_keyword_to_note(note_id, keyword_id)
    return {"message": "Keyword added to note successfully"}


@router.delete("/notes/{note_id}/keywords/{keyword_id}", tags=["Keywords"])
def remove_keyword_from_note(
    note_id: int,
    keyword_id: int,
    current_user=Depends(get_current_user),
    keyword_crud: KeywordCRUD = Depends(get_keyword_crud),
    note_crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Remove a keyword from a note.

    Args:
        note_id: Note ID from URL path
        keyword_id: Keyword ID from URL path
        current_user: Authenticated user from JWT token
        keyword_crud: KeywordCRUD instance for database operations
        note_crud: NoteCRUD instance for database operations

    Returns:
        dict: Success message

    Raises:
        HTTPException: 404 if note not found, 403 if not authorized
    """
    note, _ = note_crud.get_note_by_id(note_id)
    if note.owner_id != current_user.id:
        raise HTTPException(
            status_code=403, detail="Not authorized to modify this note"
        )

    keyword_crud.remove_keyword_from_note(note_id, keyword_id)
    return {"message": "Keyword removed from note successfully"}


@router.get("/notes/{note_id}/keywords", tags=["Keywords"])
def get_note_keywords(
    note_id: int,
    current_user=Depends(get_current_user),
    keyword_crud: KeywordCRUD = Depends(get_keyword_crud),
    note_crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Get all keywords for a specific note.

    Args:
        note_id: Note ID from URL path
        current_user: Authenticated user from JWT token
        keyword_crud: KeywordCRUD instance for database operations
        note_crud: NoteCRUD instance for database operations

    Returns:
        list[dict]: List of keywords associated with the note

    Raises:
        HTTPException: 404 if note not found, 403 if not authorized
    """
    note, _ = note_crud.get_note_by_id(note_id)
    if note.owner_id != current_user.id and not note.isPublic:
        raise HTTPException(
            status_code=403, detail="Not authorized to access this note"
        )

    keywords = keyword_crud.get_note_keywords(note_id)
    return [
        {
            "id": kw.id,
            "term": kw.term,
            "is_main_topic": kw.is_main_topic,
            "createdAt": kw.createdAt,
        }
        for kw in keywords
    ]
