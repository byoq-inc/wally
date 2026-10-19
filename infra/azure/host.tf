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
