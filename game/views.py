from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from .engine import get_state, process, _load_world, _describe_room


@require_GET
def terminal(request: HttpRequest) -> HttpResponse:
    st = get_state(request.session, case_id="case001")
    world = _load_world("case001")

    # First render: show room
    if not st.get("last_out"):
        banner = _describe_room(world, st)
        st["last_out"] = banner
        request.session["sp_state"] = st
        request.session.modified = True
    else:
        banner = st["last_out"]

    return render(request, "game/terminal.html", {"banner": banner})


@require_POST
def command(request: HttpRequest) -> JsonResponse:
    raw = (request.POST.get("cmd") or "").strip()
    out, st = process(request.session, raw, case_id="case001")
    return JsonResponse({"ok": True, "out": out, "won": bool(st["flags"].get("won"))})


@require_POST
def restart(request: HttpRequest) -> JsonResponse:
    request.session.pop("sp_state", None)
    st = get_state(request.session, case_id="case001")
    world = _load_world("case001")
    out = _describe_room(world, st)
    return JsonResponse({"ok": True, "out": out})
