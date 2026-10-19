output "vm_id" { value = azurerm_linux_virtual_machine.host.id }
output "bastion_name" { value = azurerm_bastion_host.main.name }
output "resource_group" { value = azurerm_resource_group.main.name }
output "runtime_principal_id" { value = azurerm_linux_virtual_machine.host.identity[0].principal_id }
output "data_disk_id" { value = azurerm_managed_disk.data.id }
