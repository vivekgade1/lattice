# ruff: noqa
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..models.user import UserResponse
from ..database.connection import get_db_session
from ..database import NoteCRUD, TagCRUD, KeywordCRUD
from ..models.note import NoteBase, NoteResponse
from ..api.auth import get_current_user
from ..config import settings

# Router for note-related endpoints
router = APIRouter()


def get_note_crud(session: Session = Depends(get_db_session)):
    """
    Dependency function to get NoteCRUD instance with database session.

    Args:
        session: Database session from dependency injection

    Returns:
        NoteCRUD: Instance with database operations for notes
    """
    return NoteCRUD(session)


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


@router.get("/notes", tags=["Notes"], response_model=list[dict])
def get_notes(
    current_user=Depends(get_current_user), crud: NoteCRUD = Depends(get_note_crud)
):
    """
    Get all notes for the current authenticated user.

    Args:
        current_user: Authenticated user from JWT token
        crud: NoteCRUD instance for database operations

    Returns:
        list[dict]: List of notes created by the current user with owner information

    Note:
        Only returns notes that belong to the authenticated user
    """
    return crud.get_user_notes(current_user.id)


@router.post("/notes/create", tags=["Notes"], response_model=dict, status_code=201)
def create_notes(
    item: NoteBase,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Create a new note (requires authentication).

    Args:
        item: Note data from request body
        current_user: Authenticated user from JWT token
        crud: NoteCRUD instance for database operations

    Returns:
        dict: Created note with ID, timestamps, and owner information

    Note:
        The note is automatically assigned to the current user
    """
    return crud.create_note(item, current_user.id)


@router.get("/notes/public", tags=["Notes"], response_model=list[dict])
def get_public_notes(crud: NoteCRUD = Depends(get_note_crud)):
    """
    Get all public notes (no authentication required).

    Args:
        crud: NoteCRUD instance for database operations

    Returns:
        list[dict]: List of all public notes with owner information
    """
    return crud.get_public_notes()


@router.get("/notes/stats", tags=["Notes"], response_model=dict)
def get_note_stats(
    current_user=Depends(get_current_user), crud: NoteCRUD = Depends(get_note_crud)
):
    """
    Get statistics about the current user's own notes.

    Returns:
        dict: total, public and tagged note counts, and the latest updatedAt (or None)
    """
    notes = [
        note
        for note in crud.get_all_notes()
        if note.owner_id == current_user.id
    ]
    return {
        "total": len(notes),
        "public": sum(1 for note in notes if note.isPublic),
        "tagged": sum(1 for note in notes if note.tags),
        "last_updated_at": max((note.updatedAt for note in notes), default=None),
    }


def _check_note_access(note, current_user):
    """
    Helper function to check if user has access to a note.

    Args:
        note: Note object to check access for
        current_user: Current authenticated user

    Raises:
        HTTPException: 403 if user doesn't have access
    """
    if note.owner_id != current_user.id and not note.isPublic:
        raise HTTPException(
            status_code=403, detail="Not authorized to access this note"
        )


def _call_llm_api(model: str, prompt: str) -> dict:
    """
    Helper function to call the local LLM API.

    Args:
        model: Model name to use
        prompt: System query/prompt for the model

    Returns:
        dict: JSON response from the LLM API

    Raises:
        HTTPException: 503 if LLM service is unavailable
        HTTPException: 500 if LLM API returns an error
    """
    # LATTICE MOD: import deferred into this function (upstream: module-level import).
    # This repo's own dependencies don't list ``requests``; every route above that
    # reaches this helper is an optional LLM-backed feature (summarize/extend/
    # translate/auto-tag/keywords/sentiment/related) hitting a local LLM server that
    # isn't part of this held-out fixture, so nothing in the CRUD surface needs it
    # importable. Deferring the import instead of adding the dependency lets the rest
    # of the app run unchanged; only calling one of those LLM routes would need it.
    import requests

    try:
        response = requests.post(
            settings.llm_service_url,
            headers={"Content-Type": "application/json"},
            json={"model": model, "input": prompt},
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        raise HTTPException(
            status_code=503,
            detail=f"LLM service is not available. Please ensure the LLM server is running at {settings.llm_service_url}",
        )
    except requests.exceptions.Timeout:
        raise HTTPException(
            status_code=504,
            detail="LLM service request timed out. Please try again later",
        )
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error communicating with LLM service: {str(e)}",
        )


@router.get("/notes/{id}/summarize", tags=["Notes"])
def get_notes_summarize(
    id: int,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    note, user = crud.get_note_by_id(id)
    _check_note_access(note, current_user)

    system_query = f"""You are an intelligent summarization assistant for a notes app.
Summarize the following article accurately and concisely, using the same language in which the article is written.
Focus on key ideas, relevant facts, and overall meaning — avoid unnecessary details or repetition.
Title: {note.title}
Article: {note.text}
Return only the summary text without commentary or formatting instructions in the language of the article!"""

    return _call_llm_api(settings.llm_model, system_query)


@router.get("/notes/{id}/extend", tags=["Notes"])
def get_notes_extend(
    id: int,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    note, user = crud.get_note_by_id(id)
    _check_note_access(note, current_user)

    system_query = f"""You are an intelligent writing assistant for a notes app.
Extend the following article naturally in the same language it is written in.
Keep the original tone, style, and context consistent.
Add depth by expanding on key ideas, providing additional insights, examples, or relevant background information — but do not repeat what's already written.
Title: {note.title}
Article: {note.text}
Return only the extended version of the article in the language of the article!"""

    return _call_llm_api(settings.llm_model, system_query)


@router.get("/notes/{id}/translate/{target_language}", tags=["Notes"])
def get_notes_translate(
    id: int,
    target_language: str,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Translate a note to a target language using AI.

    Args:
        id: Note ID from URL path
        target_language: Target language for translation (e.g., 'English', 'German', 'Spanish')
        current_user: Authenticated user from JWT token
        crud: NoteCRUD instance for database operations

    Returns:
        dict: JSON response from LLM API with translated text

    Raises:
        HTTPException: 403 if user doesn't have access to the note
        HTTPException: 404 if note is not found
        HTTPException: 503 if LLM service is unavailable
    """
    note, user = crud.get_note_by_id(id)
    _check_note_access(note, current_user)

    system_query = f"""You are a professional translation assistant for a notes app.
Translate the following article accurately and naturally into {target_language}.
Preserve the original tone, style, and meaning. Maintain formatting if present.
Only translate the content, do not add commentary or explanations.
Title: {note.title}
Article: {note.text}
Return only the translated text in {target_language}!"""

    return _call_llm_api(settings.llm_model, system_query)


@router.get("/notes/{id}/auto-tag", tags=["Notes"])
def get_notes_auto_tag(
    id: int,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Automatically generate categories and tags for a note using AI.

    Args:
        id: Note ID from URL path
        current_user: Authenticated user from JWT token
        crud: NoteCRUD instance for database operations

    Returns:
        dict: JSON response with suggested categories and tags

    Raises:
        HTTPException: 403 if user doesn't have access to the note
        HTTPException: 404 if note is not found
        HTTPException: 503 if LLM service is unavailable
    """
    note, user = crud.get_note_by_id(id)
    _check_note_access(note, current_user)

    system_query = f"""You are an intelligent categorization assistant for a notes app.
Analyze the following note and suggest:
1. A primary category (e.g., Work, Personal, Ideas, Learning, Health, Finance, Travel, etc.)
2. 3-5 relevant tags/keywords that describe the content

Title: {note.title}
Content: {note.text}

Return your response in this exact JSON format:
{{
  "category": "CategoryName",
  "tags": ["tag1", "tag2", "tag3"]
}}

Only return the JSON, no additional text or explanation!"""

    return _call_llm_api(settings.llm_model, system_query)


@router.get("/notes/{id}/keywords", tags=["Notes"])
def get_notes_keywords(
    id: int,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Extract key terms and concepts from a note using AI.

    Args:
        id: Note ID from URL path
        current_user: Authenticated user from JWT token
        crud: NoteCRUD instance for database operations

    Returns:
        dict: JSON response with extracted keywords and key phrases

    Raises:
        HTTPException: 403 if user doesn't have access to the note
        HTTPException: 404 if note is not found
        HTTPException: 503 if LLM service is unavailable
    """
    note, user = crud.get_note_by_id(id)
    _check_note_access(note, current_user)

    system_query = f"""You are an intelligent keyword extraction assistant for a notes app.
Analyze the following note and extract:
1. The 5-10 most important keywords (single words or short phrases)
2. The 3 main topics or themes

Title: {note.title}
Content: {note.text}

Return your response in this exact JSON format:
{{
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "main_topics": ["topic1", "topic2", "topic3"]
}}

Only return the JSON, no additional text or explanation!"""

    return _call_llm_api(settings.llm_model, system_query)


@router.get("/notes/{id}/sentiment", tags=["Notes"])
def get_notes_sentiment(
    id: int,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Analyze the emotional sentiment and tone of a note using AI.

    Args:
        id: Note ID from URL path
        current_user: Authenticated user from JWT token
        crud: NoteCRUD instance for database operations

    Returns:
        dict: JSON response with sentiment analysis (positive/negative/neutral) and confidence

    Raises:
        HTTPException: 403 if user doesn't have access to the note
        HTTPException: 404 if note is not found
        HTTPException: 503 if LLM service is unavailable
    """
    note, user = crud.get_note_by_id(id)
    _check_note_access(note, current_user)

    system_query = f"""You are an intelligent sentiment analysis assistant for a notes app.
Analyze the emotional tone and sentiment of the following note.

Title: {note.title}
Content: {note.text}

Provide:
1. Overall sentiment: positive, negative, or neutral
2. Confidence score: 0.0 to 1.0 (how confident you are in this assessment)
3. Dominant emotions: list 1-3 emotions present (e.g., joy, sadness, anger, fear, excitement, calm, etc.)
4. Brief explanation (1 sentence)

Return your response in this exact JSON format:
{{
  "sentiment": "positive|negative|neutral",
  "confidence": 0.85,
  "emotions": ["emotion1", "emotion2"],
  "explanation": "Brief explanation here"
}}

Only return the JSON, no additional text!"""

    return _call_llm_api(settings.llm_model, system_query)


@router.get("/notes/{id}/related", tags=["Notes"])
def get_notes_related(
    id: int,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Find related notes based on content similarity using AI.

    Args:
        id: Note ID from URL path
        current_user: Authenticated user from JWT token
        crud: NoteCRUD instance for database operations

    Returns:
        dict: JSON response with related note IDs and similarity scores

    Raises:
        HTTPException: 403 if user doesn't have access to the note
        HTTPException: 404 if note is not found
        HTTPException: 503 if LLM service is unavailable
    """
    note, user = crud.get_note_by_id(id)
    _check_note_access(note, current_user)

    user_notes = crud.get_user_notes(current_user.id)

    if len(user_notes) <= 1:
        return {
            "related_notes": [],
            "message": "Not enough notes to find relationships",
        }

    other_notes = [n for n in user_notes if n["id"] != id]
    notes_context = "\n\n".join(
        [
            f"ID: {n['id']}\nTitle: {n['title']}\nContent: {n['text'][:200]}..."
            for n in other_notes[:10]
        ]
    )

    system_query = f"""You are an intelligent note relationship analyzer.
Analyze the following note and find which of the user's other notes are most related to it.

CURRENT NOTE:
Title: {note.title}
Content: {note.text}

OTHER NOTES:
{notes_context}

Identify the 3 most related notes based on:
- Topic similarity
- Shared concepts or themes
- Complementary information

Return your response in this exact JSON format:
{{
  "related_notes": [
    {{"id": 123, "similarity_score": 0.85, "reason": "Brief reason"}},
    {{"id": 456, "similarity_score": 0.72, "reason": "Brief reason"}}
  ]
}}

Similarity score should be between 0.0 and 1.0.
Only return the JSON, no additional text and in the language of the written note!"""

    return _call_llm_api(settings.llm_model, system_query)


@router.get("/notes/{id}", tags=["Notes"], response_model=NoteResponse)
def get_note(
    id: int,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Get a specific note by ID (requires authentication).

    Args:
        id: Note ID from URL path
        current_user: Authenticated user from JWT token
        crud: NoteCRUD instance for database operations

    Returns:
        dict: Note with specified ID and owner information

    Raises:
        HTTPException: 403 if user doesn't own the note and it's not public
        HTTPException: 404 if note is not found (from crud.get_note_by_id)
    """
    note, user = crud.get_note_by_id(id)
    _check_note_access(note, current_user)

    owner: UserResponse = UserResponse(
        id=user.id,
        username=user.username,
        createdAt=user.createdAt,
        updatedAt=user.updatedAt,
    )

    response = NoteResponse(
        id=note.id,
        title=note.title,
        text=note.text,
        isPublic=note.isPublic,
        createdAt=note.createdAt,
        updatedAt=note.updatedAt,
        owner=owner,
    )
    return response


@router.put("/notes/{id}", tags=["Notes"], response_model=dict)
def update_note(
    id: int,
    item: NoteBase,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Update a specific note by ID (requires authentication and ownership).

    Args:
        id: Note ID from URL path
        item: Updated note data from request body
        current_user: Authenticated user from JWT token
        crud: NoteCRUD instance for database operations

    Returns:
        dict: Updated note with new data and owner information

    Raises:
        HTTPException: 403 if user doesn't own the note
        HTTPException: 404 if note is not found (from crud.get_note_by_id)

    Note:
        Ownership cannot be transferred through this endpoint
    """
    return crud.update_note(id, item, current_user.id)


@router.delete("/notes/{id}", tags=["Notes"], response_model=dict)
def delete_note(
    id: int,
    current_user=Depends(get_current_user),
    crud: NoteCRUD = Depends(get_note_crud),
):
    """
    Delete a specific note by ID (requires authentication and ownership).

    Args:
        id: Note ID from URL path
        current_user: Authenticated user from JWT token
        crud: NoteCRUD instance for database operations

    Returns:
        dict: Success message

    Raises:
        HTTPException: 403 if user doesn't own the note
        HTTPException: 404 if note is not found (from crud.get_note_by_id)

    Note:
        Only the owner can delete their notes
    """
    return crud.delete_note(id, current_user.id)
