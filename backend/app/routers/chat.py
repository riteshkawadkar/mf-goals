"""POST /chat — scoped, grounded chatbot.

Architecture:
1. Scope guard: pre-classify the query as in-scope or out-of-scope.
2. If in-scope: retrieve relevant ReasoningObjects for this user.
3. Ground Claude strictly in those objects.
4. Return ChatResponse.
"""
from __future__ import annotations
import re
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth.deps import get_current_user
from app.models.db import User, ReasoningObject as DBReasoningObject
from app.schemas.api import ChatQuery, ChatResponse
from app.config import get_settings

router = APIRouter(prefix="/chat", tags=["Chat"])

# ─── Scope guard ─────────────────────────────────────────────────────────────

_OUT_OF_SCOPE_PATTERNS = re.compile(
    r"\b(stock tip|buy this|which fund to buy|should i buy|recommend a fund|"
    r"tax filing|itr|income tax return|general market|sensex will|nifty will|"
    r"best fund|top fund|market outlook|which sector|invest in|what to buy|"
    r"sell this|exit this|switch to)\b",
    re.I,
)

_IN_SCOPE_PATTERNS = re.compile(
    r"\b(my goal|my portfolio|my holding|my earmark|why is|how is|what is my|"
    r"explain my|tell me about my|my allocation|my sip|emergency|retirement|"
    r"education|p10|p50|p90|projection|stress|fragility|band|unallocated|"
    r"reasoning|how does this app|how does the engine|what does this mean)\b",
    re.I,
)


def is_in_scope(query: str) -> bool:
    if _OUT_OF_SCOPE_PATTERNS.search(query):
        return False
    if _IN_SCOPE_PATTERNS.search(query):
        return True
    # Default: attempt in-scope for short general questions
    return len(query.split()) <= 15


# ─── Retrieval ───────────────────────────────────────────────────────────────

def retrieve_reasoning_objects(
    user_id: str,
    query: str,
    subject_ref: str | None,
    db: Session,
    limit: int = 10,
) -> list[DBReasoningObject]:
    q = db.query(DBReasoningObject).filter(DBReasoningObject.user_id == user_id)

    if subject_ref:
        subject_match = q.filter(DBReasoningObject.subject_ref.contains(subject_ref)).limit(limit).all()
        if subject_match:
            return subject_match

    # Keyword-based retrieval from plain_language
    words = [w.lower() for w in query.split() if len(w) > 3]
    all_ros = q.all()
    scored = []
    for ro in all_ros:
        text = ro.plain_language.lower()
        score = sum(1 for w in words if w in text)
        if score > 0:
            scored.append((score, ro))
    scored.sort(key=lambda x: -x[0])
    return [ro for _, ro in scored[:limit]]


# ─── Chat endpoint ───────────────────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
def chat(
    body: ChatQuery,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = get_settings()

    if not is_in_scope(body.query):
        return ChatResponse(
            answer=(
                "I can only help with questions about your portfolio, goals, earmarks, "
                "and how this app's engine works. For investment recommendations or general "
                "market questions, please consult a SEBI-registered advisor."
            ),
            in_scope=False,
            refused=True,
            referenced_reasoning_object_ids=[],
            not_computed=False,
        )

    retrieved = retrieve_reasoning_objects(
        current_user.id, body.query, body.subject_ref, db
    )

    if not retrieved:
        return ChatResponse(
            answer=(
                "I don't have computed data to answer that question yet. "
                "Please run the engine (POST /engine/run) after uploading your CAS and setting up goals."
            ),
            in_scope=True,
            refused=False,
            referenced_reasoning_object_ids=[],
            not_computed=True,
        )

    if not settings.anthropic_api_key:
        # Fallback: return the most relevant reasoning object's plain_language
        best = retrieved[0]
        return ChatResponse(
            answer=best.plain_language,
            in_scope=True,
            refused=False,
            referenced_reasoning_object_ids=[ro.id for ro in retrieved],
            not_computed=False,
        )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        context_blocks = "\n\n".join(
            f"[{ro.type.upper()} | {ro.subject_ref}]\n{ro.plain_language}"
            for ro in retrieved
        )

        system_prompt = (
            "You are a portfolio analytics assistant for a goal-based mutual fund tracker. "
            "You answer ONLY based on the user's stored engine outputs and reasoning provided below. "
            "You NEVER give investment recommendations, name funds to buy, or issue buy/sell directives. "
            "You NEVER use words like 'recommend', 'should', 'best', 'optimal', 'suitable', 'buy', 'sell'. "
            "All language is descriptive, not prescriptive. "
            "If the provided context does not contain enough information to answer, say so honestly. "
            "Always reference the assumptions the answer depends on.\n\n"
            f"USER'S ENGINE OUTPUTS (use ONLY these to answer):\n{context_blocks}"
        )

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": body.query}],
        )
        answer = message.content[0].text

    except Exception as exc:
        answer = f"Chat service unavailable: {exc}"

    return ChatResponse(
        answer=answer,
        in_scope=True,
        refused=False,
        referenced_reasoning_object_ids=[ro.id for ro in retrieved],
        not_computed=False,
    )
