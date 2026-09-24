import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import {
  Activity,
  BarChart3,
  Beaker,
  Bell,
  Calendar,
  Database,
  FlaskConical,
  HardDrive,
  Heart,
  History,
  Home,
  Layers,
  ListChecks,
  Microscope,
  Orbit,
  Radar,
  Search,
  Settings,
  Star,
  Stethoscope,
  Target,
  Zap,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { HEALTH_LABELS } from "@edgefut/contracts";
import { relativeTime } from "@edgefut/shared";

import { api } from "@/lib/api";
import { useUi } from "@/store/ui";

import { GlobalSearch } from "./GlobalSearch";
import { HealthDot } from "./ui";

type NavItem = { to: string; label: string; icon: typeof Home; end?: boolean };
const nav: { title: string | null; items: NavItem[] }[] = [
  {
    title: null,
    items: [
      { to: "/", label: "Início", icon: Home, end: true },
      { to: "/radar", label: "Radar", icon: Radar },
      { to: "/jogos", label: "Jogos", icon: Calendar },
      { to: "/entradas", label: "Melhores Entradas", icon: Target },
      { to: "/ao-vivo", label: "Ao Vivo", icon: Zap },
      { to: "/multiplas", label: "Múltiplas", icon: Layers },
      { to: "/lab", label: "Backtest Lab", icon: FlaskConical },
      { to: "/historico", label: "Histórico", icon: History },
      { to: "/favoritos", label: "Favoritos", icon: Heart },
    ],
  },
  {
    title: "Dados",
    items: [
      { to: "/fontes", label: "Fontes", icon: Database },
      { to: "/modelos", label: "Modelos", icon: Activity },
      { to: "/performance", label: "Performance", icon: BarChart3 },
      { to: "/validacao", label: "Validação", icon: ShieldCheck },
      { to: "/flywheel", label: "Data Flywheel", icon: Orbit },
      { to: "/superbet-lab", label: "Superbet Lab", icon: Microscope },
      { to: "/pesquisa", label: "Pesquisa", icon: Beaker },
    ],
  },
  {
    title: "Sistema",
    items: [
      { to: "/alertas", label: "Alertas", icon: Bell },
      { to: "/sistema/dados", label: "Dados", icon: HardDrive },
      { to: "/sistema/jobs", label: "Jobs", icon: ListChecks },
      { to: "/sistema/diagnostico", label: "Diagnóstico", icon: Stethoscope },
      { to: "/configuracoes", label: "Configurações", icon: Settings },
    ],
  },
];

export function Layout() {
  const [searchOpen, setSearchOpen] = useState(false);
  const collapsed = useUi((s) => s.sidebarCollapsed);
  const navigate = useNavigate();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15000, retry: 1 });
  const sys = useQuery({ queryKey: ["system-health"], queryFn: api.systemHealth, refetchInterval: 30000, retry: 1 });
  const alerts = useQuery({ queryKey: ["alerts", "unread-count"], queryFn: () => api.alerts({ limit: 1, unread_only: true }), refetchInterval: 30000, retry: 1 });
  const collector = useQuery({ queryKey: ["flywheel", "collector-health"], queryFn: api.collectorHealth, refetchInterval: 60000, retry: 1 });
  const searchRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const online = health.isSuccess;
  const radarRunning = health.data?.scheduler.radar_running;

  return (
    <div className="flex h-full">
      <aside className={clsx("flex h-full shrink-0 flex-col border-r border-line bg-card transition-all duration-[180ms]", collapsed ? "w-[68px]" : "w-[232px]")}>
        <div className="flex items-center gap-2 px-4 py-4">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-primary text-white shadow-sm">
            <Star size={18} fill="currentColor" />
          </div>
          {!collapsed && (
            <div className="leading-tight">
              <div className="text-[15px] font-extrabold tracking-tight">EDGEFUT AI</div>
              <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-3">Análise quantitativa</div>
            </div>
          )}
        </div>
        <nav className="flex-1 overflow-y-auto px-2 pb-3">
          {nav.map((group, gi) => (
            <div key={gi} className="mb-2">
              {group.title && !collapsed && <div className="label px-3 pb-1 pt-3">{group.title}</div>}
              {group.items.map((it) => (
                <NavLink
                  key={it.to}
                  to={it.to}
                  end={it.end}
                  title={it.label}
                  className={({ isActive }) =>
                    clsx(
                      "mb-0.5 flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition duration-[180ms]",
                      isActive ? "bg-primary-50 text-primary" : "text-ink-2 hover:bg-gray-50 hover:text-ink",
                    )
                  }
                >
                  <it.icon size={18} className="shrink-0" />
                  {!collapsed && <span className="truncate">{it.label}</span>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="border-t border-line px-4 py-3 text-xs">
          <div className="flex items-center gap-2">
            <span className={clsx("h-2 w-2 rounded-full", online ? (radarRunning ? "bg-warning animate-pulse" : "bg-success") : "bg-danger")} />
            {!collapsed && <span className="text-ink-2">{online ? (radarRunning ? "Analisando jogos…" : "Engine local ativo") : "Engine offline"}</span>}
          </div>
          {!collapsed && online && <div className="mt-1 text-[10px] text-ink-3">127.0.0.1:{health.data?.port} · v{health.data?.version}</div>}
          {online && collector.data && (
            <button
              className="mt-1.5 flex w-full items-center gap-2 text-left"
              title={`Coletor Superbet: ${collector.data.health}${collector.data.reasons.length ? " — " + collector.data.reasons.join("; ") : ""} · último snapshot ${collector.data.last_fetched_at ? relativeTime(collector.data.last_fetched_at) : "nunca"}`}
              onClick={() => navigate("/flywheel")}
            >
              <span className={clsx("h-2 w-2 shrink-0 rounded-full", collector.data.health === "HEALTHY" ? "bg-success" : collector.data.health === "DEGRADED" ? "bg-warning" : "bg-danger")} />
              {!collapsed && (
                <span className="truncate text-[10px] text-ink-3">
                  Coletor {collector.data.health} · {collector.data.last_fetched_at ? relativeTime(collector.data.last_fetched_at) : "sem snapshot"}
                </span>
              )}
            </button>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-card/80 px-5 backdrop-blur">
          <button className="btn-ghost -ml-2" onClick={useUi.getState().toggleSidebar} title="Recolher menu">
            <Layers size={16} />
          </button>
          <button
            ref={searchRef}
            onClick={() => setSearchOpen(true)}
            className="flex h-9 w-full max-w-xl items-center gap-2 rounded-lg border border-line bg-bg px-3 text-sm text-ink-3 transition hover:border-ink-3"
          >
            <Search size={15} />
            <span className="flex-1 text-left">Buscar time, campeonato ou jogador…</span>
            <kbd className="rounded border border-line bg-card px-1.5 text-[10px] font-semibold">Ctrl K</kbd>
          </button>
          <div className="ml-auto flex items-center gap-2">
            {sys.data && (
              <button className="btn-ghost inline-flex items-center gap-1.5 text-xs" title={`Saúde do sistema: ${HEALTH_LABELS[sys.data.overall]}`} onClick={() => navigate("/sistema/diagnostico")}>
                <HealthDot status={sys.data.overall} />
                <span className="hidden lg:inline">{HEALTH_LABELS[sys.data.overall]}</span>
              </button>
            )}
            <button className="btn-ghost relative" title="Alertas locais" onClick={() => navigate("/alertas")}>
              <Bell size={16} />
              {(alerts.data?.unread ?? 0) > 0 && (
                <span className="absolute -right-0.5 -top-0.5 grid min-w-[16px] place-items-center rounded-full bg-primary px-1 text-[9px] font-bold leading-4 text-white">
                  {Math.min(99, alerts.data!.unread)}
                </span>
              )}
            </button>
            <button className="btn-primary" onClick={() => navigate("/radar")}>
              <Radar size={15} /> Radar
            </button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
          <div className="mx-auto max-w-[1400px]">
            <Outlet />
          </div>
        </main>
      </div>
      {searchOpen && <GlobalSearch onClose={() => setSearchOpen(false)} />}
    </div>
  );
}
