from collections import defaultdict
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from .models import InventoryLot, InventoryMovement, WarehouseLocation


def _on_hand_by_location_and_item():
    """On-hand is always *derived* from the movement ledger, never stored
    directly (core principle 4.5). Nets to_location (+) against
    from_location (-) for every posted movement."""
    balances = defaultdict(Decimal)
    movements = InventoryMovement.objects.select_related("lot__item", "to_location", "from_location")
    for m in movements:
        if m.to_location_id:
            balances[(m.to_location_id, m.lot.item_id)] += m.quantity
        if m.from_location_id:
            balances[(m.from_location_id, m.lot.item_id)] -= m.quantity
    return balances


@login_required
def location_list(request):
    locations = WarehouseLocation.objects.select_related("zone__site").filter(is_active=True)
    balances = _on_hand_by_location_and_item()

    rows = []
    for loc in locations:
        items = {}
        for (loc_id, item_id), qty in balances.items():
            if loc_id == loc.id and qty != 0:
                items[item_id] = qty
        if items:
            from apps.items.models import Item
            for item in Item.objects.filter(id__in=items.keys()):
                rows.append({"location": loc, "item": item, "quantity": items[item.id]})
        else:
            rows.append({"location": loc, "item": None, "quantity": None})

    return render(request, "inventory/locations.html", {"rows": rows})


@login_required
def lot_detail(request, pk):
    lot = get_object_or_404(InventoryLot.objects.select_related("item"), pk=pk)
    movements = lot.movements.select_related("from_location", "to_location", "posted_by").order_by("posted_at")
    return render(request, "inventory/lot_detail.html", {"lot": lot, "movements": movements})
