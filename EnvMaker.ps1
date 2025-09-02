# Caminhos
$venvPath = "$PSScriptRoot\venv"
$activateScript = "$venvPath\Scripts\Activate.ps1"
$reqFile = "$PSScriptRoot\requirements.txt"
$pythonExe = "$venvPath\Scripts\python.exe"
$pipExe = "$venvPath\Scripts\pip.exe"

function Ensure-Python {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Output "❌ Python não está disponível no PATH."
        exit 1
    }
}

function Create-Venv {
    Write-Output "⚙️ Criando ambiente virtual..."
    python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Output "❌ Erro ao criar venv."
        exit 1
    }
}

function Activate-Venv {
    Write-Output "🚀 Ativando venv..."
    & $activateScript
}

function Generate-Requirements {
    & $pipExe install --upgrade pip > $null
    & $pipExe freeze | Out-File -Encoding UTF8 $reqFile
    Write-Output "📦 requirements.txt gerado com os pacotes instalados."
}

function Install-Requirements {
    if (-not (Test-Path $reqFile)) {
        Write-Warning "⚠️ requirements.txt não encontrado. Gerando automaticamente..."
        Generate-Requirements
    } else {
        & $pipExe install --upgrade pip > $null
        & $pipExe install -r $reqFile
        Write-Output "📦 Dependências instaladas com sucesso."
    }
}

function Clean-Venv {
    if (Test-Path $venvPath) {
        Remove-Item -Recurse -Force $venvPath
        Write-Output "🧹 venv removido."
    } else {
        Write-Output "ℹ️ Nenhum venv encontrado para limpar."
    }
}

function Reset-Env {
    Clean-Venv
    Create-Venv
    Activate-Venv
    Install-Requirements
}

function Extract-Imports {
    $modules = @()
    $pyFiles = Get-ChildItem -Recurse -Filter *.py | Where-Object { -not $_.FullName.Contains("venv") }

    foreach ($file in $pyFiles) {
        $lines = Get-Content $file.FullName
        foreach ($line in $lines) {
            if ($line -match "^\s*(import|from)\s+([a-zA-Z0-9_.]+)") {
                $modules += $Matches[2].Split(".")[0]
            }
        }
    }

    $unique = $modules | Sort-Object -Unique

    # Lista comum de módulos da stdlib (incompleta, mas cobre 90%)
    $stdlib = @(
        "os", "sys", "math", "re", "time", "datetime", "pathlib", "itertools", "functools", "subprocess",
        "threading", "json", "csv", "random", "typing", "hashlib", "shutil", "platform", "collections"
    )

    $external = $unique | Where-Object { $_ -and ($_ -notin $stdlib) }

    if ($external.Count -eq 0) {
        Write-Output "Nenhum módulo externo detectado."
        return
    }

    Write-Output "`n📋 Módulos externos detectados:"
    $external | ForEach-Object { Write-Output " - $_" }

    $external | Out-File -Encoding UTF8 $reqFile
    Write-Output "`n📦 requirements.txt gerado com base nos scripts Python."
}

function Show-Menu {
    Clear-Host
    Write-Host "====== Gerenciador de Ambiente Virtual ======" -ForegroundColor Cyan
    Write-Host "1. Criar novo venv (new)"
    Write-Host "2. Atualizar dependências (update)"
    Write-Host "3. Resetar ambiente (reset)"
    Write-Host "4. Limpar/remover venv (clean)"
    Write-Host "5. Gerar requirements.txt a partir dos scripts"
    Write-Host "6. Sair"
    Write-Host "=============================================`n"
}

Ensure-Python

do {
    Show-Menu
    $op = Read-Host "Escolha uma opção [1-6]"

    switch ($op) {
        "1" {
            if (Test-Path $venvPath) {
                Write-Host "⚠️ venv já existe. Use opção 3 para resetar.`n"
            } else {
                Create-Venv
                Activate-Venv
                Install-Requirements
            }
        }
        "2" {
            if (-not (Test-Path $activateScript)) {
                Write-Host "⚠️ venv não encontrado. Criando novo...`n"
                Create-Venv
            }
            Activate-Venv
            Install-Requirements
        }
        "3" {
            Reset-Env
        }
        "4" {
            Clean-Venv
        }
        "6" {
            Write-Host "`n🟢 Encerrando..."
            exit
        }
        "5" {
            Extract-Imports
        }
        default {
            Write-Host "Opção inválida. Tente novamente.`n"
        }
    }

    if ($op -ne "6") {
        Write-Host "`nPressione Enter para continuar..."
        [void][System.Console]::ReadLine()
    }

} while ($true)
