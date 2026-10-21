# Capability separation is not per-conversation authorization. Runtime must enforce that.
resource "azurerm_storage_container" "capability" {
  for_each = toset([
    "proof-receipts", "attachments"
  ])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.artifacts.id
  container_access_type = "private"
}
output "artifact_container_ids" {
  value = { for key, container in azurerm_storage_container.capability : key => container.id }
}
