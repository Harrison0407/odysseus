import threading

_state = threading.local()


class CurrentUserMiddleware:
    """Makes the request user available to signal handlers / model save()
    hooks that need to attach an actor to an AuditEvent without threading
    the user through every call site."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _state.user = getattr(request, "user", None)
        try:
            return self.get_response(request)
        finally:
            _state.user = None


def get_current_user():
    return getattr(_state, "user", None)
