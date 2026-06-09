import os
from unittest.mock import patch
from fastapi.testclient import TestClient
from starlette.requests import Request

# Import the FastAPI app and CORS functions
from retrieval import api_service
from retrieval.api_service import app


def test_cors_origins_local_development_defaults() -> None:
    # Test that when tenant is 'local', both localhost and 127.0.0.1 are allowed
    with patch.dict(os.environ, {"CODESEEK_TENANT_ID": "local", "CODESEEK_CORS_ORIGINS": "http://localhost:5173"}):
        origins = api_service._cors_origins()
        assert "http://localhost:5173" in origins
        assert "http://127.0.0.1:5173" in origins

    # Test that we respect other origins too
    with patch.dict(os.environ, {"CODESEEK_TENANT_ID": "local", "CODESEEK_CORS_ORIGINS": "http://localhost:5173,http://mycustomlocal.com"}):
        origins = api_service._cors_origins()
        assert "http://localhost:5173" in origins
        assert "http://127.0.0.1:5173" in origins
        assert "http://mycustomlocal.com" in origins


def test_enforce_https_middleware_bypasses_options() -> None:
    # Test that preflight OPTIONS requests bypass enforce_https_middleware even if ENFORCE_HTTPS is True
    client = TestClient(app)

    with patch.object(api_service, "ENFORCE_HTTPS", True), \
         patch.object(api_service, "TRUST_X_FORWARDED_PROTO", False), \
         patch.dict(os.environ, {"CODESEEK_CORS_ORIGINS": "http://localhost:5173"}):
        
        # Make an OPTIONS request from an allowed origin
        headers = {
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-app-encryption-key",
        }
        
        # Test an endpoint that usually enforces HTTPS (like /api/v1/sessions)
        response = client.options("/api/v1/sessions", headers=headers)
        
        # CORS preflight should succeed with 200 OK and correct headers
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert response.headers.get("access-control-allow-credentials") == "true"


def test_cors_preflight_succeeds_for_local_origins() -> None:
    client = TestClient(app)

    # When requesting from http://127.0.0.1:5173, it should match the allowed CORS origins and return 200
    with patch.object(api_service, "ENFORCE_HTTPS", False), \
         patch.dict(os.environ, {"CODESEEK_CORS_ORIGINS": "http://localhost:5173"}):
        
        for origin in ["http://localhost:5173", "http://127.0.0.1:5173"]:
            headers = {
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type,x-app-encryption-key",
            }
            response = client.options("/api/v1/health", headers=headers)
            assert response.status_code == 200
            assert response.headers.get("access-control-allow-origin") == origin
            assert response.headers.get("access-control-allow-credentials") == "true"
