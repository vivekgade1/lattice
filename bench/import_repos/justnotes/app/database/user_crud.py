# ruff: noqa
from sqlmodel import Session, select
from fastapi import HTTPException
from ..models.user import User


class UserCRUD:
    """
    CRUD operations for User model.

    Provides database operations for creating and reading users.
    All operations are performed within a database session.
    """

    def __init__(self, session: Session):
        """
        Initialize UserCRUD with database session.

        Args:
            session: SQLModel Session for database operations
        """
        self.session = session

    def get_all_users(self):
        """
        Retrieve all users from the database.

        Returns:
            list[User]: List of all users in the database
        """
        return self.session.exec(select(User)).all()

    def get_user_by_id(self, user_id: int):
        """
        Retrieve a specific user by ID.

        Args:
            user_id: ID of the user to retrieve

        Returns:
            User: User with specified ID, or None if not found
        """
        return self.session.exec(select(User).where(User.id == user_id)).one_or_none()

    def get_user_by_username(self, username: str):
        """
        Retrieve a specific user by username.

        Args:
            username: Username of the user to retrieve

        Returns:
            User: User with specified username, or None if not found
        """
        return self.session.exec(
            select(User).where(User.username == username)
        ).one_or_none()

    def create_user(self, user_data):
        """
        Create a new user in the database.

        Args:
            user_data: UserBase object with user data (password should be pre-hashed)

        Returns:
            User: Created user with ID and timestamps

        Raises:
            HTTPException: 400 if user with same username already exists

        Note:
            Password should be hashed before calling this method
        """
        result = self.session.exec(
            select(User).where(User.username == user_data.username)
        ).one_or_none()
        if result is not None:
            raise HTTPException(status_code=400, detail="User already exists")

        new_user = User(username=user_data.username, password=user_data.password)
        self.session.add(new_user)
        self.session.commit()
        self.session.refresh(new_user)
        return new_user
