param(
    [Parameter(Mandatory=$true)][int]$ProcessId,
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][string]$StagedRoot,
    [Parameter(Mandatory=$true)][string]$Executable,
    [string]$ServerScript = ""
)

$protectedNames = @('projects', 'exports', 'backups', 'imports', 'updates', 'sources', 'source_packages', 'source_registry.json', 'playset_snapshots', 'diagnostics', 'settings')
Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue

Get-ChildItem -LiteralPath $StagedRoot -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($StagedRoot.Length).TrimStart('\', '/')
    $parts = $relative -split '[\\/]'
    if (-not ($parts | Where-Object { $protectedNames -contains $_.ToLowerInvariant() })) {
        $destination = Join-Path $InstallRoot $relative
        $parent = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
    }
}

if ($ServerScript) {
    Start-Process -FilePath $Executable -ArgumentList @($ServerScript) -WindowStyle Hidden
} else {
    Start-Process -FilePath $Executable -WindowStyle Hidden
}
