import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, Loader2, Star, X } from "lucide-react";
import type { ReactNode } from "react";

import { api } from "@/lib/api";
import { useUi } from "@/store/ui";

/**
 * Bloqueia a UI até o engine responder e o primeiro boot (database, datasets, eventos) terminar
 * pelo menos uma vez. Tudo o que aparece aqui é o progresso real do backend.
 */
export function BootGate({ children }: { children: ReactNode }) {
  const onboarded = useUi((s) => s.onboarded);
  const setOnboarded = useUi((s) => s.setOnboarded);
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 2000, retry: false });
  const boot = useQuery({
    queryKey: ["bootstrap"],
    queryFn: api.bootstrap,
    enabled: health.isSuccess,
    refetchInterval: (q) => (q.state.data?.done ? false : 1500),
  });
  const start = useMutation({ mutationFn: () => api.runBootstrap(false) });

  if (onboarded && health.isSuccess) return <>{children}</>;
  if (boot.data?.done && onboarded) return <>{children}</>;

  const steps = boot.data?.steps ?? [];
  const engineDown = health.isError;

  return (
    <div className="grid h-full place-items-center bg-bg p-6">
      <div className="card w-full max-w-lg p-8">
        <div className="mb-6 flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-xl bg-primary text-white shadow-sm">
            <Star size={20} fill="currentColor" />
          </div>
          <div>
            <div className="text-xl font-extrabold tracking-tight">EDGEFUT AI</div>
            <div className="text-xs text-ink-2">Análise quantitativa local · nenhuma aposta é realizada</div>
          </div>
        </div>

        {engineDown ? (
          <div className="rounded-lg bg-danger-50 p-4 text-sm">
            <div className="font-semibold text-danger">Aguardando o engine local (127.0.0.1)…</div>
            <div className="mt-1 text-ink-2">O motor Python é iniciado junto com o app. Se esta mensagem persistir, abra Configurações › Logs ou rode <code>scripts/dev.ps1</code>.</div>
            <div className="mt-2 flex items-center gap-2 text-xs text-ink-3">
              <Loader2 size={12} className="animate-spin" /> tentando novamente
            </div>
          </div>
        ) : (
          <>
            <div className="mb-3 text-sm text-ink-2">
              {boot.data?.running ? "Preparando o primeiro uso: banco de dados, datasets públicos e próximos eventos." : boot.data?.done ? "Tudo pronto." : "Engine conectado."}
            </div>
            <ol className="space-y-1.5">
              {steps.map((s, i) => (
                <li key={i} className="flex items-center gap-2 text-sm">
                  {s.status === "ok" ? <Check size={14} className="text-success" /> : s.status === "error" ? <X size={14} className="text-danger" /> : <Loader2 size={14} className="animate-spin text-ink-3" />}
                  <span className={clsx(s.status === "error" && "text-ink-2")}>{s.name}</span>
                  <span className="ml-auto truncate text-xs text-ink-3">{s.detail}</span>
                </li>
              ))}
              {steps.length === 0 && !boot.data?.running && (
                <li className="text-sm text-ink-2">O primeiro boot ainda não rodou.</li>
              )}
            </ol>
            {boot.data?.error && <div className="mt-3 rounded-lg bg-danger-50 p-3 text-xs text-danger">{boot.data.error}</div>}
            <div className="mt-6 flex items-center justify-between">
              <span className="text-[11px] text-ink-3">Fontes públicas: Superbet (oferta), football-data.co.uk, martj42/international_results</span>
              {boot.data?.done ? (
                <button className="btn-primary" onClick={() => setOnboarded(true)}>
                  Entrar
                </button>
              ) : !boot.data?.running ? (
                <button className="btn-primary" onClick={() => start.mutate()} disabled={start.isPending}>
                  Iniciar preparação
                </button>
              ) : (
                <button className="btn-outline" onClick={() => setOnboarded(true)}>
                  Continuar em segundo plano
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
