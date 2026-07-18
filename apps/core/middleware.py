from apps.accounts.models import Organization, UserProfile


class EnsureProfileMiddleware:
    """Single-organization pilot convenience: if an authenticated user
    (typically a superuser created via createsuperuser, outside the normal
    seed command) has no UserProfile yet, attach one to the first
    Organization so views that assume `request.user.profile` do not 500.
    Business users are always created with a profile by the seed command;
    this only covers the admin-bootstrap edge case."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not hasattr(user, "profile"):
            organization = Organization.objects.first()
            if organization is not None:
                UserProfile.objects.get_or_create(user=user, defaults={"organization": organization})
                if hasattr(user, "_profile_cache"):
                    del user._profile_cache
        return self.get_response(request)
