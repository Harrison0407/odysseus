"""Synthetic-only MarketMatch procurement prototype domain.

This module intentionally contains plain dictionaries and calculation helpers,
never ORM models.  It cannot mutate procurement, gates, handoffs or evidence.
"""
from decimal import Decimal, InvalidOperation

CM_PER_M = Decimal("100")
G_PER_KG = Decimal("1000")


def number(value, field="numeric value"):
    try:
        value = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"{field} must be numeric")
    if value < 0:
        raise ValueError(f"{field} cannot be negative")
    return value


def length_m(value, unit):
    value = number(value, "dimension")
    if unit == "cm": return value / CM_PER_M
    if unit == "m": return value
    raise ValueError("dimension_unit must be cm or m")


def weight_kg(value, unit):
    value = number(value, "weight")
    if unit == "kg": return value
    if unit == "g": return value / G_PER_KG
    raise ValueError("weight_unit must be kg or g")


def cbm(line):
    return (length_m(line["length"], line["dimension_unit"]) * length_m(line["width"], line["dimension_unit"]) *
            length_m(line["height"], line["dimension_unit"]) * number(line["carton_count"], "carton_count"))


def scenario():
    pi = [
        {"sku":"BCH-CHAIR","description":"Teak lounge chair","variant":"Natural","ordered_quantity":40,"unit_price":"220.00","uom":"EA"},
        {"sku":"BCH-TABLE","description":"Aluminum side table","variant":"White","ordered_quantity":20,"unit_price":"95.00","uom":"EA"},
        {"sku":"BCH-UMBRELLA","description":"Sun umbrella","variant":"Sand","ordered_quantity":12,"unit_price":"180.00","uom":"EA"},
        {"sku":"BCH-CUSHION","description":"Outdoor cushion","variant":"Aqua","ordered_quantity":60,"unit_price":"32.00","uom":"EA"},
    ]
    packing = [
        {"sku":"BCH-CHAIR","description":"Teak lounge chair","variant":"Natural","packed_quantity":40,"units_per_carton":2,"carton_count":20,"carton_range":"1-20","length":"120","width":"75","height":"95","dimension_unit":"cm","net_weight":"360","gross_weight":"400","weight_unit":"kg","lot":"LOT-01","marks":"MM/CHAIR","notes":"Synthetic sample"},
        {"sku":"BCH-TABLE","description":"Aluminum side table","variant":"White","packed_quantity":18,"units_per_carton":2,"carton_count":9,"carton_range":"21-29","length":"70","width":"70","height":"25","dimension_unit":"cm","net_weight":"126","gross_weight":"144","weight_unit":"kg","lot":"LOT-02","marks":"MM/TABLE","notes":"Synthetic sample"},
        {"sku":"BCH-UMBRELLA","description":"Sun umbrella","variant":"Ocean","packed_quantity":14,"units_per_carton":1,"carton_count":14,"carton_range":"30-43","length":"30","width":"30","height":"180","dimension_unit":"cm","net_weight":"112","gross_weight":"140","weight_unit":"kg","lot":"LOT-03","marks":"MM/UMB","notes":"Variant requires review"},
        {"sku":"BCH-LANTERN","description":"Rechargeable lantern","variant":"Black","packed_quantity":8,"units_per_carton":2,"carton_count":4,"carton_range":"44-47","length":"40","width":"30","height":"35","dimension_unit":"cm","net_weight":"24","gross_weight":"32","weight_unit":"kg","lot":"LOT-04","marks":"MM/LANTERN","notes":"Unknown SKU"},
    ]
    return {"pi_header":{"number":"PI-MM-2026-017","supplier_reference":"QD-8841","buyer_reference":"MM-DEMO-01","currency":"USD","date":"2026-07-15","total_value":"14500.00","status":"ISSUED","revision":"R1","source_document":"SYNTHETIC_PI_TEMPLATE.csv"}, "pi":pi,
            "packing_header":{"number":"PL-MM-2026-017","supplier":"Pacific Outdoor Works (Synthetic)","date":"2026-07-20","shipment_reference":"SHP-DEMO-017","destination":"Punta Cana","total_cartons":47,"total_pallets":3,"total_net_weight":"622","total_gross_weight":"716","total_cbm":"19.03","status":"DRAFT","revision":"R1"}, "packing":packing,
            "events":[], "reviews":{}, "revision":"R1", "packing_status":"DRAFT"}


def compare(pi, packing):
    by_key = {(x["sku"], x["variant"]): x for x in pi}
    pi_by_sku = {x["sku"]: x for x in pi}
    seen = set(); rows=[]
    for p in packing:
        key=(p["sku"],p["variant"]); source=by_key.get(key); codes=[]
        if not source:
            source=pi_by_sku.get(p["sku"])
            codes.append("VARIANT_MISMATCH" if source else "SKU_NOT_IN_PI")
        if source:
            seen.add((source["sku"],source["variant"]))
            ordered=number(source["ordered_quantity"]); packed=number(p["packed_quantity"])
            if packed < ordered: codes.append("QUANTITY_SHORT")
            if packed > ordered: codes.append("QUANTITY_OVER")
            # A carton count inconsistent with declared pack size needs review.
            if number(p["carton_count"]) * number(p["units_per_carton"]) != packed: codes.append("CARTON_COUNT_MISMATCH")
            estimate=packed*number(source["unit_price"]); ordered_value=ordered*number(source["unit_price"])
        else: ordered=Decimal(0); packed=number(p["packed_quantity"]); estimate=ordered_value=Decimal(0)
        try: volume=cbm(p); net=weight_kg(p["net_weight"], p["weight_unit"]); gross=weight_kg(p["gross_weight"],p["weight_unit"])
        except ValueError: codes.append("DIMENSION_REVIEW"); volume=net=gross=Decimal(0)
        rows.append({"sku":p["sku"],"variant":p["variant"],"description":p["description"],"ordered":ordered,"packed":packed,"difference":packed-ordered,"percent":((packed-ordered)/ordered*100 if ordered else Decimal(0)),"ordered_value":ordered_value,"packed_value":estimate,"value_difference":estimate-ordered_value,"cartons":number(p["carton_count"]),"net":net,"gross":gross,"cbm":volume,"codes":codes or ["MATCH"]})
    for key, source in by_key.items():
        if key not in seen:
            rows.append({"sku":source["sku"],"variant":source["variant"],"description":source["description"],"ordered":number(source["ordered_quantity"]),"packed":Decimal(0),"difference":-number(source["ordered_quantity"]),"percent":Decimal(-100),"ordered_value":number(source["ordered_quantity"])*number(source["unit_price"]),"packed_value":Decimal(0),"value_difference":-(number(source["ordered_quantity"])*number(source["unit_price"])),"cartons":Decimal(0),"net":Decimal(0),"gross":Decimal(0),"cbm":Decimal(0),"codes":["MISSING_FROM_PACKING_LIST"]})
    totals={k:sum((r[k] for r in rows),Decimal(0)) for k in ("ordered","packed","difference","ordered_value","packed_value","value_difference","cartons","net","gross","cbm")}
    all_codes=[c for r in rows for c in r["codes"]]
    status="MATCHED" if all_codes==["MATCH"]*len(rows) else ("NOT_APPROVABLE" if any(c in all_codes for c in ("SKU_NOT_IN_PI","MISSING_FROM_PACKING_LIST","VARIANT_MISMATCH")) else "REQUIRES_REVIEW")
    summary={"matched":all_codes.count("MATCH"),"shortages":all_codes.count("QUANTITY_SHORT"),"overages":all_codes.count("QUANTITY_OVER"),"missing":all_codes.count("MISSING_FROM_PACKING_LIST"),"unknown":all_codes.count("SKU_NOT_IN_PI"),**totals,"status":status}
    return rows, summary
