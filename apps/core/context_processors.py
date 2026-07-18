def org_context(request):
    """Exposes the active organization/project to every template without
    every view needing to pass it explicitly."""
    return {
        "active_project": getattr(request, "active_project", None),
    }
