"""Classify the live LinkedIn page into a :class:`PageState`.

The browser is the source of truth: LinkedIn can bounce us to a login, an
authwall, or a checkpoint at any moment, so control loops re-read the page
rather than trust a remembered state. This module is that single, pure
classifier. It reads only the URL *path* — never the query string, whose
``?session_redirect=…%2Ffeed%2F`` once fooled a whole-URL substring check into
thinking an unauthenticated login page was the feed.
"""
from __future__ import annotations

import functools
from enum import Enum
from urllib.parse import urlsplit

from playwright.sync_api import Page

from linkedin_cli.exceptions import IllegalPageTransition


class PageState(str, Enum):
    """Where the browser currently is. Values match the auth machine's state ids."""

    CHECKPOINT = "checkpoint"
    LOGIN = "login"
    AUTHWALL = "authwall"
    FEED = "feed"
    PROFILE = "profile"
    MESSAGING = "messaging"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


# Path prefix → state, in match order. Checkpoint is first: it can surface under
# any flow and must win over whatever path it decorates.
_ROUTES: list[tuple[str, PageState]] = [
    ("/checkpoint", PageState.CHECKPOINT),
    ("/login", PageState.LOGIN),
    ("/authwall", PageState.AUTHWALL),
    ("/feed", PageState.FEED),
    ("/in/", PageState.PROFILE),
    ("/messaging", PageState.MESSAGING),
    ("/404", PageState.NOT_FOUND),
]


def classify_page(page: Page) -> PageState:
    """Return the :class:`PageState` of the live page, judged by URL path only."""
    path = urlsplit(page.url).path
    for prefix, state in _ROUTES:
        if path.startswith(prefix):
            return state
    return PageState.UNKNOWN


def transition(*, when: PageState, then: PageState | set[PageState]):
    """Declare a page-state transition as a contract on the action that performs it.

    The decorated action takes a session (anything exposing a live ``page``) and
    drives the browser. The wrapper enforces, against the *live* page:

    - **precondition** — the page must be in ``when`` before the action runs;
    - **postcondition** — the action must leave the page in one of ``then``.

    Either violation raises :class:`IllegalPageTransition`. Enforcing the
    postcondition *after* the action (re-reading the page) is what a held-state
    FSM cannot do: the destination is observed, not declared up front, and may be
    one of several (login → feed *or* checkpoint). Returns the resulting state.

    The action's contract is introspectable as ``fn.when`` / ``fn.then`` so a
    driver can build its dispatch table from the decorated actions themselves.
    """
    targets = frozenset({then} if isinstance(then, PageState) else then)

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(session, *args, **kwargs) -> PageState:
            before = classify_page(session.page)
            if before is not when:
                raise IllegalPageTransition(
                    f"{fn.__name__}() requires page state {when.value!r}, "
                    f"but page is {before.value!r} ({session.page.url})"
                )
            fn(session, *args, **kwargs)
            after = classify_page(session.page)
            if after not in targets:
                expected = sorted(t.value for t in targets)
                raise IllegalPageTransition(
                    f"{fn.__name__}() from {when.value!r} produced {after.value!r}; "
                    f"expected one of {expected} ({session.page.url})"
                )
            return after

        wrapper.when = when
        wrapper.then = targets
        return wrapper

    return decorator


class PageFlow:
    """A page-state flow: a set of ``@transition`` actions plus one generic driver.

    Declare a flow with a goal state, then attach its transitions as decorated
    actions — each registers under its precondition (``when``). :meth:`run` is the
    observe→act loop, written once for every flow: re-read the live page, dispatch
    to the action for that state, repeat until the goal. There is no per-flow loop
    and no hand-built dispatch table — a flow *is* its annotated transitions.
    """

    def __init__(self, name: str, *, goal: PageState):
        self.name = name
        self.goal = goal
        self._actions: dict[PageState, object] = {}

    def transition(self, *, when: PageState, then: PageState | set[PageState]):
        """Decorator: enforce the action's contract (via :func:`transition`) and
        register it under ``when`` so :meth:`run` can dispatch to it."""
        contract = transition(when=when, then=then)  # the module-level contract decorator

        def register(fn):
            if when in self._actions:
                raise ValueError(
                    f"{self.name!r} flow already has a transition from {when.value!r}"
                )
            self._actions[when] = contract(fn)
            return self._actions[when]

        return register

    def run(self, session, *, max_hops: int = 8) -> PageState:
        """Drive *session* to :attr:`goal`. Raise :class:`IllegalPageTransition`
        if a page has no registered action or the goal isn't reached in time."""
        for _ in range(max_hops):
            state = classify_page(session.page)
            if state is self.goal:
                return state
            action = self._actions.get(state)
            if action is None:
                raise IllegalPageTransition(
                    f"{self.name!r} flow: no transition from {state.value!r} "
                    f"({session.page.url})"
                )
            action(session)
        raise IllegalPageTransition(
            f"{self.name!r} flow: did not reach {self.goal.value!r} within "
            f"{max_hops} hops (stuck at {classify_page(session.page).value!r})"
        )
