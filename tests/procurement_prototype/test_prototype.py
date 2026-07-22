from decimal import Decimal
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from apps.audit.models import EvidenceBundle, EvidenceItem
from apps.governance.models import ChangeRequest, RiskFlag
from apps.procurement.models import ProcurementPackage
from apps.procurement_gates.models import GateAttempt, GateDecision, GateEvaluation
from apps.procurement_prototype.domain import cbm, compare, scenario, weight_kg
from apps.workflow.models import Handoff

class ComparisonTests(SimpleTestCase):
    def setUp(self): self.data=scenario(); self.rows, self.summary=compare(self.data["pi"],self.data["packing"])
    def test_synthetic_scenario_has_expected_discrepancies(self):
        codes={code for row in self.rows for code in row["codes"]}; self.assertTrue({"MATCH","QUANTITY_SHORT","QUANTITY_OVER","SKU_NOT_IN_PI","MISSING_FROM_PACKING_LIST","VARIANT_MISMATCH"}.issubset(codes))
    def test_totals_and_cbm_are_calculated(self):
        self.assertEqual(self.summary["cartons"], Decimal("47")); self.assertGreater(self.summary["cbm"], 0); self.assertEqual(cbm(self.data["packing"][0]), Decimal("17.1000"))
    def test_units_and_overall_status(self):
        self.assertEqual(weight_kg("1000","g"), Decimal("1")); self.assertEqual(self.summary["status"],"NOT_APPROVABLE")
        with self.assertRaises(ValueError): weight_kg("1", "lb")

@override_settings(PROCUREMENT_PROTOTYPE_ENABLED=True)
class PrototypeViewsTests(TestCase):
    def test_dashboard_and_gates_render(self):
        for name, args in [("procurement_prototype:dashboard",()), ("procurement_prototype:comparison",()), ("procurement_prototype:gate",(2,)), ("procurement_prototype:gate",(3,)), ("procurement_prototype:gate",(4,)), ("procurement_prototype:gate",(5,)), ("procurement_prototype:gate",(6,))]:
            response=self.client.get(reverse(name,args=args)); self.assertEqual(response.status_code,200); self.assertContains(response,"PROTOTYPE — NOT FOR PRODUCTION USE")
    def test_export_and_session_only_action(self):
        response=self.client.get(reverse("procurement_prototype:export",args=("comparison",))); self.assertEqual(response.status_code,200); self.assertEqual(response["Content-Type"],"text/csv")
        production_models=(ProcurementPackage, GateAttempt, GateEvaluation, GateDecision, EvidenceBundle, EvidenceItem, Handoff, ChangeRequest, RiskFlag)
        before={model: model.objects.count() for model in production_models}
        response=self.client.post(reverse("procurement_prototype:action"),{"action":"acknowledge","comment":"review","next":reverse("procurement_prototype:comparison")}); self.assertEqual(response.status_code,302)
        self.assertEqual(before, {model: model.objects.count() for model in production_models})
    @override_settings(PROCUREMENT_PROTOTYPE_ENABLED=False)
    def test_disabled_prototype_is_not_exposed(self): self.assertEqual(self.client.get("/prototype/procurement/").status_code,404)
