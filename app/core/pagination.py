"""Pagination for every collection endpoint (DIF-06, standard in docs/API_STANDARDS.md).

Any route declared with ``response_model=list[...]`` on a router built with
``route_class=PaginatedRoute`` gets, without further code:

    ?limit=   page size, default 100, at most 500 (larger values are refused)
    ?offset=  rows to skip, default 0

and these response headers, so the body stays a plain JSON array (existing
clients keep working):

    X-Total-Count   rows across all pages
    X-Page-Limit    the page size applied
    X-Page-Offset   the offset applied
    Link            <...>; rel="next" / rel="prev" when those pages exist

Ordering is the endpoint's own ``ORDER BY``; every list query must end with
a deterministic order (a unique column last) so pages never overlap or skip.

Pages are currently cut after the endpoint has built its list; pushing
LIMIT/OFFSET into SQL for the largest tables is a later optimisation that
doesn't change this contract (TODO.md DIF-06).
"""

import inspect
import typing
from collections.abc import Callable
from functools import wraps
from typing import Annotated

from fastapi import Depends, Query, Request, Response
from fastapi.routing import APIRoute

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


class PageParams:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="Page size")] = DEFAULT_PAGE_SIZE,
        offset: Annotated[int, Query(ge=0, le=1_000_000, description="Rows to skip")] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


def _link(request: Request, limit: int, offset: int) -> str:
    return str(request.url.include_query_params(limit=limit, offset=offset))


def apply_page(rows: list, page: PageParams, request: Request, response: Response) -> list:
    total = len(rows)
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page-Limit"] = str(page.limit)
    response.headers["X-Page-Offset"] = str(page.offset)
    links = []
    if page.offset + page.limit < total:
        links.append(f'<{_link(request, page.limit, page.offset + page.limit)}>; rel="next"')
    if page.offset > 0:
        links.append(f'<{_link(request, page.limit, max(page.offset - page.limit, 0))}>; rel="prev"')
    if links:
        response.headers["Link"] = ", ".join(links)
    return rows[page.offset : page.offset + page.limit]


def _is_list_model(model) -> bool:
    return typing.get_origin(model) is list


def paginated(endpoint: Callable) -> Callable:
    """Wrap a list endpoint: same parameters plus the page, request and
    response, which FastAPI injects; the result is cut to the page."""
    sig = inspect.signature(endpoint)
    # Resolve annotations against the endpoint's own module, not this one.
    hints = typing.get_type_hints(endpoint, include_extras=True)
    is_async = inspect.iscoroutinefunction(endpoint)

    @wraps(endpoint)
    async def wrapper(*, _page: PageParams, _request: Request, _response: Response, **kwargs):
        result = await endpoint(**kwargs) if is_async else endpoint(**kwargs)
        return apply_page(list(result), _page, _request, _response)

    # FastAPI passes every parameter by name, so all become keyword-only.
    original = [
        p.replace(kind=inspect.Parameter.KEYWORD_ONLY, annotation=hints.get(name, p.annotation))
        for name, p in sig.parameters.items()
        if p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]
    extra = [
        inspect.Parameter("_page", inspect.Parameter.KEYWORD_ONLY, annotation=Annotated[PageParams, Depends()]),
        inspect.Parameter("_request", inspect.Parameter.KEYWORD_ONLY, annotation=Request),
        inspect.Parameter("_response", inspect.Parameter.KEYWORD_ONLY, annotation=Response),
    ]
    wrapper.__signature__ = sig.replace(parameters=original + extra, return_annotation=inspect.Signature.empty)
    return wrapper


class PaginatedRoute(APIRoute):
    def __init__(self, path: str, endpoint: Callable, **kwargs) -> None:
        if _is_list_model(kwargs.get("response_model")):
            endpoint = paginated(endpoint)
        super().__init__(path, endpoint, **kwargs)
