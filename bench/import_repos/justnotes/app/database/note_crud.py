# ruff: noqa
from sqlmodel import Session, select
from fastapi import HTTPException
from ..models.note import Note
from ..models.user import User


class NoteCRUD:
    """
    CRUD operations for Note model.

    Provides database operations for creating, reading, and updating notes.
    All operations are performed within a database session.
    """

    def __init__(self, session: Session):
        """
        Initialize NoteCRUD with database session.

        Args:
            session: SQLModel Session for database operations
        """
        self.session = session

    def get_all_notes(self):
        """
        Retrieve all notes from the database (admin function).

        Returns:
            list[Note]: List of all notes in the database
        """
        return self.session.exec(select(Note)).all()

    def get_user_notes(self, user_id: int):
        """
        Retrieve all notes created by a specific user with owner information.

        Args:
            user_id: ID of the user whose notes to retrieve

        Returns:
            list[dict]: List of notes with owner information as dictionaries
        """
        notes = self.session.exec(select(Note).where(Note.owner_id == user_id)).all()

        result = []
        for note in notes:
            note_dict = {
                "id": note.id,
                "title": note.title,
                "text": note.text,
                "isPublic": note.isPublic,
                "createdAt": note.createdAt,
                "updatedAt": note.updatedAt,
                "owner_id": note.owner_id,
            }

            if note.owner_id:
                user = self.session.exec(
                    select(User).where(User.id == note.owner_id)
                ).one_or_none()
                if user:
                    note_dict["owner"] = {
                        "id": user.id,
                        "username": user.username,
                        "createdAt": user.createdAt,
                        "updatedAt": user.updatedAt,
                    }

            result.append(note_dict)

        return result

    def get_public_notes(self):
        """
        Retrieve all public notes from the database with owner information.

        Returns:
            list[dict]: List of public notes with owner information as dictionaries
        """
        notes = self.session.exec(select(Note).where(Note.isPublic)).all()

        result = []
        for note in notes:
            note_dict = {
                "id": note.id,
                "title": note.title,
                "text": note.text,
                "isPublic": note.isPublic,
                "createdAt": note.createdAt,
                "updatedAt": note.updatedAt,
                "owner_id": note.owner_id,
            }

            if note.owner_id:
                user = self.session.exec(
                    select(User).where(User.id == note.owner_id)
                ).one_or_none()
                if user:
                    note_dict["owner"] = {
                        "id": user.id,
                        "username": user.username,
                        "createdAt": user.createdAt,
                        "updatedAt": user.updatedAt,
                    }

            result.append(note_dict)

        return result

    def get_note_by_id(self, note_id: int):
        """
        Retrieve a specific note by ID with user information.

        Args:
            note_id: ID of the note to retrieve

        Returns:
            tuple: (Note, User) objects

        Raises:
            HTTPException: 404 if note is not found
        """
        statement = select(Note, User).join(User).where(Note.id == note_id)
        result = self.session.exec(statement).one_or_none()

        if result is None:
            raise HTTPException(status_code=404, detail="Note not found")

        note, user = result
        return note, user

    def create_note(self, note_data, user_id: int):
        """
        Create a new note in the database.

        Args:
            note_data: NoteBase object with note data
            user_id: ID of the user creating the note

        Returns:
            Note: Created note with ID and timestamps

        Raises:
            HTTPException: 404 if user doesn't exist
            HTTPException: 400 if note with same title already exists
        """
        result = self.session.exec(select(User).where(User.id == user_id)).one_or_none()
        if result is None:
            raise HTTPException(status_code=404, detail="User not found")

        result = self.session.exec(
            select(Note).where(
                (Note.title == note_data.title) & (Note.owner_id == user_id)
            )
        ).one_or_none()
        if result is not None:
            raise HTTPException(
                status_code=400, detail="Note with same title already exists"
            )

        new_note = Note(
            title=note_data.title,
            text=note_data.text,
            owner_id=user_id,
            isPublic=note_data.isPublic,
        )
        self.session.add(new_note)
        self.session.commit()
        self.session.refresh(new_note)

        note_dict = {
            "id": new_note.id,
            "title": new_note.title,
            "text": new_note.text,
            "isPublic": new_note.isPublic,
            "createdAt": new_note.createdAt,
            "updatedAt": new_note.updatedAt,
            "owner_id": new_note.owner_id,
        }

        user = self.session.exec(select(User).where(User.id == user_id)).one_or_none()
        if user:
            note_dict["owner"] = {
                "id": user.id,
                "username": user.username,
                "createdAt": user.createdAt,
                "updatedAt": user.updatedAt,
            }

        return note_dict

    def update_note(self, note_id: int, note_data, user_id: int):
        """
        Update an existing note in the database.

        Args:
            note_id: ID of the note to update
            note_data: NoteBase object with updated note data
            user_id: ID of the user attempting to update

        Returns:
            dict: Updated note with owner information

        Raises:
            HTTPException: 404 if note is not found
            HTTPException: 403 if user doesn't own the note
        """
        note, user = self.get_note_by_id(note_id)

        if note.owner_id != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to modify this note"
            )

        note.title = note_data.title
        note.text = note_data.text
        note.isPublic = note_data.isPublic
        note.updatedAt = int(__import__("time").time())

        self.session.add(note)
        self.session.commit()
        self.session.refresh(note)

        note_dict = {
            "id": note.id,
            "title": note.title,
            "text": note.text,
            "isPublic": note.isPublic,
            "createdAt": note.createdAt,
            "updatedAt": note.updatedAt,
            "owner_id": note.owner_id,
        }

        note_dict["owner"] = {
            "id": user.id,
            "username": user.username,
            "createdAt": user.createdAt,
            "updatedAt": user.updatedAt,
        }

        return note_dict

    def delete_note(self, note_id: int, user_id: int):
        """
        Delete a note from the database.

        Args:
            note_id: ID of the note to delete
            user_id: ID of the user attempting to delete

        Returns:
            dict: Success message

        Raises:
            HTTPException: 404 if note is not found
            HTTPException: 403 if user doesn't own the note
        """
        note, user = self.get_note_by_id(note_id)

        if note.owner_id != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this note"
            )

        self.session.delete(note)
        self.session.commit()

        return {"message": "Note deleted successfully"}
