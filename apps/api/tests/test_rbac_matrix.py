from fastapi.routing import APIRoute

from app.core.auth import PERMISSIONS, principal
from app.main import app

PUBLIC_API_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/invites/{token}",
    "/api/v1/invites/{token}/accept",
}


def _has_auth_dependency(route: APIRoute) -> bool:
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        call = dependency.call
        if call is principal or (
            getattr(call, "__module__", "") == "app.core.auth"
            and getattr(call, "__name__", "") == "dependency"
        ):
            return True
        pending.extend(dependency.dependencies)
    return False


def test_every_non_public_api_route_has_server_side_authentication():
    missing = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1"):
            continue
        if route.path not in PUBLIC_API_PATHS and not _has_auth_dependency(route):
            missing.append(f"{','.join(sorted(route.methods or []))} {route.path}")
    assert missing == []


def test_builtin_permission_matrix_remains_least_privilege():
    assert PERMISSIONS["Viewer"] == {
        "overview.read",
        "pipelines.read",
        "deliveries.read",
    }
    assert "sources.write" not in PERMISSIONS["Ops"]
    assert "destinations.write" not in PERMISSIONS["Ops"]
    assert "settings.manage" not in PERMISSIONS["Ops"]
    assert "users.manage" not in PERMISSIONS["Ops"]
    assert PERMISSIONS["Admin"] == {"*"}
