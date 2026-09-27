# ruff: noqa
from sqlmodel import Session, select
from fastapi import HTTPException
from ..models.tag import Keyword, NoteKeywordLink


class KeywordCRUD:
    """
    CRUD operations for Keyword model.

    Provides database operations for creating, reading, and managing keywords.
    All operations are performed within a database session.
    """

    def __init__(self, session: Session):
        """
        Initialize KeywordCRUD with database session.

        Args:
            session: SQLModel Session for database operations
        """
        self.session = session

    def get_or_create_keyword(
        self, term: str, is_main_topic: bool, owner_id: int
    ):
        """
        Get existing keyword or create new one if it doesn't exist.

        Args:
            term: Keyword term
            is_main_topic: Whether this is a main topic
            owner_id: User ID who owns this keyword

        Returns:
            Keyword: Existing or newly created keyword
        """
        keyword = self.session.exec(
            select(Keyword).where(
                (Keyword.term == term) & (Keyword.owner_id == owner_id)
            )
        ).one_or_none()

        if keyword is None:
            keyword = Keyword(
                term=term, is_main_topic=is_main_topic, owner_id=owner_id
            )
            self.session.add(keyword)
            self.session.commit()
            self.session.refresh(keyword)

        return keyword

    def get_user_keywords(self, owner_id: int):
        """
        Get all keywords for a specific user.

        Args:
            owner_id: User ID

        Returns:
            list[Keyword]: List of keywords owned by the user
        """
        return self.session.exec(
            select(Keyword).where(Keyword.owner_id == owner_id)
        ).all()

    def add_keyword_to_note(self, note_id: int, keyword_id: int):
        """
        Link a keyword to a note.

        Args:
            note_id: Note ID
            keyword_id: Keyword ID
        """
        existing = self.session.exec(
            select(NoteKeywordLink).where(
                (NoteKeywordLink.note_id == note_id)
                & (NoteKeywordLink.keyword_id == keyword_id)
            )
        ).one_or_none()

        if existing is None:
            link = NoteKeywordLink(note_id=note_id, keyword_id=keyword_id)
            self.session.add(link)
            self.session.commit()

    def remove_keyword_from_note(self, note_id: int, keyword_id: int):
        """
        Remove a keyword from a note.

        Args:
            note_id: Note ID
            keyword_id: Keyword ID
        """
        link = self.session.exec(
            select(NoteKeywordLink).where(
                (NoteKeywordLink.note_id == note_id)
                & (NoteKeywordLink.keyword_id == keyword_id)
            )
        ).one_or_none()

        if link:
            self.session.delete(link)
            self.session.commit()

    def get_note_keywords(self, note_id: int):
        """
        Get all keywords for a specific note.

        Args:
            note_id: Note ID

        Returns:
            list[Keyword]: List of keywords associated with the note
        """
        statement = (
            select(Keyword)
            .join(NoteKeywordLink)
            .where(NoteKeywordLink.note_id == note_id)
        )
        return self.session.exec(statement).all()

    def delete_keyword(self, keyword_id: int, owner_id: int):
        """
        Delete a keyword (only if owned by user).

        Args:
            keyword_id: Keyword ID
            owner_id: User ID

        Raises:
            HTTPException: 404 if keyword not found or 403 if not owned by user
        """
        keyword = self.session.exec(
            select(Keyword).where(Keyword.id == keyword_id)
        ).one_or_none()

        if keyword is None:
            raise HTTPException(status_code=404, detail="Keyword not found")

        if keyword.owner_id != owner_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this keyword"
            )

        self.session.delete(keyword)
        self.session.commit()
