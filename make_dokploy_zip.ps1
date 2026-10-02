# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Ahmet Harun Ertürk
param([switch]$WithoutModel)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$base = Split-Path -Leaf $root
$zipName = if ($WithoutModel) { "$base-dokploy-code-only.zip" } else { "$base-dokploy.zip" }
$zipPath = Join-Path $root $zipName
$files = @(
    'Dockerfile',
    '.dockerignore',
    '.env.example',
    'requirements.txt',
    'main.py',
    'db.py',
    'detectors.py',
    'branding.py',
    'api/server.py',
    'static/app.js',
    'static/style.css',
    'templates/index.html',
    'templates/login.html',
    'DOKPLOY.md',
    'README.md',
    'docs/images/sign-in.png',
    'docs/images/drop.png',
    'docs/images/bird.png',
    'LICENSE',
    'USAGE.md'
)
if (-not $WithoutModel) {
    # Weights are optional. Include models placed at the project root or models/.
    $blocked = @($env:BLOCKED_WEIGHTS -split ',' | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
    foreach ($folder in @($root, (Join-Path $root 'models'))) {
        if (-not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
        Get-ChildItem -LiteralPath $folder -Filter '*.pt' -File |
            Where-Object { $blocked -notcontains $_.Name.ToLowerInvariant() } |
            ForEach-Object {
                $files += $(if ($folder -eq $root) { $_.Name } else { 'models/' + $_.Name })
            }
    }
}

$output = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::Create)
try {
    $archive = [System.IO.Compression.ZipArchive]::new(
        $output, [System.IO.Compression.ZipArchiveMode]::Create, $true
    )
    try {
        foreach ($relative in $files) {
            $source = Join-Path $root $relative
            if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
                throw "Missing package file: $relative"
            }
            $entry = $archive.CreateEntry(
                $relative, [System.IO.Compression.CompressionLevel]::Optimal
            )
            $inputStream = [System.IO.File]::OpenRead($source)
            try {
                $entryStream = $entry.Open()
                try {
                    $inputStream.CopyTo($entryStream)
                } finally {
                    $entryStream.Dispose()
                }
            } finally {
                $inputStream.Dispose()
            }
        }
    } finally {
        $archive.Dispose()
    }
} finally {
    $output.Dispose()
}

Write-Output "Created $zipPath"
