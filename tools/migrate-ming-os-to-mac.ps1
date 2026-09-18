param(
    [string]$HostName = "192.168.100.4",
    [string]$User = "mac",
    [string]$RemotePath = "/Users/mac/ming-os",
    [switch]$IncludeOutput
)

$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
$remote = $User + "@" + $HostName
$target = $remote + ":" + $RemotePath + "/"

git -C $repo status --short --branch
$head = (git -C $repo rev-parse HEAD).Trim()
ssh -o StrictHostKeyChecking=accept-new $remote ("mkdir -p '" + $RemotePath + "'")

$exclude = @("--exclude=output/", "--exclude=tmp/", "--exclude=scratch/", "--exclude=.tmp*", "--exclude=.codex*", "--exclude=__pycache__/")
if ($IncludeOutput) { $exclude = $exclude | Where-Object { $_ -ne "--exclude=output/" } }
if (-not (Get-Command rsync -ErrorAction SilentlyContinue)) {
    throw "未找到 rsync，请按交接文档使用 git bundle + scp。"
}

rsync -a --delete-delay @exclude ($repo.Path + "\") $target
if ($LASTEXITCODE -ne 0) { throw "rsync 失败，未宣称迁移完成。" }

$remoteHead = (ssh $remote ("cd '" + $RemotePath + "' && git rev-parse HEAD")).Trim()
if ($remoteHead -ne $head) { throw "Mac 端提交校验失败：" + $remoteHead }
ssh $remote ("cd '" + $RemotePath + "' && git status --short --branch")
Write-Host "源码同步完成；未构建 ISO、未部署、未操作 OTA。"

