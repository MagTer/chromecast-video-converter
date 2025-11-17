param (
    [int]$LogSeconds = 45
)

function Build-Stack {
    docker compose build *> $null
}

function Run-Stack {
    docker compose up -d
}

function Tail-Logs {
    Write-Host "Tailing gpu-ffmpeg logs for $LogSeconds seconds..."
    $process = Start-Process -FilePath "docker" -ArgumentList "compose logs -f --tail 100 gpu-ffmpeg" -NoNewWindow -PassThru
    Start-Sleep -Seconds $LogSeconds
    Stop-Process -Id $process.Id
}

function Teardown-Stack {
    docker compose down
}

Build-Stack
Run-Stack
Tail-Logs
Teardown-Stack
