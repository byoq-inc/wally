terraform {
  required_version = ">= 1.8.0, < 2.0.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}
provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}
variable "subscription_id" { type = string }
variable "location" {
  type    = string
  default = "eastus"
}
variable "name" {
  type    = string
  default = "wally"
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,14}$", var.name))
    error_message = "Use 3-15 lowercase letters/digits, starting with a letter."
  }
}
variable "ssh_public_key" {
  type = string
  validation {
    condition     = startswith(var.ssh_public_key, "ssh-ed25519 ") || startswith(var.ssh_public_key, "ssh-rsa ")
    error_message = "Supply an SSH public key, never a private key."
  }
}
variable "vm_size" {
  type    = string
  default = "Standard_D2s_v5"
}
variable "data_disk_gb" {
  type    = number
  default = 128
  validation {
    condition     = var.data_disk_gb >= 128 && floor(var.data_disk_gb) == var.data_disk_gb
    error_message = "Data disk must be an integer >= 128 GiB."
  }
}
resource "azurerm_resource_group" "main" {
  name     = "${var.name}-assistant"
  location = var.location
  tags     = { workload = "wally", managed_by = "opentofu" }
}
resource "azurerm_virtual_network" "main" {
  name                = var.name
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = ["10.80.0.0/16"]
}
resource "azurerm_subnet" "host" {
  name                 = "assistant"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.80.1.0/24"]
  service_endpoints    = ["Microsoft.Storage", "Microsoft.KeyVault"]
}
resource "azurerm_subnet" "bastion" {
  name                 = "AzureBastionSubnet"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.80.2.0/26"]
}
resource "azurerm_network_security_group" "host" {
  name                = "${var.name}-host"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  security_rule {
    name                       = "SSH-from-bastion"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = "10.80.2.0/26"
    destination_address_prefix = "*"
  }
  security_rule {
    name                       = "Deny-other-inbound"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}
resource "azurerm_subnet_network_security_group_association" "host" {
  subnet_id                 = azurerm_subnet.host.id
  network_security_group_id = azurerm_network_security_group.host.id
}
resource "azurerm_public_ip" "egress" {
  name                = "${var.name}-egress"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  allocation_method   = "Static"
  sku                 = "Standard"
}
resource "azurerm_nat_gateway" "main" {
  name                = var.name
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  sku_name            = "Standard"
}
resource "azurerm_nat_gateway_public_ip_association" "main" {
  nat_gateway_id       = azurerm_nat_gateway.main.id
  public_ip_address_id = azurerm_public_ip.egress.id
}
resource "azurerm_subnet_nat_gateway_association" "main" {
  subnet_id      = azurerm_subnet.host.id
  nat_gateway_id = azurerm_nat_gateway.main.id
}
resource "azurerm_public_ip" "bastion" {
  name                = "${var.name}-bastion"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  allocation_method   = "Static"
  sku                 = "Standard"
}
resource "azurerm_bastion_host" "main" {
  name                = var.name
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Standard"
  tunneling_enabled   = true
  ip_configuration {
    name                 = "main"
    subnet_id            = azurerm_subnet.bastion.id
    public_ip_address_id = azurerm_public_ip.bastion.id
  }
}
resource "azurerm_network_interface" "host" {
  name                = var.name
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  ip_configuration {
    name                          = "main"
    subnet_id                     = azurerm_subnet.host.id
    private_ip_address_allocation = "Dynamic"
  }
}
resource "azurerm_linux_virtual_machine" "host" {
  name                            = var.name
  location                        = var.location
  resource_group_name             = azurerm_resource_group.main.name
  size                            = var.vm_size
  admin_username                  = "wallyadmin"
  disable_password_authentication = true
  network_interface_ids           = [azurerm_network_interface.host.id]
  secure_boot_enabled             = true
  vtpm_enabled                    = true
  identity { type = "SystemAssigned" }
  admin_ssh_key {
    username   = "wallyadmin"
    public_key = var.ssh_public_key
  }
  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }
  source_image_reference {
    publisher = "Canonical"
    offer     = "ubuntu-24_04-lts"
    sku       = "server"
    version   = "latest"
  }
}
resource "azurerm_managed_disk" "data" {
  name                 = "${var.name}-state"
  location             = var.location
  resource_group_name  = azurerm_resource_group.main.name
  storage_account_type = "Premium_LRS"
  create_option        = "Empty"
  disk_size_gb         = var.data_disk_gb
  lifecycle { prevent_destroy = true }
}
resource "azurerm_virtual_machine_data_disk_attachment" "data" {
  managed_disk_id    = azurerm_managed_disk.data.id
  virtual_machine_id = azurerm_linux_virtual_machine.host.id
  lun                = 0
  caching            = "None"
}
data "azurerm_client_config" "current" {}
locals {
  suffix = substr(sha256("${var.subscription_id}:${var.name}"), 0, 8)
}
resource "azurerm_key_vault" "main" {
  name                       = "${var.name}-${local.suffix}"
  location                   = var.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 90
  network_acls {
    bypass                     = "None"
    default_action             = "Deny"
    virtual_network_subnet_ids = [azurerm_subnet.host.id]
  }
}
resource "azurerm_storage_account" "artifacts" {
  name                            = "${var.name}${local.suffix}"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  network_rules {
    default_action             = "Deny"
    bypass                     = ["None"]
    virtual_network_subnet_ids = [azurerm_subnet.host.id]
  }
  blob_properties {
    versioning_enabled = true
    delete_retention_policy { days = 30 }
    container_delete_retention_policy { days = 30 }
  }
  lifecycle { prevent_destroy = true }
}
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
output "vm_id" { value = azurerm_linux_virtual_machine.host.id }
output "bastion_name" { value = azurerm_bastion_host.main.name }
output "resource_group" { value = azurerm_resource_group.main.name }
output "runtime_principal_id" { value = azurerm_linux_virtual_machine.host.identity[0].principal_id }
output "data_disk_id" { value = azurerm_managed_disk.data.id }
