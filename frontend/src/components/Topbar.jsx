import { useEffect, useState } from "react";
import { getHealth } from "../services/api";

const STATUS_LABEL = {
  OPTIMAL: "OPTIMAL",
  DEGRADED: "DEGRADED",
  CONNECTING: "CONNECTING",
};

function formatClock(date) {
  return date.toLocaleTimeString("en-GB", { hour12: false });
}

function formatDate(date) {
  return date.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

function BatteryIcon({ level = 0, charging = false }) {
  return (
    <span className={`battery-icon ${charging ? "charging" : ""}`} aria-label={`Battery ${level}%${charging ? ", charging" : ""}`}>
      <span className="battery-shell"><span className="battery-fill" style={{ width: `${Math.max(0, Math.min(100, level))}%` }} /></span>
      <span className="battery-tip" />
      {charging && <span className="battery-bolt">⚡</span>}
    </span>
  );
}

function WifiIcon({ online }) {
  return (
    <span className={`wifi-icon ${online ? "connected" : "disconnected"}`} aria-label={online ? "Wi-Fi/network connected" : "Network disconnected"}>
      <span className="wifi-arc wifi-arc-1" />
      <span className="wifi-arc wifi-arc-2" />
      <span className="wifi-arc wifi-arc-3" />
      <span className="wifi-dot" />
    </span>
  );
}

export default function Topbar() {
  const [now, setNow] = useState(new Date());
  const [status, setStatus] = useState("CONNECTING");
  const [battery, setBattery] = useState(null);
  const [online, setOnline] = useState(() => navigator.onLine);

  useEffect(() => {
    const clockTimer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(clockTimer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        await getHealth();
        if (!cancelled) setStatus("OPTIMAL");
      } catch {
        if (!cancelled) setStatus("DEGRADED");
      }
    }
    poll();
    const healthTimer = setInterval(poll, 5000);
    return () => { cancelled = true; clearInterval(healthTimer); };
  }, []);

  useEffect(() => {
    const handleOnline = () => setOnline(true);
    const handleOffline = () => setOnline(false);
    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    let cleanup = () => {};
    if (navigator.getBattery) {
      navigator.getBattery().then((manager) => {
        const update = () => setBattery({ level: Math.round(manager.level * 100), charging: manager.charging });
        update();
        manager.addEventListener("levelchange", update);
        manager.addEventListener("chargingchange", update);
        cleanup = () => {
          manager.removeEventListener("levelchange", update);
          manager.removeEventListener("chargingchange", update);
        };
      }).catch(() => {});
    }

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      cleanup();
    };
  }, []);

  return (
    <header className="topbar">
      <div className="topbar-left">
        <span className={`status-pill status-${status.toLowerCase()}`}>
          <span className="status-dot" />
          {STATUS_LABEL[status]}
        </span>
      </div>

      <div className="topbar-center">
        <span className="topbar-date">{formatDate(now)}</span>
        <span className="topbar-clock">{formatClock(now)}</span>
      </div>

      <div className="topbar-right">
        <div className="device-status" style={{ color: (battery && battery.level <= 20 && !battery.charging) ? "red" : "", color: (battery && 20 < battery.level <= 60 && !battery.charging) ? "yellow" : "", color: (battery && battery.charging) ? "green" : "", }} title={battery ? `Battery ${battery.level}%${battery.charging ? " · Plugged in" : ""}` : "Battery status unavailable in this browser"}>
          {battery ? <><BatteryIcon level={battery.level} charging={battery.charging} /><span>{battery.level}%</span>{battery.charging && <small>PLUGGED IN</small>}</> : <span className="device-unavailable">BATTERY —</span>}
        </div>
        <div className="device-status wifi-status" title={online ? "Network connected" : "Network disconnected"}>
          <WifiIcon online={online} />
          <span className={online ? "wifi-label" : "wifi-label wifi-off"}>{online ? "ONLINE" : "OFFLINE"}</span>
        </div>
      </div>
    </header>
  );
}
