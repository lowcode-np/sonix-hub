#Requires -RunAsAdministrator
<#
 Opens an inbound TCP port for SONIX Hub on this Windows VPS.

   .\scripts\open_firewall.ps1                                  # 8080, any source
   .\scripts\open_firewall.ps1 -Port 80
   .\scripts\open_firewall.ps1 -RemoteAddress 203.0.113.50      # only the MT5 VPS / office IP
#>
param(
    [int]$Port = 8080,
    [string[]]$RemoteAddress = @("Any")
)

$name = "SONIX Hub TCP $Port"

if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
    Write-Host "rule already exists: $name"
    exit 0
}

New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -RemoteAddress $RemoteAddress -Profile Any | Out-Null

Write-Host "opened inbound TCP $Port for: $($RemoteAddress -join ', ')"
Get-NetFirewallRule -DisplayName $name | Select-Object DisplayName, Enabled, Direction, Action
