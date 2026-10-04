from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from comics.decorators import admin_site_required
from comics.models import Edition, Publishing


# Used only by the admin (fill_release_date.js): same access rule as the admin.
@admin_site_required
@require_GET
def get_publishing_date(request: HttpRequest, publishing_id: int) -> JsonResponse:
    try:
        publishing = Publishing.objects.get(id=publishing_id)
        return JsonResponse({"date": publishing.date.strftime("%d/%m/%Y")})
    except Publishing.DoesNotExist:
        return JsonResponse({"date": None})


# Used only by the admin collection form (collection_form_events.js): same access rule as the admin.
@admin_site_required
@require_GET
def get_comics_by_publishing(request: HttpRequest, publishing_id: int) -> JsonResponse:
    # JSON 404 (not the HTML page): the script parses every response as JSON.
    if not Publishing.objects.filter(pk=publishing_id).exists():
        return JsonResponse({"results": []}, status=404)
    editions = (
        Edition.objects.filter(publishing_id=publishing_id)
        .select_related("publishing")
        .prefetch_related("publishing__editorials")
        .order_by("number", "variant")
    )
    data = [{"id": edition.id, "text": str(edition)} for edition in editions]
    return JsonResponse({"results": data})
