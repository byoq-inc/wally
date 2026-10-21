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
variable "lab_tailscale_auth_secret_id" {
  description = "Versioned Key Vault secret URI holding a tagged, ephemeral Tailscale enrollment key. No secret value enters OpenTofu. Operator must grant the lab identity read access to this secret and configure vault networking."
  type        = string
  default     = null
  validation {
    condition     = var.lab_tailscale_auth_secret_id == null ? true : can(regex("^https://[a-zA-Z0-9-]+\\.vault\\.azure\\.net/secrets/[a-zA-Z0-9-]+/[a-fA-F0-9]{32}$", var.lab_tailscale_auth_secret_id))
    error_message = "Supply a versioned Azure Key Vault secret URI, not an auth key."
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
  dynamic "secret" {
    for_each = var.lab_tailscale_auth_secret_id == null ? [] : [var.lab_tailscale_auth_secret_id]
    content {
      name                = "tailscale-auth"
      key_vault_secret_id = secret.value
      identity            = azurerm_user_assigned_identity.lab[0].id
    }
  }
  lifecycle {
    precondition {
      condition     = var.lab_tailscale_auth_secret_id != null
      error_message = "Lab activation requires a Tailscale enrollment secret reference."
    }
  }
  template {
    # No HTTP ingress exists to wake a zero-replica app on a tailnet request.
    min_replicas = 1
    max_replicas = 1
    container {
      name    = "lab"
      image   = "${azurerm_container_registry.lab[0].login_server}/${var.lab_image.repository}@${var.lab_image.digest}"
      cpu     = 0.5
      memory  = "1Gi"
      command = ["/usr/local/bin/lab-tailscale-entrypoint"]
      env {
        name        = "TS_AUTHKEY"
        secret_name = "tailscale-auth"
      }
      env {
        name  = "TS_USERSPACE"
        value = "true"
      }
      env {
        name  = "TS_STATE_DIR"
        value = "/tmp/tailscale"
      }
      env {
        name  = "TS_KUBE_SECRET"
        value = ""
      }
      env {
        name  = "TS_HOSTNAME"
        value = "${var.name}-lab"
      }
      env {
        name  = "TS_EXTRA_ARGS"
        value = "--accept-routes=false"
      }
      env {
        name  = "TS_SERVE_CONFIG"
        value = "/etc/tailscale/lab-serve.json"
      }
    }
  }
  # No Azure ingress, including environment-internal ingress. Access uses Serve
  # over the outbound-established tailnet connection, never Tailscale Funnel.
  depends_on = [azurerm_role_assignment.lab_pull]
}
output "lab_registry" {
  value = local.lab_enabled ? azurerm_container_registry.lab[0].login_server : null
}
output "lab_identity_principal_id" {
  description = "Operator grants this identity read access to only the dedicated enrollment secret before activating the image."
  value       = local.lab_enabled ? azurerm_user_assigned_identity.lab[0].principal_id : null
}
output "lab_service_id" {
  value = var.lab_image != null ? azurerm_container_app.lab[0].id : null
}
