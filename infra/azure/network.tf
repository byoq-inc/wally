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
