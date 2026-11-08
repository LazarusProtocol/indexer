from __future__ import annotations
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView,)

urlpatterns = [
    path("admin/", admin.site.urls),
    # OpenAPI 3.0 Schema, Swagger UI, and ReDoc Documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # Versioned v1 API Endpoints
    path("api/v1/", include("indexer.urls")),
]
