mock_provider "azurerm" {
  mock_resource "azurerm_storage_account" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Storage/storageAccounts/test" }
  }
  mock_resource "azurerm_storage_container" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Storage/storageAccounts/test/blobServices/default/containers/test" }
  }
  mock_resource "azurerm_container_registry" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ContainerRegistry/registries/test"
      login_server = "test.azurecr.io"
    }
  }
  mock_resource "azurerm_container_app_environment" {
    defaults = { id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.App/managedEnvironments/test" }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/test"
      principal_id = "00000000-0000-0000-0000-000000000001"
    }
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
    condition     = azurerm_managed_disk.data.disk_size_gb >= 128
    error_message = "State disk is undersized."
  }
  assert {
    condition     = length(azurerm_container_app_job.worker) == 0 && length(azurerm_container_app_environment.workers) == 0
    error_message = "Default must not activate the worker plane."
  }
  assert {
    condition     = alltrue([for c in azurerm_storage_container.capability : c.container_access_type == "private"])
    error_message = "All capability artifacts must remain private."
  }
}

run "registry_bootstrap" {
  command = plan
  variables { enable_worker_plane = true }
  assert {
    condition     = length(azurerm_container_registry.workers) == 1 && length(azurerm_container_app_job.worker) == 0
    error_message = "Must support provisioning the registry before publishing images or defining jobs."
  }
}

run "manual_worker" {
  command = plan
  variables {
    worker_jobs = {
      verification = {
        image_repository = "wally/verification"
        image_digest     = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      }
    }
  }
  assert {
    condition     = azurerm_container_app_job.worker["verification"].replica_retry_limit == 0 && length(azurerm_container_app_job.worker["verification"].manual_trigger_config) == 1
    error_message = "Job must be manual-only without blind retries."
  }
  assert {
    condition     = azurerm_container_app_environment.workers[0].public_network_access == "Disabled" && azurerm_container_app_environment.workers[0].logs_destination == ""
    error_message = "Workers must not expose ingress or collect raw output by default."
  }
  assert {
    condition     = azurerm_role_assignment.worker_artifacts["verification"].scope == azurerm_storage_container.capability["verification"].id
    error_message = "Worker artifact role must be container-scoped."
  }
  assert {
    condition     = length(azurerm_key_vault.main.network_acls[0].virtual_network_subnet_ids) == 1
    error_message = "Workers must not inherit host Key Vault network access."
  }
}

run "reject_unpinned_image" {
  command = plan
  variables {
    worker_jobs = {
      verification = { image_repository = "wally/verification", image_digest = "latest" }
    }
  }
  expect_failures = [var.worker_jobs]
}

run "reject_unbounded_worker" {
  command = plan
  variables {
    worker_jobs = {
      verification = {
        image_repository = "wally/verification"
        image_digest     = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        timeout_seconds  = 86401
      }
    }
  }
  expect_failures = [var.worker_jobs]
}

run "reject_unapproved_capability" {
  command = plan
  variables {
    worker_jobs = {
      arbitrary = {
        image_repository = "wally/arbitrary"
        image_digest     = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      }
    }
  }
  expect_failures = [var.worker_jobs]
}
