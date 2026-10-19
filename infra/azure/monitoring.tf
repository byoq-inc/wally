variable "alert_action_group_ids" {
  description = "Existing approved Azure Monitor action groups. Empty creates a visible alert without notification delivery."
  type        = set(string)
  default     = []
  validation {
    condition = alltrue([for id in var.alert_action_group_ids : can(regex(
      "(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Insights/actionGroups/[^/]+$", id
    ))])
    error_message = "Supply full Azure Monitor action-group resource IDs."
  }
}
resource "azurerm_monitor_metric_alert" "host_cpu" {
  name                = "${var.name}-host-cpu"
  resource_group_name = azurerm_resource_group.main.name
  scopes              = [azurerm_linux_virtual_machine.host.id]
  description         = "Host CPU above 85 percent for 15 minutes. Not a runtime or Signal delivery health check."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  criteria {
    metric_namespace = "Microsoft.Compute/virtualMachines"
    metric_name      = "Percentage CPU"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 85
  }
  dynamic "action" {
    for_each = var.alert_action_group_ids
    content { action_group_id = action.value }
  }
}
output "host_cpu_alert_id" { value = azurerm_monitor_metric_alert.host_cpu.id }
