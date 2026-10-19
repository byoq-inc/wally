# Optional execution plane. Images must already exist; no job runs on apply.
variable "enable_worker_plane" {
  description = "Provision registry/network before publishing reviewed images and declaring jobs."
  type        = bool
  default     = false
}
variable "worker_jobs" {
  description = "Approved finite workloads. Empty declares no jobs. Keys are capability names; images are digest-pinned in the provisioned ACR. No secrets in arguments."
  type = map(object({
    image_repository = string
    image_digest     = string
    cpu              = optional(number, 1)
    memory           = optional(string, "2Gi")
    timeout_seconds  = optional(number, 3600)
  }))
  default = {}
  validation {
    condition = alltrue([for name, job in var.worker_jobs :
      contains(["pr-review", "nightly", "memory-index", "verification"], name) &&
      can(regex("^[a-z0-9][a-z0-9/._-]*$", job.image_repository)) &&
      can(regex("^sha256:[a-f0-9]{64}$", job.image_digest)) &&
      contains([0.5, 1, 2], job.cpu) &&
      job.memory == lookup({ "0.5" = "1Gi", "1" = "2Gi", "2" = "4Gi" }, tostring(job.cpu), "invalid") &&
      job.timeout_seconds >= 60 && job.timeout_seconds <= 86400 && floor(job.timeout_seconds) == job.timeout_seconds
    ])
    error_message = "Use an approved capability, digest-pinned image, CPU/memory pair 0.5/1Gi, 1/2Gi or 2/4Gi, and integer timeout 60-86400 seconds."
  }
}
locals {
  workers_enabled = var.enable_worker_plane || length(var.worker_jobs) > 0
}
resource "azurerm_container_registry" "workers" {
  count               = local.workers_enabled ? 1 : 0
  name                = "${var.name}${local.suffix}acr"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = false
}
resource "azurerm_subnet" "workers" {
  count                = local.workers_enabled ? 1 : 0
  name                 = "workers"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.80.4.0/23"]
  service_endpoints    = ["Microsoft.Storage"]
  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}
resource "azurerm_subnet_nat_gateway_association" "workers" {
  count          = local.workers_enabled ? 1 : 0
  subnet_id      = azurerm_subnet.workers[0].id
  nat_gateway_id = azurerm_nat_gateway.main.id
}
resource "azurerm_container_app_environment" "workers" {
  count                          = local.workers_enabled ? 1 : 0
  name                           = "${var.name}-workers"
  resource_group_name            = azurerm_resource_group.main.name
  location                       = var.location
  infrastructure_subnet_id       = azurerm_subnet.workers[0].id
  internal_load_balancer_enabled = true
  public_network_access          = "Disabled"
  # Never collect prompts, source, credentials or tool output by default.
  logs_destination = ""
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
  depends_on = [azurerm_subnet_nat_gateway_association.workers]
}
resource "azurerm_user_assigned_identity" "worker" {
  for_each            = var.worker_jobs
  name                = "${var.name}-${each.key}"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
}
resource "azurerm_role_assignment" "worker_pull" {
  for_each             = var.worker_jobs
  scope                = azurerm_container_registry.workers[0].id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.worker[each.key].principal_id
}
resource "azurerm_container_app_job" "worker" {
  for_each                     = var.worker_jobs
  name                         = "${var.name}-${each.key}"
  resource_group_name          = azurerm_resource_group.main.name
  location                     = var.location
  container_app_environment_id = azurerm_container_app_environment.workers[0].id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = each.value.timeout_seconds
  # The durable owner must reconcile retries, not blindly duplicate side effects.
  replica_retry_limit = 0
  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.worker[each.key].id]
  }
  registry {
    server   = azurerm_container_registry.workers[0].login_server
    identity = azurerm_user_assigned_identity.worker[each.key].id
  }
  template {
    container {
      name   = "worker"
      image  = "${azurerm_container_registry.workers[0].login_server}/${each.value.image_repository}@${each.value.image_digest}"
      cpu    = each.value.cpu
      memory = each.value.memory
      env {
        name  = "WALLY_CAPABILITY"
        value = each.key
      }
      env {
        name  = "WALLY_ARTIFACT_ACCOUNT"
        value = azurerm_storage_account.artifacts.name
      }
      env {
        name  = "WALLY_ARTIFACT_CONTAINER"
        value = azurerm_storage_container.capability[each.key].name
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = azurerm_user_assigned_identity.worker[each.key].client_id
      }
    }
  }
  depends_on = [azurerm_role_assignment.worker_pull, azurerm_role_assignment.worker_artifacts]
}
output "worker_job_ids" {
  value = { for key, job in azurerm_container_app_job.worker : key => job.id }
}
output "worker_registry" {
  value = local.workers_enabled ? azurerm_container_registry.workers[0].login_server : null
}
