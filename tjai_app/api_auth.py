"""Authentication for TJAI's non-browser REST control plane."""

from functools import wraps
import hmac
import ipaddress

from django.http import JsonResponse


REST_API_KEY = "gmail_addon_api_key"


def _is_direct_loopback(request) -> bool:
    """Trust same-host callers, but never requests forwarded by a proxy."""
    if request.META.get("HTTP_X_FORWARDED_FOR"):
        return False
    try:
        return ipaddress.ip_address(request.META.get("REMOTE_ADDR", "")).is_loopback
    except ValueError:
        return False


def _auth_failure(request):
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return JsonResponse({"error": "Authorization required"}, status=401)

    from .models import SysConfig

    expected = SysConfig.objects.filter(key=REST_API_KEY).values_list(
        "value", flat=True
    ).first()
    if not expected:
        return JsonResponse({"error": "REST API key not configured"}, status=503)
    if not hmac.compare_digest(auth_header[7:], expected):
        return JsonResponse({"error": "Invalid token"}, status=403)
    return None


def rest_api_auth_required(view_func):
    """Allow an authenticated session, direct loopback, or REST bearer."""

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated) or _is_direct_loopback(request):
            return view_func(request, *args, **kwargs)
        failure = _auth_failure(request)
        if failure is not None:
            return failure
        return view_func(request, *args, **kwargs)

    return wrapped
