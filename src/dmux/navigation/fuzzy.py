"""Lightweight fuzzy matching for sessions, windows, and panes (no external deps)."""

from __future__ import annotations

from difflib import SequenceMatcher

from dmux.schemas import FuzzyMatch, SessionDTO


def score(query: str, text: str) -> float:
    q = query.strip().lower()
    t = text.lower()
    if not q:
        return 1.0
    if q in t:
        return 0.9 + 0.1 * (len(q) / max(len(t), 1))
    return SequenceMatcher(a=q, b=t).ratio()


def fuzzy_targets(
    sessions: tuple[SessionDTO, ...],
    query: str,
    *,
    limit: int = 20,
) -> tuple[FuzzyMatch, ...]:
    matches: list[FuzzyMatch] = []
    for session in sessions:
        s = score(query, session.name)
        matches.append(
            FuzzyMatch(
                kind="session",
                session_name=session.name,
                window_name=None,
                pane_title=None,
                score=s,
            )
        )
        for window in session.windows:
            ws = score(query, f"{session.name}/{window.name}")
            matches.append(
                FuzzyMatch(
                    kind="window",
                    session_name=session.name,
                    window_name=window.name,
                    pane_title=None,
                    score=ws,
                    window_id=window.window_id,
                )
            )
            for pane in window.panes:
                label = f"{session.name}/{window.name}/{pane.title}"
                ps = score(query, label)
                matches.append(
                    FuzzyMatch(
                        kind="pane",
                        session_name=session.name,
                        window_name=window.name,
                        pane_title=pane.title,
                        score=ps,
                        pane_id=pane.pane_id,
                        window_id=window.window_id,
                    )
                )
    matches.sort(key=lambda m: m.score, reverse=True)
    return tuple(matches[:limit])
