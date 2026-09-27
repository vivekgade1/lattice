# ruff: noqa
from sqlmodel import Session, select
from fastapi import HTTPException
from ..models.tag import Tag, NoteTagLink


class TagCRUD:
    """
    CRUD operations for Tag model.

    Provides database operations for creating, reading, and managing tags.
    All operations are performed within a database session.
    """

    def __init__(self, session: Session):
        """
        Initialize TagCRUD with database session.

        Args:
            session: SQLModel Session for database operations
        """
        self.session = session

    def get_or_create_tag(self, name: str, category: str | None, owner_id: int):
        """
        Get existing tag or create new one if it doesn't exist.

        Args:
            name: Tag name
            category: Optional category
            owner_id: User ID who owns this tag

        Returns:
            Tag: Existing or newly created tag
        """
        tag = self.session.exec(
            select(Tag).where((Tag.name == name) & (Tag.owner_id == owner_id))
        ).one_or_none()

        if tag is None:
            tag = Tag(name=name, category=category, owner_id=owner_id)
            self.session.add(tag)
            self.session.commit()
            self.session.refresh(tag)

        return tag

    def get_user_tags(self, owner_id: int):
        """
        Get all tags for a specific user.

        Args:
            owner_id: User ID

        Returns:
            list[Tag]: List of tags owned by the user
        """
        return self.session.exec(select(Tag).where(Tag.owner_id == owner_id)).all()

    def add_tag_to_note(self, note_id: int, tag_id: int):
        """
        Link a tag to a note.

        Args:
            note_id: Note ID
            tag_id: Tag ID
        """
        existing = self.session.exec(
            select(NoteTagLink).where(
                (NoteTagLink.note_id == note_id) & (NoteTagLink.tag_id == tag_id)
            )
        ).one_or_none()

        if existing is None:
            link = NoteTagLink(note_id=note_id, tag_id=tag_id)
            self.session.add(link)
            self.session.commit()

    def remove_tag_from_note(self, note_id: int, tag_id: int):
        """
        Remove a tag from a note.

        Args:
            note_id: Note ID
            tag_id: Tag ID
        """
        link = self.session.exec(
            select(NoteTagLink).where(
                (NoteTagLink.note_id == note_id) & (NoteTagLink.tag_id == tag_id)
            )
        ).one_or_none()

        if link:
            self.session.delete(link)
            self.session.commit()

    def get_note_tags(self, note_id: int):
        """
        Get all tags for a specific note.

        Args:
            note_id: Note ID

        Returns:
            list[Tag]: List of tags associated with the note
        """
        statement = (
            select(Tag)
            .join(NoteTagLink)
            .where(NoteTagLink.note_id == note_id)
        )
        return self.session.exec(statement).all()

    def delete_tag(self, tag_id: int, owner_id: int):
        """
        Delete a tag (only if owned by user).

        Args:
            tag_id: Tag ID
            owner_id: User ID

        Raises:
            HTTPException: 404 if tag not found or 403 if not owned by user
        """
        tag = self.session.exec(select(Tag).where(Tag.id == tag_id)).one_or_none()

        if tag is None:
            raise HTTPException(status_code=404, detail="Tag not found")

        if tag.owner_id != owner_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this tag"
            )

        self.session.delete(tag)
        self.session.commit()
