import { useQuery } from "@tanstack/react-query";
import { fmtDateTime } from "@edgefut/shared";
import { Calendar, Search, Shield, Trophy, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "@/lib/api";

export function GlobalSearch({ onClose }: { onClose: () => void }) {
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  const { data, isFetching } = useQuery({ queryKey: ["search", q], queryFn: () => api.search(q), enabled: q.trim().length >= 2 });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const go = (path: string) => {
    navigate(path);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-ink/30 p-4 pt-[10vh] backdrop-blur-sm" onClick={onClose}>
      <div className="card w-full max-w-2xl overflow-hidden p-0" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <Search size={16} className="text-ink-3" />
          <input
            autoFocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Time, campeonato ou jogador…"
            className="flex-1 bg-transparent text-sm outline-none"
          />
          {isFetching && <span className="text-xs text-ink-3">buscando…</span>}
          <button className="btn-ghost -mr-2" onClick={onClose}>
            <X size={16} />
          </button>
        </div>
        <div className="max-h-[60vh] overflow-y-auto p-2">
          {q.trim().length < 2 && <div className="p-4 text-sm text-ink-2">Digite ao menos 2 caracteres. Resultados vêm apenas de eventos reais e datasets carregados.</div>}
          {data && data.events.length === 0 && data.teams.length === 0 && data.competitions.length === 0 && (
            <div className="p-4 text-sm text-ink-2">Nada encontrado para "{data.query}".</div>
          )}
          {data && data.events.length > 0 && (
            <Group icon={<Calendar size={13} />} title="Jogos">
              {data.events.map((e) => (
                <Row key={e.id} onClick={() => go(`/jogos/${e.id}`)} title={`${e.home_name} x ${e.away_name}`} sub={`${e.competition_name ?? ""} · ${fmtDateTime(e.kickoff_utc)}`} />
              ))}
            </Group>
          )}
          {data && data.teams.length > 0 && (
            <Group icon={<Shield size={13} />} title="Times">
              {data.teams.map((t, i) => (
                <Row
                  key={i}
                  onClick={() => (t.next_event_id ? go(`/jogos/${t.next_event_id}`) : go(`/jogos?search=${encodeURIComponent(t.name)}`))}
                  title={t.name}
                  sub={t.next_event_id ? `Próximo jogo ${fmtDateTime(t.next_kickoff_utc)} · ${t.competition ?? ""}` : t.dataset ? "Presente no histórico (sem jogo próximo)" : ""}
                />
              ))}
            </Group>
          )}
          {data && data.competitions.length > 0 && (
            <Group icon={<Trophy size={13} />} title="Campeonatos">
              {data.competitions.map((c, i) => (
                <Row key={i} onClick={() => go(`/jogos?competition=${encodeURIComponent(c.name)}`)} title={c.name} sub={c.events ? `${c.events} jogos` : ""} />
              ))}
            </Group>
          )}
        </div>
      </div>
    </div>
  );
}

function Group({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="mb-2">
      <div className="label flex items-center gap-1.5 px-2 py-1">
        {icon} {title}
      </div>
      {children}
    </div>
  );
}

function Row({ title, sub, onClick }: { title: string; sub: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex w-full flex-col items-start rounded-lg px-3 py-2 text-left transition hover:bg-gray-50">
      <span className="text-sm font-medium">{title}</span>
      {sub && <span className="text-xs text-ink-2">{sub}</span>}
    </button>
  );
}
