# Capability separation is not per-conversation authorization. Runtime must enforce that.
resource "azurerm_storage_container" "capability" {
  for_each = toset([
    "pr-review", "nightly", "memory-index", "verification", "proof-receipts", "attachments"
  ])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.artifacts.id
  container_access_type = "private"
}
resource "azurerm_role_assignment" "worker_artifacts" {
  for_each             = var.worker_jobs
  scope                = azurerm_storage_container.capability[each.key].id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.worker[each.key].principal_id
}
output "artifact_container_ids" {
  value = { for key, container in azurerm_storage_container.capability : key => container.id }
}
