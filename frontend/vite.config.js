import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import os from "node:os";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

function numberOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, Math.min(100, Number(n.toFixed(1)))) : null;
}

async function runPowerShell(script, timeout = 4500) {
  const { stdout } = await execFileAsync(
    "powershell.exe",
    ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
    { timeout, windowsHide: true, maxBuffer: 1024 * 1024 },
  );
  const text = stdout.trim();
  if (!text) throw new Error("Empty telemetry response");
  return JSON.parse(text);
}

async function windowsMetrics() {
  const script = `
    $ErrorActionPreference = 'SilentlyContinue'
    $cpu = $null
    try {
      $cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
    } catch {}
    if ($null -eq $cpu) {
      try {
        $samples = (Get-Counter '\\Processor(_Total)\\% Processor Time').CounterSamples
        if ($samples) { $cpu = ($samples | Select-Object -First 1).CookedValue }
      } catch {}
    }

    $os = Get-CimInstance Win32_OperatingSystem
    $ram = $null
    if ($os -and $os.TotalVisibleMemorySize) {
      $ram = 100 * (($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize)
    }

    $disk = $null
    try {
      $logical = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
      if ($logical -and $logical.Size) { $disk = 100 * (($logical.Size - $logical.FreeSpace) / $logical.Size) }
    } catch {}

    $gpu = $null
    try {
      $gpuSamples = Get-Counter '\\GPU Engine(*)\\Utilization Percentage' | Select-Object -ExpandProperty CounterSamples
      $threeD = @($gpuSamples | Where-Object {
        $_.InstanceName -match 'engtype_3D|engtype_Compute_0|engtype_Compute|engtype_VideoDecode|engtype_VideoEncode'
      } | ForEach-Object { [double]$_.CookedValue })
      if ($threeD.Count -gt 0) { $gpu = ($threeD | Measure-Object -Maximum).Maximum }
    } catch {}

    [pscustomobject]@{
      cpu_percent = if ($null -ne $cpu) { [double]$cpu } else { $null }
      ram_percent = if ($null -ne $ram) { [double]$ram } else { $null }
      gpu_percent = if ($null -ne $gpu) { [double]$gpu } else { $null }
      disk_percent = if ($null -ne $disk) { [double]$disk } else { $null }
      platform = 'windows'
      source = 'Windows host telemetry'
    } | ConvertTo-Json -Compress
  `;
  try {
    return await runPowerShell(script);
  } catch {
    return null;
  }
}

async function unixMetrics() {
  try {
    const total = os.totalmem();
    const free = os.freemem();
    let diskPercent = null;
    try {
      const { stdout } = await execFileAsync("df", ["-k", "/"], { timeout: 1500 });
      const line = stdout.trim().split(/\r?\n/).at(-1) || "";
      const match = line.match(/(\d+)%/);
      if (match) diskPercent = Number(match[1]);
    } catch {}
    const cpuPercent = os.loadavg()[0] / Math.max(1, os.cpus().length) * 100;
    return {
      cpu_percent: numberOrNull(cpuPercent),
      ram_percent: numberOrNull((1 - free / total) * 100),
      disk_percent: numberOrNull(diskPercent),
      gpu_percent: null,
      platform: process.platform,
      source: "Node host telemetry",
    };
  } catch {
    return null;
  }
}

let telemetryCache = null;
let telemetryCacheAt = 0;
let telemetryInFlight = null;

async function getHostMetrics() {
  const now = Date.now();
  if (telemetryCache && now - telemetryCacheAt < 1200) return telemetryCache;
  if (telemetryInFlight) return telemetryInFlight;

  telemetryInFlight = (async () => {
    let result = null;
    if (process.platform === "win32") {
      result = await windowsMetrics();
    }
    if (!result) result = await unixMetrics();
    if (result) {
      telemetryCache = Object.fromEntries(Object.entries(result).map(([key, value]) => {
        if (key.endsWith("_percent")) return [key, numberOrNull(value)];
        return [key, value];
      }));
      telemetryCacheAt = Date.now();
    }
    return telemetryCache;
  })();

  try {
    return await telemetryInFlight;
  } finally {
    telemetryInFlight = null;
  }
}

function hostTelemetryPlugin() {
  return {
    name: "jarvis-host-telemetry",
    configureServer(server) {
      server.middlewares.use("/__jarvis/metrics", async (_req, res) => {
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        res.setHeader("Cache-Control", "no-store, no-cache, must-revalidate");
        try {
          const metrics = await getHostMetrics();
          res.statusCode = metrics ? 200 : 503;
          res.end(JSON.stringify(metrics || { error: "Host telemetry unavailable" }));
        } catch (error) {
          res.statusCode = 500;
          res.end(JSON.stringify({ error: error?.message || "Host telemetry failed" }));
        }
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), hostTelemetryPlugin()],
  server: { port: 5173 },
});
