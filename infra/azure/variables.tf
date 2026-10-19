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
