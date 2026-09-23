import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { SettingsModel } from "@edgefut/contracts";
import { Save, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { Card, ErrorBox, Loading, PageHeader, SectionTitle } from "@/components/ui";
import { api } from "@/lib/api";

export function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const [form, setForm] = useState<SettingsModel | null>(null);
  useEffect(() => {
    if (q.data && !form) setForm(q.data);
  }, [q.data, form]);
  const save = useMutation({
    mutationFn: (s: SettingsModel) => api.saveSettings(s),
    onSuccess: (s) => {
      setForm(s);
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["models"] });
    },
  });
  if (q.isLoading || !form) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const set = <K extends keyof SettingsModel>(k: K, v: SettingsModel[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  return (
    <div className="space-y-5">
      <PageHeader
        title="Configurações"
        right={
          <button className="btn-primary" onClick={() => save.mutate(form)} disabled={save.isPending}>
            <Save size={14} /> Salvar
          </button>
        }
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <SectionTitle title="Perfil e simulação" />
          <F label="Seu nome">
            <input className="input" value={form.user_name} onChange={(e) => set("user_name", e.target.value)} />
          </F>
          <F label="Simulações Monte Carlo padrão">
            <select className="input" value={form.default_simulations} onChange={(e) => set("default_simulations", Number(e.target.value))}>
              {[10000, 25000, 50000, 100000].map((n) => (
                <option key={n} value={n}>
                  {n.toLocaleString("pt-BR")}
                </option>
              ))}
            </select>
          </F>
        </Card>
        <Card>
          <SectionTitle title="Limiares de recomendação" subtitle="Abaixo disso o sistema diz NÃO ENTRAR" />
          <div className="grid grid-cols-2 gap-3">
            <F label="Edge mínimo (pp)">
              <input className="input" type="number" step="0.5" value={form.min_edge_pp} onChange={(e) => set("min_edge_pp", Number(e.target.value))} />
            </F>
            <F label="EV mínimo (%)">
              <input className="input" type="number" step="0.5" value={form.min_ev_pct} onChange={(e) => set("min_ev_pct", Number(e.target.value))} />
            </F>
            <F label="Odd mínima">
              <input className="input" type="number" step="0.05" value={form.min_odd} onChange={(e) => set("min_odd", Number(e.target.value))} />
            </F>
            <F label="Odd máxima">
              <input className="input" type="number" step="0.05" value={form.max_odd} onChange={(e) => set("max_odd", Number(e.target.value))} />
            </F>
          </div>
        </Card>
        <Card>
          <SectionTitle title="Banca" subtitle="Somente para o simulador de stake. O EdgeFut não realiza apostas." />
          <div className="grid grid-cols-2 gap-3">
            <F label="Banca (R$)">
              <input className="input" type="number" value={form.bankroll} onChange={(e) => set("bankroll", Number(e.target.value))} />
            </F>
            <F label="Fração de Kelly máxima (teto 0.25)">
              <input className="input" type="number" step="0.05" max={0.25} value={form.kelly_fraction_max} onChange={(e) => set("kelly_fraction_max", Math.min(0.25, Number(e.target.value)))} />
            </F>
          </div>
        </Card>
        <Card>
          <SectionTitle title="Coleta" subtitle="Intervalos do agendador local" />
          <div className="grid grid-cols-2 gap-3">
            <F label="Eventos (min)">
              <input className="input" type="number" value={form.events_refresh_min} onChange={(e) => set("events_refresh_min", Number(e.target.value))} />
            </F>
            <F label="Odds próximas (min)">
              <input className="input" type="number" value={form.odds_refresh_min} onChange={(e) => set("odds_refresh_min", Number(e.target.value))} />
            </F>
          </div>
        </Card>
        <Card>
          <SectionTitle title="Ollama (opcional)" subtitle="Só reescreve explicações a partir dos fatos calculados. Nunca gera estatísticas." />
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.ollama_enabled} onChange={(e) => set("ollama_enabled", e.target.checked)} /> Usar Ollama local (http://127.0.0.1:11434)
          </label>
          <F label="Modelo">
            <input className="input" value={form.ollama_model} onChange={(e) => set("ollama_model", e.target.value)} />
          </F>
          <div className="text-xs text-ink-2">Status: {health.data?.ollama_available ? <b className="text-success">disponível</b> : "não detectado — o ExplanationEngine por templates é usado"}</div>
        </Card>
        <Card className="border-success/30 bg-success-50/40">
          <div className="flex items-start gap-3">
            <ShieldCheck className="mt-0.5 shrink-0 text-success" />
            <div className="text-sm">
              <div className="font-bold">Segurança e ética</div>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-ink-2">
                <li>O engine escuta apenas em 127.0.0.1 ({health.data?.host}:{health.data?.port}). Nunca 0.0.0.0.</li>
                <li>Nenhuma credencial de casa de apostas é solicitada ou armazenada.</li>
                <li>O app não faz login, não aposta e não clica em “apostar”.</li>
                <li>Não contorna CAPTCHA, Cloudflare ou mecanismos anti-bot; fontes bloqueadas são registradas como indisponíveis.</li>
                <li>Banco local: {health.data?.db_path}</li>
              </ul>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

function F({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="mb-2 block text-xs">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}
