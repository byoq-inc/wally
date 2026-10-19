resource "azurerm_resource_group" "main" {
  name     = "${var.name}-assistant"
  location = var.location
  tags     = { workload = "wally", managed_by = "opentofu" }
}
data "azurerm_client_config" "current" {}
locals {
  suffix = substr(sha256("${var.subscription_id}:${var.name}"), 0, 8)
}
