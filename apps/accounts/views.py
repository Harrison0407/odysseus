from django.contrib.auth.views import LoginView

from apps.core.ratelimit import is_rate_limited, record_attempt

LOGIN_MAX_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300


class RateLimitedLoginView(LoginView):
    """Same as Django's built-in LoginView, plus a per-IP limit on failed
    attempts (spec: "rate limiting or reasonable protection" on the login
    endpoint — see docs/KNOWN_LIMITATIONS.md, apps.core.ratelimit)."""

    def _rate_limit_key(self):
        return f"login-attempts:{self.request.META.get('REMOTE_ADDR', 'unknown')}"

    def post(self, request, *args, **kwargs):
        if is_rate_limited(self._rate_limit_key(), limit=LOGIN_MAX_ATTEMPTS, window_seconds=LOGIN_WINDOW_SECONDS):
            form = self.get_form()
            form.add_error(None, "Demasiados intentos fallidos de inicio de sesión. Intente de nuevo en unos minutos.")
            return self.render_to_response(self.get_context_data(form=form))
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        record_attempt(self._rate_limit_key(), window_seconds=LOGIN_WINDOW_SECONDS)
        return super().form_invalid(form)
