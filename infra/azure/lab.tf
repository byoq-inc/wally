# Operator-owned singleton lab. Pi receives no Azure management-plane grants.
# Updates go through reviewed OpenTofu, not containerApps/write permissions.
variable "enable_lab" {
  description = "Provision an isolated lab network and registry. Publish the image before setting lab_image."
  type        = bool
  default     = false
}
variable "lab_image" {
  description = "Reviewed image in the lab registry. Null supports registry bootstrap without starting a workload."
  type = object({
    repository = string
    digest     = string
  })
  default = null
  validation {
    condition = var.lab_image == null ? true : (
      can(regex("^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$", var.lab_image.repository)) &&
      can(regex("^sha256:[a-f0-9]{64}$", var.lab_image.digest))
    )
    error_message = "Use a registry-relative repository and a sha256 digest, never a tag or external registry URL."
  }
}
locals {
  lab_enabled = var.enable_lab || var.lab_image != null
}
resource "azurerm_resource_group" "lab" {
  count    = local.lab_enabled ? 1 : 0
  name     = "${var.name}-lab"
  location = var.location
}
resource "azurerm_virtual_network" "lab" {
  count               = local.lab_enabled ? 1 : 0
  name                = "lab"
  location            = var.location
  resource_group_name = azurerm_resource_group.lab[0].name
  address_space       = ["10.81.0.0/16"]
}
resource "azurerm_subnet" "lab" {
  count                = local.lab_enabled ? 1 : 0
  name                 = "containers"
  resource_group_name  = azurerm_resource_group.lab[0].name
  virtual_network_name = azurerm_virtual_network.lab[0].name
  address_prefixes     = ["10.81.0.0/23"]
  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}
resource "azurerm_container_registry" "lab" {
  count               = local.lab_enabled ? 1 : 0
  name                = "${var.name}${local.suffix}labacr"
  location            = var.location
  resource_group_name = azurerm_resource_group.lab[0].name
  sku                 = "Basic"
  admin_enabled       = false
}
resource "azurerm_container_app_environment" "lab" {
  count                          = local.lab_enabled ? 1 : 0
  name                           = "${var.name}-lab"
  location                       = var.location
  resource_group_name            = azurerm_resource_group.lab[0].name
  infrastructure_subnet_id       = azurerm_subnet.lab[0].id
  internal_load_balancer_enabled = true
  public_network_access          = "Disabled"
  logs_destination               = ""
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}
resource "azurerm_user_assigned_identity" "lab" {
  count               = local.lab_enabled ? 1 : 0
  name                = "${var.name}-lab-pull"
  location            = var.location
  resource_group_name = azurerm_resource_group.lab[0].name
}
resource "azurerm_role_assignment" "lab_pull" {
  count                = local.lab_enabled ? 1 : 0
  scope                = azurerm_container_registry.lab[0].id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.lab[0].principal_id
}
resource "azurerm_container_app" "lab" {
  count                        = var.lab_image != null ? 1 : 0
  name                         = "${var.name}-lab"
  resource_group_name          = azurerm_resource_group.lab[0].name
  container_app_environment_id = azurerm_container_app_environment.lab[0].id
  workload_profile_name        = "Consumption"
  revision_mode                = "Single"
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.lab[0].id]
  }
  registry {
    server   = azurerm_container_registry.lab[0].login_server
    identity = azurerm_user_assigned_identity.lab[0].id
  }
  template {
    min_replicas = 0
    max_replicas = 1
    container {
      name   = "lab"
      image  = "${azurerm_container_registry.lab[0].login_server}/${var.lab_image.repository}@${var.lab_image.digest}"
      cpu    = 0.5
      memory = "1Gi"
    }
  }
  # Private ingress only; there is deliberately no peering to the assistant VNet.
  ingress {
    external_enabled = false
    target_port      = 8080
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
  depends_on = [azurerm_role_assignment.lab_pull]
}
output "lab_registry" {
  value = local.lab_enabled ? azurerm_container_registry.lab[0].login_server : null
}
output "lab_service_id" {
  value = var.lab_image != null ? azurerm_container_app.lab[0].id : null
}
