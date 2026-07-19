from django import forms
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from apps.accounts.models import UserRole
from apps.workflow import services as wfsvc

from .models import Building, BuildingFamily, Unit


def _is_management(user) -> bool:
    return UserRole.objects.filter(user=user, role__is_management=True, is_active=True).exists()


def _accessible_project_ids(user):
    return set(user.project_access.values_list("project_id", flat=True))


def _scope_to_accessible_projects(request, queryset, project_lookup):
    if _is_management(request.user):
        return queryset
    return queryset.filter(**{f"{project_lookup}__in": _accessible_project_ids(request.user)})


@login_required
def building_family_list(request):
    organization = request.user.profile.organization
    families = BuildingFamily.objects.filter(
        project__organization=organization, is_active=True
    ).select_related("project").order_by("display_name")
    families = _scope_to_accessible_projects(request, families, "project_id")
    rows = [
        {
            "family": f,
            "building_count": f.buildings.count(),
            "unit_count": Unit.objects.filter(building__family=f).count(),
        }
        for f in families
    ]
    return render(request, "projects/family_list.html", {"rows": rows})


@login_required
def building_family_detail(request, pk):
    organization = request.user.profile.organization
    family = get_object_or_404(
        BuildingFamily.objects.select_related("project"), pk=pk, project__organization=organization,
    )
    if not wfsvc.user_can_access_project(request.user, family.project):
        raise Http404
    buildings = family.buildings.order_by("building_number", "name")
    return render(request, "projects/family_detail.html", {"family": family, "buildings": buildings})


@login_required
def building_detail(request, pk):
    organization = request.user.profile.organization
    building = get_object_or_404(
        Building.objects.select_related("project", "family"), pk=pk, project__organization=organization,
    )
    if not wfsvc.user_can_access_project(request.user, building.project):
        raise Http404
    floors = building.floors.prefetch_related("units").order_by("level", "name")
    areas = building.areas.all()
    from apps.drawings.models import Drawing
    drawings = Drawing.objects.filter(building=building, is_current=True).order_by("discipline", "title")
    return render(request, "projects/building_detail.html", {
        "building": building, "floors": floors, "areas": areas, "drawings": drawings,
    })


class UnitSearchForm(forms.Form):
    q = forms.CharField(required=False, label="Buscar (código permanente o número de apartamento)")
    family = forms.ModelChoiceField(queryset=BuildingFamily.objects.none(), required=False, label="Familia de edificio")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["family"].queryset = (
            BuildingFamily.objects.filter(project__organization=organization, is_active=True).order_by("display_name")
            if organization else BuildingFamily.objects.none()
        )
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control form-control-sm")


@login_required
def unit_search(request):
    organization = request.user.profile.organization
    form = UnitSearchForm(request.GET or None, organization=organization)
    units = Unit.objects.select_related("building__family", "building__project", "floor").filter(
        building__project__organization=organization
    )
    units = _scope_to_accessible_projects(request, units, "building__project_id")
    if form.is_valid():
        q = form.cleaned_data.get("q")
        if q:
            units = units.filter(permanent_code__icontains=q) | units.filter(apartment_number__icontains=q)
        family = form.cleaned_data.get("family")
        if family:
            units = units.filter(building__family=family)
    units = units.order_by("building__name", "name")[:200]
    return render(request, "projects/unit_search.html", {"form": form, "units": units})


@login_required
def unit_detail(request, pk):
    organization = request.user.profile.organization
    unit = get_object_or_404(
        Unit.objects.select_related("building__family", "building__project", "floor"),
        pk=pk, building__project__organization=organization,
    )
    if not wfsvc.user_can_access_project(request.user, unit.building.project):
        raise Http404
    from apps.drawings.models import Drawing
    drawings = Drawing.objects.filter(unit=unit, is_current=True).order_by("discipline", "title")
    return render(request, "projects/unit_detail.html", {"unit": unit, "drawings": drawings})
