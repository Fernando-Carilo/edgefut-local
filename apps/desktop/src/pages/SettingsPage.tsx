import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { SettingsModel } from "@edgefut/contracts";
import { Save, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { Card, ErrorBox, Loading, PageHeader, SectionTitle } from "@/components/ui";
import { api } from "@/lib/api";
import { applyBackgroundSettings, inTauri, quitApp, type ShellBackgroundResult } from "@/lib/shell";

const OPP_LABELS: Record<string, string> = {
  model_confidence: "Confiança do modelo",
  data_quality: "Qualidade dos dados",
  calibration_quality: "Qualidade da calibração",
  edge: "Edge",
  ev: "EV",
  odds_freshness: "Frescor das odds",
  model_agreement: "Concordância entre modelos",
  historical_performance: "Performance histórica",
  sample_size: "Tamanho da amostra",
};

export function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health });
  const [form, setForm] = useState<SettingsModel | null>(null);
  const [shell, setShell] = useState<ShellBackgroundResult | null>(null);
  useEffect(() => {
    if (q.data && !form) setForm(q.data);
  }, [q.data, form]);
  const save = useMutation({
    mutationFn: (s: SettingsModel) => api.saveSettings(s),
    onSuccess: (s) => {
      setForm(s);
      void applyBackgroundSettings(s.background_collector, s.autostart_on_login).then((r) => setShell(r));
      qc.invalidateQueries({ queryKey: ["settings"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["models"] });
      qc.invalidateQueries({ queryKey: ["radar"] });
      qc.invalidateQueries({ queryKey: ["analysis"] });
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
          <SectionTitle title="Quality Gate" subtitle="Checks obrigatórios para uma seleção entrar em TOP OPORTUNIDADES. O sistema nunca afrouxa esses valores sozinho para 'achar' entradas." />
          <div className="grid grid-cols-2 gap-3">
            <F label="Qualidade mínima dos dados (%) · piso 40">
              <input className="input" type="number" min={40} max={100} value={form.gate_min_data_quality} onChange={(e) => set("gate_min_data_quality", Number(e.target.value))} />
            </F>
            <F label="Confiança mínima (0–100) · piso 50">
              <input className="input" type="number" min={50} max={100} value={form.gate_min_confidence} onChange={(e) => set("gate_min_confidence", Number(e.target.value))} />
            </F>
            <F label="Amostra mínima (jogos) · piso 10">
              <input className="input" type="number" min={10} value={form.gate_min_sample} onChange={(e) => set("gate_min_sample", Number(e.target.value))} />
            </F>
            <F label="Divergência máxima entre modelos (pp) · teto 15">
              <input className="input" type="number" step="0.5" max={15} value={form.gate_max_disagreement_pp} onChange={(e) => set("gate_max_disagreement_pp", Number(e.target.value))} />
            </F>
            <F label="Edge máximo sem calibrador (pp) · teto 25">
              <input className="input" type="number" step="0.5" max={25} value={form.gate_max_edge_pp_uncalibrated} onChange={(e) => set("gate_max_edge_pp_uncalibrated", Number(e.target.value))} />
            </F>
            <F label="HIGH PROBABILITY a partir de (prob.) · piso 0.55">
              <input className="input" type="number" step="0.01" min={0.55} max={0.99} value={form.high_probability_min} onChange={(e) => set("high_probability_min", Number(e.target.value))} />
            </F>
          </div>
          <p className="mt-1 text-[11px] text-ink-3">Valores fora dos pisos/tetos são corrigidos pelo engine ao salvar (HARD_FLOORS / HARD_CEILINGS).</p>
        </Card>
        <Card>
          <SectionTitle title="Opportunity Score V2" subtitle="Pesos dos 9 componentes (normalizados para somar 100). Ordena o Radar — nunca a odd." />
          <div className="grid grid-cols-3 gap-2">
            {Object.entries(form.opportunity_weights).map(([k, v]) => (
              <F key={k} label={OPP_LABELS[k] ?? k}>
                <input className="input" type="number" min={0} step="1" value={v} onChange={(e) => set("opportunity_weights", { ...form.opportunity_weights, [k]: Number(e.target.value) })} />
              </F>
            ))}
          </div>
          <p className="text-[11px] text-ink-3">Soma atual: {Object.values(form.opportunity_weights).reduce((a, b) => a + b, 0)} (normalizada automaticamente).</p>
        </Card>
        <Card>
          <SectionTitle title="Odds e ao vivo" subtitle="Remoção de margem e cadência de observação" />
          <div className="grid grid-cols-2 gap-3">
            <F label="Método de probabilidade justa">
              <select className="input" value={form.margin_method} onChange={(e) => set("margin_method", e.target.value as SettingsModel["margin_method"])}>
                <option value="SHIN">Shin (corrige favorite-longshot)</option>
                <option value="MULTIPLICATIVE">Multiplicativo (proporcional)</option>
              </select>
            </F>
            <F label="Poll ao vivo (s) · mínimo 20">
              <input className="input" type="number" min={20} value={form.live_poll_seconds} onChange={(e) => set("live_poll_seconds", Number(e.target.value))} />
            </F>
          </div>
          <p className="text-[11px] text-ink-3">Ambos os métodos são calculados e armazenados em cada snapshot; este ajuste define qual é exibido e usado no edge.</p>
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
        <Card className="border-primary/30 bg-primary-50/40">
          <SectionTitle title="Coletor em segundo plano (Windows)" subtitle="O dataset Superbet só cresce enquanto o app está rodando. Estas opções mantêm o coletor vivo mesmo com a janela fechada." />
          <div className="space-y-2 text-sm">
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-0.5" checked={form.background_collector} onChange={(e) => set("background_collector", e.target.checked)} />
              <span>
                <b>Manter coletor em segundo plano</b>
                <span className="block text-xs text-ink-2">Fechar a janela esconde o app na bandeja do sistema; o motor local e os jobs continuam. Para encerrar tudo use “Sair” no ícone da bandeja.</span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-0.5" checked={form.autostart_on_login} onChange={(e) => set("autostart_on_login", e.target.checked)} />
              <span>
                <b>Iniciar com o Windows</b>
                <span className="block text-xs text-ink-2">Opcional. Registra o EdgeFut para arrancar no login (minimizado na bandeja). Após qualquer parada, o gap de coleta é registrado — nunca preenchido com dados fabricados.</span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-0.5" checked={form.notifications_enabled} onChange={(e) => set("notifications_enabled", e.target.checked)} />
              <span>
                <b>Notificações locais</b>
                <span className="block text-xs text-ink-2">Coletor degradado, schema Superbet alterado, liquidação concluída, research signal, alvo de preço. Nunca “BET NOW”.</span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-0.5" checked={form.backup_enabled} onChange={(e) => set("backup_enabled", e.target.checked)} />
              <span>
                <b>Backup diário automático</b>
                <span className="block text-xs text-ink-2">Retenção 7 diários · 4 semanais · 3 mensais, com verificação de integridade. O raw nunca é apagado automaticamente.</span>
              </span>
            </label>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-ink-3">
            {inTauri() ? (
              <>
                <span>Shell Windows: {shell ? `autostart ${shell.autostart_active ? "registrado" : "não registrado"} · close-to-tray ${shell.background_collector ? "ativo" : "inativo"}` : "aplica-se ao salvar"}</span>
                <button className="btn-ghost ml-auto text-xs text-danger" onClick={() => void quitApp()} title="Encerra janela, bandeja e engine — o coletor para até o próximo arranque">
                  Sair e parar o coletor
                </button>
              </>
            ) : (
              <span>Fora da shell Tauri (navegador/dev): bandeja, autostart e close-to-tray não se aplicam; as flags são guardadas no engine.</span>
            )}
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
