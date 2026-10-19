resource "azurerm_recovery_services_vault" "main" {
  name                = "${var.name}-backup"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Standard"
  lifecycle { prevent_destroy = true }
}
resource "azurerm_backup_policy_vm" "main" {
  name                = "daily"
  resource_group_name = azurerm_resource_group.main.name
  recovery_vault_name = azurerm_recovery_services_vault.main.name
  timezone            = "UTC"
  backup {
    frequency = "Daily"
    time      = "03:00"
  }
  retention_daily { count = 14 }
}
resource "azurerm_backup_protected_vm" "host" {
  resource_group_name = azurerm_resource_group.main.name
  recovery_vault_name = azurerm_recovery_services_vault.main.name
  source_vm_id        = azurerm_linux_virtual_machine.host.id
  backup_policy_id    = azurerm_backup_policy_vm.main.id
  depends_on          = [azurerm_virtual_machine_data_disk_attachment.data]
}
