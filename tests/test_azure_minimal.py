"""Source contract for the Pi-only Azure foundation; not a live-state audit."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra" / "azure"


class MinimalAzureTests(unittest.TestCase):
    def test_no_deferred_compute_or_roles(self):
        text = "\n".join(p.read_text() for p in INFRA.glob("*.tf"))
        types = re.findall(r'resource\s+"([^"]+)"', text)
        self.assertFalse(any(t.startswith(("azurerm_container_", "azurerm_kubernetes_")) for t in types))
        self.assertNotIn("azurerm_user_assigned_identity", types)
        self.assertNotIn("azurerm_role_assignment", types)
        self.assertNotRegex(text, r'var\.(worker_jobs|enable_worker_plane|lab_image|enable_lab)\b')
        self.assertEqual(types.count("azurerm_linux_virtual_machine"), 1)

    def test_removed_lab_runtime(self):
        self.assertFalse((INFRA / "lab-runtime").exists())
        self.assertFalse((INFRA / "lab.tf").exists())
        self.assertFalse((INFRA / "workers.tf").exists())

    def test_preserve_data_protection(self):
        for name in ("host.tf", "storage.tf", "backup.tf"):
            self.assertIn("prevent_destroy = true", (INFRA / name).read_text())
        self.assertIn('"proof-receipts", "attachments"', (INFRA / "artifacts.tf").read_text())

    def test_no_worker_network_dependency(self):
        text = (INFRA / "storage.tf").read_text()
        self.assertIn("[azurerm_subnet.host.id]", text)
        self.assertNotIn("workers", text)

    def test_example_inputs_have_declarations(self):
        text = "\n".join(p.read_text() for p in INFRA.glob("*.tf"))
        declarations = set(re.findall(r'variable\s+"([^"]+)"', text))
        inputs = set(re.findall(r"^(\w+)\s*=", (INFRA / "terraform.tfvars.example").read_text(), re.M))
        self.assertLessEqual(inputs, declarations)

    def test_existing_state_and_cutover_gates(self):
        text = (INFRA / "README.md").read_text()
        self.assertIn("stop on any delete/replacement", text)
        self.assertIn("Apply only with explicit approval", text)
        self.assertIn("No concurrent clones of a Signal identity", text)


if __name__ == "__main__":
    unittest.main()
