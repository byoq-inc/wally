mock_provider "azurerm" {
  mock_resource "azurerm_storage_account" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Storage/storageAccounts/test" }
  }
  mock_resource "azurerm_storage_container" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Storage/storageAccounts/test/blobServices/default/containers/test" }
  }
  mock_data "azurerm_client_config" {
    defaults = { tenant_id = "00000000-0000-0000-0000-000000000000" }
  }
  mock_resource "azurerm_subnet" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test/subnets/AzureBastionSubnet" }
  }
  mock_resource "azurerm_network_security_group" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/networkSecurityGroups/test" }
  }
  mock_resource "azurerm_nat_gateway" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/natGateways/test" }
  }
  mock_resource "azurerm_public_ip" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/publicIPAddresses/test" }
  }
  mock_resource "azurerm_network_interface" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/networkInterfaces/test" }
  }
  mock_resource "azurerm_linux_virtual_machine" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Compute/virtualMachines/test" }
  }
  mock_resource "azurerm_managed_disk" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Compute/disks/test" }
  }
  mock_resource "azurerm_backup_policy_vm" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.RecoveryServices/vaults/test/backupPolicies/test" }
  }
}

variables {
  subscription_id = "00000000-0000-0000-0000-000000000000"
  ssh_public_key  = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
}

run "secure_base" {
  command = plan
  assert {
    condition     = azurerm_linux_virtual_machine.host.disable_password_authentication && azurerm_linux_virtual_machine.host.secure_boot_enabled
    error_message = "Host must require SSH keys and secure boot."
  }
  assert {
    condition     = azurerm_network_interface.host.ip_configuration[0].public_ip_address_id == null
    error_message = "Host must not have a public IP."
  }
  assert {
    condition     = !azurerm_storage_account.artifacts.shared_access_key_enabled && !azurerm_storage_account.artifacts.allow_nested_items_to_be_public
    error_message = "Artifact storage must deny shared keys and public containers."
  }
  assert {
    condition     = azurerm_key_vault.main.purge_protection_enabled && azurerm_key_vault.main.rbac_authorization_enabled
    error_message = "Key Vault must retain purge protection and RBAC."
  }
  assert {
    condition     = azurerm_managed_disk.data.disk_size_gb >= 128 && azurerm_virtual_machine_data_disk_attachment.data.caching == "None"
    error_message = "State disk must be retained with caching None."
  }
}

run "minimal_private_artifacts" {
  command = plan
  assert {
    condition     = toset(keys(azurerm_storage_container.capability)) == toset(["attachments", "proof-receipts"])
    error_message = "Only initial-port artifact containers belong in this root."
  }
  assert {
    condition     = alltrue([for c in azurerm_storage_container.capability : c.container_access_type == "private"])
    error_message = "Artifacts must remain private."
  }
  assert {
    condition     = azurerm_storage_account.artifacts.network_rules[0].default_action == "Deny" && toset(azurerm_storage_account.artifacts.network_rules[0].virtual_network_subnet_ids) == toset([azurerm_subnet.host.id])
    error_message = "Only the host subnet may reach artifact storage."
  }
  assert {
    condition     = length(azurerm_key_vault.main.network_acls[0].virtual_network_subnet_ids) == 1
    error_message = "Do not broaden the secret network boundary."
  }
}
