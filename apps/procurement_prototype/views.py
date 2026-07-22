import csv
from datetime import datetime, timezone
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from .domain import compare, scenario

AREAS = ["overall flow", "A2", "A3", "A4", "A5 Packing List", "PI comparison", "A6", "navigation", "terminology", "reports", "visual design"]

def _state(request):
    state=request.session.get("marketmatch_prototype")
    if not state:
        state=scenario(); request.session["marketmatch_prototype"]=state
    return state

def _allowed():
    return settings.PROCUREMENT_PROTOTYPE_ENABLED

def _context(request):
    if not _allowed(): raise Http404("Prototype disabled")
    state=_state(request); rows, summary=compare(state["pi"],state["packing"])
    return state, rows, summary

def dashboard(request):
    state, rows, summary=_context(request)
    return render(request,"procurement_prototype/dashboard.html", {"state":state,"summary":summary,"areas":AREAS})

def gate(request, gate):
    if gate not in range(2,7): raise Http404
    state, rows, summary=_context(request)
    return render(request,"procurement_prototype/gate.html", {"state":state,"summary":summary,"rows":rows,"gate":gate,"areas":AREAS})

def comparison(request):
    state, rows, summary=_context(request)
    return render(request,"procurement_prototype/comparison.html", {"state":state,"rows":rows,"summary":summary,"areas":AREAS})

@require_POST
def action(request):
    state, _, _=_context(request); action=request.POST.get("action", "")
    comment=request.POST.get("comment", "").strip()[:500]
    if action in {"freeze", "request_change", "acknowledge", "supplier_correction", "accept_tolerance", "reject", "approve"}:
        changes={"freeze":"FROZEN (prototype)","request_change":"CHANGE_REQUESTED (prototype)","acknowledge":"ACKNOWLEDGED", "supplier_correction":"SUPPLIER_CORRECTION_REQUESTED", "accept_tolerance":"ACCEPTED_WITHIN_TOLERANCE", "reject":"REJECTED", "approve":"APPROVED (prototype only)"}
        state["packing_status"]=changes[action]
        state["events"].append({"actor":"Demo Procurement Team","timestamp":datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),"prior":"DRAFT","new":changes[action],"comment":comment or "Synthetic prototype action", "code":request.POST.get("code", "GENERAL"),"revision":state["revision"]})
    elif action == "new_revision":
        state["revision"]="R2"; state["packing_header"]["revision"]="R2"; state["events"].append({"actor":"Demo Supplier","timestamp":datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),"prior":"R1","new":"R2 selected","comment":comment or "Corrected synthetic revision", "code":"REVISION", "revision":"R2"})
    elif action == "review":
        area=request.POST.get("area"); decision=request.POST.get("decision")
        if area in AREAS and decision in {"APPROVE","APPROVE_WITH_CHANGES","REJECT","DEFER"}: state["reviews"][area]={"decision":decision,"comment":comment,"actor":"Team reviewer"}
    request.session["marketmatch_prototype"]=state; request.session.modified=True
    return redirect(request.POST.get("next") or "procurement_prototype:dashboard")

def export_csv(request, kind):
    state, rows, summary=_context(request)
    response=HttpResponse(content_type="text/csv"); response["Content-Disposition"]=f'attachment; filename="synthetic_{kind}.csv"'
    w=csv.writer(response)
    if kind == "packing":
        w.writerow(state["packing"][0].keys()); [w.writerow(x.values()) for x in state["packing"]]
    elif kind == "pi":
        w.writerow(state["pi"][0].keys()); [w.writerow(x.values()) for x in state["pi"]]
    elif kind in {"comparison","discrepancies"}:
        w.writerow(["sku","variant","ordered","packed","difference","codes","cartons","net_kg","gross_kg","cbm"])
        for r in rows:
            if kind=="comparison" or r["codes"] != ["MATCH"]: w.writerow([r["sku"],r["variant"],r["ordered"],r["packed"],r["difference"],"|".join(r["codes"]),r["cartons"],r["net"],r["gross"],r["cbm"]])
    else: raise Http404
    return response
